// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#include <algorithm>
#include <chainparams.h>
#include <chronik-bridge/src/ffi.rs.h>
#include <chronik-cpp/chronik_bridge.h>
#include <chronik-cpp/ergon_archival_probe.h>
#include <chronik_lib/src/ergon_archival.rs.h>
#include <clientversion.h>
#include <coins.h>
#include <config.h>
#include <map>
#include <set>
#include <shutdown.h>
#include <stdexcept>
#include <tuple>
#include <txmempool.h>
#include <txdb.h>
#include <undo.h>
#include <validation.h>
#include <vector>
namespace ergon_archival {
void StartupHook::run() const {
    AssertLockNotHeld(cs_main);
    if (callback)
        callback();
}
namespace {
// Values only: no borrowed native index, parent pointer or hash address.
struct CaptureReference {
    CBlockHeader header;
    BlockHash hash;
    BlockStatus status;
    FlatFilePos data, undo;
    int height;
};
struct PoolRead {
    CTransactionRef tx;
    int64_t time;
    size_t wire_bytes, retained_index;
    std::vector<Coin> coins;
    std::vector<size_t> cold;
};
} // namespace
void archival_fixture_guard(const chronik_bridge::ErgonNodeAccess &node) {
    LOCK2(cs_main, node.mempool.cs);
    if (node.config.GetChainParams().NetworkIDString() != "regtest" ||
        fPruneMode || ShutdownRequested() || node.index_failed.load() ||
        !ChainActive().GetSourceGeneration() ||
        !node.mempool.GetSourceGeneration() || ChainActive().Height() < 0 ||
        ChainActive().Height() > 2000 || node.mempool.size() > 4096 ||
        !pcoinsTip || !pcoinsdbview) {
        throw std::runtime_error(
            "Archival probe requires a healthy, unpruned, bounded-mempool "
            "regtest fixture (height <= 2000)");
    }
}
std::unique_ptr<chronik_bridge::ChronikBridge>
make_startup_bridge(const chronik_bridge::ErgonNodeAccess &node) {
    auto bridge = chronik_bridge::make_bridge(node);
    bridge->capture_startup_snapshot();
    return bridge;
}
void finish_startup_bridge(const chronik_bridge::ChronikBridge &bridge) {
    // The Rust UniquePtr is exclusively owned by synchronous startup. This
    // bridge cannot yet be shared with workers or requests.
    bridge.finish_startup_snapshot();
}
bool finish_service_startup_bridge(const chronik_bridge::ChronikBridge &bridge) {
    return bridge.finish_startup_snapshot(true);
}
bool service_startup_cancelled() { return ShutdownRequested(); }
bool capture_service_startup(ArchivalProbe &probe,
                            const chronik_bridge::ChronikBridge &bridge,
                            const chronik_bridge::ErgonNodeAccess &node) {
    AssertLockNotHeld(cs_main);
    AssertLockNotHeld(node.mempool.cs);
    // The bridge is still private and owns its immutable ancestor graph.
    // Seed the OLD indexed branch before switching to live access, so honest
    // progress/reorg can use the existing captured upstream disconnect/connect.
    const auto tip = chronik_bridge::get_block_info(bridge.get_chain_tip());
    const auto generation = bridge.chain_generation();
    const auto revision = bridge.mempool_revision();
    const int base = std::max(0, tip.height - 287);
    auto source = [&] {
        LOCK2(cs_main, node.mempool.cs);
        return new_capture(node, base, tip.height, tip.hash, generation, revision);
    }();
    size_t bytes = 0;
    for (int height = base; height <= tip.height; ++height) {
        if (ShutdownRequested())
            return false;
        const auto &index = bridge.lookup_block_index_by_height(height);
        auto block = bridge.load_block(index);
        auto undo = bridge.load_block_undo(index);
        const auto charge = ::GetSerializeSize(*block, CLIENT_VERSION) +
                            ::GetSerializeSize(*undo, CLIENT_VERSION);
        if (charge > 64 * 1024 * 1024 - bytes)
            throw std::runtime_error("Startup retained payload budget exceeded");
        bytes += charge;
        source->capture_block(*block, *undo, index, charge, node);
    }
    probe.seed_service_startup(*source, node);
    return true;
}
bool reconcile_startup(ArchivalProbe &probe,
                       const chronik_bridge::ErgonNodeAccess &node) {
    return reconcile_captured(probe, node);
}
uint64_t
archival_mempool_revision(const chronik_bridge::ErgonNodeAccess &node) {
    return node.mempool.GetSourceGeneration();
}
uint64_t archival_chain_generation(const chronik_bridge::ErgonNodeAccess &) {
    AssertLockHeld(cs_main);
    return ChainActive().GetSourceGeneration();
}
void archival_reconcile_guard(const chronik_bridge::ErgonNodeAccess &node) {
    AssertLockHeld(cs_main);
    AssertLockHeld(node.mempool.cs);
    if (node.config.GetChainParams().NetworkIDString() != "regtest" ||
        fPruneMode || node.index_failed.load() ||
        !ChainActive().GetSourceGeneration() ||
        !node.mempool.GetSourceGeneration() || !pcoinsTip || !pcoinsdbview ||
        ChainActive().Height() < 0 || ChainActive().Height() > 2000 ||
        node.mempool.size() > 4096) {
        throw std::runtime_error(
            "Reconciliation requires a healthy bounded regtest fixture");
    }
}
void archival_database_guard(const chronik_bridge::ErgonNodeAccess &node) {
    AssertLockNotHeld(cs_main);
    AssertLockNotHeld(node.mempool.cs);
    if (node.index_failed.load())
        throw std::runtime_error("Captured index unavailable");
}
bool reconcile_captured(ArchivalProbe &probe,
                        const chronik_bridge::ErgonNodeAccess &node,
                        const std::function<void()> &after_capture,
                        const std::function<void()> &before_disk,
                        CaptureStats *stats) {
    AssertLockNotHeld(cs_main);
    AssertLockNotHeld(node.mempool.cs);
    constexpr int WINDOW = 288;
    if (stats)
        *stats = {};
    auto native_hash = [](const auto &bytes) {
        uint256 hash;
        std::copy(bytes.begin(), bytes.end(), hash.begin());
        return hash;
    };
    try {
        for (int attempt = 0; attempt < 4; ++attempt) {
            // Shutdown is cancellation, not persistent index corruption. The
            // owner still retains native resources until worker/HTTP join.
            // current() refuses publication if shutdown races this attempt.
            if (ShutdownRequested())
                return false;
            if (stats)
                ++stats->attempts;
            const auto retained = probe.capture_inventory(node);
            const auto pool = probe.pool_capture_inventory(node);
            std::map<TxId, size_t> cached;
            for (size_t i = 0; i < pool.size(); ++i)
                if (!cached.emplace(TxId(native_hash(pool[i].txid)), i).second)
                    throw std::runtime_error(
                        "Duplicate retained pool identity");
            auto captured = [&] {
                LOCK2(cs_main, node.mempool.cs);
                archival_reconcile_guard(node);
                const int height = ChainActive().Height();
                const auto generation = archival_chain_generation(node);
                const int base = retained.empty()
                                     ? std::max(0, height + 1 - WINDOW)
                                     : retained[0].height;
                if (!retained.empty()) {
                    auto found = mapBlockIndex.find(
                        BlockHash(native_hash(retained.back().hash)));
                    if (found == mapBlockIndex.end())
                        throw std::runtime_error(
                            "Retained native tip unavailable");
                    const auto *fork = ChainActive().FindFork(found->second);
                    if (!fork || fork->nHeight < base)
                        throw std::runtime_error("Native reorg below retained "
                                                 "anchor; rebuild required");
                }
                if (height < base || height - base + 1 > 2 * WINDOW)
                    throw std::runtime_error(
                        "Native capture span exceeds bounded profile");
                auto source = new_capture(
                    node, base, height,
                    chronik_bridge::get_block_info(*ChainActive().Tip()).hash,
                    generation, archival_mempool_revision(node));
                size_t bytes = 0;
                auto charge = [&](size_t amount) {
                    if (amount > 64 * 1024 * 1024 - bytes)
                        throw std::runtime_error(
                            "Captured source payload budget exceeded");
                    bytes += amount;
                };
                auto matches = [&](const auto &old) {
                    if (stats)
                        ++stats->metadata_checked;
                    const auto *index = ChainActive()[old.height];
                    return index &&
                           old.hash ==
                               chronik_bridge::get_block_info(*index).hash &&
                           !index->GetBlockPos().IsNull() &&
                           old.file_num ==
                               uint32_t(index->GetBlockPos().nFile) &&
                           old.data_pos == index->GetBlockPos().nPos &&
                           old.undo_pos == (index->GetUndoPos().IsNull()
                                                ? 0
                                                : index->GetUndoPos().nPos);
                };
                size_t common = 0;
                while (common < retained.size() &&
                       retained[common].height <= height &&
                       matches(retained[common])) {
                    charge(retained[common].wire_bytes);
                    ++common;
                }
                // Only fresh suffix headers are copied under the native fence.
                // Prefix checking is bounded by the retained window, not
                // height.
                std::vector<CaptureReference> references;
                references.reserve(height - base + 1 - common);
                for (int h = base + int(common); h <= height; ++h) {
                    const auto &index = *ChainActive()[h];
                    references.push_back({index.GetBlockHeader(),
                                          index.GetBlockHash(), index.nStatus,
                                          index.GetBlockPos(),
                                          index.GetUndoPos(), h});
                    if (stats)
                        ++stats->metadata_copied;
                }
                std::map<TxId, TxMempoolInfo> entries;
                for (const auto &entry : node.mempool.infoAll())
                    if (!entry.tx ||
                        !entries.emplace(entry.tx->GetId(), entry).second)
                        throw std::runtime_error(
                            "Invalid captured native entry");
                // Deterministic parent-before-child schedule, one edge per
                // parent identity even if multiple outputs of it are spent.
                std::map<TxId, size_t> degree;
                std::map<TxId, std::set<TxId>> children;
                std::set<TxId> ready;
                for (const auto &[id, entry] : entries) {
                    std::set<TxId> parents;
                    for (const auto &input : entry.tx->vin)
                        if (entries.count(input.prevout.GetTxId()))
                            parents.insert(input.prevout.GetTxId());
                    degree[id] = parents.size();
                    if (parents.empty())
                        ready.insert(id);
                    for (const auto &parent : parents)
                        children[parent].insert(id);
                }
                // Metadata-only LevelDB snapshot acquisition is coherent with
                // the copied cache overlay under cs_main. A flush afterwards
                // cannot resurrect spent cache entries or change this read.
                auto coin_snapshot = pcoinsdbview->Snapshot();
                auto cache_identity = pcoinsTip->ReadIdentity();
                std::vector<PoolRead> reads;
                size_t emitted = 0;
                while (!ready.empty()) {
                    const auto id = *ready.begin();
                    ready.erase(ready.begin());
                    const auto &entry = entries.at(id);
                    const auto found = cached.find(id);
                    if (found != cached.end() &&
                        pool[found->second].chain_generation == generation &&
                        pool[found->second].time == entry.nTime) {
                        charge(pool[found->second].wire_bytes);
                        reads.push_back({entry.tx, entry.nTime,
                                         pool[found->second].wire_bytes,
                                         found->second, {}, {}});
                        if (stats)
                            ++stats->pool_reused;
                    } else {
                        size_t entry_bytes =
                            ::GetSerializeSize(*entry.tx, CLIENT_VERSION);
                        if (entry_bytes > 64 * 1024 * 1024)
                            throw std::runtime_error(
                                "Captured pool entry exceeds budget");
                        PoolRead read{entry.tx, entry.nTime, entry_bytes,
                                      SIZE_MAX, {}, {}};
                        read.coins.resize(entry.tx->vin.size());
                        for (size_t i = 0; i < entry.tx->vin.size(); ++i) {
                            const auto &input = entry.tx->vin[i];
                            Coin coin;
                            if (stats)
                                ++stats->prevout_reads;
                            const auto parent = entries.find(
                                input.prevout.GetTxId());
                            if (parent != entries.end()) {
                                if (input.prevout.GetN() >=
                                    parent->second.tx->vout.size())
                                    throw std::runtime_error(
                                        "Missing captured pool parent output");
                                coin = Coin(parent->second.tx->vout[
                                                input.prevout.GetN()],
                                            MEMPOOL_HEIGHT, false);
                            } else if (!pcoinsTip->CopyCachedCoin(
                                           input.prevout, coin)) {
                                read.cold.push_back(i);
                                continue;
                            }
                            // A spent cache entry is NOT a cold miss. Never
                            // fall back to an older unspent database value.
                            if (coin.IsSpent())
                                throw std::runtime_error(
                                    "Missing actual captured native prevout");
                            const auto amount =
                                ::GetSerializeSize(coin, CLIENT_VERSION);
                            if (amount > 64 * 1024 * 1024 - entry_bytes)
                                throw std::runtime_error(
                                    "Captured pool entry exceeds budget");
                            entry_bytes += amount;
                            read.coins[i] = std::move(coin);
                        }
                        charge(entry_bytes);
                        read.wire_bytes = entry_bytes;
                        reads.push_back(std::move(read));
                        if (stats)
                            ++stats->pool_captured;
                    }
                    ++emitted;
                    for (const auto &child : children[id])
                        if (--degree[child] == 0)
                            ready.insert(child);
                }
                if (emitted != entries.size())
                    throw std::runtime_error("Captured dependency cycle");
                return std::make_tuple(std::move(source), std::move(entries),
                                       ChainActive().Tip()->GetBlockHash(),
                                       generation,
                                       archival_mempool_revision(node),
                                       std::move(references), bytes, common,
                                       std::move(coin_snapshot),
                                       std::move(cache_identity),
                                       std::move(reads));
            }();
            auto current = [&] {
                AssertLockHeld(cs_main);
                AssertLockHeld(node.mempool.cs);
                const auto *tip = ChainActive().Tip();
                if (!tip || fPruneMode || ShutdownRequested() ||
                    !pcoinsdbview || !pcoinsTip ||
                    !pcoinsdbview->OwnsSnapshot(*std::get<8>(captured)) ||
                    pcoinsTip->ReadIdentity() != std::get<9>(captured) ||
                    tip->GetBlockHash() != std::get<2>(captured) ||
                    archival_chain_generation(node) != std::get<3>(captured) ||
                    archival_mempool_revision(node) != std::get<4>(captured))
                    return false;
                for (size_t i = 0; i < std::get<7>(captured); ++i) {
                    if (stats)
                        ++stats->metadata_checked;
                    const auto &old = retained[i];
                    const auto *index = ChainActive()[old.height];
                    if (!index ||
                        old.hash !=
                            chronik_bridge::get_block_info(*index).hash ||
                        index->GetBlockPos().IsNull() ||
                        old.file_num != uint32_t(index->GetBlockPos().nFile) ||
                        old.data_pos != index->GetBlockPos().nPos ||
                        old.undo_pos != (index->GetUndoPos().IsNull()
                                             ? 0
                                             : index->GetUndoPos().nPos))
                        return false;
                }
                for (const auto &ref : std::get<5>(captured)) {
                    if (stats)
                        ++stats->metadata_checked;
                    const auto *index = ChainActive()[ref.height];
                    if (!index || index->GetBlockHash() != ref.hash ||
                        index->GetBlockPos() != ref.data ||
                        index->GetUndoPos() != ref.undo)
                        return false;
                }
                return true;
            };
            archival_database_guard(node);
            const auto common = std::get<7>(captured);
            probe.seed_capture(*std::get<0>(captured), common, node);
            if (stats)
                stats->reused_blocks += common;
            if (before_disk)
                before_disk();
            try {
                auto bridge = chronik_bridge::make_bridge(node);
                size_t bytes = std::get<6>(captured);
                for (auto &read : std::get<10>(captured)) {
                    AssertLockNotHeld(cs_main);
                    AssertLockNotHeld(node.mempool.cs);
                    if (read.retained_index != SIZE_MAX) {
                        probe.seed_pool_entry(*std::get<0>(captured),
                                              read.retained_index, node);
                        continue;
                    }
                    for (const auto i : read.cold) {
                        if (stats) ++stats->cold_prevout_reads;
                        auto &coin = read.coins[i];
                        if (!std::get<8>(captured)->GetCoin(
                                read.tx->vin[i].prevout, coin) ||
                            coin.IsSpent())
                            throw std::runtime_error(
                                "Missing actual cold native prevout");
                        const auto amount =
                            ::GetSerializeSize(coin, CLIENT_VERSION);
                        if (amount > 64 * 1024 * 1024 - bytes)
                            throw std::runtime_error(
                                "Captured source payload budget exceeded");
                        bytes += amount;
                        read.wire_bytes += amount;
                    }
                    std::get<0>(captured)->capture_mempool(
                        *read.tx, read.coins, read.time, read.wire_bytes, node);
                }
                for (const auto &ref : std::get<5>(captured)) {
                    AssertLockNotHeld(cs_main);
                    AssertLockNotHeld(node.mempool.cs);
                    CBlockIndex index(ref.header), parent;
                    index.phashBlock = &ref.hash;
                    parent.phashBlock = &ref.header.hashPrevBlock;
                    index.pprev = ref.height ? &parent : nullptr;
                    index.nHeight = ref.height;
                    index.nStatus = ref.status;
                    index.nFile = ref.data.nFile;
                    index.nDataPos = ref.data.nPos;
                    index.nUndoPos = ref.undo.IsNull() ? 0 : ref.undo.nPos;
                    if (stats)
                        ++stats->block_read_calls;
                    auto block = bridge->load_block(index);
                    if (stats)
                        ++stats->undo_load_calls;
                    auto undo = bridge->load_block_undo(index);
                    uint64_t wire_bytes = 0;
                    for (auto amount :
                         {::GetSerializeSize(*block, CLIENT_VERSION),
                          ::GetSerializeSize(*undo, CLIENT_VERSION)}) {
                        if (amount > 64 * 1024 * 1024 - bytes)
                            throw std::runtime_error(
                                "Captured source payload budget exceeded");
                        bytes += amount;
                        wire_bytes += amount;
                    }
                    std::get<0>(captured)->capture_block(*block, *undo, index,
                                                         wire_bytes, node);
                }
            } catch (...) {
                LOCK2(cs_main, node.mempool.cs);
                if (!current()) {
                    if (stats)
                        ++stats->discarded_before_db;
                    continue;
                }
                throw;
            }
            {
                LOCK2(cs_main, node.mempool.cs);
                if (!current()) {
                    if (stats)
                        ++stats->discarded_before_db;
                    continue;
                }
            }
            if (after_capture)
                after_capture();
            probe.apply_capture(*std::get<0>(captured), node);
            {
                LOCK2(cs_main, node.mempool.cs);
                archival_reconcile_guard(node);
                if (!current())
                    continue;
                const auto actual = node.mempool.infoAll();
                const auto &expected = std::get<1>(captured);
                if (actual.size() != expected.size())
                    continue;
                bool match = true;
                for (const auto &entry : actual) {
                    const auto found = expected.find(entry.tx->GetId());
                    if (found == expected.end() ||
                        found->second.nTime != entry.nTime)
                        match = false;
                }
                if (!match)
                    continue;
                if (probe.publish_capture(*std::get<0>(captured), node))
                    return true;
            }
        }
        return false;
    } catch (...) {
        probe.fail_reconcile();
        throw;
    }
}
void reconcile_archival(ArchivalProbe &probe,
                        const chronik_bridge::ErgonNodeAccess &node) {
    LOCK2(cs_main, node.mempool.cs);
    archival_reconcile_guard(node);
    const auto revision = archival_mempool_revision(node);
    std::map<TxId, TxMempoolInfo> entries;
    size_t bytes = 0;
    for (const auto &entry : node.mempool.infoAll()) {
        if (!entry.tx || !entries.emplace(entry.tx->GetId(), entry).second)
            throw std::runtime_error("Invalid native mempool entry");
        bytes += ::GetSerializeSize(*entry.tx, CLIENT_VERSION);
        if (bytes > 64 * 1024 * 1024)
            throw std::runtime_error(
                "Fixture mempool serialized budget exceeded");
    }
    // Actual dependencies, deterministic parent-before-child order. Validate
    // all native coins BEFORE changing the index; never synthesize a prevout.
    std::vector<TxMempoolInfo> ordered;
    std::set<TxId> emitted;
    while (ordered.size() < entries.size()) {
        bool progress = false;
        for (const auto &[id, entry] : entries) {
            if (emitted.count(id))
                continue;
            bool ready = true;
            for (const auto &input : entry.tx->vin)
                if (entries.count(input.prevout.GetTxId()) &&
                    !emitted.count(input.prevout.GetTxId()))
                    ready = false;
            if (!ready)
                continue;
            ordered.push_back(entry);
            emitted.insert(id);
            progress = true;
        }
        if (!progress)
            throw std::runtime_error("Native mempool dependency cycle");
    }
    CCoinsViewMemPool view(pcoinsTip.get(), node.mempool);
    std::vector<std::vector<Coin>> coins;
    for (const auto &entry : ordered) {
        coins.emplace_back();
        for (const auto &input : entry.tx->vin) {
            Coin coin;
            if (!view.GetCoin(input.prevout, coin) || coin.IsSpent())
                throw std::runtime_error(
                    "Missing actual native mempool prevout");
            coins.back().push_back(std::move(coin));
        }
    }
    std::vector<uint8_t> ids;
    for (const auto &[id, entry] : entries)
        ids.insert(ids.end(), id.begin(), id.end());
    try {
        probe.begin_reconcile(node);
        for (size_t i = 0; i < ordered.size(); ++i)
            probe.add_native_mempool(*ordered[i].tx, coins[i], ordered[i].nTime,
                                     node);
        if (archival_mempool_revision(node) != revision)
            throw std::runtime_error(
                "Mempool changed within native lock barrier");
        probe.finish_reconcile({ids.data(), ids.size()}, node);
    } catch (...) {
        probe.fail_reconcile();
        throw;
    }
}
} // namespace ergon_archival

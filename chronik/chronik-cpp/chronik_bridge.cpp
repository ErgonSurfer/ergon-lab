// Copyright (c) 2022 The Bitcoin developers
// Copyright (c) 2026 The Ergon developers
// Distributed under the MIT software license, see the accompanying COPYING.
// Host adaptation of Bitcoin ABC 784d83de2e19eab726898d77bfe7465410d27a9c.
// This file adapts host access, not the upstream token/index/query algorithms.

#include <chain.h>
#include <chainparams.h>
#include <chronik-bridge/src/ffi.rs.h>
#include <chronik-cpp/chronik_bridge.h>
#include <clientversion.h>
#include <compressor.h>
#include <config.h>
#include <consensus/merkle.h>
#include <feerate.h>
#include <logging.h>
#include <primitives/block.h>
#include <shutdown.h>
#include <streams.h>
#include <undo.h>
#include <validation.h>
#include <validationinterface.h>
#include <version.h>

#include <algorithm>
#include <limits>
#include <map>
#include <stdexcept>

namespace {
template <typename Hash> std::array<uint8_t, 32> HashBytes(const Hash &hash) {
    std::array<uint8_t, 32> bytes;
    std::copy_n(hash.begin(), bytes.size(), bytes.begin());
    return bytes;
}

uint256 NativeHash(const std::array<uint8_t, 32> &bytes) {
    uint256 hash;
    std::copy(bytes.begin(), bytes.end(), hash.begin());
    return hash;
}

template <typename Container> rust::Vec<uint8_t> Bytes(const Container &input) {
    rust::Vec<uint8_t> output;
    output.reserve(input.size());
    for (auto byte : input) {
        output.push_back(static_cast<uint8_t>(byte));
    }
    return output;
}

uint32_t Position(size_t value) {
    if (value > std::numeric_limits<uint32_t>::max()) {
        throw std::runtime_error(
            "Native disk position exceeds the Chronik ABI");
    }
    return static_cast<uint32_t>(value);
}

chronik_bridge::TxOutput Output(const CTxOut &output) {
    return {output.nValue / FIXOSHI, Bytes(output.scriptPubKey)};
}

chronik_bridge::Coin SpentCoin(const Coin &coin) {
    if (coin.IsSpent()) {
        throw std::runtime_error("Spent coin data is unavailable");
    }
    return {Output(coin.GetTxOut()),
            coin.GetHeight() == MEMPOOL_HEIGHT ? -1 : int32_t(coin.GetHeight()),
            coin.IsCoinBase()};
}

chronik_bridge::Tx Transaction(const CTransaction &tx,
                               const std::vector<Coin> &coins) {
    const bool coinbase = tx.IsCoinBase();
    if ((!coinbase && coins.size() != tx.vin.size()) ||
        (coinbase && !coins.empty())) {
        throw std::runtime_error("Spent coin count does not match inputs");
    }
    chronik_bridge::Tx result;
    result.txid = HashBytes(tx.GetId());
    // Preserve the signed native version's exact unsigned wire bit pattern.
    result.version = static_cast<uint32_t>(tx.nVersion);
    result.locktime = tx.nLockTime;
    for (size_t i = 0; i < tx.vin.size(); ++i) {
        const auto &input = tx.vin[i];
        result.inputs.push_back(
            {{HashBytes(input.prevout.GetTxId()), input.prevout.GetN()},
             Bytes(input.scriptSig),
             input.nSequence,
             coinbase ? chronik_bridge::Coin{} : SpentCoin(coins[i])});
    }
    for (const auto &output : tx.vout) {
        result.outputs.push_back(Output(output));
    }
    return result;
}

struct DiskSnapshot {
    FlatFilePos block;
    FlatFilePos undo;
    BlockHash hash;
    std::unique_ptr<CBlockIndex> undo_index{std::make_unique<CBlockIndex>()};
    std::unique_ptr<CBlockIndex> undo_parent{std::make_unique<CBlockIndex>()};
    std::unique_ptr<BlockHash> undo_parent_hash{std::make_unique<BlockHash>()};
};

DiskSnapshot Snapshot(const CBlockIndex &index) {
    LOCK(cs_main);
    if (fPruneMode) {
        throw std::runtime_error(
            "Complete Chronik requires archival block data");
    }
    DiskSnapshot result;
    result.block = index.GetBlockPos();
    result.undo = index.GetUndoPos();
    result.hash = index.GetBlockHash();
    result.undo_index->nStatus = index.nStatus;
    result.undo_index->nFile = index.nFile;
    result.undo_index->nUndoPos = index.nUndoPos;
    if (index.pprev) {
        *result.undo_parent_hash = index.pprev->GetBlockHash();
        result.undo_parent->phashBlock = result.undo_parent_hash.get();
        result.undo_index->pprev = result.undo_parent.get();
    }
    if (result.block.IsNull()) {
        throw std::runtime_error("Native block storage is unavailable");
    }
    return result;
}

void CheckStorage(const CBlockIndex &index, const DiskSnapshot &snapshot) {
    LOCK(cs_main);
    if (index.GetBlockPos() != snapshot.block ||
        index.GetUndoPos() != snapshot.undo ||
        index.GetBlockHash() != snapshot.hash) {
        throw std::runtime_error("Native storage identity changed during read");
    }
}

} // namespace

namespace chronik_bridge {
void log_print(rust::Str, rust::Str, uint32_t, rust::Str message) {
    LogInstance().LogPrintStr(std::string(message));
}

void log_print_chronik(rust::Str function, rust::Str file, uint32_t line,
                       rust::Str message) {
    // The legacy node has no CHRONIK category; use the existing DB category.
    if (LogInstance().WillLogCategory(BCLog::DB)) {
        log_print(function, file, line, message);
    }
}

struct ChronikBridge::StartupSnapshot {
    // std::map keys and heap indexes have stable addresses. All three index
    // pointers (hash, parent, skip) refer only to this owned graph.
    std::map<BlockHash, std::unique_ptr<CBlockIndex>> indexes;
    CChain chain;
    uint64_t generation{0}, revision{0};
};
ChronikBridge::ChronikBridge(const ErgonNodeAccess &node)
    : m_node(node), m_positions(node) {}
ChronikBridge::~ChronikBridge() = default;

void ChronikBridge::capture_startup_snapshot() {
    AssertLockNotHeld(cs_main);
    AssertLockNotHeld(m_node.mempool.cs);
    if (m_startup)
        throw std::runtime_error("Startup snapshot already captured");
    auto owned = std::make_unique<StartupSnapshot>();
    LOCK2(cs_main, m_node.mempool.cs);
    if (m_node.config.GetChainParams().NetworkIDString() != "regtest" ||
        fPruneMode || ShutdownRequested() || m_node.index_failed.load() ||
        ChainActive().Height() < 0 || ChainActive().Height() > 2000 ||
        mapBlockIndex.size() > 4096 || m_node.mempool.size() > 4096 ||
        !pcoinsTip || !ChainActive().GetSourceGeneration() ||
        !m_node.mempool.GetSourceGeneration())
        throw std::runtime_error(
            "Startup snapshot exceeds healthy fixture profile");
    for (const auto &[hash, index] : mapBlockIndex) {
        if (!index || index->GetBlockHash() != hash || index->nHeight < 0 ||
            index->nHeight > 2000)
            throw std::runtime_error("Invalid startup native index identity");
        auto copy = std::make_unique<CBlockIndex>(index->GetBlockHeader());
        copy->nHeight = index->nHeight;
        copy->nStatus = index->nStatus;
        copy->nFile = index->nFile;
        copy->nDataPos = index->nDataPos;
        copy->nUndoPos = index->nUndoPos;
        copy->nTx = index->nTx;
        copy->nChainTx = index->nChainTx;
        auto entry = owned->indexes.emplace(hash, std::move(copy)).first;
        entry->second->phashBlock = &entry->first;
    }
    for (const auto &[hash, index] : mapBlockIndex) {
        auto &copy = owned->indexes.at(hash);
        if (index->pprev) {
            auto parent = owned->indexes.find(index->pprev->GetBlockHash());
            if (parent == owned->indexes.end() ||
                parent->second->nHeight + 1 != copy->nHeight)
                throw std::runtime_error("Invalid startup ancestor graph");
            copy->pprev = parent->second.get();
        } else if (copy->nHeight != 0) {
            throw std::runtime_error("Missing startup ancestor");
        }
    }
    owned->chain.SetTip(
        owned->indexes.at(ChainActive().Tip()->GetBlockHash()).get());
    owned->generation = ChainActive().GetSourceGeneration();
    owned->revision = m_node.mempool.GetSourceGeneration();
    m_startup = std::move(owned);
    m_startup_active = true;
}
void ChronikBridge::finish_startup_snapshot() const {
    AssertLockNotHeld(cs_main);
    AssertLockNotHeld(m_node.mempool.cs);
    LOCK2(cs_main, m_node.mempool.cs);
    if (!m_startup_active || !m_startup || fPruneMode || ShutdownRequested() ||
        m_node.index_failed.load() || !ChainActive().Tip() ||
        ChainActive().Tip()->GetBlockHash() !=
            m_startup->chain.Tip()->GetBlockHash() ||
        ChainActive().GetSourceGeneration() != m_startup->generation ||
        m_node.mempool.GetSourceGeneration() != m_startup->revision)
        throw std::runtime_error("Native source changed during startup resync");
    for (const auto &[hash, copy] : m_startup->indexes) {
        const auto found = mapBlockIndex.find(hash);
        if (found == mapBlockIndex.end() ||
            found->second->nHeight != copy->nHeight ||
            found->second->GetBlockPos() != copy->GetBlockPos() ||
            found->second->GetUndoPos() != copy->GetUndoPos())
            throw std::runtime_error(
                "Native storage changed during startup resync");
    }
    m_startup_active = false;
    // Keep the graph alive until bridge destruction, including exception
    // unwind. No asynchronous work is allowed to retain startup borrows.
}

NativePositionStats ChronikBridge::position_stats() const {
    return m_positions.Stats();
}
void ChronikBridge::position_read_hook_for_testing(std::function<void()> hook) {
    m_positions.BeforeReadForTesting(std::move(hook));
}

uint64_t ChronikBridge::mempool_revision() const {
    if (m_startup_active)
        return m_startup->revision;
    return m_node.mempool.GetSourceGeneration();
}

uint64_t ChronikBridge::chain_generation() const {
    if (m_startup_active)
        return m_startup->generation;
    LOCK(cs_main);
    return ChainActive().GetSourceGeneration();
}

bool ChronikBridge::matches_source(std::array<uint8_t, 32> hash, int32_t height,
                                   uint64_t generation,
                                   uint64_t revision) const {
    TRY_LOCK(cs_main, chain_lock);
    if (!chain_lock)
        return false;
    TRY_LOCK(m_node.mempool.cs, pool_lock);
    if (!pool_lock)
        return false;
    const auto *tip = ChainActive().Tip();
    return tip && generation && revision && !ShutdownRequested() &&
           !fPruneMode &&
           !m_node.index_failed.load(std::memory_order_acquire) &&
           tip->nHeight == height && HashBytes(tip->GetBlockHash()) == hash &&
           ChainActive().GetSourceGeneration() == generation &&
           m_node.mempool.GetSourceGeneration() == revision;
}

const CBlockIndex &ChronikBridge::get_chain_tip() const {
    if (m_startup_active)
        return *m_startup->chain.Tip();
    LOCK(cs_main);
    const auto *tip = ChainActive().Tip();
    if (!tip)
        throw block_index_not_found();
    return *tip;
}

const CBlockIndex &
ChronikBridge::lookup_block_index(std::array<uint8_t, 32> hash) const {
    if (m_startup_active) {
        auto found = m_startup->indexes.find(BlockHash(NativeHash(hash)));
        if (found == m_startup->indexes.end())
            throw block_index_not_found();
        return *found->second;
    }
    LOCK(cs_main);
    auto found = mapBlockIndex.find(BlockHash(NativeHash(hash)));
    if (found == mapBlockIndex.end())
        throw block_index_not_found();
    return *found->second;
}

const CBlockIndex &
ChronikBridge::lookup_block_index_by_height(int height) const {
    if (m_startup_active) {
        const auto *index = m_startup->chain[height];
        if (!index)
            throw block_index_not_found();
        return *index;
    }
    LOCK(cs_main);
    const auto *index = ChainActive()[height];
    if (!index)
        throw block_index_not_found();
    return *index;
}

rust::Vec<RawBlockHeader>
ChronikBridge::get_block_headers_by_range(int start, int end) const {
    if (start < 0 || end < start)
        throw invalid_block_range();
    LOCK(cs_main);
    rust::Vec<RawBlockHeader> result;
    const int last = std::min(end, ChainActive().Height());
    for (int64_t height = start; height <= last; ++height) {
        result.push_back({get_block_header(*ChainActive()[int(height)])});
    }
    return result;
}

rust::Vec<WrappedBlockHash>
ChronikBridge::get_block_hashes_by_range(int start, int end) const {
    if (start < 0 || end < start)
        throw invalid_block_range();
    LOCK(cs_main);
    if (end > ChainActive().Height())
        throw block_index_not_found();
    rust::Vec<WrappedBlockHash> result;
    for (int64_t height = start; height <= end; ++height) {
        result.push_back(
            {HashBytes(ChainActive()[int(height)]->GetBlockHash())});
    }
    return result;
}

std::unique_ptr<CBlock>
ChronikBridge::load_block(const CBlockIndex &index) const {
    const auto snapshot = Snapshot(index);
    auto block = std::make_unique<CBlock>();
    bool mutated = false;
    if (!ReadBlockFromDisk(*block, snapshot.block, Params().GetConsensus()) ||
        block->GetHash() != snapshot.hash ||
        BlockMerkleRoot(*block, &mutated) != block->hashMerkleRoot || mutated) {
        throw std::runtime_error(
            "Native block read or hash verification failed");
    }
    CheckStorage(index, snapshot);
    return block;
}

std::unique_ptr<CBlockUndo>
ChronikBridge::load_block_undo(const CBlockIndex &index) const {
    auto undo = std::make_unique<CBlockUndo>();
    if (index.nHeight == 0)
        return undo;
    const auto snapshot = Snapshot(index);
    if (!snapshot.undo_index->pprev ||
        !UndoReadFromDisk(*undo, snapshot.undo_index.get())) {
        throw std::runtime_error(
            "Native undo read or checksum verification failed");
    }
    CheckStorage(index, snapshot);
    return undo;
}

Tx ChronikBridge::load_tx(uint32_t file, uint32_t position,
                          uint32_t undo) const {
    auto loaded = m_positions.Read(file, position, &undo);
    return Transaction(*loaded.tx, loaded.coins);
}

rust::Vec<uint8_t> ChronikBridge::load_raw_tx(uint32_t file,
                                              uint32_t position) const {
    auto loaded = m_positions.Read(file, position, nullptr);
    CDataStream bytes(SER_NETWORK, PROTOCOL_VERSION);
    bytes << *loaded.tx;
    return Bytes(bytes);
}

bool ChronikBridge::is_avalanche_finalized_preconsensus(
    const std::array<uint8_t, 32> &) const {
    return false; // Ergon has no Avalanche finality; this is not PoW finality.
}

const CBlockIndex &ChronikBridge::find_fork(const CBlockIndex &index) const {
    if (m_startup_active) {
        auto found = m_startup->indexes.find(index.GetBlockHash());
        if (found == m_startup->indexes.end() || found->second.get() != &index)
            throw block_index_not_found();
        const auto *fork = m_startup->chain.FindFork(&index);
        if (!fork)
            throw block_index_not_found();
        return *fork;
    }
    LOCK(cs_main);
    const auto *fork = ChainActive().FindFork(&index);
    if (!fork)
        throw block_index_not_found();
    return *fork;
}

void ChronikBridge::lookup_spent_coins(Tx &, rust::Vec<OutPoint> &,
                                       rust::Vec<OutPoint> &) const {
    throw std::runtime_error("501: Chronik submission preflight is disabled");
}
void ChronikBridge::uncache_coins(rust::Slice<const OutPoint>) const {
    throw std::runtime_error("501: Chronik submission preflight is disabled");
}
std::array<uint8_t, 32> ChronikBridge::broadcast_tx(rust::Slice<const uint8_t>,
                                                    int64_t) const {
    throw std::runtime_error(
        "501: Chronik broadcast is disabled; use native Ergon submission");
}

void ChronikBridge::fatal_error(rust::Str message, rust::Str detail) const {
    m_node.index_failed.store(true, std::memory_order_release);
    LogPrintf("Chronik service failed: %s: %s\n", std::string(message),
              std::string(detail));
    // No AbortNode/StartShutdown: the service owner must close requests.
}
bool ChronikBridge::shutdown_requested() const {
    return ShutdownRequested() ||
           m_node.index_failed.load(std::memory_order_acquire);
}
WrappedBlockHash ChronikBridge::get_genesis_hash() const {
    return {HashBytes(Params().GenesisBlock().GetHash())};
}
int64_t ChronikBridge::estimate_feerate_sats_per_kb() const {
    return m_node.mempool.estimateFee().GetFeePerK() / FIXOSHI;
}
int64_t ChronikBridge::min_relay_feerate_sats_per_kb() const {
    return minRelayTxFee.GetFeePerK() / FIXOSHI;
}
bool ChronikBridge::get_feerate_info(std::array<uint8_t, 32> hash,
                                     int64_t &rate, uint32_t &size) const {
    LOCK(m_node.mempool.cs);
    const auto entry = m_node.mempool.GetIter(TxId(NativeHash(hash)));
    if (!entry)
        return false;
    const auto bytes = (**entry).GetTxVirtualSize();
    if (bytes > std::numeric_limits<uint32_t>::max())
        return false;
    size = uint32_t(bytes);
    rate = CFeeRate((**entry).GetModifiedFee(), bytes).GetFeePerK() / FIXOSHI;
    return true;
}
void ChronikBridge::sync_with_validation_interface_queue() const {
    ::SyncWithValidationInterfaceQueue();
    // This drains the node queue, not the Chronik collector's worker queue.
}
std::unique_ptr<ChronikBridge> make_bridge(const ErgonNodeAccess &node) {
    return std::make_unique<ChronikBridge>(node);
}
Tx bridge_tx(const CTransaction &tx, const std::vector<::Coin> &coins) {
    return Transaction(tx, coins);
}

Block bridge_block(const CBlock &block, const CBlockUndo &undo,
                   const CBlockIndex &index) {
    if (block.vtx.empty() || !block.vtx.front()->IsCoinBase() ||
        block.GetHash() != index.GetBlockHash() ||
        undo.vtxundo.size() != block.vtx.size() - 1) {
        throw std::runtime_error("Block identity or undo cardinality mismatch");
    }
    const auto snapshot = Snapshot(index);
    if (index.nHeight > 0 && snapshot.undo.IsNull()) {
        throw std::runtime_error("Native undo storage is unavailable");
    }
    Block result;
    result.hash = HashBytes(block.GetHash());
    result.prev_hash = HashBytes(block.hashPrevBlock);
    result.n_bits = block.nBits;
    result.timestamp = block.GetBlockTime();
    result.height = index.nHeight;
    result.file_num = uint32_t(snapshot.block.nFile);
    result.data_pos = snapshot.block.nPos;
    result.undo_pos = snapshot.undo.IsNull() ? 0 : snapshot.undo.nPos;
    result.size = ::GetSerializeSize(block, CLIENT_VERSION);
    size_t data = snapshot.block.nPos +
                  ::GetSerializeSize(CBlockHeader(), CLIENT_VERSION) +
                  GetSizeOfCompactSize(block.vtx.size());
    size_t undo_position =
        index.nHeight > 0
            ? snapshot.undo.nPos + GetSizeOfCompactSize(block.vtx.size() - 1)
            : 0;
    const std::vector<::Coin> empty;
    for (size_t i = 0; i < block.vtx.size(); ++i) {
        if (i != 0 && block.vtx[i]->IsCoinBase()) {
            throw std::runtime_error("Unexpected extra coinbase");
        }
        const auto &coins = i == 0 ? empty : undo.vtxundo[i - 1].vprevout;
        result.txs.push_back({Transaction(*block.vtx[i], coins), Position(data),
                              i == 0 ? 0 : Position(undo_position)});
        data += ::GetSerializeSize(*block.vtx[i], CLIENT_VERSION);
        if (i != 0)
            undo_position +=
                ::GetSerializeSize(undo.vtxundo[i - 1], CLIENT_VERSION);
    }
    CheckStorage(index, snapshot);
    return result;
}

BlockInfo get_block_info(const CBlockIndex &index) {
    return {HashBytes(index.GetBlockHash()), index.nHeight};
}
std::array<uint8_t, 80> get_block_header(const CBlockIndex &index) {
    CDataStream bytes(SER_NETWORK, PROTOCOL_VERSION);
    bytes << index.GetBlockHeader();
    std::array<uint8_t, 80> header{};
    std::copy_n(bytes.begin(), header.size(), header.begin());
    return header;
}
const CBlockIndex &get_block_ancestor(const CBlockIndex &index,
                                      int32_t height) {
    const auto *ancestor = index.GetAncestor(height);
    if (!ancestor)
        throw block_index_not_found();
    return *ancestor;
}
rust::Vec<uint8_t> compress_script(rust::Slice<const uint8_t> bytes) {
    std::vector<uint8_t> input(bytes.begin(), bytes.end());
    CScript script(input.begin(), input.end());
    CDataStream compressed(SER_DISK, CLIENT_VERSION);
    compressed << CScriptCompressor(script);
    return Bytes(compressed);
}
rust::Vec<uint8_t> decompress_script(rust::Slice<const uint8_t> bytes) {
    std::vector<uint8_t> input(bytes.begin(), bytes.end());
    CDataStream compressed(input, SER_DISK, CLIENT_VERSION);
    CScript script;
    // Decode the exact native format without CScriptCompressor's lossy
    // oversized-script replacement (OP_RETURN) or ignored EC decode failure.
    unsigned int size = 0;
    compressed >> VARINT(size);
    if (size < 6) {
        std::vector<uint8_t> special(GetSpecialScriptSize(size));
        compressed >> MakeSpan(special);
        if (!DecompressScript(script, size, special)) {
            throw std::runtime_error("Invalid native compressed public key");
        }
    } else {
        size -= 6;
        if (size != compressed.size()) {
            throw std::runtime_error("Compressed script length mismatch");
        }
        script.resize(size);
        compressed >> MakeSpan(script);
    }
    if (!compressed.empty())
        throw std::runtime_error("Trailing compressed script bytes");
    return Bytes(script);
}
int64_t calc_fee(size_t bytes, int64_t rate) {
    // No exception crosses this non-Result FFI. Reject unrepresentable input
    // with the explicit unavailable sentinel instead of native signed overflow.
    if (bytes > uint64_t(std::numeric_limits<int64_t>::max()) ||
        rate == std::numeric_limits<int64_t>::min() ||
        (rate != 0 && bytes > uint64_t(std::numeric_limits<int64_t>::max() /
                                       std::abs(rate))))
        return -1;
    return CFeeRate(rate * FIXOSHI).GetFee(bytes) / FIXOSHI;
}
int64_t default_max_raw_tx_fee_rate_per_kb() {
    // Legacy Ergon supplies an absolute fee ceiling, not the donor rate
    // default. Submission is disabled: do not fabricate an equivalent policy.
    return -1;
}
bool init_error(rust::Str message) {
    LogPrintf("Chronik setup failed: %s\n", std::string(message));
    return false;
}
rust::String client_name() { return CLIENT_NAME; }
rust::String format_full_version() { return FormatFullVersion(); }
} // namespace chronik_bridge

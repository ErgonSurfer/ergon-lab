// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#include <chronik-bridge/src/ffi.rs.h>
#include <chronik-cpp/chronik_bridge.h>
#include <chronik-cpp/ergon_node_access.h>
#include <chronik_lib/src/ffi.rs.h>
#include <clientversion.h>
#include <coins.h>
#include <config.h>
#include <consensus/validation.h>
#include <feerate.h>
#include <flatfile.h>
#include <primitives/block.h>
#include <script/sign.h>
#include <shutdown.h>
#include <streams.h>
#include <test/setup_common.h>
#include <txmempool.h>
#include <undo.h>
#include <util/system.h>
#include <validation.h>
#include <version.h>

#include <algorithm>
#include <boost/test/unit_test.hpp>
#include <chrono>
#include <future>
#include <limits>
#include <thread>

namespace {
struct NativeBridgeBasic : BasicTestingSetup {
    NativeBridgeBasic() : BasicTestingSetup(CBaseChainParams::REGTEST) {}
};
template <typename Hash> std::array<uint8_t, 32> RawHash(const Hash &hash) {
    std::array<uint8_t, 32> bytes;
    std::copy_n(hash.begin(), bytes.size(), bytes.begin());
    return bytes;
}
template <typename A, typename B> bool SameBytes(const A &a, const B &b) {
    return a.size() == b.size() &&
           std::equal(a.begin(), a.end(), b.begin(), [](auto left, auto right) {
               return static_cast<uint8_t>(left) == static_cast<uint8_t>(right);
           });
}
// Restore one byte in fixture-owned native storage before teardown/reorg.
// No operator file, generated baseline golden or private path is involved.
class ChangedByte {
    CAutoFile file;
    unsigned int position;
    uint8_t original;

  public:
    ChangedByte(FlatFilePos pos, bool undo, uint8_t mask = 1)
        : file(undo ? FlatFileSeq(GetBlocksDir(), "rev", 1).Open(pos)
                    : OpenBlockFile(pos),
               SER_DISK, CLIENT_VERSION),
          position(pos.nPos) {
        if (file.IsNull())
            throw std::runtime_error("Fixture storage unavailable");
        file >> original;
        if (fseek(file.Get(), position, SEEK_SET))
            throw std::runtime_error("Fixture storage seek failed");
        file << uint8_t(original ^ mask);
        if (fflush(file.Get()))
            throw std::runtime_error("Fixture storage flush failed");
    }
    ~ChangedByte() {
        if (fseek(file.Get(), position, SEEK_SET) ||
            fwrite(&original, 1, 1, file.Get()) != 1 || fflush(file.Get()))
            std::terminate();
    }
};
// Threads are joined after Read has unwound, including a failed lock-release
// oracle. A failed oracle must not detach or deadlock during hook unwinding.
struct JoinedPositionReaders {
    std::vector<std::thread> threads;
    ~JoinedPositionReaders() {
        for (auto &thread : threads) thread.join();
    }
};
struct NativeCoinbasePosition {
    uint32_t file, position;
    CTransactionRef tx;
};
NativeCoinbasePosition CoinbasePosition(chronik_bridge::ErgonNodeAccess &node) {
    auto bridge = chronik_bridge::make_bridge(node);
    const auto &tip = bridge->get_chain_tip();
    auto block = bridge->load_block(tip);
    auto undo = bridge->load_block_undo(tip);
    const auto projection = chronik_bridge::bridge_block(*block, *undo, tip);
    return {projection.file_num, projection.txs[0].data_pos, block->vtx[0]};
}
} // namespace

BOOST_FIXTURE_TEST_SUITE(chronik_native_upstream_bridge_tests,
                         NativeBridgeBasic)

BOOST_AUTO_TEST_CASE(transaction_wire_units_and_spent_coins) {
    CMutableTransaction mutable_tx;
    mutable_tx.nVersion = std::numeric_limits<int32_t>::min();
    mutable_tx.nLockTime = 19;
    mutable_tx.vin.emplace_back(COutPoint(TxId(uint256S("01")), 4),
                                CScript() << OP_1, 23);
    mutable_tx.vout.emplace_back(11 * FIXOSHI, CScript() << OP_RETURN << OP_0);
    const CTransaction tx(mutable_tx);
    std::vector<::Coin> coins;
    coins.emplace_back(CTxOut(7 * FIXOSHI, CScript() << OP_1), 42, false);
    auto converted = chronik_bridge::bridge_tx(tx, coins);
    BOOST_CHECK(converted.txid == RawHash(tx.GetId()));
    BOOST_CHECK_EQUAL(converted.version, uint32_t(0x80000000));
    BOOST_CHECK_EQUAL(converted.locktime, 19);
    BOOST_REQUIRE_EQUAL(converted.inputs.size(), 1);
    BOOST_REQUIRE_EQUAL(converted.outputs.size(), 1);
    BOOST_CHECK_EQUAL(converted.inputs[0].prev_out.out_idx, 4);
    BOOST_CHECK_EQUAL(converted.inputs[0].sequence, 23);
    BOOST_CHECK_EQUAL(converted.inputs[0].coin.output.sats, 7);
    BOOST_CHECK_EQUAL(converted.inputs[0].coin.height, 42);
    BOOST_CHECK_EQUAL(converted.outputs[0].sats, 11);
    BOOST_CHECK(
        SameBytes(converted.outputs[0].script, tx.vout[0].scriptPubKey));
    BOOST_CHECK_THROW(chronik_bridge::bridge_tx(tx, {}), std::runtime_error);
    coins.emplace_back(CTxOut(1 * FIXOSHI, CScript() << OP_1), 42, false);
    BOOST_CHECK_THROW(chronik_bridge::bridge_tx(tx, coins), std::runtime_error);
    coins.resize(1);
    coins[0] = ::Coin();
    BOOST_CHECK_THROW(chronik_bridge::bridge_tx(tx, coins), std::runtime_error);
    coins[0] =
        ::Coin(CTxOut(0 * FIXOSHI, CScript() << OP_1), MEMPOOL_HEIGHT, false);
    converted = chronik_bridge::bridge_tx(tx, coins);
    BOOST_CHECK_EQUAL(converted.inputs[0].coin.height, -1);
    BOOST_CHECK_EQUAL(converted.inputs[0].coin.output.sats, 0);
}

BOOST_AUTO_TEST_CASE(script_roundtrip_and_malformed_rejection) {
    const CScript script = CScript()
                           << OP_RETURN << std::vector<uint8_t>{1, 2, 3};
    auto compressed =
        chronik_bridge::compress_script({script.data(), script.size()});
    auto decoded = chronik_bridge::decompress_script(
        {compressed.data(), compressed.size()});
    BOOST_CHECK(SameBytes(script, decoded));
    compressed.push_back(0);
    BOOST_CHECK_THROW(chronik_bridge::decompress_script(
                          {compressed.data(), compressed.size()}),
                      std::runtime_error);
    const std::vector<uint8_t> truncated{0};
    BOOST_CHECK_THROW(
        chronik_bridge::decompress_script({truncated.data(), truncated.size()}),
        std::exception);
    const std::vector<uint8_t> large(MAX_SCRIPT_SIZE + 1, OP_NOP);
    auto large_compressed =
        chronik_bridge::compress_script({large.data(), large.size()});
    auto large_decoded = chronik_bridge::decompress_script(
        {large_compressed.data(), large_compressed.size()});
    BOOST_CHECK(SameBytes(large, large_decoded));
    std::vector<uint8_t> invalid_key(33, 0xff);
    invalid_key[0] = 4;
    BOOST_CHECK_THROW(chronik_bridge::decompress_script(
                          {invalid_key.data(), invalid_key.size()}),
                      std::runtime_error);
}

BOOST_AUTO_TEST_CASE(native_fee_parity_and_overflow_rejection) {
    for (const int64_t rate : {0, 1, 1000, -1000}) {
        for (const size_t bytes :
             {size_t(0), size_t(1), size_t(250), size_t(1000)}) {
            BOOST_CHECK_EQUAL(chronik_bridge::calc_fee(bytes, rate),
                              CFeeRate(rate * FIXOSHI).GetFee(bytes) / FIXOSHI);
        }
    }
    BOOST_CHECK_EQUAL(
        chronik_bridge::calc_fee(std::numeric_limits<size_t>::max(), 2), -1);
    BOOST_CHECK_EQUAL(
        chronik_bridge::calc_fee(1, std::numeric_limits<int64_t>::min()), -1);
    BOOST_CHECK_EQUAL(chronik_bridge::default_max_raw_tx_fee_rate_per_kb(), -1);
}

BOOST_AUTO_TEST_CASE(runtime_admission_and_failure_isolation) {
    chronik_bridge::ErgonNodeAccess node(GetConfig(), g_mempool);
    auto bridge = chronik_bridge::make_bridge(node);
    BOOST_CHECK(!chronik_bridge::upstream_runtime_permitted(node));
    chronik_bridge::SetupParams params{};
    params.net = chronik_bridge::Net::Regtest;
    // These deliberately unusable paths must never be opened by setup.
    params.datadir = "";
    params.datadir_net = "";
    BOOST_CHECK(!chronik_bridge::setup_chronik(std::move(params), node));
    BOOST_CHECK(!node.index_failed.load());
    BOOST_CHECK(!bridge->is_avalanche_finalized_preconsensus({}));
    int64_t rate = 0;
    uint32_t size = 0;
    BOOST_CHECK(!bridge->get_feerate_info({}, rate, size));
    BOOST_CHECK_EQUAL(bridge->min_relay_feerate_sats_per_kb(),
                      minRelayTxFee.GetFeePerK() / FIXOSHI);
    chronik_bridge::Tx tx;
    rust::Vec<chronik_bridge::OutPoint> missing, uncache;
    BOOST_CHECK_THROW(bridge->lookup_spent_coins(tx, missing, uncache),
                      std::runtime_error);
    BOOST_CHECK_THROW(bridge->broadcast_tx({}, 0), std::runtime_error);
    const bool shutdown_before = ShutdownRequested();
    bridge->fatal_error("synthetic test failure", "local unit test");
    BOOST_CHECK(node.index_failed.load());
    BOOST_CHECK(bridge->shutdown_requested());
    BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown_before);
    BOOST_CHECK(!chronik_bridge::init_error("synthetic setup failure"));
    BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown_before);
}
BOOST_AUTO_TEST_SUITE_END()

BOOST_FIXTURE_TEST_SUITE(chronik_native_upstream_disk_tests, TestChain100Setup)
BOOST_AUTO_TEST_CASE(native_block_undo_and_positioned_coinbase_reads) {
    chronik_bridge::ErgonNodeAccess node(GetConfig(), g_mempool);
    auto bridge = chronik_bridge::make_bridge(node);
    const auto &tip = bridge->get_chain_tip();
    BOOST_REQUIRE_EQUAL(tip.nHeight, 100);
    auto block = bridge->load_block(tip);
    auto undo = bridge->load_block_undo(tip);
    auto projected = chronik_bridge::bridge_block(*block, *undo, tip);
    BOOST_CHECK(projected.hash == RawHash(block->GetHash()));
    BOOST_CHECK_EQUAL(projected.height, 100);
    BOOST_REQUIRE_EQUAL(projected.txs.size(), 1);
    const auto &position = projected.txs[0];
    auto tx = bridge->load_tx(projected.file_num, position.data_pos,
                              position.undo_pos);
    BOOST_CHECK(tx.txid == RawHash(block->vtx[0]->GetId()));
    auto raw = bridge->load_raw_tx(projected.file_num, position.data_pos);
    CDataStream native(SER_NETWORK, PROTOCOL_VERSION);
    native << *block->vtx[0];
    BOOST_CHECK(SameBytes(raw, native));
    BOOST_CHECK_THROW(
        bridge->load_raw_tx(projected.file_num, position.data_pos + 1),
        std::runtime_error);
    BOOST_CHECK_THROW(bridge->load_tx(projected.file_num, position.data_pos, 1),
                      std::runtime_error);
    BOOST_CHECK(&bridge->lookup_block_index(projected.hash) == &tip);
    BOOST_CHECK(&bridge->lookup_block_index_by_height(100) == &tip);
    BOOST_CHECK(&chronik_bridge::get_block_ancestor(tip, 99) ==
                &bridge->lookup_block_index_by_height(99));
    BOOST_CHECK_EQUAL(bridge->get_block_headers_by_range(99, 100).size(), 2);
    BOOST_CHECK_EQUAL(bridge->get_block_hashes_by_range(99, 100).size(), 2);
    BOOST_CHECK_THROW(bridge->get_block_headers_by_range(-1, 0),
                      chronik_bridge::invalid_block_range);
    BOOST_CHECK_THROW(bridge->lookup_block_index_by_height(101),
                      chronik_bridge::block_index_not_found);
    const auto &genesis = bridge->lookup_block_index_by_height(0);
    BOOST_CHECK(bridge->get_genesis_hash().data ==
                RawHash(genesis.GetBlockHash()));
    BOOST_CHECK(bridge->load_block_undo(genesis)->vtxundo.empty());
    CBlockUndo wrong_undo;
    wrong_undo.vtxundo.resize(1);
    BOOST_CHECK_THROW(chronik_bridge::bridge_block(*block, wrong_undo, tip),
                      std::runtime_error);
    {
        LOCK(cs_main);
        const bool prune_before = fPruneMode;
        fPruneMode = true;
        BOOST_CHECK_THROW(bridge->load_block(tip), std::runtime_error);
        fPruneMode = prune_before;
    }

    // A real accepted zero-value spend exercises native checksum-checked undo,
    // not only the coinbase special case or a synthetic serialization fixture.
    const auto &prev = *m_coinbase_txns[0];
    CMutableTransaction spend;
    spend.vin.emplace_back(COutPoint(prev.GetId(), 0));
    spend.vout.emplace_back(prev.vout[0].nValue, CScript() << OP_1);
    const auto digest =
        SignatureHash(prev.vout[0].scriptPubKey, CTransaction(spend), 0,
                      SigHashType().withForkId(), prev.vout[0].nValue);
    std::vector<uint8_t> signature;
    BOOST_REQUIRE(coinbaseKey.SignECDSA(digest, signature));
    signature.push_back(uint8_t(SIGHASH_ALL | SIGHASH_FORKID));
    spend.vin[0].scriptSig << signature;
    const auto spent_block =
        CreateAndProcessBlock({spend}, prev.vout[0].scriptPubKey);
    const auto &spent_tip = bridge->get_chain_tip();
    BOOST_REQUIRE_EQUAL(spent_tip.nHeight, 101);
    BOOST_REQUIRE(spent_tip.GetBlockHash() == spent_block.GetHash());
    auto spent_undo = bridge->load_block_undo(spent_tip);
    BOOST_REQUIRE_EQUAL(spent_undo->vtxundo.size(), 1);
    const auto spent_projection =
        chronik_bridge::bridge_block(spent_block, *spent_undo, spent_tip);
    BOOST_REQUIRE_EQUAL(spent_projection.txs.size(), 2);
    const auto &spent_position = spent_projection.txs[1];
    const auto loaded_spend =
        bridge->load_tx(spent_projection.file_num, spent_position.data_pos,
                        spent_position.undo_pos);
    BOOST_CHECK(loaded_spend.txid == RawHash(CTransaction(spend).GetId()));
    BOOST_REQUIRE_EQUAL(loaded_spend.inputs.size(), 1);
    BOOST_CHECK_EQUAL(loaded_spend.inputs[0].coin.output.sats,
                      prev.vout[0].nValue / FIXOSHI);
    BOOST_CHECK_EQUAL(loaded_spend.inputs[0].coin.height, 1);
    BOOST_CHECK(loaded_spend.inputs[0].coin.is_coinbase);
    BOOST_CHECK_THROW(bridge->load_tx(spent_projection.file_num,
                                      spent_position.data_pos,
                                      spent_position.undo_pos + 1),
                      std::runtime_error);
    const auto read_before = bridge->position_stats();
    BOOST_CHECK(bridge
                    ->load_tx(spent_projection.file_num,
                              spent_position.data_pos, spent_position.undo_pos)
                    .txid == loaded_spend.txid);
    const auto read_after = bridge->position_stats();
    BOOST_CHECK_EQUAL(read_after.cold_layouts, read_before.cold_layouts);
    BOOST_CHECK_EQUAL(read_after.warm_reads, read_before.warm_reads + 1);
    BOOST_CHECK_EQUAL(read_after.transaction_bytes -
                          read_before.transaction_bytes,
                      ::GetSerializeSize(CTransaction(spend), CLIENT_VERSION));
    BOOST_CHECK_EQUAL(read_after.undo_bytes - read_before.undo_bytes,
                      8 + ::GetSerializeSize(*spent_undo, CLIENT_VERSION) + 32);
    {
        auto checksum = spent_tip.GetUndoPos();
        checksum.nPos += ::GetSerializeSize(*spent_undo, CLIENT_VERSION);
        ChangedByte corrupt(checksum, true);
        BOOST_CHECK_THROW(bridge->load_tx(spent_projection.file_num,
                                          spent_position.data_pos,
                                          spent_position.undo_pos),
                          std::runtime_error);
        // Raw transaction reads do not invent an undo dependency.
        BOOST_CHECK(!bridge
                         ->load_raw_tx(spent_projection.file_num,
                                       spent_position.data_pos)
                         .empty());
    }
    BOOST_CHECK(bridge
                    ->load_tx(spent_projection.file_num,
                              spent_position.data_pos, spent_position.undo_pos)
                    .txid == loaded_spend.txid);
}

BOOST_AUTO_TEST_CASE(position_catalogue_incremental_and_bounded_cache) {
    chronik_bridge::ErgonNodeAccess node(GetConfig(), g_mempool);
    auto bridge = chronik_bridge::make_bridge(node);
    const auto &tip = bridge->get_chain_tip();
    auto block = bridge->load_block(tip);
    auto undo = bridge->load_block_undo(tip);
    const auto p = chronik_bridge::bridge_block(*block, *undo, tip);
    CDataStream native(SER_NETWORK, PROTOCOL_VERSION);
    native << *block->vtx[0];
    BOOST_CHECK(
        SameBytes(bridge->load_raw_tx(p.file_num, p.txs[0].data_pos), native));
    auto cold = bridge->position_stats();
    BOOST_CHECK_EQUAL(cold.catalogue_added, 101);
    BOOST_CHECK_EQUAL(cold.cold_layouts, 1);
    BOOST_CHECK_EQUAL(cold.warm_reads, 0);
    BOOST_CHECK_EQUAL(cold.cached_layouts, 1);
    BOOST_CHECK_EQUAL(cold.cold_block_bytes,
                      ::GetSerializeSize(*block, CLIENT_VERSION) + 8);
    BOOST_CHECK(
        SameBytes(bridge->load_raw_tx(p.file_num, p.txs[0].data_pos), native));
    auto warm = bridge->position_stats();
    BOOST_CHECK_EQUAL(warm.catalogue_added, cold.catalogue_added);
    BOOST_CHECK_EQUAL(warm.cold_block_bytes, cold.cold_block_bytes);
    BOOST_CHECK_EQUAL(warm.warm_reads, 1);
    BOOST_CHECK_EQUAL(warm.transaction_bytes - cold.transaction_bytes,
                      native.size());
    BOOST_CHECK_EQUAL(warm.undo_bytes, 0);
    CreateAndProcessBlock({}, block->vtx[0]->vout[0].scriptPubKey);
    BOOST_CHECK(
        SameBytes(bridge->load_raw_tx(p.file_num, p.txs[0].data_pos), native));
    BOOST_CHECK_EQUAL(bridge->position_stats().catalogue_added, 102);
    BOOST_CHECK_EQUAL(bridge->position_stats().catalogue_removed, 0);
    for (int h = 90; h < 100; ++h) {
        const auto &index = bridge->lookup_block_index_by_height(h);
        auto other = bridge->load_block(index);
        auto other_undo = bridge->load_block_undo(index);
        auto q = chronik_bridge::bridge_block(*other, *other_undo, index);
        BOOST_CHECK(bridge->load_tx(q.file_num, q.txs[0].data_pos, 0).txid ==
                    RawHash(other->vtx[0]->GetId()));
        BOOST_CHECK_LE(bridge->position_stats().cached_layouts, 8);
    }
    BOOST_CHECK_EQUAL(bridge->position_stats().cached_layouts, 8);
    BOOST_CHECK_THROW(bridge->load_raw_tx(p.file_num, p.txs[0].data_pos - 1),
                      std::runtime_error);
    BOOST_CHECK_THROW(
        bridge->load_raw_tx(std::numeric_limits<uint32_t>::max(), 0),
        std::runtime_error);
    BOOST_CHECK_THROW(
        bridge->load_raw_tx(p.file_num, std::numeric_limits<uint32_t>::max()),
        std::runtime_error);
}

BOOST_AUTO_TEST_CASE(position_corruption_and_storage_drift_fail_closed) {
    chronik_bridge::ErgonNodeAccess node(GetConfig(), g_mempool);
    auto bridge = chronik_bridge::make_bridge(node);
    auto &tip = *ChainActive().Tip();
    auto block = bridge->load_block(tip);
    auto undo = bridge->load_block_undo(tip);
    const auto p = chronik_bridge::bridge_block(*block, *undo, tip);
    const auto pos = p.txs[0].data_pos;
    BOOST_REQUIRE(!bridge->load_raw_tx(p.file_num, pos).empty());
    const bool shutdown = ShutdownRequested();
    for (unsigned int changed :
         {tip.nDataPos - 8, tip.nDataPos - 4, tip.nDataPos, pos + 4,
          pos + unsigned(::GetSerializeSize(*block->vtx[0], CLIENT_VERSION)) -
              1}) {
        ChangedByte corrupt(FlatFilePos(tip.nFile, changed), false);
        BOOST_CHECK_THROW(bridge->load_raw_tx(p.file_num, pos), std::exception);
        // A separate cold reader authenticates the complete native block body.
        auto cold = chronik_bridge::make_bridge(node);
        BOOST_CHECK_THROW(cold->load_raw_tx(p.file_num, pos), std::exception);
    }
    {
        // Oversized advertised record fails before any payload allocation.
        ChangedByte corrupt(FlatFilePos(tip.nFile, tip.nDataPos - 1), false,
                            0x80);
        auto cold = chronik_bridge::make_bridge(node);
        BOOST_CHECK_THROW(cold->load_raw_tx(p.file_num, pos),
                          std::runtime_error);
    }
    {
        // Structurally valid body with altered locktime must fail Merkle check.
        ChangedByte corrupt(
            FlatFilePos(tip.nFile, pos +
                                       unsigned(::GetSerializeSize(
                                           *block->vtx[0], CLIENT_VERSION)) -
                                       1),
            false);
        BOOST_CHECK_THROW(bridge->load_block(tip), std::runtime_error);
    }
    BOOST_CHECK(!bridge->load_raw_tx(p.file_num, pos).empty());
    {
        LOCK(cs_main);
        const auto saved = tip.nDataPos;
        ++tip.nDataPos;
        BOOST_CHECK_THROW(bridge->load_raw_tx(p.file_num, pos),
                          std::runtime_error);
        tip.nDataPos = saved;
        fPruneMode = true;
        BOOST_CHECK_THROW(bridge->load_raw_tx(p.file_num, pos),
                          std::runtime_error);
        fPruneMode = false;
    }
    BOOST_CHECK(!bridge->load_raw_tx(p.file_num, pos).empty());
    node.index_failed = true;
    BOOST_CHECK_THROW(bridge->load_raw_tx(p.file_num, pos), std::runtime_error);
    node.index_failed = false;
    BOOST_CHECK(!bridge->load_raw_tx(p.file_num, pos).empty());
    BOOST_CHECK_EQUAL(ShutdownRequested(), shutdown);
}

BOOST_AUTO_TEST_CASE(position_native_aba_and_suffix_reorg_recovery) {
    chronik_bridge::ErgonNodeAccess node(GetConfig(), g_mempool);
    auto bridge = chronik_bridge::make_bridge(node);
    auto *old = ChainActive().Tip();
    auto block = bridge->load_block(*old);
    auto undo = bridge->load_block_undo(*old);
    const auto p = chronik_bridge::bridge_block(*block, *undo, *old);
    BOOST_REQUIRE(!bridge->load_raw_tx(p.file_num, p.txs[0].data_pos).empty());
    bridge->position_read_hook_for_testing([&] {
        // Actual native changes outside the reader's metadata lock; restoring
        // the identical tip cannot restore the synchronous source generation.
        CValidationState state;
        BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, old));
        BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
        {
            LOCK(cs_main);
            ResetBlockFailureFlags(old);
        }
        BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    });
    BOOST_CHECK_THROW(bridge->load_raw_tx(p.file_num, p.txs[0].data_pos),
                      std::runtime_error);
    bridge->position_read_hook_for_testing({});
    BOOST_CHECK(ChainActive().Tip() == old);
    BOOST_CHECK(!bridge->load_raw_tx(p.file_num, p.txs[0].data_pos).empty());
    CValidationState state;
    BOOST_REQUIRE(InvalidateBlock(GetConfig(), state, old));
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    BOOST_CHECK_THROW(bridge->load_raw_tx(p.file_num, p.txs[0].data_pos),
                      std::runtime_error);
    const auto removed = bridge->position_stats();
    BOOST_CHECK_EQUAL(removed.catalogue_removed, 1);
    {
        LOCK(cs_main);
        ResetBlockFailureFlags(old);
    }
    BOOST_REQUIRE(ActivateBestChain(GetConfig(), state));
    BOOST_CHECK(!bridge->load_raw_tx(p.file_num, p.txs[0].data_pos).empty());
    const auto recovered = bridge->position_stats();
    BOOST_CHECK_EQUAL(recovered.catalogue_added, 102);
    BOOST_CHECK_EQUAL(recovered.cold_layouts, removed.cold_layouts + 1);
    BOOST_CHECK_LE(recovered.cached_layouts, 8);
}
BOOST_AUTO_TEST_SUITE_END()

BOOST_FIXTURE_TEST_SUITE(chronik_native_position_catalogue_tests, TestChain100Setup)
BOOST_AUTO_TEST_CASE(position_catalogue_batches_release_native_locks) {
    chronik_bridge::ErgonNodeAccess node(GetConfig(), g_mempool);
    const auto input = CoinbasePosition(node);
    chronik_bridge::NativePositionReader reader(node);
    JoinedPositionReaders peers;
    reader.AfterCatalogueBatchForTesting([&] {
        auto ready = std::make_shared<std::promise<bool>>();
        auto observed = ready->get_future();
        peers.threads.emplace_back([ready] {
            LOCK2(cs_main, g_mempool.cs);
            ready->set_value(true);
        });
        if (observed.wait_for(std::chrono::seconds(2)) != std::future_status::ready ||
            !observed.get())
            throw std::runtime_error("Native locks held across catalogue batch");
    });
    BOOST_CHECK(reader.Read(input.file, input.position, nullptr).tx->GetId() == input.tx->GetId());
    const auto cold = reader.Stats();
    BOOST_CHECK_EQUAL(cold.catalogue_batches, 2);
    BOOST_CHECK_EQUAL(cold.maximum_catalogue_batch, 64);
    BOOST_CHECK_EQUAL(cold.catalogue_added, 101);
    BOOST_CHECK_EQUAL(cold.catalogue_aborts, 0);
    BOOST_CHECK_EQUAL(peers.threads.size(), 2);
    reader.AfterCatalogueBatchForTesting({});
    BOOST_CHECK(reader.Read(input.file, input.position, nullptr).tx->GetId() == input.tx->GetId());
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_batches, cold.catalogue_batches);
    CreateAndProcessBlock({}, input.tx->vout[0].scriptPubKey);
    BOOST_CHECK(reader.Read(input.file, input.position, nullptr).tx->GetId() == input.tx->GetId());
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_added, 102);
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_removed, 0);
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_batches, 3);
}

BOOST_AUTO_TEST_CASE(position_catalogue_staging_rejects_native_aba) {
    chronik_bridge::ErgonNodeAccess node(GetConfig(), g_mempool);
    const auto input = CoinbasePosition(node);
    chronik_bridge::NativePositionReader reader(node);
    auto *original = ChainActive().Tip();
    bool changed = false;
    reader.AfterCatalogueBatchForTesting([&] {
        if (changed) return;
        changed = true;
        CValidationState state;
        if (!InvalidateBlock(GetConfig(), state, original) ||
            !ActivateBestChain(GetConfig(), state))
            throw std::runtime_error("Native ABA disconnect failed");
        {
            LOCK(cs_main);
            ResetBlockFailureFlags(original);
        }
        if (!ActivateBestChain(GetConfig(), state))
            throw std::runtime_error("Native ABA restoration failed");
    });
    BOOST_CHECK_THROW(reader.Read(input.file, input.position, nullptr), std::runtime_error);
    BOOST_CHECK(changed);
    BOOST_CHECK(ChainActive().Tip() == original);
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_added, 0);
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_aborts, 1);
    reader.AfterCatalogueBatchForTesting({});
    BOOST_CHECK(reader.Read(input.file, input.position, nullptr).tx->GetId() == input.tx->GetId());
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_added, 101);
    BOOST_CHECK(!node.index_failed.load());
}

BOOST_AUTO_TEST_CASE(position_catalogue_final_fence_rejects_cancelled_source) {
    chronik_bridge::ErgonNodeAccess node(GetConfig(), g_mempool);
    const auto input = CoinbasePosition(node);
    chronik_bridge::NativePositionReader reader(node);
    size_t batches = 0;
    reader.AfterCatalogueBatchForTesting([&] {
        if (++batches == 2) node.index_failed = true;
    });
    BOOST_CHECK_THROW(reader.Read(input.file, input.position, nullptr), std::runtime_error);
    BOOST_CHECK_EQUAL(batches, 2);
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_added, 0);
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_aborts, 1);
    node.index_failed = false;
    reader.AfterCatalogueBatchForTesting({});
    BOOST_CHECK(reader.Read(input.file, input.position, nullptr).tx->GetId() == input.tx->GetId());
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_added, 101);
    BOOST_CHECK(!node.index_failed.load());
}

BOOST_AUTO_TEST_CASE(position_catalogue_final_fence_rejects_native_progress) {
    chronik_bridge::ErgonNodeAccess node(GetConfig(), g_mempool);
    const auto input = CoinbasePosition(node);
    chronik_bridge::NativePositionReader reader(node);
    size_t batches = 0;
    reader.AfterCatalogueBatchForTesting([&] {
        if (++batches == 2)
            CreateAndProcessBlock({}, input.tx->vout[0].scriptPubKey);
    });
    BOOST_CHECK_THROW(reader.Read(input.file, input.position, nullptr), std::runtime_error);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 101);
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_added, 0);
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_aborts, 1);
    reader.AfterCatalogueBatchForTesting({});
    BOOST_CHECK(reader.Read(input.file, input.position, nullptr).tx->GetId() == input.tx->GetId());
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_added, 102);
    BOOST_CHECK(!node.index_failed.load());
}

BOOST_AUTO_TEST_CASE(position_catalogue_storage_drift_is_not_a_generation_fence) {
    chronik_bridge::ErgonNodeAccess node(GetConfig(), g_mempool);
    const auto input = CoinbasePosition(node);
    chronik_bridge::NativePositionReader reader(node);
    auto *tip = ChainActive().Tip();
    const auto data_position = tip->nDataPos;
    const auto generation = ChainActive().GetSourceGeneration();
    size_t batches = 0;
    reader.AfterCatalogueBatchForTesting([&] {
        if (++batches == 2) {
            LOCK(cs_main);
            ++tip->nDataPos;
        }
    });
    BOOST_CHECK_THROW(reader.Read(input.file, input.position, nullptr), std::runtime_error);
    {
        LOCK(cs_main);
        tip->nDataPos = data_position;
    }
    BOOST_CHECK_EQUAL(ChainActive().GetSourceGeneration(), generation);
    BOOST_CHECK_EQUAL(reader.Stats().cold_layouts, 0);
    reader.AfterCatalogueBatchForTesting({});
    BOOST_CHECK(reader.Read(input.file, input.position, nullptr).tx->GetId() == input.tx->GetId());
    BOOST_CHECK(!node.index_failed.load());
}

BOOST_AUTO_TEST_CASE(position_catalogue_concurrent_refresh_waits_without_native_locks) {
    chronik_bridge::ErgonNodeAccess node(GetConfig(), g_mempool);
    const auto input = CoinbasePosition(node);
    chronik_bridge::NativePositionReader reader(node);
    JoinedPositionReaders peers;
    bool tested = false;
    std::future<bool> queued_result;
    reader.AfterCatalogueBatchForTesting([&] {
        if (tested) return;
        tested = true;
        auto ready = std::make_shared<std::promise<bool>>();
        queued_result = ready->get_future();
        peers.threads.emplace_back([&, ready] {
            try {
                ready->set_value(reader.Read(input.file, input.position, nullptr).tx->GetId() == input.tx->GetId());
            } catch (...) {
                ready->set_value(false);
            }
        });
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(2);
        while (reader.Stats().catalogue_refresh_requests < 2 &&
               std::chrono::steady_clock::now() < deadline)
            std::this_thread::yield();
        if (reader.Stats().catalogue_refresh_requests != 2)
            throw std::runtime_error("Concurrent reader did not reach refresh queue");
        auto unlocked = std::make_shared<std::promise<bool>>();
        auto observed = unlocked->get_future();
        peers.threads.emplace_back([unlocked] {
            LOCK2(cs_main, g_mempool.cs);
            unlocked->set_value(true);
        });
        if (observed.wait_for(std::chrono::seconds(2)) != std::future_status::ready ||
            !observed.get())
            throw std::runtime_error("Queued reader retained native locks");
    });
    BOOST_CHECK(reader.Read(input.file, input.position, nullptr).tx->GetId() == input.tx->GetId());
    BOOST_CHECK(tested);
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_aborts, 0);
    BOOST_CHECK(queued_result.get());
    reader.AfterCatalogueBatchForTesting({});
    std::vector<std::future<bool>> results;
    for (int i = 0; i < 4; ++i) {
        auto ready = std::make_shared<std::promise<bool>>();
        results.push_back(ready->get_future());
        peers.threads.emplace_back([&, ready] {
            try {
                ready->set_value(reader.Read(input.file, input.position, nullptr).tx->GetId() == input.tx->GetId());
            } catch (...) {
                ready->set_value(false);
            }
        });
    }
    for (auto &result : results) BOOST_CHECK(result.get());
    BOOST_CHECK_EQUAL(reader.Stats().catalogue_added, 101);
    BOOST_CHECK_EQUAL(reader.Stats().cached_layouts, 1);
    BOOST_CHECK(!node.index_failed.load());
}
BOOST_AUTO_TEST_SUITE_END()

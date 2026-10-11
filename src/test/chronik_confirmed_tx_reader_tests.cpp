// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#include <chronik/confirmed-tx-reader.h>

#include <coins.h>
#include <primitives/block.h>
#include <script/script.h>
#include <undo.h>

#include <boost/test/unit_test.hpp>

#include <memory>
#include <string>
#include <vector>

namespace {

CTransactionRef MakeTx(const TxId &prev_txid, uint32_t prev_index,
                       Amount value, uint8_t marker) {
    CMutableTransaction tx;
    tx.vin.emplace_back(prev_txid, prev_index,
                        CScript() << std::vector<uint8_t>{marker});
    tx.vout.emplace_back(value,
                         CScript() << OP_RETURN << std::vector<uint8_t>{marker});
    return MakeTransactionRef(std::move(tx));
}

CBlock MakeBlock() {
    CBlock block;
    block.nVersion = 1;
    block.nTime = 123456;
    block.nBits = 0x207fffff;
    block.nNonce = 7;
    block.vtx.push_back(MakeTx(TxId{}, COutPoint::NULL_INDEX, 0 * FIXOSHI, 1));
    block.vtx.push_back(MakeTx(block.vtx[0]->GetId(), 0, 3 * FIXOSHI, 2));
    block.vtx.push_back(MakeTx(block.vtx[1]->GetId(), 0, 2 * FIXOSHI, 3));
    return block;
}

CBlockUndo MakeUndo(const CBlock &block) {
    CBlockUndo undo;
    undo.vtxundo.resize(2);
    undo.vtxundo[0].vprevout.emplace_back(
        CTxOut(4 * FIXOSHI, CScript() << OP_TRUE), 10, false);
    undo.vtxundo[1].vprevout.emplace_back(
        CTxOut(3 * FIXOSHI, CScript() << OP_TRUE), 11, false);
    BOOST_REQUIRE_EQUAL(block.vtx[1]->vin.size(), 1);
    BOOST_REQUIRE_EQUAL(block.vtx[2]->vin.size(), 1);
    return undo;
}

} // namespace

BOOST_AUTO_TEST_SUITE(chronik_confirmed_tx_reader_tests)

BOOST_AUTO_TEST_CASE(selects_coinbase_without_undo) {
    const CBlock block = MakeBlock();
    std::string error;
    const auto result = chronik::SelectConfirmedTx(
        block, nullptr, block.GetHash(), 12, block.vtx[0]->GetId(), error);

    BOOST_REQUIRE(result);
    BOOST_CHECK(error.empty());
    BOOST_CHECK_EQUAL(result->tx->GetId().ToString(),
                      block.vtx[0]->GetId().ToString());
    BOOST_CHECK(result->spent_coins.empty());
    BOOST_CHECK_EQUAL(result->block_height, 12);
    BOOST_CHECK_EQUAL(result->block_timestamp, 123456);
    BOOST_CHECK_EQUAL(result->tx_index, 0);
}

BOOST_AUTO_TEST_CASE(selects_non_coinbase_with_positionally_aligned_coins) {
    const CBlock block = MakeBlock();
    const CBlockUndo undo = MakeUndo(block);
    std::string error;
    const auto result = chronik::SelectConfirmedTx(
        block, &undo, block.GetHash(), 12, block.vtx[2]->GetId(), error);

    BOOST_REQUIRE(result);
    BOOST_CHECK(error.empty());
    BOOST_REQUIRE_EQUAL(result->spent_coins.size(), 1);
    BOOST_CHECK_EQUAL(result->spent_coins[0].GetTxOut().nValue / FIXOSHI, 3);
    BOOST_CHECK_EQUAL(result->spent_coins[0].GetHeight(), 11);
    BOOST_CHECK_EQUAL(result->tx_index, 2);
}

BOOST_AUTO_TEST_CASE(fails_closed_on_missing_or_misaligned_undo) {
    const CBlock block = MakeBlock();
    std::string error;
    BOOST_CHECK(!chronik::SelectConfirmedTx(
        block, nullptr, block.GetHash(), 12, block.vtx[1]->GetId(), error));
    BOOST_CHECK_EQUAL(error, "missing undo data for confirmed transaction");

    CBlockUndo undo = MakeUndo(block);
    undo.vtxundo[0].vprevout.clear();
    BOOST_CHECK(!chronik::SelectConfirmedTx(
        block, &undo, block.GetHash(), 12, block.vtx[1]->GetId(), error));
    BOOST_CHECK_EQUAL(error,
                      "spent coin count does not match transaction inputs");
}

BOOST_AUTO_TEST_CASE(fails_closed_on_identity_contradictions) {
    const CBlock block = MakeBlock();
    const CBlockUndo undo = MakeUndo(block);
    std::string error;

    BlockHash wrong_hash;
    wrong_hash.SetHex("01");
    BOOST_CHECK(!chronik::SelectConfirmedTx(
        block, &undo, wrong_hash, 12, block.vtx[1]->GetId(), error));
    BOOST_CHECK_EQUAL(error,
                      "loaded block hash does not match the indexed block");

    TxId absent_txid;
    absent_txid.SetHex("02");
    BOOST_CHECK(!chronik::SelectConfirmedTx(
        block, &undo, block.GetHash(), 12, absent_txid, error));
    BOOST_CHECK_EQUAL(error, "transaction is absent from the indexed block");
}

BOOST_AUTO_TEST_SUITE_END()

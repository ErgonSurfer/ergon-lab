// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#include <boost/test/unit_test.hpp>
#include <chronik/confirmed-tx-bridge.h>
#include <primitives/block.h>
#include <streams.h>
#include <test/setup_common.h>
#include <undo.h>
#include <version.h>

namespace {
extern "C" {
void *chronik_runtime_create(const uint8_t *, size_t, uint64_t);
uint64_t chronik_runtime_destroy(void *);
struct Mutation {
    uint64_t success, error_code;
    int64_t height;
    uint64_t transactions, new_tokens, token_transactions;
};
Mutation chronik_runtime_connect(const void *, const uint8_t *, int32_t,
                                 const uint8_t *, size_t);
Mutation chronik_runtime_disconnect(const void *, const uint8_t *, int32_t,
                                    const uint8_t *, size_t);
}

struct BridgeSetup : BasicTestingSetup {
    void *runtime{nullptr};
    CBlock block;
    CBlockUndo undo;
    CDataStream raw_block{SER_NETWORK, PROTOCOL_VERSION};

    BridgeSetup() {
        const auto path = (SetDataDir("confirmed-bridge") / "index").string();
        runtime = chronik_runtime_create(
            reinterpret_cast<const uint8_t *>(path.data()), path.size(), 0);
        BOOST_REQUIRE(runtime != nullptr);
        CMutableTransaction coinbase;
        coinbase.vin.emplace_back(TxId{}, COutPoint::NULL_INDEX,
                                  CScript() << 1);
        coinbase.vout.emplace_back(0 * FIXOSHI, CScript() << OP_TRUE);
        block.nVersion = 1;
        block.nTime = 123456;
        block.vtx.push_back(MakeTransactionRef(coinbase));
        CMutableTransaction spend;
        spend.vin.emplace_back(block.vtx[0]->GetId(), 0, CScript() << OP_TRUE);
        spend.vout.emplace_back(0 * FIXOSHI, CScript() << OP_TRUE);
        block.vtx.push_back(MakeTransactionRef(spend));
        undo.vtxundo.resize(1);
        undo.vtxundo[0].vprevout.emplace_back(coinbase.vout[0], 0, true);
        raw_block << block;
        const auto result = chronik_runtime_connect(
            runtime, block.GetHash().begin(), 0,
            reinterpret_cast<const uint8_t *>(raw_block.data()),
            raw_block.size());
        BOOST_REQUIRE_EQUAL(result.success, 1);
    }
    ~BridgeSetup() {
        if (runtime) {
            BOOST_CHECK_EQUAL(chronik_runtime_destroy(runtime), 1);
        }
    }
    chronik::ConfirmedTx Read(size_t index) {
        std::string error;
        auto confirmed = chronik::SelectConfirmedTx(
            block, &undo, block.GetHash(), 0, block.vtx[index]->GetId(), error);
        BOOST_REQUIRE(confirmed);
        return *confirmed;
    }
};
} // namespace

BOOST_FIXTURE_TEST_SUITE(chronik_confirmed_tx_bridge_tests, BridgeSetup)

BOOST_AUTO_TEST_CASE(encodes_coinbase_and_spend_through_the_real_rust_abi) {
    std::string error;
    for (size_t index = 0; index < block.vtx.size(); ++index) {
        const auto location = chronik::LookupConfirmedTxLocation(
            runtime, block.vtx[index]->GetId(), error);
        BOOST_REQUIRE(location);
        BOOST_CHECK_EQUAL(location->height, 0);
        BOOST_CHECK_EQUAL(location->timestamp, block.nTime);
        BOOST_CHECK_EQUAL(location->is_coinbase, uint64_t(index == 0));
        const auto payload =
            chronik::EncodeConfirmedTxProto(runtime, Read(index), error);
        BOOST_REQUIRE(payload);
        BOOST_CHECK(!payload->empty());
        BOOST_CHECK(error.empty());
        const auto repeated =
            chronik::EncodeConfirmedTxProto(runtime, Read(index), error);
        BOOST_REQUIRE(repeated);
        BOOST_CHECK(*payload == *repeated);
    }
}

BOOST_AUTO_TEST_CASE(rejects_missing_coins_metadata_and_absent_index_entries) {
    std::string error;
    auto confirmed = Read(1);
    confirmed.spent_coins.clear();
    BOOST_CHECK(!chronik::EncodeConfirmedTxProto(runtime, confirmed, error));
    confirmed = Read(1);
    ++confirmed.block_timestamp;
    BOOST_CHECK(!chronik::EncodeConfirmedTxProto(runtime, confirmed, error));
    confirmed = Read(1);
    confirmed.spent_coins[0].Clear();
    BOOST_CHECK(!chronik::EncodeConfirmedTxProto(runtime, confirmed, error));
    confirmed = Read(1);
    confirmed.tx_index = 0;
    BOOST_CHECK(!chronik::EncodeConfirmedTxProto(runtime, confirmed, error));
    BOOST_CHECK(!chronik::LookupConfirmedTxLocation(
        nullptr, confirmed.tx->GetId(), error));
    BOOST_CHECK(!chronik::LookupConfirmedTxLocation(runtime, TxId{}, error));

    const auto disconnected = chronik_runtime_disconnect(
        runtime, block.GetHash().begin(), 0,
        reinterpret_cast<const uint8_t *>(raw_block.data()), raw_block.size());
    BOOST_REQUIRE_EQUAL(disconnected.success, 1);
    BOOST_CHECK(!chronik::LookupConfirmedTxLocation(
        runtime, confirmed.tx->GetId(), error));
    BOOST_CHECK(!chronik::EncodeConfirmedTxProto(runtime, confirmed, error));
}

BOOST_AUTO_TEST_SUITE_END()

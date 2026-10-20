// Copyright (c) 2011-2019 The Bitcoin Core developers
// Copyright (c) 2017-2019 The Bitcoin developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#include <validation.h>

#include <chainparams.h>
#include <clientversion.h>
#include <config.h>
#include <consensus/consensus.h>
#include <net.h>
#include <primitives/transaction.h>
#include <streams.h>
#include <util/system.h>
#include <validation.h>

#include <test/setup_common.h>

#include <boost/signals2/signal.hpp>
#include <boost/test/unit_test.hpp>

#include <cstdint>
#include <cstdio>
#include <vector>

BOOST_FIXTURE_TEST_SUITE(validation_tests, TestingSetup)

//static void TestBlockSubsidyHalvings(const Consensus::Params &consensusParams) {
//    int maxHalvings = 64;
//    Amount nInitialSubsidy = 1 * FIXOSHI;
//
//    // for height == 0
//    Amount nPreviousSubsidy = 2 * nInitialSubsidy;
//    BOOST_CHECK_EQUAL(nPreviousSubsidy, 2 * nInitialSubsidy);
//    for (int nHalvings = 0; nHalvings < maxHalvings; nHalvings++) {
//        int nHeight = nHalvings * consensusParams.nSubsidyHalvingInterval;
//        Amount nSubsidy = GetBlockSubsidy(0x1d0fffff, nHeight, consensusParams);
//        //BOOST_CHECK(nSubsidy <= nInitialSubsidy);
//        nPreviousSubsidy = nSubsidy;
//    }
//}


//static void TestBlockSubsidyHalvings(int nSubsidyHalvingInterval) {
//    Consensus::Params consensusParams;
//    consensusParams.nSubsidyHalvingInterval = nSubsidyHalvingInterval;
//    TestBlockSubsidyHalvings(consensusParams);
//}
//
//BOOST_AUTO_TEST_CASE(block_subsidy_test) {
//    const auto chainParams = CreateChainParams(CBaseChainParams::MAIN);
//    // As in main
//    //TestBlockSubsidyHalvings(chainParams->GetConsensus());
//    // As in regtest
//    TestBlockSubsidyHalvings(150);
//    // Just another interval
//    TestBlockSubsidyHalvings(1000);
//}


static CBlock makeLargeDummyBlock(const size_t num_tx) {
    CBlock block;
    block.vtx.reserve(num_tx);

    for (size_t i = 0; i < num_tx; i++) {
        block.vtx.push_back(MakeTransactionRef());
    }
    return block;
}

/**
 * Test that LoadExternalBlockFile works with the buffer size set below the
 * size of a large block. Currently, LoadExternalBlockFile has the buffer size
 * for CBufferedFile set to 2 * MAX_TX_SIZE. Test with a value of
 * 10 * MAX_TX_SIZE.
 */
BOOST_AUTO_TEST_CASE(validation_load_external_block_file) {
    fs::path tmpfile_name =
        SetDataDir("validation_load_external_block_file") / "block.dat";

    FILE *fp = fopen(tmpfile_name.string().c_str(), "wb+");

    BOOST_CHECK(fp != nullptr);

    const Config &config = GetConfig();
    const CChainParams &chainparams = config.GetChainParams();

    // serialization format is:
    // message start magic, size of block, block

    size_t nwritten = fwrite(std::begin(chainparams.DiskMagic()),
                             CMessageHeader::MESSAGE_START_SIZE, 1, fp);

    BOOST_CHECK_EQUAL(nwritten, 1UL);

    const auto &empty_tx = CTransaction::null;
    size_t empty_tx_size = GetSerializeSize(empty_tx, CLIENT_VERSION);

    size_t num_tx = (10 * MAX_TX_SIZE) / empty_tx_size;

    CBlock block = makeLargeDummyBlock(num_tx);

    BOOST_CHECK(GetSerializeSize(block, CLIENT_VERSION) > 2 * MAX_TX_SIZE);

    unsigned int size = GetSerializeSize(block, CLIENT_VERSION);
    {
        CAutoFile outs(fp, SER_DISK, CLIENT_VERSION);
        outs << size;
        outs << block;
        outs.release();
    }

    fseek(fp, 0, SEEK_SET);
    BOOST_CHECK_NO_THROW({ LoadExternalBlockFile(config, fp, 0); });
}

BOOST_AUTO_TEST_SUITE_END()

// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
// Public synthetic tests: zero-subsidy native data only.
#include <validation_event_journal.h>
#include <validationinterface.h>
#include <consensus/validation.h>
#include <txmempool.h>
#include <script/sign.h>
#include <util/time.h>

namespace {
using Journal = ValidationEventJournal;
struct JournalRegistration {
    std::shared_ptr<Journal> journal;
    explicit JournalRegistration(std::shared_ptr<Journal> j) : journal(std::move(j)) {
        LOCK2(cs_main, g_mempool.cs);
        if (!GetMainSignals().AttachEventJournal(journal))
            throw std::runtime_error("Synthetic journal registration refused");
        GetMainSignals().RegisterWithMempoolSignals(g_mempool);
    }
    ~JournalRegistration() {
        {
            LOCK2(cs_main, g_mempool.cs);
            GetMainSignals().DetachEventJournal(journal);
            GetMainSignals().UnregisterWithMempoolSignals(g_mempool);
        }
        journal->Close();
    }
};
CTransactionRef JournalSyntheticTx() {
    CMutableTransaction tx;
    tx.vin.emplace_back(COutPoint(TxId(uint256S("01")),0));
    tx.vout.emplace_back(Amount::zero(), CScript()<<OP_TRUE);
    return MakeTransactionRef(tx);
}
void JournalCoin(CCoinsViewCache &view, const CTransactionRef &tx) {
    view.AddCoin(tx->vin[0].prevout,
        Coin(CTxOut(Amount::zero(),CScript()<<OP_TRUE),23,true),false);
}
CMutableTransaction JournalSignedSpend(const CTransactionRef &coinbase,
                                      const CKey &key) {
    CMutableTransaction tx;
    tx.nVersion=2;tx.vin.emplace_back(COutPoint(coinbase->GetId(),0));
    // Zero-subsidy regtest cannot fund a standard spendable output above dust.
    // Use a standard zero-valued OP_RETURN, not a fee/policy bypass.
    tx.vout.emplace_back(Amount::zero(),CScript()<<OP_RETURN<<std::vector<uint8_t>(64,0));
    std::vector<uint8_t> sig;
    const auto hash=SignatureHash(coinbase->vout[0].scriptPubKey,
        CTransaction(tx),0,SigHashType().withForkId(),coinbase->vout[0].nValue);
    if (!key.SignECDSA(hash,sig)) throw std::runtime_error("Synthetic signing refused");
    sig.push_back(uint8_t(SIGHASH_ALL|SIGHASH_FORKID));
    tx.vin[0].scriptSig<<sig;
    return tx;
}
bool JournalAccept(const CMutableTransaction &tx, bool test_only=false) {
    LOCK(cs_main);
    CValidationState state;
    const bool accepted=AcceptToMemoryPool(GetConfig(),g_mempool,state,MakeTransactionRef(tx),
                             nullptr,true,Amount::zero(),test_only);
    if (!accepted) BOOST_TEST_MESSAGE("Native fixture refusal: "<<FormatStateMessage(state));
    return accepted;
}
}

BOOST_FIXTURE_TEST_SUITE(validation_event_journal_tests, TestingSetup)
BOOST_AUTO_TEST_CASE(configuration_and_invalid_payload_refusal) {
    BOOST_CHECK_THROW(Journal(0),std::invalid_argument);
    BOOST_CHECK_THROW(Journal(4097),std::invalid_argument);
    BOOST_CHECK_THROW((void)Journal{std::numeric_limits<size_t>::max()},std::invalid_argument);
    BOOST_CHECK_THROW(Journal(1,1),std::invalid_argument);
    CCoinsView dummy;CCoinsViewCache view(&dummy);Journal journal;
    journal.Added(MakeTransactionRef(),view,1);
    BOOST_CHECK(journal.GetStatus().failure==Journal::Failure::InvalidPayload);
    BOOST_CHECK_EQUAL(journal.GetStatus().accepted,0);
}
BOOST_AUTO_TEST_CASE(owned_coins_and_leases) {
    CCoinsView dummy;CCoinsViewCache view(&dummy);
    const auto tx=JournalSyntheticTx();JournalCoin(view,tx);
    auto journal=std::make_shared<Journal>();journal->Added(tx,view,12345);
    BOOST_REQUIRE(view.SpendCoin(tx->vin[0].prevout));
    auto event=journal->Take();BOOST_REQUIRE(event);
    BOOST_CHECK(event->kind==Journal::Kind::Added);
    BOOST_CHECK_EQUAL(event->sequence,1);BOOST_CHECK_EQUAL(event->accept_time,12345);
    BOOST_REQUIRE_EQUAL(event->spent_coins.size(),1);
    BOOST_CHECK(!event->spent_coins[0].IsSpent());
    BOOST_CHECK(event->spent_coins[0].IsCoinBase());
    BOOST_CHECK_EQUAL(event->spent_coins[0].GetHeight(),23);
    BOOST_CHECK(event->spent_coins[0].GetTxOut().nValue==Amount::zero());
    BOOST_CHECK(event->spent_coins[0].GetTxOut().scriptPubKey==CScript()<<OP_TRUE);
    BOOST_CHECK_EQUAL(journal->GetStatus().queued,0);
    BOOST_CHECK_EQUAL(journal->GetStatus().retained_events,1);
    BOOST_CHECK(journal->GetStatus().retained_bytes>=sizeof(Journal::Event));
    event.reset();
    BOOST_CHECK_EQUAL(journal->GetStatus().retained_events,0);
    BOOST_CHECK_EQUAL(journal->GetStatus().retained_bytes,0);
}
BOOST_AUTO_TEST_CASE(fifo_and_owned_block_metadata) {
    CCoinsView dummy;CCoinsViewCache view(&dummy);
    const auto tx=JournalSyntheticTx();JournalCoin(view,tx);Journal journal;
    CBlockHeader header;header.nVersion=1;
    CBlockIndex index(header);index.nHeight=1;index.nFile=3;
    index.nDataPos=321;index.nUndoPos=654;
    journal.Added(tx,view,77);journal.Removed(tx);
    journal.Block(Journal::Kind::Connected,&index,{tx});
    journal.Block(Journal::Kind::Disconnected,&index);
    index.nDataPos=999;index.nHeight=99;
    const std::vector<Journal::Kind> kinds={Journal::Kind::Added,Journal::Kind::Removed,
        Journal::Kind::Connected,Journal::Kind::Disconnected};
    for (size_t i=0;i<kinds.size();++i) {
        auto event=journal.Take();BOOST_REQUIRE(event);
        BOOST_CHECK_EQUAL(event->sequence,i+1);BOOST_CHECK(event->kind==kinds[i]);
        if(i>=2) {
            BOOST_CHECK_EQUAL(event->height,1);BOOST_CHECK_EQUAL(event->file,3);
            BOOST_CHECK_EQUAL(event->data_pos,321);BOOST_CHECK_EQUAL(event->undo_pos,654);
            BOOST_CHECK(event->header.GetHash()==header.GetHash());
        }
        if(i==2) {
            BOOST_REQUIRE_EQUAL(event->conflicts.size(),1);
            BOOST_CHECK(event->conflicts[0]==tx->GetId());
        }
    }
    BOOST_CHECK(!journal.Take());BOOST_CHECK_EQUAL(journal.GetStatus().retained_bytes,0);
}
BOOST_AUTO_TEST_CASE(inflight_lease_counts_against_capacity) {
    CCoinsView dummy;CCoinsViewCache view(&dummy);
    const auto tx=JournalSyntheticTx();JournalCoin(view,tx);Journal journal(1);
    journal.Added(tx,view,1);auto lease=journal.Take();BOOST_REQUIRE(lease);
    journal.Removed(tx);
    BOOST_CHECK(journal.GetStatus().failure==Journal::Failure::Capacity);
    BOOST_CHECK_EQUAL(journal.GetStatus().accepted,1);
    lease.reset();journal.Removed(tx);BOOST_CHECK(!journal.Take());
    BOOST_CHECK_EQUAL(journal.GetStatus().accepted,1);
}
BOOST_AUTO_TEST_CASE(bytes_and_missing_coins_fail_closed) {
    CCoinsView dummy;CCoinsViewCache view(&dummy);
    const auto tx=JournalSyntheticTx();JournalCoin(view,tx);
    Journal bytes(2,sizeof(Journal::Event));bytes.Added(tx,view,1);
    BOOST_CHECK(bytes.GetStatus().failure==Journal::Failure::Bytes);
    BOOST_CHECK_EQUAL(bytes.GetStatus().retained_events,0);
    view.SpendCoin(tx->vin[0].prevout);
    Journal missing;missing.Added(tx,view,2);
    BOOST_CHECK(missing.GetStatus().failure==Journal::Failure::MissingCoin);
    JournalCoin(view,tx);missing.Added(tx,view,3);
    BOOST_CHECK_EQUAL(missing.GetStatus().accepted,0);BOOST_CHECK(!missing.Take());
}
BOOST_AUTO_TEST_CASE(close_preserves_existing_lease_only) {
    CCoinsView dummy;CCoinsViewCache view(&dummy);
    const auto tx=JournalSyntheticTx();JournalCoin(view,tx);
    auto journal=std::make_shared<Journal>();journal->Added(tx,view,1);
    auto lease=journal->Take();BOOST_REQUIRE(lease);
    journal->Close();journal->Removed(tx);
    BOOST_CHECK(journal->GetStatus().closed);BOOST_CHECK(!journal->Take());
    BOOST_CHECK_EQUAL(journal->GetStatus().accepted,1);
    journal.reset();BOOST_CHECK_EQUAL(lease->accept_time,1);
    lease.reset();
}
BOOST_AUTO_TEST_CASE(singleton_registration_and_other_pool_isolation) {
    auto journal=std::make_shared<Journal>();JournalRegistration registration(journal);
    auto other=std::make_shared<Journal>();
    CCoinsView dummy;CCoinsViewCache view(&dummy);
    const auto tx=JournalSyntheticTx();JournalCoin(view,tx);
    CTxMemPool other_pool;
    {
        LOCK2(cs_main,g_mempool.cs);
        BOOST_CHECK(!GetMainSignals().AttachEventJournal(other));
        BOOST_CHECK(!GetMainSignals().DetachEventJournal(other));
        GetMainSignals().CaptureAddedToMempool(other_pool,tx,view,1);
    }
    BOOST_CHECK_EQUAL(journal->GetStatus().accepted,0);
}
BOOST_AUTO_TEST_SUITE_END()

BOOST_FIXTURE_TEST_SUITE(validation_event_journal_native_tests, TestChain100Setup)
BOOST_AUTO_TEST_CASE(actual_transient_accept_remove_mine_disconnect) {
    auto journal=std::make_shared<Journal>();JournalRegistration registration(journal);
    const auto spend=JournalSignedSpend(m_coinbase_txns[0],coinbaseKey);
    BOOST_REQUIRE(JournalAccept(spend,true));
    BOOST_CHECK_EQUAL(journal->GetStatus().accepted,0); // dry-run never journals
    const auto before=GetTime();BOOST_REQUIRE(JournalAccept(spend));
    {
        LOCK2(cs_main,g_mempool.cs);
        g_mempool.removeRecursive(CTransaction(spend),MemPoolRemovalReason::EXPIRY);
    }
    BOOST_REQUIRE(JournalAccept(spend));
    const auto block=CreateAndProcessBlock({spend},
        CScript()<<ToByteVector(coinbaseKey.GetPubKey())<<OP_CHECKSIG);
    {
        LOCK(cs_main);
        BOOST_REQUIRE(ChainActive().Tip()->GetBlockHash()==block.GetHash());
        CValidationState state;
        BOOST_REQUIRE(InvalidateBlock(GetConfig(),state,ChainActive().Tip()));
    }
    SyncWithValidationInterfaceQueue();
    const std::vector<Journal::Kind> expected={Journal::Kind::Added,Journal::Kind::Removed,
        Journal::Kind::Added,Journal::Kind::Connected,Journal::Kind::Disconnected,Journal::Kind::Added};
    BOOST_REQUIRE_EQUAL(journal->GetStatus().accepted,expected.size());
    for(size_t i=0;i<expected.size();++i) {
        auto event=journal->Take();BOOST_REQUIRE(event);
        BOOST_CHECK_EQUAL(event->sequence,i+1);BOOST_CHECK(event->kind==expected[i]);
        if(event->kind==Journal::Kind::Added) {
            BOOST_CHECK(event->tx->GetId()==CTransaction(spend).GetId());
            BOOST_REQUIRE_EQUAL(event->spent_coins.size(),1);
            BOOST_CHECK(event->spent_coins[0].GetTxOut()==m_coinbase_txns[0]->vout[0]);
            BOOST_CHECK(event->spent_coins[0].IsCoinBase());
            BOOST_CHECK(event->accept_time>=before && event->accept_time<=GetTime());
        }
    }
    BOOST_CHECK(journal->GetStatus().failure==Journal::Failure::None);
}
BOOST_AUTO_TEST_CASE(journal_overflow_cannot_reject_native_validation) {
    auto journal=std::make_shared<Journal>(1);JournalRegistration registration(journal);
    const auto spend=JournalSignedSpend(m_coinbase_txns[0],coinbaseKey);
    BOOST_REQUIRE(JournalAccept(spend));
    const auto block=CreateAndProcessBlock({spend},
        CScript()<<ToByteVector(coinbaseKey.GetPubKey())<<OP_CHECKSIG);
    BOOST_REQUIRE(ChainActive().Tip()->GetBlockHash()==block.GetHash());
    BOOST_CHECK(journal->GetStatus().failure==Journal::Failure::Capacity);
    // Mining made the next independent zero-subsidy coinbase mature.
    const auto next=JournalSignedSpend(m_coinbase_txns[1],coinbaseKey);
    BOOST_REQUIRE(JournalAccept(next));
    BOOST_CHECK(g_mempool.exists(CTransaction(next).GetId()));
    BOOST_CHECK_EQUAL(journal->GetStatus().accepted,1);
    BOOST_CHECK(!journal->Take()); // no continuing through a journal hole
}
BOOST_AUTO_TEST_SUITE_END()

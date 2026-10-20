// SPDX-License-Identifier: MIT
// Dedicated network identity, unchanged-mainnet vectors and native DAA models.
// Synthetic index sequences are simulations, not mined-network observations.
#include <amount.h>
#include <arith_uint256.h>
#include <chain.h>
#include <chainparams.h>
#include <cashaddrenc.h>
#include <config.h>
#include <consensus/activation.h>
#include <key_io.h>
#include <pow.h>
#include <test/setup_common.h>
#include <util/system.h>
#include <validation.h>

#include <boost/test/unit_test.hpp>
#include <array>
#include <vector>

BOOST_FIXTURE_TEST_SUITE(ergon_testnet_tests, BasicTestingSetup)

BOOST_AUTO_TEST_CASE(independent_identity_and_addresses) {
    const auto test = CreateChainParams(CBaseChainParams::ERGON_TESTNET);
    BOOST_CHECK_EQUAL(test->NetworkIDString(), "ergon-testnet-v1");
    BOOST_CHECK_EQUAL(test->CashAddrPrefix(), "ergontest");
    BOOST_CHECK_EQUAL(test->GetDefaultPort(), 42137);
    const auto base = CreateBaseChainParams(CBaseChainParams::ERGON_TESTNET);
    BOOST_CHECK_EQUAL(base->DataDir(), "ergon-testnet-v1");
    BOOST_CHECK_EQUAL(base->RPCPort(), 42136);
    BOOST_CHECK(test->IsTestChain());
    BOOST_CHECK(test->RequireStandard());
    BOOST_CHECK(!test->MineBlocksOnDemand());
    BOOST_CHECK(test->DNSSeeds().empty());
    BOOST_CHECK(test->FixedSeeds().empty());
    const auto &genesis = test->GenesisBlock();
    BOOST_CHECK_EQUAL(genesis.GetHash().ToString(),
        "00000066119ff801d756659ca11217fe21c0b5f66779eaf9fbc87a8945aa8b4c");
    BOOST_CHECK(CheckProofOfWork(genesis.GetHash(), genesis.nBits, test->GetConsensus()));
    BOOST_CHECK(genesis.vtx[0]->vout[0].nValue == Amount::zero());
    for (const auto &name : {CBaseChainParams::MAIN, CBaseChainParams::TESTNET,
                            CBaseChainParams::TESTNET4, CBaseChainParams::SCALENET,
                            CBaseChainParams::REGTEST}) {
        const auto other = CreateChainParams(name);
        BOOST_CHECK(genesis.GetHash() != other->GenesisBlock().GetHash());
        BOOST_CHECK(test->NetMagic() != other->NetMagic());
        BOOST_CHECK(test->DiskMagic() != other->DiskMagic());
        BOOST_CHECK(test->CashAddrPrefix() != other->CashAddrPrefix());
        BOOST_CHECK(test->GetDefaultPort() != other->GetDefaultPort());
    }
    const CTxDestination destination = CKeyID(uint160S("1234567890123456789012345678901234567890"));
    const auto main = CreateChainParams(CBaseChainParams::MAIN);
    DummyConfig testConfig(CBaseChainParams::ERGON_TESTNET), mainConfig(CBaseChainParams::MAIN);
    const auto address = EncodeDestination(destination, testConfig);
    BOOST_CHECK(IsValidDestination(DecodeDestination(address, *test)));
    BOOST_CHECK(!IsValidDestination(DecodeDestination(address, *main)));
    BOOST_CHECK(!IsValidDestination(DecodeDestination(EncodeDestination(destination, mainConfig), *test)));
    const auto cashAddress = EncodeCashAddr(destination, *test);
    BOOST_CHECK(cashAddress.find("ergontest:") == 0);
    BOOST_CHECK(IsValidDestination(DecodeDestination(cashAddress, *test)));
    BOOST_CHECK(!IsValidDestination(DecodeDestination(cashAddress, *main)));
}

BOOST_AUTO_TEST_CASE(explicit_selection_and_conflicts) {
    ArgsManager args;
    BOOST_CHECK_EQUAL(args.GetChainName(), CBaseChainParams::MAIN);
    args.ForceSetArg("-ergontestnet", "1");
    BOOST_CHECK_EQUAL(args.GetChainName(), CBaseChainParams::ERGON_TESTNET);
    for (const auto &flag : {"-testnet", "-testnet4", "-scalenet", "-regtest"}) {
        args.ForceSetArg(flag, "1");
        BOOST_CHECK_THROW(args.GetChainName(), std::runtime_error);
        args.ForceSetArg(flag, "0");
    }
    args.ForceSetArg("-ergontestnet", "0");
    BOOST_CHECK_EQUAL(args.GetChainName(), CBaseChainParams::MAIN);
}

static void IndexSequence(std::vector<CBlockIndex> &blocks, uint32_t bits,
                          uint32_t time, uint32_t spacing) {
    for (size_t i = 0; i < blocks.size(); ++i) {
        auto &block = blocks[i];
        block.nHeight = i;
        block.nBits = bits;
        block.nTime = time + i * spacing;
        block.pprev = i ? &blocks[i - 1] : nullptr;
        block.BuildSkip();
        block.nChainWork = (i ? blocks[i - 1].nChainWork : arith_uint256()) + GetBlockProof(block);
    }
}

BOOST_AUTO_TEST_CASE(mainnet_monetary_rules_and_zero_bootstrap_subsidy) {
    const auto test = CreateChainParams(CBaseChainParams::ERGON_TESTNET);
    const auto main = CreateChainParams(CBaseChainParams::MAIN);
    const auto &params = test->GetConsensus();
    const auto &reference = main->GetConsensus();
    BOOST_CHECK_EQUAL(params.nPowTargetSpacing, reference.nPowTargetSpacing);
    BOOST_CHECK_EQUAL(params.nSubsidyHalvingInterval, reference.nSubsidyHalvingInterval);
    BOOST_CHECK_EQUAL(params.nValueCalibration, reference.nValueCalibration);
    BOOST_CHECK_EQUAL(params.nBootstrapBits, 0x1e00ffffU);
    std::vector<CBlockIndex> blocks(6);
    IndexSequence(blocks, params.nBootstrapBits, test->GenesisBlock().nTime, params.nPowTargetSpacing);
    for (size_t i = 0; i < blocks.size() - 1; ++i) {
        CBlockHeader next;
        next.nTime = blocks[i].nTime + params.nPowTargetSpacing;
        const auto bits = GetNextWorkRequired(&blocks[i], &next, params);
        if (i < 4) BOOST_CHECK_EQUAL(bits, params.nBootstrapBits);
        BOOST_CHECK(UintToArith256(params.powLimit) >= arith_uint256().SetCompact(bits));
        BOOST_CHECK(bits != 0x1a04b500U);
        BOOST_CHECK(IsEMAEnabled(params, &blocks[i]));
    }
    BOOST_CHECK_EQUAL(GetBlockProof(blocks[0]).GetLow64(), 16777472U);
    // Low work may earn zero. Never add a floor to fund the development chain.
    BOOST_CHECK_EQUAL(GetBlockSubsidy(&blocks[0], params.nBootstrapBits, 1, params) / FIXOSHI, 0);
    bool observedZero = false, observedPositive = false;
    for (const uint32_t bits : {0x1e00ffffU, 0x1d0fffffU, 0x1c0fffffU,
                               0x1b0404cbU, 0x1a04b500U}) {
        for (const int height : {1, 143, 144, 288, 144 * 365}) {
            const auto amount = GetBlockSubsidy(&blocks[0], bits, height, params);
            BOOST_CHECK(amount == GetBlockSubsidy(&blocks[0], bits, height, reference));
            observedZero |= amount == Amount::zero();
            observedPositive |= amount > Amount::zero();
        }
    }
    BOOST_CHECK(observedZero);
    BOOST_CHECK(observedPositive);
    for (const auto &vector : std::array<std::array<uint32_t, 3>, 3>{{
             {{1, 0x1b27a4f5U, 0x1b27a4f4U}},
             {{144, 0x1b279ca3U, 0x1b279ca2U}},
             {{52560, 0x1b1d62e0U, 0x1b1d62dfU}}}}) {
        BOOST_CHECK_EQUAL(GetBlockSubsidy(&blocks[0], vector[1], vector[0], params) / FIXOSHI, 0);
        BOOST_CHECK_EQUAL(GetBlockSubsidy(&blocks[0], vector[2], vector[0], params) / FIXOSHI, 1);
    }
}

BOOST_AUTO_TEST_CASE(mainnet_bootstrap_and_zero_reward_vectors_unchanged) {
    const auto main = CreateChainParams(CBaseChainParams::MAIN);
    const auto &params = main->GetConsensus();
    BOOST_CHECK_EQUAL(params.nBootstrapBits, 0x1a04b500U);
    BOOST_CHECK_EQUAL(params.nPowTargetSpacing, 600);
    BOOST_CHECK_EQUAL(params.nSubsidyHalvingInterval, 144);
    BOOST_CHECK_EQUAL(params.nValueCalibration, 7100000000000LL);
    BOOST_CHECK_EQUAL(main->GenesisBlock().GetHash().ToString(),
        "000000070e37bfee7e84b94f997f38155a85b22172f5ca25fd4eb3d64c5ad7c5");
    std::vector<CBlockIndex> blocks(5);
    IndexSequence(blocks, params.nBootstrapBits, main->GenesisBlock().nTime, 600);
    CBlockHeader next;
    for (int i = 0; i < 4; ++i)
        BOOST_CHECK_EQUAL(GetNextWorkRequired(&blocks[i], &next, params), 0x1a04b500U);
    BOOST_CHECK_EQUAL(GetBlockSubsidy(&blocks[0], UintToArith256(params.powLimit).GetCompact(), 1, params) / FIXOSHI, 0);
}

BOOST_AUTO_TEST_CASE(hashrate_arrival_increases_work_without_reward_floor) {
    const auto test = CreateChainParams(CBaseChainParams::ERGON_TESTNET);
    const auto &params = test->GetConsensus();
    std::vector<CBlockIndex> blocks(1036);
    // Half-target timestamp steps model a moderate arrival. This is a native
    // DAA vector, not proof of accepted blocks or observed mining equipment.
    const auto spacing = params.nPowTargetSpacing / 2;
    IndexSequence(blocks, params.nBootstrapBits, test->GenesisBlock().nTime, spacing);
    for (size_t i = 4; i + 1 < blocks.size(); ++i) {
        CBlockHeader next;
        next.nTime = blocks[i].nTime + spacing;
        blocks[i + 1].nBits = GetNextWorkRequired(&blocks[i], &next, params);
        blocks[i + 1].nChainWork = blocks[i].nChainWork + GetBlockProof(blocks[i + 1]);
    }
    BOOST_CHECK(GetBlockProof(blocks.back()) > GetBlockProof(blocks[4]));
    const auto main = CreateChainParams(CBaseChainParams::MAIN);
    BOOST_CHECK(GetBlockSubsidy(&blocks[4], blocks.back().nBits, 1, params) ==
                GetBlockSubsidy(&blocks[4], blocks.back().nBits, 1, main->GetConsensus()));
}

BOOST_AUTO_TEST_CASE(moderate_hashrate_departure_matches_mainnet_daa) {
    const auto test = CreateChainParams(CBaseChainParams::ERGON_TESTNET);
    const auto &params = test->GetConsensus();
    // Synthetic 1 TH/s equilibrium followed by a fourfold withdrawal.
    // No claim that a hash-rate estimate identifies actual mining equipment.
    const arith_uint256 work(1000000000000ULL * params.nPowTargetSpacing);
    const arith_uint256 target = (-work) / work;
    std::vector<CBlockIndex> blocks(12);
    IndexSequence(blocks, target.GetCompact(), test->GenesisBlock().nTime, params.nPowTargetSpacing);
    CBlockHeader now, afterYear;
    now.nTime = blocks.back().nTime + params.nPowTargetSpacing;
    afterYear.nTime = now.nTime + 365 * 86400;
    const auto bits = GetNextWorkRequired(&blocks.back(), &now, params);
    const auto main = CreateChainParams(CBaseChainParams::MAIN);
    BOOST_CHECK_EQUAL(bits, GetNextWorkRequired(&blocks.back(), &now, main->GetConsensus()));
    BOOST_CHECK_EQUAL(bits, GetNextWorkRequired(&blocks.back(), &afterYear, params));
    CBlockIndex next;
    next.nBits = bits;
    const auto expectedWaitSeconds = GetBlockProof(next).GetLow64() / 250000000000ULL;
    BOOST_CHECK(expectedWaitSeconds > 2300 && expectedWaitSeconds < 2500);
    BOOST_TEST_MESSAGE("SIMULATION: 1TH/s->250GH/s assumed equilibrium; first-block expectation only, not a recovery deadline");
}

BOOST_AUTO_TEST_CASE(hundred_petahash_equilibrium_money_range) {
    const auto test = CreateChainParams(CBaseChainParams::ERGON_TESTNET);
    const auto &params = test->GetConsensus();
    const arith_uint256 work = arith_uint256(100000000000000000ULL) * params.nPowTargetSpacing;
    const arith_uint256 target = (-work) / work;
    const auto bits = target.GetCompact();
    std::vector<CBlockIndex> blocks(12);
    IndexSequence(blocks, bits, test->GenesisBlock().nTime, params.nPowTargetSpacing);
    const auto subsidy = GetBlockSubsidy(&blocks.back(), bits, 1, params);
    BOOST_CHECK(subsidy > Amount::zero());
    BOOST_CHECK(MoneyRange(subsidy));
    const auto main = CreateChainParams(CBaseChainParams::MAIN);
    BOOST_CHECK(subsidy == GetBlockSubsidy(&blocks.back(), bits, 1, main->GetConsensus()));
    // A bounded numerical vector, not a guarantee for arbitrary hash rates.
}

BOOST_AUTO_TEST_SUITE_END()

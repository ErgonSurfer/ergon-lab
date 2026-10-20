// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#include <boost/test/unit_test.hpp>
#include <chronik-cpp/ergon_archival_worker.h>
#include <chronik-cpp/ergon_archival_probe.h>
#include <chronik-cpp/ergon_regtest_service.h>
#include <chronik_lib/src/ergon_archival.rs.h>
#include <config.h>
#include <consensus/validation.h>
#include <fs.h>
#include <fstream>
#include <future>
#include <iostream>
#include <primitives/block.h>
#include <script/script.h>
#include <shutdown.h>
#include <test/setup_common.h>
#include <txmempool.h>
#include <util/system.h>
#include <util/strencodings.h>
#include <validation.h>
#include <validationinterface.h>
#include <thread>

namespace {
struct LoadedRestore {
    bool original = g_mempool.IsLoaded();
    ~LoadedRestore() { g_mempool.SetIsLoaded(original); }
};
struct ReleaseLatch {
    std::promise<void> entered, release;
    std::future<void> signal = release.get_future();
    bool released{false};
    void Block() {
        entered.set_value();
        if (signal.wait_for(std::chrono::seconds(5)) != std::future_status::ready)
            throw std::runtime_error("Synthetic startup latch expired");
    }
    void Resume() { if (!released) { released = true; release.set_value(); } }
    ~ReleaseLatch() { Resume(); }
};
bool WaitListener(const ergon_regtest::OwnedService &owner) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (!owner.Status().failed && !owner.Status().port &&
           std::chrono::steady_clock::now() < deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    return owner.Status().port != 0;
}
}

BOOST_FIXTURE_TEST_SUITE(chronik_upstream_regtest_tests, TestChain100Setup)
BOOST_AUTO_TEST_CASE(export_upstream_alp_templates) {
    // Test-only transport of the existing upstream builder output. The Python
    // daemon driver substitutes this one synthetic token-id placeholder; it
    // does not implement another ALP builder, parser or validator.
    std::array<uint8_t, 32> placeholder;
    placeholder.fill(0x42);
    for (uint8_t op = 0; op < 4; ++op) {
        const auto script = ergon_archival::fixture_alp_script(op, placeholder);
        BOOST_REQUIRE(!script.empty());
        std::cout << "Ergon ALP template " << unsigned(op) << " "
                  << HexStr(script.begin(), script.end()) << std::endl;
    }
}

BOOST_AUTO_TEST_CASE(service_owned_startup_reconciles_progress_and_reorg) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    const auto directory = GetDataDir() / "service-owned-progress";
    BOOST_REQUIRE(fs::create_directory(directory));
    const auto root = fs::canonical(directory).string();
    const auto old = ChainActive().Tip()->GetBlockHash();
    ergon_archival::StartupHook hook([&] {
        CValidationState state;
        if (!InvalidateBlock(GetConfig(), state, ChainActive().Tip()) ||
            !ActivateBestChain(GetConfig(), state))
            throw std::runtime_error("Synthetic reorg setup refused");
        CreateAndProcessBlock({}, CScript() << OP_2);
        CreateAndProcessBlock({}, CScript() << OP_3);
    });
    {
        auto probe = ergon_archival::open_regtest_service_with_hook(root, access, hook);
        BOOST_CHECK_EQUAL(ChainActive().Height(), 101);
        BOOST_CHECK(ChainActive()[100]->GetBlockHash() != old);
        BOOST_CHECK_EQUAL(probe->snapshot().height, 101);
        BOOST_CHECK(!access.index_failed.load());
        BOOST_CHECK(!fs::exists(directory / "probe.failed"));
    }
    auto reopened = ergon_archival::open_regtest_service(root, access);
    BOOST_CHECK_EQUAL(reopened->snapshot().height, 101);
}

BOOST_AUTO_TEST_CASE(service_startup_cancellation_preserves_reopenable_root) {
    const auto directory = GetDataDir() / "service-startup-cancel";
    BOOST_REQUIRE(fs::create_directory(directory));
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    struct Reset { ~Reset() { AbortShutdown(); } } reset;
    ergon_archival::StartupHook hook([] { StartShutdown(); });
    BOOST_CHECK_THROW(ergon_archival::open_regtest_service_with_hook(
        fs::canonical(directory).string(), access, hook), rust::Error);
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(!fs::exists(directory / "probe.failed"));
    AbortShutdown();
    auto reopened = ergon_archival::open_regtest_service(fs::canonical(directory).string(), access);
    BOOST_CHECK_EQUAL(reopened->snapshot().height, 100);
}

BOOST_AUTO_TEST_CASE(asynchronous_owner_does_not_wait_for_native_import) {
    LoadedRestore loaded;
    g_mempool.SetIsLoaded(false);
    ergon_regtest::OwnedService owner(GetConfig());
    BOOST_CHECK_EQUAL(owner.Status().port, 0);
    BOOST_CHECK(!owner.Status().failed);
    CreateAndProcessBlock({}, CScript() << OP_1);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 101);
    owner.Stop();
    BOOST_CHECK(owner.Status().joined);
    BOOST_CHECK(!owner.Status().failed);
    BOOST_CHECK(!fs::exists(GetDataDir() / "chronik-upstream-regtest"));
}

BOOST_AUTO_TEST_CASE(asynchronous_owner_allows_native_progress_before_open) {
    LoadedRestore loaded;
    g_mempool.SetIsLoaded(true);
    ReleaseLatch latch;
    auto entered = latch.entered.get_future();
    ergon_regtest::OwnedService owner(GetConfig(), [&] { latch.Block(); });
    BOOST_REQUIRE(entered.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
    BOOST_CHECK_EQUAL(owner.Status().port, 0);
    CreateAndProcessBlock({}, CScript() << OP_1);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 101);
    BOOST_CHECK(!fs::exists(GetDataDir() / "chronik-upstream-regtest"));
    latch.Resume();
    BOOST_REQUIRE(WaitListener(owner));
    BOOST_CHECK(!owner.Status().failed);
    owner.Stop();
    BOOST_CHECK(owner.Status().joined);
    BOOST_CHECK_EQUAL(owner.Status().port, 0);
    BOOST_CHECK(!fs::exists(GetDataDir() / "chronik-upstream-regtest/probe.failed"));
}

BOOST_AUTO_TEST_CASE(asynchronous_owner_stop_joins_pending_startup) {
    LoadedRestore loaded;
    g_mempool.SetIsLoaded(true);
    ReleaseLatch latch;
    auto entered = latch.entered.get_future();
    ergon_regtest::OwnedService owner(GetConfig(), [&] { latch.Block(); });
    BOOST_REQUIRE(entered.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
    auto stopping = std::async(std::launch::async, [&] { owner.Stop(); });
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (!owner.Status().stopping && std::chrono::steady_clock::now() < deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    BOOST_CHECK(owner.Status().stopping);
    BOOST_CHECK(stopping.wait_for(std::chrono::milliseconds(0)) != std::future_status::ready);
    CreateAndProcessBlock({}, CScript() << OP_1);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 101);
    latch.Resume();
    BOOST_REQUIRE(stopping.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
    stopping.get();
    BOOST_CHECK(owner.Status().joined);
    BOOST_CHECK(!owner.Status().failed);
    BOOST_CHECK_EQUAL(owner.Status().port, 0);
    BOOST_CHECK(!fs::exists(GetDataDir() / "chronik-upstream-regtest"));
}

BOOST_AUTO_TEST_CASE(service_os_lease_excludes_other_owners_and_preserves_inode) {
    const auto directory = GetDataDir() / "regtest-service-lease";
    BOOST_REQUIRE(fs::create_directory(directory));
    const auto root = fs::canonical(directory).string();
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    {
        auto probe = ergon_archival::open_regtest_service(root, access);
        BOOST_CHECK(fs::is_regular_file(directory / "probe.lock"));
        BOOST_CHECK_THROW(ergon_archival::open_regtest_service(root, access), rust::Error);
        BOOST_CHECK_THROW(ergon_archival::open_archival(root, access), rust::Error);
        BOOST_CHECK(!access.index_failed.load());
    }
    BOOST_CHECK(fs::is_regular_file(directory / "probe.lock"));
    BOOST_CHECK(!fs::exists(directory / "probe.failed"));
    {
        auto reopened = ergon_archival::open_regtest_service(root, access);
        BOOST_CHECK(fs::is_regular_file(directory / "indexes/chronik/CURRENT"));
    }
    BOOST_CHECK(!access.index_failed.load());
}

BOOST_AUTO_TEST_CASE(service_unknown_or_partial_lease_is_not_adopted) {
    const auto directory = GetDataDir() / "regtest-service-unknown-lease";
    BOOST_REQUIRE(fs::create_directory(directory));
    const auto root = fs::canonical(directory).string();
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    {
        std::ofstream unknown((directory / "probe.lock").string());
        unknown << "partial-or-unknown\n";
    }
    BOOST_CHECK_THROW(ergon_archival::open_regtest_service(root, access), rust::Error);
    BOOST_CHECK(!fs::exists(directory / "ergon-identity.json"));
    BOOST_CHECK(!fs::exists(directory / "indexes"));
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(fs::is_regular_file(directory / "probe.lock"));
}

#ifdef ENABLE_CHRONIK_UPSTREAM_REGTEST_PLUGINS
BOOST_AUTO_TEST_CASE(asynchronous_owner_loads_public_plugin_and_joins) {
    LoadedRestore loaded;
    g_mempool.SetIsLoaded(true);
    ReleaseLatch latch;
    auto entered = latch.entered.get_future();
    ergon_regtest::OwnedService owner(GetConfig(), [&] { latch.Block(); }, true);
    BOOST_REQUIRE(entered.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
    const auto inputs = GetDataDir() / "chronik-upstream-regtest-plugin-inputs";
    BOOST_CHECK(!fs::exists(inputs));
    CreateAndProcessBlock({}, CScript() << OP_1);
    BOOST_CHECK_EQUAL(ChainActive().Height(), 101);
    latch.Resume();
    BOOST_REQUIRE(WaitListener(owner));
    BOOST_CHECK(!owner.Status().failed);
    BOOST_CHECK(fs::is_regular_file(inputs / "ergon_public_probe.py"));
    BOOST_CHECK(fs::is_regular_file(inputs / "plugins.toml"));
    BOOST_CHECK(!fs::exists(inputs / "__pycache__"));
    owner.Stop();
    BOOST_CHECK(owner.Status().joined);
    BOOST_CHECK_EQUAL(owner.Status().port, 0);
    BOOST_CHECK(!fs::exists(GetDataDir() / "chronik-upstream-regtest/probe.failed"));
    std::ifstream identity((GetDataDir() / "chronik-upstream-regtest/ergon-identity.json").string());
    const std::string raw((std::istreambuf_iterator<char>(identity)), std::istreambuf_iterator<char>());
    BOOST_CHECK(raw.find("\"plugins\":true") != std::string::npos);
}
#endif

BOOST_AUTO_TEST_CASE(shutdown_cancels_inflight_capture_without_failed_root) {
    const auto directory = GetDataDir() / "regtest-shutdown-capture";
    BOOST_REQUIRE(fs::create_directory(directory));
    const auto root = fs::canonical(directory).string();
    chronik_bridge::ErgonNodeAccess access(GetConfig(), g_mempool);
    std::promise<void> entered, release;
    auto entered_future = entered.get_future();
    auto release_future = release.get_future();
    ergon_archival::ArchivalWorker worker(root, access, 128, [&] {
        entered.set_value();
        release_future.wait();
    });
    // Unwind must release the latch BEFORE worker destruction and restore the
    // process-global shutdown bit even if an assertion/exception aborts here.
    struct Restore {
        std::promise<void> &release;
        bool released{false};
        void Resume() {
            if (!released) {
                released = true;
                release.set_value();
            }
        }
        ~Restore() { Resume(); AbortShutdown(); }
    } restore{release};
    CreateAndProcessBlock({}, CScript() << OP_1);
    BOOST_REQUIRE(entered_future.wait_for(std::chrono::seconds(5)) == std::future_status::ready);
    StartShutdown();
    restore.Resume();
    worker.Stop();
    const auto stopped = worker.Status();
    BOOST_CHECK(stopped.joined);
    BOOST_CHECK(!stopped.failed);
    BOOST_CHECK_EQUAL(stopped.pending, 0);
    BOOST_CHECK(!access.index_failed.load());
    BOOST_CHECK(!fs::exists(directory / "probe.failed"));
    BOOST_CHECK(!fs::exists(directory / "probe.lock"));
    AbortShutdown();
    ergon_archival::ArchivalWorker reopened(root, access);
    BOOST_CHECK_EQUAL(reopened.ReadSnapshot().height, 101);
    reopened.Stop();
}
BOOST_AUTO_TEST_SUITE_END()

// A separate, explicit network/genesis-bound startup constructor. No daemon
// flag, network connection, listener, plugin or submission is admitted here.
namespace {
std::string ReplayRoot(const char *name);
std::string HistoricalIdentityFile(const std::string &root) {
    std::ifstream stream(root+"/ergon-identity.json",std::ios::binary);
    if(!stream) throw std::runtime_error("Missing synthetic historical identity");
    return std::string(std::istreambuf_iterator<char>(stream),{});
}
chronik_bridge::HistoricalReadBinding HistoricalRegtestBinding() {
    return {GetConfig(),"regtest",GetConfig().GetChainParams().GetConsensus().hashGenesisBlock.GetHex()};
}
}
BOOST_FIXTURE_TEST_SUITE(chronik_upstream_historical_client_tests,TestChain100Setup)
BOOST_AUTO_TEST_CASE(typed_client_binding_is_explicit_and_network_fenced) {
    using chronik_bridge::HistoricalClientAdmission;
    DummyConfig mutable_config("regtest");
    const auto genesis=mutable_config.GetChainParams().GetConsensus().hashGenesisBlock.GetHex();
    const auto binding=chronik_bridge::HistoricalReadBinding(mutable_config,"regtest",genesis);
    chronik_bridge::ErgonNodeAccess access(mutable_config,g_mempool,binding,HistoricalClientAdmission::Explicit);
    BOOST_CHECK(access.historical_clients());
    BOOST_CHECK(!access.historical_readonly());
    BOOST_CHECK(access.permits_clients());
    BOOST_CHECK(access.permits_native_network());
    BOOST_CHECK(!chronik_bridge::upstream_runtime_permitted(access));
    BOOST_CHECK_THROW(chronik_bridge::ErgonNodeAccess(GetConfig(),g_mempool,
        chronik_bridge::NativeReadScope::HistoricalClientService,true),std::runtime_error);
    BOOST_CHECK_THROW(chronik_bridge::ErgonNodeAccess(GetConfig(),g_mempool,HistoricalRegtestBinding(),
        static_cast<HistoricalClientAdmission>(99)),std::runtime_error);
    access.index_failed.store(true);BOOST_CHECK(!access.permits_clients());
    mutable_config.SetChainParams("main");BOOST_CHECK(!access.permits_native_network());
    BOOST_CHECK_THROW(chronik_bridge::ErgonNodeAccess(mutable_config,g_mempool,binding,
        HistoricalClientAdmission::Explicit),std::runtime_error);
}
BOOST_AUTO_TEST_CASE(client_scope_and_index_constructor_must_match_before_root_access) {
    using chronik_bridge::HistoricalClientAdmission;
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool,HistoricalRegtestBinding(),HistoricalClientAdmission::Explicit);
    chronik_bridge::ErgonNodeAccess readonly(GetConfig(),g_mempool,HistoricalRegtestBinding());
    const auto root=(GetDataDir()/"historical-client-scope-refusal").string();
    BOOST_CHECK_THROW(ergon_archival::open_historical_clients(root,readonly),rust::Error);
    BOOST_CHECK_THROW(ergon_archival::open_historical_readonly(root,access),rust::Error);
    BOOST_CHECK_THROW(ergon_archival::open_regtest_service(root,access),rust::Error);
    BOOST_CHECK_THROW(ergon_archival::open_archival(root,access),rust::Error);
    BOOST_CHECK_THROW(ergon_archival::ArchivalWorker(root,readonly,128,{},{},{},{},nullptr,true,
        ergon_archival::WorkerProfile::HistoricalClientService),std::runtime_error);
    BOOST_CHECK_THROW(ergon_archival::ArchivalWorker(root,access),std::runtime_error);
    BOOST_CHECK(!fs::exists(fs::path(root)));
    BOOST_CHECK(!access.index_failed.load());
}
BOOST_AUTO_TEST_CASE(client_index_identity_is_separate_and_fixture_transports_stay_closed) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool,HistoricalRegtestBinding(),
        chronik_bridge::HistoricalClientAdmission::Explicit);
    const auto root=ReplayRoot("historical-client-startup");
    {
        auto probe=ergon_archival::open_historical_clients(root,access);
        const auto id=HistoricalIdentityFile(root);
        BOOST_CHECK(id.find("ergon-historical-client-service/v1")!=std::string::npos);
        BOOST_CHECK(id.find("\"read_only\":false,\"clients\":true")!=std::string::npos);
        BOOST_CHECK_THROW(probe->start_http_fixture(),rust::Error);
        BOOST_CHECK_THROW(probe->start_regtest_http_clients(),rust::Error);
        BOOST_CHECK_THROW(probe->start_historical_http(),rust::Error);
        BOOST_CHECK_THROW(probe->start_historical_clients_http(0),rust::Error);
        BOOST_CHECK_THROW(probe->runtime_probe(),rust::Error);
    }
    chronik_bridge::ErgonNodeAccess readonly(GetConfig(),g_mempool,HistoricalRegtestBinding());
    BOOST_CHECK_THROW(ergon_archival::open_historical_readonly(root,readonly),rust::Error);
    BOOST_CHECK(!fs::exists(fs::path(root)/"probe.failed"));
    BOOST_CHECK(!readonly.index_failed.load());
}
BOOST_AUTO_TEST_CASE(client_owner_waits_for_native_import_and_joins_without_a_root) {
    LoadedRestore restore;g_mempool.SetIsLoaded(false);
    ergon_regtest::OwnedService owner(GetConfig(),HistoricalRegtestBinding(),
        chronik_bridge::HistoricalClientAdmission::Explicit,0);
    BOOST_CHECK(!owner.Status().port);
    BOOST_CHECK(!fs::exists(GetDataDir()/"chronik-upstream-service"));
    owner.Stop();owner.Stop();
    BOOST_CHECK(owner.Status().joined);
    BOOST_CHECK(!owner.Status().failed);
    BOOST_CHECK(!fs::exists(GetDataDir()/"chronik-upstream-service"));
}
BOOST_AUTO_TEST_CASE(client_owner_uses_joined_journal_and_reopens_only_its_identity) {
    LoadedRestore restore;g_mempool.SetIsLoaded(true);
    const auto root=(GetDataDir()/"chronik-upstream-service").string();
    std::string id;
    {
        ergon_regtest::OwnedService owner(GetConfig(),HistoricalRegtestBinding(),
            chronik_bridge::HistoricalClientAdmission::Explicit,0);
        BOOST_REQUIRE(WaitListener(owner));
        id=HistoricalIdentityFile(root);
        BOOST_CHECK(id.find("ergon-historical-client-service/v1")!=std::string::npos);
        BOOST_CHECK(!fs::exists(GetDataDir()/"chronik-upstream-shadow"));
        BOOST_CHECK(!fs::exists(GetDataDir()/"chronik-upstream-regtest"));
        CreateAndProcessBlock({},CScript()<<OP_TRUE);
        owner.Stop();BOOST_CHECK(owner.Status().joined);
        BOOST_CHECK(!owner.Status().failed);
    }
    ergon_regtest::OwnedService reopened(GetConfig(),HistoricalRegtestBinding(),
        chronik_bridge::HistoricalClientAdmission::Explicit,0);
    BOOST_REQUIRE(WaitListener(reopened));
    BOOST_CHECK_EQUAL(HistoricalIdentityFile(root),id);
    reopened.Stop();BOOST_CHECK(reopened.Status().joined);
    BOOST_CHECK(!reopened.Status().failed);
    BOOST_CHECK(!fs::exists(fs::path(root)/"probe.failed"));
    BOOST_CHECK_EQUAL(ChainActive().Height(),101);
}
BOOST_AUTO_TEST_SUITE_END()

BOOST_FIXTURE_TEST_SUITE(chronik_upstream_historical_admission_tests,TestChain100Setup)
BOOST_AUTO_TEST_CASE(explicit_main_and_regtest_binding_preserves_closed_clients_and_fixture_bounds) {
    DummyConfig main_config("main");
    chronik_bridge::HistoricalReadBinding main_binding(main_config,"main",
        main_config.GetChainParams().GetConsensus().hashGenesisBlock.GetHex());
    chronik_bridge::ErgonNodeAccess main_access(main_config,g_mempool,main_binding);
    BOOST_CHECK(main_access.permits_native_network());
    BOOST_CHECK(main_access.historical_readonly());
    BOOST_CHECK(!main_access.permits_clients());
    BOOST_CHECK(!chronik_bridge::upstream_runtime_permitted(main_access));
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool,HistoricalRegtestBinding());
    BOOST_CHECK(access.permits_native_network());
    BOOST_CHECK(access.permits_height(5000));
    BOOST_CHECK(access.permits_index_count(5001));
    BOOST_CHECK(access.permits_height(std::numeric_limits<int32_t>::max()-1));
    BOOST_CHECK(!access.permits_height(-1));
    BOOST_CHECK(!access.permits_height(std::numeric_limits<int32_t>::max()));
    BOOST_CHECK(!access.permits_clients());
    chronik_bridge::ErgonNodeAccess bounded(GetConfig(),g_mempool);
    BOOST_CHECK(!bounded.permits_height(5000));
    BOOST_CHECK(!bounded.permits_index_count(5001));
}
BOOST_AUTO_TEST_CASE(binding_rejects_bad_names_hashes_config_drift_and_enum_bypass) {
    const auto genesis=GetConfig().GetChainParams().GetConsensus().hashGenesisBlock.GetHex();
    BOOST_CHECK_THROW(chronik_bridge::HistoricalReadBinding(GetConfig(),"main",genesis),std::runtime_error);
    BOOST_CHECK_THROW(chronik_bridge::HistoricalReadBinding(GetConfig(),"test",genesis),std::runtime_error);
    BOOST_CHECK_THROW(chronik_bridge::HistoricalReadBinding(GetConfig(),"regtest",std::string(64,'0')),std::runtime_error);
    for(const auto &bad:{std::string{},std::string(63,'0'),std::string(65,'0'),std::string(64,'A'),std::string(64,'g')})
        BOOST_CHECK_THROW(chronik_bridge::HistoricalReadBinding(GetConfig(),"regtest",bad),std::runtime_error);
    BOOST_CHECK_THROW(chronik_bridge::ErgonNodeAccess(GetConfig(),g_mempool,
        chronik_bridge::NativeReadScope::HistoricalReadOnly),std::runtime_error);
    BOOST_CHECK_THROW(chronik_bridge::ErgonNodeAccess(GetConfig(),g_mempool,
        static_cast<chronik_bridge::NativeReadScope>(99)),std::runtime_error);
    DummyConfig mutable_config("regtest");
    auto binding=chronik_bridge::HistoricalReadBinding(mutable_config,"regtest",genesis);
    chronik_bridge::ErgonNodeAccess access(mutable_config,g_mempool,binding);
    mutable_config.SetChainParams("main");
    BOOST_CHECK(!access.permits_native_network());
    BOOST_CHECK_THROW(chronik_bridge::ErgonNodeAccess(mutable_config,g_mempool,binding),std::runtime_error);
}
BOOST_AUTO_TEST_CASE(native_active_genesis_must_match_config_before_root_access) {
    DummyConfig main_config("main");
    chronik_bridge::ErgonNodeAccess access(main_config,g_mempool,
        chronik_bridge::HistoricalReadBinding(main_config,"main",
            main_config.GetChainParams().GetConsensus().hashGenesisBlock.GetHex()));
    const auto root=(GetDataDir()/"historical-wrong-active-genesis").string();
    BOOST_CHECK_THROW(ergon_archival::open_historical_readonly(root,access),rust::Error);
    BOOST_CHECK(!fs::exists(fs::path(root)));
    BOOST_CHECK(!access.index_failed.load());
}
BOOST_AUTO_TEST_CASE(pruned_source_refused_before_root_access) {
    struct Reset { bool old=fPruneMode;~Reset(){fPruneMode=old;} } reset;
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool,HistoricalRegtestBinding());
    const auto root=(GetDataDir()/"historical-pruned-refusal").string();
    fPruneMode=true;
    BOOST_CHECK_THROW(ergon_archival::open_historical_readonly(root,access),rust::Error);
    BOOST_CHECK(!fs::exists(fs::path(root)));
    BOOST_CHECK(!access.index_failed.load());
}
BOOST_AUTO_TEST_CASE(index_constructors_cannot_silently_cross_profile_domains) {
    chronik_bridge::ErgonNodeAccess bounded(GetConfig(),g_mempool);
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool,HistoricalRegtestBinding());
    const auto root=(GetDataDir()/"historical-constructor-refusal").string();
    BOOST_CHECK_THROW(ergon_archival::open_historical_readonly(root,bounded),rust::Error);
    BOOST_CHECK_THROW(ergon_archival::open_archival(root,access),rust::Error);
    BOOST_CHECK_THROW(ergon_archival::open_regtest_service(root,access),rust::Error);
    BOOST_CHECK(!fs::exists(fs::path(root)));
}
BOOST_AUTO_TEST_CASE(upstream_cold_open_and_same_identity_reopen_without_transports) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool,HistoricalRegtestBinding());
    const auto root=ReplayRoot("historical-readonly-reopen");
    std::string identity;
    {
        auto probe=ergon_archival::open_historical_readonly(root,access);
        BOOST_CHECK_EQUAL(probe->snapshot().height,100);
        identity=HistoricalIdentityFile(root);
        BOOST_CHECK(identity.find("ergon-historical-readonly/v1")!=std::string::npos);
        BOOST_CHECK(identity.find("\"read_only\":true")!=std::string::npos);
        BOOST_CHECK(identity.find("\"network\":\"regtest\"")!=std::string::npos);
        BOOST_CHECK_THROW(probe->start_http_fixture(),rust::Error);
        BOOST_CHECK_THROW(probe->start_regtest_http(),rust::Error);
        BOOST_CHECK_THROW(probe->start_regtest_http_clients(),rust::Error);
        BOOST_CHECK_THROW(probe->start_historical_clients_http(0),rust::Error);
        BOOST_CHECK_THROW(probe->runtime_probe(),rust::Error);
        BOOST_CHECK(!access.index_failed.load());
    }
    BOOST_CHECK(!fs::exists(fs::path(root)/"probe.failed"));
    CreateAndProcessBlock({},CScript()<<OP_TRUE);
    auto reopened=ergon_archival::open_historical_readonly(root,access);
    BOOST_CHECK_EQUAL(reopened->snapshot().height,101);
    BOOST_CHECK_EQUAL(HistoricalIdentityFile(root),identity);
    BOOST_CHECK(!fs::exists(fs::path(root)/"probe.failed"));
}
BOOST_AUTO_TEST_CASE(existing_fixture_identity_is_refused_without_adoption_or_quarantine) {
    chronik_bridge::ErgonNodeAccess fixture(GetConfig(),g_mempool);
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool,HistoricalRegtestBinding());
    const auto root=ReplayRoot("historical-wrong-root-domain");
    { auto old=ergon_archival::open_archival(root,fixture);BOOST_CHECK_EQUAL(old->snapshot().height,100); }
    const auto before=HistoricalIdentityFile(root);
    BOOST_CHECK_THROW(ergon_archival::open_historical_readonly(root,access),rust::Error);
    BOOST_CHECK_EQUAL(HistoricalIdentityFile(root),before);
    BOOST_CHECK(!fs::exists(fs::path(root)/"probe.failed"));
    BOOST_CHECK(!access.index_failed.load());
}
BOOST_AUTO_TEST_CASE(redirected_root_refused_without_index_creation) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool,HistoricalRegtestBinding());
    const auto target=fs::path(ReplayRoot("historical-symlink-target"));
    const auto link=GetDataDir()/"historical-root-symlink";
    fs::create_directory_symlink(target,link);
    BOOST_CHECK_THROW(ergon_archival::open_historical_readonly(link.string(),access),rust::Error);
    BOOST_CHECK(!fs::exists(target/"indexes"));
    BOOST_CHECK(!fs::exists(target/"ergon-identity.json"));
}
BOOST_AUTO_TEST_SUITE_END()

// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#include <chronik-cpp/ergon_journal_replay.h>
#include <script/sign.h>
#include <util/time.h>
namespace {
std::string ReplayRoot(const char *name) {
    const auto directory=GetDataDir()/name;
    BOOST_REQUIRE(fs::create_directory(directory));
    return fs::canonical(directory).string();
}
template<class Hash> std::array<uint8_t,32> ReplayBytes(const Hash &hash) {
    std::array<uint8_t,32> out;std::copy_n(hash.begin(),32,out.begin());return out;
}
struct ReplayPoolSignals {
    ReplayPoolSignals() { GetMainSignals().RegisterWithMempoolSignals(g_mempool); }
    ~ReplayPoolSignals() { GetMainSignals().UnregisterWithMempoolSignals(g_mempool); }
};
CMutableTransaction ReplaySpend(const CTransactionRef &coinbase,const CKey &key) {
    CMutableTransaction tx;tx.nVersion=2;
    tx.vin.emplace_back(COutPoint(coinbase->GetId(),0));
    tx.vout.emplace_back(Amount::zero(),CScript()<<OP_RETURN<<std::vector<uint8_t>(64,0));
    std::vector<uint8_t> sig;
    const auto hash=SignatureHash(coinbase->vout[0].scriptPubKey,CTransaction(tx),0,
        SigHashType().withForkId(),coinbase->vout[0].nValue);
    if(!key.SignECDSA(hash,sig)) throw std::runtime_error("Synthetic signing refused");
    sig.push_back(uint8_t(SIGHASH_ALL|SIGHASH_FORKID));tx.vin[0].scriptSig<<sig;
    return tx;
}
bool ReplayAccept(const CMutableTransaction &tx) {
    LOCK(cs_main);CValidationState state;
    return AcceptToMemoryPool(GetConfig(),g_mempool,state,MakeTransactionRef(tx),
                             nullptr,true,Amount::zero());
}
}
BOOST_FIXTURE_TEST_SUITE(chronik_upstream_journal_replay_tests,TestChain100Setup)
BOOST_AUTO_TEST_CASE(delayed_consumer_preserves_actual_upstream_events) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool);
    auto probe=ergon_archival::open_archival(ReplayRoot("journal-ordered"),access);
    auto watch=probe->journal_watch(access);
    ReplayPoolSignals signals;
    ergon_archival::JournalReplay replay(*probe,access);
    BOOST_CHECK_THROW(probe->snapshot(),rust::Error);
    BOOST_REQUIRE(replay.Publish());
    const auto spend=ReplaySpend(m_coinbase_txns[0],coinbaseKey);
    const auto id=ReplayBytes(CTransaction(spend).GetId());
    BOOST_REQUIRE(ReplayAccept(spend));
    { LOCK2(cs_main,g_mempool.cs);g_mempool.removeRecursive(CTransaction(spend),MemPoolRemovalReason::EXPIRY); }
    BOOST_REQUIRE(ReplayAccept(spend));
    const auto block=CreateAndProcessBlock({spend},
        CScript()<<ToByteVector(coinbaseKey.GetPubKey())<<OP_CHECKSIG);
    {
        LOCK(cs_main);CValidationState state;
        BOOST_REQUIRE(ChainActive().Tip()->GetBlockHash()==block.GetHash());
        BOOST_REQUIRE(InvalidateBlock(GetConfig(),state,ChainActive().Tip()));
    }
    SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE_EQUAL(replay.Status().accepted,6);
    BOOST_CHECK_THROW(probe->snapshot(),rust::Error);
    BOOST_REQUIRE_EQUAL(replay.Drain(),6);
    BOOST_REQUIRE(replay.Publish());
    BOOST_CHECK_EQUAL(probe->snapshot().height,100);
    const auto tx=probe->tx_view(id,0);
    BOOST_CHECK_EQUAL(tx.height,-1);
    BOOST_CHECK_EQUAL(tx.input_value,0);
    BOOST_CHECK_EQUAL(tx.mempool_input_height,1);
    BOOST_CHECK(tx.mempool_input_coinbase);
    const auto messages=watch->drain();
    // The GLOBAL stream includes the block coinbase, not only our spend.
    // With no group subscription, upstream must not short-circuit it away.
    const std::vector<uint8_t> expected={1,2,1,3,3,1};
    BOOST_REQUIRE_EQUAL(messages.txs.size(),expected.size());
    for(size_t i=0;i<expected.size();++i) {
        BOOST_CHECK_EQUAL(messages.txs[i].kind,expected[i]);
        BOOST_CHECK(messages.txs[i].id==(i==3?ReplayBytes(block.vtx[0]->GetId()):id));
    }
    BOOST_REQUIRE_EQUAL(messages.blocks.size(),2);
    BOOST_CHECK_EQUAL(messages.blocks[0].kind,1);
    BOOST_CHECK_EQUAL(messages.blocks[1].kind,2);
    for(const auto &message:messages.blocks) {
        BOOST_CHECK(message.hash==ReplayBytes(block.GetHash()));
        BOOST_CHECK_EQUAL(message.height,101);
    }
    BOOST_CHECK_THROW(probe->begin_reconcile(access),rust::Error);
    BOOST_REQUIRE_EQUAL(replay.Drain(),0);
    BOOST_REQUIRE(replay.Publish());
    const auto empty=watch->drain();
    BOOST_CHECK(empty.txs.empty()&&empty.blocks.empty());
    replay.Stop();replay.Stop();
    BOOST_CHECK(replay.Status().closed);
}
BOOST_AUTO_TEST_CASE(overflow_closes_index_but_not_native_validation) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool);
    auto probe=ergon_archival::open_archival(ReplayRoot("journal-capacity"),access);
    auto watch=probe->journal_watch(access);ReplayPoolSignals signals;
    ergon_archival::JournalReplay replay(*probe,access,1);
    BOOST_REQUIRE(replay.Publish());
    const auto spend=ReplaySpend(m_coinbase_txns[0],coinbaseKey);
    BOOST_REQUIRE(ReplayAccept(spend));
    const auto block=CreateAndProcessBlock({spend},
        CScript()<<ToByteVector(coinbaseKey.GetPubKey())<<OP_CHECKSIG);
    BOOST_REQUIRE(ChainActive().Tip()->GetBlockHash()==block.GetHash());
    BOOST_CHECK_THROW(replay.Drain(),std::runtime_error);
    BOOST_CHECK(access.index_failed.load());
    BOOST_CHECK_THROW(probe->snapshot(),rust::Error);
    BOOST_CHECK_THROW(replay.Publish(),std::runtime_error);
    BOOST_REQUIRE(ReplayAccept(ReplaySpend(m_coinbase_txns[1],coinbaseKey)));
    const auto messages=watch->drain();
    BOOST_CHECK(messages.txs.empty()&&messages.blocks.empty());
}
BOOST_AUTO_TEST_CASE(stale_startup_cannot_anchor_and_does_not_steal_registry) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool);
    auto old=ergon_archival::open_archival(ReplayRoot("journal-stale"),access);
    CreateAndProcessBlock({},CScript()<<OP_TRUE);
    BOOST_CHECK_THROW(ergon_archival::JournalReplay(*old,access),rust::Error);
    auto fresh=ergon_archival::open_archival(ReplayRoot("journal-fresh"),access);
    ergon_archival::JournalReplay replay(*fresh,access);
    BOOST_REQUIRE(replay.Publish());
    BOOST_CHECK_EQUAL(fresh->snapshot().height,101);
}
BOOST_AUTO_TEST_CASE(storage_movement_refuses_before_upstream_delta) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool);
    auto probe=ergon_archival::open_archival(ReplayRoot("journal-storage"),access);
    auto watch=probe->journal_watch(access);
    ergon_archival::JournalReplay replay(*probe,access);
    BOOST_REQUIRE(replay.Publish());
    const auto block=CreateAndProcessBlock({},CScript()<<OP_TRUE);
    struct Restore {
        CBlockIndex *index;unsigned pos;
        ~Restore() { LOCK(cs_main);index->nDataPos=pos; }
    };
    Restore restore{ChainActive().Tip(),ChainActive().Tip()->nDataPos};
    { LOCK(cs_main);++restore.index->nDataPos; }
    BOOST_CHECK_THROW(replay.Drain(),std::runtime_error);
    BOOST_CHECK(access.index_failed.load());
    const auto messages=watch->drain();
    BOOST_CHECK(messages.txs.empty()&&messages.blocks.empty());
}
BOOST_AUTO_TEST_SUITE_END()

// Native shutdown is cancellation, never evidence of database corruption.
BOOST_FIXTURE_TEST_SUITE(chronik_upstream_journal_shutdown_tests,TestChain100Setup)
BOOST_AUTO_TEST_CASE(healthy_shutdown_closes_admission_without_quarantine) {
    struct Reset { ~Reset() { AbortShutdown(); } } reset;
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool);
    const auto root=ReplayRoot("journal-shutdown-healthy");
    {
        auto probe=ergon_archival::open_archival(root,access);
        ergon_archival::JournalReplay replay(*probe,access);
        BOOST_REQUIRE(replay.Publish());
        CreateAndProcessBlock({},CScript()<<OP_TRUE);
        SyncWithValidationInterfaceQueue();
        BOOST_REQUIRE_EQUAL(replay.Drain(),1);
        // The upstream block delta is complete, but publication is cancelled.
        // Reopening must recover that valid prefix without a failure marker.
        StartShutdown();
        BOOST_CHECK_THROW(replay.Drain(),ergon_archival::JournalReplayCancelled);
        BOOST_CHECK_THROW(replay.Publish(),ergon_archival::JournalReplayCancelled);
        BOOST_CHECK(!access.index_failed.load());
        replay.Stop();
        BOOST_CHECK(replay.Status().closed);
    }
    AbortShutdown();
    BOOST_CHECK(!fs::exists(fs::path(root)/"probe.failed"));
    auto reopened=ergon_archival::open_archival(root,access);
    BOOST_CHECK_EQUAL(reopened->snapshot().height,101);
}
BOOST_AUTO_TEST_CASE(real_continuity_failure_still_wins_during_shutdown) {
    struct Reset { ~Reset() { AbortShutdown(); } } reset;
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool);
    const auto root=ReplayRoot("journal-shutdown-overflow");
    {
        auto probe=ergon_archival::open_archival(root,access);
        ReplayPoolSignals signals;
        ergon_archival::JournalReplay replay(*probe,access,1);
        BOOST_REQUIRE(replay.Publish());
        const auto spend=ReplaySpend(m_coinbase_txns[0],coinbaseKey);
        BOOST_REQUIRE(ReplayAccept(spend));
        CreateAndProcessBlock({spend},CScript()<<ToByteVector(coinbaseKey.GetPubKey())<<OP_CHECKSIG);
        BOOST_CHECK(replay.Status().failure!=ValidationEventJournal::Failure::None);
        StartShutdown();
        BOOST_CHECK_THROW(replay.Drain(),std::runtime_error);
        BOOST_CHECK(access.index_failed.load());
        replay.Stop();
    }
    AbortShutdown();
    BOOST_CHECK(fs::exists(fs::path(root)/"probe.failed"));
    BOOST_REQUIRE(ReplayAccept(ReplaySpend(m_coinbase_txns[1],coinbaseKey)));
}
BOOST_AUTO_TEST_SUITE_END()

// Continuous journal owner: no HTTP listener precedes its initial snapshot cut.
BOOST_FIXTURE_TEST_SUITE(chronik_upstream_continuous_journal_tests,TestChain100Setup)
BOOST_AUTO_TEST_CASE(delayed_owner_replays_and_reopens_nonempty_pool) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool,
        HistoricalRegtestBinding(),chronik_bridge::HistoricalClientAdmission::Explicit);
    ReplayPoolSignals signals;ReleaseLatch latch;
    auto entered=latch.entered.get_future();std::atomic<bool> once{false};
    const auto root=ReplayRoot("continuous-delayed");
    ergon_archival::ArchivalWorker worker(root,access,128,
        [&] { if(!once.exchange(true)) latch.Block(); },{},{},{},nullptr,true,ergon_archival::WorkerProfile::HistoricalClientService);
    BOOST_REQUIRE(entered.wait_for(std::chrono::seconds(5))==std::future_status::ready);
    const auto spend=ReplaySpend(m_coinbase_txns[0],coinbaseKey);
    BOOST_REQUIRE(ReplayAccept(spend));
    { LOCK2(cs_main,g_mempool.cs);g_mempool.removeRecursive(CTransaction(spend),MemPoolRemovalReason::EXPIRY); }
    BOOST_REQUIRE(ReplayAccept(spend));
    CreateAndProcessBlock({spend},CScript()<<ToByteVector(coinbaseKey.GetPubKey())<<OP_CHECKSIG);
    { LOCK(cs_main);CValidationState state;
      BOOST_REQUIRE(InvalidateBlock(GetConfig(),state,ChainActive().Tip())); }
    SyncWithValidationInterfaceQueue();
    BOOST_CHECK(!worker.Status().ready);
    BOOST_CHECK_EQUAL(worker.TryStartHistoricalClientsHttp(0),0);
    latch.Resume();
    BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted,10000));
    BOOST_CHECK(!worker.Status().failed);
    BOOST_CHECK_EQUAL(worker.ReadSnapshot().height,100);
    BOOST_CHECK_EQUAL(worker.ReadTx(ReplayBytes(CTransaction(spend).GetId()),0).height,-1);
    worker.Stop();BOOST_CHECK(worker.Status().joined);
    ergon_archival::ArchivalWorker reopened(root,access,128,{},{},{},{},nullptr,true,ergon_archival::WorkerProfile::HistoricalClientService);
    BOOST_REQUIRE(reopened.WaitThrough(0,10000));
    BOOST_CHECK_EQUAL(reopened.ReadTx(ReplayBytes(CTransaction(spend).GetId()),0).height,-1);
    reopened.Stop();BOOST_CHECK(reopened.Status().joined);
}
BOOST_AUTO_TEST_CASE(initial_capture_mutation_is_before_atomic_live_cut) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool,
        HistoricalRegtestBinding(),chronik_bridge::HistoricalClientAdmission::Explicit);
    bool changed=false;
    ergon_archival::ArchivalWorker worker(ReplayRoot("continuous-startup-cut"),access,128,{},{},[&] {
        if(!changed) { changed=true;CreateAndProcessBlock({},CScript()<<OP_TRUE); }
    },{},nullptr,true,ergon_archival::WorkerProfile::HistoricalClientService);
    BOOST_REQUIRE(worker.WaitThrough(0,10000));
    BOOST_CHECK(changed);BOOST_CHECK_EQUAL(worker.ReadSnapshot().height,101);
    CreateAndProcessBlock({},CScript()<<OP_TRUE);SyncWithValidationInterfaceQueue();
    BOOST_REQUIRE(worker.WaitThrough(worker.Status().accepted,10000));
    BOOST_CHECK_EQUAL(worker.ReadSnapshot().height,102);
    worker.Stop();BOOST_CHECK(worker.Status().joined);
}
BOOST_AUTO_TEST_CASE(overflow_fails_owned_index_and_native_admission_continues) {
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool,
        HistoricalRegtestBinding(),chronik_bridge::HistoricalClientAdmission::Explicit);
    ReplayPoolSignals signals;ReleaseLatch latch;
    auto entered=latch.entered.get_future();std::atomic<bool> once{false};
    ergon_archival::ArchivalWorker worker(ReplayRoot("continuous-overflow"),access,1,
        [&] { if(!once.exchange(true)) latch.Block(); },{},{},{},nullptr,true,ergon_archival::WorkerProfile::HistoricalClientService);
    BOOST_REQUIRE(entered.wait_for(std::chrono::seconds(5))==std::future_status::ready);
    const auto spend=ReplaySpend(m_coinbase_txns[0],coinbaseKey);
    BOOST_REQUIRE(ReplayAccept(spend));
    CreateAndProcessBlock({spend},CScript()<<ToByteVector(coinbaseKey.GetPubKey())<<OP_CHECKSIG);
    latch.Resume();
    BOOST_CHECK(!worker.WaitThrough(worker.Status().accepted,10000));
    BOOST_CHECK(worker.Status().failed);BOOST_CHECK(access.index_failed.load());
    BOOST_CHECK_THROW(worker.ReadSnapshot(),std::runtime_error);
    BOOST_REQUIRE(ReplayAccept(ReplaySpend(m_coinbase_txns[1],coinbaseKey)));
    worker.Stop();BOOST_CHECK(worker.Status().joined);
}
BOOST_AUTO_TEST_CASE(shutdown_between_worker_fence_and_drain_is_not_corruption) {
    struct Reset { ~Reset() { AbortShutdown(); } } reset;
    chronik_bridge::ErgonNodeAccess access(GetConfig(),g_mempool,
        HistoricalRegtestBinding(),chronik_bridge::HistoricalClientAdmission::Explicit);
    const auto root=ReplayRoot("continuous-shutdown-fence");
    std::promise<void> entered;auto signal=entered.get_future();
    {
        ergon_archival::ArchivalWorker worker(root,access,128,[&] {
            StartShutdown();entered.set_value();
        },{},{},{},nullptr,true,ergon_archival::WorkerProfile::HistoricalClientService);
        BOOST_REQUIRE(signal.wait_for(std::chrono::seconds(5))==std::future_status::ready);
        worker.Stop();
        BOOST_CHECK(worker.Status().joined);
        BOOST_CHECK(!worker.Status().failed);
        BOOST_CHECK(!access.index_failed.load());
    }
    AbortShutdown();
    BOOST_CHECK(!fs::exists(fs::path(root)/"probe.failed"));
    ergon_archival::ArchivalWorker reopened(root,access,128,{},{},{},{},nullptr,true,ergon_archival::WorkerProfile::HistoricalClientService);
    BOOST_REQUIRE(reopened.WaitThrough(0,10000));
    BOOST_CHECK_EQUAL(reopened.ReadSnapshot().height,100);
    reopened.Stop();BOOST_CHECK(reopened.Status().joined);
}
BOOST_AUTO_TEST_SUITE_END()

#ifdef ENABLE_CHRONIK_UPSTREAM_SHADOW
BOOST_FIXTURE_TEST_SUITE(chronik_upstream_shadow_owner_tests,TestChain100Setup)
BOOST_AUTO_TEST_CASE(explicit_worker_profile_cannot_be_inferred_or_mixed) {
    chronik_bridge::ErgonNodeAccess historical(GetConfig(),g_mempool,HistoricalRegtestBinding());
    chronik_bridge::ErgonNodeAccess inherited(GetConfig(),g_mempool);
    const auto root=(GetDataDir()/"shadow-explicit-worker").string();
    BOOST_CHECK_THROW(ergon_archival::ArchivalWorker(root,historical),std::runtime_error);
    BOOST_CHECK_THROW(ergon_archival::ArchivalWorker(root,inherited,128,{},{},{},{},nullptr,true,
        ergon_archival::WorkerProfile::HistoricalReadOnly),std::runtime_error);
    BOOST_CHECK_THROW(ergon_archival::ArchivalWorker(root,historical,128,{},{},{},{},nullptr,false,
        ergon_archival::WorkerProfile::HistoricalReadOnly),std::runtime_error);
    BOOST_CHECK_THROW(ergon_archival::ArchivalWorker(root,inherited,128,{},{},{},{},nullptr,true,
        static_cast<ergon_archival::WorkerProfile>(99)),std::runtime_error);
    BOOST_CHECK(!fs::exists(fs::path(root)));
    const auto startup_root=ReplayRoot("shadow-startup-no-listener");
    auto startup=ergon_archival::open_historical_readonly(startup_root,historical);
    BOOST_CHECK_THROW(startup->start_historical_http(),rust::Error);
    BOOST_CHECK(!historical.permits_clients());
}
BOOST_AUTO_TEST_CASE(historical_plugin_worker_requires_exact_explicit_profile) {
    chronik_bridge::ErgonNodeAccess historical(GetConfig(),g_mempool,HistoricalRegtestBinding());
    chronik_bridge::ErgonNodeAccess inherited(GetConfig(),g_mempool);
    const auto root=(GetDataDir()/"historical-plugin-refused").string();
    BOOST_CHECK_THROW(ergon_archival::ArchivalWorker(root,historical,128,{},{},{},{},nullptr,true,
        ergon_archival::WorkerProfile::HistoricalPluginsReadOnly),std::runtime_error);
    BOOST_CHECK_THROW(ergon_archival::ArchivalWorker(root,inherited,128,{},{},{},{},nullptr,true,
        ergon_archival::WorkerProfile::HistoricalPluginsReadOnly),std::runtime_error);
    BOOST_CHECK(!fs::exists(fs::path(root)));
    BOOST_CHECK(!historical.permits_clients());
}
#ifdef ENABLE_CHRONIK_UPSTREAM_REGTEST_PLUGINS
BOOST_AUTO_TEST_CASE(bound_historical_plugin_owner_has_separate_readonly_identity) {
    LoadedRestore restore;g_mempool.SetIsLoaded(true);
    const auto inputs=ReplayRoot("historical-public-plugin-inputs");
    {
        std::ofstream config(inputs+"/plugins.toml");
        config << "[regtest.plugin.ergon_history_probe]\nclass = \"HistoricalReadProbe\"\n";
        BOOST_REQUIRE(config.good());
        std::ofstream module(inputs+"/ergon_history_probe.py");
        module << "from chronik_plugin.plugin import Plugin\n"
                  "class HistoricalReadProbe(Plugin):\n"
                  "    def lokad_id(self): return b'HPRB'\n"
                  "    def version(self): return '1.0.0'\n"
                  "    def run(self, tx): return []\n";
        BOOST_REQUIRE(module.good());
    }
    const std::string binding(ergon_archival::operator_bundle_binding(inputs));
    ergon_regtest::OwnedService owner(GetConfig(),HistoricalRegtestBinding(),{},inputs,binding);
    BOOST_REQUIRE(WaitListener(owner));
    const auto root=(GetDataDir()/"chronik-upstream-shadow-plugins").string();
    const auto identity=HistoricalIdentityFile(root);
    BOOST_CHECK(identity.find("ergon-historical-plugins-readonly/v1")!=std::string::npos);
    BOOST_CHECK(identity.find("\"network\":\"regtest\"")!=std::string::npos);
    BOOST_CHECK(identity.find("\"read_only\":true")!=std::string::npos);
    BOOST_CHECK(identity.find("\"plugins\":true")!=std::string::npos);
    BOOST_CHECK(identity.find(binding)!=std::string::npos);
    BOOST_CHECK(!fs::exists(GetDataDir()/"chronik-upstream-shadow"));
    CreateAndProcessBlock({},CScript()<<OP_TRUE);
    owner.Stop();
    BOOST_CHECK(owner.Status().joined);
    BOOST_CHECK(!owner.Status().failed);
    BOOST_CHECK(!fs::exists(fs::path(root)/"probe.failed"));
}
#endif
BOOST_AUTO_TEST_CASE(shadow_owner_waits_for_import_without_opening_an_index) {
    LoadedRestore restore;g_mempool.SetIsLoaded(false);
    ergon_regtest::OwnedService owner(GetConfig(),HistoricalRegtestBinding());
    BOOST_CHECK(!owner.Status().port);
    BOOST_CHECK(!fs::exists(GetDataDir()/"chronik-upstream-shadow"));
    owner.Stop();owner.Stop();
    BOOST_CHECK(owner.Status().joined);
    BOOST_CHECK(!owner.Status().failed);
    BOOST_CHECK(!fs::exists(GetDataDir()/"chronik-upstream-shadow"));
}
BOOST_AUTO_TEST_CASE(shadow_owner_reopens_its_own_identity_and_native_progress_continues) {
    LoadedRestore restore;g_mempool.SetIsLoaded(true);
    const auto root=(GetDataDir()/"chronik-upstream-shadow").string();
    std::string identity;
    {
        ergon_regtest::OwnedService owner(GetConfig(),HistoricalRegtestBinding());
        BOOST_REQUIRE(WaitListener(owner));
        BOOST_CHECK(!owner.Status().failed);
        identity=HistoricalIdentityFile(root);
        BOOST_CHECK(identity.find("ergon-historical-readonly/v1")!=std::string::npos);
        CreateAndProcessBlock({},CScript()<<OP_TRUE);
        owner.Stop();BOOST_CHECK(owner.Status().joined);
    }
    BOOST_CHECK(!fs::exists(fs::path(root)/"probe.failed"));
    BOOST_CHECK(!fs::exists(GetDataDir()/"chronik-upstream-regtest"));
    ergon_regtest::OwnedService reopened(GetConfig(),HistoricalRegtestBinding());
    BOOST_REQUIRE(WaitListener(reopened));
    BOOST_CHECK_EQUAL(HistoricalIdentityFile(root),identity);
    reopened.Stop();BOOST_CHECK(reopened.Status().joined);
    BOOST_CHECK(!reopened.Status().failed);
    BOOST_CHECK_EQUAL(ChainActive().Height(),101);
}
BOOST_AUTO_TEST_CASE(shadow_stop_joins_inflight_startup_hook_before_root_creation) {
    LoadedRestore restore;g_mempool.SetIsLoaded(true);
    ReleaseLatch latch;auto entered=latch.entered.get_future();
    ergon_regtest::OwnedService owner(GetConfig(),HistoricalRegtestBinding(),[&]{latch.Block();});
    BOOST_REQUIRE(entered.wait_for(std::chrono::seconds(5))==std::future_status::ready);
    auto stopped=std::async(std::launch::async,[&]{owner.Stop();});
    BOOST_CHECK(stopped.wait_for(std::chrono::milliseconds(20))==std::future_status::timeout);
    latch.Resume();
    BOOST_REQUIRE(stopped.wait_for(std::chrono::seconds(5))==std::future_status::ready);
    stopped.get();
    BOOST_CHECK(owner.Status().joined);
    BOOST_CHECK(!owner.Status().failed);
    BOOST_CHECK(!fs::exists(GetDataDir()/"chronik-upstream-shadow"));
}
BOOST_AUTO_TEST_SUITE_END()
#endif

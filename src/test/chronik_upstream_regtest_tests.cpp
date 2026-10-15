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
#include <primitives/block.h>
#include <script/script.h>
#include <shutdown.h>
#include <test/setup_common.h>
#include <txmempool.h>
#include <util/system.h>
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

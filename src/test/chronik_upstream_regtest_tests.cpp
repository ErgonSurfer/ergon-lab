// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#include <boost/test/unit_test.hpp>
#include <chronik-cpp/ergon_archival_worker.h>
#include <chronik_lib/src/ergon_archival.rs.h>
#include <config.h>
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

BOOST_FIXTURE_TEST_SUITE(chronik_upstream_regtest_tests, TestChain100Setup)
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

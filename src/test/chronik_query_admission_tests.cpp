// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#include <boost/test/unit_test.hpp>
#include <chronik/query-admission.h>
#include <future>
#include <thread>

BOOST_AUTO_TEST_SUITE(chronik_query_admission_tests)

BOOST_AUTO_TEST_CASE(busy_query_and_mutation_reject_without_waiting) {
    chronik::QueryAdmission gate;
    auto mutation = gate.Mutation();
    bool acquired = true;
    std::thread reader([&] { acquired = gate.TryQuery().owns_lock(); });
    reader.join();
    BOOST_CHECK(!acquired);
    mutation.unlock();
    auto first = gate.TryQuery();
    BOOST_REQUIRE(first.owns_lock());
    std::thread second([&] { acquired = gate.TryQuery().owns_lock(); });
    second.join();
    BOOST_CHECK(!acquired);
    first.unlock();
    BOOST_CHECK(gate.TryQuery().owns_lock());
}

BOOST_AUTO_TEST_CASE(close_invalidates_inflight_and_prevents_new_queries) {
    chronik::QueryAdmission gate;
    auto query = gate.TryQuery();
    BOOST_REQUIRE(query.owns_lock());
    std::thread shutdown([&] { gate.Close(); });
    shutdown.join();
    BOOST_CHECK(!gate.IsOpen());
    query.unlock();
    BOOST_CHECK(!gate.TryQuery().owns_lock());
    // Closing request admission still allows the worker to drain before close.
    BOOST_CHECK(gate.Mutation().owns_lock());
}

BOOST_AUTO_TEST_CASE(runtime_close_waits_for_read_lease_before_destroy) {
    chronik::QueryAdmission gate;
    auto query = gate.TryQuery();
    BOOST_REQUIRE(query.owns_lock());
    std::promise<void> started;
    auto ready = started.get_future();
    std::atomic<bool> destroyed{false};
    std::thread shutdown([&] {
        gate.Close();
        started.set_value();
        auto mutation = gate.Mutation();
        destroyed.store(true);
    });
    ready.wait();
    BOOST_CHECK(!destroyed.load());
    query.unlock();
    shutdown.join();
    BOOST_CHECK(destroyed.load());
}

BOOST_AUTO_TEST_SUITE_END()

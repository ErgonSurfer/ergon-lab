// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#include "../../chronik/event-envelope.h"
#ifdef ENABLE_CHRONIK_OBSERVER
#include "../../chronik/node-observer.h"
#endif

#include <boost/test/unit_test.hpp>

#include <array>
#include <atomic>
#include <chrono>
#include <future>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>

using namespace std::chrono_literals;

#ifdef ENABLE_CHRONIK_OBSERVER
namespace chronik {
bool StartNodeObserverForTest(uint8_t test_failure_point) noexcept;
bool NodeObserverActiveForTest() noexcept;
bool NodeObserverRegisteredForTest() noexcept;
} // namespace chronik
#endif

BOOST_AUTO_TEST_SUITE(chronik_event_envelope_tests)

bool WaitForWorkerDone(chronik::EventEnvelope &envelope,
                       std::chrono::milliseconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    do {
        if (envelope.Snapshot().worker_done) {
            return true;
        }
        std::this_thread::sleep_for(1ms);
    } while (std::chrono::steady_clock::now() < deadline);
    return envelope.Snapshot().worker_done;
}

BOOST_AUTO_TEST_CASE(orders_events_and_owns_payloads) {
    std::mutex mutex;
    std::vector<chronik::OwnedEvent> observed;
    auto envelope = chronik::EventEnvelope::Create(
        4, [&](const chronik::OwnedEvent &event) {
            std::lock_guard<std::mutex> lock(mutex);
            observed.push_back(event);
            return true;
        });
    BOOST_REQUIRE(envelope);

    std::array<uint8_t, 32> hash{};
    hash[0] = 7;
    std::vector<uint8_t> payload{1, 2, 3};
    envelope->Submit(1, chronik::EventKind::BLOCK_CONNECTED, hash, 9,
                     payload);
    payload.assign({8, 8, 8});
    hash[0] = 99;
    envelope->Submit(2, chronik::EventKind::BLOCK_DISCONNECTED, hash, -1,
                     {});

    BOOST_CHECK(envelope->Shutdown(2s));
    const auto snapshot = envelope->Snapshot();
    BOOST_CHECK_EQUAL(snapshot.accepted, 2);
    BOOST_CHECK_EQUAL(snapshot.processed, 2);
    BOOST_CHECK_EQUAL(snapshot.last_processed_sequence, 2);
    BOOST_CHECK(!snapshot.degraded);
    BOOST_CHECK(!snapshot.rebuild_required);
    BOOST_REQUIRE_EQUAL(observed.size(), 2);
    BOOST_CHECK_EQUAL(observed[0].sequence, 1);
    BOOST_CHECK_EQUAL(observed[0].hash[0], 7);
    const std::vector<uint8_t> expected_payload{1, 2, 3};
    BOOST_CHECK_EQUAL_COLLECTIONS(observed[0].payload.begin(),
                                  observed[0].payload.end(),
                                  expected_payload.begin(),
                                  expected_payload.end());
    BOOST_CHECK_EQUAL(observed[1].sequence, 2);
    BOOST_CHECK_EQUAL(observed[1].hash[0], 99);
}

BOOST_AUTO_TEST_CASE(wait_transition_cannot_lose_the_only_notification) {
    std::promise<void> wait_entered_promise;
    auto wait_entered = wait_entered_promise.get_future();
    std::promise<void> release_wait_promise;
    auto release_wait = release_wait_promise.get_future().share();
    std::atomic<bool> forced{false};
    auto hooks = std::make_shared<chronik::EventEnvelopeTestHooks>();
    hooks->before_worker_wait = [&] {
        if (!forced.exchange(true)) {
            wait_entered_promise.set_value();
            release_wait.wait();
        }
    };

    auto envelope = chronik::EventEnvelope::Create(
        1, [](const chronik::OwnedEvent &) { return true; }, hooks);
    BOOST_REQUIRE(envelope);
    BOOST_REQUIRE(wait_entered.wait_for(2s) == std::future_status::ready);

    const std::array<uint8_t, 32> hash{};
    auto submit = std::async(std::launch::async, [&] {
        envelope->Submit(1, chronik::EventKind::BLOCK_CONNECTED, hash, 1,
                         {1});
    });
    BOOST_CHECK(submit.wait_for(20ms) == std::future_status::timeout);
    release_wait_promise.set_value();
    BOOST_REQUIRE(submit.wait_for(250ms) == std::future_status::ready);
    submit.get();

    BOOST_CHECK(envelope->Shutdown(2s));
    const auto snapshot = envelope->Snapshot();
    BOOST_CHECK_EQUAL(snapshot.accepted, 1);
    BOOST_CHECK_EQUAL(snapshot.processed, 1);
    BOOST_CHECK_EQUAL(snapshot.last_enqueued_sequence, 1);
    BOOST_CHECK_EQUAL(snapshot.last_processed_sequence, 1);
    BOOST_CHECK(!snapshot.degraded);
    BOOST_CHECK(!snapshot.rebuild_required);
}

#ifdef ENABLE_CHRONIK_OBSERVER
BOOST_AUTO_TEST_CASE(observer_construction_exception_disables_only_observer) {
    const auto start = std::chrono::steady_clock::now();
    BOOST_CHECK(!chronik::StartNodeObserverForTest(1));
    BOOST_CHECK(std::chrono::steady_clock::now() - start < 500ms);
    BOOST_CHECK(!chronik::NodeObserverActiveForTest());
    BOOST_CHECK(!chronik::NodeObserverRegisteredForTest());
}

BOOST_AUTO_TEST_CASE(observer_bootstrap_exception_disables_only_observer) {
    const auto start = std::chrono::steady_clock::now();
    BOOST_CHECK(!chronik::StartNodeObserverForTest(2));
    BOOST_CHECK(std::chrono::steady_clock::now() - start < 500ms);
    BOOST_CHECK(!chronik::NodeObserverActiveForTest());
    BOOST_CHECK(!chronik::NodeObserverRegisteredForTest());
}

BOOST_AUTO_TEST_CASE(observer_envelope_exception_disables_only_observer) {
    const auto start = std::chrono::steady_clock::now();
    BOOST_CHECK(!chronik::StartNodeObserverForTest(3));
    BOOST_CHECK(std::chrono::steady_clock::now() - start < 500ms);
    BOOST_CHECK(!chronik::NodeObserverActiveForTest());
    BOOST_CHECK(!chronik::NodeObserverRegisteredForTest());
}
#endif

BOOST_AUTO_TEST_CASE(capacity_overflow_never_waits_for_consumer) {
    std::promise<void> entered_promise;
    auto entered = entered_promise.get_future();
    std::promise<void> release_promise;
    auto release = release_promise.get_future().share();
    std::atomic<bool> announced{false};
    auto envelope = chronik::EventEnvelope::Create(
        2, [&](const chronik::OwnedEvent &) {
            if (!announced.exchange(true)) {
                entered_promise.set_value();
            }
            release.wait();
            return true;
        });
    BOOST_REQUIRE(envelope);

    const std::array<uint8_t, 32> hash{};
    envelope->Submit(1, chronik::EventKind::BLOCK_CONNECTED, hash, 1, {1});
    BOOST_REQUIRE(entered.wait_for(2s) == std::future_status::ready);
    auto queued = std::async(std::launch::async, [&] {
        envelope->Submit(2, chronik::EventKind::BLOCK_CONNECTED, hash, 2,
                         {2});
    });
    BOOST_CHECK(queued.wait_for(250ms) == std::future_status::ready);
    queued.get();
    envelope->Submit(3, chronik::EventKind::BLOCK_CONNECTED, hash, 3, {3});

    auto overflow = std::async(std::launch::async, [&] {
        envelope->Submit(4, chronik::EventKind::BLOCK_CONNECTED, hash, 4,
                         {4});
    });
    BOOST_CHECK(overflow.wait_for(250ms) == std::future_status::ready);
    overflow.get();
    const auto overflowed = envelope->Snapshot();
    BOOST_CHECK(overflowed.degraded);
    BOOST_CHECK(overflowed.rebuild_required);
    BOOST_CHECK_EQUAL(overflowed.accepted, 3);
    BOOST_CHECK_EQUAL(overflowed.last_enqueued_sequence, 3);

    release_promise.set_value();
    BOOST_CHECK(envelope->Shutdown(2s));
}

BOOST_AUTO_TEST_CASE(concurrent_producers_fail_closed_without_waiting) {
    std::promise<void> acquired_promise;
    auto acquired = acquired_promise.get_future();
    std::promise<void> release_promise;
    auto release = release_promise.get_future().share();
    std::atomic<uint64_t> producer_hooks{0};
    std::atomic<uint64_t> consumer_calls{0};
    auto hooks = std::make_shared<chronik::EventEnvelopeTestHooks>();
    hooks->producer_acquired = [&] {
        if (producer_hooks.fetch_add(1) == 0) {
            acquired_promise.set_value();
            release.wait();
        }
    };
    auto envelope = chronik::EventEnvelope::Create(
        2,
        [&](const chronik::OwnedEvent &) {
            ++consumer_calls;
            return true;
        },
        hooks);
    BOOST_REQUIRE(envelope);

    const std::array<uint8_t, 32> hash{};
    auto first = std::async(std::launch::async, [&] {
        envelope->Submit(1, chronik::EventKind::BLOCK_CONNECTED, hash, 1,
                         {1});
    });
    BOOST_REQUIRE(acquired.wait_for(2s) == std::future_status::ready);
    auto colliding = std::async(std::launch::async, [&] {
        envelope->Submit(2, chronik::EventKind::BLOCK_CONNECTED, hash, 2,
                         {2});
    });
    BOOST_CHECK(colliding.wait_for(250ms) == std::future_status::ready);
    colliding.get();

    const auto collided = envelope->Snapshot();
    BOOST_CHECK(collided.degraded);
    BOOST_CHECK(collided.rebuild_required);
    BOOST_CHECK_EQUAL(collided.accepted, 0);
    BOOST_CHECK_EQUAL(collided.processed, 0);

    const auto release_time = std::chrono::steady_clock::now();
    release_promise.set_value();
    BOOST_CHECK(first.wait_for(250ms) == std::future_status::ready);
    first.get();
    BOOST_CHECK(std::chrono::steady_clock::now() - release_time < 500ms);
    BOOST_CHECK(WaitForWorkerDone(*envelope, 2s));
    BOOST_CHECK(envelope->Shutdown(2s));
    BOOST_CHECK_EQUAL(consumer_calls.load(), 0);
}

BOOST_AUTO_TEST_CASE(sequence_gap_fails_closed) {
    auto envelope = chronik::EventEnvelope::Create(
        4, [](const chronik::OwnedEvent &) { return true; });
    BOOST_REQUIRE(envelope);
    const std::array<uint8_t, 32> hash{};
    envelope->Submit(1, chronik::EventKind::BLOCK_CONNECTED, hash, 1, {});
    envelope->Submit(3, chronik::EventKind::BLOCK_CONNECTED, hash, 3, {});
    BOOST_CHECK(envelope->Shutdown(2s));
    const auto snapshot = envelope->Snapshot();
    BOOST_CHECK(snapshot.degraded);
    BOOST_CHECK(snapshot.rebuild_required);
    BOOST_CHECK_EQUAL(snapshot.processed, 1);
    BOOST_CHECK_EQUAL(snapshot.last_processed_sequence, 1);
}

BOOST_AUTO_TEST_CASE(worker_error_and_exception_are_contained) {
    const std::array<uint8_t, 32> hash{};
    auto rejected = chronik::EventEnvelope::Create(
        1, [](const chronik::OwnedEvent &) { return false; });
    BOOST_REQUIRE(rejected);
    rejected->Submit(1, chronik::EventKind::BLOCK_CONNECTED, hash, 1, {});
    BOOST_CHECK(rejected->Shutdown(2s));
    BOOST_CHECK(rejected->Snapshot().degraded);
    BOOST_CHECK(rejected->Snapshot().rebuild_required);

    auto throwing = chronik::EventEnvelope::Create(
        1, [](const chronik::OwnedEvent &) -> bool {
            throw std::runtime_error("injected consumer failure");
        });
    BOOST_REQUIRE(throwing);
    throwing->Submit(1, chronik::EventKind::BLOCK_CONNECTED, hash, 1, {});
    BOOST_CHECK(throwing->Shutdown(2s));
    BOOST_CHECK(throwing->Snapshot().degraded);
    BOOST_CHECK(throwing->Snapshot().rebuild_required);
}

BOOST_AUTO_TEST_CASE(startup_exception_returns_null_and_worker_exits) {
    std::promise<void> worker_exit_promise;
    auto worker_exit = worker_exit_promise.get_future();
    auto hooks = std::make_shared<chronik::EventEnvelopeTestHooks>();
    hooks->after_worker_start = [] {
        throw std::runtime_error("injected startup wait failure");
    };
    hooks->worker_exit = [&] { worker_exit_promise.set_value(); };

    const auto start = std::chrono::steady_clock::now();
    auto envelope = chronik::EventEnvelope::Create(
        1, [](const chronik::OwnedEvent &) { return true; }, hooks);
    BOOST_CHECK(!envelope);
    BOOST_CHECK(std::chrono::steady_clock::now() - start < 500ms);
    BOOST_CHECK(worker_exit.wait_for(2s) == std::future_status::ready);
}

BOOST_AUTO_TEST_CASE(worker_exception_degrades_and_finishes) {
    auto hooks = std::make_shared<chronik::EventEnvelopeTestHooks>();
    hooks->worker_entry = [] {
        throw std::runtime_error("injected worker-loop failure");
    };
    auto envelope = chronik::EventEnvelope::Create(
        1, [](const chronik::OwnedEvent &) { return true; }, hooks);
    BOOST_REQUIRE(envelope);
    BOOST_CHECK(WaitForWorkerDone(*envelope, 2s));
    const auto snapshot = envelope->Snapshot();
    BOOST_CHECK(snapshot.degraded);
    BOOST_CHECK(snapshot.rebuild_required);
    BOOST_CHECK_EQUAL(snapshot.processed, 0);
    BOOST_CHECK(envelope->Shutdown(2s));
}

BOOST_AUTO_TEST_CASE(join_cleanup_exception_is_contained) {
    auto hooks = std::make_shared<chronik::EventEnvelopeTestHooks>();
    hooks->before_join = [] {
        throw std::runtime_error("injected join cleanup failure");
    };
    auto envelope = chronik::EventEnvelope::Create(
        1, [](const chronik::OwnedEvent &) { return true; }, hooks);
    BOOST_REQUIRE(envelope);
    const std::array<uint8_t, 32> hash{};
    envelope->Submit(1, chronik::EventKind::BLOCK_CONNECTED, hash, 1, {});
    BOOST_CHECK(!envelope->Shutdown(2s));
    const auto snapshot = envelope->Snapshot();
    BOOST_CHECK(snapshot.worker_done);
    BOOST_CHECK(snapshot.degraded);
    BOOST_CHECK(snapshot.rebuild_required);
    BOOST_CHECK_EQUAL(snapshot.processed, 1);
}

BOOST_AUTO_TEST_CASE(disable_and_invalid_sequence_are_inert_and_degraded) {
    std::atomic<uint64_t> calls{0};
    auto envelope = chronik::EventEnvelope::Create(
        2, [&](const chronik::OwnedEvent &) {
            ++calls;
            return true;
        });
    BOOST_REQUIRE(envelope);
    const std::array<uint8_t, 32> hash{};
    envelope->MarkDegraded();
    envelope->Submit(1, chronik::EventKind::BLOCK_CONNECTED, hash, 1, {});
    BOOST_CHECK(envelope->Shutdown(2s));
    BOOST_CHECK_EQUAL(calls.load(), 0);
    BOOST_CHECK_EQUAL(envelope->Snapshot().accepted, 0);
    BOOST_CHECK(envelope->Snapshot().rebuild_required);

    auto invalid = chronik::EventEnvelope::Create(
        2, [&](const chronik::OwnedEvent &) {
            ++calls;
            return true;
        });
    BOOST_REQUIRE(invalid);
    invalid->Submit(0, chronik::EventKind::BLOCK_CONNECTED, hash, 0, {});
    BOOST_CHECK(invalid->Shutdown(2s));
    BOOST_CHECK(invalid->Snapshot().degraded);
    BOOST_CHECK_EQUAL(calls.load(), 0);
}

BOOST_AUTO_TEST_CASE(shutdown_is_bounded_when_consumer_stalls) {
    std::promise<void> entered_promise;
    auto entered = entered_promise.get_future();
    auto release_promise = std::make_shared<std::promise<void>>();
    auto release = release_promise->get_future().share();
    auto hooks = std::make_shared<chronik::EventEnvelopeTestHooks>();
    hooks->before_detach = [] {
        throw std::runtime_error("injected detach cleanup failure");
    };
    auto envelope = chronik::EventEnvelope::Create(
        1, [release, &entered_promise](const chronik::OwnedEvent &) {
            entered_promise.set_value();
            release.wait();
            return true;
        },
        hooks);
    BOOST_REQUIRE(envelope);
    const std::array<uint8_t, 32> hash{};
    envelope->Submit(1, chronik::EventKind::BLOCK_CONNECTED, hash, 1, {});
    BOOST_REQUIRE(entered.wait_for(2s) == std::future_status::ready);

    const auto start = std::chrono::steady_clock::now();
    BOOST_CHECK(!envelope->Shutdown(20ms));
    const auto elapsed = std::chrono::steady_clock::now() - start;
    BOOST_CHECK(elapsed < 500ms);
    BOOST_CHECK(envelope->Snapshot().degraded);
    BOOST_CHECK(envelope->Snapshot().rebuild_required);
    release_promise->set_value();
    BOOST_CHECK(WaitForWorkerDone(*envelope, 2s));
    const auto finished = envelope->Snapshot();
    std::this_thread::sleep_for(10ms);
    const auto stable = envelope->Snapshot();
    BOOST_CHECK(finished.worker_done);
    BOOST_CHECK(finished.degraded);
    BOOST_CHECK(finished.rebuild_required);
    BOOST_CHECK(stable.worker_done);
    BOOST_CHECK(stable.degraded);
    BOOST_CHECK(stable.rebuild_required);
    BOOST_CHECK_EQUAL(stable.processed, finished.processed);
}

BOOST_AUTO_TEST_SUITE_END()

// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#include <chronik/event-envelope.h>

#include <atomic>
#include <condition_variable>
#include <mutex>
#include <optional>
#include <utility>

namespace chronik {

namespace {
constexpr uint8_t ENVELOPE_DEGRADED = 1U << 0;
constexpr uint8_t ENVELOPE_REBUILD_REQUIRED = 1U << 1;
constexpr uint8_t ENVELOPE_FAILED =
    ENVELOPE_DEGRADED | ENVELOPE_REBUILD_REQUIRED;
} // namespace

struct EventEnvelope::SharedState {
    SharedState(size_t capacity_in, Consumer consumer_in,
                std::shared_ptr<const EventEnvelopeTestHooks> test_hooks_in)
        : capacity(capacity_in), consumer(std::move(consumer_in)),
          slots(capacity_in), test_hooks(std::move(test_hooks_in)) {}

    const size_t capacity;
    Consumer consumer;
    std::vector<std::optional<OwnedEvent>> slots;
    std::atomic<uint64_t> read_index{0};
    std::atomic<uint64_t> write_index{0};
    std::atomic_flag producer_busy = ATOMIC_FLAG_INIT;
    std::mutex wait_mutex;
    std::condition_variable condition;
    std::mutex lifecycle_mutex;
    std::condition_variable lifecycle_condition;
    std::atomic<uint64_t> accepted{0};
    std::atomic<uint64_t> processed{0};
    std::atomic<uint64_t> last_enqueued_sequence{0};
    std::atomic<uint64_t> last_processed_sequence{0};
    std::atomic<uint8_t> failure_flags{0};
    std::atomic<bool> stopping{false};
    std::atomic<bool> worker_ready{false};
    std::atomic<bool> worker_done{false};
    const std::shared_ptr<const EventEnvelopeTestHooks> test_hooks;
};

void EventEnvelope::MarkStateDegraded(
    const std::shared_ptr<SharedState> &state) noexcept {
    try {
        {
            std::lock_guard<std::mutex> lock(state->wait_mutex);
            state->failure_flags.store(ENVELOPE_FAILED,
                                       std::memory_order_release);
        }
    } catch (...) {
        state->failure_flags.store(ENVELOPE_FAILED,
                                   std::memory_order_release);
    }
    state->condition.notify_all();
}

void EventEnvelope::RunWorker(
    const std::shared_ptr<SharedState> &state) noexcept {
    try {
        uint64_t expected_sequence = 1;
        {
            std::lock_guard<std::mutex> lock(state->lifecycle_mutex);
            state->worker_ready.store(true, std::memory_order_release);
        }
        state->lifecycle_condition.notify_all();
        if (state->test_hooks && state->test_hooks->worker_entry) {
            state->test_hooks->worker_entry();
        }
        while (true) {
            if ((state->failure_flags.load(std::memory_order_acquire) &
                 ENVELOPE_DEGRADED) != 0) {
                break;
            }

            const uint64_t read_index =
                state->read_index.load(std::memory_order_relaxed);
            const uint64_t write_index =
                state->write_index.load(std::memory_order_acquire);
            if (read_index == write_index) {
                if (state->stopping.load(std::memory_order_acquire)) {
                    break;
                }
                std::unique_lock<std::mutex> lock(state->wait_mutex);
                const auto can_run = [&] {
                    return (state->failure_flags.load(
                                std::memory_order_acquire) &
                            ENVELOPE_DEGRADED) != 0 ||
                           state->stopping.load(std::memory_order_acquire) ||
                           state->read_index.load(std::memory_order_relaxed) !=
                               state->write_index.load(
                                   std::memory_order_acquire);
                };
                while (!can_run()) {
                    if (state->test_hooks &&
                        state->test_hooks->before_worker_wait) {
                        state->test_hooks->before_worker_wait();
                    }
                    state->condition.wait(lock);
                }
                continue;
            }

            auto &slot = state->slots[read_index % state->capacity];
            if (!slot) {
                EventEnvelope::MarkStateDegraded(state);
                break;
            }
            OwnedEvent event = std::move(*slot);
            slot.reset();
            state->read_index.store(read_index + 1,
                                    std::memory_order_release);

            if (event.sequence != expected_sequence) {
                EventEnvelope::MarkStateDegraded(state);
                break;
            }

            bool accepted = false;
            try {
                accepted = state->consumer(event);
            } catch (...) {
                accepted = false;
            }
            if (!accepted) {
                EventEnvelope::MarkStateDegraded(state);
                break;
            }

            state->last_processed_sequence.store(event.sequence,
                                                 std::memory_order_release);
            state->processed.fetch_add(1, std::memory_order_acq_rel);
            if (expected_sequence == UINT64_MAX) {
                EventEnvelope::MarkStateDegraded(state);
                break;
            }
            ++expected_sequence;
        }
    } catch (...) {
        EventEnvelope::MarkStateDegraded(state);
    }

    {
        std::lock_guard<std::mutex> lock(state->lifecycle_mutex);
        state->worker_done.store(true, std::memory_order_release);
    }
    state->lifecycle_condition.notify_all();
    try {
        if (state->test_hooks && state->test_hooks->worker_exit) {
            state->test_hooks->worker_exit();
        }
    } catch (...) {
        EventEnvelope::MarkStateDegraded(state);
    }
}

void EventEnvelope::ContainThread(
    std::unique_ptr<std::thread> &worker) noexcept {
    if (!worker) {
        return;
    }
    if (!worker->joinable()) {
        worker.reset();
        return;
    }
    try {
        worker->detach();
        worker.reset();
    } catch (...) {
        // A joinable std::thread destructor terminates the process. If the
        // platform refuses detachment, deliberately leak only the tiny thread
        // control object; the worker and its state remain self-owned.
        (void)worker.release();
    }
}

EventEnvelope::EventEnvelope(std::shared_ptr<SharedState> state,
                             std::unique_ptr<std::thread> worker) noexcept
    : m_state(std::move(state)), m_worker(std::move(worker)) {}

std::unique_ptr<EventEnvelope>
EventEnvelope::Create(
    size_t capacity, Consumer consumer,
    std::shared_ptr<const EventEnvelopeTestHooks> test_hooks) noexcept {
    if (capacity == 0 || !consumer) {
        return nullptr;
    }
    std::shared_ptr<SharedState> state;
    std::unique_ptr<std::thread> worker;
    try {
        state = std::make_shared<SharedState>(capacity, std::move(consumer),
                                              std::move(test_hooks));
        worker = std::make_unique<std::thread>(
            [state] { EventEnvelope::RunWorker(state); });
        if (state->test_hooks && state->test_hooks->after_worker_start) {
            state->test_hooks->after_worker_start();
        }
        {
            std::unique_lock<std::mutex> lock(state->lifecycle_mutex);
            if (!state->lifecycle_condition.wait_for(
                    lock, std::chrono::seconds(2), [&] {
                    return state->worker_ready.load(std::memory_order_acquire) ||
                           state->worker_done.load(std::memory_order_acquire);
                })) {
                lock.unlock();
                {
                    std::lock_guard<std::mutex> wait_lock(state->wait_mutex);
                    state->stopping.store(true, std::memory_order_release);
                    state->failure_flags.store(ENVELOPE_FAILED,
                                               std::memory_order_release);
                }
                state->condition.notify_all();
                EventEnvelope::ContainThread(worker);
                return nullptr;
            }
        }
        if (!state->worker_ready.load(std::memory_order_acquire)) {
            {
                std::lock_guard<std::mutex> lock(state->wait_mutex);
                state->stopping.store(true, std::memory_order_release);
                state->failure_flags.store(ENVELOPE_FAILED,
                                           std::memory_order_release);
            }
            state->condition.notify_all();
            EventEnvelope::ContainThread(worker);
            return nullptr;
        }
        return std::unique_ptr<EventEnvelope>(
            new EventEnvelope(std::move(state), std::move(worker)));
    } catch (...) {
        if (state) {
            try {
                {
                    std::lock_guard<std::mutex> lock(state->wait_mutex);
                    state->stopping.store(true, std::memory_order_release);
                    state->failure_flags.store(ENVELOPE_FAILED,
                                               std::memory_order_release);
                }
            } catch (...) {
                state->stopping.store(true, std::memory_order_release);
                state->failure_flags.store(ENVELOPE_FAILED,
                                           std::memory_order_release);
            }
            state->condition.notify_all();
        }
        EventEnvelope::ContainThread(worker);
        return nullptr;
    }
}

EventEnvelope::~EventEnvelope() {
    if (m_worker && m_worker->joinable()) {
        Shutdown(std::chrono::seconds(2));
    }
}

void EventEnvelope::Submit(uint64_t sequence, EventKind kind,
                           const std::array<uint8_t, 32> &hash,
                           int32_t height,
                           std::vector<uint8_t> payload) noexcept {
    const auto state = m_state;
    if (!state || sequence == 0 ||
        (state->failure_flags.load(std::memory_order_acquire) &
         ENVELOPE_DEGRADED) != 0 ||
        state->stopping.load(std::memory_order_acquire)) {
        if (state && sequence == 0) {
            EventEnvelope::MarkStateDegraded(state);
        }
        return;
    }

    if (state->producer_busy.test_and_set(std::memory_order_acquire)) {
        EventEnvelope::MarkStateDegraded(state);
        return;
    }

    try {
        if (state->test_hooks && state->test_hooks->producer_acquired) {
            state->test_hooks->producer_acquired();
        }
        if ((state->failure_flags.load(std::memory_order_acquire) &
             ENVELOPE_DEGRADED) != 0 ||
            state->stopping.load(std::memory_order_acquire)) {
            state->producer_busy.clear(std::memory_order_release);
            return;
        }

        const uint64_t write_index =
            state->write_index.load(std::memory_order_relaxed);
        const uint64_t read_index =
            state->read_index.load(std::memory_order_acquire);
        if (write_index == UINT64_MAX ||
            write_index - read_index >= state->capacity) {
            state->producer_busy.clear(std::memory_order_release);
            EventEnvelope::MarkStateDegraded(state);
            return;
        }

        auto &slot = state->slots[write_index % state->capacity];
        if (slot) {
            state->producer_busy.clear(std::memory_order_release);
            EventEnvelope::MarkStateDegraded(state);
            return;
        }
        slot.emplace(OwnedEvent{sequence, kind, hash, height,
                                std::move(payload)});
        {
            std::lock_guard<std::mutex> lock(state->wait_mutex);
            if ((state->failure_flags.load(std::memory_order_acquire) &
                 ENVELOPE_DEGRADED) != 0 ||
                state->stopping.load(std::memory_order_acquire)) {
                slot.reset();
                state->producer_busy.clear(std::memory_order_release);
                return;
            }
            state->write_index.store(write_index + 1,
                                     std::memory_order_release);
            state->last_enqueued_sequence.store(sequence,
                                                std::memory_order_release);
            state->accepted.fetch_add(1, std::memory_order_acq_rel);
        }
        state->producer_busy.clear(std::memory_order_release);
        state->condition.notify_one();
    } catch (...) {
        state->producer_busy.clear(std::memory_order_release);
        EventEnvelope::MarkStateDegraded(state);
    }
}

void EventEnvelope::MarkDegraded() noexcept {
    if (m_state) {
        EventEnvelope::MarkStateDegraded(m_state);
    }
}

EnvelopeSnapshot EventEnvelope::Snapshot() const noexcept {
    const auto state = m_state;
    if (!state) {
        return {};
    }
    const uint8_t failure_flags =
        state->failure_flags.load(std::memory_order_acquire);
    return {
        state->accepted.load(std::memory_order_acquire),
        state->processed.load(std::memory_order_acquire),
        state->last_enqueued_sequence.load(std::memory_order_acquire),
        state->last_processed_sequence.load(std::memory_order_acquire),
        (failure_flags & ENVELOPE_DEGRADED) != 0,
        (failure_flags & ENVELOPE_REBUILD_REQUIRED) != 0,
        state->worker_done.load(std::memory_order_acquire),
    };
}

bool EventEnvelope::Shutdown(std::chrono::milliseconds timeout) noexcept {
    const auto state = m_state;
    if (!state || !m_worker) {
        return true;
    }
    if (!m_worker->joinable()) {
        m_worker.reset();
        return true;
    }

    try {
        {
            std::lock_guard<std::mutex> lock(state->wait_mutex);
            state->stopping.store(true, std::memory_order_release);
        }
    } catch (...) {
        state->stopping.store(true, std::memory_order_release);
    }
    state->condition.notify_all();
    bool completed = false;
    try {
        std::unique_lock<std::mutex> lock(state->lifecycle_mutex);
        completed = state->lifecycle_condition.wait_for(lock, timeout, [&] {
            return state->worker_done.load(std::memory_order_acquire);
        });
    } catch (...) {
        completed = false;
    }

    if (completed) {
        try {
            if (state->test_hooks && state->test_hooks->before_join) {
                state->test_hooks->before_join();
            }
            m_worker->join();
            m_worker.reset();
        } catch (...) {
            completed = false;
            EventEnvelope::MarkStateDegraded(state);
            EventEnvelope::ContainThread(m_worker);
        }
    } else {
        EventEnvelope::MarkStateDegraded(state);
        try {
            if (state->test_hooks && state->test_hooks->before_detach) {
                state->test_hooks->before_detach();
            }
        } catch (...) {
            EventEnvelope::MarkStateDegraded(state);
        }
        EventEnvelope::ContainThread(m_worker);
    }
    return completed;
}

} // namespace chronik

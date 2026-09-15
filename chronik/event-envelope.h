// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#ifndef BITCOIN_CHRONIK_EVENT_ENVELOPE_H
#define BITCOIN_CHRONIK_EVENT_ENVELOPE_H

#include <array>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <thread>
#include <vector>

namespace chronik {

enum class EventKind : uint8_t {
    BLOCK_CONNECTED = 1,
    BLOCK_DISCONNECTED = 2,
};

/** Immutable, owned observation copied before it enters the worker queue. */
struct OwnedEvent {
    uint64_t sequence{0};
    EventKind kind{EventKind::BLOCK_CONNECTED};
    std::array<uint8_t, 32> hash{};
    int32_t height{-1};
    std::vector<uint8_t> payload;
};

struct EnvelopeSnapshot {
    uint64_t accepted{0};
    uint64_t processed{0};
    uint64_t last_enqueued_sequence{0};
    uint64_t last_processed_sequence{0};
    bool degraded{false};
    bool rebuild_required{false};
    bool worker_done{false};
};

/** Narrow deterministic failure/collision seams used only by direct tests. */
struct EventEnvelopeTestHooks {
    std::function<void()> after_worker_start;
    std::function<void()> worker_entry;
    std::function<void()> before_worker_wait;
    std::function<void()> producer_acquired;
    std::function<void()> before_join;
    std::function<void()> before_detach;
    std::function<void()> worker_exit;
};

/**
 * Bounded one-way event envelope.
 *
 * Submit never waits for the consumer and never returns a consumer result.
 * Concurrent producers, allocation failure, capacity overflow, a sequence
 * gap, or a consumer failure atomically degrade the envelope and require a
 * rebuild.
 */
class EventEnvelope final {
public:
    using Consumer = std::function<bool(const OwnedEvent &)>;

    static std::unique_ptr<EventEnvelope> Create(
        size_t capacity, Consumer consumer,
        std::shared_ptr<const EventEnvelopeTestHooks> test_hooks =
            nullptr) noexcept;

    EventEnvelope(const EventEnvelope &) = delete;
    EventEnvelope &operator=(const EventEnvelope &) = delete;
    ~EventEnvelope();

    void Submit(uint64_t sequence, EventKind kind,
                const std::array<uint8_t, 32> &hash, int32_t height,
                std::vector<uint8_t> payload) noexcept;

    void MarkDegraded() noexcept;
    EnvelopeSnapshot Snapshot() const noexcept;

    /** Drain within the deadline, otherwise detach the self-owned worker. */
    bool Shutdown(std::chrono::milliseconds timeout) noexcept;

private:
    struct SharedState;

    static void MarkStateDegraded(
        const std::shared_ptr<SharedState> &state) noexcept;
    static void RunWorker(
        const std::shared_ptr<SharedState> &state) noexcept;
    static void ContainThread(
        std::unique_ptr<std::thread> &worker) noexcept;

    EventEnvelope(std::shared_ptr<SharedState> state,
                  std::unique_ptr<std::thread> worker) noexcept;

    std::shared_ptr<SharedState> m_state;
    std::unique_ptr<std::thread> m_worker;
};

} // namespace chronik

#endif // BITCOIN_CHRONIK_EVENT_ENVELOPE_H

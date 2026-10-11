// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#ifndef BITCOIN_CHRONIK_QUERY_ADMISSION_H
#define BITCOIN_CHRONIK_QUERY_ADMISSION_H

#include <atomic>
#include <mutex>

namespace chronik {

/** Serialize host queries with the index worker, never consensus callbacks.
 * Query admission is nonblocking. Closing is irreversible and in-flight
 * queries must recheck IsOpen at their response linearization boundary.
 */
class QueryAdmission final {
public:
    std::unique_lock<std::mutex> TryQuery() {
        std::unique_lock<std::mutex> lock(m_mutex, std::try_to_lock);
        if (!lock.owns_lock() || !IsOpen()) {
            return {};
        }
        return lock;
    }

    std::unique_lock<std::mutex> Mutation() {
        return std::unique_lock<std::mutex>(m_mutex);
    }

    bool IsOpen() const noexcept {
        return !m_closed.load(std::memory_order_acquire);
    }

    void Close() noexcept {
        m_closed.store(true, std::memory_order_release);
    }

private:
    std::mutex m_mutex;
    std::atomic<bool> m_closed{false};
};

} // namespace chronik
#endif // BITCOIN_CHRONIK_QUERY_ADMISSION_H

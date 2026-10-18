// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#ifndef ERGON_CHRONIK_CPP_ERGON_NODE_ACCESS_H
#define ERGON_CHRONIK_CPP_ERGON_NODE_ACCESS_H

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <limits>

class Config;
class CTxMemPool;

namespace chronik_bridge {
enum class NativeReadScope { BoundedFixture, ArchivalRegtestService };

/**
 * Small Ergon host binding, not an ABC NodeContext or consensus owner.
 *
 * Construct only after the native node resources exist. The service owner
 * must close admission and join all bridge users before destroying this
 * object or the referenced native resources. Chain and mempool access still
 * require their native locks; const references do not grant safe snapshots.
 *
 * The default scope retains historical fixture limits. Only the explicitly
 * opted-in, joined regtest service owner selects the archival history scope.
 * This scope does not grant network admission or native validation authority.
 */
struct ErgonNodeAccess {
    const Config &config;
    CTxMemPool &mempool;
    mutable std::atomic<bool> index_failed{false};

  private:
    const NativeReadScope m_scope;

  public:

    ErgonNodeAccess(const Config &config_in, CTxMemPool &mempool_in,
                   NativeReadScope scope = NativeReadScope::BoundedFixture)
        : config(config_in), mempool(mempool_in), m_scope(scope) {}

    bool permits_height(int height) const noexcept {
        // Retained-window arithmetic uses height + 1 in both C++ and Rust.
        return height >= 0 && height < std::numeric_limits<int32_t>::max() &&
            (m_scope == NativeReadScope::ArchivalRegtestService || height <= 2000);
    }
    bool permits_index_count(size_t count) const noexcept {
        return m_scope == NativeReadScope::ArchivalRegtestService || count <= 4096;
    }

    ErgonNodeAccess(const ErgonNodeAccess &) = delete;
    ErgonNodeAccess &operator=(const ErgonNodeAccess &) = delete;
};

// Build/link integration is not runtime admission. This gate stays closed
// until ordered reconciliation, request admission and joined shutdown have
// passed their runtime tests. There is deliberately no command-line override.
inline bool upstream_runtime_permitted(const ErgonNodeAccess &) noexcept {
    return false;
}

} // namespace chronik_bridge

#endif // ERGON_CHRONIK_CPP_ERGON_NODE_ACCESS_H

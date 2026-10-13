// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#ifndef ERGON_CHRONIK_CPP_ERGON_NODE_ACCESS_H
#define ERGON_CHRONIK_CPP_ERGON_NODE_ACCESS_H

#include <atomic>

class Config;
class CTxMemPool;

namespace chronik_bridge {

/**
 * Small Ergon host binding, not an ABC NodeContext or consensus owner.
 *
 * Construct only after the native node resources exist. The service owner
 * must close admission and join all bridge users before destroying this
 * object or the referenced native resources. Chain and mempool access still
 * require their native locks; const references do not grant safe snapshots.
 *
 * This link profile never instantiates the context in the daemon or starts a
 * service. Native unit tests may construct it around their owned fixtures.
 */
struct ErgonNodeAccess {
    const Config &config;
    CTxMemPool &mempool;
    mutable std::atomic<bool> index_failed{false};

    ErgonNodeAccess(const Config &config_in, CTxMemPool &mempool_in)
        : config(config_in), mempool(mempool_in) {}

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

// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#ifndef ERGON_ARCHIVAL_PROBE_H
#define ERGON_ARCHIVAL_PROBE_H
#include <chronik-cpp/ergon_node_access.h>
#include <cstdint>
#include <functional>
#include <memory>
namespace chronik_bridge {
class ChronikBridge;
}
namespace ergon_archival {
struct ArchivalProbe;
// Synchronous native test hook only: no daemon/config/HTTP exposure.
class StartupHook {
    const std::function<void()> callback;

  public:
    explicit StartupHook(std::function<void()> hook)
        : callback(std::move(hook)) {}
    void run() const;
};
// Fixture diagnostics only. Counts are summed across at most four attempts;
// undo_load_calls includes the empty genesis undo, not necessarily a disk read.
struct CaptureStats {
    uint64_t attempts{0}, reused_blocks{0}, block_read_calls{0},
        undo_load_calls{0}, discarded_before_db{0}, metadata_copied{0},
        metadata_checked{0}, pool_reused{0}, pool_captured{0}, prevout_reads{0},
        cold_prevout_reads{0};
};
// Native fixture or separately opted-in bounded regtest owner must outlive the
// synchronous Rust box. Not general runtime admission or live-source validation.
void archival_fixture_guard(const chronik_bridge::ErgonNodeAccess &node);
// Fixture-only: hold both native locks throughout synchronous reconciliation.
// This intentionally blocks mutation and is NOT a production service design.
void reconcile_archival(ArchivalProbe &probe,
                        const chronik_bridge::ErgonNodeAccess &node);
void archival_reconcile_guard(const chronik_bridge::ErgonNodeAccess &node);
void archival_database_guard(const chronik_bridge::ErgonNodeAccess &node);
std::unique_ptr<chronik_bridge::ChronikBridge>
make_startup_bridge(const chronik_bridge::ErgonNodeAccess &node);
std::unique_ptr<chronik_bridge::ChronikBridge>
make_service_startup_bridge(const chronik_bridge::ErgonNodeAccess &node);
void finish_startup_bridge(const chronik_bridge::ChronikBridge &bridge);
bool finish_service_startup_bridge(const chronik_bridge::ChronikBridge &bridge);
bool service_startup_cancelled();
bool capture_service_startup(ArchivalProbe &probe,
                            const chronik_bridge::ChronikBridge &bridge,
                            const chronik_bridge::ErgonNodeAccess &node);
bool reconcile_startup(ArchivalProbe &probe,
                       const chronik_bridge::ErgonNodeAccess &node);
// Copy native metadata, mempool parent outputs and the cache overlay under the
// source fence. Resolve cold prevouts through an owned native DB snapshot and
// read block/undo after release; recheck source, storage AND cache/DB owners
// before DB application, then again before publication. No borrowed native coin
// view crosses the fence. Fixture hooks are not daemon capabilities.
bool reconcile_captured(ArchivalProbe &probe,
                        const chronik_bridge::ErgonNodeAccess &node,
                        const std::function<void()> &after_capture = {},
                        const std::function<void()> &before_disk = {},
                        CaptureStats *stats = nullptr);
uint64_t archival_mempool_revision(const chronik_bridge::ErgonNodeAccess &node);
uint64_t archival_chain_generation(const chronik_bridge::ErgonNodeAccess &node);
} // namespace ergon_archival
#endif

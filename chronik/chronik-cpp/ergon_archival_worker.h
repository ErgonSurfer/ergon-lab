// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#ifndef ERGON_ARCHIVAL_WORKER_H
#define ERGON_ARCHIVAL_WORKER_H
#include <array>
#include <chronik-cpp/ergon_node_access.h>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <memory>
#include <rust/cxx.h>
#include <string>
namespace ergon_archival {
struct Snapshot;
struct TxView;
struct RequestGate;
struct ResponseLatch;
struct PluginFixture;
enum class WorkerProfile { Inherited, HistoricalReadOnly, HistoricalPluginsReadOnly, HistoricalClientService };
// Stable bounded diagnostics only: never expose an exception containing native
// paths, data, plugin text or an operator identity.
enum class WorkerFailure {
    None,
    NotificationHook,
    CallbackCapacity,
    SequenceExhausted,
    TicketAllocation,
    Reconcile,
};
const char *WorkerFailureName(WorkerFailure failure) noexcept;
struct WorkerStatus {
    uint64_t accepted, completed, discarded, rejected;
    size_t pending;
    bool ready, failed, admission_closed, joined;
    WorkerFailure failure;
    size_t failure_pending;
    size_t queued_tickets;
};
// Owned bounded callback reactor over the real upstream indexer. Ordinary
// profiles have no daemon entry point; the separate regtest owner is opt-in.
// Optional ephemeral HTTP reuses upstream handlers;
// strict fixture notifications are
// ordered invalidation tickets, NOT historical transaction payloads. The
// worker captures owned native bytes and applies upstream DB writers outside
// native locks. The full-chain bounded capture is not production performance.
// Idle scalar polling notices silent native generation changes; exhausted
// capture retries remain unavailable and retry after the queued callback fence.
// The continuous regtest service anchors its native payload journal atomically
// at initial snapshot publication, before exposing a listener. Later callbacks
// are constant-storage wakeups only. The single owned journal consumer applies
// every later event through upstream writers; coalesced reconciliation cannot
// run after the cut. Initial import transients precede the public subscription
// boundary and are represented by the coherent initial snapshot, not a claimed
// historical notification replay.
// Strict component fixtures retain their original bounded ticket queue.
class ArchivalWorker {
    struct Impl;
    std::unique_ptr<Impl> m_impl;

  public:
    // Hooks are deterministic fixture latches, never daemon hooks.
    ArchivalWorker(const std::string &root,
                   const chronik_bridge::ErgonNodeAccess &node,
                   size_t max_pending = 128,
                   std::function<void()> before_reconcile = {},
                   std::function<void()> before_notification = {},
                   std::function<void()> after_capture = {},
                   std::function<void()> before_disk = {},
                   const PluginFixture *plugins = nullptr,
                   bool regtest_service = false,
                   WorkerProfile profile = WorkerProfile::Inherited);
    ~ArchivalWorker();
    ArchivalWorker(const ArchivalWorker &) = delete;
    ArchivalWorker &operator=(const ArchivalWorker &) = delete;
    WorkerStatus Status() const;
    bool WaitThrough(uint64_t ticket, int milliseconds);
    Snapshot ReadSnapshot();
    TxView ReadTx(const std::array<uint8_t, 32> &id, size_t output);
    uint16_t StartHttpFixture();
    // Service-only: 0 means a concurrent capture owns the data mutex. Read
    // publication remains governed by the original source/epoch request gate.
    uint16_t TryStartRegtestHttp();
    uint16_t TryStartHistoricalHttp();
    uint16_t TryStartHistoricalClientsHttp(uint16_t port);
    // Shared metadata-only controls: no data/native/DB lock acquisition. Safe
    // to retrieve while holding native locks or while busy/closed; a handle
    // does not itself grant readiness, retain native access, or reopen Stop.
    rust::Box<RequestGate> RequestAdmission();
    rust::Box<ResponseLatch> ResponseBarrier();
    // Owner must call outside native locks and outlive this call. Serialized,
    // idempotent: close admission, unregister, drain native callbacks, discard
    // pending tickets, join current work, destroy indexer, release DB lease.
    // No detach or pretend timeout: a stalled storage call remains a stop gate.
    void Stop();
};
} // namespace ergon_archival
#endif

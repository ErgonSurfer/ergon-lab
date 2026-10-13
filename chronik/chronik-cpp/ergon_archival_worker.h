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
struct WorkerStatus {
    uint64_t accepted, completed, discarded, rejected;
    size_t pending;
    bool ready, failed, admission_closed, joined;
};
// Test-only owned callback reactor over the real upstream indexer. No daemon
// entry point. Optional ephemeral HTTP fixture reuses upstream handlers;
// websocket notification semantics are not enabled. Notifications are
// ordered invalidation tickets, NOT historical transaction payloads. The
// worker captures owned native bytes and applies upstream DB writers outside
// native locks. The full-chain bounded capture is not production performance.
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
                   std::function<void()> before_disk = {});
    ~ArchivalWorker();
    ArchivalWorker(const ArchivalWorker &) = delete;
    ArchivalWorker &operator=(const ArchivalWorker &) = delete;
    WorkerStatus Status() const;
    bool WaitThrough(uint64_t ticket, int milliseconds);
    Snapshot ReadSnapshot();
    TxView ReadTx(const std::array<uint8_t, 32> &id, size_t output);
    uint16_t StartHttpFixture();
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

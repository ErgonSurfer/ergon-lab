// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#include <chronik-cpp/ergon_archival_probe.h>
#include <chronik-cpp/ergon_archival_worker.h>
#include <chronik_lib/src/ergon_archival.rs.h>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <txmempool.h>
#include <validation.h>
#include <validationinterface.h>

namespace ergon_archival {
struct ArchivalWorker::Impl final : CValidationInterface {
    const chronik_bridge::ErgonNodeAccess &node;
    // No query borrows escape; data_mu serializes every access to the Rust box.
    std::mutex data_mu, stop_mu;
    mutable std::mutex mu;
    std::condition_variable cv;
    std::deque<uint64_t> tickets;
    size_t capacity, pending{0}; // Includes the in-flight ticket.
    uint64_t accepted{0}, completed{0}, discarded{0}, rejected{0};
    bool ready{false}, failed{false}, closed{false}, stopping{false},
        joined{false}, registered{false};
    std::function<void()> before, before_notification, after_capture,
        before_disk;
    std::unique_ptr<rust::Box<ArchivalProbe>> probe;
    std::unique_ptr<rust::Box<RequestGate>> gate;
    // Immutable control handles share metadata only, never the indexer/node.
    std::unique_ptr<rust::Box<ResponseLatch>> response_latch;
    std::thread worker;

    Impl(const std::string &root, const chronik_bridge::ErgonNodeAccess &access,
         size_t limit, std::function<void()> hook,
         std::function<void()> notification_hook,
         std::function<void()> capture_hook, std::function<void()> disk_hook)
        : node(access), capacity(limit), before(std::move(hook)),
          before_notification(std::move(notification_hook)),
          after_capture(std::move(capture_hook)),
          before_disk(std::move(disk_hook)) {
        if (!capacity || capacity > 128)
            throw std::runtime_error("Invalid fixture callback budget");
        // Drain old fixture events before subscribing, never under cs_main.
        SyncWithValidationInterfaceQueue();
        probe = std::make_unique<rust::Box<ArchivalProbe>>(
            open_archival(root, node));
        gate =
            std::make_unique<rust::Box<RequestGate>>((*probe)->request_gate());
        response_latch = std::make_unique<rust::Box<ResponseLatch>>(
            (*probe)->response_latch());
        if (!reconcile_captured(**probe, node))
            throw std::runtime_error("Native source busy at worker startup");
        // Register without a native source mutation gap. The fixture owner
        // must not concurrently destroy node access.
        try {
            LOCK2(cs_main, node.mempool.cs);
            // No snapshot/DB query under native locks during registration.
            archival_reconcile_guard(node);
            (*probe)->assert_current_capture(node);
            ready = true;
            // Queue tickets until thread creation succeeds. On failure, drain
            // the subscriber outside native locks before unwinding.
            registered = true;
            RegisterValidationInterface(this);
            worker = std::thread([this] { Run(); });
        } catch (...) {
            if (registered) {
                UnregisterValidationInterface(this);
                SyncWithValidationInterfaceQueue();
            }
            throw;
        }
    }
    void Push() {
        // Metadata-only invalidation; no DB/data mutex, no native borrow.
        (*gate)->invalidate();
        try {
            if (before_notification)
                before_notification();
        } catch (...) {
            std::lock_guard<std::mutex> lock(mu);
            failed = true;
            (*gate)->fail();
            ready = false;
            node.index_failed.store(true);
            cv.notify_all();
            return;
        }
        std::lock_guard<std::mutex> lock(mu);
        if (closed || failed) {
            ++rejected;
            return;
        }
        ready = false;
        if (pending == capacity || accepted == UINT64_MAX) {
            failed = true;
            (*gate)->fail();
            node.index_failed.store(true);
        } else {
            try {
                tickets.push_back(accepted + 1);
                ++accepted;
                ++pending;
            } catch (...) {
                // Allocation failure must not escape into native validation.
                failed = true;
                (*gate)->fail();
                node.index_failed.store(true);
            }
        }
        cv.notify_all();
    }
    void TransactionAddedToMempool(const CTransactionRef &) override { Push(); }
    void TransactionRemovedFromMempool(const CTransactionRef &) override {
        Push();
    }
    void BlockConnected(const std::shared_ptr<const CBlock> &,
                        const CBlockIndex *,
                        const std::vector<CTransactionRef> &) override {
        Push();
    }
    void BlockDisconnected(const std::shared_ptr<const CBlock> &) override {
        Push();
    }
    void Run() noexcept {
        try {
            bool retry = false;
            for (;;) {
                uint64_t ticket =
                    0; // Zero denotes observation, not a callback.
                {
                    std::unique_lock<std::mutex> lock(mu);
                    cv.wait_for(lock, std::chrono::milliseconds(100), [&] {
                        return stopping || closed || failed || !tickets.empty();
                    });
                    if (stopping || closed || failed)
                        break;
                    if (!tickets.empty()) {
                        ticket = tickets.front();
                        tickets.pop_front();
                    }
                }
                if (!ticket) {
                    {
                        std::lock_guard<std::mutex> data(data_mu);
                        // A quiet poll neither waits for native owners nor
                        // traverses the chain/pool or touches disk/database.
                        TRY_LOCK(cs_main, chain_lock);
                        if (!chain_lock)
                            continue;
                        TRY_LOCK(node.mempool.cs, pool_lock);
                        if (!pool_lock)
                            continue;
                        if (!retry && (*probe)->source_current())
                            continue;
                    }
                    // Preserve queued native notification order before a
                    // silent refresh. This fence owns no data/native mutex.
                    // Like Stop's drain, it is not a production latency bound.
                    SyncWithValidationInterfaceQueue();
                    std::lock_guard<std::mutex> lock(mu);
                    if (stopping || closed || failed)
                        break;
                    if (!tickets.empty()) {
                        ticket = tickets.front();
                        tickets.pop_front();
                    }
                    ready = false;
                    (*gate)->invalidate();
                }
                if (ticket && ticket != completed + 1)
                    throw std::runtime_error("Callback ticket order gap");
                if (before)
                    before();
                bool published;
                {
                    std::lock_guard<std::mutex> data(data_mu);
                    published = reconcile_captured(**probe, node, after_capture,
                                                   before_disk);
                }
                retry = !published; // A quiet healthy source can recover later.
                {
                    std::lock_guard<std::mutex> lock(mu);
                    if (ticket) {
                        completed = ticket;
                        --pending;
                    }
                    ready = published && !closed && !failed && pending == 0;
                    cv.notify_all();
                }
            }
        } catch (...) {
            std::lock_guard<std::mutex> lock(mu);
            failed = true;
            (*gate)->fail();
            node.index_failed.store(true);
            cv.notify_all();
        }
        // The worker owns destruction as well as mutation. No Rust reference
        // or DB handle can survive a successful join.
        std::lock_guard<std::mutex> data(data_mu);
        {
            std::lock_guard<std::mutex> lock(mu);
            ready = false;
            discarded = accepted - completed;
            pending = 0;
            tickets.clear();
            if (failed)
                (*probe)->fail_reconcile();
        }
        probe.reset();
        cv.notify_all();
    }
    void Stop() {
        AssertLockNotHeld(cs_main);
        AssertLockNotHeld(node.mempool.cs);
        std::lock_guard<std::mutex> stop(stop_mu);
        (*gate)->close();
        {
            std::lock_guard<std::mutex> lock(mu);
            if (joined)
                return;
            closed = true;
            ready = false;
            cv.notify_all();
        }
        if (registered) {
            UnregisterValidationInterface(this);
            registered = false;
            // Unregister alone is not a queued-callback lifetime barrier.
            SyncWithValidationInterfaceQueue();
        }
        {
            std::lock_guard<std::mutex> lock(mu);
            stopping = true;
            cv.notify_all();
        }
        worker.join();
        std::lock_guard<std::mutex> lock(mu);
        joined = true;
        cv.notify_all();
    }
    void QueryGuard() {
        std::lock_guard<std::mutex> lock(mu);
        if (!ready || closed || failed || !probe)
            throw std::runtime_error("Callback index view unavailable");
    }
};
ArchivalWorker::ArchivalWorker(const std::string &root,
                               const chronik_bridge::ErgonNodeAccess &node,
                               size_t max_pending,
                               std::function<void()> before_reconcile,
                               std::function<void()> before_notification,
                               std::function<void()> after_capture,
                               std::function<void()> before_disk)
    : m_impl(std::make_unique<Impl>(
          root, node, max_pending, std::move(before_reconcile),
          std::move(before_notification), std::move(after_capture),
          std::move(before_disk))) {}
ArchivalWorker::~ArchivalWorker() { Stop(); }
WorkerStatus ArchivalWorker::Status() const {
    std::lock_guard<std::mutex> lock(m_impl->mu);
    return {m_impl->accepted, m_impl->completed, m_impl->discarded,
            m_impl->rejected, m_impl->pending,   m_impl->ready,
            m_impl->failed,   m_impl->closed,    m_impl->joined};
}
bool ArchivalWorker::WaitThrough(uint64_t ticket, int milliseconds) {
    AssertLockNotHeld(cs_main);
    AssertLockNotHeld(m_impl->node.mempool.cs);
    if (milliseconds < 0 || milliseconds > 10000)
        throw std::runtime_error("Invalid fixture wait budget");
    std::unique_lock<std::mutex> lock(m_impl->mu);
    m_impl->cv.wait_for(lock, std::chrono::milliseconds(milliseconds), [&] {
        return m_impl->failed || m_impl->closed ||
               (m_impl->completed >= ticket && m_impl->ready);
    });
    return !m_impl->failed && !m_impl->closed && m_impl->ready &&
           m_impl->completed >= ticket;
}
Snapshot ArchivalWorker::ReadSnapshot() {
    AssertLockNotHeld(cs_main);
    AssertLockNotHeld(m_impl->node.mempool.cs);
    std::unique_lock<std::mutex> data(m_impl->data_mu, std::try_to_lock);
    if (!data.owns_lock())
        throw std::runtime_error("Callback index busy");
    m_impl->QueryGuard();
    return (*m_impl->probe)->snapshot();
}
TxView ArchivalWorker::ReadTx(const std::array<uint8_t, 32> &id,
                              size_t output) {
    AssertLockNotHeld(cs_main);
    AssertLockNotHeld(m_impl->node.mempool.cs);
    std::unique_lock<std::mutex> data(m_impl->data_mu, std::try_to_lock);
    if (!data.owns_lock())
        throw std::runtime_error("Callback index busy");
    m_impl->QueryGuard();
    return (*m_impl->probe)->tx_view(id, output);
}
void ArchivalWorker::Stop() { m_impl->Stop(); }
uint16_t ArchivalWorker::StartHttpFixture() {
    AssertLockNotHeld(cs_main);
    AssertLockNotHeld(m_impl->node.mempool.cs);
    std::unique_lock<std::mutex> data(m_impl->data_mu, std::try_to_lock);
    if (!data.owns_lock())
        throw std::runtime_error("Callback index busy");
    m_impl->QueryGuard();
    return (*m_impl->probe)->start_http_fixture();
}
rust::Box<RequestGate> ArchivalWorker::RequestAdmission() {
    return (*m_impl->gate)->clone_handle();
}
rust::Box<ResponseLatch> ArchivalWorker::ResponseBarrier() {
    return (*m_impl->response_latch)->clone_handle();
}
} // namespace ergon_archival

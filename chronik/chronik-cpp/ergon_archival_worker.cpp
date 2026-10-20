// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#include <chronik-cpp/ergon_archival_probe.h>
#include <chronik-cpp/ergon_archival_worker.h>
#include <chronik-cpp/ergon_journal_replay.h>
#include <chronik_lib/src/ergon_archival.rs.h>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <shutdown.h>
#include <txmempool.h>
#include <validation.h>
#include <validationinterface.h>

namespace ergon_archival {
const char *WorkerFailureName(WorkerFailure failure) noexcept {
    switch (failure) {
    case WorkerFailure::None: return "none";
    case WorkerFailure::NotificationHook: return "notification-hook";
    case WorkerFailure::CallbackCapacity: return "callback-capacity";
    case WorkerFailure::SequenceExhausted: return "sequence-exhausted";
    case WorkerFailure::TicketAllocation: return "ticket-allocation";
    case WorkerFailure::Reconcile: return "reconcile";
    }
    return "unknown";
}
struct ArchivalWorker::Impl final : CValidationInterface {
    const chronik_bridge::ErgonNodeAccess &node;
    const bool service_mode;
    const WorkerProfile profile;
    // No query borrows escape; data_mu serializes every access to the Rust box.
    std::mutex data_mu, stop_mu;
    mutable std::mutex mu;
    std::condition_variable cv;
    std::deque<uint64_t> tickets;
    size_t capacity, pending{0}; // Includes the in-flight ticket.
    uint64_t accepted{0}, completed{0}, discarded{0}, rejected{0};
    WorkerFailure failure{WorkerFailure::None};
    size_t failure_pending{0};
    bool ready{false}, failed{false}, closed{false}, stopping{false},
        joined{false}, registered{false};
    std::function<void()> before, before_notification, after_capture,
        before_disk;
    std::unique_ptr<rust::Box<ArchivalProbe>> probe;
    // Created atomically at a current publication fence, before any listener.
    // After this cut callbacks are wakeups only; never reconcile snapshots.
    std::unique_ptr<JournalReplay> replay;
    std::unique_ptr<rust::Box<RequestGate>> gate;
    // Immutable control handles share metadata only, never the indexer/node.
    std::unique_ptr<rust::Box<ResponseLatch>> response_latch;
    std::thread worker;

    Impl(const std::string &root, const chronik_bridge::ErgonNodeAccess &access,
         size_t limit, std::function<void()> hook,
         std::function<void()> notification_hook,
         std::function<void()> capture_hook, std::function<void()> disk_hook,
         const PluginFixture *plugins, bool regtest_service, WorkerProfile selected_profile)
        : node(access), service_mode(regtest_service), profile(selected_profile), capacity(limit), before(std::move(hook)),
          before_notification(std::move(notification_hook)),
          after_capture(std::move(capture_hook)),
          before_disk(std::move(disk_hook)) {
        if (!capacity || capacity > 128)
            throw std::runtime_error("Invalid fixture callback budget");
        const bool historical = profile == WorkerProfile::HistoricalReadOnly ||
                                profile == WorkerProfile::HistoricalPluginsReadOnly;
        const bool clients = profile == WorkerProfile::HistoricalClientService;
        if(profile != WorkerProfile::Inherited && !historical && !clients)
            throw std::runtime_error("Unknown worker profile");
        if(historical != node.historical_readonly() ||
           clients != node.historical_clients() ||
           ((historical || clients) && !service_mode) ||
           (profile == WorkerProfile::HistoricalReadOnly && plugins) ||
           (profile == WorkerProfile::HistoricalPluginsReadOnly && !plugins))
            throw std::runtime_error("Historical worker scope/plugin profile mismatch");
        // Drain old fixture events before subscribing, never under cs_main.
        SyncWithValidationInterfaceQueue();
        probe = std::make_unique<rust::Box<ArchivalProbe>>(
            clients ? (plugins ? open_historical_clients_with_plugins(root,node,*plugins)
                               : open_historical_clients(root,node)) :
            profile==WorkerProfile::HistoricalPluginsReadOnly ? open_historical_with_plugins(root,node,*plugins) :
            profile==WorkerProfile::HistoricalReadOnly ? open_historical_readonly(root,node) :
            regtest_service ? (plugins ? open_regtest_service_with_plugins(root, node, *plugins)
                                      : open_regtest_service(root, node))
                : plugins ? open_archival_with_plugins(root, node, *plugins)
                          : open_archival(root, node));
        gate =
            std::make_unique<rust::Box<RequestGate>>((*probe)->request_gate());
        response_latch = std::make_unique<rust::Box<ResponseLatch>>(
            (*probe)->response_latch());
        const bool published = service_mode ? AnchorIfCurrent() : reconcile_captured(**probe, node);
        if (!published && !service_mode)
            throw std::runtime_error("Native source busy at worker startup");
        // Register without a native source mutation gap. The fixture owner
        // must not concurrently destroy node access.
        try {
            LOCK2(cs_main, node.mempool.cs);
            // No snapshot/DB query under native locks during registration.
            archival_reconcile_guard(node);
            if (ShutdownRequested())
                throw std::runtime_error("Callback startup cancelled");
            if (!service_mode)
                (*probe)->assert_current_capture(node);
            ready = !service_mode && published && (*probe)->source_current();
            if (!ready)
                (*gate)->invalidate();
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
    bool AnchorIfCurrent() {
        const bool current=reconcile_captured_and_anchor(**probe,node,after_capture,before_disk,nullptr,[&] {
            if(replay) throw std::runtime_error("Journal already owned by worker");
            replay=JournalReplay::AnchorUnderNativeFence(**probe,node,capacity);
        });
        // Anchoring deliberately invalidates the snapshot gate. The worker
        // must drain/publish its journal frontier before serving any request.
        return current;
    }
    // Caller owns mu. Preserve the first cause, including the occupancy before
    // the worker drains/discards pending tickets during destruction.
    void Fail(WorkerFailure reason) {
        if (!failed) {
            failure = reason;
            failure_pending = pending;
        }
        failed = true;
        ready = false;
        (*gate)->fail();
        node.index_failed.store(true);
    }
    // All frontier/queue access owns mu. Service notifications are invalidation
    // ordinals, not transaction/block payloads. One constant-storage interval
    // (completed, accepted] represents every unacknowledged notification.
    bool HasTicket() const {
        return service_mode ? accepted != completed : !tickets.empty();
    }
    uint64_t TakeTicket() {
        if (service_mode)
            return accepted;
        const auto ticket = tickets.front();
        tickets.pop_front();
        return ticket;
    }
    void Push() {
        // Metadata-only invalidation; no DB/data mutex, no native borrow.
        (*gate)->invalidate();
        try {
            if (before_notification)
                before_notification();
        } catch (...) {
            std::lock_guard<std::mutex> lock(mu);
            Fail(WorkerFailure::NotificationHook);
            cv.notify_all();
            return;
        }
        std::lock_guard<std::mutex> lock(mu);
        if (closed || failed) {
            ++rejected;
            return;
        }
        ready = false;
        if (service_mode && (accepted == UINT64_MAX || pending == SIZE_MAX)) {
            Fail(WorkerFailure::SequenceExhausted);
        } else if (service_mode) {
            // Payload continuity is governed by the native journal, not the
            // number of asynchronous wakeups awaiting acknowledgement.
            // No queue growth, DB access or native-source wait. A later capture
            // can acknowledge only a monotonic prefix, and publication still
            // refuses native progress, shutdown, storage drift or a stale epoch.
            ++accepted;
            ++pending;
        } else if (pending == capacity) {
            Fail(WorkerFailure::CallbackCapacity);
        } else if (accepted == UINT64_MAX) {
            Fail(WorkerFailure::SequenceExhausted);
        } else {
            try {
                tickets.push_back(accepted + 1);
                ++accepted;
                ++pending;
            } catch (...) {
                // Allocation failure must not escape into native validation.
                Fail(WorkerFailure::TicketAllocation);
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
            bool retry = service_mode;
            for (;;) {
                if (ShutdownRequested()) {
                    (*gate)->close();
                    std::lock_guard<std::mutex> lock(mu);
                    closed = true;
                    ready = false;
                    break;
                }
                uint64_t ticket =
                    0; // Zero denotes observation, not a callback.
                {
                    std::unique_lock<std::mutex> lock(mu);
                    cv.wait_for(lock, std::chrono::milliseconds(100), [&] {
                        return stopping || closed || failed || HasTicket();
                    });
                    if (stopping || closed || failed)
                        break;
                    if (HasTicket())
                        ticket = TakeTicket();
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
                        const auto journal=replay ? replay->Status() : ValidationEventJournal::Status{};
                        if (!retry && (*probe)->source_current() &&
                            (!service_mode || (replay && !journal.closed &&
                             journal.failure==ValidationEventJournal::Failure::None &&
                             journal.queued==0)))
                            continue;
                    }
                    // Preserve queued native notification order before a
                    // silent refresh. This fence owns no data/native mutex.
                    // Like Stop's drain, it is not a production latency bound.
                    if(!service_mode) SyncWithValidationInterfaceQueue();
                    std::lock_guard<std::mutex> lock(mu);
                    if (stopping || closed || failed)
                        break;
                    if (HasTicket())
                        ticket = TakeTicket();
                    ready = false;
                    (*gate)->invalidate();
                }
                if (ticket && (service_mode ? ticket <= completed
                                            : ticket != completed + 1))
                    throw std::runtime_error("Callback ticket order gap");
                if (before)
                    before();
                bool published;
                {
                    std::lock_guard<std::mutex> data(data_mu);
                    if(service_mode) {
                        if(!replay) AnchorIfCurrent();
                        if(replay) {
                            replay->Drain();
                            published=replay->Publish();
                        } else published=false;
                    } else published = reconcile_captured(**probe, node, after_capture,
                                                         before_disk);
                }
                retry = !published; // A quiet healthy source can recover later.
                {
                    std::lock_guard<std::mutex> lock(mu);
                    if (ticket) {
                        const auto acknowledged = ticket - completed;
                        if (!acknowledged || acknowledged > pending)
                            throw std::runtime_error("Callback frontier accounting gap");
                        completed = ticket;
                        pending -= static_cast<size_t>(acknowledged);
                    }
                    ready = published && !closed && !failed && pending == 0;
                    cv.notify_all();
                }
            }
        } catch (const JournalReplayCancelled &) {
            // No database/continuity exception is caught here. Replay throws
            // this distinct type only for native shutdown at a healthy fence.
            (*gate)->close();
            std::lock_guard<std::mutex> lock(mu);
            closed = true;
            ready = false;
            cv.notify_all();
        } catch (...) {
            std::lock_guard<std::mutex> lock(mu);
            Fail(WorkerFailure::Reconcile);
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
        // Detach the journal while native owners still exist, then destroy
        // its payloads and HTTP/index users outside native validation locks.
        if(replay) { replay->Stop();replay.reset(); }
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
                               std::function<void()> before_disk,
                               const PluginFixture *plugins, bool regtest_service,
                               WorkerProfile profile)
    : m_impl(std::make_unique<Impl>(
          root, node, max_pending, std::move(before_reconcile),
          std::move(before_notification), std::move(after_capture),
          std::move(before_disk), plugins, regtest_service, profile)) {}
ArchivalWorker::~ArchivalWorker() { Stop(); }
WorkerStatus ArchivalWorker::Status() const {
    std::lock_guard<std::mutex> lock(m_impl->mu);
    return {m_impl->accepted, m_impl->completed, m_impl->discarded,
            m_impl->rejected, m_impl->pending,   m_impl->ready,
            m_impl->failed,   m_impl->closed,    m_impl->joined,
            m_impl->failure,  m_impl->failure_pending, m_impl->tickets.size()};
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
uint16_t ArchivalWorker::TryStartRegtestHttp() {
    AssertLockNotHeld(cs_main);
    AssertLockNotHeld(m_impl->node.mempool.cs);
    if (!m_impl->service_mode || m_impl->profile!=WorkerProfile::Inherited)
        throw std::runtime_error("Not a regtest service worker");
    std::unique_lock<std::mutex> data(m_impl->data_mu, std::try_to_lock);
    if (!data.owns_lock())
        return 0;
    {
        std::lock_guard<std::mutex> lock(m_impl->mu);
        if (m_impl->closed || m_impl->failed || !m_impl->probe)
            throw std::runtime_error("Regtest HTTP owner closed");
        if(!m_impl->ready || !m_impl->replay) return 0;
    }
    return m_impl->node.permits_clients()
        ? (*m_impl->probe)->start_regtest_http_clients()
        : (*m_impl->probe)->start_regtest_http();
}
uint16_t ArchivalWorker::TryStartHistoricalHttp() {
    AssertLockNotHeld(cs_main);
    AssertLockNotHeld(m_impl->node.mempool.cs);
    if(!m_impl->service_mode || (m_impl->profile!=WorkerProfile::HistoricalReadOnly &&
                               m_impl->profile!=WorkerProfile::HistoricalPluginsReadOnly))
        throw std::runtime_error("Not a historical read-only service worker");
    std::unique_lock<std::mutex> data(m_impl->data_mu,std::try_to_lock);
    if(!data.owns_lock()) return 0;
    {
        std::lock_guard<std::mutex> lock(m_impl->mu);
        if(m_impl->closed || m_impl->failed || !m_impl->probe)
            throw std::runtime_error("Historical HTTP owner closed");
        if(!m_impl->ready || !m_impl->replay) return 0;
    }
    return (*m_impl->probe)->start_historical_http();
}
rust::Box<ResponseLatch> ArchivalWorker::ResponseBarrier() {
    return (*m_impl->response_latch)->clone_handle();
}
uint16_t ArchivalWorker::TryStartHistoricalClientsHttp(uint16_t port) {
    AssertLockNotHeld(cs_main);
    AssertLockNotHeld(m_impl->node.mempool.cs);
    if(!m_impl->service_mode || m_impl->profile!=WorkerProfile::HistoricalClientService)
        throw std::runtime_error("Not a historical client service worker");
    std::unique_lock<std::mutex> data(m_impl->data_mu,std::try_to_lock);
    if(!data.owns_lock()) return 0;
    {
        std::lock_guard<std::mutex> lock(m_impl->mu);
        if(m_impl->closed || m_impl->failed || !m_impl->probe)
            throw std::runtime_error("Historical client HTTP owner closed");
        if(!m_impl->ready || !m_impl->replay) return 0;
    }
    return (*m_impl->probe)->start_historical_clients_http(port);
}
} // namespace ergon_archival

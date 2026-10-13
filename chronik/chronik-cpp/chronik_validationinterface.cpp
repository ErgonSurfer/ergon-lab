// Copyright (c) 2022 The Bitcoin developers
// Copyright (c) 2026 The Ergon developers
// Distributed under the MIT software license, see the accompanying COPYING.
// Ergon host adaptation of the Bitcoin ABC validation-interface glue.

#include <chain.h>
#include <chronik-cpp/chronik_validationinterface.h>
#include <chronik-cpp/ergon_node_access.h>
#include <chronik_lib/src/ffi.rs.h>
#include <clientversion.h>
#include <logging.h>
#include <primitives/block.h>
#include <validation.h>
#include <validationinterface.h>
#include <version.h>

#include <algorithm>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <thread>

namespace chronik {
namespace {
// Actual joined block-event collector, not a direct donor callback forwarder.
// Mempool changes fail closed until source-barrier reconciliation is supplied.
// Runtime startup is separately denied before any Rust setup/server work.
class Collector final : public CValidationInterface {
    struct Event {
        bool connected;
        std::shared_ptr<const CBlock> block;
        const CBlockIndex *index;
        std::vector<std::array<uint8_t, 32>> conflicts;
        size_t bytes;
    };
    const chronik_bridge::ErgonNodeAccess &m_node;
    rust::Box<chronik_bridge::Chronik> m_chronik;
    std::mutex m_mutex;
    std::condition_variable m_changed;
    std::deque<Event> m_events;
    size_t m_bytes{0};
    size_t m_count{0};
    bool m_stopping{false};
    std::thread m_worker;
    static constexpr size_t MAX_EVENTS = 128;
    // Serialized payload budget, not a bound on all allocator/deep-object RAM.
    static constexpr size_t MAX_BYTES = 64 * 1024 * 1024;

    void Fail() noexcept {
        m_node.index_failed.store(true, std::memory_order_release);
        m_changed.notify_all();
    }

    void Push(Event event) {
        std::lock_guard<std::mutex> lock(m_mutex);
        if (m_stopping || m_node.index_failed.load(std::memory_order_acquire))
            return;
        if (m_count >= MAX_EVENTS || event.bytes > MAX_BYTES - m_bytes) {
            Fail();
            return;
        }
        m_bytes += event.bytes;
        ++m_count;
        m_events.push_back(std::move(event));
        m_changed.notify_one();
    }

    void Run() noexcept {
        try {
            for (;;) {
                std::unique_lock<std::mutex> lock(m_mutex);
                m_changed.wait_for(lock, std::chrono::milliseconds(250),
                                   [this] {
                                       return m_stopping || !m_events.empty() ||
                                              m_node.index_failed.load(
                                                  std::memory_order_acquire);
                                   });
                if (m_stopping ||
                    m_node.index_failed.load(std::memory_order_acquire))
                    break;
                if (m_events.empty())
                    continue;
                Event event = std::move(m_events.front());
                m_events.pop_front();
                // Charge the in-flight event until Rust finishes consuming it.
                lock.unlock();
                for (const auto &txid : event.conflicts) {
                    m_chronik->handle_tx_removed_from_mempool(txid);
                }
                if (event.connected) {
                    m_chronik->handle_block_connected(*event.block,
                                                      *event.index);
                } else {
                    m_chronik->handle_block_disconnected(*event.block,
                                                         *event.index);
                }
                lock.lock();
                m_bytes -= event.bytes;
                --m_count;
            }
            m_chronik->interrupt();
        } catch (const std::exception &) {
            Fail();
            try {
                m_chronik->interrupt();
            } catch (...) {
            }
        } catch (...) {
            Fail();
        }
    }

    void BlockConnected(
        const std::shared_ptr<const CBlock> &block, const CBlockIndex *index,
        const std::vector<CTransactionRef> &conflicts) noexcept override {
        try {
            if (!block || !index || block->GetHash() != index->GetBlockHash()) {
                Fail();
                return;
            }
            const size_t bytes = ::GetSerializeSize(*block, CLIENT_VERSION);
            if (bytes > MAX_BYTES ||
                conflicts.size() > (MAX_BYTES - bytes) / 32) {
                Fail();
                return;
            }
            Event event{true, block, index, {}, bytes + conflicts.size() * 32};
            for (const auto &tx : conflicts) {
                if (!tx) {
                    Fail();
                    return;
                }
                std::array<uint8_t, 32> hash;
                std::copy_n(tx->GetId().begin(), hash.size(), hash.begin());
                event.conflicts.push_back(hash);
            }
            Push(std::move(event));
        } catch (...) {
            Fail();
        }
    }

    void BlockDisconnected(
        const std::shared_ptr<const CBlock> &block) noexcept override {
        try {
            if (!block) {
                Fail();
                return;
            }
            const CBlockIndex *index = nullptr;
            {
                LOCK(cs_main);
                const auto found = mapBlockIndex.find(block->GetHash());
                if (found != mapBlockIndex.end())
                    index = found->second;
            }
            if (!index) {
                Fail();
                return;
            }
            Push({false,
                  block,
                  index,
                  {},
                  ::GetSerializeSize(*block, CLIENT_VERSION)});
        } catch (...) {
            Fail();
        }
    }

    void TransactionAddedToMempool(const CTransactionRef &) noexcept override {
        Fail();
    }
    void
    TransactionRemovedFromMempool(const CTransactionRef &) noexcept override {
        Fail();
    }

  public:
    Collector(const chronik_bridge::ErgonNodeAccess &node,
              rust::Box<chronik_bridge::Chronik> chronik)
        : m_node(node), m_chronik(std::move(chronik)),
          m_worker([this] { Run(); }) {}
    void Register() { RegisterValidationInterface(this); }
    void Interrupt() {
        // Called by the native service owner, never by a node callback.
        m_chronik->interrupt();
    }
    void Stop() {
        UnregisterValidationInterface(this);
        ::SyncWithValidationInterfaceQueue();
        {
            std::lock_guard<std::mutex> lock(m_mutex);
            m_stopping = true;
            m_changed.notify_all();
        }
        if (m_worker.joinable())
            m_worker.join();
        m_events.clear();
        try {
            m_chronik->stop();
        } catch (...) {
            Fail();
        }
    }
    ~Collector() {
        // Ownership is never detached from the node-backed worker.
        if (m_worker.joinable())
            Stop();
    }
};
std::unique_ptr<Collector> collector;
} // namespace

void StartChronikValidationInterface(
    const chronik_bridge::ErgonNodeAccess &node,
    rust::Box<chronik_bridge::Chronik> chronik) {
    if (!chronik_bridge::upstream_runtime_permitted(node) || collector) {
        // Defensive second gate; ordinary setup returns false before opening
        // any index or listener. Never register callbacks in the link profile.
        node.index_failed.store(true, std::memory_order_release);
        chronik->interrupt();
        try {
            chronik->stop();
        } catch (...) {
        }
        return;
    }
    collector = std::make_unique<Collector>(node, std::move(chronik));
    collector->Register();
}
void InterruptChronikRequestHandlers() {
    if (collector)
        collector->Interrupt();
}
void StopChronikValidationInterface() {
    if (collector) {
        collector->Stop();
        collector.reset();
    }
}
} // namespace chronik

// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#include <chronik/node-observer.h>

#include <chronik/event-envelope.h>

#include <chain.h>
#include <chainparams.h>
#include <logging.h>
#include <primitives/block.h>
#include <streams.h>
#include <uint256.h>
#include <validation.h>
#include <validationinterface.h>
#include <version.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <stdexcept>
#include <utility>
#include <vector>

namespace {

struct ChronikBlockObservation {
    uint64_t sequence;
    uint64_t fingerprint;
    uint64_t payload_size;
    uint64_t payload_fingerprint;
    uint64_t transaction_count;
    uint64_t slp_family_transactions;
    uint64_t alp_family_transactions;
    uint64_t token_parse_failures;
    uint64_t token_color_failures;
    uint64_t cash_token_prefix_outputs;
    uint64_t projection_blocks;
    uint64_t projection_transactions;
    uint64_t projection_slp_family_transactions;
    uint64_t projection_alp_family_transactions;
    uint64_t projection_token_parse_failures;
    uint64_t projection_token_color_failures;
    uint64_t projection_cash_token_prefix_outputs;
    uint64_t block_transaction_record_fingerprint;
    uint64_t projection_transaction_record_fingerprint;
};

static_assert(sizeof(ChronikBlockObservation) == 19 * sizeof(uint64_t));

struct ChronikProjectionObservation {
    uint64_t success;
    uint64_t blocks;
    uint64_t transactions;
    uint64_t slp_family_transactions;
    uint64_t alp_family_transactions;
    uint64_t token_parse_failures;
    uint64_t token_color_failures;
    uint64_t cash_token_prefix_outputs;
    uint64_t transaction_record_fingerprint;
};

static_assert(sizeof(ChronikProjectionObservation) == 9 * sizeof(uint64_t));

struct ChronikRuntimeTip {
    uint64_t success;
    uint64_t error_code;
    uint64_t present;
    int64_t height;
    std::array<uint8_t, 32> hash;
};

static_assert(sizeof(ChronikRuntimeTip) == 8 * sizeof(uint64_t));

struct ChronikRuntimeMutation {
    uint64_t success;
    uint64_t error_code;
    int64_t height;
    uint64_t transactions;
    uint64_t new_tokens;
    uint64_t token_transactions;
};

static_assert(sizeof(ChronikRuntimeMutation) == 6 * sizeof(uint64_t));

// Match the active-chain suffix that the legacy node guarantees to retain.
constexpr int32_t CHRONIK_OBSERVER_RETAINED_BLOCKS = MIN_BLOCKS_TO_KEEP;
// Cover the governed 288-block observer window plus callback bursts while
// RocksDB commits are serialized by the worker. The queue remains bounded and
// callbacks never wait; overflow still disables observation fail-closed.
constexpr size_t CHRONIK_EVENT_CAPACITY = 512;
constexpr auto CHRONIK_SHUTDOWN_TIMEOUT = std::chrono::seconds(2);
constexpr uint8_t TEST_FAILURE_NONE = 0;
constexpr uint8_t TEST_FAILURE_CONSTRUCTION = 1;
constexpr uint8_t TEST_FAILURE_BOOTSTRAP = 2;
constexpr uint8_t TEST_FAILURE_ENVELOPE_START = 3;

extern "C" {
void *chronik_observer_create_bounded(uint64_t max_blocks);
uint64_t chronik_observer_destroy(void *observer);
uint64_t chronik_observer_requires_rebuild(const void *observer);
ChronikProjectionObservation chronik_observer_adopt_projection(
    void *observer, void *rebuilt);
ChronikBlockObservation chronik_observer_block_connected(
    void *observer, const uint8_t *hash, int32_t height,
    const uint8_t *raw_block, size_t raw_block_size);
ChronikBlockObservation chronik_observer_block_disconnected(
    void *observer, const uint8_t *hash);
void *chronik_runtime_create(const uint8_t *path, size_t path_size,
                             uint64_t reset);
uint64_t chronik_runtime_destroy(void *runtime);
ChronikRuntimeTip chronik_runtime_tip(const void *runtime);
ChronikRuntimeMutation chronik_runtime_connect(
    const void *runtime, const uint8_t *hash, int32_t height,
    const uint8_t *raw_block, size_t raw_block_size);
ChronikRuntimeMutation chronik_runtime_disconnect(
    const void *runtime, const uint8_t *hash, int32_t height,
    const uint8_t *raw_block, size_t raw_block_size);
}

class RustObserverHandle final {
public:
    explicit RustObserverHandle(void *observer) : m_observer(observer) {}
    RustObserverHandle(const RustObserverHandle &) = delete;
    RustObserverHandle &operator=(const RustObserverHandle &) = delete;

    ~RustObserverHandle() { Destroy(); }

    void *Get() const noexcept { return m_observer; }

    uint64_t Destroy() noexcept {
        void *observer = std::exchange(m_observer, nullptr);
        return observer == nullptr ? 0 : chronik_observer_destroy(observer);
    }

private:
    void *m_observer;
};

class RustRuntimeHandle final {
public:
    explicit RustRuntimeHandle(void *runtime) : m_runtime(runtime) {}
    RustRuntimeHandle(const RustRuntimeHandle &) = delete;
    RustRuntimeHandle &operator=(const RustRuntimeHandle &) = delete;

    ~RustRuntimeHandle() { Destroy(); }

    void *Get() const noexcept { return m_runtime; }

    uint64_t Destroy() noexcept {
        void *runtime = std::exchange(m_runtime, nullptr);
        return runtime == nullptr ? 0 : chronik_runtime_destroy(runtime);
    }

private:
    void *m_runtime;
};

std::array<uint8_t, 32> CopyHash(const uint256 &hash) {
    std::array<uint8_t, 32> result{};
    std::copy(hash.begin(), hash.end(), result.begin());
    return result;
}

uint256 CopyUint256(const std::array<uint8_t, 32> &hash) {
    uint256 result;
    std::copy(hash.begin(), hash.end(), result.begin());
    return result;
}

void LogConnectedObservation(
    const uint256 &hash, int32_t height,
    const ChronikBlockObservation &observation) noexcept {
    try {
        if (observation.sequence == 0) {
            LogPrintf("Chronik observer rejected kind=connected hash=%s\n",
                      hash.GetHex());
            return;
        }
        LogPrintf("Chronik observer event sequence=%u kind=connected hash=%s "
                  "height=%d fingerprint=%u bytes=%u "
                  "payload_fingerprint=%u transactions=%u "
                  "slp_family_transactions=%u alp_family_transactions=%u "
                  "token_parse_failures=%u token_color_failures=%u "
                  "cash_token_prefix_outputs=%u projection_blocks=%u "
                  "projection_transactions=%u "
                  "projection_slp_family_transactions=%u "
                  "projection_alp_family_transactions=%u "
                  "projection_token_parse_failures=%u "
                  "projection_token_color_failures=%u "
                  "projection_cash_token_prefix_outputs=%u "
                  "block_transaction_record_fingerprint=%u "
                  "projection_transaction_record_fingerprint=%u\n",
                  observation.sequence, hash.GetHex(), height,
                  observation.fingerprint, observation.payload_size,
                  observation.payload_fingerprint,
                  observation.transaction_count,
                  observation.slp_family_transactions,
                  observation.alp_family_transactions,
                  observation.token_parse_failures,
                  observation.token_color_failures,
                  observation.cash_token_prefix_outputs,
                  observation.projection_blocks,
                  observation.projection_transactions,
                  observation.projection_slp_family_transactions,
                  observation.projection_alp_family_transactions,
                  observation.projection_token_parse_failures,
                  observation.projection_token_color_failures,
                  observation.projection_cash_token_prefix_outputs,
                  observation.block_transaction_record_fingerprint,
                  observation.projection_transaction_record_fingerprint);
    } catch (...) {
    }
}

void LogDisconnectedObservation(
    const uint256 &hash,
    const ChronikBlockObservation &observation) noexcept {
    try {
        if (observation.sequence == 0) {
            LogPrintf("Chronik observer rejected kind=disconnected hash=%s\n",
                      hash.GetHex());
            return;
        }
        LogPrintf("Chronik observer event sequence=%u kind=disconnected "
                  "hash=%s height=-1 fingerprint=%u transactions=%u "
                  "slp_family_transactions=%u alp_family_transactions=%u "
                  "token_parse_failures=%u token_color_failures=%u "
                  "cash_token_prefix_outputs=%u projection_blocks=%u "
                  "projection_transactions=%u "
                  "projection_slp_family_transactions=%u "
                  "projection_alp_family_transactions=%u "
                  "projection_token_parse_failures=%u "
                  "projection_token_color_failures=%u "
                  "projection_cash_token_prefix_outputs=%u "
                  "block_transaction_record_fingerprint=%u "
                  "projection_transaction_record_fingerprint=%u\n",
                  observation.sequence, hash.GetHex(), observation.fingerprint,
                  observation.transaction_count,
                  observation.slp_family_transactions,
                  observation.alp_family_transactions,
                  observation.token_parse_failures,
                  observation.token_color_failures,
                  observation.cash_token_prefix_outputs,
                  observation.projection_blocks,
                  observation.projection_transactions,
                  observation.projection_slp_family_transactions,
                  observation.projection_alp_family_transactions,
                  observation.projection_token_parse_failures,
                  observation.projection_token_color_failures,
                  observation.projection_cash_token_prefix_outputs,
                  observation.block_transaction_record_fingerprint,
                  observation.projection_transaction_record_fingerprint);
    } catch (...) {
    }
}

class ChronikNodeObserver final : public CValidationInterface {
public:
    explicit ChronikNodeObserver(const std::string &runtime_path,
                                 bool reset_runtime,
                                 uint8_t test_failure_point = TEST_FAILURE_NONE)
        : m_observer(std::make_shared<RustObserverHandle>(
              chronik_observer_create_bounded(
                  CHRONIK_OBSERVER_RETAINED_BLOCKS))),
          m_runtime_requested(!runtime_path.empty()),
          m_runtime_active(std::make_shared<std::atomic<bool>>(false)),
          m_rebuild_required_logged(std::make_shared<std::atomic<bool>>(false)),
          m_test_failure_point(test_failure_point) {
        if (m_runtime_requested) {
            void *runtime = chronik_runtime_create(
                reinterpret_cast<const uint8_t *>(runtime_path.data()),
                runtime_path.size(), reset_runtime);
            if (runtime != nullptr) {
                m_runtime = std::make_shared<RustRuntimeHandle>(runtime);
                m_runtime_active->store(true, std::memory_order_release);
            } else {
                LogPrintf("Chronik runtime state=disabled reason=open-failed "
                          "node_continues=1 observer_continues=1\n");
            }
        }
    }

    ~ChronikNodeObserver() {
        chronik::EnvelopeSnapshot snapshot;
        bool drained = true;
        if (m_envelope) {
            snapshot = m_envelope->Snapshot();
            drained = m_envelope->Shutdown(CHRONIK_SHUTDOWN_TIMEOUT);
            snapshot = m_envelope->Snapshot();
            m_envelope.reset();
        }

        uint64_t observations = snapshot.last_processed_sequence;
        if (drained && m_observer) {
            observations = m_observer->Destroy();
        }
        m_observer.reset();
        const uint64_t runtime_closed =
            drained && m_runtime ? m_runtime->Destroy() : 0;
        m_runtime.reset();
        LogPrintf("Chronik observer stopped observations=%u accepted=%u "
                  "processed=%u degraded=%d rebuild_required=%d "
                  "runtime_closed=%d shutdown=%s\n",
                  observations, snapshot.accepted, snapshot.processed,
                  snapshot.degraded, snapshot.rebuild_required,
                  runtime_closed != 0,
                  drained ? "drained" : "detached");
    }

    bool IsReady() const {
        return m_observer && m_observer->Get() != nullptr;
    }

    bool RuntimeActive() const noexcept {
        return m_runtime_active->load(std::memory_order_acquire);
    }

    bool Bootstrap() {
        if (m_test_failure_point == TEST_FAILURE_BOOTSTRAP) {
            throw std::runtime_error("injected bootstrap failure");
        }
        if (!BootstrapRuntime()) {
            DisableRuntime("bootstrap-failed");
        }
        std::vector<const CBlockIndex *> indexes;
        {
            LOCK(cs_main);
            const CBlockIndex *tip = ::ChainActive().Tip();
            if (tip == nullptr) {
                LogPrintf("Chronik observer bootstrap active_chain=empty "
                          "retained_blocks=0\n");
                return true;
            }
            const int32_t start_height = std::max<int32_t>(
                0, tip->nHeight - CHRONIK_OBSERVER_RETAINED_BLOCKS + 1);
            indexes.reserve(tip->nHeight - start_height + 1);
            for (int32_t height = start_height; height <= tip->nHeight;
                 ++height) {
                indexes.push_back(::ChainActive()[height]);
            }
        }

        void *rebuilt = chronik_observer_create_bounded(
            CHRONIK_OBSERVER_RETAINED_BLOCKS);
        if (rebuilt == nullptr) {
            return false;
        }
        for (const CBlockIndex *index : indexes) {
            CBlock block;
            if (!ReadBlockFromDisk(block, index, Params().GetConsensus())) {
                chronik_observer_destroy(rebuilt);
                LogPrintf("Chronik observer rejected kind=bootstrap-read "
                          "hash=%s height=%d\n",
                          index->GetBlockHash().GetHex(), index->nHeight);
                return false;
            }
            CDataStream serialized_block(SER_NETWORK, PROTOCOL_VERSION);
            serialized_block << block;
            const uint256 hash = index->GetBlockHash();
            const ChronikBlockObservation observation =
                chronik_observer_block_connected(
                    rebuilt, hash.begin(), index->nHeight,
                    reinterpret_cast<const uint8_t *>(serialized_block.data()),
                    serialized_block.size());
            if (observation.sequence == 0) {
                chronik_observer_destroy(rebuilt);
                LogPrintf("Chronik observer rejected kind=bootstrap-parse "
                          "hash=%s height=%d\n",
                          hash.GetHex(), index->nHeight);
                return false;
            }
        }

        const ChronikProjectionObservation projection =
            chronik_observer_adopt_projection(m_observer->Get(), rebuilt);
        if (projection.success == 0) {
            LogPrintf("Chronik observer rejected kind=bootstrap-adopt\n");
            return false;
        }
        LogPrintf("Chronik observer bootstrap start_height=%d tip_height=%d "
                  "retained_blocks=%u transactions=%u "
                  "slp_family_transactions=%u alp_family_transactions=%u "
                  "token_parse_failures=%u token_color_failures=%u "
                  "cash_token_prefix_outputs=%u "
                  "transaction_record_fingerprint=%u\n",
                  indexes.front()->nHeight, indexes.back()->nHeight,
                  projection.blocks, projection.transactions,
                  projection.slp_family_transactions,
                  projection.alp_family_transactions,
                  projection.token_parse_failures,
                  projection.token_color_failures,
                  projection.cash_token_prefix_outputs,
                  projection.transaction_record_fingerprint);
        return true;
    }

    bool BootstrapRuntime() {
        if (!m_runtime || !RuntimeActive()) {
            return true;
        }
        const ChronikRuntimeTip runtime_tip =
            chronik_runtime_tip(m_runtime->Get());
        if (runtime_tip.success == 0) {
            LogPrintf("Chronik runtime rejected kind=tip-query error=%u\n",
                      runtime_tip.error_code);
            return false;
        }

        std::vector<const CBlockIndex *> indexes;
        const CBlockIndex *active_tip = nullptr;
        {
            LOCK(cs_main);
            active_tip = ::ChainActive().Tip();
            if (runtime_tip.present != 0) {
                if (active_tip != nullptr &&
                    runtime_tip.height == active_tip->nHeight &&
                    runtime_tip.hash == CopyHash(active_tip->GetBlockHash())) {
                    LogPrintf("Chronik runtime bootstrap mode=resume "
                              "tip_height=%d tip_hash=%s\n",
                              active_tip->nHeight,
                              active_tip->GetBlockHash().GetHex());
                    return true;
                }
                LogPrintf("Chronik runtime state=rebuild-required "
                          "reason=tip-mismatch recovery=reindex\n");
                return false;
            }
            if (active_tip == nullptr) {
                LogPrintf("Chronik runtime bootstrap mode=empty tip_height=-1\n");
                return true;
            }
            indexes.reserve(active_tip->nHeight + 1);
            for (int32_t height = 0; height <= active_tip->nHeight; ++height) {
                indexes.push_back(::ChainActive()[height]);
            }
        }

        uint64_t transactions = 0;
        uint64_t new_tokens = 0;
        uint64_t token_transactions = 0;
        for (const CBlockIndex *index : indexes) {
            CBlock block;
            if (!ReadBlockFromDisk(block, index, Params().GetConsensus())) {
                LogPrintf("Chronik runtime rejected kind=bootstrap-read "
                          "hash=%s height=%d recovery=reindex\n",
                          index->GetBlockHash().GetHex(), index->nHeight);
                return false;
            }
            CDataStream serialized_block(SER_NETWORK, PROTOCOL_VERSION);
            serialized_block << block;
            const uint256 hash = index->GetBlockHash();
            const ChronikRuntimeMutation mutation = chronik_runtime_connect(
                m_runtime->Get(), hash.begin(), index->nHeight,
                reinterpret_cast<const uint8_t *>(serialized_block.data()),
                serialized_block.size());
            if (mutation.success == 0) {
                LogPrintf("Chronik runtime rejected kind=bootstrap-index "
                          "hash=%s height=%d error=%u recovery=reindex\n",
                          hash.GetHex(), index->nHeight,
                          mutation.error_code);
                return false;
            }
            transactions += mutation.transactions;
            new_tokens += mutation.new_tokens;
            token_transactions += mutation.token_transactions;
        }
        LogPrintf("Chronik runtime bootstrap mode=rebuild start_height=0 "
                  "tip_height=%d blocks=%u transactions=%u new_tokens=%u "
                  "token_transactions=%u\n",
                  active_tip->nHeight, indexes.size(), transactions,
                  new_tokens, token_transactions);
        return true;
    }

    void DisableRuntime(const char *reason) noexcept {
        if (!m_runtime_active->exchange(false, std::memory_order_acq_rel)) {
            return;
        }
        if (m_runtime) {
            m_runtime->Destroy();
        }
        LogPrintf("Chronik runtime state=disabled reason=%s node_continues=1 "
                  "observer_continues=1 recovery=reindex\n",
                  reason);
    }

    bool StartEnvelope() {
        if (m_test_failure_point == TEST_FAILURE_ENVELOPE_START) {
            throw std::runtime_error("injected envelope-start failure");
        }
        const auto observer = m_observer;
        const auto runtime = m_runtime;
        const auto runtime_active = m_runtime_active;
        const auto rebuild_logged = m_rebuild_required_logged;
        m_envelope = chronik::EventEnvelope::Create(
            CHRONIK_EVENT_CAPACITY,
            [observer, runtime, runtime_active,
             rebuild_logged](const chronik::OwnedEvent &event) {
                const uint256 hash = CopyUint256(event.hash);
                ChronikBlockObservation observation{};
                if (event.kind == chronik::EventKind::BLOCK_CONNECTED) {
                    observation = chronik_observer_block_connected(
                        observer->Get(), event.hash.data(), event.height,
                        event.payload.data(), event.payload.size());
                    LogConnectedObservation(hash, event.height, observation);
                } else {
                    observation = chronik_observer_block_disconnected(
                        observer->Get(), event.hash.data());
                    LogDisconnectedObservation(hash, observation);
                }
                if (runtime &&
                    runtime_active->load(std::memory_order_acquire)) {
                    const ChronikRuntimeMutation mutation =
                        event.kind == chronik::EventKind::BLOCK_CONNECTED
                            ? chronik_runtime_connect(
                                  runtime->Get(), event.hash.data(),
                                  event.height, event.payload.data(),
                                  event.payload.size())
                            : chronik_runtime_disconnect(
                                  runtime->Get(), event.hash.data(),
                                  event.height, event.payload.data(),
                                  event.payload.size());
                    if (mutation.success == 0) {
                        runtime_active->store(false, std::memory_order_release);
                        runtime->Destroy();
                        LogPrintf("Chronik runtime rejected kind=%s hash=%s "
                                  "height=%d error=%u node_continues=1 "
                                  "observer_continues=1 "
                                  "runtime_state=disabled recovery=reindex\n",
                                  event.kind ==
                                          chronik::EventKind::BLOCK_CONNECTED
                                      ? "connected"
                                      : "disconnected",
                                  hash.GetHex(), event.height,
                                  mutation.error_code);
                    } else {
                        LogPrintf("Chronik runtime event kind=%s hash=%s "
                                  "height=%d transactions=%u new_tokens=%u "
                                  "token_transactions=%u\n",
                                  event.kind ==
                                          chronik::EventKind::BLOCK_CONNECTED
                                      ? "connected"
                                      : "disconnected",
                                  hash.GetHex(), event.height,
                                  mutation.transactions, mutation.new_tokens,
                                  mutation.token_transactions);
                    }
                }
                if (observation.sequence == 0 ||
                    observation.sequence != event.sequence) {
                    return false;
                }
                if (chronik_observer_requires_rebuild(observer->Get()) != 0) {
                    if (!rebuild_logged->exchange(true)) {
                        LogPrintf("Chronik observer state=rebuild-required "
                                  "hash=%s reason=retained-anchor-disconnected "
                                  "recovery=restart\n",
                                  hash.GetHex());
                    }
                    return false;
                }
                return true;
            });
        return m_envelope != nullptr;
    }

protected:
    void BlockConnected(
        const std::shared_ptr<const CBlock> &block,
        const CBlockIndex *index,
        const std::vector<CTransactionRef> &transactions_conflicted) noexcept
        override {
        (void)transactions_conflicted;
        if (!m_envelope || block == nullptr || index == nullptr) {
            if (m_envelope) {
                m_envelope->MarkDegraded();
            }
            return;
        }
        try {
            const uint256 hash = index->GetBlockHash();
            CDataStream serialized_block(SER_NETWORK, PROTOCOL_VERSION);
            serialized_block << *block;
            const auto *payload_begin =
                reinterpret_cast<const uint8_t *>(serialized_block.data());
            const std::vector<uint8_t> payload(
                payload_begin, payload_begin + serialized_block.size());
            const uint64_t sequence = NextSequence();
            if (sequence != 0) {
                m_envelope->Submit(sequence,
                                   chronik::EventKind::BLOCK_CONNECTED,
                                   CopyHash(hash), index->nHeight,
                                   std::move(payload));
            }
        } catch (...) {
            m_envelope->MarkDegraded();
        }
    }

    void BlockDisconnected(
        const std::shared_ptr<const CBlock> &block) noexcept override {
        if (!m_envelope || block == nullptr) {
            if (m_envelope) {
                m_envelope->MarkDegraded();
            }
            return;
        }
        try {
            CDataStream serialized_block(SER_NETWORK, PROTOCOL_VERSION);
            serialized_block << *block;
            const auto *payload_begin =
                reinterpret_cast<const uint8_t *>(serialized_block.data());
            const std::vector<uint8_t> payload(
                payload_begin, payload_begin + serialized_block.size());
            const uint64_t sequence = NextSequence();
            if (sequence != 0) {
                m_envelope->Submit(sequence,
                                   chronik::EventKind::BLOCK_DISCONNECTED,
                                   CopyHash(block->GetHash()), -1,
                                   std::move(payload));
            }
        } catch (...) {
            m_envelope->MarkDegraded();
        }
    }

private:
    uint64_t NextSequence() noexcept {
        const uint64_t previous =
            m_next_sequence.fetch_add(1, std::memory_order_acq_rel);
        if (previous == UINT64_MAX) {
            m_envelope->MarkDegraded();
            return 0;
        }
        return previous + 1;
    }

    std::shared_ptr<RustObserverHandle> m_observer;
    std::shared_ptr<RustRuntimeHandle> m_runtime;
    bool m_runtime_requested;
    std::shared_ptr<std::atomic<bool>> m_runtime_active;
    std::shared_ptr<std::atomic<bool>> m_rebuild_required_logged;
    uint8_t m_test_failure_point;
    std::unique_ptr<chronik::EventEnvelope> m_envelope;
    std::atomic<uint64_t> m_next_sequence{0};
};

std::unique_ptr<ChronikNodeObserver> g_chronik_node_observer;
bool g_chronik_node_observer_registered{false};

bool StartNodeObserverWithFailurePoint(const std::string &runtime_path,
                                       bool reset_runtime,
                                       uint8_t test_failure_point) noexcept {
    if (g_chronik_node_observer) {
        return false;
    }

    ChronikNodeObserver *registered_observer = nullptr;
    try {
        if (test_failure_point == TEST_FAILURE_CONSTRUCTION) {
            throw std::runtime_error("injected construction failure");
        }
        auto observer = std::make_unique<ChronikNodeObserver>(
            runtime_path, reset_runtime, test_failure_point);
        if (!observer->IsReady() || !observer->Bootstrap() ||
            !observer->StartEnvelope()) {
            return false;
        }
        const bool runtime_active = observer->RuntimeActive();
        registered_observer = observer.get();
        RegisterValidationInterface(registered_observer);
        g_chronik_node_observer_registered = true;
        g_chronik_node_observer = std::move(observer);
        LogPrintf("Chronik observer started mode=persistent-token-runtime "
                  "runtime_enabled=%d events=blocks "
                  "retained_blocks=%d owner=event-envelope+rust-worker "
                  "command_capacity=%u callback_waits=0\n",
                  runtime_active,
                  CHRONIK_OBSERVER_RETAINED_BLOCKS,
                  static_cast<unsigned>(CHRONIK_EVENT_CAPACITY));
        return true;
    } catch (...) {
        if (registered_observer != nullptr) {
            try {
                UnregisterValidationInterface(registered_observer);
            } catch (...) {
            }
        }
        g_chronik_node_observer_registered = false;
        if (g_chronik_node_observer.get() == registered_observer) {
            g_chronik_node_observer.reset();
        }
        return false;
    }
}

} // namespace

namespace chronik {

bool StartNodeObserver(const std::string &runtime_path, bool reset_runtime) {
    return StartNodeObserverWithFailurePoint(
        runtime_path, reset_runtime, TEST_FAILURE_NONE);
}

bool StartNodeObserverForTest(uint8_t test_failure_point) noexcept {
    return StartNodeObserverWithFailurePoint(
        std::string{}, false, test_failure_point);
}

bool StartNodeObserverForTest(uint8_t test_failure_point,
                              const std::string &runtime_path,
                              bool reset_runtime) noexcept {
    return StartNodeObserverWithFailurePoint(
        runtime_path, reset_runtime, test_failure_point);
}

bool NodeObserverActiveForTest() noexcept {
    return g_chronik_node_observer != nullptr;
}

bool NodeObserverRegisteredForTest() noexcept {
    return g_chronik_node_observer_registered;
}

void StopNodeObserver() {
    if (!g_chronik_node_observer) {
        return;
    }
    UnregisterValidationInterface(g_chronik_node_observer.get());
    g_chronik_node_observer_registered = false;
    g_chronik_node_observer.reset();
}

} // namespace chronik

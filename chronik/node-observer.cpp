// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#include <chronik/node-observer.h>

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
#include <cstddef>
#include <cstdint>
#include <memory>
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
};

static_assert(sizeof(ChronikBlockObservation) == 17 * sizeof(uint64_t));

struct ChronikProjectionObservation {
    uint64_t success;
    uint64_t blocks;
    uint64_t transactions;
    uint64_t slp_family_transactions;
    uint64_t alp_family_transactions;
    uint64_t token_parse_failures;
    uint64_t token_color_failures;
    uint64_t cash_token_prefix_outputs;
};

static_assert(sizeof(ChronikProjectionObservation) == 8 * sizeof(uint64_t));

// Match the active-chain suffix that the legacy node guarantees to retain.
constexpr int32_t CHRONIK_OBSERVER_RETAINED_BLOCKS = MIN_BLOCKS_TO_KEEP;

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
}

class ChronikNodeObserver final : public CValidationInterface {
public:
    ChronikNodeObserver()
        : m_observer(chronik_observer_create_bounded(
              CHRONIK_OBSERVER_RETAINED_BLOCKS)) {}

    ~ChronikNodeObserver() {
        const uint64_t observations = chronik_observer_destroy(m_observer);
        LogPrintf("Chronik observer stopped observations=%u\n", observations);
    }

    bool IsReady() const { return m_observer != nullptr; }

    bool Bootstrap() {
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
            chronik_observer_adopt_projection(m_observer, rebuilt);
        if (projection.success == 0) {
            LogPrintf("Chronik observer rejected kind=bootstrap-adopt\n");
            return false;
        }
        LogPrintf("Chronik observer bootstrap start_height=%d tip_height=%d "
                  "retained_blocks=%u transactions=%u "
                  "slp_family_transactions=%u alp_family_transactions=%u "
                  "token_parse_failures=%u token_color_failures=%u "
                  "cash_token_prefix_outputs=%u\n",
                  indexes.front()->nHeight, indexes.back()->nHeight,
                  projection.blocks, projection.transactions,
                  projection.slp_family_transactions,
                  projection.alp_family_transactions,
                  projection.token_parse_failures,
                  projection.token_color_failures,
                  projection.cash_token_prefix_outputs);
        return true;
    }

protected:
    void BlockConnected(
        const std::shared_ptr<const CBlock> &block,
        const CBlockIndex *index,
        const std::vector<CTransactionRef> &transactions_conflicted) override {
        (void)transactions_conflicted;
        const uint256 hash = index->GetBlockHash();
        CDataStream serialized_block(SER_NETWORK, PROTOCOL_VERSION);
        serialized_block << *block;
        const ChronikBlockObservation observation =
            chronik_observer_block_connected(
                m_observer, hash.begin(), index->nHeight,
                reinterpret_cast<const uint8_t *>(serialized_block.data()),
                serialized_block.size());
        LogConnectedObservation(hash, index->nHeight, observation);
        LogRebuildRequired(hash);
    }

    void BlockDisconnected(
        const std::shared_ptr<const CBlock> &block) override {
        const uint256 hash = block->GetHash();
        const ChronikBlockObservation observation =
            chronik_observer_block_disconnected(m_observer, hash.begin());
        LogDisconnectedObservation(hash, observation);
        LogRebuildRequired(hash);
    }

private:
    void LogConnectedObservation(
        const uint256 &hash, int32_t height,
        const ChronikBlockObservation &observation) const {
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
                  "projection_cash_token_prefix_outputs=%u\n",
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
                  observation.projection_cash_token_prefix_outputs);
    }

    void LogDisconnectedObservation(
        const uint256 &hash,
        const ChronikBlockObservation &observation) const {
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
                  "projection_cash_token_prefix_outputs=%u\n",
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
                  observation.projection_cash_token_prefix_outputs);
    }

    void LogRebuildRequired(const uint256 &hash) {
        if (m_rebuild_required_logged ||
            chronik_observer_requires_rebuild(m_observer) == 0) {
            return;
        }
        m_rebuild_required_logged = true;
        LogPrintf("Chronik observer state=rebuild-required hash=%s "
                  "reason=retained-anchor-disconnected recovery=restart\n",
                  hash.GetHex());
    }

    void *m_observer;
    bool m_rebuild_required_logged{false};
};

std::unique_ptr<ChronikNodeObserver> g_chronik_node_observer;

} // namespace

namespace chronik {

bool StartNodeObserver() {
    if (g_chronik_node_observer) {
        return false;
    }
    auto observer = std::make_unique<ChronikNodeObserver>();
    if (!observer->IsReady() || !observer->Bootstrap()) {
        return false;
    }
    RegisterValidationInterface(observer.get());
    g_chronik_node_observer = std::move(observer);
    LogPrintf("Chronik observer started mode=in-memory events=blocks "
              "retained_blocks=%d\n",
              CHRONIK_OBSERVER_RETAINED_BLOCKS);
    return true;
}

void StopNodeObserver() {
    if (!g_chronik_node_observer) {
        return;
    }
    UnregisterValidationInterface(g_chronik_node_observer.get());
    g_chronik_node_observer.reset();
}

} // namespace chronik

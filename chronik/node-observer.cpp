// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#include <chronik/node-observer.h>

#include <chain.h>
#include <logging.h>
#include <primitives/block.h>
#include <uint256.h>
#include <validationinterface.h>

#include <cstdint>
#include <memory>

namespace {

struct ChronikObservation {
    uint64_t sequence;
    uint64_t fingerprint;
};

extern "C" {
void *chronik_observer_create();
uint64_t chronik_observer_destroy(void *observer);
ChronikObservation chronik_observer_block_connected(void *observer,
                                                     const uint8_t *hash,
                                                     int32_t height);
ChronikObservation chronik_observer_block_disconnected(void *observer,
                                                        const uint8_t *hash);
}

class ChronikNodeObserver final : public CValidationInterface {
public:
    ChronikNodeObserver() : m_observer(chronik_observer_create()) {}

    ~ChronikNodeObserver() {
        const uint64_t observations = chronik_observer_destroy(m_observer);
        LogPrintf("Chronik observer stopped observations=%u\n", observations);
    }

    bool IsReady() const { return m_observer != nullptr; }

protected:
    void BlockConnected(
        const std::shared_ptr<const CBlock> &block,
        const CBlockIndex *index,
        const std::vector<CTransactionRef> &transactions_conflicted) override {
        (void)block;
        (void)transactions_conflicted;
        const uint256 hash = index->GetBlockHash();
        LogObservation("connected", hash, index->nHeight,
                       chronik_observer_block_connected(
                           m_observer, hash.begin(), index->nHeight));
    }

    void BlockDisconnected(
        const std::shared_ptr<const CBlock> &block) override {
        const uint256 hash = block->GetHash();
        LogObservation("disconnected", hash, -1,
                       chronik_observer_block_disconnected(m_observer,
                                                           hash.begin()));
    }

private:
    void LogObservation(const char *kind, const uint256 &hash, int32_t height,
                        const ChronikObservation &observation) const {
        if (observation.sequence == 0) {
            LogPrintf("Chronik observer rejected kind=%s hash=%s\n", kind,
                      hash.GetHex());
            return;
        }
        LogPrintf("Chronik observer event sequence=%u kind=%s hash=%s "
                  "height=%d fingerprint=%u\n",
                  observation.sequence, kind, hash.GetHex(), height,
                  observation.fingerprint);
    }

    void *m_observer;
};

std::unique_ptr<ChronikNodeObserver> g_chronik_node_observer;

} // namespace

namespace chronik {

bool StartNodeObserver() {
    if (g_chronik_node_observer) {
        return false;
    }
    auto observer = std::make_unique<ChronikNodeObserver>();
    if (!observer->IsReady()) {
        return false;
    }
    RegisterValidationInterface(observer.get());
    g_chronik_node_observer = std::move(observer);
    LogPrintf("Chronik observer started mode=in-memory events=blocks\n");
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

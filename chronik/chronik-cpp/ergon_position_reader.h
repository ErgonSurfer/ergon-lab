// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#ifndef ERGON_CHRONIK_POSITION_READER_H
#define ERGON_CHRONIK_POSITION_READER_H

#include <coins.h>
#include <cstdint>
#include <functional>
#include <memory>
#include <primitives/transaction.h>
#include <vector>

namespace chronik_bridge {
struct ErgonNodeAccess;
struct NativePositionStats {
    uint64_t catalogue_added{0}, catalogue_removed{0}, cold_layouts{0};
    uint64_t warm_reads{0}, transaction_bytes{0}, undo_bytes{0};
    uint64_t cold_block_bytes{0};
    size_t cached_layouts{0};
};
struct PositionedTransaction {
    CTransactionRef tx;
    std::vector<::Coin> coins;
};
// Service-owned, nonpersistent position metadata. No consensus authority,
// donor schema change, escaped CBlockIndex pointer or cached coin fabrication.
class NativePositionReader {
    struct Impl;
    std::unique_ptr<Impl> m_impl;

  public:
    explicit NativePositionReader(const ErgonNodeAccess &node);
    ~NativePositionReader();
    PositionedTransaction Read(uint32_t file, uint32_t position,
                               const uint32_t *expected_undo) const;
    NativePositionStats Stats() const;
    // Deterministic native fixture only; no CLI/config/network exposure.
    void BeforeReadForTesting(std::function<void()> hook);
};
} // namespace chronik_bridge
#endif

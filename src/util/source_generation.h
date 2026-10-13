// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#ifndef BITCOIN_UTIL_SOURCE_GENERATION_H
#define BITCOIN_UTIL_SOURCE_GENERATION_H

#include <cstdint>
#include <limits>

/** Observer identity only; never a validity, mining or chain-selection rule.
 * The owning native lock must fence every read and mutation. Zero is terminal
 * exhaustion, not a wrapped/reusable generation. Advance never throws or
 * blocks validation. This identity is process-local, not persisted evidence.
 * Noncopyable: restoring another source object must not restore an old epoch.
 */
class SourceGeneration {
    uint64_t m_value{1};

  public:
    SourceGeneration() = default;
    SourceGeneration(const SourceGeneration &) = delete;
    SourceGeneration &operator=(const SourceGeneration &) = delete;
    uint64_t Value() const noexcept { return m_value; }
    void Advance(uint64_t count = 1) noexcept {
        if (!m_value ||
            count > std::numeric_limits<uint64_t>::max() - m_value) {
            m_value = 0;
        } else {
            m_value += count;
        }
    }
};
#endif

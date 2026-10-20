// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#ifndef ERGON_CHRONIK_CPP_ERGON_NODE_ACCESS_H
#define ERGON_CHRONIK_CPP_ERGON_NODE_ACCESS_H

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <limits>
#include <chainparams.h>
#include <config.h>
#include <optional>
#include <stdexcept>
#include <string>
#include <utility>

class Config;
class CTxMemPool;

namespace chronik_bridge {
enum class NativeReadScope { BoundedFixture, ArchivalRegtestService, HistoricalReadOnly, HistoricalClientService };
// Explicit API admission, never native validation authority.
enum class HistoricalClientAdmission { Explicit };

// Explicit expected public network/genesis, not values silently inferred from
// an index root. Copy this immutable binding before any database/root access.
class HistoricalReadBinding {
    const std::string m_network;
    const BlockHash m_genesis;
    static BlockHash CheckedGenesis(const std::string &hex) {
        if(hex.size()!=64 || hex.find_first_not_of("0123456789abcdef")!=std::string::npos)
            throw std::runtime_error("Historical genesis requires canonical lowercase hex");
        return BlockHash::fromHex(hex);
    }
public:
    HistoricalReadBinding(const Config &config, std::string expected_network,
                          const std::string &expected_genesis)
        :m_network(std::move(expected_network)),m_genesis(CheckedGenesis(expected_genesis)) {
        // regtest is retained solely for the synthetic historical-profile
        // oracle. The dedicated development network has its own native
        // parameters; inherited BCH test profiles remain inadmissible.
        if((m_network!="main" && m_network!="regtest" &&
            m_network!=CBaseChainParams::ERGON_TESTNET) || !Matches(config))
            throw std::runtime_error("Historical native network/genesis mismatch");
    }
    bool Matches(const Config &config) const {
        return config.GetChainParams().NetworkIDString()==m_network &&
            config.GetChainParams().GetConsensus().hashGenesisBlock==m_genesis;
    }
    const std::string &Network() const { return m_network; }
};

/**
 * Small Ergon host binding, not an ABC NodeContext or consensus owner.
 *
 * Construct only after the native node resources exist. The service owner
 * must close admission and join all bridge users before destroying this
 * object or the referenced native resources. Chain and mempool access still
 * require their native locks; const references do not grant safe snapshots.
 *
 * The default scope retains historical fixture limits. Only the explicitly
 * opted-in, joined regtest service owner selects its archival history scope.
 * The distinct historical constructor additionally requires explicit expected
 * network/genesis pins. It grants no transport, client or validation authority.
 */
struct ErgonNodeAccess {
    const Config &config;
    CTxMemPool &mempool;
    mutable std::atomic<bool> index_failed{false};

  private:
    const NativeReadScope m_scope;
    const bool m_clients;
    const std::optional<HistoricalReadBinding> m_historical;

  public:

    ErgonNodeAccess(const Config &config_in, CTxMemPool &mempool_in,
                   NativeReadScope scope = NativeReadScope::BoundedFixture, bool clients = false)
        : config(config_in), mempool(mempool_in), m_scope(scope),
          m_clients(clients && scope == NativeReadScope::ArchivalRegtestService) {
        if(scope!=NativeReadScope::BoundedFixture && scope!=NativeReadScope::ArchivalRegtestService)
            throw std::runtime_error("Historical access requires an explicit network/genesis binding");
    }
    ErgonNodeAccess(const Config &config_in, CTxMemPool &mempool_in,
                   HistoricalReadBinding binding)
        :config(config_in),mempool(mempool_in),m_scope(NativeReadScope::HistoricalReadOnly),
         m_clients(false),m_historical(std::move(binding)) {
        if(!m_historical->Matches(config))
            throw std::runtime_error("Historical binding does not match native config");
    }
    ErgonNodeAccess(const Config &config_in, CTxMemPool &mempool_in,
                   HistoricalReadBinding binding, HistoricalClientAdmission admission)
        :config(config_in),mempool(mempool_in),m_scope(NativeReadScope::HistoricalClientService),
         m_clients(true),m_historical(std::move(binding)) {
        if(admission!=HistoricalClientAdmission::Explicit || !m_historical->Matches(config))
            throw std::runtime_error("Historical client admission does not match native binding");
    }
    bool historical_readonly() const noexcept { return m_scope==NativeReadScope::HistoricalReadOnly; }
    bool historical_clients() const noexcept { return m_scope==NativeReadScope::HistoricalClientService; }
    bool permits_native_network() const {
        return m_historical ? m_historical->Matches(config)
            : config.GetChainParams().NetworkIDString()=="regtest";
    }
    std::string historical_network() const {
        return m_historical ? m_historical->Network() : std::string{};
    }
    bool permits_clients() const noexcept {
        return m_clients && !index_failed.load(std::memory_order_acquire);
    }

    bool permits_height(int height) const noexcept {
        // Retained-window arithmetic uses height + 1 in both C++ and Rust.
        return height >= 0 && height < std::numeric_limits<int32_t>::max() &&
            (m_scope != NativeReadScope::BoundedFixture || height <= 2000);
    }
    bool permits_index_count(size_t count) const noexcept {
        return m_scope != NativeReadScope::BoundedFixture || count <= 4096;
    }

    ErgonNodeAccess(const ErgonNodeAccess &) = delete;
    ErgonNodeAccess &operator=(const ErgonNodeAccess &) = delete;
};

// Build/link integration is not runtime admission. This gate stays closed
// until ordered reconciliation, request admission and joined shutdown have
// passed their runtime tests. There is deliberately no command-line override.
inline bool upstream_runtime_permitted(const ErgonNodeAccess &) noexcept {
    return false;
}

} // namespace chronik_bridge

#endif // ERGON_CHRONIK_CPP_ERGON_NODE_ACCESS_H

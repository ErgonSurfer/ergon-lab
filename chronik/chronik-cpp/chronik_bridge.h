// Copyright (c) 2022 The Bitcoin developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

#ifndef BITCOIN_CHRONIK_CPP_CHRONIK_BRIDGE_H
#define BITCOIN_CHRONIK_CPP_CHRONIK_BRIDGE_H

#include <chronik-cpp/ergon_node_access.h>
#include <chronik-cpp/ergon_position_reader.h>
#include <threadsafety.h>
#include <txmempool.h>

#include <array>
#include <cstddef>
#include <functional>
#include <memory>
#include <rust/cxx.h>
#include <vector>

class CBlock;
class CBlockIndex;
class CBlockUndo;
class Coin;
class Config;
class CTransaction;

namespace Consensus {
struct Params;
} // namespace Consensus

class uint256;

namespace chronik_bridge {

struct BlockInfo;
struct Block;
struct Tx;
struct OutPoint;
struct WrappedBlockHash;
struct RawBlockHeader;

class block_index_not_found : public std::exception {
  public:
    const char *what() const noexcept override {
        return "CBlockIndex not found";
    }
};

class invalid_block_range : public std::exception {
  public:
    const char *what() const noexcept override {
        return "Invalid block range requested";
    }
};

void log_print(const rust::Str logging_function, const rust::Str source_file,
               const uint32_t source_line, const rust::Str msg);

void log_print_chronik(const rust::Str logging_function,
                       const rust::Str source_file, const uint32_t source_line,
                       const rust::Str msg);

/**
 * Bridge to bitcoind to access the node.
 */
class ChronikBridge {
    const ErgonNodeAccess &m_node;
    NativePositionReader m_positions;
    struct StartupSnapshot;
    mutable std::unique_ptr<StartupSnapshot> m_startup;
    mutable bool m_startup_active{false};

  public:
    ChronikBridge(const ErgonNodeAccess &node);
    ~ChronikBridge();
    // Private synchronous frame on the initialization owner thread, before
    // any worker/request can borrow this bridge. Own upstream's ancestors;
    // never hold native locks across DB/disk work. Default finish is strict;
    // only the bounded regtest service retains the old branch across progress.
    // The optional hook is a deterministic native fixture only. It runs after
    // coherent value capture, outside both locks and before graph allocation.
    // The second native-test hook runs after each <=64-row metadata batch,
    // with neither native lock held. No CLI, CXX wire or runtime configuration
    // exposes either hook.
    void capture_startup_snapshot(
        std::function<void()> after_metadata = {},
        std::function<void(size_t)> after_batch = {});
    bool finish_startup_snapshot(bool allow_progress = false) const;
    // Native-only diagnostics/hooks; not an HTTP or CXX wire capability.
    size_t startup_snapshot_index_count_for_testing() const;
    size_t startup_snapshot_retained_bytes_floor_for_testing() const;
    NativePositionStats position_stats() const;
    void position_read_hook_for_testing(std::function<void()> hook);

    const CBlockIndex &get_chain_tip() const;
    uint64_t mempool_revision() const;
    uint64_t chain_generation() const;
    // Nonblocking coherent source check; no DB access or escaped pointer.
    bool matches_source(std::array<uint8_t, 32> hash, int32_t height,
                        uint64_t generation, uint64_t revision) const;

    const CBlockIndex &lookup_block_index(std::array<uint8_t, 32> hash) const;

    const CBlockIndex &lookup_block_index_by_height(int height) const;

    rust::Vec<RawBlockHeader> get_block_headers_by_range(int start,
                                                         int end) const;
    rust::Vec<WrappedBlockHash> get_block_hashes_by_range(int start,
                                                          int end) const;

    std::unique_ptr<CBlock> load_block(const CBlockIndex &bindex) const;

    std::unique_ptr<CBlockUndo>
    load_block_undo(const CBlockIndex &bindex) const;

    Tx load_tx(uint32_t file_num, uint32_t data_pos, uint32_t undo_pos) const;

    rust::Vec<uint8_t> load_raw_tx(uint32_t file_num, uint32_t data_pos) const;

    bool is_avalanche_finalized_preconsensus(
        const std::array<uint8_t, 32> &txid) const
        LOCKS_EXCLUDED(m_node.mempool.cs);

    const CBlockIndex &find_fork(const CBlockIndex &index) const;

    void lookup_spent_coins(Tx &, rust::Vec<OutPoint> &not_found,
                            rust::Vec<OutPoint> &coins_to_uncache) const;
    void uncache_coins(rust::Slice<const OutPoint>) const;

    std::array<uint8_t, 32> broadcast_tx(rust::Slice<const uint8_t> raw_tx,
                                         int64_t max_fee) const;

    void fatal_error(const rust::Str msg, const rust::Str user_msg) const;

    bool shutdown_requested() const;

    WrappedBlockHash get_genesis_hash() const;

    int64_t estimate_feerate_sats_per_kb() const;

    int64_t min_relay_feerate_sats_per_kb() const;

    bool get_feerate_info(std::array<uint8_t, 32> mempool_txid,
                          int64_t &modified_fee_rate_sats_per_kb,
                          uint32_t &virtual_size_bytes) const
        LOCKS_EXCLUDED(m_node.mempool.cs);

    void sync_with_validation_interface_queue() const;
};

std::unique_ptr<ChronikBridge> make_bridge(const ErgonNodeAccess &node);

Tx bridge_tx(const CTransaction &tx, const std::vector<Coin> &spent_coins);

Block bridge_block(const CBlock &block, const CBlockUndo &block_undo,
                   const CBlockIndex &bindex);

BlockInfo get_block_info(const CBlockIndex &index);

std::array<uint8_t, 80> get_block_header(const CBlockIndex &index);

const CBlockIndex &get_block_ancestor(const CBlockIndex &index, int32_t height);

rust::Vec<uint8_t> compress_script(rust::Slice<const uint8_t> script);

rust::Vec<uint8_t> decompress_script(rust::Slice<const uint8_t> compressed);

int64_t calc_fee(size_t num_bytes, int64_t sats_fee_per_kb);

int64_t default_max_raw_tx_fee_rate_per_kb();

bool init_error(const rust::Str msg);

rust::String client_name();

rust::String format_full_version();

} // namespace chronik_bridge

#endif // BITCOIN_CHRONIK_CPP_CHRONIK_BRIDGE_H

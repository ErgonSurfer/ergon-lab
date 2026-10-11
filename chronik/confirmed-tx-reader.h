// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#ifndef BITCOIN_CHRONIK_CONFIRMED_TX_READER_H
#define BITCOIN_CHRONIK_CONFIRMED_TX_READER_H

#include <coins.h>
#include <primitives/blockhash.h>
#include <primitives/transaction.h>

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <vector>

class CBlock;
class CBlockUndo;

namespace chronik {

/**
 * Transaction data recovered from the node's canonical blk/rev files.
 *
 * Chronik owns no duplicate transaction-body store. For non-coinbase
 * transactions, spent_coins is positionally aligned with tx->vin.
 */
struct ConfirmedTx {
    CTransactionRef tx;
    std::vector<Coin> spent_coins;
    BlockHash block_hash;
    int32_t block_height{-1};
    int64_t block_timestamp{0};
    size_t tx_index{0};
};

/**
 * Select and validate one transaction from already loaded block/undo data.
 *
 * The undo payload is required for every non-coinbase transaction. This pure
 * boundary is exposed separately so mismatch and fail-closed behavior can be
 * tested without filesystem fixtures.
 */
std::optional<ConfirmedTx>
SelectConfirmedTx(const CBlock &block, const CBlockUndo *block_undo,
                  const BlockHash &expected_block_hash, int32_t block_height,
                  const TxId &txid, std::string &error);

/**
 * Read one active-chain transaction and its spent coins from node storage.
 *
 * This adapts Chronik's upstream bridge law to Bitcoin Static v24: blk/rev
 * files remain authoritative and missing, pruned or contradictory data fails
 * the query without affecting validation or chain selection.
 */
std::optional<ConfirmedTx> ReadConfirmedTx(const BlockHash &block_hash,
                                           const TxId &txid,
                                           std::string &error);

} // namespace chronik

#endif // BITCOIN_CHRONIK_CONFIRMED_TX_READER_H

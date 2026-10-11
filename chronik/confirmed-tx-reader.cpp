// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#include <chronik/confirmed-tx-reader.h>

#include <chain.h>
#include <chainparams.h>
#include <primitives/block.h>
#include <sync.h>
#include <undo.h>
#include <validation.h>

#include <utility>

namespace chronik {

std::optional<ConfirmedTx>
SelectConfirmedTx(const CBlock &block, const CBlockUndo *block_undo,
                  const BlockHash &expected_block_hash, int32_t block_height,
                  const TxId &txid, std::string &error) {
    if (block.GetHash() != expected_block_hash) {
        error = "loaded block hash does not match the indexed block";
        return std::nullopt;
    }

    for (size_t tx_index = 0; tx_index < block.vtx.size(); ++tx_index) {
        const CTransactionRef &tx = block.vtx[tx_index];
        if (tx->GetId() != txid) {
            continue;
        }

        std::vector<Coin> spent_coins;
        if (tx_index != 0) {
            if (block_undo == nullptr ||
                tx_index - 1 >= block_undo->vtxundo.size()) {
                error = "missing undo data for confirmed transaction";
                return std::nullopt;
            }
            spent_coins = block_undo->vtxundo[tx_index - 1].vprevout;
            if (spent_coins.size() != tx->vin.size()) {
                error = "spent coin count does not match transaction inputs";
                return std::nullopt;
            }
        }

        error.clear();
        return ConfirmedTx{
            tx,
            std::move(spent_coins),
            expected_block_hash,
            block_height,
            block.GetBlockTime(),
            tx_index,
        };
    }

    error = "transaction is absent from the indexed block";
    return std::nullopt;
}

std::optional<ConfirmedTx> ReadConfirmedTx(const BlockHash &block_hash,
                                           const TxId &txid,
                                           std::string &error) {
    const CBlockIndex *block_index = nullptr;
    FlatFilePos block_position;
    CBlockIndex undo_snapshot;
    int32_t block_height = -1;
    {
        LOCK(cs_main);
        const auto it = mapBlockIndex.find(block_hash);
        if (it == mapBlockIndex.end() ||
            !ChainActive().Contains(it->second)) {
            error = "block is not on the active chain";
            return std::nullopt;
        }
        block_index = it->second;
        block_position = block_index->GetBlockPos();
        block_height = block_index->nHeight;
        // UndoReadFromDisk reads nStatus/nFile/nUndoPos without taking
        // cs_main. Pruning can change those fields; give it a detached
        // read-only snapshot instead of the live index. The predecessor's
        // hash is immutable and index entries outlive observer shutdown.
        undo_snapshot.nStatus = block_index->nStatus;
        undo_snapshot.nFile = block_index->nFile;
        undo_snapshot.nUndoPos = block_index->nUndoPos;
        undo_snapshot.pprev = block_index->pprev;
    }

    CBlock block;
    if (!ReadBlockFromDisk(block, block_position,
                           Params().GetConsensus())) {
        error = "block data is unavailable";
        return std::nullopt;
    }

    CBlockUndo block_undo;
    const CBlockUndo *block_undo_ptr = nullptr;
    if (block_height > 0) {
        if (!undo_snapshot.pprev ||
            !UndoReadFromDisk(block_undo, &undo_snapshot)) {
            error = "block undo data is unavailable";
            return std::nullopt;
        }
        block_undo_ptr = &block_undo;
    }

    auto confirmed = SelectConfirmedTx(block, block_undo_ptr, block_hash,
                                       block_height, txid, error);
    if (!confirmed) {
        return std::nullopt;
    }

    // Disk reads happen without cs_main. Re-check the active-chain identity so
    // a concurrent reorganization cannot turn an indexed confirmed query into
    // a silent orphan-block response.
    {
        LOCK(cs_main);
        const auto it = mapBlockIndex.find(block_hash);
        if (it == mapBlockIndex.end() || it->second != block_index ||
            !ChainActive().Contains(block_index)) {
            error = "block left the active chain while loading transaction";
            return std::nullopt;
        }
    }
    return confirmed;
}

} // namespace chronik

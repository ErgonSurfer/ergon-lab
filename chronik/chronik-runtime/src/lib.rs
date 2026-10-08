// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

//! Persistent, reversible token-index runtime core.
//!
//! This crate owns no node callbacks and exposes no network API. It turns an
//! already accepted, ordered block stream into the donor Chronik transaction
//! and token database primitives. The host adapter remains responsible for
//! supplying only active-chain blocks and for treating every runtime error as
//! an indexing failure, never as a consensus decision.

mod query;

use std::path::Path;

use abc_rust_error::Result;
use bitcoinsuite_core::{
    block::BlockHash,
    hash::{Hashed, Sha256d},
    ser::BitcoinSer,
    tx::{Tx, TxId},
};
use bitcoinsuite_slp::{
    structs::{GenesisInfo, TokenMeta},
    verify::SpentToken,
};
use bytes::Bytes;
use chronik_db::{
    db::{Db, WriteBatch},
    index_tx::prepare_indexed_txs,
    io::{
        token::{DbTokenTx, ProcessedTokenTxBatch, TokenReader, TokenWriter},
        BlockReader, BlockTxs, BlockWriter, DbBlock, TxEntry, TxReader, TxWriter, TxsMemData,
    },
};
use thiserror::Error;

pub use query::TokenQueryError;

/// One fully accepted block presented by the node host adapter.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AcceptedBlock {
    /// Block hash in internal byte order.
    pub hash: BlockHash,
    /// Previous block hash in internal byte order.
    pub prev_hash: BlockHash,
    /// Active-chain height, with genesis at zero.
    pub height: i32,
    /// Header timestamp committed by the accepted block.
    pub timestamp: i64,
    /// Transactions in their exact block order.
    pub txs: Vec<Tx>,
}

/// Stable result of one atomic block connection.
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct ConnectSummary {
    /// Height committed to the database.
    pub height: i32,
    /// Number of transactions committed.
    pub transactions: u64,
    /// Number of newly defined token IDs.
    pub new_tokens: u64,
    /// Number of transactions retaining token state, including burns.
    pub token_transactions: u64,
    /// Whether token validation ran for this block.
    pub did_token_validation: bool,
}

/// Persistent token data for one transaction.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct TokenRecord {
    /// Monotonic transaction number in active-chain order.
    pub tx_num: u64,
    /// Token inputs after resolving their token metadata.
    pub spent_tokens: Vec<Option<SpentToken>>,
    /// Compact token inputs and outputs stored by Chronik.
    pub token_tx: DbTokenTx,
    /// Token metadata when this transaction defines a token ID.
    pub token_meta: Option<TokenMeta>,
    /// Genesis payload when this transaction defines a token ID.
    pub genesis_info: Option<GenesisInfo>,
}

/// Fail-closed ordering and identity errors at the node/runtime boundary.
#[derive(Clone, Debug, Eq, Error, PartialEq)]
pub enum RuntimeError {
    /// A serialized block must contain its 80-byte header and tx count.
    #[error("serialized block is shorter than its header and transaction count")]
    SerializedBlockTooShort,

    /// The host-supplied block hash must match the serialized header.
    #[error("serialized block header does not match the supplied block hash")]
    BlockHashMismatch,

    /// A complete block payload may not have trailing bytes.
    #[error("serialized block has trailing bytes")]
    TrailingBlockBytes,

    /// Blocks must contain at least their coinbase transaction.
    #[error("refusing an empty accepted block at height {0}")]
    EmptyBlock(i32),

    /// A fresh database must begin at genesis.
    #[error("fresh token database must begin at height 0, got {0}")]
    InvalidFirstHeight(i32),

    /// Connected blocks must extend the current tip by exactly one.
    #[error("expected block height {expected}, got {actual}")]
    UnexpectedHeight { expected: i32, actual: i32 },

    /// Connected blocks must name the exact current tip as parent.
    #[error("block parent does not match the indexed tip")]
    UnexpectedParent,

    /// Disconnects must name the exact indexed tip.
    #[error("disconnect target is not the indexed tip")]
    NotIndexedTip,

    /// A disconnect body must match every transaction stored for that block.
    #[error("disconnect block transactions do not match the indexed tip")]
    TipTransactionsMismatch,
}

/// RocksDB-backed, atomically updated ALP/SLP token runtime.
#[derive(Debug)]
pub struct PersistentTokenIndexer {
    db: Db,
}

impl PersistentTokenIndexer {
    /// Open or create a token index under `path`.
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        Ok(Self {
            db: Db::open(path)?,
        })
    }

    /// Destroy a closed runtime database before an explicit rebuild.
    pub fn destroy(path: impl AsRef<Path>) -> Result<()> {
        Db::destroy(path)
    }

    /// Return the currently indexed active-chain tip.
    pub fn tip(&self) -> Result<Option<DbBlock>> {
        BlockReader::new(&self.db)?.tip()
    }

    /// Atomically append one accepted active-chain block.
    pub fn connect_block(&self, block: &AcceptedBlock) -> Result<ConnectSummary> {
        if block.txs.is_empty() {
            return Err(RuntimeError::EmptyBlock(block.height).into());
        }
        match self.tip()? {
            None => {
                if block.height != 0 {
                    return Err(RuntimeError::InvalidFirstHeight(block.height).into());
                }
                if block.prev_hash != BlockHash::default() {
                    return Err(RuntimeError::UnexpectedParent.into());
                }
            }
            Some(tip) => {
                let expected = tip
                    .height
                    .checked_add(1)
                    .ok_or(RuntimeError::UnexpectedHeight {
                        expected: tip.height,
                        actual: block.height,
                    })?;
                if block.height != expected {
                    return Err(RuntimeError::UnexpectedHeight {
                        expected,
                        actual: block.height,
                    }
                    .into());
                }
                if block.prev_hash != tip.hash {
                    return Err(RuntimeError::UnexpectedParent.into());
                }
            }
        }

        let db_block = db_block(block);
        let block_txs = block_txs(block);
        let block_writer = BlockWriter::new(&self.db)?;
        let tx_writer = TxWriter::new(&self.db)?;
        let token_writer = TokenWriter::new(&self.db)?;
        let mut batch = WriteBatch::default();
        let mut txs_mem = TxsMemData::default();

        block_writer.insert(&mut batch, &db_block)?;
        let first_tx_num = tx_writer.insert(&mut batch, &block_txs, &mut txs_mem)?;
        let index_txs = prepare_indexed_txs(&self.db, first_tx_num, &block.txs)?;
        let token_batch = token_writer.insert(&mut batch, &index_txs)?;
        let summary = connect_summary(block, &token_batch)?;

        // Block, tx reverse lookups and token ancestry become visible together.
        self.db.write_batch(batch)?;
        Ok(summary)
    }

    /// Atomically remove the exact active-chain tip.
    pub fn disconnect_block(&self, block: &AcceptedBlock) -> Result<()> {
        let Some(tip) = self.tip()? else {
            return Err(RuntimeError::NotIndexedTip.into());
        };
        if tip.height != block.height || tip.hash != block.hash || tip.prev_hash != block.prev_hash
        {
            return Err(RuntimeError::NotIndexedTip.into());
        }
        self.verify_tip_transactions(block)?;

        let db_block = db_block(block);
        let block_txs = block_txs(block);
        let block_writer = BlockWriter::new(&self.db)?;
        let tx_writer = TxWriter::new(&self.db)?;
        let token_writer = TokenWriter::new(&self.db)?;
        let mut batch = WriteBatch::default();
        let mut txs_mem = TxsMemData::default();

        let first_tx_num = tx_writer.delete(&mut batch, &block_txs, &mut txs_mem)?;
        let index_txs = prepare_indexed_txs(&self.db, first_tx_num, &block.txs)?;
        token_writer.delete(&mut batch, &index_txs)?;
        block_writer.delete(&mut batch, &db_block)?;
        self.db.write_batch(batch)?;
        Ok(())
    }

    /// Read persisted token ancestry for one transaction ID.
    pub fn token_record(&self, txid: &TxId) -> Result<Option<TokenRecord>> {
        let tx_reader = TxReader::new(&self.db)?;
        let Some(tx_num) = tx_reader.tx_num_by_txid(txid)? else {
            return Ok(None);
        };
        let token_reader = TokenReader::new(&self.db)?;
        let Some((spent_tokens, token_tx)) = token_reader.spent_tokens_and_db_tx(tx_num)? else {
            return Ok(None);
        };
        Ok(Some(TokenRecord {
            tx_num,
            spent_tokens,
            token_tx,
            token_meta: token_reader.token_meta(tx_num)?,
            genesis_info: token_reader.genesis_info(tx_num)?,
        }))
    }

    /// Read canonical Chronik protobuf metadata for one confirmed token ID.
    pub fn token_info(
        &self,
        token_id_txid: &TxId,
    ) -> Result<Option<chronik_proto::proto::TokenInfo>> {
        query::token_info(&self.db, token_id_txid)
    }

    /// Flush the database before orderly shutdown.
    pub fn close(&self) -> Result<()> {
        self.db.close()
    }

    fn verify_tip_transactions(&self, block: &AcceptedBlock) -> Result<()> {
        let tx_reader = TxReader::new(&self.db)?;
        let Some(range) = tx_reader.block_tx_num_range(block.height)? else {
            return Err(RuntimeError::TipTransactionsMismatch.into());
        };
        if range.end - range.start != u64::try_from(block.txs.len())? {
            return Err(RuntimeError::TipTransactionsMismatch.into());
        }
        for (tx_num, tx) in range.zip(&block.txs) {
            if tx_reader.txid_by_tx_num(tx_num)? != Some(tx.txid()) {
                return Err(RuntimeError::TipTransactionsMismatch.into());
            }
        }
        Ok(())
    }
}

/// Decode the node's canonical network serialization into the runtime input.
pub fn decode_accepted_block(
    hash: [u8; 32],
    height: i32,
    raw_block: &[u8],
) -> Result<AcceptedBlock> {
    if raw_block.len() <= 80 {
        return Err(RuntimeError::SerializedBlockTooShort.into());
    }
    let computed_hash = BlockHash::from(Sha256d::digest(&raw_block[..80]));
    let hash = BlockHash::from(hash);
    if computed_hash != hash {
        return Err(RuntimeError::BlockHashMismatch.into());
    }
    let mut prev_hash = [0; 32];
    prev_hash.copy_from_slice(&raw_block[4..36]);
    let mut tx_bytes = Bytes::copy_from_slice(&raw_block[80..]);
    let txs = Vec::<Tx>::deser(&mut tx_bytes)?;
    if !tx_bytes.is_empty() {
        return Err(RuntimeError::TrailingBlockBytes.into());
    }
    Ok(AcceptedBlock {
        hash,
        prev_hash: BlockHash::from(prev_hash),
        height,
        timestamp: i64::from(u32::from_le_bytes(raw_block[68..72].try_into()?)),
        txs,
    })
}

fn db_block(block: &AcceptedBlock) -> DbBlock {
    DbBlock {
        hash: block.hash.clone(),
        prev_hash: block.prev_hash.clone(),
        height: block.height,
        timestamp: block.timestamp,
        ..Default::default()
    }
}

fn block_txs(block: &AcceptedBlock) -> BlockTxs {
    BlockTxs {
        block_height: block.height,
        txs: block
            .txs
            .iter()
            .enumerate()
            .map(|(index, tx)| TxEntry {
                txid: tx.txid(),
                is_coinbase: index == 0,
                // Runtime token queries never load raw tx bytes from blk/rev
                // files. Keep non-coinbase undo positions nonzero so the
                // donor DB representation preserves its coinbase invariant.
                undo_pos: u32::from(index != 0),
                ..Default::default()
            })
            .collect(),
    }
}

fn connect_summary(
    block: &AcceptedBlock,
    token_batch: &ProcessedTokenTxBatch,
) -> Result<ConnectSummary> {
    Ok(ConnectSummary {
        height: block.height,
        transactions: u64::try_from(block.txs.len())?,
        new_tokens: u64::try_from(token_batch.new_tokens.len())?,
        token_transactions: u64::try_from(token_batch.db_token_txs.len())?,
        did_token_validation: token_batch.did_validation,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use bitcoinsuite_core::{
        script::Script,
        tx::{OutPoint, TxInput, TxMut, TxOutput},
    };
    use bitcoinsuite_slp::{
        alp::{genesis_section, mint_section, sections_opreturn, send_section},
        parsed::ParsedMintData,
        slp::{genesis_opreturn, send_opreturn},
        structs::GenesisInfo,
        token_id::TokenId,
        token_type::{AlpTokenType, SlpTokenType, TokenType},
    };

    fn tx(
        txid: u8,
        inputs: impl IntoIterator<Item = (u8, u32)>,
        output_scripts: impl IntoIterator<Item = Script>,
    ) -> Tx {
        Tx::with_txid(
            TxId::from([txid; 32]),
            TxMut {
                inputs: inputs
                    .into_iter()
                    .map(|(input_txid, out_idx)| TxInput {
                        prev_out: OutPoint {
                            txid: TxId::from([input_txid; 32]),
                            out_idx,
                        },
                        ..Default::default()
                    })
                    .collect(),
                outputs: output_scripts
                    .into_iter()
                    .map(|script| TxOutput { sats: 0, script })
                    .collect(),
                ..Default::default()
            },
        )
    }

    fn scripts(op_return: Script, spendable_outputs: usize) -> Vec<Script> {
        std::iter::once(op_return)
            .chain((0..spendable_outputs).map(|_| Script::EMPTY))
            .collect()
    }

    fn block(hash: u8, prev_hash: u8, height: i32, txs: Vec<Tx>) -> AcceptedBlock {
        AcceptedBlock {
            hash: BlockHash::from([hash; 32]),
            prev_hash: BlockHash::from([prev_hash; 32]),
            height,
            timestamp: i64::from(height.max(0)) + 1_700_000_000,
            txs,
        }
    }

    fn alp_chain() -> (AcceptedBlock, AcceptedBlock, TxId, TxId, TxId, TxId) {
        let genesis_txid = TxId::from([1; 32]);
        let token_id = TokenId::new(genesis_txid);
        let coinbase = tx(0, [], [Script::EMPTY]);
        let genesis = tx(
            1,
            [],
            scripts(
                sections_opreturn(vec![genesis_section(
                    AlpTokenType::Standard,
                    &GenesisInfo::empty_alp(),
                    &ParsedMintData {
                        atoms_vec: vec![100, 0, 25],
                        num_batons: 1,
                    },
                )]),
                4,
            ),
        );
        let mint = tx(
            2,
            [(1, 4)],
            scripts(
                sections_opreturn(vec![mint_section(
                    &token_id,
                    AlpTokenType::Standard,
                    &ParsedMintData {
                        atoms_vec: vec![50],
                        num_batons: 1,
                    },
                )]),
                2,
            ),
        );
        let send = tx(
            3,
            [(1, 1), (1, 3), (2, 1)],
            scripts(
                sections_opreturn(vec![send_section(
                    &token_id,
                    AlpTokenType::Standard,
                    [120, 55],
                )]),
                2,
            ),
        );
        let burn = tx(4, [(3, 1)], [Script::EMPTY]);
        (
            block(10, 0, 0, vec![coinbase, genesis, mint, send]),
            block(11, 10, 1, vec![tx(5, [], [Script::EMPTY]), burn]),
            genesis_txid,
            TxId::from([2; 32]),
            TxId::from([3; 32]),
            TxId::from([4; 32]),
        )
    }

    #[test]
    fn persists_alp_lifecycle_across_restart_and_tip_rollback() -> Result<()> {
        abc_rust_error::install();
        let tempdir = tempdir::TempDir::new("chronik-runtime-alp")?;
        let (block0, block1, genesis, mint, send, burn) = alp_chain();

        {
            let runtime = PersistentTokenIndexer::open(tempdir.path())?;
            assert_eq!(
                runtime.connect_block(&block0)?,
                ConnectSummary {
                    height: 0,
                    transactions: 4,
                    new_tokens: 1,
                    token_transactions: 3,
                    did_token_validation: true,
                },
            );
            let genesis_record = runtime.token_record(&genesis)?.unwrap();
            assert_eq!(
                genesis_record.token_meta.unwrap().token_type,
                TokenType::Alp(AlpTokenType::Standard),
            );
            assert!(genesis_record.genesis_info.is_some());
            assert!(runtime.token_record(&mint)?.is_some());
            assert!(runtime.token_record(&send)?.is_some());
            let token_info = runtime.token_info(&genesis)?.unwrap();
            assert_eq!(token_info.token_id, TokenId::new(genesis).to_string());
            assert_eq!(
                token_info.token_type.unwrap().token_type,
                Some(chronik_proto::proto::token_type::TokenType::Alp(
                    chronik_proto::proto::AlpTokenType::Standard as i32,
                )),
            );
            let block = token_info.block.unwrap();
            assert_eq!(block.height, 0);
            assert_eq!(block.hash, block0.hash.to_vec());
            assert_eq!(block.timestamp, block0.timestamp);
            assert!(!block.is_final);
            assert!(token_info.genesis_info.is_some());
            assert_eq!(runtime.token_info(&mint)?, None);
            runtime.close()?;
        }

        {
            let runtime = PersistentTokenIndexer::open(tempdir.path())?;
            assert_eq!(runtime.tip()?.unwrap().hash, block0.hash);
            assert!(runtime.token_record(&send)?.is_some());
            let summary = runtime.connect_block(&block1)?;
            assert_eq!(summary.height, 1);
            assert_eq!(summary.token_transactions, 1);
            assert!(runtime.token_record(&burn)?.is_some());

            runtime.disconnect_block(&block1)?;
            assert_eq!(runtime.token_record(&burn)?, None);
            assert!(runtime.token_record(&send)?.is_some());
            runtime.disconnect_block(&block0)?;
            assert_eq!(runtime.tip()?, None);
            assert_eq!(runtime.token_record(&genesis)?, None);
            assert_eq!(runtime.token_info(&genesis)?, None);
        }
        Ok(())
    }

    #[test]
    fn rejects_gaps_wrong_parents_and_non_tip_disconnects() -> Result<()> {
        abc_rust_error::install();
        let tempdir = tempdir::TempDir::new("chronik-runtime-order")?;
        let runtime = PersistentTokenIndexer::open(tempdir.path())?;
        let coinbase = tx(0, [], [Script::EMPTY]);

        assert!(runtime
            .connect_block(&block(1, 0, 1, vec![coinbase.clone()]))
            .is_err());
        let genesis = block(1, 0, 0, vec![coinbase.clone()]);
        runtime.connect_block(&genesis)?;
        assert!(runtime
            .connect_block(&block(2, 9, 1, vec![coinbase.clone()]))
            .is_err());
        assert!(runtime
            .connect_block(&block(2, 1, 2, vec![coinbase.clone()]))
            .is_err());
        assert!(runtime
            .disconnect_block(&block(9, 0, 0, vec![coinbase.clone()]))
            .is_err());

        let wrong_body = block(1, 0, 0, vec![tx(9, [], [Script::EMPTY])]);
        assert!(runtime.disconnect_block(&wrong_body).is_err());
        assert_eq!(runtime.tip()?.unwrap().hash, genesis.hash);
        Ok(())
    }

    #[test]
    fn persists_slp_genesis_and_send() -> Result<()> {
        abc_rust_error::install();
        let tempdir = tempdir::TempDir::new("chronik-runtime-slp")?;
        let runtime = PersistentTokenIndexer::open(tempdir.path())?;
        let genesis_txid = TxId::from([21; 32]);
        let token_id = TokenId::new(genesis_txid);
        let genesis = tx(
            21,
            [],
            [
                genesis_opreturn(&GenesisInfo::empty_slp(), SlpTokenType::Fungible, None, 100),
                Script::EMPTY,
            ],
        );
        let send_txid = TxId::from([22; 32]);
        let send = tx(
            22,
            [(21, 1)],
            [
                send_opreturn(&token_id, SlpTokenType::Fungible, &[40, 60]),
                Script::EMPTY,
                Script::EMPTY,
            ],
        );
        let accepted = block(20, 0, 0, vec![tx(0, [], [Script::EMPTY]), genesis, send]);

        let summary = runtime.connect_block(&accepted)?;
        assert_eq!(summary.new_tokens, 1);
        assert_eq!(summary.token_transactions, 2);
        assert_eq!(
            runtime
                .token_record(&genesis_txid)?
                .unwrap()
                .token_meta
                .unwrap()
                .token_type,
            TokenType::Slp(SlpTokenType::Fungible),
        );
        assert!(runtime.token_record(&send_txid)?.is_some());
        let token_info = runtime.token_info(&genesis_txid)?.unwrap();
        assert_eq!(token_info.token_id, token_id.to_string());
        assert_eq!(
            token_info.token_type.unwrap().token_type,
            Some(chronik_proto::proto::token_type::TokenType::Slp(
                chronik_proto::proto::SlpTokenType::Fungible as i32,
            )),
        );
        assert_eq!(token_info.block.unwrap().timestamp, accepted.timestamp);
        assert!(token_info.genesis_info.is_some());
        assert_eq!(runtime.token_info(&send_txid)?, None);
        runtime.disconnect_block(&accepted)?;
        assert_eq!(runtime.token_record(&genesis_txid)?, None);
        assert_eq!(runtime.token_record(&send_txid)?, None);
        assert_eq!(runtime.token_info(&genesis_txid)?, None);
        Ok(())
    }

    #[test]
    fn rejects_token_cycle_without_partial_commit() -> Result<()> {
        abc_rust_error::install();
        let tempdir = tempdir::TempDir::new("chronik-runtime-atomic")?;
        let runtime = PersistentTokenIndexer::open(tempdir.path())?;
        let token_id = TokenId::new(TxId::from([1; 32]));
        let cyclic_genesis = tx(
            1,
            [(2, 1)],
            scripts(
                sections_opreturn(vec![genesis_section(
                    AlpTokenType::Standard,
                    &GenesisInfo::empty_alp(),
                    &ParsedMintData {
                        atoms_vec: vec![1],
                        num_batons: 0,
                    },
                )]),
                1,
            ),
        );
        let cyclic_send = tx(
            2,
            [(1, 1)],
            scripts(
                sections_opreturn(vec![send_section(&token_id, AlpTokenType::Standard, [1])]),
                1,
            ),
        );
        let invalid = block(
            1,
            0,
            0,
            vec![tx(0, [], [Script::EMPTY]), cyclic_genesis, cyclic_send],
        );

        assert!(runtime.connect_block(&invalid).is_err());
        assert_eq!(runtime.tip()?, None);
        assert_eq!(runtime.token_record(&TxId::from([1; 32]))?, None);
        assert_eq!(runtime.token_record(&TxId::from([2; 32]))?, None);
        Ok(())
    }

    #[test]
    fn decodes_exact_node_block_serialization() -> Result<()> {
        let mut header = [0u8; 80];
        header[..4].copy_from_slice(&1i32.to_le_bytes());
        header[4..36].copy_from_slice(&[7; 32]);
        let tx_mut = TxMut {
            outputs: vec![TxOutput {
                sats: 42,
                script: Script::EMPTY,
            }],
            ..Default::default()
        };
        let transaction = Tx::with_txid(TxId::from_tx(&tx_mut), tx_mut);
        let mut serialized = header.to_vec();
        serialized.extend_from_slice(&vec![transaction.clone()].ser());
        let hash = Sha256d::digest(header).to_le_bytes();

        let block = decode_accepted_block(hash, 9, &serialized)?;
        assert_eq!(block.hash, BlockHash::from(hash));
        assert_eq!(block.prev_hash, BlockHash::from([7; 32]));
        assert_eq!(block.height, 9);
        assert_eq!(block.timestamp, 0);
        assert_eq!(block.txs, vec![transaction]);
        Ok(())
    }

    #[test]
    fn rejects_mismatched_truncated_and_trailing_block_payloads() {
        let mut header = [0u8; 80];
        header[..4].copy_from_slice(&1i32.to_le_bytes());
        let hash = Sha256d::digest(header).to_le_bytes();
        let transaction = TxMut::default();
        let mut serialized = header.to_vec();
        serialized.extend_from_slice(&vec![transaction].ser());

        assert!(decode_accepted_block([9; 32], 0, &serialized).is_err());
        assert!(decode_accepted_block(hash, 0, &serialized[..80]).is_err());
        serialized.push(0);
        assert!(decode_accepted_block(hash, 0, &serialized).is_err());
    }
}

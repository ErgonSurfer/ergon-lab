// SPDX-License-Identifier: MIT
// Copyright (c) 2023-2024 The Bitcoin developers
// Copyright (c) 2026 The Ergon developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

//! Canonical confirmed-transaction protobuf construction.
//!
//! This is the confirmed-only subset of Chronik's upstream transaction query
//! path. The node remains the source of transaction bodies and spent coins;
//! the accepted database supplies block identity and token ancestry.

use abc_rust_error::Result;
use bitcoinsuite_core::{
    ser::BitcoinSer,
    tx::{Tx, TxId},
};
use bitcoinsuite_slp::{
    color::ColoredTx,
    structs::{TokenMeta, TxType},
    token_tx::TokenTx,
    token_type::{AlpTokenType, SlpTokenType, TokenType},
    verify::{SpentToken, VerifyContext},
};
use chronik_db::{
    db::Db,
    io::{token::TokenReader, BlockReader, TxReader},
};
use chronik_proto::proto;
use thiserror::Error;

/// Active-chain location bound to one indexed transaction.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ConfirmedTxLocation {
    pub block_hash: [u8; 32],
    pub block_height: i32,
    pub block_timestamp: i64,
    pub is_coinbase: bool,
}

/// Contradictions at the confirmed transaction query boundary.
#[derive(Clone, Debug, Eq, Error, PartialEq)]
pub enum ConfirmedTxQueryError {
    #[error("confirmed transaction is absent from the accepted index")]
    TransactionAbsent,

    #[error("accepted transaction references a missing block")]
    BlockAbsent,

    #[error("node transaction metadata contradicts the accepted index")]
    MetadataMismatch,

    #[error("transaction body does not match its cached txid")]
    TransactionIdentityMismatch,

    #[error("confirmed transaction input is missing its spent coin")]
    SpentCoinAbsent,

    #[error("transaction protobuf size exceeds u32")]
    TransactionTooLarge,
}

struct TxTokenData {
    inputs: Vec<Option<SpentToken>>,
    tx: TokenTx,
}

/// Resolve the block identity the node must use for a confirmed tx disk read.
pub fn confirmed_tx_location(db: &Db, txid: &TxId) -> Result<Option<ConfirmedTxLocation>> {
    let tx_reader = TxReader::new(db)?;
    let Some((_tx_num, block_tx)) = tx_reader.tx_and_num_by_txid(txid)? else {
        return Ok(None);
    };
    let block = BlockReader::new(db)?
        .by_height(block_tx.block_height)?
        .ok_or(ConfirmedTxQueryError::BlockAbsent)?;
    Ok(Some(ConfirmedTxLocation {
        block_hash: block.hash.to_bytes(),
        block_height: block.height,
        block_timestamp: block.timestamp,
        is_coinbase: block_tx.entry.is_coinbase,
    }))
}

/// Build the canonical Chronik `Tx` protobuf for an accepted transaction.
pub fn confirmed_tx_proto(
    db: &Db,
    tx: &Tx,
    supplied_location: &ConfirmedTxLocation,
) -> Result<proto::Tx> {
    if TxId::from_tx(tx) != tx.txid() {
        return Err(ConfirmedTxQueryError::TransactionIdentityMismatch.into());
    }
    let tx_reader = TxReader::new(db)?;
    let Some((tx_num, block_tx)) = tx_reader.tx_and_num_by_txid(tx.txid_ref())? else {
        return Err(ConfirmedTxQueryError::TransactionAbsent.into());
    };
    let block = BlockReader::new(db)?
        .by_height(block_tx.block_height)?
        .ok_or(ConfirmedTxQueryError::BlockAbsent)?;
    let indexed_location = ConfirmedTxLocation {
        block_hash: block.hash.to_bytes(),
        block_height: block.height,
        block_timestamp: block.timestamp,
        is_coinbase: block_tx.entry.is_coinbase,
    };
    if &indexed_location != supplied_location {
        return Err(ConfirmedTxQueryError::MetadataMismatch.into());
    }

    if !indexed_location.is_coinbase && tx.inputs.iter().any(|input| input.coin.is_none()) {
        return Err(ConfirmedTxQueryError::SpentCoinAbsent.into());
    }
    let token = load_token_data(db, tx_num, tx)?;
    let size =
        u32::try_from(tx.ser_len()).map_err(|_| ConfirmedTxQueryError::TransactionTooLarge)?;

    Ok(proto::Tx {
        txid: tx.txid().to_vec(),
        version: tx.version,
        inputs: tx
            .inputs
            .iter()
            .enumerate()
            .map(|(input_idx, input)| {
                let (output_script, sats) = input
                    .coin
                    .as_ref()
                    .map(|coin| (coin.output.script.to_vec(), coin.output.sats))
                    .unwrap_or_default();
                proto::TxInput {
                    prev_out: Some(proto::OutPoint {
                        txid: input.prev_out.txid.to_vec(),
                        out_idx: input.prev_out.out_idx,
                    }),
                    input_script: input.script.to_vec(),
                    output_script,
                    sats,
                    sequence_no: input.sequence,
                    token: token
                        .as_ref()
                        .and_then(|token| input_token_proto(token, input_idx)),
                    plugins: Default::default(),
                }
            })
            .collect(),
        outputs: tx
            .outputs
            .iter()
            .enumerate()
            .map(|(output_idx, output)| proto::TxOutput {
                sats: output.sats,
                output_script: output.script.to_vec(),
                spent_by: None,
                token: token
                    .as_ref()
                    .and_then(|token| output_token_proto(token, output_idx)),
                plugins: Default::default(),
            })
            .collect(),
        lock_time: tx.locktime,
        block: Some(proto::BlockMetadata {
            height: block.height,
            hash: block.hash.to_vec(),
            timestamp: block.timestamp,
            is_final: false,
        }),
        time_first_seen: block_tx.entry.time_first_seen,
        size,
        is_coinbase: block_tx.entry.is_coinbase,
        token_entries: token.as_ref().map_or_else(Vec::new, token_entries_proto),
        token_failed_parsings: token.as_ref().map_or_else(Vec::new, |token| {
            token
                .tx
                .failed_parsings
                .iter()
                .map(|failed| proto::TokenFailedParsing {
                    pushdata_idx: failed.pushdata_idx.map_or(-1, |idx| idx as i32),
                    bytes: failed.bytes.to_vec(),
                    error: failed.error.to_string(),
                })
                .collect()
        }),
        token_status: match &token {
            Some(token)
                if token.tx.failed_parsings.is_empty()
                    && token.tx.entries.iter().all(|entry| entry.is_normal()) =>
            {
                proto::TokenStatus::Normal as _
            }
            Some(_) => proto::TokenStatus::NotNormal as _,
            None => proto::TokenStatus::NonToken as _,
        },
        is_final: false,
    })
}

fn load_token_data(db: &Db, tx_num: u64, tx: &Tx) -> Result<Option<TxTokenData>> {
    let colored = ColoredTx::color_tx(tx);
    let token_reader = TokenReader::new(db)?;
    let (spent_tokens, db_tx) = match token_reader.spent_tokens_and_db_tx(tx_num)? {
        Some(db_data) => db_data,
        None if colored.is_none() => return Ok(None),
        _ => Default::default(),
    };
    let verified = VerifyContext {
        genesis_info: None,
        spent_tokens: &spent_tokens,
        spent_scripts: None,
        override_has_mint_vault: Some(db_tx.has_mint_vault()),
    }
    .verify(colored.unwrap_or_else(|| ColoredTx {
        outputs: vec![None; tx.outputs.len()],
        ..Default::default()
    }));
    Ok(Some(TxTokenData {
        inputs: spent_tokens,
        tx: verified,
    }))
}

fn input_token_proto(token: &TxTokenData, input_idx: usize) -> Option<proto::Token> {
    let spent_token = token.inputs.get(input_idx)?.as_ref()?;
    let value = &spent_token.token;
    let entry_idx = token
        .tx
        .entries
        .iter()
        .position(|entry| entry.meta == value.meta)
        .map_or(-1, |idx| idx as i32);
    Some(proto::Token {
        token_id: value.meta.token_id.to_string(),
        token_type: Some(make_token_type_proto(value.meta)),
        entry_idx,
        atoms: value.variant.atoms(),
        is_mint_baton: value.variant.is_mint_baton(),
    })
}

fn output_token_proto(token: &TxTokenData, output_idx: usize) -> Option<proto::Token> {
    let token_output = token.tx.outputs.get(output_idx)?.as_ref()?;
    let value = token.tx.token(token_output);
    Some(proto::Token {
        token_id: value.meta.token_id.to_string(),
        token_type: Some(make_token_type_proto(value.meta)),
        entry_idx: token_output.token_idx as _,
        atoms: value.variant.atoms(),
        is_mint_baton: value.variant.is_mint_baton(),
    })
}

fn token_entries_proto(token: &TxTokenData) -> Vec<proto::TokenEntry> {
    token
        .tx
        .entries
        .iter()
        .map(|entry| proto::TokenEntry {
            token_id: entry.meta.token_id.to_string(),
            token_type: Some(make_token_type_proto(entry.meta)),
            tx_type: match entry.tx_type {
                Some(TxType::GENESIS) => proto::TokenTxType::Genesis,
                Some(TxType::MINT) => proto::TokenTxType::Mint,
                Some(TxType::SEND) => proto::TokenTxType::Send,
                Some(TxType::UNKNOWN) => proto::TokenTxType::Unknown,
                Some(TxType::BURN) => proto::TokenTxType::Burn,
                None => proto::TokenTxType::None,
            } as _,
            is_invalid: entry.is_invalid,
            group_token_id: entry
                .group_token_meta
                .as_ref()
                .map_or(String::new(), |meta| meta.token_id.to_string()),
            burn_summary: if entry.is_normal() {
                String::new()
            } else {
                entry.burn_summary()
            },
            failed_colorings: entry
                .failed_colorings
                .iter()
                .map(|failed| proto::TokenFailedColoring {
                    pushdata_idx: failed.pushdata_idx as _,
                    error: failed.error.to_string(),
                })
                .collect(),
            actual_burn_atoms: entry.actual_burn_atoms.to_string(),
            intentional_burn_atoms: entry.intentional_burn_atoms.unwrap_or_default(),
            burns_mint_batons: entry.burns_mint_batons,
        })
        .collect()
}

fn make_token_type_proto(meta: TokenMeta) -> proto::TokenType {
    proto::TokenType {
        token_type: Some(match meta.token_type {
            TokenType::Slp(slp) => {
                use proto::SlpTokenType::*;
                proto::token_type::TokenType::Slp(match slp {
                    SlpTokenType::Fungible => Fungible as _,
                    SlpTokenType::MintVault => MintVault as _,
                    SlpTokenType::Nft1Group => Nft1Group as _,
                    SlpTokenType::Nft1Child => Nft1Child as _,
                    SlpTokenType::Unknown(unknown) => unknown as _,
                })
            }
            TokenType::Alp(alp) => {
                use proto::AlpTokenType::*;
                proto::token_type::TokenType::Alp(match alp {
                    AlpTokenType::Standard => Standard as _,
                    AlpTokenType::Unknown(unknown) => unknown as _,
                })
            }
        }),
    }
}

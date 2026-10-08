// Copyright (c) 2024 The Bitcoin developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

//! Read-only protobuf queries over the accepted persistent token index.
//!
//! This is the confirmed-database subset of Chronik's upstream token query
//! path. It deliberately excludes mempool, Avalanche and HTTP concerns while
//! preserving the canonical Chronik protobuf representation.

use abc_rust_error::Result;
use bitcoinsuite_core::{hash::Hashed, tx::TxId};
use bitcoinsuite_slp::{
    structs::GenesisInfo,
    token_type::{AlpTokenType, SlpTokenType, TokenType},
};
use chronik_db::{
    db::Db,
    io::{token::TokenReader, BlockReader, TxReader},
};
use chronik_proto::proto;
use thiserror::Error;

/// Errors indicating an internally inconsistent accepted index.
#[derive(Clone, Debug, Eq, Error, PartialEq)]
pub enum TokenQueryError {
    /// The transaction points to a block that is absent from the block index.
    #[error("inconsistent token index: missing block for height {0}")]
    MissingBlockForHeight(i32),

    /// Genesis data exists without its corresponding token metadata.
    #[error("inconsistent token index: token metadata {0} is absent")]
    TokenMetadataAbsent(u64),
}

/// Read canonical Chronik token information for a confirmed token genesis.
pub fn token_info(db: &Db, token_id_txid: &TxId) -> Result<Option<proto::TokenInfo>> {
    let tx_reader = TxReader::new(db)?;
    let token_reader = TokenReader::new(db)?;
    let block_reader = BlockReader::new(db)?;
    let Some((tx_num, block_tx)) = tx_reader.tx_and_num_by_txid(token_id_txid)? else {
        return Ok(None);
    };
    let Some(genesis_info) = token_reader.genesis_info(tx_num)? else {
        return Ok(None);
    };
    let block = block_reader.by_height(block_tx.block_height)?.ok_or(
        TokenQueryError::MissingBlockForHeight(block_tx.block_height),
    )?;
    let meta = token_reader
        .token_meta(tx_num)?
        .ok_or(TokenQueryError::TokenMetadataAbsent(tx_num))?;
    Ok(Some(proto::TokenInfo {
        token_id: meta.token_id.to_string(),
        token_type: Some(make_token_type_proto(meta.token_type)),
        genesis_info: Some(make_genesis_info_proto(&genesis_info)),
        block: Some(proto::BlockMetadata {
            height: block_tx.block_height,
            hash: block.hash.to_vec(),
            timestamp: block.timestamp,
            // Finalization is outside this confirmed-index-only boundary.
            is_final: false,
        }),
        time_first_seen: block_tx.entry.time_first_seen,
    }))
}

/// Build Chronik's canonical protobuf token type.
pub fn make_token_type_proto(token_type: TokenType) -> proto::TokenType {
    proto::TokenType {
        token_type: Some(match token_type {
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

/// Build Chronik's canonical protobuf genesis payload.
pub fn make_genesis_info_proto(genesis_info: &GenesisInfo) -> proto::GenesisInfo {
    proto::GenesisInfo {
        token_ticker: genesis_info.token_ticker.to_vec(),
        token_name: genesis_info.token_name.to_vec(),
        url: genesis_info.url.to_vec(),
        hash: genesis_info
            .hash
            .as_ref()
            .map_or(vec![], |hash| hash.to_vec()),
        mint_vault_scripthash: genesis_info
            .mint_vault_scripthash
            .map_or(vec![], |hash| hash.to_le_vec()),
        data: genesis_info
            .data
            .as_ref()
            .map_or(vec![], |data| data.to_vec()),
        auth_pubkey: genesis_info
            .auth_pubkey
            .as_ref()
            .map_or(vec![], |pubkey| pubkey.to_vec()),
        decimals: genesis_info.decimals as _,
    }
}

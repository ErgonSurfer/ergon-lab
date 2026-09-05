// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

//! Reconstructible, volatile block projection for the legacy node.
//!
//! This crate has no persistence, networking, APIs, or threads. Its C ABI
//! observes blocks after node validation and never returns a validation
//! decision.

use std::collections::VecDeque;
use std::panic::{catch_unwind, AssertUnwindSafe};

use bitcoinsuite_core::{
    hash::{Hashed, Sha256d},
    ser::BitcoinSer,
    tx::Tx,
};
use bitcoinsuite_slp::{color::ColoredTx, token_type::TokenType};
use bytes::Bytes;

const CONNECTED: u8 = 1;
const DISCONNECTED: u8 = 2;
const CASH_TOKEN_PREFIX: u8 = 0xef;
const FNV_OFFSET_BASIS: u64 = 0xcbf29ce484222325;
const FNV_PRIME: u64 = 0x100000001b3;

/// Result of one connected or disconnected block observation.
///
/// Sequence zero means rejection. Rejection does not mutate the projection or
/// consume a sequence number.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct BlockObservation {
    pub sequence: u64,
    pub fingerprint: u64,
    pub payload_size: u64,
    pub payload_fingerprint: u64,
    pub transaction_count: u64,
    pub slp_family_transactions: u64,
    pub alp_family_transactions: u64,
    pub token_parse_failures: u64,
    pub token_color_failures: u64,
    pub cash_token_prefix_outputs: u64,
    pub projection_blocks: u64,
    pub projection_transactions: u64,
    pub projection_slp_family_transactions: u64,
    pub projection_alp_family_transactions: u64,
    pub projection_token_parse_failures: u64,
    pub projection_token_color_failures: u64,
    pub projection_cash_token_prefix_outputs: u64,
}

const _: () = assert!(std::mem::size_of::<BlockObservation>() == 17 * std::mem::size_of::<u64>());

/// Aggregate result after atomically adopting a rebuilt projection.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct ProjectionObservation {
    pub success: u64,
    pub blocks: u64,
    pub transactions: u64,
    pub slp_family_transactions: u64,
    pub alp_family_transactions: u64,
    pub token_parse_failures: u64,
    pub token_color_failures: u64,
    pub cash_token_prefix_outputs: u64,
}

const _: () =
    assert!(std::mem::size_of::<ProjectionObservation>() == 8 * std::mem::size_of::<u64>());

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct ProjectionTotals {
    blocks: u64,
    transactions: u64,
    slp_family_transactions: u64,
    alp_family_transactions: u64,
    token_parse_failures: u64,
    token_color_failures: u64,
    cash_token_prefix_outputs: u64,
}

impl ProjectionTotals {
    fn checked_add(self, block: &ProjectedBlock) -> Option<Self> {
        Some(Self {
            blocks: self.blocks.checked_add(1)?,
            transactions: self.transactions.checked_add(block.transactions)?,
            slp_family_transactions: self
                .slp_family_transactions
                .checked_add(block.slp_family_transactions)?,
            alp_family_transactions: self
                .alp_family_transactions
                .checked_add(block.alp_family_transactions)?,
            token_parse_failures: self
                .token_parse_failures
                .checked_add(block.token_parse_failures)?,
            token_color_failures: self
                .token_color_failures
                .checked_add(block.token_color_failures)?,
            cash_token_prefix_outputs: self
                .cash_token_prefix_outputs
                .checked_add(block.cash_token_prefix_outputs)?,
        })
    }

    fn checked_sub(self, block: &ProjectedBlock) -> Option<Self> {
        Some(Self {
            blocks: self.blocks.checked_sub(1)?,
            transactions: self.transactions.checked_sub(block.transactions)?,
            slp_family_transactions: self
                .slp_family_transactions
                .checked_sub(block.slp_family_transactions)?,
            alp_family_transactions: self
                .alp_family_transactions
                .checked_sub(block.alp_family_transactions)?,
            token_parse_failures: self
                .token_parse_failures
                .checked_sub(block.token_parse_failures)?,
            token_color_failures: self
                .token_color_failures
                .checked_sub(block.token_color_failures)?,
            cash_token_prefix_outputs: self
                .cash_token_prefix_outputs
                .checked_sub(block.cash_token_prefix_outputs)?,
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ProjectedBlock {
    hash: [u8; 32],
    height: i32,
    transactions: u64,
    slp_family_transactions: u64,
    alp_family_transactions: u64,
    token_parse_failures: u64,
    token_color_failures: u64,
    cash_token_prefix_outputs: u64,
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
struct TokenSummary {
    slp_family_transactions: u64,
    alp_family_transactions: u64,
    token_parse_failures: u64,
    token_color_failures: u64,
    cash_token_prefix_outputs: u64,
}

fn summarize_transactions(transactions: &[Tx]) -> Option<TokenSummary> {
    let mut summary = TokenSummary::default();
    for transaction in transactions {
        if let Some(colored) = ColoredTx::color_tx(transaction) {
            let token_types = colored
                .sections
                .iter()
                .map(|section| section.meta.token_type)
                .chain(
                    colored
                        .intentional_burns
                        .iter()
                        .map(|burn| burn.meta.token_type),
                )
                .chain(
                    colored
                        .failed_colorings
                        .iter()
                        .map(|failure| failure.parsed.meta.token_type),
                );
            let mut saw_slp = false;
            let mut saw_alp = false;
            for token_type in token_types {
                match token_type {
                    TokenType::Slp(_) => saw_slp = true,
                    TokenType::Alp(_) => saw_alp = true,
                }
            }
            summary.slp_family_transactions = summary
                .slp_family_transactions
                .checked_add(u64::from(saw_slp))?;
            summary.alp_family_transactions = summary
                .alp_family_transactions
                .checked_add(u64::from(saw_alp))?;
            summary.token_parse_failures = summary
                .token_parse_failures
                .checked_add(u64::try_from(colored.failed_parsings.len()).ok()?)?;
            summary.token_color_failures = summary
                .token_color_failures
                .checked_add(u64::try_from(colored.failed_colorings.len()).ok()?)?;
        }
        let prefix_outputs = transaction
            .outputs
            .iter()
            .filter(|output| output.script.bytecode().first() == Some(&CASH_TOKEN_PREFIX))
            .count();
        summary.cash_token_prefix_outputs = summary
            .cash_token_prefix_outputs
            .checked_add(u64::try_from(prefix_outputs).ok()?)?;
    }
    Some(summary)
}

fn apply_projection(observation: &mut BlockObservation, projection: ProjectionTotals) {
    observation.projection_blocks = projection.blocks;
    observation.projection_transactions = projection.transactions;
    observation.projection_slp_family_transactions = projection.slp_family_transactions;
    observation.projection_alp_family_transactions = projection.alp_family_transactions;
    observation.projection_token_parse_failures = projection.token_parse_failures;
    observation.projection_token_color_failures = projection.token_color_failures;
    observation.projection_cash_token_prefix_outputs = projection.cash_token_prefix_outputs;
}

#[derive(Debug)]
pub struct Observer {
    sequence: u64,
    blocks: VecDeque<ProjectedBlock>,
    projection: ProjectionTotals,
    max_blocks: usize,
    is_truncated: bool,
    needs_rebuild: bool,
}

impl Observer {
    fn new(max_blocks: usize) -> Self {
        Self {
            sequence: 0,
            blocks: VecDeque::new(),
            projection: ProjectionTotals::default(),
            max_blocks,
            is_truncated: false,
            needs_rebuild: false,
        }
    }

    fn record(&mut self, kind: u8, hash: &[u8; 32], height: i32) -> (u64, u64) {
        self.sequence = self.sequence.wrapping_add(1);
        if self.sequence == 0 {
            self.sequence = 1;
        }
        (self.sequence, event_fingerprint(kind, hash, height))
    }

    fn connect_block(
        &mut self,
        previous_hash: &[u8; 32],
        block: ProjectedBlock,
    ) -> Option<BlockObservation> {
        if self.needs_rebuild || block.height < 0 {
            return None;
        }
        if let Some(tip) = self.blocks.back() {
            if tip.hash != *previous_hash || tip.height.checked_add(1)? != block.height {
                return None;
            }
        }

        let mut next_projection = self.projection.checked_add(&block)?;
        let evicted = if self.blocks.len() == self.max_blocks {
            let evicted = *self.blocks.front()?;
            next_projection = next_projection.checked_sub(&evicted)?;
            Some(evicted)
        } else {
            self.blocks.try_reserve(1).ok()?;
            None
        };

        let first_block = self.blocks.is_empty();
        if evicted.is_some() {
            self.blocks.pop_front();
            self.is_truncated = true;
        }
        self.blocks.push_back(block);
        if first_block {
            self.is_truncated = block.height > 0;
        }
        self.projection = next_projection;

        let (sequence, fingerprint) = self.record(CONNECTED, &block.hash, block.height);
        let mut observation = BlockObservation {
            sequence,
            fingerprint,
            transaction_count: block.transactions,
            slp_family_transactions: block.slp_family_transactions,
            alp_family_transactions: block.alp_family_transactions,
            token_parse_failures: block.token_parse_failures,
            token_color_failures: block.token_color_failures,
            cash_token_prefix_outputs: block.cash_token_prefix_outputs,
            ..Default::default()
        };
        apply_projection(&mut observation, next_projection);
        Some(observation)
    }

    fn disconnect_block(&mut self, hash: &[u8; 32]) -> Option<BlockObservation> {
        if self.needs_rebuild {
            return None;
        }
        let tip = *self.blocks.back()?;
        if tip.hash != *hash {
            return None;
        }

        let next_projection = self.projection.checked_sub(&tip)?;
        self.blocks.pop_back();
        self.projection = next_projection;
        if self.blocks.is_empty() && self.is_truncated {
            self.needs_rebuild = true;
        }

        let (sequence, fingerprint) = self.record(DISCONNECTED, hash, -1);
        let mut observation = BlockObservation {
            sequence,
            fingerprint,
            transaction_count: tip.transactions,
            slp_family_transactions: tip.slp_family_transactions,
            alp_family_transactions: tip.alp_family_transactions,
            token_parse_failures: tip.token_parse_failures,
            token_color_failures: tip.token_color_failures,
            cash_token_prefix_outputs: tip.cash_token_prefix_outputs,
            ..Default::default()
        };
        apply_projection(&mut observation, next_projection);
        Some(observation)
    }
}

fn event_fingerprint(kind: u8, hash: &[u8; 32], height: i32) -> u64 {
    fingerprint_bytes(
        std::iter::once(kind)
            .chain(hash.iter().copied())
            .chain(height.to_le_bytes()),
    )
}

fn fingerprint_bytes(bytes: impl IntoIterator<Item = u8>) -> u64 {
    let mut fingerprint = FNV_OFFSET_BASIS;
    for byte in bytes {
        fingerprint ^= u64::from(byte);
        fingerprint = fingerprint.wrapping_mul(FNV_PRIME);
    }
    fingerprint
}

fn merkle_root(mut layer: Vec<[u8; 32]>) -> Option<[u8; 32]> {
    if layer.is_empty() {
        return None;
    }
    while layer.len() > 1 {
        if layer.len() % 2 != 0 {
            layer.push(*layer.last()?);
        }
        layer = layer
            .chunks_exact(2)
            .map(|pair| {
                let mut children = [0u8; 64];
                children[..32].copy_from_slice(&pair[0]);
                children[32..].copy_from_slice(&pair[1]);
                Sha256d::digest(children).to_le_bytes()
            })
            .collect();
    }
    layer.pop()
}

fn transaction_merkle_root(transactions: &[Tx]) -> Option<[u8; 32]> {
    merkle_root(
        transactions
            .iter()
            .map(|transaction| transaction.txid().to_bytes())
            .collect(),
    )
}

fn observe_block(
    observer: *mut Observer,
    hash: *const u8,
    height: i32,
    raw_block: *const u8,
    raw_block_size: usize,
) -> BlockObservation {
    if observer.is_null()
        || hash.is_null()
        || raw_block.is_null()
        || raw_block_size <= 80
        || raw_block_size > isize::MAX as usize
    {
        return BlockObservation::default();
    }

    catch_unwind(AssertUnwindSafe(|| {
        let mut owned_hash = [0u8; 32];
        // SAFETY: C++ keeps the hash and serialized block alive for this
        // synchronous call; Rust copies both before parsing or mutation.
        unsafe { std::ptr::copy_nonoverlapping(hash, owned_hash.as_mut_ptr(), 32) };
        // SAFETY: The non-null C++ buffer contains raw_block_size initialized
        // bytes and remains alive for this synchronous call.
        let owned_raw_block =
            unsafe { std::slice::from_raw_parts(raw_block, raw_block_size) }.to_vec();

        if Sha256d::digest(&owned_raw_block[..80]).as_le_bytes() != &owned_hash {
            return None;
        }
        let mut previous_hash = [0u8; 32];
        previous_hash.copy_from_slice(&owned_raw_block[4..36]);
        let mut expected_merkle_root = [0u8; 32];
        expected_merkle_root.copy_from_slice(&owned_raw_block[36..68]);

        let payload_size = u64::try_from(owned_raw_block.len()).ok()?;
        let payload_fingerprint = fingerprint_bytes(owned_raw_block.iter().copied());
        let mut serialized_block = Bytes::from(owned_raw_block);
        let _header = serialized_block.split_to(80);
        let transactions = Vec::<Tx>::deser(&mut serialized_block).ok()?;
        if !serialized_block.is_empty()
            || transaction_merkle_root(&transactions)? != expected_merkle_root
        {
            return None;
        }

        let transaction_count = u64::try_from(transactions.len()).ok()?;
        let token_summary = summarize_transactions(&transactions)?;
        let block = ProjectedBlock {
            hash: owned_hash,
            height,
            transactions: transaction_count,
            slp_family_transactions: token_summary.slp_family_transactions,
            alp_family_transactions: token_summary.alp_family_transactions,
            token_parse_failures: token_summary.token_parse_failures,
            token_color_failures: token_summary.token_color_failures,
            cash_token_prefix_outputs: token_summary.cash_token_prefix_outputs,
        };
        // SAFETY: C++ exclusively owns the non-null observer handle and
        // serializes every callback that mutates it.
        let mut observation = unsafe { &mut *observer }.connect_block(&previous_hash, block)?;
        observation.payload_size = payload_size;
        observation.payload_fingerprint = payload_fingerprint;
        Some(observation)
    }))
    .ok()
    .flatten()
    .unwrap_or_default()
}

#[no_mangle]
pub extern "C" fn chronik_observer_create_bounded(max_blocks: u64) -> *mut Observer {
    catch_unwind(AssertUnwindSafe(|| {
        let max_blocks = usize::try_from(max_blocks).ok()?;
        if max_blocks == 0 {
            return None;
        }
        Some(Box::into_raw(Box::new(Observer::new(max_blocks))))
    }))
    .ok()
    .flatten()
    .unwrap_or(std::ptr::null_mut())
}

#[no_mangle]
pub extern "C" fn chronik_observer_destroy(observer: *mut Observer) -> u64 {
    if observer.is_null() {
        return 0;
    }
    catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: C++ returns this handle exactly once after draining and
        // unregistering the validation interface.
        unsafe { Box::from_raw(observer) }.sequence
    }))
    .unwrap_or_default()
}

#[no_mangle]
pub extern "C" fn chronik_observer_requires_rebuild(observer: *const Observer) -> u64 {
    if observer.is_null() {
        return 0;
    }
    catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: The caller keeps this observer alive and serializes access.
        u64::from(unsafe { &*observer }.needs_rebuild)
    }))
    .unwrap_or_default()
}

/// Consume a staging observer and atomically replace the target projection.
///
/// The live event sequence is preserved because rebuilding historical state is
/// not a validation-interface event. Every non-aliasing staging handle is
/// consumed, including when the target or staging projection is invalid.
#[no_mangle]
pub extern "C" fn chronik_observer_adopt_projection(
    observer: *mut Observer,
    rebuilt: *mut Observer,
) -> ProjectionObservation {
    if rebuilt.is_null() || observer == rebuilt {
        return ProjectionObservation::default();
    }

    catch_unwind(AssertUnwindSafe(|| {
        // SAFETY: The caller transfers this distinct staging handle exactly
        // once. The box is dropped on rejection or after its state is moved.
        let mut rebuilt = unsafe { Box::from_raw(rebuilt) };
        if observer.is_null() {
            return None;
        }
        let rebuilt_blocks = u64::try_from(rebuilt.blocks.len()).ok()?;
        // SAFETY: The target is non-null and remains exclusively owned by C++.
        let target = unsafe { &*observer };
        if rebuilt_blocks == 0
            || rebuilt.sequence != rebuilt_blocks
            || rebuilt.projection.blocks != rebuilt_blocks
            || rebuilt.max_blocks != target.max_blocks
            || rebuilt.needs_rebuild
        {
            return None;
        }

        let projection = rebuilt.projection;
        let blocks = std::mem::take(&mut rebuilt.blocks);
        // SAFETY: All fallible validation completed before this exclusive
        // replacement of the target projection.
        let target = unsafe { &mut *observer };
        target.blocks = blocks;
        target.projection = projection;
        target.is_truncated = rebuilt.is_truncated;
        target.needs_rebuild = false;
        Some(ProjectionObservation {
            success: 1,
            blocks: projection.blocks,
            transactions: projection.transactions,
            slp_family_transactions: projection.slp_family_transactions,
            alp_family_transactions: projection.alp_family_transactions,
            token_parse_failures: projection.token_parse_failures,
            token_color_failures: projection.token_color_failures,
            cash_token_prefix_outputs: projection.cash_token_prefix_outputs,
        })
    }))
    .ok()
    .flatten()
    .unwrap_or_default()
}

#[no_mangle]
pub extern "C" fn chronik_observer_block_connected(
    observer: *mut Observer,
    hash: *const u8,
    height: i32,
    raw_block: *const u8,
    raw_block_size: usize,
) -> BlockObservation {
    observe_block(observer, hash, height, raw_block, raw_block_size)
}

#[no_mangle]
pub extern "C" fn chronik_observer_block_disconnected(
    observer: *mut Observer,
    hash: *const u8,
) -> BlockObservation {
    if observer.is_null() || hash.is_null() {
        return BlockObservation::default();
    }

    catch_unwind(AssertUnwindSafe(|| {
        let mut owned_hash = [0u8; 32];
        // SAFETY: C++ keeps the hash alive for this synchronous call; Rust
        // copies it before mutating projection state.
        unsafe { std::ptr::copy_nonoverlapping(hash, owned_hash.as_mut_ptr(), 32) };
        // SAFETY: C++ exclusively owns the non-null observer handle and
        // serializes every callback that mutates it.
        unsafe { &mut *observer }.disconnect_block(&owned_hash)
    }))
    .ok()
    .flatten()
    .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;
    use bitcoinsuite_core::{
        script::Script,
        tx::{TxId, TxMut, TxOutput},
    };
    use bitcoinsuite_slp::{
        alp::{genesis_section, sections_opreturn},
        parsed::ParsedMintData,
        slp::genesis_opreturn,
        structs::GenesisInfo,
        token_type::{AlpTokenType, SlpTokenType},
    };

    fn tx_with_outputs(scripts: impl IntoIterator<Item = Script>) -> Tx {
        let tx = TxMut {
            outputs: scripts
                .into_iter()
                .map(|script| TxOutput { sats: 0, script })
                .collect(),
            ..Default::default()
        };
        Tx::with_txid(TxId::from_tx(&tx), tx)
    }

    fn canonical_default_tx() -> Tx {
        let tx = TxMut::default();
        Tx::with_txid(TxId::from_tx(&tx), tx)
    }

    fn serialized_block_with_parent(
        transactions: Vec<Tx>,
        header_byte: u8,
        previous_hash: [u8; 32],
    ) -> (Vec<u8>, [u8; 32]) {
        let mut raw_block = vec![header_byte; 80];
        raw_block[4..36].copy_from_slice(&previous_hash);
        if let Some(root) = transaction_merkle_root(&transactions) {
            raw_block[36..68].copy_from_slice(&root);
        }
        raw_block.extend(transactions.ser());
        let hash = Sha256d::digest(&raw_block[..80]).to_le_bytes();
        (raw_block, hash)
    }

    fn serialized_block(transactions: Vec<Tx>, header_byte: u8) -> (Vec<u8>, [u8; 32]) {
        serialized_block_with_parent(transactions, header_byte, [0; 32])
    }

    fn observe_connected(
        observer: *mut Observer,
        raw_block: &[u8],
        hash: &[u8; 32],
        height: i32,
    ) -> BlockObservation {
        chronik_observer_block_connected(
            observer,
            hash.as_ptr(),
            height,
            raw_block.as_ptr(),
            raw_block.len(),
        )
    }

    #[test]
    fn computes_fixed_odd_width_merkle_vector() {
        assert_eq!(
            merkle_root(vec![[0; 32], [1; 32], [2; 32]]),
            Some([
                0xd6, 0x38, 0x46, 0x40, 0x76, 0x2f, 0x79, 0x7e, 0xde, 0x7e, 0x7f, 0x13, 0x83, 0x92,
                0x22, 0xf9, 0x45, 0x22, 0x72, 0x80, 0x99, 0x32, 0xcc, 0x60, 0x89, 0xf7, 0x01, 0x33,
                0x1d, 0xf4, 0x55, 0x2d,
            ]),
        );
    }

    #[test]
    fn rejects_malformed_payloads_without_mutating_projection() {
        let observer = chronik_observer_create_bounded(2);
        let (raw_block, hash) = serialized_block(vec![canonical_default_tx()], 0x11);

        let wrong_hash = [0x22; 32];
        assert_eq!(
            observe_connected(observer, &raw_block, &wrong_hash, 7),
            BlockObservation::default(),
        );

        let mut wrong_merkle = raw_block.clone();
        wrong_merkle[36] ^= 1;
        let wrong_merkle_hash = Sha256d::digest(&wrong_merkle[..80]).to_le_bytes();
        assert_eq!(
            observe_connected(observer, &wrong_merkle, &wrong_merkle_hash, 7),
            BlockObservation::default(),
        );

        let mut trailing = raw_block.clone();
        trailing.push(0);
        assert_eq!(
            observe_connected(observer, &trailing, &hash, 7),
            BlockObservation::default(),
        );

        let malformed = &raw_block[..raw_block.len() - 1];
        assert_eq!(
            observe_connected(observer, malformed, &hash, 7),
            BlockObservation::default(),
        );

        let short = [0u8; 80];
        assert_eq!(
            observe_connected(observer, &short, &hash, 7),
            BlockObservation::default(),
        );

        let (empty_block, empty_hash) = serialized_block(Vec::new(), 0x33);
        assert_eq!(
            observe_connected(observer, &empty_block, &empty_hash, 7),
            BlockObservation::default(),
        );

        let accepted = observe_connected(observer, &raw_block, &hash, 7);
        assert_eq!(accepted.sequence, 1);
        assert_eq!(accepted.fingerprint, event_fingerprint(CONNECTED, &hash, 7));
        assert_eq!(accepted.payload_size, raw_block.len() as u64);
        assert_eq!(
            accepted.payload_fingerprint,
            fingerprint_bytes(raw_block.iter().copied()),
        );
        assert_eq!(accepted.transaction_count, 1);
        assert_eq!(accepted.projection_blocks, 1);
        assert_eq!(accepted.projection_transactions, 1);
        assert_eq!(chronik_observer_destroy(observer), 1);
    }

    #[test]
    fn connects_and_disconnects_only_the_exact_tip() {
        let observer = chronik_observer_create_bounded(4);
        let (raw_block_1, hash_1) = serialized_block(vec![canonical_default_tx()], 0x41);
        let connected_1 = observe_connected(observer, &raw_block_1, &hash_1, 7);
        assert_eq!(connected_1.sequence, 1);
        assert_eq!(connected_1.projection_transactions, 1);

        let (raw_block_2, hash_2) = serialized_block_with_parent(
            vec![canonical_default_tx(), canonical_default_tx()],
            0x42,
            hash_1,
        );
        let connected_2 = observe_connected(observer, &raw_block_2, &hash_2, 8);
        assert_eq!(connected_2.sequence, 2);
        assert_eq!(connected_2.projection_blocks, 2);
        assert_eq!(connected_2.projection_transactions, 3);

        let (wrong_parent, wrong_parent_hash) =
            serialized_block_with_parent(vec![canonical_default_tx()], 0x43, [0x55; 32]);
        assert_eq!(
            observe_connected(observer, &wrong_parent, &wrong_parent_hash, 9),
            BlockObservation::default(),
        );
        let (wrong_height, wrong_height_hash) =
            serialized_block_with_parent(vec![canonical_default_tx()], 0x44, hash_2);
        assert_eq!(
            observe_connected(observer, &wrong_height, &wrong_height_hash, 10),
            BlockObservation::default(),
        );
        assert_eq!(
            chronik_observer_block_disconnected(observer, hash_1.as_ptr()),
            BlockObservation::default(),
        );

        let disconnected_2 = chronik_observer_block_disconnected(observer, hash_2.as_ptr());
        assert_eq!(disconnected_2.sequence, 3);
        assert_eq!(disconnected_2.transaction_count, 2);
        assert_eq!(disconnected_2.projection_blocks, 1);
        assert_eq!(disconnected_2.projection_transactions, 1);
        let disconnected_1 = chronik_observer_block_disconnected(observer, hash_1.as_ptr());
        assert_eq!(disconnected_1.sequence, 4);
        assert_eq!(disconnected_1.projection_blocks, 0);
        assert_eq!(disconnected_1.projection_transactions, 0);
        assert_eq!(chronik_observer_destroy(observer), 4);
    }

    #[test]
    fn projects_observed_asset_families_and_prefix_candidates() {
        let observer = chronik_observer_create_bounded(2);
        let slp = tx_with_outputs([
            genesis_opreturn(&GenesisInfo::empty_slp(), SlpTokenType::Fungible, None, 1),
            Script::EMPTY,
        ]);
        let alp = tx_with_outputs([
            sections_opreturn(vec![genesis_section(
                AlpTokenType::Standard,
                &GenesisInfo::empty_alp(),
                &ParsedMintData {
                    atoms_vec: vec![1],
                    num_batons: 0,
                },
            )]),
            Script::EMPTY,
        ]);
        let alp_color_failure = tx_with_outputs([sections_opreturn(vec![genesis_section(
            AlpTokenType::Standard,
            &GenesisInfo::empty_alp(),
            &ParsedMintData {
                atoms_vec: vec![1],
                num_batons: 0,
            },
        )])]);
        let malformed_slp =
            tx_with_outputs([Script::new(vec![0x6a, 0x04, b'S', b'L', b'P', 0].into())]);
        let prefix_candidates = tx_with_outputs([
            Script::new(vec![CASH_TOKEN_PREFIX].into()),
            Script::new(vec![CASH_TOKEN_PREFIX, 0x01].into()),
            Script::new(vec![0x51, CASH_TOKEN_PREFIX].into()),
        ]);

        let (raw_block_1, hash_1) = serialized_block(vec![slp], 0x45);
        let connected_1 = observe_connected(observer, &raw_block_1, &hash_1, 7);
        assert_eq!(connected_1.slp_family_transactions, 1);
        assert_eq!(connected_1.alp_family_transactions, 0);
        assert_eq!(connected_1.projection_slp_family_transactions, 1);

        let (raw_block_2, hash_2) = serialized_block_with_parent(
            vec![alp, alp_color_failure, malformed_slp, prefix_candidates],
            0x46,
            hash_1,
        );
        let connected_2 = observe_connected(observer, &raw_block_2, &hash_2, 8);
        assert_eq!(connected_2.transaction_count, 4);
        assert_eq!(connected_2.slp_family_transactions, 0);
        assert_eq!(connected_2.alp_family_transactions, 2);
        assert_eq!(connected_2.token_parse_failures, 1);
        assert_eq!(connected_2.token_color_failures, 1);
        assert_eq!(connected_2.cash_token_prefix_outputs, 2);
        assert_eq!(connected_2.projection_slp_family_transactions, 1);
        assert_eq!(connected_2.projection_alp_family_transactions, 2);
        assert_eq!(connected_2.projection_token_parse_failures, 1);
        assert_eq!(connected_2.projection_token_color_failures, 1);
        assert_eq!(connected_2.projection_cash_token_prefix_outputs, 2);

        let prefix_only = tx_with_outputs([Script::new(vec![CASH_TOKEN_PREFIX].into())]);
        let (raw_block_3, hash_3) = serialized_block_with_parent(vec![prefix_only], 0x47, hash_2);
        let connected_3 = observe_connected(observer, &raw_block_3, &hash_3, 9);
        assert_eq!(connected_3.cash_token_prefix_outputs, 1);
        assert_eq!(connected_3.projection_blocks, 2);
        assert_eq!(connected_3.projection_transactions, 5);
        assert_eq!(connected_3.projection_slp_family_transactions, 0);
        assert_eq!(connected_3.projection_alp_family_transactions, 2);
        assert_eq!(connected_3.projection_token_parse_failures, 1);
        assert_eq!(connected_3.projection_token_color_failures, 1);
        assert_eq!(connected_3.projection_cash_token_prefix_outputs, 3);

        let disconnected_3 = chronik_observer_block_disconnected(observer, hash_3.as_ptr());
        assert_eq!(disconnected_3.projection_blocks, 1);
        assert_eq!(disconnected_3.projection_transactions, 4);
        assert_eq!(disconnected_3.projection_alp_family_transactions, 2);
        assert_eq!(disconnected_3.projection_token_parse_failures, 1);
        assert_eq!(disconnected_3.projection_token_color_failures, 1);
        assert_eq!(disconnected_3.projection_cash_token_prefix_outputs, 2);

        let disconnected_2 = chronik_observer_block_disconnected(observer, hash_2.as_ptr());
        assert_eq!(disconnected_2.alp_family_transactions, 2);
        assert_eq!(disconnected_2.token_parse_failures, 1);
        assert_eq!(disconnected_2.token_color_failures, 1);
        assert_eq!(disconnected_2.cash_token_prefix_outputs, 2);
        assert_eq!(disconnected_2.projection_slp_family_transactions, 0);
        assert_eq!(disconnected_2.projection_alp_family_transactions, 0);
        assert_eq!(disconnected_2.projection_token_parse_failures, 0);
        assert_eq!(disconnected_2.projection_token_color_failures, 0);
        assert_eq!(disconnected_2.projection_cash_token_prefix_outputs, 0);
        assert_eq!(chronik_observer_requires_rebuild(observer), 1);
        assert_eq!(
            observe_connected(observer, &raw_block_1, &hash_1, 7),
            BlockObservation::default()
        );
        assert_eq!(chronik_observer_destroy(observer), 5);
    }

    #[test]
    fn bounded_projection_fails_closed_beyond_its_anchor() {
        let observer = chronik_observer_create_bounded(2);
        let (raw_block_1, hash_1) = serialized_block(vec![canonical_default_tx()], 0x51);
        let (raw_block_2, hash_2) = serialized_block_with_parent(
            vec![canonical_default_tx(), canonical_default_tx()],
            0x52,
            hash_1,
        );
        let (raw_block_3, hash_3) =
            serialized_block_with_parent(vec![canonical_default_tx(); 3], 0x53, hash_2);
        assert_eq!(
            observe_connected(observer, &raw_block_1, &hash_1, 7).sequence,
            1
        );
        assert_eq!(
            observe_connected(observer, &raw_block_2, &hash_2, 8).sequence,
            2
        );
        let connected_3 = observe_connected(observer, &raw_block_3, &hash_3, 9);
        assert_eq!(connected_3.sequence, 3);
        assert_eq!(connected_3.projection_blocks, 2);
        assert_eq!(connected_3.projection_transactions, 5);

        let disconnected_3 = chronik_observer_block_disconnected(observer, hash_3.as_ptr());
        assert_eq!(disconnected_3.sequence, 4);
        assert_eq!(disconnected_3.projection_transactions, 2);
        let disconnected_2 = chronik_observer_block_disconnected(observer, hash_2.as_ptr());
        assert_eq!(disconnected_2.sequence, 5);
        assert_eq!(disconnected_2.projection_blocks, 0);
        assert_eq!(chronik_observer_requires_rebuild(observer), 1);

        assert_eq!(
            chronik_observer_block_disconnected(observer, hash_1.as_ptr()),
            BlockObservation::default(),
        );
        let (replacement, replacement_hash) =
            serialized_block_with_parent(vec![canonical_default_tx()], 0x54, hash_1);
        assert_eq!(
            observe_connected(observer, &replacement, &replacement_hash, 8),
            BlockObservation::default(),
        );
        assert_eq!(chronik_observer_destroy(observer), 5);
    }

    #[test]
    fn atomically_adopts_projection_without_minting_live_events() {
        let observer = chronik_observer_create_bounded(2);
        let (old_block, old_hash) = serialized_block(vec![canonical_default_tx()], 0x61);
        assert_eq!(
            observe_connected(observer, &old_block, &old_hash, 20).sequence,
            1
        );
        assert_eq!(
            chronik_observer_block_disconnected(observer, old_hash.as_ptr()).sequence,
            2,
        );
        assert_eq!(chronik_observer_requires_rebuild(observer), 1);

        let rebuilt = chronik_observer_create_bounded(2);
        let (raw_block_1, hash_1) = serialized_block(vec![canonical_default_tx()], 0x62);
        let (raw_block_2, hash_2) = serialized_block_with_parent(
            vec![canonical_default_tx(), canonical_default_tx()],
            0x63,
            hash_1,
        );
        assert_eq!(
            observe_connected(rebuilt, &raw_block_1, &hash_1, 7).sequence,
            1
        );
        assert_eq!(
            observe_connected(rebuilt, &raw_block_2, &hash_2, 8).sequence,
            2
        );
        assert_eq!(
            chronik_observer_adopt_projection(observer, rebuilt),
            ProjectionObservation {
                success: 1,
                blocks: 2,
                transactions: 3,
                ..Default::default()
            },
        );
        assert_eq!(chronik_observer_requires_rebuild(observer), 0);
        let disconnected = chronik_observer_block_disconnected(observer, hash_2.as_ptr());
        assert_eq!(disconnected.sequence, 3);
        assert_eq!(disconnected.projection_transactions, 1);
        assert_eq!(chronik_observer_destroy(observer), 3);
    }

    #[test]
    fn rejected_adoption_preserves_the_target() {
        let observer = chronik_observer_create_bounded(2);
        let (raw_block, hash) = serialized_block(vec![canonical_default_tx()], 0x71);
        assert_eq!(
            observe_connected(observer, &raw_block, &hash, 7).sequence,
            1
        );

        let empty = chronik_observer_create_bounded(2);
        assert_eq!(
            chronik_observer_adopt_projection(observer, empty),
            ProjectionObservation::default(),
        );
        let wrong_bound = chronik_observer_create_bounded(1);
        let (other_block, other_hash) = serialized_block(vec![canonical_default_tx()], 0x72);
        assert_eq!(
            observe_connected(wrong_bound, &other_block, &other_hash, 9).sequence,
            1
        );
        assert_eq!(
            chronik_observer_adopt_projection(observer, wrong_bound),
            ProjectionObservation::default(),
        );
        assert_eq!(
            chronik_observer_adopt_projection(observer, observer),
            ProjectionObservation::default(),
        );

        let disconnected = chronik_observer_block_disconnected(observer, hash.as_ptr());
        assert_eq!(disconnected.sequence, 2);
        assert_eq!(disconnected.projection_blocks, 0);
        assert_eq!(chronik_observer_destroy(observer), 2);
    }

    #[test]
    fn rejects_null_and_zero_bound_inputs() {
        assert!(chronik_observer_create_bounded(0).is_null());
        assert_eq!(chronik_observer_requires_rebuild(std::ptr::null()), 0);
        assert_eq!(chronik_observer_destroy(std::ptr::null_mut()), 0);

        let observer = chronik_observer_create_bounded(2);
        let (raw_block, hash) = serialized_block(vec![canonical_default_tx()], 0x81);
        assert_eq!(
            chronik_observer_block_connected(
                std::ptr::null_mut(),
                hash.as_ptr(),
                7,
                raw_block.as_ptr(),
                raw_block.len(),
            ),
            BlockObservation::default(),
        );
        assert_eq!(
            chronik_observer_block_connected(
                observer,
                std::ptr::null(),
                7,
                raw_block.as_ptr(),
                raw_block.len(),
            ),
            BlockObservation::default(),
        );
        assert_eq!(
            chronik_observer_block_connected(
                observer,
                hash.as_ptr(),
                7,
                std::ptr::null(),
                raw_block.len(),
            ),
            BlockObservation::default(),
        );
        assert_eq!(
            chronik_observer_block_disconnected(observer, std::ptr::null()),
            BlockObservation::default(),
        );
        assert_eq!(
            chronik_observer_adopt_projection(observer, std::ptr::null_mut()),
            ProjectionObservation::default(),
        );

        let staging = chronik_observer_create_bounded(2);
        assert_eq!(
            chronik_observer_adopt_projection(std::ptr::null_mut(), staging),
            ProjectionObservation::default(),
        );
        assert_eq!(chronik_observer_destroy(observer), 0);
    }
}

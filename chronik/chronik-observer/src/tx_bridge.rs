// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

//! Bounded confirmed-transaction C ABI. All pointer allocations remain owned
//! by the C++ caller and alive for the entire call, including sizing probes.

use crate::persistent::{indexer_ref, RuntimeQuery};
use bitcoinsuite_core::{
    script::Script,
    ser::BitcoinSer,
    tx::{Coin, Tx, TxId, TxMut, TxOutput},
};
use bytes::Bytes;
use chronik_runtime::ConfirmedTxLocation;
use prost::Message;
use std::{
    ffi::c_void,
    panic::{catch_unwind, AssertUnwindSafe},
    ptr, slice,
};

const MAX_BYTES: usize = 64 * 1024 * 1024;
const MAX_COINS: usize = 1_000_000;
const INVALID: u64 = 2;
const PAYLOAD: u64 = 4;
const RUNTIME: u64 = 5;
const PANIC: u64 = 6;
const SMALL_BUFFER: u64 = 7;

#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct RuntimeTxLocation {
    pub success: u64,
    pub error_code: u64,
    pub present: u64,
    pub height: i64,
    pub timestamp: i64,
    pub is_coinbase: u64,
    pub hash: [u8; 32],
}

#[repr(C)]
pub struct RuntimeSpentCoin {
    pub sats: i64,
    pub height: i32,
    pub is_coinbase: u32,
    pub script: *const u8,
    pub script_len: usize,
}

const _: () = assert!(std::mem::size_of::<RuntimeTxLocation>() == 80);
const _: () = assert!(std::mem::size_of::<RuntimeSpentCoin>() == 32);

#[no_mangle]
pub extern "C" fn chronik_runtime_tx_location(
    runtime: *const c_void,
    txid: *const u8,
) -> RuntimeTxLocation {
    catch_unwind(AssertUnwindSafe(|| {
        let Some(indexer) = indexer_ref(runtime) else {
            return location_error(INVALID);
        };
        if txid.is_null() {
            return location_error(INVALID);
        }
        // SAFETY: caller supplies exactly 32 readable txid bytes.
        let txid = TxId::from(unsafe { *(txid.cast::<[u8; 32]>()) });
        match indexer.confirmed_tx_location(&txid) {
            Ok(Some(location)) => RuntimeTxLocation {
                success: 1,
                present: 1,
                height: location.block_height.into(),
                timestamp: location.block_timestamp,
                is_coinbase: location.is_coinbase.into(),
                hash: location.block_hash,
                ..Default::default()
            },
            Ok(None) => RuntimeTxLocation {
                success: 1,
                ..Default::default()
            },
            Err(_) => location_error(RUNTIME),
        }
    }))
    .unwrap_or_else(|_| location_error(PANIC))
}

#[no_mangle]
pub extern "C" fn chronik_runtime_confirmed_tx(
    runtime: *const c_void,
    raw_tx: *const u8,
    raw_tx_len: usize,
    coins: *const RuntimeSpentCoin,
    coin_count: usize,
    location: *const RuntimeTxLocation,
    output: *mut u8,
    output_capacity: usize,
) -> RuntimeQuery {
    catch_unwind(AssertUnwindSafe(|| {
        let Some(indexer) = indexer_ref(runtime) else {
            return query_error(INVALID);
        };
        if raw_tx.is_null()
            || raw_tx_len == 0
            || raw_tx_len > MAX_BYTES
            || coin_count > MAX_COINS
            || coins.is_null() != (coin_count == 0)
            || location.is_null()
            || output.is_null() != (output_capacity == 0)
        {
            return query_error(INVALID);
        }
        // SAFETY: caller supplies a valid, aligned, live location structure.
        let location = unsafe { &*location };
        let Ok(block_height) = i32::try_from(location.height) else {
            return query_error(INVALID);
        };
        if location.success != 1
            || location.present != 1
            || location.error_code != 0
            || location.is_coinbase > 1
            || block_height < 0
        {
            return query_error(INVALID);
        }
        // SAFETY: the C++ caller owns raw_tx_len immutable readable bytes.
        let mut bytes =
            Bytes::copy_from_slice(unsafe { slice::from_raw_parts(raw_tx, raw_tx_len) });
        let Ok(tx) = Tx::deser(&mut bytes) else {
            return query_error(PAYLOAD);
        };
        if !bytes.is_empty() {
            return query_error(PAYLOAD);
        }
        let is_coinbase = tx.inputs.len() == 1 && tx.inputs[0].prev_out.is_coinbase();
        if is_coinbase != (location.is_coinbase == 1)
            || (is_coinbase && coin_count != 0)
            || (!is_coinbase && coin_count != tx.inputs.len())
        {
            return query_error(PAYLOAD);
        }
        let txid = tx.txid();
        let mut tx = TxMut::from(tx);
        let coins = if coin_count == 0 {
            &[][..]
        } else {
            // SAFETY: caller provides coin_count aligned, live structures.
            unsafe { slice::from_raw_parts(coins, coin_count) }
        };
        let mut total_script_bytes = 0usize;
        for (input, coin) in tx.inputs.iter_mut().zip(coins) {
            total_script_bytes = match total_script_bytes.checked_add(coin.script_len) {
                Some(total) if total <= MAX_BYTES => total,
                _ => return query_error(INVALID),
            };
            if coin.is_coinbase > 1
                || coin.height < 0
                || coin.sats < 0
                || coin.script.is_null() != (coin.script_len == 0)
            {
                return query_error(INVALID);
            }
            let script = if coin.script_len == 0 {
                Bytes::new()
            } else {
                // SAFETY: caller provides script_len readable bytes for each coin.
                Bytes::copy_from_slice(unsafe {
                    slice::from_raw_parts(coin.script, coin.script_len)
                })
            };
            input.coin = Some(Coin {
                output: TxOutput {
                    sats: coin.sats,
                    script: Script::new(script),
                },
                height: coin.height,
                is_coinbase: coin.is_coinbase == 1,
            });
        }
        let location = ConfirmedTxLocation {
            block_hash: location.hash,
            block_height,
            block_timestamp: location.timestamp,
            is_coinbase,
        };
        let proto = match indexer.confirmed_tx_proto(&Tx::with_txid(txid, tx), &location) {
            Ok(proto) => proto,
            Err(_) => return query_error(RUNTIME),
        };
        let payload = proto.encode_to_vec();
        if payload.len() > MAX_BYTES {
            return query_error(PAYLOAD);
        }
        let result = RuntimeQuery {
            success: 1,
            present: 1,
            payload_len: payload.len() as u64,
            error_code: 0,
        };
        if output_capacity == 0 {
            return result;
        }
        if output_capacity < payload.len() {
            return RuntimeQuery {
                success: 0,
                error_code: SMALL_BUFFER,
                ..result
            };
        }
        // SAFETY: caller owns output_capacity writable bytes, checked above.
        unsafe {
            ptr::copy_nonoverlapping(payload.as_ptr(), output, payload.len());
        }
        result
    }))
    .unwrap_or_else(|_| query_error(PANIC))
}

fn location_error(error_code: u64) -> RuntimeTxLocation {
    RuntimeTxLocation {
        error_code,
        ..Default::default()
    }
}
fn query_error(error_code: u64) -> RuntimeQuery {
    RuntimeQuery {
        error_code,
        ..Default::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::persistent::{chronik_runtime_create, chronik_runtime_destroy};
    use bitcoinsuite_core::{
        block::BlockHash,
        tx::{OutPoint, TxInput},
    };
    use bitcoinsuite_slp::{
        alp::{genesis_section, sections_opreturn, send_section},
        parsed::ParsedMintData,
        structs::GenesisInfo,
        token_id::TokenId,
        token_type::AlpTokenType,
    };
    use chronik_proto::proto;
    use chronik_runtime::AcceptedBlock;

    fn hashed(tx: TxMut) -> Tx {
        Tx::with_txid(TxId::from_tx(&tx), tx)
    }

    #[test]
    fn bounds_null_arguments_before_dereferencing() {
        assert_eq!(
            chronik_runtime_tx_location(ptr::null(), ptr::null()).error_code,
            INVALID
        );
        assert_eq!(
            chronik_runtime_confirmed_tx(
                ptr::null(),
                ptr::null(),
                0,
                ptr::null(),
                0,
                ptr::null(),
                ptr::null_mut(),
                0
            )
            .error_code,
            INVALID
        );
    }

    #[test]
    fn canonical_alp_bytes_buffers_and_contradictions() {
        let tempdir = tempdir::TempDir::new("chronik-confirmed-bridge").unwrap();
        let path = tempdir.path().to_str().unwrap().as_bytes();
        let runtime = chronik_runtime_create(path.as_ptr(), path.len(), 0);
        assert!(!runtime.is_null());
        let coinbase = hashed(TxMut {
            inputs: vec![TxInput {
                prev_out: OutPoint {
                    txid: TxId::default(),
                    out_idx: u32::MAX,
                },
                ..Default::default()
            }],
            outputs: vec![TxOutput::default()],
            ..Default::default()
        });
        let genesis = hashed(TxMut {
            outputs: vec![
                TxOutput {
                    sats: 0,
                    script: sections_opreturn(vec![genesis_section(
                        AlpTokenType::Standard,
                        &GenesisInfo::empty_alp(),
                        &ParsedMintData {
                            atoms_vec: vec![42],
                            num_batons: 0,
                        },
                    )]),
                },
                TxOutput::default(),
            ],
            ..Default::default()
        });
        let send = hashed(TxMut {
            inputs: vec![TxInput {
                prev_out: OutPoint {
                    txid: genesis.txid(),
                    out_idx: 1,
                },
                ..Default::default()
            }],
            outputs: vec![
                TxOutput {
                    sats: 0,
                    script: sections_opreturn(vec![send_section(
                        &TokenId::new(genesis.txid()),
                        AlpTokenType::Standard,
                        [42],
                    )]),
                },
                TxOutput::default(),
            ],
            ..Default::default()
        });
        let block = AcceptedBlock {
            hash: BlockHash::from([10; 32]),
            prev_hash: BlockHash::default(),
            height: 0,
            timestamp: 12345,
            txs: vec![coinbase.clone(), genesis, send.clone()],
        };
        indexer_ref(runtime).unwrap().connect_block(&block).unwrap();
        let location = chronik_runtime_tx_location(runtime, send.txid().as_bytes().as_ptr());
        assert_eq!(location.present, 1);
        let raw = send.ser();
        let script = [0x51];
        let mut coin = RuntimeSpentCoin {
            sats: 7,
            height: 0,
            is_coinbase: 0,
            script: script.as_ptr(),
            script_len: script.len(),
        };
        let probe = chronik_runtime_confirmed_tx(
            runtime,
            raw.as_ptr(),
            raw.len(),
            &coin,
            1,
            &location,
            ptr::null_mut(),
            0,
        );
        assert_eq!(probe.success, 1);
        assert_eq!(
            chronik_runtime_tx_location(runtime, ptr::null()).error_code,
            INVALID
        );
        assert_eq!(
            chronik_runtime_confirmed_tx(
                runtime,
                raw.as_ptr(),
                raw.len(),
                &coin,
                1,
                &location,
                ptr::null_mut(),
                1
            )
            .error_code,
            INVALID
        );
        assert_eq!(
            chronik_runtime_confirmed_tx(
                runtime,
                raw.as_ptr(),
                raw.len() - 1,
                &coin,
                1,
                &location,
                ptr::null_mut(),
                0
            )
            .error_code,
            PAYLOAD
        );
        let mut invalid_location = location;
        invalid_location.is_coinbase = 2;
        assert_eq!(
            chronik_runtime_confirmed_tx(
                runtime,
                raw.as_ptr(),
                raw.len(),
                &coin,
                1,
                &invalid_location,
                ptr::null_mut(),
                0
            )
            .error_code,
            INVALID
        );
        let mut output = vec![0xaa; probe.payload_len as usize];
        let small = chronik_runtime_confirmed_tx(
            runtime,
            raw.as_ptr(),
            raw.len(),
            &coin,
            1,
            &location,
            output.as_mut_ptr(),
            output.len() - 1,
        );
        assert_eq!(small.error_code, SMALL_BUFFER);
        assert_eq!(small.payload_len, probe.payload_len);
        assert!(output.iter().all(|byte| *byte == 0xaa));
        let result = chronik_runtime_confirmed_tx(
            runtime,
            raw.as_ptr(),
            raw.len(),
            &coin,
            1,
            &location,
            output.as_mut_ptr(),
            output.len(),
        );
        assert_eq!(result, probe);
        let tx = proto::Tx::decode(output.as_slice()).unwrap();
        assert_eq!(tx.txid, send.txid().to_vec());
        assert_eq!(tx.inputs[0].sats, 7);
        assert_eq!(tx.inputs[0].output_script, script);
        assert_eq!(tx.inputs[0].token.as_ref().unwrap().atoms, 42);
        assert_eq!(tx.outputs[1].token.as_ref().unwrap().atoms, 42);
        assert_eq!(tx.token_status, proto::TokenStatus::Normal as i32);
        assert_eq!(tx.block.unwrap().timestamp, block.timestamp);

        let mut bad_location = location;
        bad_location.hash[0] ^= 1;
        assert_eq!(
            chronik_runtime_confirmed_tx(
                runtime,
                raw.as_ptr(),
                raw.len(),
                &coin,
                1,
                &bad_location,
                ptr::null_mut(),
                0
            )
            .error_code,
            RUNTIME
        );
        assert_eq!(
            chronik_runtime_confirmed_tx(
                runtime,
                raw.as_ptr(),
                raw.len(),
                ptr::null(),
                0,
                &location,
                ptr::null_mut(),
                0
            )
            .error_code,
            PAYLOAD
        );
        assert_eq!(
            chronik_runtime_confirmed_tx(
                runtime,
                raw.as_ptr(),
                MAX_BYTES + 1,
                &coin,
                1,
                &location,
                ptr::null_mut(),
                0
            )
            .error_code,
            INVALID
        );
        coin.script_len = MAX_BYTES + 1;
        assert_eq!(
            chronik_runtime_confirmed_tx(
                runtime,
                raw.as_ptr(),
                raw.len(),
                &coin,
                1,
                &location,
                ptr::null_mut(),
                0
            )
            .error_code,
            INVALID
        );
        coin.script_len = script.len();
        let mut trailing = raw.to_vec();
        trailing.push(0);
        assert_eq!(
            chronik_runtime_confirmed_tx(
                runtime,
                trailing.as_ptr(),
                trailing.len(),
                &coin,
                1,
                &location,
                ptr::null_mut(),
                0
            )
            .error_code,
            PAYLOAD
        );
        let cb_location = chronik_runtime_tx_location(runtime, coinbase.txid().as_bytes().as_ptr());
        let cb_raw = coinbase.ser();
        assert_eq!(
            chronik_runtime_confirmed_tx(
                runtime,
                cb_raw.as_ptr(),
                cb_raw.len(),
                ptr::null(),
                0,
                &cb_location,
                ptr::null_mut(),
                0
            )
            .success,
            1
        );
        indexer_ref(runtime)
            .unwrap()
            .disconnect_block(&block)
            .unwrap();
        assert_eq!(
            chronik_runtime_tx_location(runtime, send.txid().as_bytes().as_ptr()).present,
            0
        );
        assert_eq!(
            chronik_runtime_confirmed_tx(
                runtime,
                raw.as_ptr(),
                raw.len(),
                &coin,
                1,
                &location,
                ptr::null_mut(),
                0
            )
            .error_code,
            RUNTIME
        );
        assert_eq!(chronik_runtime_destroy(runtime), 1);
    }
}

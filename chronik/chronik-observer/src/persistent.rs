// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

//! Panic-contained C ABI for the persistent token runtime.

use std::{
    panic::{catch_unwind, AssertUnwindSafe},
    path::PathBuf,
    ptr, slice,
};

use bitcoinsuite_core::tx::TxId;
use chronik_runtime::{decode_accepted_block, PersistentTokenIndexer};
use prost::Message;

const STATUS_OK: u64 = 1;
const ERROR_INVALID_ARGUMENT: u64 = 2;
const ERROR_PAYLOAD: u64 = 4;
const ERROR_RUNTIME: u64 = 5;
const ERROR_PANIC: u64 = 6;
const ERROR_BUFFER_TOO_SMALL: u64 = 7;

struct RuntimeHandle {
    indexer: PersistentTokenIndexer,
}

/// Stable tip query result for the C++ host adapter.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct RuntimeTip {
    pub success: u64,
    pub error_code: u64,
    pub present: u64,
    pub height: i64,
    pub hash: [u8; 32],
}

/// Stable mutation result for the C++ host adapter.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct RuntimeMutation {
    pub success: u64,
    pub error_code: u64,
    pub height: i64,
    pub transactions: u64,
    pub new_tokens: u64,
    pub token_transactions: u64,
}

/// Stable serialized-query result for the C++ host adapter.
///
/// A null output with zero capacity is a successful sizing probe. When a
/// token is present, `payload_len` is the exact size of its canonical Chronik
/// `TokenInfo` protobuf. The caller may then repeat the query with a buffer of
/// at least that size.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct RuntimeQuery {
    pub success: u64,
    pub error_code: u64,
    pub present: u64,
    pub payload_len: u64,
}

const _: () = assert!(std::mem::size_of::<RuntimeQuery>() == 4 * std::mem::size_of::<u64>());

#[no_mangle]
pub extern "C" fn chronik_runtime_create(
    path: *const u8,
    path_len: usize,
    reset: u64,
) -> *mut std::ffi::c_void {
    catch_unwind(AssertUnwindSafe(|| {
        let Some(path) = read_path(path, path_len) else {
            return ptr::null_mut();
        };
        if reset != 0 && path.exists() && PersistentTokenIndexer::destroy(&path).is_err() {
            return ptr::null_mut();
        }
        let Ok(indexer) = PersistentTokenIndexer::open(&path) else {
            return ptr::null_mut();
        };
        Box::into_raw(Box::new(RuntimeHandle { indexer })).cast()
    }))
    .unwrap_or(ptr::null_mut())
}

#[no_mangle]
pub extern "C" fn chronik_runtime_destroy(runtime: *mut std::ffi::c_void) -> u64 {
    catch_unwind(AssertUnwindSafe(|| {
        if runtime.is_null() {
            return 0;
        }
        // SAFETY: The pointer is created by chronik_runtime_create and this
        // function consumes the sole owning C++ handle exactly once.
        let runtime = unsafe { Box::from_raw(runtime.cast::<RuntimeHandle>()) };
        u64::from(runtime.indexer.close().is_ok())
    }))
    .unwrap_or(0)
}

#[no_mangle]
pub extern "C" fn chronik_runtime_tip(runtime: *const std::ffi::c_void) -> RuntimeTip {
    catch_unwind(AssertUnwindSafe(|| {
        let Some(runtime) = runtime_ref(runtime) else {
            return tip_error(ERROR_INVALID_ARGUMENT);
        };
        match runtime.indexer.tip() {
            Ok(Some(tip)) => RuntimeTip {
                success: STATUS_OK,
                present: STATUS_OK,
                height: i64::from(tip.height),
                hash: tip.hash.to_bytes(),
                ..RuntimeTip::default()
            },
            Ok(None) => RuntimeTip {
                success: STATUS_OK,
                ..RuntimeTip::default()
            },
            Err(_) => tip_error(ERROR_RUNTIME),
        }
    }))
    .unwrap_or_else(|_| tip_error(ERROR_PANIC))
}

/// Read confirmed token genesis metadata as canonical Chronik protobuf bytes.
#[no_mangle]
pub extern "C" fn chronik_runtime_token_info(
    runtime: *const std::ffi::c_void,
    token_id_txid: *const u8,
    output: *mut u8,
    output_capacity: usize,
) -> RuntimeQuery {
    catch_unwind(AssertUnwindSafe(|| {
        let Some(runtime) = runtime_ref(runtime) else {
            return query_error(ERROR_INVALID_ARGUMENT);
        };
        let Some(token_id_txid) = read_hash(token_id_txid) else {
            return query_error(ERROR_INVALID_ARGUMENT);
        };
        if output.is_null() != (output_capacity == 0) {
            return query_error(ERROR_INVALID_ARGUMENT);
        }
        let token_id_txid = TxId::from(token_id_txid);
        let token_info = match runtime.indexer.token_info(&token_id_txid) {
            Ok(token_info) => token_info,
            Err(_) => return query_error(ERROR_RUNTIME),
        };
        let Some(token_info) = token_info else {
            return RuntimeQuery {
                success: STATUS_OK,
                ..RuntimeQuery::default()
            };
        };
        let payload = token_info.encode_to_vec();
        let Ok(payload_len) = u64::try_from(payload.len()) else {
            return query_error(ERROR_RUNTIME);
        };
        if output_capacity == 0 {
            return RuntimeQuery {
                success: STATUS_OK,
                present: STATUS_OK,
                payload_len,
                ..RuntimeQuery::default()
            };
        }
        if output_capacity < payload.len() {
            return RuntimeQuery {
                error_code: ERROR_BUFFER_TOO_SMALL,
                present: STATUS_OK,
                payload_len,
                ..RuntimeQuery::default()
            };
        }
        // SAFETY: The caller provided a non-null allocation of at least
        // output_capacity bytes, and the capacity was checked above.
        unsafe { ptr::copy_nonoverlapping(payload.as_ptr(), output, payload.len()) };
        RuntimeQuery {
            success: STATUS_OK,
            present: STATUS_OK,
            payload_len,
            ..RuntimeQuery::default()
        }
    }))
    .unwrap_or_else(|_| query_error(ERROR_PANIC))
}

#[no_mangle]
pub extern "C" fn chronik_runtime_connect(
    runtime: *const std::ffi::c_void,
    hash: *const u8,
    height: i32,
    raw_block: *const u8,
    raw_block_len: usize,
) -> RuntimeMutation {
    mutate(runtime, hash, height, raw_block, raw_block_len, true)
}

#[no_mangle]
pub extern "C" fn chronik_runtime_disconnect(
    runtime: *const std::ffi::c_void,
    hash: *const u8,
    height: i32,
    raw_block: *const u8,
    raw_block_len: usize,
) -> RuntimeMutation {
    mutate(runtime, hash, height, raw_block, raw_block_len, false)
}

fn mutate(
    runtime: *const std::ffi::c_void,
    hash: *const u8,
    height: i32,
    raw_block: *const u8,
    raw_block_len: usize,
    connect: bool,
) -> RuntimeMutation {
    catch_unwind(AssertUnwindSafe(|| {
        let Some(runtime) = runtime_ref(runtime) else {
            return mutation_error(ERROR_INVALID_ARGUMENT);
        };
        let Some(hash) = read_hash(hash) else {
            return mutation_error(ERROR_INVALID_ARGUMENT);
        };
        let Some(raw_block) = read_bytes(raw_block, raw_block_len) else {
            return mutation_error(ERROR_INVALID_ARGUMENT);
        };
        let Ok(mut block) = decode_accepted_block(hash, height, raw_block) else {
            return mutation_error(ERROR_PAYLOAD);
        };
        if connect {
            match runtime.indexer.connect_block(&block) {
                Ok(summary) => RuntimeMutation {
                    success: STATUS_OK,
                    height: i64::from(summary.height),
                    transactions: summary.transactions,
                    new_tokens: summary.new_tokens,
                    token_transactions: summary.token_transactions,
                    ..RuntimeMutation::default()
                },
                Err(_) => mutation_error(ERROR_RUNTIME),
            }
        } else {
            if block.height < 0 {
                let Ok(Some(tip)) = runtime.indexer.tip() else {
                    return mutation_error(ERROR_RUNTIME);
                };
                block.height = tip.height;
            }
            match runtime.indexer.disconnect_block(&block) {
                Ok(()) => RuntimeMutation {
                    success: STATUS_OK,
                    height: i64::from(height),
                    transactions: u64::try_from(block.txs.len()).unwrap_or(u64::MAX),
                    ..RuntimeMutation::default()
                },
                Err(_) => mutation_error(ERROR_RUNTIME),
            }
        }
    }))
    .unwrap_or_else(|_| mutation_error(ERROR_PANIC))
}

fn read_path(path: *const u8, path_len: usize) -> Option<PathBuf> {
    let bytes = read_bytes(path, path_len)?;
    let path = std::str::from_utf8(bytes).ok()?;
    if path.is_empty() {
        return None;
    }
    Some(PathBuf::from(path))
}

fn read_hash(hash: *const u8) -> Option<[u8; 32]> {
    let bytes = read_bytes(hash, 32)?;
    bytes.try_into().ok()
}

fn read_bytes<'a>(data: *const u8, len: usize) -> Option<&'a [u8]> {
    if data.is_null() || len == 0 {
        return None;
    }
    // SAFETY: The C++ caller keeps this immutable allocation alive for the
    // duration of the call and provides its exact byte length.
    Some(unsafe { slice::from_raw_parts(data, len) })
}

fn runtime_ref(runtime: *const std::ffi::c_void) -> Option<&'static RuntimeHandle> {
    if runtime.is_null() {
        return None;
    }
    // SAFETY: Creation returns a RuntimeHandle and C++ serializes every call
    // on the envelope worker before destroying the handle.
    Some(unsafe { &*runtime.cast::<RuntimeHandle>() })
}

fn tip_error(error_code: u64) -> RuntimeTip {
    RuntimeTip {
        error_code,
        ..RuntimeTip::default()
    }
}

fn mutation_error(error_code: u64) -> RuntimeMutation {
    RuntimeMutation {
        error_code,
        ..RuntimeMutation::default()
    }
}

fn query_error(error_code: u64) -> RuntimeQuery {
    RuntimeQuery {
        error_code,
        ..RuntimeQuery::default()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use bitcoinsuite_core::{
        block::BlockHash,
        script::Script,
        tx::{Tx, TxMut, TxOutput},
    };
    use bitcoinsuite_slp::{
        alp::{genesis_section, sections_opreturn},
        parsed::ParsedMintData,
        structs::GenesisInfo,
        token_type::AlpTokenType,
    };
    use chronik_proto::proto;
    use chronik_runtime::AcceptedBlock;

    #[test]
    fn null_arguments_fail_closed() {
        assert_eq!(
            chronik_runtime_tip(ptr::null()).error_code,
            ERROR_INVALID_ARGUMENT
        );
        assert_eq!(
            chronik_runtime_connect(ptr::null(), ptr::null(), 0, ptr::null(), 0).error_code,
            ERROR_INVALID_ARGUMENT,
        );
        assert_eq!(chronik_runtime_destroy(ptr::null_mut()), 0);
        assert_eq!(
            chronik_runtime_token_info(ptr::null(), ptr::null(), ptr::null_mut(), 0).error_code,
            ERROR_INVALID_ARGUMENT,
        );
    }

    #[test]
    fn invalid_utf8_path_is_rejected() {
        let path = [0xff];
        assert!(chronik_runtime_create(path.as_ptr(), path.len(), 0).is_null());
    }

    #[test]
    fn token_info_query_is_sized_canonical_and_fail_closed() {
        let tempdir = tempdir::TempDir::new("chronik-runtime-query").unwrap();
        let path = tempdir.path().to_str().unwrap().as_bytes();
        let runtime = chronik_runtime_create(path.as_ptr(), path.len(), 0);
        assert!(!runtime.is_null());

        let missing = [99; 32];
        assert_eq!(
            chronik_runtime_token_info(runtime, missing.as_ptr(), ptr::null_mut(), 0),
            RuntimeQuery {
                success: STATUS_OK,
                ..RuntimeQuery::default()
            },
        );
        assert_eq!(
            chronik_runtime_token_info(runtime, missing.as_ptr(), ptr::null_mut(), 1).error_code,
            ERROR_INVALID_ARGUMENT,
        );

        let genesis_txid = TxId::from([1; 32]);
        let coinbase = Tx::with_txid(
            TxId::from([0; 32]),
            TxMut {
                outputs: vec![TxOutput {
                    sats: 0,
                    script: Script::EMPTY,
                }],
                ..Default::default()
            },
        );
        let genesis = Tx::with_txid(
            genesis_txid,
            TxMut {
                outputs: std::iter::once(TxOutput {
                    sats: 0,
                    script: sections_opreturn(vec![genesis_section(
                        AlpTokenType::Standard,
                        &GenesisInfo::empty_alp(),
                        &ParsedMintData {
                            atoms_vec: vec![42],
                            num_batons: 0,
                        },
                    )]),
                })
                .chain(std::iter::once(TxOutput {
                    sats: 0,
                    script: Script::EMPTY,
                }))
                .collect(),
                ..Default::default()
            },
        );
        let block = AcceptedBlock {
            hash: BlockHash::from([10; 32]),
            prev_hash: BlockHash::default(),
            height: 0,
            timestamp: 1_700_000_000,
            txs: vec![coinbase, genesis],
        };
        runtime_ref(runtime)
            .unwrap()
            .indexer
            .connect_block(&block)
            .unwrap();

        let token_id = genesis_txid.to_bytes();
        let sizing = chronik_runtime_token_info(runtime, token_id.as_ptr(), ptr::null_mut(), 0);
        assert_eq!(sizing.success, STATUS_OK);
        assert_eq!(sizing.present, STATUS_OK);
        assert!(sizing.payload_len > 0);

        let mut too_small = vec![0xa5; sizing.payload_len as usize - 1];
        let small = chronik_runtime_token_info(
            runtime,
            token_id.as_ptr(),
            too_small.as_mut_ptr(),
            too_small.len(),
        );
        assert_eq!(small.error_code, ERROR_BUFFER_TOO_SMALL);
        assert_eq!(small.present, STATUS_OK);
        assert_eq!(small.payload_len, sizing.payload_len);
        assert!(too_small.iter().all(|byte| *byte == 0xa5));

        let mut payload = vec![0; sizing.payload_len as usize];
        let copied = chronik_runtime_token_info(
            runtime,
            token_id.as_ptr(),
            payload.as_mut_ptr(),
            payload.len(),
        );
        assert_eq!(copied, sizing);
        let token_info = proto::TokenInfo::decode(payload.as_slice()).unwrap();
        assert_eq!(token_info.token_id, genesis_txid.to_string());
        assert_eq!(token_info.block.unwrap().timestamp, block.timestamp);

        assert_eq!(chronik_runtime_destroy(runtime), STATUS_OK);
    }
}

// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

//! Panic-contained C ABI for the persistent token runtime.

use std::{
    panic::{catch_unwind, AssertUnwindSafe},
    path::PathBuf,
    ptr, slice,
};

use chronik_runtime::{decode_accepted_block, PersistentTokenIndexer};

const STATUS_OK: u64 = 1;
const ERROR_INVALID_ARGUMENT: u64 = 2;
const ERROR_PAYLOAD: u64 = 4;
const ERROR_RUNTIME: u64 = 5;
const ERROR_PANIC: u64 = 6;

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

#[cfg(test)]
mod tests {
    use super::*;

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
    }

    #[test]
    fn invalid_utf8_path_is_rejected() {
        let path = [0xff];
        assert!(chronik_runtime_create(path.as_ptr(), path.len(), 0).is_null());
    }
}

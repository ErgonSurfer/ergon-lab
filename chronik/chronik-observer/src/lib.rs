// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

//! Volatile block-event boundary between the legacy node and Rust.
//!
//! This crate has no persistence, networking, or threads. Its C ABI never
//! returns a decision to node validation.

use std::panic::{catch_unwind, AssertUnwindSafe};

const CONNECTED: u8 = 1;
const DISCONNECTED: u8 = 2;
const FNV_OFFSET_BASIS: u64 = 0xcbf29ce484222325;
const FNV_PRIME: u64 = 0x100000001b3;

#[repr(C)]
#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct Observation {
    pub sequence: u64,
    pub fingerprint: u64,
}

#[derive(Debug, Default)]
pub struct Observer {
    sequence: u64,
}

impl Observer {
    fn record(&mut self, kind: u8, hash: &[u8; 32], height: i32) -> Observation {
        self.sequence = self.sequence.wrapping_add(1);
        if self.sequence == 0 {
            self.sequence = 1;
        }
        Observation {
            sequence: self.sequence,
            fingerprint: event_fingerprint(kind, hash, height),
        }
    }
}

fn event_fingerprint(kind: u8, hash: &[u8; 32], height: i32) -> u64 {
    let mut fingerprint = FNV_OFFSET_BASIS;
    for byte in std::iter::once(&kind)
        .chain(hash.iter())
        .chain(height.to_le_bytes().iter())
    {
        fingerprint ^= u64::from(*byte);
        fingerprint = fingerprint.wrapping_mul(FNV_PRIME);
    }
    fingerprint
}

fn observe(observer: *mut Observer, kind: u8, hash: *const u8, height: i32) -> Observation {
    if observer.is_null() || hash.is_null() {
        return Observation::default();
    }

    catch_unwind(AssertUnwindSafe(|| {
        let mut owned_hash = [0u8; 32];
        // SAFETY: C++ passes a non-null pointer to 32 live hash bytes for the
        // duration of this synchronous call.
        unsafe { std::ptr::copy_nonoverlapping(hash, owned_hash.as_mut_ptr(), 32) };
        // SAFETY: This crate created the non-null handle, and C++ serializes
        // access until it returns ownership to chronik_observer_destroy.
        unsafe { &mut *observer }.record(kind, &owned_hash, height)
    }))
    .unwrap_or_default()
}

#[no_mangle]
pub extern "C" fn chronik_observer_create() -> *mut Observer {
    Box::into_raw(Box::<Observer>::default())
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
pub extern "C" fn chronik_observer_block_connected(
    observer: *mut Observer,
    hash: *const u8,
    height: i32,
) -> Observation {
    observe(observer, CONNECTED, hash, height)
}

#[no_mangle]
pub extern "C" fn chronik_observer_block_disconnected(
    observer: *mut Observer,
    hash: *const u8,
) -> Observation {
    observe(observer, DISCONNECTED, hash, -1)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn records_order_and_owned_hash_bytes() {
        let observer = chronik_observer_create();
        let connected_hash = [0x11; 32];
        let disconnected_hash = [0x22; 32];

        let connected = chronik_observer_block_connected(observer, connected_hash.as_ptr(), 7);
        let disconnected =
            chronik_observer_block_disconnected(observer, disconnected_hash.as_ptr());

        assert_eq!(connected.sequence, 1);
        assert_eq!(
            connected.fingerprint,
            event_fingerprint(CONNECTED, &connected_hash, 7),
        );
        assert_eq!(disconnected.sequence, 2);
        assert_eq!(
            disconnected.fingerprint,
            event_fingerprint(DISCONNECTED, &disconnected_hash, -1),
        );
        assert_eq!(chronik_observer_destroy(observer), 2);
    }

    #[test]
    fn rejects_invalid_ffi_inputs_without_state_change() {
        let observer = chronik_observer_create();
        let hash = [0x33; 32];

        assert_eq!(
            chronik_observer_block_connected(std::ptr::null_mut(), hash.as_ptr(), 1),
            Observation::default(),
        );
        assert_eq!(
            chronik_observer_block_connected(observer, std::ptr::null(), 1),
            Observation::default(),
        );
        assert_eq!(chronik_observer_destroy(observer), 0);
        assert_eq!(chronik_observer_destroy(std::ptr::null_mut()), 0);
    }
}

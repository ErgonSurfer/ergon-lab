// Copyright (c) 2022 The Bitcoin developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

fn main() {
    // Build the C++ sources and headers for bridging Rust and C++.
    let _build = cxx_build::bridge("src/ffi.rs");
    println!("cargo:rerun-if-changed=src/ffi.rs");
    // SPDX-License-Identifier: MIT — Ergon fixture-only bridge generation.
    if std::env::var_os("CARGO_FEATURE_ERGON_ARCHIVAL_PROBE").is_some() {
        let id = std::env::var("CHRONIK_ERGON_ADAPTER_ID")
            .expect("archival probe requires a bound adapter identity");
        assert!(
            id.len() == 64
                && id
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        );
        println!("cargo:rustc-env=CHRONIK_ERGON_ADAPTER_ID={id}");
        println!("cargo:rerun-if-env-changed=CHRONIK_ERGON_ADAPTER_ID");
        let _probe = cxx_build::bridge("src/ergon_archival.rs");
        println!("cargo:rerun-if-changed=src/ergon_archival.rs");
    }
}

// Copyright (c) 2022 The Bitcoin developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

//! Foreign-function interface to bitcoind. Bridges Rust to C++.
//!
//! The bridge is split into two crates, `chronik-bridge` and `chronik-lib`.
//!
//! In this crate `chronik-lib`, we define a library that can be statically
//! linked to by the node.
//!
//! We define all the functions that the C++ side needs to call:
//! 1. Methods in CValidationInterface notifying the indexer of added/removed
//!    blocks/transactions.
//! 2. `setup_bridge`, to instantiate the bridge connecting to C++.

abc_rust_lint::lint! {
    pub mod bridge;
    pub mod ffi;
}

// SPDX-License-Identifier: MIT — Ergon owned runtime shutdown adapter.
mod ergon_runtime;

// SPDX-License-Identifier: MIT — independently authored Ergon test adapter.
#[cfg(feature = "ergon-archival-probe")]
mod ergon_archival;

// SPDX-License-Identifier: MIT — fixture-only upstream request admission.
#[cfg(feature = "ergon-archival-probe")]
mod ergon_service;

// SPDX-License-Identifier: MIT — synthetic fixtures using upstream ALP builders.
#[cfg(feature = "ergon-archival-probe")]
mod ergon_http_tests;

// SPDX-License-Identifier: MIT — public synthetic plugin fixture, no daemon API.
#[cfg(feature = "ergon-archival-probe")]
mod ergon_plugin_fixture;

// SPDX-License-Identifier: MIT — content-bound trusted operator inputs.
#[cfg(feature = "ergon-archival-probe")]
mod ergon_operator_bundle;

// SPDX-License-Identifier: MIT — immutable, owner-derived HTTP capability.
#[cfg(feature = "ergon-archival-probe")]
mod ergon_plugin_access;

// SPDX-License-Identifier: MIT — native receipt versus indexed-readiness contract.
#[cfg(feature = "ergon-archival-probe")]
mod ergon_submission;

// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
//! Owned bounded adapter for the actual upstream archival indexer. Native
//! capture is separate from DB application. Only the separate default-OFF
//! regtest owner admits a daemon service; legacy lock-fenced reconciliation
//! remains a synchronous comparison oracle.

use crate::ergon_plugin_fixture::{
    binding_for_mode, http_plugin_group_matches, http_plugin_tx_matches, load_plugin_fixture,
    make_plugin_fixture, load_operator_plugins, operator_bundle_binding, PluginFixture,
};
use crate::ergon_service::{Binding, RequestGate, ResponseLatch};
use abc_rust_error::{bail, Report, Result};
use bitcoinsuite_core::{
    hash::{Hashed, Sha256},
    script::Script,
    tx::{Tx, TxId},
};
use chronik_bridge::ffi as native;
use chronik_db::{
    group::GroupMember, index_tx::TxNumCacheSettings, io::GroupHistorySettings, mem::MempoolTx,
};
use chronik_indexer::indexer::{ChronikIndexer, ChronikIndexerError, ChronikIndexerParams, Node};
use chronik_plugin::context::PluginContext;
use prost::Message;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, OpenOptions},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::Arc,
};

const RETAINED_BLOCKS: usize = 288;
use tokio::sync::{RwLock, RwLockReadGuard};

#[cxx::bridge(namespace = "ergon_archival")]
mod ffi {
    // Fixture-owned capture metadata, not an upstream wire-contract change.
    struct CaptureEntry {
        height: i32,
        hash: [u8; 32],
        file_num: u32,
        data_pos: u32,
        undo_pos: u32,
        wire_bytes: u64,
    }
    struct PoolCaptureEntry {
        txid: [u8; 32],
        time: i64,
        wire_bytes: u64,
        chain_generation: u64,
    }
    struct PoolApplyStats {
        removed: u64,
        added: u64,
        retained: u64,
    }
    struct Snapshot {
        height: i32,
        hash: [u8; 32],
        logical_digest: [u8; 32],
        num_txs: u64,
    }
    struct TxView {
        height: i32,
        output_spent: bool,
        spender: [u8; 32],
        spender_input: u32,
        input_value: i64,
        time_first_seen: i64,
        // Fixture diagnostics from the actual indexed mempool input coin.
        mempool_input_height: i32,
        mempool_input_coinbase: bool,
    }
    struct ScriptView {
        confirmed: u32,
        utxos: u64,
        unconfirmed: u32,
    }
    struct RuntimeObservation {
        http_ok: bool,
        websocket_ok: bool,
        blocking_waited: bool,
        concurrent_stop_waited: bool,
        async_dropped: bool,
        server_finished: bool,
        indexer_unique: bool,
        native_refs_released: bool,
        listener_closed: bool,
        websocket_closed: bool,
        partial_http_closed: bool,
    }
    unsafe extern "C++" {
        include!("chronik-cpp/ergon_archival_probe.h");
        #[namespace = "chronik_bridge"]
        type ErgonNodeAccess = chronik_bridge::ffi::ErgonNodeAccess;
        include!("coins.h");
        include!("primitives/transaction.h");
        #[namespace = ""]
        type CTransaction = chronik_bridge::ffi::CTransaction;
        #[namespace = ""]
        #[cxx_name = "Coin"]
        type CCoin = chronik_bridge::ffi::CCoin;
        include!("primitives/block.h");
        include!("undo.h");
        include!("chain.h");
        #[namespace = ""]
        type CBlock = chronik_bridge::ffi::CBlock;
        #[namespace = ""]
        type CBlockUndo = chronik_bridge::ffi::CBlockUndo;
        #[namespace = ""]
        type CBlockIndex = chronik_bridge::ffi::CBlockIndex;
        include!("chronik-cpp/chronik_bridge.h");
        #[namespace = "chronik_bridge"]
        type ChronikBridge = chronik_bridge::ffi::ChronikBridge;
        fn make_startup_bridge(node: &ErgonNodeAccess) -> Result<UniquePtr<ChronikBridge>>;
        fn make_service_startup_bridge(node: &ErgonNodeAccess) -> Result<UniquePtr<ChronikBridge>>;
        fn finish_startup_bridge(bridge: &ChronikBridge) -> Result<()>;
        fn finish_service_startup_bridge(bridge: &ChronikBridge) -> Result<bool>;
        fn service_startup_cancelled() -> bool;
        fn capture_service_startup(probe: &mut ArchivalProbe, bridge: &ChronikBridge,
                                  node: &ErgonNodeAccess) -> Result<bool>;
        fn reconcile_startup(probe: &mut ArchivalProbe, node: &ErgonNodeAccess) -> Result<bool>;
        type StartupHook;
        fn run(self: &StartupHook) -> Result<()>;
        fn archival_fixture_guard(node: &ErgonNodeAccess) -> Result<()>;
        fn archival_capture_height_guard(node: &ErgonNodeAccess, height: i32) -> Result<()>;
        fn archival_reconcile_guard(node: &ErgonNodeAccess) -> Result<()>;
        fn archival_mempool_revision(node: &ErgonNodeAccess) -> u64;
        fn archival_chain_generation(node: &ErgonNodeAccess) -> u64;
        fn archival_database_guard(node: &ErgonNodeAccess) -> Result<()>;
    }
    extern "Rust" {
        type ArchivalProbe;
        type PluginFixture;
        fn make_plugin_fixture(root: &str, mode: u8) -> Result<Box<PluginFixture>>;
        fn load_plugin_fixture(root: &str, mode: u8) -> Result<Box<PluginFixture>>;
        fn loaded(self: &PluginFixture) -> u32;
        fn load_operator_plugins(root: &str, expected: &str) -> Result<Box<PluginFixture>>;
        fn operator_bundle_binding(root: &str) -> Result<String>;
        fn open_archival_with_plugins(
            root: &str,
            node: &ErgonNodeAccess,
            plugins: &PluginFixture,
        ) -> Result<Box<ArchivalProbe>>;
        fn open_archival_with_plugins_and_hook(
            root: &str, node: &ErgonNodeAccess, plugins: &PluginFixture,
            hook: &StartupHook,
        ) -> Result<Box<ArchivalProbe>>;
        fn upstream_plugin_version_refusal(
            root: &str, node: &ErgonNodeAccess, plugins: &PluginFixture,
        ) -> Result<bool>;
        fn http_plugin_tx_matches(
            body: &[u8],
            output: u32,
            parents: u32,
            atoms: u64,
        ) -> Result<bool>;
        fn http_plugin_group_matches(body: &[u8], kind: u8, points: &[u8]) -> Result<bool>;
        type CapturedSource;
        fn capture_inventory(
            self: &ArchivalProbe,
            node: &ErgonNodeAccess,
        ) -> Result<Vec<CaptureEntry>>;
        fn pool_capture_inventory(
            self: &ArchivalProbe,
            node: &ErgonNodeAccess,
        ) -> Result<Vec<PoolCaptureEntry>>;
        fn seed_pool_entry(
            self: &ArchivalProbe,
            source: &mut CapturedSource,
            index: usize,
            node: &ErgonNodeAccess,
        ) -> Result<()>;
        fn pool_apply_stats(self: &ArchivalProbe) -> PoolApplyStats;
        fn seed_capture(
            self: &ArchivalProbe,
            source: &mut CapturedSource,
            count: usize,
            node: &ErgonNodeAccess,
        ) -> Result<()>;
        fn new_capture(
            node: &ErgonNodeAccess,
            start_height: i32,
            height: i32,
            hash: [u8; 32],
            generation: u64,
            revision: u64,
        ) -> Result<Box<CapturedSource>>;
        fn capture_block(
            self: &mut CapturedSource,
            block: &CBlock,
            undo: &CBlockUndo,
            index: &CBlockIndex,
            wire_bytes: u64,
            node: &ErgonNodeAccess,
        ) -> Result<()>;
        fn capture_mempool(
            self: &mut CapturedSource,
            tx: &CTransaction,
            coins: &CxxVector<CCoin>,
            time: i64,
            wire_bytes: u64,
            node: &ErgonNodeAccess,
        ) -> Result<()>;
        fn apply_capture(
            self: &mut ArchivalProbe,
            source: &CapturedSource,
            node: &ErgonNodeAccess,
        ) -> Result<()>;
        fn publish_capture(
            self: &mut ArchivalProbe,
            source: &CapturedSource,
            node: &ErgonNodeAccess,
        ) -> Result<bool>;
        fn assert_current_capture(self: &ArchivalProbe, node: &ErgonNodeAccess) -> Result<()>;
        fn source_current(self: &ArchivalProbe) -> bool;
        fn runtime_probe(self: &mut ArchivalProbe) -> Result<RuntimeObservation>;
        type RequestGate;
        type ResponseLatch;
        fn request_gate(self: &ArchivalProbe) -> Box<RequestGate>;
        fn response_latch(self: &ArchivalProbe) -> Box<ResponseLatch>;
        fn clone_handle(self: &RequestGate) -> Box<RequestGate>;
        fn clone_handle(self: &ResponseLatch) -> Box<ResponseLatch>;
        fn invalidate(self: &RequestGate);
        fn close(self: &RequestGate);
        fn fail(self: &RequestGate);
        fn dispatched(self: &RequestGate) -> u64;
        fn arm(self: &ResponseLatch) -> bool;
        fn wait(self: &ResponseLatch, milliseconds: u32) -> bool;
        fn release(self: &ResponseLatch);
        fn start_http_fixture(self: &mut ArchivalProbe) -> Result<u16>;
        fn stop_http_fixture(self: &mut ArchivalProbe) -> Result<()>;
        fn http_chain_matches(body: &[u8], hash: [u8; 32], height: i32) -> Result<bool>;
        fn http_error_matches(body: &[u8], message: &str) -> Result<bool>;
        fn fixture_alp_script(op: u8, id: [u8; 32]) -> Result<Vec<u8>>;
        fn http_fixture_check(kind: u8, body: &[u8], ids: &[u8], values: &[u64]) -> Result<bool>;
        fn http_alp_tx_matches(
            body: &[u8],
            id: [u8; 32],
            token: [u8; 32],
            kind: i32,
            atoms: u64,
            burn: u64,
            invalid: bool,
            expected_height: i32,
            time: i64,
        ) -> Result<bool>;
        fn http_spent_matches(
            body: &[u8],
            output: u32,
            spender: [u8; 32],
            input: u32,
        ) -> Result<bool>;
        fn http_alp_parse_failure_matches(body: &[u8], id: [u8; 32]) -> Result<bool>;
        fn open_archival(root: &str, node: &ErgonNodeAccess) -> Result<Box<ArchivalProbe>>;
        fn open_regtest_service(root: &str, node: &ErgonNodeAccess) -> Result<Box<ArchivalProbe>>;
        fn open_regtest_service_with_plugins(root: &str, node: &ErgonNodeAccess,
                                             plugins: &PluginFixture) -> Result<Box<ArchivalProbe>>;
        fn open_regtest_service_with_hook(root: &str, node: &ErgonNodeAccess,
                                         hook: &StartupHook) -> Result<Box<ArchivalProbe>>;
        fn seed_service_startup(self: &mut ArchivalProbe, source: &CapturedSource,
                               node: &ErgonNodeAccess) -> Result<()>;
        fn start_regtest_http(self: &mut ArchivalProbe) -> Result<u16>;
        fn open_archival_with_hook(
            root: &str,
            node: &ErgonNodeAccess,
            hook: &StartupHook,
        ) -> Result<Box<ArchivalProbe>>;
        fn snapshot(self: &ArchivalProbe) -> Result<Snapshot>;
        fn tx_view(self: &ArchivalProbe, txid: [u8; 32], output: usize) -> Result<TxView>;
        fn script_view(self: &ArchivalProbe, script: &[u8]) -> Result<ScriptView>;
        fn begin_reconcile(self: &mut ArchivalProbe, node: &ErgonNodeAccess) -> Result<()>;
        fn add_native_mempool(
            self: &mut ArchivalProbe,
            tx: &CTransaction,
            coins: &CxxVector<CCoin>,
            time: i64,
            node: &ErgonNodeAccess,
        ) -> Result<()>;
        fn finish_reconcile(
            self: &mut ArchivalProbe,
            ids: &[u8],
            node: &ErgonNodeAccess,
        ) -> Result<()>;
        fn fail_reconcile(self: &mut ArchivalProbe);
    }
}

struct Lease {
    root: PathBuf,
    // The daemon profile keeps the file inode stable and owns an OS lock.
    // Process death releases the lock, not the identity or database contents.
    service_lock: Option<fs::File>,
}
impl Drop for Lease {
    fn drop(&mut self) {
        if self.service_lock.is_none() {
            let _ = fs::remove_file(self.root.join("probe.lock"));
        }
    }
}

const SERVICE_LEASE: &[u8] = b"ergon-regtest-service-os-lock/v1\n";

fn service_lease(root: &Path) -> Result<Lease> {
    // A persistent lock file MUST NOT be unlinked on release: another owner
    // could otherwise lock the old inode while a third locks a new inode.
    let path = root.join("probe.lock");
    let (mut file, created) = match OpenOptions::new().read(true).write(true)
        .create_new(true).open(&path) {
        Ok(file) => (file, true),
        Err(err) if err.kind() == std::io::ErrorKind::AlreadyExists =>
            (OpenOptions::new().read(true).write(true).open(&path)?, false),
        Err(err) => return Err(err.into()),
    };
    file.try_lock()?;
    let metadata = file.metadata()?;
    if !metadata.is_file() || fs::symlink_metadata(&path)?.file_type().is_symlink() {
        bail!("Service lease must be a regular file");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let current = fs::metadata(&path)?;
        if metadata.nlink() != 1 || metadata.dev() != current.dev()
            || metadata.ino() != current.ino() {
            bail!("Service lease inode alias refused");
        }
    }
    if created {
        file.write_all(SERVICE_LEASE)?;
        file.sync_all()?;
        fs::File::open(root)?.sync_all()?;
    } else {
        // Bounded read from the locked descriptor, not an independently
        // reopened path. Includes old fixture leases and partial writes.
        let mut marker = Vec::with_capacity(SERVICE_LEASE.len() + 1);
        if metadata.len() != SERVICE_LEASE.len() as u64 {
            bail!("Unknown or incomplete service lease; external review required");
        }
        (&mut file).take(SERVICE_LEASE.len() as u64 + 1).read_to_end(&mut marker)?;
        if marker != SERVICE_LEASE {
            // Never guess a PID, delete a stale file, or adopt an unknown root.
            bail!("Unknown or incomplete service lease; external review required");
        }
    }
    Ok(Lease { root: root.to_owned(), service_lock: Some(file) })
}
pub struct ArchivalProbe {
    // Drop indexer before the node and before removing the exclusive lease.
    indexer: Option<Arc<RwLock<ChronikIndexer>>>,
    node: Arc<Node>,
    _lease: Lease,
    bound_height: i32,
    bound_hash: [u8; 32],
    bound_generation: u64,
    bound_revision: u64,
    access_address: usize,
    live_fixture: bool,
    reconciling: bool,
    // Owned wire data, never native pointers, needed to rewind a captured old
    // branch while the live node has already advanced to another branch.
    captured_chain: Vec<Arc<RetainedBlock>>,
    captured_pool: Vec<Arc<RetainedPoolEntry>>,
    pool_chain_generation: u64,
    last_pool_ops: ffi::PoolApplyStats,
    applied_capture: Option<([u8; 32], i32, u64, u64, u64)>,
    request_gate: RequestGate,
    response_latch: ResponseLatch,
    service: Option<HttpFixture>,
    plugins: Option<PluginFixture>,
}

struct HttpFixture {
    runtime: crate::ergon_runtime::JoinedRuntime,
    task: tokio::task::JoinHandle<Result<()>>,
}
pub fn http_chain_matches(body: &[u8], hash: [u8; 32], height: i32) -> Result<bool> {
    let info = chronik_proto::proto::BlockchainInfo::decode(body)?;
    Ok(info.tip_hash == hash && info.tip_height == height)
}
pub fn http_error_matches(body: &[u8], message: &str) -> Result<bool> {
    Ok(chronik_proto::proto::Error::decode(body)?.msg == message)
}

use crate::ergon_http_tests::{
    alp_script as fixture_alp_script, alp_tx as http_alp_tx_matches, check as http_fixture_check,
    parse_failure as http_alp_parse_failure_matches, spent as http_spent_matches,
};

struct RetainedBlock {
    block: native::Block,
    // Exact original native block + undo serialized charge, not Rust heap size.
    wire_bytes: u64,
}
struct RetainedPoolEntry {
    entry: MempoolTx,
    wire_bytes: u64,
}
pub struct CapturedSource {
    owner: usize,
    start_height: i32,
    height: i32,
    hash: [u8; 32],
    generation: u64,
    revision: u64,
    blocks: Vec<Arc<RetainedBlock>>,
    mempool: Vec<Arc<RetainedPoolEntry>>,
}
pub fn new_capture(
    node: &native::ErgonNodeAccess,
    start_height: i32,
    height: i32,
    hash: [u8; 32],
    generation: u64,
    revision: u64,
) -> Result<Box<CapturedSource>> {
    ffi::archival_reconcile_guard(node)?;
    if generation == 0 || revision == 0 {
        bail!("Exhausted native source generation");
    }
    ffi::archival_capture_height_guard(node, height)?;
    if !(0..=height).contains(&start_height)
        || (height - start_height + 1) as usize > 2 * RETAINED_BLOCKS
    {
        bail!("Capture height outside fixture profile");
    }
    Ok(Box::new(CapturedSource {
        owner: node as *const _ as usize,
        start_height,
        height,
        hash,
        generation,
        revision,
        blocks: Vec::new(),
        mempool: Vec::new(),
    }))
}
impl CapturedSource {
    fn owner(&self, node: &native::ErgonNodeAccess) -> Result<()> {
        if self.owner != node as *const _ as usize {
            bail!("Capture native owner mismatch");
        }
        Ok(ffi::archival_reconcile_guard(node)?)
    }
    pub fn capture_block(
        &mut self,
        block: &native::CBlock,
        undo: &native::CBlockUndo,
        index: &native::CBlockIndex,
        wire_bytes: u64,
        node: &native::ErgonNodeAccess,
    ) -> Result<()> {
        if self.owner != node as *const _ as usize {
            bail!("Capture native owner mismatch");
        }
        // Index metadata is detached or a service-owned immutable native
        // header loan. Mutable positions are captured/rechecked by C++ under
        // cs_main around each unlocked read/conversion. No mutable native
        // storage field is accessed directly from Rust.
        ffi::archival_database_guard(node)?;
        let block = native::bridge_block(block, undo, index)?;
        if block.height != self.start_height + self.blocks.len() as i32
            || self
                .blocks
                .last()
                .is_some_and(|prev| block.prev_hash != prev.block.hash)
        {
            bail!("Captured chain continuity mismatch");
        }
        if wire_bytes <= block.size || wire_bytes > 64 * 1024 * 1024 {
            bail!("Invalid native capture payload charge");
        }
        self.blocks
            .push(Arc::new(RetainedBlock { block, wire_bytes }));
        Ok(())
    }
    pub fn capture_mempool(
        &mut self,
        tx: &native::CTransaction,
        coins: &cxx::CxxVector<native::CCoin>,
        time: i64,
        wire_bytes: u64,
        node: &native::ErgonNodeAccess,
    ) -> Result<()> {
        // Native transactions and coin values are owned detached inputs. C++
        // resolves cold prevouts using an owned snapshot outside native locks,
        // then rechecks the live source/owner before apply and publication.
        if self.owner != node as *const _ as usize {
            bail!("Capture native owner mismatch");
        }
        ffi::archival_database_guard(node)?;
        if wire_bytes == 0 || wire_bytes > 64 * 1024 * 1024 {
            bail!("Invalid captured pool payload charge");
        }
        self.mempool.push(Arc::new(RetainedPoolEntry {
            entry: MempoolTx {
                tx: Tx::from(native::bridge_tx(tx, coins)?),
                time_first_seen: time,
            },
            wire_bytes,
        }));
        Ok(())
    }
}

fn checked_root(root: &str) -> Result<PathBuf> {
    let path = Path::new(root);
    if !path.is_absolute() || fs::canonicalize(path)? != path || !path.is_dir() {
        bail!("Archival root must be an existing canonical absolute directory");
    }
    // Symlink components, including one which resolves back to itself, rejected.
    let mut component_path = PathBuf::new();
    for component in path.components() {
        component_path.push(component);
        if fs::symlink_metadata(&component_path)?
            .file_type()
            .is_symlink()
        {
            bail!("Archival root contains a symlink");
        }
    }
    Ok(path.to_owned())
}
fn check_entries(root: &Path) -> Result<()> {
    for entry in fs::read_dir(root)? {
        let entry = entry?;
        let typ = entry.file_type()?;
        let name = entry.file_name();
        if typ.is_symlink()
            || !matches!(
                name.to_str(),
                Some("ergon-identity.json" | "indexes" | "probe.lock")
            )
        {
            bail!("Unexpected archival root entry (including failed marker)");
        }
        if name == "indexes" {
            if !typ.is_dir() {
                bail!("Indexes must be a directory");
            }
            // All on-disk DB members must be regular files, never redirected.
            for db in fs::read_dir(entry.path())? {
                let db = db?;
                if db.file_name() != "chronik" || !db.file_type()?.is_dir() {
                    bail!("Unexpected index directory");
                }
                for file in fs::read_dir(db.path())? {
                    if !file?.file_type()?.is_file() {
                        bail!("Non-regular database member");
                    }
                }
            }
        } else if !typ.is_file() {
            bail!("Identity and lease must be regular files");
        }
    }
    Ok(())
}
fn identity(genesis: [u8; 32]) -> Vec<u8> {
    let hex: String = genesis.iter().map(|b| format!("{b:02x}")).collect();
    format!("{{\"schema\":\"ergon-archival-probe/v1\",\"network\":\"regtest\",\"genesis_le\":\"{hex}\",\"public_parent\":\"8ae2e14af4765b0ffa5c8f4bd89d6ba3850ae84a\",\"donor\":\"784d83de2e19eab726898d77bfe7465410d27a9c\",\"adapter_sha256\":\"{}\",\"token\":true,\"lokad\":true,\"scripthash\":true,\"plugins\":false}}\n", env!("CHRONIK_ERGON_ADAPTER_ID")).into_bytes()
}
fn failure(root: &Path, node: &Node) {
    if let Ok(mut file) = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(root.join("probe.failed"))
    {
        let _ = file.write_all(
            b"Rebuild in a new, empty reviewed root; never auto-adopt failed database.\n",
        );
        let _ = file.sync_all();
    }
    node.bridge
        .fatal_error("Archival probe failed", "fixture-only upstream indexing");
}
fn decompress(script: &[u8]) -> Result<Vec<u8>> {
    Ok(native::decompress_script(script)?)
}

fn plugin_identity(genesis: [u8; 32], binding: &str) -> Result<Vec<u8>> {
    Ok(String::from_utf8(identity(genesis))?
        .replace("\"plugins\":false", &format!(
            "\"plugins\":true,\"plugin_fixture_sha256\":\"{binding}\""))
        .into_bytes())
}

fn indexer_params(root: &Path, plugins: Option<&PluginFixture>) -> ChronikIndexerParams {
    ChronikIndexerParams {
        datadir_net: root.to_owned(), wipe_db: false,
        enable_token_index: true, enable_lokad_id_index: true,
        enable_scripthash_index: true, enable_perf_stats: false,
        tx_num_cache: TxNumCacheSettings { bucket_size: 100, num_buckets: 10 },
        script_history: GroupHistorySettings { is_member_hash_index_enabled: true },
        plugin_ctx: plugins.map(|p| p.context.clone())
            .unwrap_or_else(|| Arc::new(PluginContext::default())),
        decompress_script_fn: decompress,
    }
}

// Negative fixture oracle only. NEVER returns an indexer, Ready state or HTTP
// owner, and never changes the identity/version record or requests wipe_db.
// Operational opens still enforce their complete identity first. This narrowly
// checks the REAL upstream setup rejection against a bound synthetic v1 DB.
pub fn upstream_plugin_version_refusal(
    root: &str, access: &native::ErgonNodeAccess, plugins: &PluginFixture,
) -> Result<bool> {
    ffi::archival_fixture_guard(access)?;
    plugins.check()?;
    let loaded = plugins.context.plugins();
    if loaded.len() != 1 || loaded[0].module_name != "ergon_public_probe"
        || loaded[0].version.to_string() != "2.0.0" {
        bail!("Version-refusal oracle requires the actual public v2 plugin");
    }
    let root = checked_root(root)?;
    check_entries(&root)?;
    let _lock = OpenOptions::new().write(true).create_new(true).open(root.join("probe.lock"))?;
    let _lease = Lease { root: root.clone(), service_lock: None };
    let node = Arc::new(Node { bridge: ffi::make_startup_bridge(access)? });
    let expected = plugin_identity(node.bridge.get_genesis_hash().data, &binding_for_mode(0)?)?;
    if fs::read(root.join("ergon-identity.json"))? != expected || !root.join("indexes/chronik").is_dir() {
        bail!("Version-refusal oracle requires a bound existing public v1 index");
    }
    let error = match ChronikIndexer::setup(indexer_params(&root, Some(plugins)), node) {
        Ok(_) => bail!("Upstream accepted an incompatible public plugin version"),
        Err(error) => error.downcast::<ChronikIndexerError>()?,
    };
    Ok(matches!(error, ChronikIndexerError::PluginVersionMismatch {
        plugin_name, db_version, loaded_version,
    } if plugin_name == "ergon_public_probe" && db_version == "1.0.0" && loaded_version == "2.0.0"))
}

pub fn open_archival(root: &str, access: &native::ErgonNodeAccess) -> Result<Box<ArchivalProbe>> {
    open_archival_impl(root, access, None, None, false)
}
pub fn open_regtest_service(root: &str, access: &native::ErgonNodeAccess) -> Result<Box<ArchivalProbe>> {
    open_archival_impl(root, access, None, None, true)
}
pub fn open_regtest_service_with_plugins(
    root: &str, access: &native::ErgonNodeAccess, plugins: &PluginFixture,
) -> Result<Box<ArchivalProbe>> {
    plugins.check()?;
    open_archival_impl(root, access, None, Some(plugins), true)
}
pub fn open_regtest_service_with_hook(root: &str, access: &native::ErgonNodeAccess,
                                    hook: &ffi::StartupHook) -> Result<Box<ArchivalProbe>> {
    open_archival_impl(root, access, Some(hook), None, true)
}
pub fn open_archival_with_plugins(
    root: &str,
    access: &native::ErgonNodeAccess,
    plugins: &PluginFixture,
) -> Result<Box<ArchivalProbe>> {
    plugins.check()?;
    open_archival_impl(root, access, None, Some(plugins), false)
}
pub fn open_archival_with_hook(
    root: &str,
    access: &native::ErgonNodeAccess,
    hook: &ffi::StartupHook,
) -> Result<Box<ArchivalProbe>> {
    open_archival_impl(root, access, Some(hook), None, false)
}
pub fn open_archival_with_plugins_and_hook(
    root: &str, access: &native::ErgonNodeAccess,
    plugins: &PluginFixture, hook: &ffi::StartupHook,
) -> Result<Box<ArchivalProbe>> {
    plugins.check()?;
    open_archival_impl(root, access, Some(hook), Some(plugins), false)
}
fn open_archival_impl(
    root: &str,
    access: &native::ErgonNodeAccess,
    hook: Option<&ffi::StartupHook>,
    plugins: Option<&PluginFixture>,
    service: bool,
) -> Result<Box<ArchivalProbe>> {
    ffi::archival_fixture_guard(access)?;
    chronik_util::mount_loggers(chronik_util::Loggers {
        log: native::log_print,
        log_chronik: native::log_print_chronik,
    });
    let root = checked_root(root)?;
    check_entries(&root)?;
    let lease = if service {
        service_lease(&root)?
    } else {
        let mut lock = OpenOptions::new().write(true).create_new(true)
            .open(root.join("probe.lock"))?;
        let lease = Lease { root: root.clone(), service_lock: None };
        lock.write_all(b"fixture-exclusive; stale lock requires external review\n")?;
        lock.sync_all()?;
        lease
    };
    let node = Arc::new(Node {
        bridge: if service { ffi::make_service_startup_bridge(access)? }
                else { ffi::make_startup_bridge(access)? },
    });
    let mut expected = identity(node.bridge.get_genesis_hash().data);
    if let Some(plugins) = plugins {
        expected = plugin_identity(node.bridge.get_genesis_hash().data, &plugins.binding)?;
        if plugins.is_operator() {
            // Separate identity namespace; never adopt a synthetic-fixture DB.
            expected = String::from_utf8(expected)?.replace("plugin_fixture_sha256", "operator_bundle_sha256").into_bytes();
        }
    }
    let id_path = root.join("ergon-identity.json");
    if id_path.exists() {
        if fs::read(&id_path)? != expected {
            bail!("Archival identity mismatch; no database opened");
        }
    } else {
        if root.join("indexes").exists() {
            bail!("Unbound existing database rejected");
        }
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(id_path)?;
        file.write_all(&expected)?;
        file.sync_all()?;
        fs::File::open(&root)?.sync_all()?;
    }
    let tip = native::get_block_info(node.bridge.get_chain_tip()?);
    let generation = node.bridge.chain_generation();
    let revision = node.bridge.mempool_revision();
    let result = (|| -> Result<Option<ChronikIndexer>> {
        if let Some(hook) = hook {
            // Run after branch capture, before DB/disk work; requests
            // remain absent. Deterministic source-drift testing only.
            hook.run()?;
        }
        let mut indexer = ChronikIndexer::setup(
            indexer_params(&root, plugins),
            node.clone(),
        )?;
        indexer.resync_indexer(&node)?;
        if service && ffi::service_startup_cancelled() {
            indexer.stop()?; // A real flush error still takes the failure path.
            return Ok(None); // Upstream resync itself permits joined cancellation.
        }
        // Strict fixtures own copied metadata and require its complete fence.
        // Service startup borrows immutable native ancestors until the joined
        // owner ends; reads capture/check mutable storage independently.
        if !service {
            ffi::finish_startup_bridge(&node.bridge)?;
            ffi::archival_fixture_guard(access)?;
        }
        let indexed = indexer.blocks(&node).blockchain_info()?;
        if (!service && !node
            .bridge
            .matches_source(tip.hash, tip.height, generation, revision))
            || indexed.tip_height != tip.height
            || indexed.tip_hash != tip.hash
        {
            bail!("Incomplete resync or source tip drift");
        }
        Ok(Some(indexer))
    })();
    let indexer = match result {
        Ok(Some(i)) => i,
        Ok(None) => bail!("Service startup cancelled; bound database preserved"),
        Err(err) => {
            failure(&root, &node);
            return Err(err);
        }
    };
    let mut probe = Box::new(ArchivalProbe {
        indexer: Some(Arc::new(RwLock::new(indexer))),
        node,
        _lease: lease,
        bound_height: tip.height,
        bound_hash: tip.hash,
        bound_generation: generation,
        bound_revision: revision,
        access_address: access as *const native::ErgonNodeAccess as usize,
        live_fixture: false,
        reconciling: false,
        captured_chain: Vec::new(),
        captured_pool: Vec::new(),
        pool_chain_generation: 0,
        last_pool_ops: ffi::PoolApplyStats {
            removed: 0,
            added: 0,
            retained: 0,
        },
        applied_capture: None,
        request_gate: RequestGate::new(),
        response_latch: ResponseLatch::new(),
        service: None,
        plugins: plugins.cloned(),
    });
    // No requests or callback worker exist yet. Capture actual native pool
    // coins/times parent-first and reconcile through unchanged upstream
    // writers before this owner can be returned or published.
    if service {
        // Clone only the Arc owner, not a native borrow into the Rust box.
        let node = probe.node.clone();
        let prepared = (|| -> Result<bool> {
            if !ffi::capture_service_startup(&mut probe, &node.bridge, access)? {
                return Ok(false);
            }
            Ok(ffi::finish_service_startup_bridge(&node.bridge)?)
        })();
        match prepared {
            Ok(true) => (),
            Ok(false) => bail!("Service startup cancelled; bound database preserved"),
            Err(error) => { probe.fail_reconcile(); return Err(error); }
        }
    }
    if !ffi::reconcile_startup(&mut probe, access)? && !service {
        probe.fail_reconcile();
        bail!("Native source busy during startup capture; index unavailable");
    }
    Ok(probe)
}
impl ArchivalProbe {
    pub fn seed_service_startup(&mut self, source: &CapturedSource,
                               access: &native::ErgonNodeAccess) -> Result<()> {
        ffi::archival_database_guard(access)?;
        if self._lease.service_lock.is_none() || source.owner != self.access_address
            || source.owner != access as *const _ as usize || !self.captured_chain.is_empty()
            || source.height != self.bound_height || source.hash != self.bound_hash
            || source.generation != self.bound_generation || source.revision != self.bound_revision
            || source.start_height != (self.bound_height + 1 - RETAINED_BLOCKS as i32).max(0)
            || source.blocks.first().map(|b| b.block.height) != Some(source.start_height)
            || source.blocks.len() != (source.height - source.start_height + 1) as usize
            || source.blocks.is_empty() || source.blocks.len() > RETAINED_BLOCKS
            || source.blocks.last().map(|b| b.block.hash) != Some(source.hash)
            || source.blocks.last().map(|b| b.block.height) != Some(source.height)
            || !source.mempool.is_empty()
            || source.blocks.iter().map(|b| b.wire_bytes).sum::<u64>() > 64 * 1024 * 1024 {
            bail!("Invalid service startup retained source");
        }
        let indexed = self.indexer.as_ref().ok_or_else(|| Report::msg("Closed startup index"))?
            .blocking_read().blocks(&self.node).blockchain_info()?;
        if indexed.tip_height != source.height || indexed.tip_hash != source.hash {
            bail!("Startup index/captured old branch disagreement");
        }
        self.captured_chain = source.blocks.clone();
        Ok(())
    }
    pub fn capture_inventory(
        &self,
        node: &native::ErgonNodeAccess,
    ) -> Result<Vec<ffi::CaptureEntry>> {
        ffi::archival_database_guard(node)?;
        if let Some(plugins) = &self.plugins {
            plugins.check()?;
        }
        if self.access_address != node as *const _ as usize {
            bail!("Capture inventory owner mismatch");
        }
        Ok(self
            .captured_chain
            .iter()
            .map(|entry| ffi::CaptureEntry {
                height: entry.block.height,
                hash: entry.block.hash,
                file_num: entry.block.file_num,
                data_pos: entry.block.data_pos,
                undo_pos: entry.block.undo_pos,
                wire_bytes: entry.wire_bytes,
            })
            .collect())
    }
    pub fn seed_capture(
        &self,
        source: &mut CapturedSource,
        count: usize,
        node: &native::ErgonNodeAccess,
    ) -> Result<()> {
        ffi::archival_database_guard(node)?;
        if self.access_address != node as *const _ as usize
            || source.owner != self.access_address
            || !source.blocks.is_empty()
            || count > self.captured_chain.len()
            || count > (source.height - source.start_height + 1) as usize
            || (count > 0 && self.captured_chain[0].block.height != source.start_height)
        {
            bail!("Invalid retained capture prefix");
        }
        // Share immutable owned payload, not deep-clone the prefix or borrow
        // a native index. C++ independently compares every prefix binding.
        source
            .blocks
            .extend(self.captured_chain[..count].iter().cloned());
        Ok(())
    }
    pub fn pool_capture_inventory(
        &self,
        node: &native::ErgonNodeAccess,
    ) -> Result<Vec<ffi::PoolCaptureEntry>> {
        ffi::archival_database_guard(node)?;
        if self.access_address != node as *const _ as usize {
            bail!("Pool inventory owner mismatch");
        }
        Ok(self
            .captured_pool
            .iter()
            .map(|row| ffi::PoolCaptureEntry {
                txid: row.entry.tx.txid().to_bytes(),
                time: row.entry.time_first_seen,
                wire_bytes: row.wire_bytes,
                chain_generation: self.pool_chain_generation,
            })
            .collect())
    }
    pub fn seed_pool_entry(
        &self,
        source: &mut CapturedSource,
        index: usize,
        node: &native::ErgonNodeAccess,
    ) -> Result<()> {
        ffi::archival_database_guard(node)?;
        if source.owner != self.access_address
            || source.generation != self.pool_chain_generation
            || index >= self.captured_pool.len()
        {
            bail!("Invalid retained pool entry");
        }
        source.mempool.push(self.captured_pool[index].clone());
        Ok(())
    }
    pub fn pool_apply_stats(&self) -> ffi::PoolApplyStats {
        ffi::PoolApplyStats {
            removed: self.last_pool_ops.removed,
            added: self.last_pool_ops.added,
            retained: self.last_pool_ops.retained,
        }
    }
    pub fn request_gate(&self) -> Box<RequestGate> {
        Box::new(self.request_gate.clone())
    }
    pub fn response_latch(&self) -> Box<ResponseLatch> {
        Box::new(self.response_latch.clone())
    }
    pub fn start_http_fixture(&mut self) -> Result<u16> {
        self.check_source()?;
        self.bind_http()
    }
    pub fn start_regtest_http(&mut self) -> Result<u16> {
        // Binding a listener is not publication of data: the unchanged request
        // gate returns 503 until a complete source-current capture is published.
        if self._lease.service_lock.is_none() || self.captured_chain.is_empty()
            || ffi::service_startup_cancelled() {
            bail!("Invalid or cancelled regtest HTTP owner");
        }
        self.bind_http()
    }
    fn bind_http(&mut self) -> Result<u16> {
        if self.service.is_some() {
            bail!("HTTP fixture already active");
        }
        use chronik_http::{
            server::{ChronikServer, ChronikServerParams, ChronikSettings},
            subscription_limits::SubscriptionLimiter,
        };
        let address = "127.0.0.1:0".parse()?;
        let runtime = crate::ergon_runtime::JoinedRuntime::new(
            tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .max_blocking_threads(2)
                .enable_all()
                .build()?,
        );
        let handle = runtime
            .handle()
            .ok_or_else(|| Report::msg("Missing fixture runtime"))?;
        let (_, notify) = chronik_indexer::pause::Pause::new_pair(false);
        let server = {
            let _entered = handle.enter();
            ChronikServer::setup(ChronikServerParams {
                hosts: vec![address],
                indexer: self
                    .indexer
                    .as_ref()
                    .ok_or_else(|| Report::msg("Closed fixture indexer"))?
                    .clone(),
                node: self.node.clone(),
                pause_notify: Arc::new(notify),
                settings: ChronikSettings {
                    ws_ping_interval: std::time::Duration::from_secs(60),
                    enable_cors: false,
                },
                subscription_limiter: SubscriptionLimiter::new_ref(8, 8),
            })?
        };
        let port = server.single_bound_addr()?.port();
        let gate = self.request_gate.clone();
        let node = self.node.clone();
        let latch = self.response_latch.clone();
        let plugins = match &self.plugins {
            None => crate::ergon_plugin_access::PluginAccess::Disabled,
            Some(owner) if owner.is_operator() => crate::ergon_plugin_access::PluginAccess::Operator(
                Arc::new(owner.context.plugins().iter().map(|p| p.module_name.clone()).collect())),
            Some(_) => crate::ergon_plugin_access::PluginAccess::Synthetic,
        };
        let task = handle.spawn(async move {
            server
                .serve_with_router(|router| {
                    crate::ergon_service::wrap(router, gate, node, latch, plugins)
                })
                .await
        });
        self.service = Some(HttpFixture { runtime, task });
        Ok(port)
    }
    pub fn stop_http_fixture(&mut self) -> Result<()> {
        self.request_gate.close();
        if let Some(service) = self.service.take() {
            service.runtime.stop();
            if !service.task.is_finished() {
                bail!("HTTP fixture did not join");
            }
            if self
                .indexer
                .as_ref()
                .is_some_and(|indexer| Arc::strong_count(indexer) != 1)
            {
                bail!("HTTP fixture still retains the indexer");
            }
        }
        Ok(())
    }
    pub fn runtime_probe(&mut self) -> Result<ffi::RuntimeObservation> {
        drop(self.indexer()?);
        if self.service.is_some() {
            bail!("Active gated HTTP fixture");
        }
        let indexer = self
            .indexer
            .take()
            .ok_or_else(|| Report::msg("Closed runtime fixture index"))?;
        let result = exercise_runtime(&indexer, &self.node);
        // Recover the actual indexer only after every runtime-backed user is
        // gone. A strong-reference leak refuses recovery and quarantines.
        if Arc::strong_count(&indexer) != 1 {
            self.indexer = Some(indexer);
            self.fail_reconcile();
            bail!("Runtime still owns the indexer after shutdown");
        }
        self.indexer = Some(indexer);
        if result.is_err() {
            self.fail_reconcile();
        }
        result
    }
    pub fn apply_capture(
        &mut self,
        source: &CapturedSource,
        node: &native::ErgonNodeAccess,
    ) -> Result<()> {
        ffi::archival_database_guard(node)?;
        if source.owner != self.access_address || source.owner != node as *const _ as usize {
            bail!("Captured source owner mismatch");
        }
        if source.blocks.len() != (source.height - source.start_height + 1) as usize
            || source.blocks.first().map(|b| b.block.height) != Some(source.start_height)
            || source.blocks.last().map(|b| b.block.hash) != Some(source.hash)
            || source.blocks.iter().map(|b| b.wire_bytes).sum::<u64>()
                + source.mempool.iter().map(|e| e.wire_bytes).sum::<u64>()
                > 64 * 1024 * 1024
            || source.mempool.len() > 4096
        {
            bail!("Incomplete captured source");
        }
        if self
            .captured_chain
            .first()
            .is_some_and(|b| b.block.height != source.start_height)
        {
            bail!("Captured anchor mismatch");
        }
        // This process owns immutable native storage bindings. A same-hash
        // relocation is not an ordinary reorg: existing DB positions and the
        // positioned-reader catalogue would need a separately owned rebuild.
        // Refuse before touching DB rather than silently rebind that storage.
        for (old, new) in self.captured_chain.iter().zip(&source.blocks) {
            if old.block.hash != new.block.hash {
                break;
            }
            if old.block.file_num != new.block.file_num
                || old.block.data_pos != new.block.data_pos
                || old.block.undo_pos != new.block.undo_pos
            {
                bail!("Retained native storage moved; rebuild required");
            }
        }
        self.live_fixture = true;
        self.reconciling = true; // Remains unavailable if the source changes.
        self.applied_capture = None;
        let epoch = self.request_gate.begin();
        let indexer = self
            .indexer
            .as_ref()
            .ok_or_else(|| Report::msg("Closed index"))?;
        let mut indexer = indexer.blocking_write();
        let indexed = indexer.blocks(&self.node).blockchain_info()?;
        if self.captured_chain.is_empty() {
            // First capture after the already checked startup resync. Do not
            // invent an old branch if it has changed before the capture.
            if indexed.tip_height != source.height || indexed.tip_hash != source.hash {
                bail!("Unbound initial captured chain");
            }
            self.captured_chain = source.blocks.clone();
        }
        let old = self
            .captured_chain
            .last()
            .ok_or_else(|| Report::msg("Missing captured tip"))?;
        if indexed.tip_hash != old.block.hash || indexed.tip_height != old.block.height {
            bail!("Indexed/captured old branch disagreement");
        }
        let common = self
            .captured_chain
            .iter()
            .zip(&source.blocks)
            .take_while(|(a, b)| {
                a.block.hash == b.block.hash
                    && a.block.file_num == b.block.file_num
                    && a.block.data_pos == b.block.data_pos
                    && a.block.undo_pos == b.block.undo_pos
            })
            .count();
        if common == 0 {
            bail!("Captured retained anchor mismatch");
        }
        let chain_unchanged = common == self.captured_chain.len()
            && common == source.blocks.len()
            && source.generation == self.pool_chain_generation;
        self.last_pool_ops = ffi::PoolApplyStats {
            removed: 0,
            added: 0,
            retained: 0,
        };
        // Let upstream make_chronik_block retain known first-seen times from
        // the old indexed overlay BEFORE removing it. No timestamp fabrication.
        let additions: Vec<_> = source.blocks[common..]
            .iter()
            .map(|block| indexer.make_chronik_block(block.block.clone()))
            .collect();
        // Upstream writers and queries remain authoritative. This loop only
        // schedules their inputs from owned, native-validated capture bytes.
        let desired: BTreeMap<_, _> = source
            .mempool
            .iter()
            .map(|row| (row.entry.tx.txid(), row))
            .collect();
        if desired.len() != source.mempool.len() {
            bail!("Duplicate captured pool entry");
        }
        let mut remove: BTreeSet<TxId> = indexer
            .mempool()
            .txs()
            .iter()
            .filter(|(id, old)| {
                !chain_unchanged
                    || desired.get(*id).is_none_or(|new| {
                        old.tx != new.entry.tx || old.time_first_seen != new.entry.time_first_seen
                    })
            })
            .map(|(id, _)| *id)
            .collect();
        // If a parent is replaced (even only its native arrival time), its
        // descendants must be removed/re-added through upstream writers too.
        loop {
            let descendants: Vec<_> = indexer
                .mempool()
                .txs()
                .iter()
                .filter(|(id, tx)| {
                    !remove.contains(*id)
                        && tx
                            .tx
                            .inputs
                            .iter()
                            .any(|i| remove.contains(&i.prev_out.txid))
                })
                .map(|(id, _)| *id)
                .collect();
            if descendants.is_empty() {
                break;
            }
            remove.extend(descendants);
        }
        while !remove.is_empty() {
            let txs = indexer.mempool().txs();
            let mut leaves: Vec<TxId> = remove
                .iter()
                .filter(|id| {
                    !remove
                        .iter()
                        .any(|child| txs[child].tx.inputs.iter().any(|i| i.prev_out.txid == **id))
                })
                .copied()
                .collect();
            leaves.sort();
            if leaves.is_empty() {
                bail!("Indexed mempool dependency cycle");
            }
            for id in leaves {
                indexer.handle_tx_removed_from_mempool(id)?;
                remove.remove(&id);
                self.last_pool_ops.removed += 1;
            }
        }
        for old in self.captured_chain[common..].iter().rev() {
            let block = indexer.make_chronik_block(old.block.clone());
            indexer.handle_block_disconnected(block)?;
        }
        for block in additions {
            indexer.handle_block_connected(block)?;
        }
        for entry in &source.mempool {
            let entry = &entry.entry;
            if indexer.mempool().txs().contains_key(&entry.tx.txid()) {
                self.last_pool_ops.retained += 1;
                continue;
            }
            indexer.handle_tx_added_to_mempool(MempoolTx {
                tx: entry.tx.clone(),
                time_first_seen: entry.time_first_seen,
            })?;
            self.last_pool_ops.added += 1;
        }
        let mut expected: Vec<_> = source.mempool.iter().map(|e| e.entry.tx.txid()).collect();
        expected.sort();
        let mut actual: Vec<_> = indexer.mempool().txs().keys().copied().collect();
        actual.sort();
        let tip = indexer.blocks(&self.node).blockchain_info()?;
        if actual != expected || tip.tip_hash != source.hash || tip.tip_height != source.height {
            bail!("Captured/actual index disagreement");
        }
        // Even an unpublished capture must become the next rewind origin.
        self.captured_chain =
            source.blocks[source.blocks.len().saturating_sub(RETAINED_BLOCKS)..].to_vec();
        self.captured_pool = source.mempool.clone();
        self.pool_chain_generation = source.generation;
        self.applied_capture = Some((
            source.hash,
            source.height,
            source.generation,
            source.revision,
            epoch,
        ));
        Ok(())
    }
    pub fn publish_capture(
        &mut self,
        source: &CapturedSource,
        node: &native::ErgonNodeAccess,
    ) -> Result<bool> {
        // Called only after C++ rechecks the exact native source under locks.
        // No database operation in this publication phase.
        source.owner(node)?;
        let Some((hash, height, generation, revision, epoch)) = self.applied_capture else {
            bail!("Missing capture publication");
        };
        if self.access_address != source.owner
            || !self.reconciling
            || (hash, height, generation, revision)
                != (
                    source.hash,
                    source.height,
                    source.generation,
                    source.revision,
                )
        {
            bail!("Invalid capture publication");
        }
        if !self.request_gate.publish(
            epoch,
            Binding {
                hash,
                height,
                generation,
                revision,
            },
        ) {
            return Ok(false);
        }
        self.bound_height = source.height;
        self.bound_hash = source.hash;
        self.bound_generation = source.generation;
        self.bound_revision = source.revision;
        self.reconciling = false;
        self.applied_capture = None;
        Ok(true)
    }
    pub fn assert_current_capture(&self, node: &native::ErgonNodeAccess) -> Result<()> {
        self.check_owner(node)?;
        if self.reconciling
            || !self.node.bridge.matches_source(
                self.bound_hash,
                self.bound_height,
                self.bound_generation,
                self.bound_revision,
            )
        {
            bail!("Native source changed before callback registration");
        }
        Ok(())
    }
    /// Scalar observation only: no indexer/DB access or readiness promotion.
    /// The worker tries both native fences before polling this binding.
    pub fn source_current(&self) -> bool {
        !self.reconciling
            && self.node.bridge.matches_source(
                self.bound_hash,
                self.bound_height,
                self.bound_generation,
                self.bound_revision,
            )
    }
    fn check_source(&self) -> Result<()> {
        if self.reconciling
            || !self.node.bridge.matches_source(
                self.bound_hash,
                self.bound_height,
                self.bound_generation,
                self.bound_revision,
            )
        {
            if !self.live_fixture || self.node.bridge.shutdown_requested() {
                failure(&self._lease.root, &self.node);
            }
            bail!("Archival view stale or unavailable; reconcile before querying");
        }
        Ok(())
    }
    fn indexer(&self) -> Result<RwLockReadGuard<'_, ChronikIndexer>> {
        self.check_source()?;
        Ok(self
            .indexer
            .as_ref()
            .ok_or_else(|| Report::msg("Closed probe"))?
            .blocking_read())
    }
    pub fn snapshot(&self) -> Result<ffi::Snapshot> {
        let indexer = self.indexer()?;
        let query = indexer.blocks(&self.node);
        let mut digest = Sha256::digest(b"ergon-upstream-block-and-tx-protobuf/v1").to_be_bytes();
        let mut num_txs = 0;
        // Logical query commitment, not a byte-level DB reproducibility claim.
        for height in 0..=self.bound_height {
            let block = query.by_hash_or_height(height.to_string())?;
            let mut records = vec![block.encode_to_vec()];
            let first = query.block_txs(height.to_string(), 0, 200)?;
            num_txs += u64::from(first.num_txs);
            let num_pages = first.num_pages;
            records.push(first.encode_to_vec());
            for page in 1..num_pages {
                records.push(
                    query
                        .block_txs(height.to_string(), page as usize, 200)?
                        .encode_to_vec(),
                );
            }
            for record in records {
                let mut input = digest.to_vec();
                input.extend_from_slice(&(record.len() as u64).to_le_bytes());
                input.extend_from_slice(&record);
                digest = Sha256::digest(input).to_be_bytes();
            }
        }
        self.check_source()?;
        Ok(ffi::Snapshot {
            height: self.bound_height,
            hash: self.bound_hash,
            logical_digest: digest,
            num_txs,
        })
    }
    pub fn tx_view(&self, txid: [u8; 32], output: usize) -> Result<ffi::TxView> {
        let indexer = self.indexer()?;
        let mempool_coin = indexer
            .mempool()
            .tx(&TxId::from(txid))
            .and_then(|tx| tx.tx.inputs.first())
            .and_then(|input| input.coin.as_ref());
        let tx = indexer.txs(&self.node).tx_by_id(TxId::from(txid))?;
        if tx.txid != txid {
            bail!("Query transaction identity mismatch");
        }
        let out = tx
            .outputs
            .get(output)
            .ok_or_else(|| Report::msg("Missing requested output"))?;
        let spent = out.spent_by.as_ref();
        let spender = match spent {
            Some(s) => s.txid.as_slice().try_into()?,
            None => [0; 32],
        };
        self.check_source()?;
        Ok(ffi::TxView {
            height: tx.block.map_or(-1, |block| block.height),
            output_spent: spent.is_some(),
            spender,
            spender_input: spent.map_or(0, |s| s.input_idx),
            input_value: tx.inputs.first().map_or(0, |i| i.sats),
            time_first_seen: tx.time_first_seen,
            mempool_input_height: mempool_coin.map_or(-2, |coin| coin.height),
            mempool_input_coinbase: mempool_coin.is_some_and(|coin| coin.is_coinbase),
        })
    }
    pub fn script_view(&self, bytes: &[u8]) -> Result<ffi::ScriptView> {
        let indexer = self.indexer()?;
        let script = Script::new(bytes.to_vec().into());
        let history = indexer.script_history(&self.node)?.confirmed_txs(
            GroupMember::Member(&script),
            0,
            200,
        )?;
        let utxos = indexer.script_utxos(&self.node)?.utxos(&script)?;
        let unconfirmed = indexer
            .script_history(&self.node)?
            .unconfirmed_txs(GroupMember::Member(&script))?;
        self.check_source()?;
        Ok(ffi::ScriptView {
            confirmed: history.num_txs,
            utxos: utxos.len() as u64,
            unconfirmed: unconfirmed.num_txs,
        })
    }
    fn check_owner(&self, node: &native::ErgonNodeAccess) -> Result<()> {
        if self.access_address != node as *const native::ErgonNodeAccess as usize {
            bail!("Mismatched native source owner");
        }
        ffi::archival_reconcile_guard(node)?;
        Ok(())
    }
    pub fn begin_reconcile(&mut self, node: &native::ErgonNodeAccess) -> Result<()> {
        self.check_owner(node)?;
        if self.service.is_some() {
            bail!("Lock-fenced comparison cannot run with an HTTP fixture");
        }
        if self.reconciling {
            bail!("Nested reconciliation");
        }
        self.live_fixture = true;
        self.reconciling = true;
        self.request_gate.invalidate();
        let indexer = self
            .indexer
            .as_ref()
            .ok_or_else(|| Report::msg("Closed index"))?;
        let mut indexer = indexer.blocking_write();
        // Child-first old overlay removal, then real upstream chain resync.
        // No live subscribers; NOT a callback or websocket notification policy.
        while !indexer.mempool().txs().is_empty() {
            let txs = indexer.mempool().txs();
            let mut leaves: Vec<TxId> = txs
                .keys()
                .filter(|id| {
                    !txs.values()
                        .any(|tx| tx.tx.inputs.iter().any(|input| input.prev_out.txid == **id))
                })
                .copied()
                .collect();
            leaves.sort();
            if leaves.is_empty() {
                bail!("Indexed mempool dependency cycle");
            }
            for id in leaves {
                indexer.handle_tx_removed_from_mempool(id)?;
            }
        }
        indexer.resync_indexer(&self.node)?;
        Ok(())
    }
    pub fn add_native_mempool(
        &mut self,
        tx: &native::CTransaction,
        coins: &cxx::CxxVector<native::CCoin>,
        time: i64,
        node: &native::ErgonNodeAccess,
    ) -> Result<()> {
        self.check_owner(node)?;
        if !self.reconciling {
            bail!("No native source barrier");
        }
        let tx = Tx::from(native::bridge_tx(tx, coins)?);
        self.indexer
            .as_ref()
            .ok_or_else(|| Report::msg("Closed index"))?
            .blocking_write()
            .handle_tx_added_to_mempool(MempoolTx {
                tx,
                time_first_seen: time,
            })?;
        Ok(())
    }
    pub fn finish_reconcile(&mut self, ids: &[u8], node: &native::ErgonNodeAccess) -> Result<()> {
        self.check_owner(node)?;
        if !self.reconciling || ids.len() % 32 != 0 {
            bail!("Invalid reconciliation boundary");
        }
        let indexer = self
            .indexer
            .as_ref()
            .ok_or_else(|| Report::msg("Closed index"))?;
        let indexer = indexer.blocking_read();
        let mut actual: Vec<_> = indexer.mempool().txs().keys().copied().collect();
        actual.sort();
        let flattened: Vec<u8> = actual
            .iter()
            .flat_map(|id| id.as_bytes().iter().copied())
            .collect();
        if flattened != ids {
            bail!("Native/indexed mempool identity disagreement");
        }
        let tip = native::get_block_info(self.node.bridge.get_chain_tip()?);
        let indexed = indexer.blocks(&self.node).blockchain_info()?;
        if indexed.tip_hash != tip.hash || indexed.tip_height != tip.height {
            bail!("Incomplete chain reconciliation");
        }
        self.bound_height = tip.height;
        self.bound_hash = tip.hash;
        self.bound_generation = ffi::archival_chain_generation(node);
        self.bound_revision = ffi::archival_mempool_revision(node);
        self.reconciling = false;
        Ok(())
    }
    pub fn fail_reconcile(&mut self) {
        self.request_gate.fail();
        self.reconciling = true;
        failure(&self._lease.root, &self.node);
    }
}
impl Drop for ArchivalProbe {
    fn drop(&mut self) {
        if self.stop_http_fixture().is_err() {
            failure(&self._lease.root, &self.node);
        }
        if let Some(indexer) = self.indexer.take() {
            if indexer.blocking_read().stop().is_err() {
                failure(&self._lease.root, &self.node);
            }
            drop(indexer);
        }
    }
}

// Ephemeral loopback fixture using the UNMODIFIED upstream HTTP/WS router.
// No daemon gate, endpoint, fixed port or service deployment is introduced.
fn exercise_runtime(
    indexer: &Arc<tokio::sync::RwLock<ChronikIndexer>>,
    node: &Arc<Node>,
) -> Result<ffi::RuntimeObservation> {
    use crate::ergon_runtime::JoinedRuntime;
    use chronik_http::{
        server::{ChronikServer, ChronikServerParams, ChronikSettings},
        subscription_limits::SubscriptionLimiter,
    };
    use chronik_indexer::pause::Pause;
    use std::{
        io::{Read, Write},
        net::{TcpListener, TcpStream},
        sync::{
            atomic::{AtomicBool, Ordering},
            mpsc,
        },
        time::{Duration, Instant},
    };
    let native_refs = Arc::strong_count(node);
    let reservation = TcpListener::bind("127.0.0.1:0")?;
    let address = reservation.local_addr()?;
    drop(reservation);
    let runtime = Arc::new(JoinedRuntime::new(
        tokio::runtime::Builder::new_multi_thread()
            .worker_threads(2)
            .max_blocking_threads(2)
            .enable_all()
            .build()?,
    ));
    let handle = runtime
        .handle()
        .ok_or_else(|| Report::msg("Missing runtime"))?;
    let (_, pause_notify) = Pause::new_pair(false);
    let server = {
        let _entered = handle.enter();
        ChronikServer::setup(ChronikServerParams {
            hosts: vec![address],
            indexer: indexer.clone(),
            node: node.clone(),
            pause_notify: Arc::new(pause_notify),
            settings: ChronikSettings {
                ws_ping_interval: Duration::from_secs(60),
                enable_cors: false,
            },
            subscription_limiter: SubscriptionLimiter::new_ref(8, 8),
        })?
    };
    let server_task = handle.spawn(async move { server.serve().await });
    let mut http = TcpStream::connect_timeout(&address, Duration::from_secs(2))?;
    http.set_read_timeout(Some(Duration::from_secs(2)))?;
    http.write_all(
        b"GET /blockchain-info HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n",
    )?;
    let mut response = Vec::new();
    http.read_to_end(&mut response)?;
    if !response.starts_with(b"HTTP/1.1 200 ") {
        bail!("Actual upstream HTTP fixture failed");
    }
    let mut websocket = TcpStream::connect_timeout(&address, Duration::from_secs(2))?;
    websocket.set_read_timeout(Some(Duration::from_secs(2)))?;
    websocket.write_all(b"GET /ws HTTP/1.1\r\nHost: localhost\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n")?;
    let mut upgrade = Vec::new();
    while !upgrade.ends_with(b"\r\n\r\n") {
        let mut byte = [0];
        if websocket.read(&mut byte)? != 1 || upgrade.len() > 4096 {
            bail!("Incomplete WS upgrade");
        }
        upgrade.push(byte[0]);
    }
    if !upgrade.starts_with(b"HTTP/1.1 101 ") {
        bail!("Actual upstream WS fixture failed");
    }
    // Keep an HTTP connection awaiting headers, and a real WS connection alive
    // during shutdown. Their independently spawned tasks must also be dropped.
    let mut partial_http = TcpStream::connect_timeout(&address, Duration::from_secs(2))?;
    partial_http.write_all(b"GET /blockchain-info HTTP/1.1\r\nHost: localhost\r\n")?;
    struct PendingOwner {
        _node: Arc<Node>,
        _indexer: Arc<tokio::sync::RwLock<ChronikIndexer>>,
        dropped: Arc<AtomicBool>,
    }
    impl Drop for PendingOwner {
        fn drop(&mut self) {
            self.dropped.store(true, Ordering::SeqCst);
        }
    }
    let dropped = Arc::new(AtomicBool::new(false));
    let (async_enter_tx, async_enter_rx) = mpsc::channel();
    let pending = PendingOwner {
        _node: node.clone(),
        _indexer: indexer.clone(),
        dropped: dropped.clone(),
    };
    handle.spawn(async move {
        let _owner = pending;
        let _ = async_enter_tx.send(());
        std::future::pending::<()>().await;
    });
    async_enter_rx.recv_timeout(Duration::from_secs(2))?;
    let expected_genesis = node.bridge.get_genesis_hash().data;
    let (block_enter_tx, block_enter_rx) = mpsc::channel();
    let (release_tx, release_rx) = mpsc::channel();
    let block_node = node.clone();
    let block_indexer = indexer.clone();
    let blocking = handle.spawn_blocking(move || {
        let _ = block_enter_tx.send(());
        // Bounded synthetic latch only; stop itself has no unsafe timeout.
        release_rx
            .recv_timeout(Duration::from_secs(5))
            .map_err(|_| "Blocking fixture latch timed out")?;
        let tip = block_indexer
            .blocking_read()
            .blocks(&block_node)
            .blockchain_info()
            .map_err(|_| "Shared index query failed")?;
        if tip.tip_height < 0 {
            return Err("Invalid indexed tip");
        }
        Ok(block_node.bridge.get_genesis_hash().data)
    });
    block_enter_rx.recv_timeout(Duration::from_secs(2))?;
    let (done_tx, done_rx) = mpsc::channel();
    let (second_tx, second_rx) = mpsc::channel();
    let waited = std::thread::scope(|scope| -> Result<bool> {
        let rt = runtime.clone();
        let done = done_tx.clone();
        scope.spawn(move || {
            rt.stop();
            let _ = done.send(());
        });
        let deadline = Instant::now() + Duration::from_secs(2);
        while runtime.handle().is_some() {
            if Instant::now() > deadline {
                bail!("Shutdown did not close runtime admission");
            }
            std::thread::sleep(Duration::from_millis(1));
        }
        let rt = runtime.clone();
        let done = done_tx.clone();
        scope.spawn(move || {
            let _ = second_tx.send(());
            rt.stop();
            let _ = done.send(());
        });
        second_rx.recv_timeout(Duration::from_secs(2))?;
        // Neither interrupt caller can return while native/DB blocking work
        // is still latched, even though runtime Option has already been taken.
        let waited = done_rx.recv_timeout(Duration::from_millis(50)).is_err();
        release_tx.send(())?;
        done_rx.recv_timeout(Duration::from_secs(2))?;
        done_rx.recv_timeout(Duration::from_secs(2))?;
        Ok(waited)
    })?;
    let native_result = handle
        .block_on(blocking)
        .map_err(|_| Report::msg("Blocking runtime task did not join"))?
        .map_err(Report::msg)?;
    if native_result != expected_genesis {
        bail!("Native resource changed before task completion");
    }
    runtime.stop(); // Concurrent shutdown and repeated shutdown are barriers.
    fn connection_closed(stream: &mut TcpStream) -> Result<bool> {
        stream.set_read_timeout(Some(Duration::from_secs(2)))?;
        let mut remaining = 4096;
        loop {
            let mut bytes = [0; 256];
            match stream.read(&mut bytes) {
                Ok(0) => return Ok(true),
                Ok(n) if n <= remaining => remaining -= n,
                Ok(_) => return Ok(false),
                Err(err) => {
                    return Ok(matches!(
                        err.kind(),
                        std::io::ErrorKind::ConnectionReset
                            | std::io::ErrorKind::ConnectionAborted
                            | std::io::ErrorKind::BrokenPipe
                    ));
                }
            }
        }
    }
    let observation = ffi::RuntimeObservation {
        http_ok: true,
        websocket_ok: true,
        blocking_waited: waited,
        concurrent_stop_waited: waited,
        async_dropped: dropped.load(Ordering::SeqCst),
        server_finished: server_task.is_finished(),
        indexer_unique: Arc::strong_count(indexer) == 1,
        native_refs_released: Arc::strong_count(node) == native_refs,
        listener_closed: TcpStream::connect_timeout(&address, Duration::from_millis(100)).is_err(),
        websocket_closed: connection_closed(&mut websocket)?,
        partial_http_closed: connection_closed(&mut partial_http)?,
    };
    if !observation.blocking_waited
        || !observation.async_dropped
        || !observation.server_finished
        || !observation.indexer_unique
        || !observation.native_refs_released
        || !observation.listener_closed
        || !observation.websocket_closed
        || !observation.partial_http_closed
    {
        bail!("Runtime shutdown ownership contradiction");
    }
    Ok(observation)
}

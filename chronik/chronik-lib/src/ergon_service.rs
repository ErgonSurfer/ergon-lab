// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
//! Fixture-only admission wrapper for the actual upstream router. No daemon
//! startup, replacement handler, indexing algorithm or public endpoint.
use abc_rust_error::Report;
use axum::{
    body::{to_bytes, Body},
    extract::{Request, State},
    http::{header, Method},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    Router,
};
use chronik_http::error::ReportError;
use chronik_indexer::indexer::Node;
use std::{
    collections::BTreeSet,
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};

#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) struct Binding {
    pub(crate) hash: [u8; 32],
    pub(crate) height: i32,
    pub(crate) generation: u64,
    pub(crate) revision: u64,
}
#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Busy,
    Ready,
    Failed,
    Closed,
}
struct Readiness {
    epoch: u64,
    mode: Mode,
    binding: Option<Binding>,
}
pub struct RequestGate {
    state: Arc<Mutex<Readiness>>,
    // Counts dispatches, not successful responses or unique users.
    dispatched: Arc<AtomicU64>,
}
impl Clone for RequestGate {
    fn clone(&self) -> Self {
        Self {
            state: self.state.clone(),
            dispatched: self.dispatched.clone(),
        }
    }
}
impl RequestGate {
    pub fn clone_handle(&self) -> Box<Self> {
        Box::new(self.clone())
    }
    pub(crate) fn new() -> Self {
        Self {
            state: Arc::new(Mutex::new(Readiness {
                epoch: 0,
                mode: Mode::Busy,
                binding: None,
            })),
            dispatched: Arc::new(AtomicU64::new(0)),
        }
    }
    pub fn invalidate(&self) {
        self.begin();
    }
    pub(crate) fn begin(&self) -> u64 {
        let mut state = self.state.lock().unwrap();
        if matches!(state.mode, Mode::Closed | Mode::Failed) {
            return state.epoch;
        }
        match state.epoch.checked_add(1) {
            Some(epoch) => {
                state.epoch = epoch;
                state.mode = Mode::Busy;
            }
            None => state.mode = Mode::Failed,
        }
        state.binding = None;
        state.epoch
    }
    pub(crate) fn publish(&self, epoch: u64, binding: Binding) -> bool {
        let mut state = self.state.lock().unwrap();
        // Already running DB work may finish during Stop, but cannot reopen
        // request admission. A failure is never turned into a healthy view.
        if state.mode == Mode::Closed {
            return true;
        }
        if state.mode != Mode::Busy || state.epoch != epoch {
            return false;
        }
        state.binding = Some(binding);
        state.mode = Mode::Ready;
        true
    }
    pub fn close(&self) {
        let mut state = self.state.lock().unwrap();
        state.mode = Mode::Closed;
        state.binding = None;
    }
    pub fn fail(&self) {
        let mut state = self.state.lock().unwrap();
        if state.mode != Mode::Closed {
            state.mode = Mode::Failed;
        }
        state.binding = None;
    }
    pub fn dispatched(&self) -> u64 {
        self.dispatched.load(Ordering::SeqCst)
    }
    fn permit(&self) -> Option<(u64, Binding)> {
        let state = self.state.lock().unwrap();
        if state.mode != Mode::Ready {
            return None;
        }
        Some((state.epoch, state.binding?))
    }
    fn current(&self, permit: (u64, Binding), node: &Node) -> bool {
        // No gate mutex is held while trying native locks; no lock inversion
        // with scalar publication under the native source fence.
        self.permit() == Some(permit)
            && node.bridge.matches_source(
                permit.1.hash,
                permit.1.height,
                permit.1.generation,
                permit.1.revision,
            )
            && self.permit() == Some(permit)
    }
}

// A deterministic async fixture latch after the REAL handler body is buffered,
// before publication. No daemon latch/hook or synchronous Tokio-thread block.
pub struct ResponseLatch {
    inner: Arc<LatchInner>,
}
struct LatchInner {
    armed: AtomicBool,
    entered: AtomicBool,
    release: tokio::sync::Notify,
}
impl Clone for ResponseLatch {
    fn clone(&self) -> Self {
        Self {
            inner: self.inner.clone(),
        }
    }
}
impl ResponseLatch {
    pub fn clone_handle(&self) -> Box<Self> {
        Box::new(self.clone())
    }
    pub(crate) fn new() -> Self {
        Self {
            inner: Arc::new(LatchInner {
                armed: AtomicBool::new(false),
                entered: AtomicBool::new(false),
                release: tokio::sync::Notify::new(),
            }),
        }
    }
    pub fn arm(&self) -> bool {
        !self.inner.entered.load(Ordering::SeqCst)
            && self
                .inner
                .armed
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
    }
    pub fn wait(&self, milliseconds: u32) -> bool {
        if milliseconds > 5000 {
            return false;
        }
        let deadline = Instant::now() + Duration::from_millis(milliseconds.into());
        while !self.inner.entered.load(Ordering::SeqCst) {
            if Instant::now() >= deadline {
                return false;
            }
            std::thread::sleep(Duration::from_millis(1));
        }
        true
    }
    pub fn release(&self) {
        self.inner.release.notify_one();
    }
    async fn pause(&self) {
        if self.inner.armed.swap(false, Ordering::SeqCst) {
            self.inner.entered.store(true, Ordering::SeqCst);
            self.inner.release.notified().await;
            self.inner.entered.store(false, Ordering::SeqCst);
        }
    }
}

#[derive(Clone)]
struct Admission {
    gate: RequestGate,
    node: Arc<Node>,
    latch: ResponseLatch,
    plugins: bool,
}
pub(crate) fn wrap(
    router: Router,
    gate: RequestGate,
    node: Arc<Node>,
    latch: ResponseLatch,
    plugins: bool,
) -> Router {
    router.layer(middleware::from_fn_with_state(
        Admission {
            gate,
            node,
            latch,
            plugins,
        },
        admit,
    ))
}
fn error(message: &'static str) -> Response {
    let mut response = ReportError(Report::msg(message)).into_response();
    response
        .headers_mut()
        .insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    response
}
fn supported(request: &Request, plugins: bool) -> bool {
    if request.method() != Method::GET || request.uri().path().contains('%') {
        return false;
    }
    let path: Vec<_> = request
        .uri()
        .path()
        .trim_start_matches('/')
        .split('/')
        .collect();
    match path.as_slice() {
        ["blockchain-info" | "chronik-info" | "unconfirmed-txs"] => true,
        ["block" | "block-header" | "block-txs" | "tx" | "raw-tx" | "token", id] => !id.is_empty(),
        ["blocks" | "block-headers", start, end] => !start.is_empty() && !end.is_empty(),
        ["script", kind, payload, "confirmed-txs" | "history" | "unconfirmed-txs" | "utxos"] => {
            !kind.is_empty() && !payload.is_empty()
        }
        ["token-id", id, "confirmed-txs" | "history" | "unconfirmed-txs" | "utxos"] => {
            !id.is_empty()
        }
        ["lokad-id", id, "confirmed-txs" | "history" | "unconfirmed-txs"] => !id.is_empty(),
        ["plugin", "ergon_public_probe", "groups"] => plugins,
        ["plugin", "ergon_public_probe", group, "utxos" | "confirmed-txs" | "history" | "unconfirmed-txs"] => {
            plugins && *group == "7075626c6963"
        }
        _ => false,
    }
}
fn supported_query(request: &Request) -> bool {
    let Some(query) = request.uri().query() else {
        return true;
    };
    let mut names = BTreeSet::new();
    for pair in query.split('&') {
        let Some((key, value)) = pair.split_once('=') else {
            return false;
        };
        if !names.insert(key)
            || !matches!(key, "page" | "page_size")
            || value.is_empty()
            || !value.bytes().all(|b| b.is_ascii_digit())
        {
            return false;
        }
        let Ok(value) = value.parse::<u32>() else {
            return false;
        };
        if (key == "page" && value > 1000) || (key == "page_size" && (value == 0 || value > 200)) {
            return false;
        }
    }
    true
}
async fn admit(State(state): State<Admission>, request: Request, next: Next) -> Response {
    if !supported(&request, state.plugins) {
        return error("501: Capability not enabled for this Ergon observer profile");
    }
    if !supported_query(&request) {
        return error("400: Query parameter not enabled for this Ergon observer profile");
    }
    let Some(permit) = state.gate.permit() else {
        return error("503: Ergon index view unavailable");
    };
    if !state.gate.current(permit, &state.node) {
        return error("503: Ergon index view unavailable");
    }
    state.gate.dispatched.fetch_add(1, Ordering::SeqCst);
    let response = next.run(request).await;
    let (mut parts, body) = response.into_parts();
    // Buffer before the final source check: no deferred query/body stream may
    // expose partial stale output. This is a fixture profile response limit.
    let Ok(bytes) = to_bytes(body, 8 * 1024 * 1024).await else {
        return error("503: Ergon response unavailable");
    };
    state.latch.pause().await;
    if !state.gate.current(permit, &state.node) {
        return error("503: Ergon index generation changed during request");
    }
    parts
        .headers
        .insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    Response::from_parts(parts, Body::from(bytes))
}

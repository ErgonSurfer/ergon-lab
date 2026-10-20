// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
//! Fixture-only admission wrapper for the actual upstream router. No daemon
//! startup, replacement handler, indexing algorithm or public endpoint.
use abc_rust_error::Report;
use axum::{
    body::{to_bytes, Body},
    extract::{ws::WebSocketUpgrade, ConnectInfo, FromRequestParts, Request, State},
    http::{header, Method},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    Router,
};
use chronik_http::error::ReportError;
use chronik_indexer::indexer::Node;
use prost::Message;
use std::{
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::{Duration, Instant},
    net::SocketAddr,
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
    terminal: tokio::sync::watch::Sender<bool>,
    continuity: tokio::sync::watch::Sender<u64>,
}
impl Clone for RequestGate {
    fn clone(&self) -> Self {
        Self {
            state: self.state.clone(),
            dispatched: self.dispatched.clone(),
            terminal: self.terminal.clone(),
            continuity: self.continuity.clone(),
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
            terminal: tokio::sync::watch::channel(false).0,
            continuity: tokio::sync::watch::channel(0).0,
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
            None => {
                state.mode = Mode::Failed;
                self.terminal.send_replace(true);
            },
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
        self.terminal.send_replace(true);
    }
    pub fn fail(&self) {
        let mut state = self.state.lock().unwrap();
        if state.mode != Mode::Closed {
            state.mode = Mode::Failed;
        }
        state.binding = None;
        self.terminal.send_replace(true);
    }
    // This is a continuity-loss signal, NOT replay of missed native events.
    // Existing sessions must refresh their HTTP baseline before resubscribing.
    pub fn notification_gap(&self) {
        self.continuity.send_modify(|epoch| {
            if let Some(next) = epoch.checked_add(1) { *epoch = next; }
            else { self.terminal.send_replace(true); }
        });
    }
    pub fn dispatched(&self) -> u64 {
        self.dispatched.load(Ordering::SeqCst)
    }
    async fn terminal(&self) {
        let mut signal = self.terminal.subscribe();
        while !*signal.borrow_and_update() {
            if signal.changed().await.is_err() { break; }
        }
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

// Capabilities are supplied only by the separately opted-in service owner.
#[derive(Clone)]
pub(crate) struct ClientAccess {
    indexer: chronik_http::server::ChronikIndexerRef,
    limiter: chronik_http::subscription_limits::SubscriptionLimiterRef,
    sockets: Arc<tokio::sync::Semaphore>,
    submissions: Arc<tokio::sync::Semaphore>,
}
impl ClientAccess {
    pub(crate) fn new(
        indexer: chronik_http::server::ChronikIndexerRef,
        limiter: chronik_http::subscription_limits::SubscriptionLimiterRef,
    ) -> Self {
        Self { indexer, limiter, sockets: Arc::new(tokio::sync::Semaphore::new(8)),
            submissions: Arc::new(tokio::sync::Semaphore::new(2)) }
    }
}
#[derive(Clone)]
struct Admission {
    gate: RequestGate,
    node: Arc<Node>,
    latch: ResponseLatch,
    plugins: crate::ergon_plugin_access::PluginAccess,
    clients: Option<ClientAccess>,
}
pub(crate) fn wrap(
    router: Router,
    gate: RequestGate,
    node: Arc<Node>,
    latch: ResponseLatch,
    plugins: crate::ergon_plugin_access::PluginAccess,
) -> Router {
    wrap_clients(router, gate, node, latch, plugins, None)
}
pub(crate) fn wrap_clients(
    router: Router, gate: RequestGate, node: Arc<Node>, latch: ResponseLatch,
    plugins: crate::ergon_plugin_access::PluginAccess, clients: Option<ClientAccess>,
) -> Router {
    router.layer(middleware::from_fn_with_state(
        Admission {
            gate,
            node,
            latch,
            plugins,
            clients,
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
fn supported(request: &Request, plugins: &crate::ergon_plugin_access::PluginAccess) -> bool {
    if request.uri().path().contains('%') { return false; }
    if request.method() != Method::GET { return false; }
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
        ["plugin", name, "groups"] => plugins.allows(name, None),
        ["plugin", name, group, "utxos" | "confirmed-txs" | "history" | "unconfirmed-txs"] => {
            plugins.allows(name, Some(group))
        }
        _ => false,
    }
}
fn same_origin(request: &Request) -> bool {
    let Some(origin) = request.headers().get(header::ORIGIN) else { return true; };
    let Some(host) = request.headers().get(header::HOST).and_then(|h| h.to_str().ok()) else {
        return false;
    };
    // A loopback reverse proxy may terminate TLS. Host must still match
    // exactly; no forwarded-origin header or cross-host allowlist is trusted.
    origin.to_str().is_ok_and(|value|
        value == format!("http://{host}") || value == format!("https://{host}"))
}
async fn websocket(state: Admission, request: Request, client: ClientAccess) -> Response {
    let Ok(connection) = client.sockets.clone().try_acquire_owned() else {
        return error("503: WebSocket connection limit");
    };
    let mut continuity = state.gate.continuity.subscribe();
    continuity.borrow_and_update();
    let Some(permit) = state.gate.permit() else { return error("503: Index unavailable"); };
    if !state.gate.current(permit, &state.node) { return error("503: Index generation changed"); }
    let (mut parts, _) = request.into_parts();
    let Some(peer) = parts.extensions.get::<ConnectInfo<SocketAddr>>().map(|p| p.0) else {
        return error("503: Missing WebSocket peer binding");
    };
    if !peer.ip().is_loopback() { return error("403: Loopback clients only"); }
    let upgrade = match WebSocketUpgrade::from_request_parts(&mut parts, &()).await {
        Ok(upgrade) => upgrade,
        Err(rejection) => return rejection.into_response(),
    };
    let mut response = upgrade
        .max_message_size(chronik_http::ws::MAX_WS_MESSAGE_SIZE)
        .max_frame_size(chronik_http::ws::MAX_WS_MESSAGE_SIZE)
        .on_upgrade(move |socket| async move {
            let _connection = connection;
            // Existing upstream protobuf subscriptions, event mapping and
            // limits, not an Ergon replacement WS protocol implementation.
            let lifecycle = async move {
                tokio::select! {
                    biased;
                    _ = state.gate.terminal() => "503: Ergon service stopped",
                    _ = continuity.changed() => "503: Ergon notification continuity lost; refresh HTTP snapshot before resubscribing",
                }
            };
            chronik_http::ws::handle_subscribe_socket_with_lifecycle(
                socket, client.indexer,
                chronik_http::server::ChronikSettings {
                    ws_ping_interval: Duration::from_secs(60), enable_cors: false,
                }, client.limiter, peer.ip(), lifecycle).await;
        }).into_response();
    response.headers_mut().insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    response
}
fn valid_submission(path: &str, bytes: &[u8]) -> bool {
    let permitted = |skip: bool, seconds: u64| !skip && seconds == 0;
    match path {
        "/broadcast-tx" => chronik_proto::proto::BroadcastTxRequest::decode(bytes)
            .is_ok_and(|r| permitted(r.skip_token_checks, r.finalization_timeout_secs)
                && !r.raw_tx.is_empty() && r.raw_tx.len() <= 1_000_000),
        "/broadcast-txs" => chronik_proto::proto::BroadcastTxsRequest::decode(bytes)
            .is_ok_and(|r| permitted(r.skip_token_checks, r.finalization_timeout_secs)
                && !r.raw_txs.is_empty() && r.raw_txs.len() <= 16
                && r.raw_txs.iter().all(|tx| !tx.is_empty() && tx.len() <= 1_000_000)),
        _ => false,
    }
}
async fn admit(State(state): State<Admission>, mut request: Request, next: Next) -> Response {
    let is_ws = request.method() == Method::GET && request.uri().path() == "/ws";
    let mutation = request.method() == Method::POST
        && matches!(request.uri().path(), "/broadcast-tx" | "/broadcast-txs");
    if !(supported(&request, &state.plugins)
        || (state.clients.is_some() && (is_ws || mutation))) {
        return error("501: Capability not enabled for this Ergon observer profile");
    }
    if !state.plugins.allows_query(request.uri().path(), request.uri().query())
        || ((is_ws || mutation) && request.uri().query().is_some()) {
        return error("400: Query parameter not enabled for this Ergon observer profile");
    }
    if (is_ws || mutation) && !same_origin(&request) {
        return error("403: Cross-origin client capability refused");
    }
    let _submission = if mutation {
        let client = state.clients.as_ref().unwrap();
        let Ok(permit) = client.submissions.clone().try_acquire_owned() else {
            return error("503: Native submission concurrency limit");
        };
        if request.headers().get(header::CONTENT_TYPE)
            .and_then(|v| v.to_str().ok()) != Some("application/x-protobuf") {
            return error("415: Protobuf submission required");
        }
        let (parts, body) = request.into_parts();
        let cap = if parts.uri.path() == "/broadcast-tx" { 1_000_064 } else { 4_000_256 };
        let bytes = match tokio::time::timeout(Duration::from_secs(5), to_bytes(body, cap)).await {
            Ok(Ok(bytes)) => bytes,
            Ok(Err(_)) => return error("413: Native submission body limit"),
            Err(_) => return error("408: Native submission body deadline"),
        };
        if !valid_submission(parts.uri.path(), &bytes) {
            return error("400: Unsupported submission or finalization options");
        }
        request = Request::from_parts(parts, Body::from(bytes));
        Some(permit)
    } else { None };
    let Some(permit) = state.gate.permit() else {
        return error("503: Ergon index view unavailable");
    };
    if !state.gate.current(permit, &state.node) {
        return error("503: Ergon index view unavailable");
    }
    state.gate.dispatched.fetch_add(1, Ordering::SeqCst);
    if is_ws {
        return websocket(state.clone(), request, state.clients.clone().unwrap()).await;
    }
    let receipt_path = request.uri().path().to_owned();
    let response = next.run(request).await;
    let (mut parts, body) = response.into_parts();
    let Ok(bytes) = to_bytes(body, 8 * 1024 * 1024).await else {
        return error("503: Ergon response unavailable");
    };
    if mutation {
        // Do not turn native acceptance into a rejection. Only a source-fenced
        // successful query of EVERY accepted tx can produce a ready200 receipt.
        if parts.status == axum::http::StatusCode::OK {
            let ids = crate::ergon_submission::receipt_ids(&receipt_path, &bytes);
            let client = state.clients.as_ref().unwrap();
            let ready = if let Some(ids) = ids {
                crate::ergon_submission::wait_ready(|| async {
                    let Some(view) = state.gate.permit() else { return false; };
                    if !state.gate.current(view, &state.node) { return false; }
                    let indexer = client.indexer.read().await;
                    let queries = indexer.txs(&state.node);
                    let present = ids.iter().all(|id| queries.tx_by_id(
                        bitcoinsuite_core::tx::TxId::from(*id)).is_ok());
                    present && state.gate.current(view, &state.node)
                }, state.gate.terminal(), Duration::from_secs(2)).await
            } else { false };
            parts.headers.insert("x-ergon-index-state", if ready { "ready" } else { "pending" }.parse().unwrap());
            if !ready {
                parts.status = axum::http::StatusCode::ACCEPTED;
                parts.headers.insert(header::RETRY_AFTER, "1".parse().unwrap());
            }
        }
        parts.headers.insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
        return Response::from_parts(parts, Body::from(bytes));
    }
    state.latch.pause().await;
    if !state.gate.current(permit, &state.node) {
        return error("503: Ergon index generation changed during request");
    }
    parts.headers.insert(header::CACHE_CONTROL, "no-store".parse().unwrap());
    Response::from_parts(parts, Body::from(bytes))
}

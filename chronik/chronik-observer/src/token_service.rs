// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

//! Narrow loopback HTTP surface for confirmed token and transaction data.

use std::{
    net::{IpAddr, SocketAddr, TcpListener},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread::{self, JoinHandle},
};

use axum::{
    body::Body,
    extract::{Path, State},
    http::{header::CONTENT_TYPE, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    routing::get,
    Router,
};
use bitcoinsuite_core::tx::TxId;
use bytes::Bytes;
use chronik_proto::proto;
use chronik_runtime::PersistentTokenIndexer;
use prost::Message;
use tokio::sync::oneshot;

pub const CONTENT_TYPE_PROTOBUF: &str = "application/x-protobuf";

#[derive(Clone)]
struct TokenServiceState {
    indexer: Arc<PersistentTokenIndexer>,
    tx_query: Option<HostTxQuery>,
    token_query: Option<HostTxQuery>,
    tx_busy: Arc<AtomicBool>,
}

/// One owned C++ response. Payloads must be released exactly once, even when
/// rejected. No host pointer or diagnostic text is exposed to HTTP clients.
#[repr(C)]
pub struct HostTxResponse {
    pub status: u64,
    pub payload: *mut u8,
    pub payload_len: usize,
}
const _: () = assert!(std::mem::size_of::<HostTxResponse>() == 24);

pub type HostTxQueryFn = extern "C" fn(usize, *const u8) -> HostTxResponse;
pub type HostTxReleaseFn = extern "C" fn(usize, *mut u8);

/// The host retains context until service stop has joined all request work.
#[derive(Clone, Copy)]
pub struct HostTxQuery {
    pub query: HostTxQueryFn,
    pub release: HostTxReleaseFn,
    pub context: usize,
}

const MAX_TX_RESPONSE_BYTES: usize = 64 * 1024 * 1024;

struct HostPayload {
    response: HostTxResponse,
    release: HostTxReleaseFn,
    context: usize,
}

impl Drop for HostPayload {
    fn drop(&mut self) {
        if !self.response.payload.is_null() {
            (self.release)(self.context, self.response.payload);
        }
    }
}

struct TxPermit(Arc<AtomicBool>);
impl Drop for TxPermit {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

struct LeasedTxBytes {
    bytes: Vec<u8>,
    _permit: TxPermit,
}

impl AsRef<[u8]> for LeasedTxBytes {
    fn as_ref(&self) -> &[u8] {
        &self.bytes
    }
}

pub struct TokenService {
    address: SocketAddr,
    shutdown: Option<oneshot::Sender<()>>,
    thread: Option<JoinHandle<()>>,
}

impl TokenService {
    pub fn start_with_queries(
        indexer: Arc<PersistentTokenIndexer>,
        address: SocketAddr,
        tx_query: Option<HostTxQuery>,
        token_query: Option<HostTxQuery>,
    ) -> Result<Self, TokenServiceError> {
        if !is_loopback(address.ip()) {
            return Err(TokenServiceError::NonLoopbackAddress);
        }
        let listener = TcpListener::bind(address).map_err(|_| TokenServiceError::BindFailed)?;
        listener
            .set_nonblocking(true)
            .map_err(|_| TokenServiceError::BindFailed)?;
        let address = listener
            .local_addr()
            .map_err(|_| TokenServiceError::BindFailed)?;
        let runtime = tokio::runtime::Builder::new_multi_thread()
            .worker_threads(1)
            .enable_io()
            .build()
            .map_err(|_| TokenServiceError::ThreadFailed)?;
        let listener = {
            let _runtime_guard = runtime.enter();
            tokio::net::TcpListener::from_std(listener)
                .map_err(|_| TokenServiceError::BindFailed)?
        };
        let (shutdown_tx, shutdown_rx) = oneshot::channel();
        let thread = thread::Builder::new()
            .name("chronik-token-http".to_string())
            .spawn(move || {
                runtime.block_on(async move {
                    let router = token_router(indexer, tx_query, token_query);
                    let _ = axum::serve(listener, router)
                        .with_graceful_shutdown(async move {
                            let _ = shutdown_rx.await;
                        })
                        .await;
                });
            })
            .map_err(|_| TokenServiceError::ThreadFailed)?;
        Ok(Self {
            address,
            shutdown: Some(shutdown_tx),
            thread: Some(thread),
        })
    }

    pub fn port(&self) -> u16 {
        self.address.port()
    }

    pub fn stop(mut self) -> bool {
        if let Some(shutdown) = self.shutdown.take() {
            let _ = shutdown.send(());
        }
        self.thread
            .take()
            .is_some_and(|thread| thread.join().is_ok())
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TokenServiceError {
    NonLoopbackAddress,
    BindFailed,
    ThreadFailed,
}

fn is_loopback(ip: IpAddr) -> bool {
    ip.is_loopback()
}

fn token_router(
    indexer: Arc<PersistentTokenIndexer>,
    tx_query: Option<HostTxQuery>,
    token_query: Option<HostTxQuery>,
) -> Router {
    let router = Router::new().route("/token/:txid", get(handle_token_info));
    // The token-only ABI does not silently acquire a new route.
    let router = if tx_query.is_some() {
        router.route("/tx/:txid", get(handle_confirmed_tx))
    } else {
        router
    };
    router.with_state(TokenServiceState {
        indexer,
        tx_query,
        token_query,
        tx_busy: Arc::new(AtomicBool::new(false)),
    })
}

async fn handle_confirmed_tx(
    Path(txid): Path<String>,
    State(state): State<TokenServiceState>,
) -> Response {
    let txid = match txid.parse::<TxId>() {
        Ok(txid) => txid,
        Err(_) => {
            return protobuf_error(StatusCode::BAD_REQUEST, format!("400: Not a txid: {txid}"))
        }
    };
    let Some(query) = state.tx_query else {
        return tx_unavailable();
    };
    // One host disk read at a time, no unbounded blocking-pool queue. A busy
    // observer/request is retriable; it must not wait on a consensus callback.
    if state
        .tx_busy
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_err()
    {
        return tx_unavailable();
    }
    let permit = TxPermit(Arc::clone(&state.tx_busy));
    let result =
        tokio::task::spawn_blocking(move || read_host_tx(query, txid).map(|bytes| (bytes, permit)))
            .await;
    match result {
        Ok(Ok((bytes, permit))) => {
            // Retain admission until Hyper releases the body, including slow
            // readers and dropped clients. Never queue a second 64 MiB result.
            let bytes = Bytes::from_owner(LeasedTxBytes {
                bytes,
                _permit: permit,
            });
            protobuf_body(StatusCode::OK, Body::from(bytes))
        }
        Ok(Err(StatusCode::NOT_FOUND)) => protobuf_error(
            StatusCode::NOT_FOUND,
            format!("404: Transaction {txid} not found in the confirmed index"),
        ),
        _ => tx_unavailable(),
    }
}

fn read_host_tx(query: HostTxQuery, txid: TxId) -> Result<Vec<u8>, StatusCode> {
    read_host_response(query, txid, false)
}

fn read_host_token(query: HostTxQuery, token_id: TxId) -> Result<Vec<u8>, StatusCode> {
    read_host_response(query, token_id, true)
}

fn read_host_response(query: HostTxQuery, txid: TxId, token: bool) -> Result<Vec<u8>, StatusCode> {
    let payload = HostPayload {
        response: (query.query)(query.context, txid.as_bytes().as_ptr()),
        release: query.release,
        context: query.context,
    };
    let response = &payload.response;
    if response.status != 200 {
        return Err(if response.status == 404 {
            StatusCode::NOT_FOUND
        } else {
            StatusCode::SERVICE_UNAVAILABLE
        });
    }
    if response.payload.is_null()
        || response.payload_len == 0
        || response.payload_len > MAX_TX_RESPONSE_BYTES
    {
        return Err(StatusCode::SERVICE_UNAVAILABLE);
    }
    // SAFETY: host provides a live immutable allocation until release.
    let bytes = unsafe { std::slice::from_raw_parts(response.payload, response.payload_len) };
    if token {
        let proto = proto::TokenInfo::decode(bytes).map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
        if proto.token_id != txid.to_string() || proto.block.is_none() {
            return Err(StatusCode::SERVICE_UNAVAILABLE);
        }
    } else {
        let proto = proto::Tx::decode(bytes).map_err(|_| StatusCode::SERVICE_UNAVAILABLE)?;
        if proto.txid != txid.to_vec() || proto.block.is_none() {
            return Err(StatusCode::SERVICE_UNAVAILABLE);
        }
    }
    Ok(bytes.to_vec())
}

fn tx_unavailable() -> Response {
    protobuf_error(
        StatusCode::SERVICE_UNAVAILABLE,
        "503: Confirmed transaction data unavailable".to_string(),
    )
}

async fn handle_token_info(
    Path(txid): Path<String>,
    State(state): State<TokenServiceState>,
) -> Response {
    let token_id = match txid.parse::<TxId>() {
        Ok(token_id) => token_id,
        Err(_) => {
            return protobuf_error(StatusCode::BAD_REQUEST, format!("400: Not a txid: {txid}"));
        }
    };
    if let Some(query) = state.token_query {
        // Share the transaction admission budget, including response-body
        // lifetime. Both routes use the host's coherent accepted-tip gate.
        if state
            .tx_busy
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return token_unavailable();
        }
        let permit = TxPermit(Arc::clone(&state.tx_busy));
        let result = tokio::task::spawn_blocking(move || {
            read_host_token(query, token_id).map(|bytes| (bytes, permit))
        })
        .await;
        return match result {
            Ok(Ok((bytes, permit))) => protobuf_body(
                StatusCode::OK,
                Body::from(Bytes::from_owner(LeasedTxBytes {
                    bytes,
                    _permit: permit,
                })),
            ),
            Ok(Err(StatusCode::NOT_FOUND)) => protobuf_error(
                StatusCode::NOT_FOUND,
                format!("404: Token {token_id} not found in the index"),
            ),
            _ => token_unavailable(),
        };
    }
    // Preserve the original standalone token-service ABI. The node never uses
    // this unguarded compatibility path; it selects the guarded-query ABI.
    match state.indexer.token_info(&token_id) {
        Ok(Some(token_info)) => protobuf(StatusCode::OK, token_info),
        Ok(None) => protobuf_error(
            StatusCode::NOT_FOUND,
            format!("404: Token {token_id} not found in the index"),
        ),
        Err(_) => protobuf_error(
            StatusCode::INTERNAL_SERVER_ERROR,
            "Unknown error, contact admins".to_string(),
        ),
    }
}

fn token_unavailable() -> Response {
    protobuf_error(
        StatusCode::SERVICE_UNAVAILABLE,
        "503: Confirmed token data unavailable".to_string(),
    )
}

fn protobuf<P: Message>(status: StatusCode, payload: P) -> Response {
    protobuf_bytes(status, payload.encode_to_vec())
}

fn protobuf_bytes(status: StatusCode, payload: Vec<u8>) -> Response {
    protobuf_body(status, Body::from(payload))
}

fn protobuf_body(status: StatusCode, payload: Body) -> Response {
    let mut response = Response::builder()
        .status(status)
        .body(payload)
        .expect("static Chronik response is valid");
    response.headers_mut().insert(
        CONTENT_TYPE,
        HeaderValue::from_static(CONTENT_TYPE_PROTOBUF),
    );
    response.into_response()
}

fn protobuf_error(status: StatusCode, msg: String) -> Response {
    protobuf(status, proto::Error { msg })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{atomic::AtomicUsize, Mutex};

    struct HostFixture {
        payload: Mutex<Option<Vec<u8>>>,
        status: u64,
        declared_len: Option<usize>,
        releases: AtomicUsize,
    }

    extern "C" fn fixture_query(context: usize, _txid: *const u8) -> HostTxResponse {
        // SAFETY: tests keep this exact fixture alive until read_host_tx returns.
        let fixture = unsafe { &*(context as *const HostFixture) };
        let mut payload = fixture.payload.lock().unwrap();
        let (ptr, len) = payload.as_mut().map_or((std::ptr::null_mut(), 0), |bytes| {
            (bytes.as_mut_ptr(), bytes.len())
        });
        HostTxResponse {
            status: fixture.status,
            payload: ptr,
            payload_len: fixture.declared_len.unwrap_or(len),
        }
    }

    extern "C" fn fixture_release(context: usize, payload: *mut u8) {
        let fixture = unsafe { &*(context as *const HostFixture) };
        let owned = fixture.payload.lock().unwrap().take().unwrap();
        assert_eq!(owned.as_ptr(), payload);
        fixture.releases.fetch_add(1, Ordering::SeqCst);
    }

    fn run_fixture(
        status: u64,
        bytes: Option<Vec<u8>>,
        declared_len: Option<usize>,
    ) -> (Result<Vec<u8>, StatusCode>, usize) {
        run_fixture_for(status, bytes, declared_len, false)
    }

    fn run_fixture_for(
        status: u64,
        bytes: Option<Vec<u8>>,
        declared_len: Option<usize>,
        token: bool,
    ) -> (Result<Vec<u8>, StatusCode>, usize) {
        let fixture = HostFixture {
            payload: Mutex::new(bytes),
            status,
            declared_len,
            releases: AtomicUsize::new(0),
        };
        let query = HostTxQuery {
            query: fixture_query,
            release: fixture_release,
            context: (&fixture as *const HostFixture) as usize,
        };
        let result = if token {
            read_host_token(query, TxId::from([7; 32]))
        } else {
            read_host_tx(query, TxId::from([7; 32]))
        };
        (result, fixture.releases.load(Ordering::SeqCst))
    }

    #[test]
    fn token_payloads_require_identity_confirmation_and_exact_release() {
        let token = proto::TokenInfo {
            token_id: TxId::from([7; 32]).to_string(),
            block: Some(proto::BlockMetadata::default()),
            ..Default::default()
        };
        let bytes = token.encode_to_vec();
        assert_eq!(
            run_fixture_for(200, Some(bytes.clone()), None, true),
            (Ok(bytes), 1)
        );
        for status in [404, 503, 500, 999] {
            assert_eq!(
                run_fixture_for(status, Some(vec![1]), None, true),
                (
                    Err(if status == 404 {
                        StatusCode::NOT_FOUND
                    } else {
                        StatusCode::SERVICE_UNAVAILABLE
                    }),
                    1
                )
            );
        }
        for declared_len in [0, MAX_TX_RESPONSE_BYTES + 1] {
            assert_eq!(
                run_fixture_for(200, Some(vec![1]), Some(declared_len), true),
                (Err(StatusCode::SERVICE_UNAVAILABLE), 1)
            );
        }
        assert_eq!(
            run_fixture_for(200, None, Some(1), true),
            (Err(StatusCode::SERVICE_UNAVAILABLE), 0)
        );
        assert_eq!(
            run_fixture_for(200, Some(vec![0xff]), None, true),
            (Err(StatusCode::SERVICE_UNAVAILABLE), 1)
        );
        for token in [
            proto::TokenInfo {
                token_id: token.token_id.clone(),
                ..Default::default()
            },
            proto::TokenInfo {
                token_id: TxId::from([8; 32]).to_string(),
                ..token
            },
        ] {
            assert_eq!(
                run_fixture_for(200, Some(token.encode_to_vec()), None, true),
                (Err(StatusCode::SERVICE_UNAVAILABLE), 1)
            );
        }
    }

    #[test]
    fn releases_success_and_every_rejected_owned_payload() {
        let tx = proto::Tx {
            txid: vec![7; 32],
            block: Some(proto::BlockMetadata::default()),
            ..Default::default()
        };
        let bytes = tx.encode_to_vec();
        assert_eq!(run_fixture(200, Some(bytes.clone()), None), (Ok(bytes), 1));
        for status in [404, 503, 500, 999] {
            let (result, releases) = run_fixture(status, Some(vec![1]), None);
            assert_eq!(
                result,
                Err(if status == 404 {
                    StatusCode::NOT_FOUND
                } else {
                    StatusCode::SERVICE_UNAVAILABLE
                })
            );
            assert_eq!(releases, 1);
        }
        for declared_len in [0, MAX_TX_RESPONSE_BYTES + 1] {
            assert_eq!(
                run_fixture(200, Some(vec![1]), Some(declared_len)),
                (Err(StatusCode::SERVICE_UNAVAILABLE), 1)
            );
        }
        assert_eq!(
            run_fixture(200, None, Some(1)),
            (Err(StatusCode::SERVICE_UNAVAILABLE), 0)
        );
        assert_eq!(
            run_fixture(200, Some(vec![0xff]), None),
            (Err(StatusCode::SERVICE_UNAVAILABLE), 1)
        );
        for tx in [
            proto::Tx::default(),
            proto::Tx {
                txid: vec![8; 32],
                block: Some(proto::BlockMetadata::default()),
                ..Default::default()
            },
        ] {
            assert_eq!(
                run_fixture(200, Some(tx.encode_to_vec()), None),
                (Err(StatusCode::SERVICE_UNAVAILABLE), 1)
            );
        }
    }

    #[test]
    fn permit_survives_until_blocking_work_finishes() {
        let busy = Arc::new(AtomicBool::new(true));
        let permit = TxPermit(Arc::clone(&busy));
        assert!(busy.load(Ordering::Acquire));
        drop(permit);
        assert!(!busy.load(Ordering::Acquire));
    }

    #[test]
    fn response_clones_retain_admission_until_last_body_drop() {
        let busy = Arc::new(AtomicBool::new(true));
        let bytes = Bytes::from_owner(LeasedTxBytes {
            bytes: vec![1, 2, 3],
            _permit: TxPermit(Arc::clone(&busy)),
        });
        let clone = bytes.clone();
        drop(bytes);
        assert!(busy.load(Ordering::Acquire));
        assert_eq!(clone.as_ref(), &[1, 2, 3]);
        drop(clone);
        assert!(!busy.load(Ordering::Acquire));
    }
}

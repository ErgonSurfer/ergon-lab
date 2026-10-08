// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

//! Narrow Chronik-compatible HTTP surface for confirmed token metadata.

use std::{
    net::{IpAddr, SocketAddr, TcpListener},
    sync::Arc,
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
use chronik_proto::proto;
use chronik_runtime::PersistentTokenIndexer;
use prost::Message;
use tokio::sync::oneshot;

pub const CONTENT_TYPE_PROTOBUF: &str = "application/x-protobuf";

#[derive(Clone)]
struct TokenServiceState {
    indexer: Arc<PersistentTokenIndexer>,
}

pub struct TokenService {
    address: SocketAddr,
    shutdown: Option<oneshot::Sender<()>>,
    thread: Option<JoinHandle<()>>,
}

impl TokenService {
    pub fn start(
        indexer: Arc<PersistentTokenIndexer>,
        address: SocketAddr,
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
                    let router = token_router(indexer);
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

fn token_router(indexer: Arc<PersistentTokenIndexer>) -> Router {
    Router::new()
        .route("/token/:txid", get(handle_token_info))
        .with_state(TokenServiceState { indexer })
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

fn protobuf<P: Message>(status: StatusCode, payload: P) -> Response {
    let mut response = Response::builder()
        .status(status)
        .body(Body::from(payload.encode_to_vec()))
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

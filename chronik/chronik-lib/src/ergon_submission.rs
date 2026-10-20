// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
//! Acceptance receipts and bounded indexed-readiness waiting are distinct.
use prost::Message;
use std::{future::Future, time::Duration};

pub(crate) fn receipt_ids(path: &str, body: &[u8]) -> Option<Vec<[u8; 32]>> {
    let ids = match path {
        "/broadcast-tx" => vec![
            chronik_proto::proto::BroadcastTxResponse::decode(body)
                .ok()?
                .txid,
        ],
        "/broadcast-txs" => {
            chronik_proto::proto::BroadcastTxsResponse::decode(body)
                .ok()?
                .txids
        }
        _ => return None,
    };
    if ids.is_empty() || ids.len() > 16 {
        return None;
    }
    ids.into_iter().map(|id| id.try_into().ok()).collect()
}

pub(crate) async fn wait_ready<F, Check, Stop>(mut check: F, stop: Stop, budget: Duration) -> bool
where
    F: FnMut() -> Check,
    Check: Future<Output = bool>,
    Stop: Future<Output = ()>,
{
    // This is an asynchronous waiting budget, not a cancellation guarantee for
    // synchronous storage I/O inside check. Runtime shutdown still joins it.
    tokio::select! {
        biased;
        _ = stop => false,
        result = tokio::time::timeout(budget, async {
            loop {
                if check().await { return true; }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        }) => result.unwrap_or(false),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };
    #[test]
    fn single_receipt_binds_exact_identity() {
        let body = chronik_proto::proto::BroadcastTxResponse { txid: vec![7; 32] }.encode_to_vec();
        assert_eq!(receipt_ids("/broadcast-tx", &body), Some(vec![[7; 32]]));
    }
    #[test]
    fn malformed_or_missing_identity_is_pending_not_ready() {
        for id in [vec![], vec![1; 31], vec![1; 33]] {
            let body = chronik_proto::proto::BroadcastTxResponse { txid: id }.encode_to_vec();
            assert_eq!(receipt_ids("/broadcast-tx", &body), None);
        }
        assert_eq!(receipt_ids("/broadcast-tx", &[255]), None);
        assert_eq!(receipt_ids("/unknown", &[]), None);
    }
    #[test]
    fn batch_is_nonempty_bounded_and_ordered() {
        for count in [0, 17] {
            let body = chronik_proto::proto::BroadcastTxsResponse {
                txids: vec![vec![1; 32]; count],
            }
            .encode_to_vec();
            assert_eq!(receipt_ids("/broadcast-txs", &body), None);
        }
        let body = chronik_proto::proto::BroadcastTxsResponse {
            txids: vec![vec![1; 32], vec![2; 32]],
        }
        .encode_to_vec();
        assert_eq!(
            receipt_ids("/broadcast-txs", &body),
            Some(vec![[1; 32], [2; 32]])
        );
    }
    #[tokio::test]
    async fn ready_only_after_check_succeeds() {
        let calls = Arc::new(AtomicUsize::new(0));
        let seen = calls.clone();
        assert!(
            wait_ready(
                move || {
                    let yes = seen.fetch_add(1, Ordering::SeqCst) >= 1;
                    async move { yes }
                },
                std::future::pending(),
                Duration::from_secs(1)
            )
            .await
        );
        assert!(calls.load(Ordering::SeqCst) >= 2);
    }
    #[tokio::test]
    async fn waiting_deadline_is_pending_not_rejection() {
        assert!(
            !wait_ready(
                || async { false },
                std::future::pending(),
                Duration::from_millis(20)
            )
            .await
        );
    }
    #[tokio::test]
    async fn terminal_owner_never_claims_ready() {
        assert!(!wait_ready(|| async { true }, async {}, Duration::from_secs(1)).await);
    }
}

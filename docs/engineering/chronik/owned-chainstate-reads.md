<!-- SPDX-License-Identifier: MIT -->
# Chainstate reads without holding validation locks

This review slice moves cold native UTXO reads out of both `cs_main` and the
mempool lock. It builds on the public PR #51 checkpoint, not an assumed merge.
Chronik remains disabled by default and daemon admission remains closed.

## One coherent read, two phases

Under the native source fence, the adapter owns a LevelDB snapshot, copies the
required cache entries (including spent tombstones), copies mempool parent
outputs, and records chain/pool generations and cache/database owner identities.
It does not call `FetchCoin`, perform a database `Get`, or borrow a coin view.

After releasing both locks, cold keys use the native coin key, serialization and
obfuscation codecs through that owned snapshot. Parent-before-child conversion,
upstream indexing, and block/undo reads remain detached. Before applying and
before publishing, the adapter rechecks the native source and both owners.
Address reuse cannot substitute a new cache or database for the captured owner.

A cache flush cannot resurrect a spent coin: the copied tombstone wins over an
older database value. A missing or malformed stable input quarantines only the
index. A raced capture is discarded and retried within the existing four-attempt
bound; it cannot publish partial readiness or synthesize prevout values.

## Lifetime and shutdown

The read session owns LevelDB, its snapshot, XOR key, cache, logger and optional
memory environment. Snapshot release precedes final storage release. The worker
still closes request admission, drains callbacks and positively joins its native
readers and HTTP/Tokio tasks; there is no detached reader or borrowed cache.

An owned session can retain a real database file lock. Reindex/storage reopen
must therefore follow positive reader shutdown, not race an outstanding lease.
This is not a new daemon startup/reindex hook or permission to activate one.

## Tests and limits

New native fixtures cover reads after wrapper destruction, immutable snapshots
across writes/erases, native decoder rejection, dirty/spent overlays across
flush, owner replacement, chain ABA, cold-read/native-validation progress, joined
shutdown and index-only failure. They retain the earlier 39 native cases.
Synthetic standard-library gates reject source, selection and binding drift.

These are bounded regtest component tests, not live service, historical-chain,
mainnet, reproducible-build or independent-review evidence. Public record state
is `Open Question` / `record-review-only`; no incoming reports are attached.
No token algorithm, consensus, dependency, plugin, wallet or activation changes
are part of this slice. The original upstream/donor manifests remain unchanged.

Before landing, the separate PR must follow acceptance of its parent and be
rebound/retested if the accepted parent differs from the pinned review checkpoint.

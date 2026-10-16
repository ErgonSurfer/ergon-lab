<!-- SPDX-License-Identifier: MIT -->
# Native ancestor references for upstream resynchronization

The continuous regtest service now starts upstream Chronik using a captured
native tip and its immutable header ancestors. It no longer copies every
entry in the native block index, allocates another ancestor graph or makes a
whole-map storage comparison at the end of service startup. The additional
capture state is constant-size: one tip pointer and two source generations.
This is an Ergon host adaptation, not another Chronik indexer or token engine.

The strict historical fixtures still use the owned graph, its batched metadata
capture and its original atomic source/storage finish fence. Their existing
test bodies and assertion counts are unchanged. Only service opens choose the
new factory; compile/runtime defaults and all regtest budgets stay unchanged.

## Why upstream does not need another graph

The pinned upstream `ChronikIndexer::resync_indexer` in
`chronik/chronik-indexer/src/indexer.rs` obtains one tip reference and walks its
ancestors through the existing CXX ABI. When its database is on another branch,
it looks up the indexed hashes and rewinds to a common ancestor. It does not
require a private copy of the host's complete block-index map.

The service therefore keeps its captured tip even if the native active chain
advances or reorganizes. Height lookup follows that captured tip's ancestors;
fork lookup compares against that tip, not the moving active chain. After
upstream resync, the existing 288-block retained suffix is seeded, startup
finishes, and ordinary live reconciliation catches up before request admission.
No partially synchronized view is made available to HTTP clients.

## Native contract and limits

The following public source inputs are pinned by the source gate. These are
source/lifetime observations, not a formal whole-program proof.

| Surface | Reviewed rule |
| --- | --- |
| `CChainState::AddToBlockIndex` | Existing hashes return their existing index. New indexes receive hash, header, height, parent and skip link under `cs_main` before publication. |
| `CChainState::LoadBlockIndex` / `CBlockTreeDB::LoadBlockIndexGuts` | Header/ancestor fields are restored before the service starts, not rewritten while it runs. |
| `CBlockIndex::GetAncestor` | Traverses only height, parent and skip fields. It does not read mutable validity or disk-position metadata. |
| Native hash map | Rehash invalidates iterators, not the heap indexes or references to stored keys. No map iterator is retained. |
| `AcceptBlock` / `ReceivedBlockTransactions` | Already stored data is not received again. In the unpruned runtime, an existing available block's data position is not relocated. |
| `WriteUndoDataForBlock` | Writes an undo position only when undo is absent. Active-chain ancestors already have their required undo, except genesis. |
| `PruneOneBlockFile` | Pruning is excluded. Disk relocation, live reload, header replacement and live index erasure are not supported operations. |
| `Shutdown` / service owner | Service startup, workers and HTTP users are joined before scheduler/native resources are destroyed. `UnloadBlockIndex` belongs to initialization, before service startup. |

Only hash, header, height, parent and skip fields may be read through an escaped
index reference. Mutable block/undo positions and availability are captured
under `cs_main`, disk reads happen after release, and positions/hash are checked
again under `cs_main`. Upstream Rust never reads these C++ fields directly.
The native test-only read hook exercises an actual position change between
disk read and recheck on another joined thread. Failure remains index-only.

There is deliberately no copied-position identity for every historical index
to compare at the final service fence. The unpruned storage-writer contract is
required instead: storage relocation or live reload would need a new design,
not a relaxed gate. The old owned-fixture storage fence remains intact as an
independent negative control. A late unsupported mutation after an earlier
read is not claimed to be detected by an all-history final scan.

The native owner must remain alive until every CXX borrower and service task is
joined. A pointer or `const` qualification is not an ownership mechanism. The
existing owner/shutdown ordering is part of admission, not an optional caller
convention to bypass. No live unload or reload test may destroy borrowed native
indexes to manufacture a use-after-free.

## Verification and next boundary

The new disjoint `native-startup` group covers six cases and 74 assertions:
no owned graph, rehash stability, native progress and reorg on a joined thread,
captured-branch fork lookup, mid-read storage drift, cancellation, pruning and
owner refusal, foreign-index refusal, and actual upstream offline rewind/reopen
compared with an owned-graph control. All inherited groups and 60-second process
deadlines remain. Existing daemon startup/reconstruction, plugin and ALP clients
exercise the actual service factory, not just the direct bridge fixture.

This removes a host-side O(index-count) copy, not all full-history limits.
Runtime remains local unpruned regtest with height 2000 and 4096 index/mempool
entries. Full-history resource budgets, stable startup under continuous ingress,
operator plugins, reproducible build provenance and mainnet shadow comparison
remain separate work before server replacement. Historical timeouts and upstream
dependency alerts remain open. Tests and CI do not automatically promote the
engineering record to independently reproduced evidence.

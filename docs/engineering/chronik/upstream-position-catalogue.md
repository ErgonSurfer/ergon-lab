<!-- SPDX-License-Identifier: MIT -->
# Historical position metadata without a full-chain native lock

The optional local-regtest service still uses the existing upstream Chronik
indexer, ALP engine and plugin engine. This change concerns only the Ergon
adapter that locates an already accepted transaction in native block storage.
It adds no consensus rule, network admission, dependency or database schema.
Compile-time and runtime defaults remain OFF.

## What changes

The first historical query formerly populated its entire position catalogue
while holding `cs_main`. The refresher now takes exclusive ownership of the
catalogue, captures at most 64 block positions per native-lock hold, and builds
the directory and vector after releasing that lock. Suffix removals, layout
retirement and abandoned staging are also processed outside native locks.

A native health, tip and nonwrapping generation check precedes every capture
batch and final publication. An incomplete or stale catalogue is discarded;
it is never exposed as a completed view and never poisons native validation.
Finding the shared ancestry uses at most 31 binary ancestry probes rather than
a linear disconnected-suffix walk. This bounds the number of ancestry probes,
not their wall-clock duration.

Overlapping cold or suffix refreshes serialize only after releasing native
and metadata locks. Once admitted, a waiting reader rechecks whether the first
refresh already completed the required catalogue. Warm reads retain their
existing fast path. There is no automatic retry loop. The caller must not
invoke a refresh while holding native chain or mempool locks.

Generation equality is not a storage fence: mutable native file positions are
still checked for the selected block. Native envelope, header, Merkle,
transaction, undo checksum and final source checks are byte-for-byte unchanged.
The eight-layout cache, transaction/payload budgets and raw-read behavior are
unchanged. A source change during a disk read still fails closed.

## Exact verification contract

Six new native leaf cases have 12, 8, 7, 7, 5 and 11 assertions, respectively.
They cover cross-batch lock acquisition by another thread, incremental append,
native ABA, final cancellation and native-progress fences, mutable storage
positions without a generation change, queued readers and concurrent warm
reads. All eight inherited bridge/disk case bodies and their 166 assertions
remain unchanged.

The new cases are separate fresh processes. All inherited groups keep their
relative order, selected cases, assertion counts, ten CI repetitions and
60-second per-process deadline. Native fixture and public-process output
handling, environment isolation, failure, cleanup and report laws remain
unchanged. Failure diagnostics add only the six public case names to their
existing finite allowlist.

CI continues every existing daemon scenario, including reconstruction of 5000
actual native regtest blocks, historical ALP/public-plugin data at height 102,
same-index process reopen and progression to 5001. That experiment retains its
declared 120-second listener-readiness budget; no inherited budget increases.

## Limits and integration boundary

Initial total work and retained catalogue memory are still O(height). This
change moves container work out of native locks; it does not establish a
production latency or memory guarantee. Rapid continuous ingress may invalidate
a capture, and complete mainnet-history resource admission remains open.

The basis is the published PR #65 head, not an assertion that it is merged or
integration-ready. Its historical local matrix timeouts and integration hold
remain explicit. Passing this change's local tests or CI does not explain or
retire those failures. No incoming report, private profiling data, binary,
operator plugin or host metadata is an evidence input.

Engineering knowledge remains `Open Question` / `record-review-only` until
separately reviewed public observations exist. No mainnet readiness, deployment,
activation, server comparison, release, cutover or desktop integration is
included. Upstream dependency alerts remain open.

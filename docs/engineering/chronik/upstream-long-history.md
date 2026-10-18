<!-- SPDX-License-Identifier: MIT -->
# Archival regtest history without the fixture-height ceiling

The explicitly opted-in regtest service can now reconstruct a history longer
than the old 2000-block / 4096-index fixture ceilings. It still uses the
existing upstream Chronik indexer, ALP engine and plugin engine. No mainnet or
testnet network is admitted, no consensus rule changes, and both compile-time
and runtime defaults remain OFF.

## Separate history from retained live work

`ErgonNodeAccess` has an immutable construction-time native read scope. Its
default is `BoundedFixture`: height 2000 and index count 4096 remain the fixture
limits. Only the existing joined `OwnedService` constructs
`ArchivalRegtestService`. That scope removes the artificial historical
height/index-count ceilings, not native lifetime or network constraints.
Height must remain nonnegative and at most signed-32-bit maximum minus one,
so existing `height + 1` arithmetic cannot overflow.

The service retains the captured native tip and immutable ancestors rather
than another complete index graph. Once upstream has reconstructed the index,
the same live reconciliation retains at most 288 blocks, refuses a reorg below
its anchor and caps captured payload at 64 MiB and the mempool at 4096 entries.
The index database can contain historical records older than that live window.
Pruned storage, live reload/unload and storage relocation remain unsupported.
The node and indexer lifetime/authority boundaries do not change.

Strict owned-graph fixture capture and finish retain their original limits and
atomic storage fence, even if passed an archival-scope access object. Neither
an enum nor a `const` pointer owns native resources: joined startup, worker and
HTTP shutdown must still precede native resource destruction.

## Public long-history experiment

`tests/compatibility/upstream-chronik/long_history_service.py` reuses the
existing cold-reconstruction driver and upstream-generated ALP templates.
It creates 5000 actual node-accepted regtest blocks while Chronik is disabled.
The public zero-value ALP/plugin corpus is in block 102, far older than the
288-block retained suffix. Only then does it open an absent Chronik index,
reconstruct the complete history through upstream, query the historical token,
plugin and raw-transaction data, stop, reopen that same index in another process
and advance the native chain again. Three daemons are used, with zero external
connections. Historical mempool first-seen times remain explicitly unknown;
they are never invented from block times.

The new long-history listener-readiness budget is declared as 120 seconds
before the experiment. It is not an increase to an existing test deadline:
the inherited cold scenario remains 402 blocks with a 30-second default;
inherited native processes remain capped at 60 seconds. Failure creates no
passing report and causes no automatic retry. CI runs this experiment only in
the regtest-plugins profile and continues to run every inherited profile.

Two native scope-law tests preserve the default fixture bounds, the signed
height arithmetic and the closed ordinary runtime authority. All 68 preceding
archival test bodies are unchanged. Source gates bind every public postimage,
the existing mutable-storage/lifetime contract and the unchanged upstream
engines and dependency lock.

## What this does not establish

A 5000-block synthetic history is not Ergon mainnet historical parity or a
production resource guarantee. Native block-index memory is still owned by the
node. The current positioned-transaction catalogue has an O(height) first-query
bootstrap under `cs_main`; this remains a full-history query/resource question,
not something removed by lifting the fixture ceiling. Full mainnet replay,
continuous-ingress behavior, operator plugin review, binary provenance and a
parallel server comparison are still required before replacing an operator
node. No server access, deployment, activation or cutover is included.

Historical timeouts and upstream dependency alerts remain open. Local and CI
observations do not automatically promote engineering evidence or imply a
reproducible build.

The local normal regtest-plugins matrix for this lot timed out in the inherited
`capture-and-query-controls` group at repetition 2 under its unchanged
60-second deadline. No passing complete report exists for that run. A passing
long-history scenario or a later diagnostic cannot replace that refusal; the
lot remains blocked from publication until the failure is understood and the
normal gate is resolved without weakening its contract.

A draft pull request may run the declared Ubuntu CI profile for independent
review. Under `PUBLICATION_POLICY.md`, that review step is distinct from
protected-main publication. A draft is not integration-ready, does not retire
the local refusal and does not authorize a release or proof promotion.

Timeouts now expose only the last observed start/done names from the finite
public native-case allowlist, after the original child-group stop has run.
The diagnostic refuses symlinks and non-regular files, caps input at 2 MiB,
requires strict UTF-8 and never echoes child text, paths or exception messages.
It rethrows the original timeout and creates no passing report. Successful
process handling, output equality, assertion parsing, cleanup and the
60-second process deadline are unchanged; an exact parent-relative source
comparison and synthetic timeout/privacy tests enforce that distinction.

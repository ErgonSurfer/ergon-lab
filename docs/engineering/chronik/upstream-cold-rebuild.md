<!-- SPDX-License-Identifier: MIT -->
# Reconstruct ALP and plugin history through upstream Chronik

**Engineering change 0056 · Open Question · record-review-only**

This test-only step checks the existing upstream indexer's first reconstruction
and subsequent reopen. It adds no index, token parser, plugin engine or native
consensus implementation. Its parent is the signed public commit integrated by
PR #58, `e70c7c2063e786663a0164a0a6c8320cb015eae8`.

## A history older than the capture window

One native-only process creates the existing upstream-generated zero-value
GENESIS 1000 + baton, MINT 500 + baton, SEND 1400 + 100 and BURN 100 graph, with
the existing public plugin fixture identifier. No Chronik index or plugin-input
directory may exist during this phase. The graph is confirmed at height 102;
one-block native RPCs extend the chain to 402 without changing any deadline.

Transaction roles are bound by exact native input outpoints, not block order:
Ergon's canonical transaction order need not be the graph's ancestry order.
Both transaction IDs and raw native wires must match before reconstruction.
The corpus is now older than the retained 288-block capture window.

A second process opens a genuinely absent index through unchanged upstream
Chronik setup/resync and writers. Both existing complete ALP and public-plugin
HTTP oracles must pass. A third process reopens that same index, rechecks the
responses, mines one more native block and requires the same corpus at tip 403.
Identity, plugin inputs and the persistent lease inode must remain unchanged.

No index observed the original mempool. Historical first-seen values are
therefore explicitly unknown (`0`) under upstream semantics, not fabricated
from a block/header or filesystem time. Reopen must preserve that exact result.
This differs from same-index hot catchup, where known observation times survive.

## Run and refuse

After the existing reviewed `regtest-plugins` build:

```sh
python3 -B tools/engineering/check_chronik_cold_rebuild.py check
python3 -B tools/engineering/check_chronik_cold_rebuild_self_test.py
python3 -B tests/compatibility/upstream-chronik/cold_rebuild_service_self_test.py
python3 -B tests/compatibility/upstream-chronik/cold_rebuild_service.py \
  --source-root "$PUBLIC_SOURCE" --build-root "$PUBLIC_BUILD" \
  --work-parent "$DISPOSABLE_PARENT" --repetitions 3 --report "$NEW_REPORT"
```

Three repetitions mean nine fresh daemons, three native-only positive stops
and six positive joined service stops. The exact upstream template export
requires four assertions. All inherited matrices, assertion counts, clients,
budgets and deadlines remain mandatory; this adds a CI step, not a replacement.

The first failure ends the series without retry or a passing report. Cleanup
attempts every owner, retaining an unverified root if cleanup fails. Existing
or redirected index/input paths are refused, never adopted or deleted as a
recovery shortcut. Reports are exclusively created outside source/build/work
after cleanup and before/after source, test-input and binary binding checks.
They contain fixed results and public commitments, not raw logs or host paths.

## Limits and counterevidence

The initial local module preflight passed three repetitions/nine processes
with the existing bound binaries. That is not a fresh public-commit build,
independent reproduction or an imported evidence pack. The final public CLI
and exact candidate CI require their own checks. No local report is tracked.

The final public CLI subsequently passed its own three-repetition/nine-daemon
local run, with before/after source, test-input, build and binary bindings and
complete cleanup. Sixteen standard-library contract/rejection suites passed
112 tests, including 17 new cold-runner/policy tests. The runner self-test's
synthetic native summaries are not fresh executions of those native matrices.
These local checks do not substitute for exact public-commit CI or independent
reproduction, and do not explain any historical timeout.

This remains unpruned local regtest, maximum height 2000, default OFF, with the
exact trusted public synthetic plugin, zero peers, and WebSocket/broadcast
disabled. Native values and fees are zero, not funded token economics.
No full-chain scaling, native reindex/reindex-chainstate, arbitrary operator
plugin, power-loss recovery, server replacement or deployment is established.
The current native startup graph is still bounded and copied in full: this
test must not be represented as removing that full-chain prerequisite.

All historical startup and aggregate stop/reopen timeouts and upstream
dependency alerts remain open. A later success does not explain or erase
them. Rust registry inputs remain locked; OS/SDK/runner inputs are not fully
locked. Source/configuration checks and binary hashes do not replace a build
provenance record. No automatic readiness or proof promotion occurs.

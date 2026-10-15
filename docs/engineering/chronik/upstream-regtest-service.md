<!-- SPDX-License-Identifier: MIT -->
# Upstream Chronik in a real regtest daemon

This experimental profile runs the existing Chronik upstream indexer, database
writers and HTTP router inside `bitcoind`. It is an integration step beyond the
component fixtures, not a production service or a mainnet activation.

## Two explicit switches

Build with `BUILD_CHRONIK_UPSTREAM_REGTEST=ON`, the upstream link and archival
adapters ON, and plugins, the old observer and Qt OFF. The new switch defaults
OFF. The ordinary archival and plugin fixture builds remain dormant.

Run that binary with `-regtest -chronikupstreamregtest=1` on a disposable,
unpruned datadir. Runtime defaults OFF. Other networks and pruning are refused
before index creation. There is no arbitrary index path, public bind address,
plugin configuration, websocket or transaction-broadcast capability.

The service owns `chronik-upstream-regtest` under the native network datadir.
Its HTTP listener binds atomically to an ephemeral IPv4 loopback port, printed
in the local node log. That line is a listener address, not a durable readiness
promise: requests are still checked against the exact native source generation.
Unavailable or changing views fail closed instead of serving a stale success.

## Native lifetime

Startup waits for native block import/reindex and mempool restoration. It then
constructs the existing owned worker before peers start. All DB work uses the
existing upstream setup/resync and writers outside the native source locks.
Callbacks remain ordered invalidation tickets, not substitute transaction data.

Shutdown closes requests, unregisters and drains callbacks, joins the worker and
HTTP/Tokio users, and releases the DB lease before the native scheduler stops or
chain/coin resources are destroyed. Native shutdown cancels an in-progress
capture; it is not itself persistent index corruption. Genuine index failures
remain index-only. A rejected root does not stop native mining and is never
automatically wiped, relabeled or reconstructed.

Startup is still synchronous in this experimental profile. Slow or stalled
storage can delay initialization or a joined stop. There is no hard I/O latency
bound or unrestricted service-availability claim.

## Run the real-process witness

Use independent out-of-source build and disposable work roots, with the locked
offline Rust inputs from the upstream integration workflow. Then run:

```sh
python3 -B tools/engineering/check_chronik_regtest_service.py check
python3 -B tools/engineering/check_chronik_regtest_service_self_test.py
python3 -B tests/compatibility/upstream-chronik/regtest_service_self_test.py
python3 -B tests/compatibility/upstream-chronik/regtest_service.py \
  --source-root "$PUBLIC_SOURCE" --build-root "$PUBLIC_BUILD" \
  --work-parent "$DISPOSABLE_PARENT" --repetitions 3 \
  --report "$NEW_REPORT"
```

The witness checks runtime default OFF, exact wrong-network/pruned refusals,
fresh genesis startup, 101 natively mined zero-subsidy blocks, an accepted
zero-value parent/child mempool, restart with that nonempty native pool,
confirmation, disconnection, reconsideration, raw transaction byte parity,
confirmed restart and positive joined stops. A synthetic failed-root marker is
then refused while the same native daemon mines another block. Reports are
exclusive, path-free and emitted only after process-group and directory cleanup.
They contain no raw logs, parent environment, operator material or credentials.
Each repetition also runs a fresh native process with nine fixed assertions:
hold a capture in flight, request shutdown, join, require no failed marker or
remaining lease, restore the test shutdown bit and reopen the actual index.

The zero-value transactions use the existing native `prioritisetransaction` RPC
with a synthetic priority of 1,000,000 fixoshis per transaction. No value or fee
is created or paid. Native minimum relay/dust fees are unchanged: zero settings
are invalid in this baseline. This witness is not a funded fee-market test.

## What this does not establish

The existing experimental limits are retained: height at most 2000, at most
4096 native block-index entries/pool transactions, a 288-block retained capture
window, a bounded callback queue and a 64 MiB serialized payload budget. These
are rejection limits, not measured heap/RSS or full-chain scaling guarantees.
Deep reorgs below the retained anchor require a separately governed rebuild.

Plugins remain disabled in this daemon profile, even though their existing
upstream engine is exercised in separate component builds. No operator plugin,
mainnet data, real server, crash-recovery contract, production migration,
deployment or desktop integration is included. Upstream dependency security
alerts remain open. System packages and runner images are not locked. CI or a
local pass is an observation, never independent reproduction or readiness for
production exposure.

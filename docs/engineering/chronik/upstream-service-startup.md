<!-- SPDX-License-Identifier: MIT -->
# Owned nonblocking startup in the bounded regtest service

The optional index now initializes on its own joined owner thread. Native
initialization does not wait for Chronik's import/mempool wait, filesystem,
RocksDB resync or HTTP setup. The owner still waits for the **real** native
mempool restoration before reading chain/pool data. Nothing is detached.

An unpublished bridge owns the native ancestor graph throughout upstream
resync. Before switching to live access, the service captures that indexed old
branch's last at most 288 blocks and verifies its native storage binding. The
existing captured reconciliation can then disconnect/connect through unchanged
upstream writers if the native node progressed or reorganized. A busy source
stays unavailable and retries; request admission remains the same source/epoch
gate and returns 503 until a complete current view exists. Binding a loopback
listener never grants readiness or consensus authority.

The historical fixture startup remains strict: its source drift still rejects
and quarantines as before. Only the separately owned service transition permits
honest native progress, with the old branch retained and checked. Unsupported
storage relocation, deeper-than-retained reorg, failed or mismatched roots,
profile/resource contradictions and real DB errors still fail closed.

Native shutdown during initial resync is cancellation, not a fabricated DB
corruption marker. The bound index is preserved and a fresh process must reopen
it through upstream setup/resync. Shutdown joins initialization, callbacks,
HTTP/Tokio and DB ownership **before** native scheduler/chain/coins destruction.
A storage call which stalls still holds this join: no hard I/O latency promise,
timeout-discard or unsafe resource release is introduced.

The public runner adds five fresh-process native witnesses with exact passing
assertions 7 / 5 / 6 / 9 / 9: reorg/progress during owned startup, cancelled
startup and reopen, import-wait independence, native mining before index open,
and Stop waiting for admitted startup. The last test waits for the explicit
stop-admission bit before releasing its deterministic latch. The existing real
daemon crash/reopen/failed-root lifecycle and strict native matrix remain.

This is still default OFF, unpruned bounded local regtest, plugins OFF and
ephemeral loopback reads only. It changes no consensus, activation, dependency,
operator configuration or upstream indexing algorithm. No deployment or proof
promotion is authorized. Adapter identities change; this is not a migration
contract for an existing operator database.

The retained stop/reopen matrix timeout documented in changes 0049 and 0050 is
**still unresolved**. Two local progress-instrumented diagnostic runs passed,
but they do not explain that timeout or replace the required unmodified matrix.
This work is a local review checkpoint, not PR-ready. Full real-daemon ALP
lifecycle coverage remains the next separate slice.

Final-source local checks passed the three daemon cycles and one whole inherited
matrix (45 cases / 2291 assertions). The ten-repetition run then timed out on
stop/reopen repetition 7, with the same 60-second bound. It produced no passing
report. These observations remain separate: startup progress is not evidence
that the retained timeout was fixed.

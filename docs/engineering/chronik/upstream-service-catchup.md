<!-- SPDX-License-Identifier: MIT -->
# Catch up without retaining a callback queue

**Engineering change 0055 · Open Question · record-review-only**

The real service must tolerate native progress while Chronik is busy. A bounded
queue of invalidation tickets is useful for strict component fixtures, but it
is not a suitable backlog representation for the continuous service: a burst
can exhaust it even when each notification carries no historical payload.

This change keeps the existing upstream Chronik indexer, ALP parser, plugin
engine and HTTP router. Only the host worker's invalidation bookkeeping changes.
It depends on the published PR #57 head, not on an assumption that PR #57 has
already merged.

## One frontier, unchanged publication fences

In the service profile, accepted and completed sequence counters represent the
outstanding ordered interval. The worker takes its current high-water mark,
captures native state and reconciles through the existing upstream writers.
It acknowledges only that prefix; notifications arriving meanwhile remain
pending. No transaction/block payload or ticket deque is retained by callbacks.

The source identity, generation, epoch and ready/read-admission laws still
apply. A notification is not a block to replay and is not permission to publish
an intermediate or stale projection. Native callbacks perform bounded metadata
work only. The original strict fixture deque and its capacity refusal remain.
Capture, retained-history, reorg and storage budgets are not enlarged.

The first terminal index failure records a fixed reason and pending counter
before draining. It does not serialize exception text. Hook failure remains
terminal to the index; native validation and mining remain independent. Stop
closes admission, drains the unpublished interval and joins existing owners.
There is no automatic wipe, adoption or reindex of failed roots.

Source gates cache only identities read from full immutable Git object IDs,
returning fresh dictionaries so callers cannot poison the cache. Current files,
contracts and records are rechecked on every validation. No branch, `HEAD`,
working-tree identity or validation success is cached. This avoids repeated Git
subprocesses in the rejection tests without weakening an oracle or deadline.

## Tests that can falsify the change

Five new fresh-process native leaves require **10 / 8 / 25 / 21 / 8 assertions**:
capacity reason after drain; first notification reason preservation; ordered
service coalescing through disconnect/replacement; stop discarding an unpublished
range; and terminal notification failure isolation. Each retains the existing
60-second process deadline. All 48 inherited native case bodies and the old
runner's matrices, assertion counts, execution and cleanup code remain exact.

The new `catchup_service.py` uses the existing public ALP templates and both
ALP/plugin HTTP oracles. It confirms GENESIS, MINT, SEND and BURN at height 102,
advances the native chain through 300 one-block RPCs without waiting for HTTP
catchup between them, then requires tip 402 and the unchanged ALP/plugin graph.
It stops positively, reopens the same index in another process and rechecks the
snapshot, known native first-seen times, input/index identity, zero peers and
disabled WebSocket/broadcast. Three repetitions mean six fresh daemons. The
existing daemon waits and joined-stop deadline are unchanged. The first failure
ends the series; it creates no passing report and triggers no automatic retry.
Cleanup attempts every owned daemon even if one attempt fails. An unverified
cleanup retains the disposable root and refuses a passing report. A synthetic
regression test covers failure at either owner; it failed before this review
correction and passes with it. This changes no daemon or native deadline.

Run after the existing reviewed `regtest-plugins` build and controls:

```sh
python3 -B tools/engineering/check_chronik_service_catchup.py check
python3 -B tools/engineering/check_chronik_service_catchup_self_test.py
python3 -B tests/compatibility/upstream-chronik/catchup_service_self_test.py
python3 -B tests/compatibility/upstream-chronik/catchup_service.py \
  --source-root "$PUBLIC_SOURCE" --build-root "$PUBLIC_BUILD" \
  --work-parent "$DISPOSABLE_PARENT" --repetitions 3 \
  --report "$NEW_REPORT"
```

The report must be new, outside source/build/work, created exclusively after
cleanup and before/after identity checks. Only fixed results, public commitments
and environment contracts are retained, not operational output or local paths.
The configured adapter digest changes with compiled inputs. This is not a
substitute for a separately reviewed binary-to-source build provenance record.

## What is still unresolved

Local diagnosis reproduced the old queue's capacity refusal at 128 pending
notifications while native mining continued. After the change, 18 individual
native leaves passed 379 assertions, including 13 inherited stop/reopen leaves.
That is **not** a passing repetition of the historical aggregate matrix.

A predeclared three-observation runtime series stopped on its first startup
deadline, before RPC or log output; the remaining two observations were not
run. A later, explicitly nonqualifying diagnostic reached 402 and reopened the
same index with matching ALP/plugin data. It neither replaces the failed series
nor establishes the startup cause or cold-start stability. Public CI must run
the new public client separately; no private report is imported as evidence.

Subsequently, one invocation of the new public client passed its five native
leaves (72 assertions), upstream template export (4 assertions) and all three
catchup/reopen repetitions with six fresh daemons locally. Both ALP/plugin
oracles and known first-seen commitments remained exact, with six joined stops
and complete disposable-root cleanup. This is a separate local preflight, not
independent public reproduction or a replacement for the earlier failed run.

The first local policy regression batch also exceeded its 180-second per-file
driver budget in the startup-policy self-test; that batch was then interrupted.
Narrow identity reuse allowed that test to pass, but the next batch reached the
same budget in the recovery-policy test and was interrupted too. Immutable-byte
reuse is therefore shared across the historical Git reader, not just new gates.
This is distinct from the native daemon startup failure. Immutable-object
lookup reuse addresses repeated policy subprocess work; both regression
timeouts remain recorded and all rejection classes must still pass afterwards.

All previous aggregate timeouts and upstream dependency alerts remain open.
This is bounded unpruned regtest, height at most 2000, default OFF, and the exact
public synthetic plugin only. Native transaction values and fees are zero;
token atoms are not proof of funded token economics. OS/SDK/runner dependencies
are not locked. No full-chain, operator-plugin, crash-safety, deployment,
activation, independent reproduction or server-replacement readiness is claimed.

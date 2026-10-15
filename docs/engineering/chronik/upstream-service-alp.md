<!-- SPDX-License-Identifier: MIT -->
# ALP through the real bounded regtest daemon

This is a test-only successor to change0051, not another ALP implementation.
The daemon still uses the integrated upstream indexer, token engine and router.
Compile-time and runtime defaults, native consensus and all dependencies are
unchanged. No plugin or new service capability is enabled.

## What the client exercises

The native test-only export calls the existing `fixture_alp_script`, which
uses upstream `genesis_section`, `mint_section`, `send_section`, `burn_section`
and `sections_opreturn`. Four exact builder results cross into the Python
client; only a uniquely bound synthetic token-id placeholder is substituted,
using upstream's little-endian wire convention. Python serializes native
transactions and checks protobuf responses; it does not parse or validate ALP.

The actual legacy-compatible daemon accepts four zero-value transactions:
GENESIS1000 and a mint baton; MINT500 and a replacement baton; SEND1400/100;
intentional BURN100. The oracle binds each input and output color, baton,
spender, token metadata, raw bytes, native first-seen time, token/LOKAD history
and exact token UTXOs (1400 atoms and one baton).

It repeats those queries after a real process restart with pending mempool,
confirmation, invalidation, reconsideration, and confirmed process restart.
An invalid mint without a baton and truncated upstream metadata remain valid
native transactions but receive the upstream token diagnostics. Native mining
must continue and the index must not be quarantined. Listener and OS lease
cleanup use the existing joined shutdown contract.

## Run

Use the unchanged reviewed regtest build profile, plugins/Qt/observer OFF.
Use separate canonical source/build/work/report roots. Heavy ephemeral work
belongs on the operator-selected dedicated scratch volume, not in source.

```sh
python3 -B tools/engineering/check_chronik_service_alp.py check
python3 -B tools/engineering/check_chronik_service_alp_self_test.py
python3 -B tests/compatibility/upstream-chronik/alp_service_self_test.py
python3 -B tests/compatibility/upstream-chronik/alp_service.py \
  --source-root /public/source --build-root /public/build \
  --work-parent /public/alp-work --repetitions 3 \
  --report /public/evidence/new-alp-observation.json
```

Each three-cycle run requires nine fresh daemons plus one native template
process with exactly four passing assertions. Reports contain only bounded
facts/digests, never raw output, binding paths or parent environment. Reports
are created exclusively after cleanup and repeat binding checks.

## Limits and counterevidence

Knowledge remains **Open Question**, evidence ceiling **record-review-only**;
no incoming artifact or automatic public proof promotion. Locally generated
observations are not independent reproduction or a reproducible-build claim.

This tests zero-value inherited regtest mechanics, not funded token economics,
standard relay, production persistence or deployment. The native priority
adjustment of1000000 fixoshis is synthetic, not a real fee. No token activation
or native consensus authority is introduced. Plugins, public exposure,
WebSocket/broadcast, server cutover and desktop integration remain out of scope.

The inherited grouped stop/reopen matrix still has an unresolved60-second
timeout. Its exact cases, assertions, process deadlines, equality and cleanup
are unchanged, and it still blocks PR readiness. A diagnostic sample during a
slow run placed the main thread in upstream RocksDB reopen/WAL recovery,
including a read syscall; it did not establish a deadlock or the cause of all
timeouts. Sampling itself is intrusive. Targeted ALP success cannot erase or
replace that counterevidence.

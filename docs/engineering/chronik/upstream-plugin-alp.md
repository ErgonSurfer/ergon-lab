<!-- SPDX-License-Identifier: MIT -->
# Full ALP lifecycle with the existing public plugin active

This test-only change combines the existing upstream ALP lifecycle and public
synthetic plugin on the same real native daemon graph. It adds no token parser,
token validator, plugin implementation, native rule or production source.
The compiled adapter, all five build profiles and default-OFF gates are unchanged.

The client obtains GENESIS/MINT/SEND/BURN scripts from the existing upstream
builder export (one exact native case, four assertions). Only the exact token-ID
placeholder is rebound; the existing public fixture's `ERGN` eMPP LOKAD push is
appended to each operation to invoke the already integrated upstream plugin.

Each repetition runs three fresh daemon processes and checks:

- GENESIS 1000 plus mint baton, MINT 500 plus baton, SEND 1400 and 100, BURN 100;
- the existing ALP oracle's native wire, first-seen times, colors, spenders,
  token metadata, token/LOKAD histories, 1400 remaining atoms and one baton;
- exact plugin data for every non-OP_RETURN output and inherited plugin input,
  including the two-parent SEND and baton propagation;
- plugin group, confirmed/unconfirmed histories, and exactly three plugin
  UTXOs (baton, 1400-atom output and non-token burn change);
- pending and confirmed process reopen with stable combined response digests,
  confirmation, disconnect and reconsideration under the actual node;
- unchanged plugin source/config/index identity, zero peers, unavailable
  WebSocket/broadcast, positive joined shutdown and verified disposable cleanup.

The separate original ALP and plugin clients remain mandatory and unchanged.
In particular, the original ALP client's invalid-mint and malformed-metadata
controls are not replaced by this positive combined lifecycle. Arbitrary or
operator plugins, actual-daemon version changes/rebuilds and server migration
remain separate future work. Python is trusted test code, not a sandbox.

Run the new source gate and rejection tests, then use the existing
`regtest-plugins` build:

```sh
python3 -B tools/engineering/check_chronik_plugin_alp.py check
python3 -B tools/engineering/check_chronik_plugin_alp_self_test.py
python3 -B tests/compatibility/upstream-chronik/plugin_alp_service_self_test.py
python3 -B tests/compatibility/upstream-chronik/plugin_alp_service.py \
  --source-root SOURCE --build-root BUILD --work-parent EMPTY_WORK \
  --repetitions 3 --report NEW_REPORT
```

SOURCE, BUILD, EMPTY_WORK and NEW_REPORT must be distinct canonical bindings.
The existing compiled adapter projection is unchanged. A separate exact digest
binds the two new test files and six unchanged public oracle/template inputs
before and after execution; it is not binary-to-source provenance. Every child
uses the existing closed environment and unique temporary directory. Reports
contain normalized commitments, never raw streams or absolute input paths,
and are created exclusively after positive cleanup and source/binary rechecks.

All native input/output values and actual fees are zero. Synthetic mining
priority is not currency. This does not prove funded economics, a full-chain
service, power-loss recovery, independent reproduction or production readiness.
Historical local aggregate 60-second stop/reopen timeouts remain counterevidence
even after Linux CI successes; no timeout, selection, assertion, durability or
cleanup criterion is changed. No deployment, activation or automatic evidence
promotion is included. This record starts at Open Question / record-review-only.

## Initial local preflight — 2026-10-16

Three complete repetitions passed locally: nine fresh daemon processes and one
exact upstream template-export process with four assertions. Both ALP and
plugin oracles passed for all four operations, pending and confirmed process
reopen, and disconnect/reconsider. Positive joined shutdown and removal of all
owned disposable roots were verified before report creation.

This is a local observation with the unchanged, separately bound compiled
adapter, not a fresh public-candidate build or independent reproduction. The
report remains outside the repository. The new client still requires public CI
and review; older timeout counterevidence and upstream alerts remain open.

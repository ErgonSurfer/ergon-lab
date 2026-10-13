<!-- SPDX-License-Identifier: MIT -->
# Chronik plugin restart and reconstruction

A plugin index must survive an interpreter restart without changing its
transaction metadata. An incompatible plugin version must be refused, and
reconstruction must use the existing Chronik indexer rather than a separate
implementation. These fixtures exercise those boundaries on accepted native
Ergon blocks with the public synthetic plugin.

## Native source and separate processes

The first child creates a small public regtest corpus through native block
validation: 101 blocks, an ALP GENESIS and its confirmed child, plus one
unconfirmed descendant. Every later child replays the exact block bytes through
`ProcessNewBlock` and admits the pending transaction through the native mempool.
No chain, coin view or index is fabricated to make a restart pass.

Each phase owns a fresh process and exactly one Python interpreter. Plugin
source and configuration are checked before initialization. Loading an existing
fixture neither recreates missing inputs nor overwrites them. Healthy phases
must agree on the native tip and on an ordered commitment to the real plugin
transaction, groups, UTXO and history responses.

## Version refusal and reconstruction

The runner executes eight phases per repetition:

1. Index with the public version 1 plugin, then stop positively.
2. Reopen version 1 in another process and compare responses.
3. Load version 2 and require both the fixture identity refusal and the exact
   upstream `PluginVersionMismatch`. The latter is a negative setup oracle;
   it cannot return an indexer or start HTTP. RocksDB may update operational
   log files while opening; this is not a byte identity claim for DB files.
4. Reopen version 1 again to verify that its logical index remains usable.
5. Change the accepted native source during a separate startup reconstruction;
   require rejection and a persistent failed marker, without stopping the node.
6. Refuse that incomplete root in a fresh process.
7. Reconstruct version 2 in a new empty root using upstream setup and resync.
8. Reopen version 2 in another process and compare the same healthy responses.

The original version 1 identity and corpus remain unchanged. The original and
failed index roots are preserved throughout the scenario. Reconstruction never
edits an identity, requests `wipe_db`, or quietly adopts a failed root. This is
a fixture test of reconstruction in a new root, not a production database
migration or a daemon `-chronikreindex` contract.

## Run the witnesses

Use the plugin build profile and CPython embedding described in
[Upstream plugins](upstream-plugin-fixtures.md). Keep builds and disposable data
outside the source tree. Create a separate empty work parent and a report path
outside it:

```sh
python3 -B tools/engineering/check_chronik_plugin_restart.py check
python3 -B tools/engineering/check_chronik_plugin_restart_self_test.py
python3 -B tests/compatibility/upstream-chronik/plugin_restart_self_test.py
python3 -B tests/compatibility/upstream-chronik/plugin_restart.py \
  --source-root "$PUBLIC_SOURCE" --build-root "$PUBLIC_BUILD" \
  --work-parent "$DISPOSABLE_PARENT" --repetitions 10 \
  --report "$NEW_REPORT"
```

Ten repetitions execute 80 fresh processes. Each phase has a fixed selection,
assertion count and 60 second deadline. Wrong summaries, changed source or
responses, leaks, incomplete cleanup and report overwrite fail the run. The
report contains commitments and claim levels, never process text or local paths.
All task-owned scenario roots are removed before exclusive report creation.

The eight phase counts are 239, 237, 222, 237, 222, 218, 236 and 237 passing
assertions: 1848 assertions per complete repetition. The duplicate version 1
reopen is deliberate, before and after the incompatible-version oracle.

## Scope of the result

The engineering record starts as Open Question with no incoming evidence. A
passing run is an Observed component result. It does not establish arbitrary
operator plugin compatibility, Python sandboxing, full-chain resource bounds,
mainnet readiness or independent reproduction. The earlier disabled-matrix
timeout observations remain documented; this slice does not explain them.
The first local ten-repetition restart invocation also refused completion with
no report and no retained exact condition. Later passing runs do not establish
the cause of that refusal. An initial retained enabled-matrix attempt refused
completion, and a quiescent disabled-matrix attempt exceeded the unchanged
60 second deadline in the HTTP admission case. Isolated passes of that case
on both the preceding public binary and the new binary do not explain the
group-level refusal. Complete passing reruns must be recorded separately.

The plugin, archival and compiled-out CI profiles remain separate. Daemon
admission and optional defaults stay closed. No consensus, activation,
deployment or desktop integration is introduced.

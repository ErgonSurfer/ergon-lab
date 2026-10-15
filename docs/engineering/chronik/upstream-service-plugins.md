<!-- SPDX-License-Identifier: MIT -->
# Public synthetic plugin in the regtest service

This experimental profile joins the already integrated upstream plugin engine
to the real daemon service. It does not implement a second plugin or token
engine. Both build and runtime admission remain OFF by default.

Build with `BUILD_CHRONIK_UPSTREAM_REGTEST_PLUGINS=ON` in addition to the existing
upstream-link, archival, plugin-test and regtest profiles. Qt and the older
observer remain OFF. Explicit runtime admission requires both
`-chronikupstreamregtest=1` and `-chronikupstreamregtestplugins=1`.

Only the existing public `ergon_public_probe` synthetic fixture is admitted.
There is no arbitrary plugin path, config, class or mode option. The fixed
two-file input directory is created exclusively or checked byte-for-byte
before import. The existing source/config/Python-bound index identity refuses
incompatible roots without rewriting their identity or deleting their data.

The joined startup thread owns one existing upstream `PluginContext` per
process. Python setup/import and all index work remain outside native locks
and off native initialization. Worker callbacks, HTTP/Tokio tasks and the
indexer stop before their plugin and native owners are released. No interpreter
finalization/reinitialization or security sandbox is claimed.

`tests/compatibility/upstream-chronik/plugin_service.py` binds the exact source
projection, CMake profile, Python embedding and binaries before and after its
run. Each repetition invokes one exact native owner case with 12 assertions,
then six fresh daemon processes covering:

- plugin runtime OFF, with no plugin input directory;
- refusal to adopt a non-plugin index, with native mining continuing;
- a public zero-value parent/child pair using upstream-built ALP GENESIS
  metadata and the existing `ERGN` fixture identifier;
- pending and confirmed process restarts, confirmation, disconnect and
  reconsideration, with exact plugin output/input data, groups, history and
  UTXOs checked through the upstream HTTP handlers;
- refusal of modified synthetic config without repair or identity rewrite,
  while native mining continues, and positive joined shutdown.

The script uses synthetic fee-priority deltas, not real currency. All native
prevout/output values and actual fees are zero. The ALP GENESIS quantity of
1000 is checked in plugin metadata; this is not a funded-economic claim or a
replacement for the separate full ALP lifecycle client. Existing upstream
plugin exception, invalid-output, incompatible-version and rebuild witnesses
remain separate component/process tests, not new actual-daemon claims here.

The fifth CI profile is separate from archival, plugins, compiled-out and
regtest. Existing exact case/assertion selections, 60-second component bounds,
cleanup and default-OFF smokes remain mandatory. Historical timeouts and
upstream dependency alerts remain recorded; later green runs do not establish
their complete cause or production readiness.

Scope remains unpruned local regtest, height at most 2000, under the existing
native graph/pool, payload and callback limits. No WebSocket/broadcast,
operator plugin, full-chain admission, consensus change, activation, server
deployment, desktop integration or automatic proof promotion is included.
Rust inputs remain locked; OS/SDK/runner inputs are not reproducible-build
inputs. Reports are generated locally and reviewed separately, never incoming
private evidence.

## Initial local preflight — 2026-10-15

Local preflight completed three actual-daemon repetitions (18 processes) and
the runtime-OFF mining/stop smoke. The normal ten-repetition component matrix
was refused in `runtime-stop-and-reopen-controls`, repetition 3, under the
unchanged 60-second deadline. No complete passing component report was produced
by that run. The deadline covers the full selected 13-case process, not an
individual case. A diagnostic's last active case is not proof that this case
alone blocked for 60 seconds.

These local results are not incoming public evidence or independent
reproduction. The earlier and current refusals remain counterevidence;
instrumented diagnostics cannot substitute for the normal matrix. Review and
independent public CI remain required at this checkpoint before readiness can
be reassessed.

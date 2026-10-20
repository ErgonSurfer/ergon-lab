<!-- SPDX-License-Identifier: MIT -->
# One plugin, several protocol identifiers

Some plugins need to inspect more than one transaction family. The existing
upstream Chronik dispatcher already owns a vector of four-byte identifiers and
invokes each matching plugin once. Its metadata loader previously accepted
only a single Python `bytes` identifier. This change extends that loader, not
the token engine, transaction dispatcher, indexer or database.

The method `lokad_id()` now accepts either the existing four-byte `bytes` value
or a Python `list` containing 1 through 32 unique four-byte `bytes` values.
Order is preserved. Empty or oversized lists, duplicates, wrong widths,
non-byte elements, strings, tuples and other iterable types are rejected.
Legacy single-identifier plugins keep their dispatch behavior. Plugin class
construction, inheritance checks and semantic-version parsing are unchanged.

This is an explicitly authored adaptation of pinned upstream MIT source, not
an assertion that the changed loader is still an exact donor file. Upstream
copyright notices and the immutable donor inventory remain intact. The new
finite manifest binds current postimages and the earlier public preimages.
Both loader files enter the optional adapter content binding so an index does
not silently retain the same adapter identity across a metadata-loader change.

## Public synthetic verification

Eleven Rust unit cases exercise exact limits, legacy bytes, order preservation
and rejection paths. A separate executable uses the actual upstream
`PluginContext` with two synthetic public modules. Six transactions check the
first identifier, the second, both together, an unmatched identifier, the
legacy plugin and a subsequent matching call. A stateful invocation counter
detects accidental double dispatch and calls on unmatched transactions.

The example target is test-only and not linked into the daemon. Its configuration
contains only the two public module names. It enables Python environment/site
isolation before embedding and uses the existing upstream Python helpers.
No private plugin source, wallet, endpoint, operator input or incoming report
is part of this reproduction.

CI builds the example and library tests from the existing locked workspace,
then invokes both executables in separate processes with the existing closed
environment, unique temporary directories, 60-second execution deadline and
verified process-group cleanup. The harness requires all eleven exact case
names, an unambiguous passing summary and the exact six-transaction marker.
Compilation is a separate build step, not included in the process deadline.
All seven existing CI jobs and all inherited node/ALP/plugin controls remain.

The first Linux CI attempt compiled the library and example, then refused the
example's output in both plugin profiles. The standalone upstream logger emits
initialization messages to stdout when no node logger is mounted; the witness
requires only its final marker. The test-only example now mounts the existing
logger API before setup and captures exactly three bounded messages. It checks
the configured Python 3.12 patch version and both complete plugin metadata
messages against the actual loaded context. Unexpected, missing or additional
messages still fail. The stdout/stderr oracle is unchanged and explicitly
rejects leaked setup logging. No daemon logger or production source is changed;
the failed CI runs remain counter-evidence, not a passing runtime result.

```sh
python3 -B tools/engineering/check_chronik_multi_lokad.py check
python3 -B tools/engineering/check_chronik_multi_lokad_self_test.py
python3 -B tests/compatibility/upstream-chronik/multi_lokad_component_self_test.py
```

The exact Cargo build commands, outside-source target directory and executable
invocation are in `.github/workflows/chronik-upstream-link.yml`.
Rust registry dependencies remain locked; runner images, system packages and
CPython are recorded but not fully locked. There is no reproducible-build claim.

## Limits and retained counter-evidence

Plugins remain trusted Python code, not a security sandbox. Registering several
identifiers proves neither token validity nor an ALP/SLP metadata standard.
This change adds no daemon admission, client endpoint, native validation rule,
consensus activation, mainnet parameter, default-ON feature or deployment.

The two historical local normal-matrix timeouts remain open, with unknown
cause and no claim of a complete passing local normal matrix. The maintainer's
[explicit disposition](https://github.com/ErgonSurfer/ergon-lab/pull/65#issuecomment-6277823779)
removed their unconditional source-integration veto, not their evidence or the
strict test contracts. A recurrence on the deployment target, data-integrity
contradiction or unsafe shutdown blocks operational readiness for investigation.
Upstream dependency alerts remain open.

Delivery is `active`; knowledge is `Open Question`; the evidence ceiling is
`record-review-only`. Public CI and code integration do not automatically
promote evidence or authorize release, deployment or cutover.

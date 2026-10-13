<!-- SPDX-License-Identifier: MIT -->
# Upstream plugins, exercised on native Ergon data

This slice uses the existing Chronik Python plugin engine, database indexes,
token metadata and HTTP handlers. It does not implement a replacement plugin
engine. The only Python plugin admitted by the fixture is a small, public,
synthetic example; it is not an operator plugin or a migration of a deployed
plugin. Daemon admission remains closed.

## Three independent build profiles

| Profile | Upstream link | Archival fixtures | Plugin fixtures | Component oracle |
| --- | --- | --- | --- | --- |
| `compiled-out` | OFF | OFF | OFF | Dormant native mining smoke |
| `archival` | ON | ON | OFF | 45 cases / 2291 assertions, including explicit setup refusal |
| `plugins` | ON | ON | ON | 48 cases / 2385 assertions, including four public plugin cases |

All flags default OFF. CI constructs each profile separately. The plugin
profile requires CPython 3.12 interpreter and embedding development library;
their actual bytes and exact patch version bind the adapter. The already
locked PyO3 package is reused: no registry tuple changes. Python startup ignores
parent environment, user/site startup and bytecode caches. This is process
ownership and input isolation, **not a security sandbox for Python code**.

The two helper modules are exact MIT files from public Bitcoin ABC commit
`784d83de2e19eab726898d77bfe7465410d27a9c`. They live in a dedicated plugin
directory; the native Bitcoin Static functional-test helper is unchanged.
The helper manifest records their source paths, licenses and exact digests.
The engine's module adaptation changes only its two embedded include paths.

## What is exercised

The synthetic `ERGN` plugin emits a `public` output group, native satoshi amount,
input plugin metadata count and ALP output atoms. The fixture uses actual native
accepted transactions, the existing upstream indexer and real protobuf HTTP
responses. It checks mempool parent/child propagation, ALP GENESIS metadata,
confirmation, reorg resurrection, query indexes, positive Stop and database
reopen with the same owned process-once Python context.

Plugin exceptions and invalid output indexes quarantine only the index. The
test first witnesses a healthy HTTP response, then accepts canonical index
unavailability or recognized transport closure after failure. Stale success,
malformed responses and unexpected timeouts fail. Native mempool admission,
height progression and shutdown state remain separately checked. Source/config
drift is rejected before opening the database. A plugin-disabled build rejects
setup without creating plugin files or changing native state.

Two corrections accompany this slice:

1. Upstream's mempool token cache intentionally omits all-empty spent-token
   vectors. A GENESIS transaction still has output token metadata. Preserve
   that metadata for the existing plugin engine using one absent input-token
   slot per native input, instead of discarding the whole token transaction
   through `zip`. Token parsing, coloring and validation are unchanged.
2. Make the native executable explicitly depend on the Rust static archive.
   A changed Rust body with unchanged CXX signatures must relink both native
   binaries; a linker flag alone did not establish that dependency.

## Run the public gate

Configure and build with the three flags in the table, keeping builds and Rust
targets outside the source tree. For `plugins`, install CPython 3.12 development
files and set `Python3_EXECUTABLE` if automatic discovery is ambiguous. Follow
the pinned Rust toolchain and locked/offline construction in
`.github/workflows/chronik-upstream-link.yml`.

```sh
python3 -B tools/engineering/check_chronik_plugin_fixtures.py check
python3 -B tools/engineering/check_chronik_plugin_fixtures_self_test.py
python3 -B tests/compatibility/upstream-chronik/plugins/self_test.py
python3 -B tests/compatibility/upstream-chronik/self_test.py
python3 -B tests/compatibility/upstream-chronik/run_repetitions.py repeat \
  --source-root "$PUBLIC_SOURCE" --build-root "$PUBLIC_BUILD" \
  --adaptation-manifest docs/engineering/chronik/upstream-host-adaptation-v1.json \
  --work-parent "$DISPOSABLE_PARENT" --profile plugins --repetitions 10 \
  --report "$NEW_REPORT"
```

Use `--profile archival` for the plugin-disabled oracle. Ten repetitions mean
100 fresh component processes for plugins, or 70 for archival without plugins.
Each plugin-enabled case gets its own process and interpreter. Every case,
assertion count and stream signature is fixed; drift, skipped cases, failures,
timeouts, privacy leaks, incomplete cleanup or report overwrites fail closed.
Reports contain commitments, not process text, binding paths or environment
contents. Dormant daemon smoke profiles are `compiled-out`,
`upstream-linked-dormant` and `upstream-plugins-dormant`.

## Evidence and remaining work

The engineering record remains **Open Question / record-review-only** with no
incoming evidence. A new runner result is a local **Observed /
component_observation**, not automatic independent reproduction. Source/CMake
identity is not binary-to-source provenance. System packages and runner images
are not locked.

Cross-process plugin version/reindex migration, custom operator plugins,
production Python isolation, mainnet replay, resource budgets, server deployment
and desktop integration remain separate work. Earlier observer pruning/reindex
canaries are not proof of those behaviors for this upstream-linked service.
The parent is the public PR #52 checkpoint; parent acceptance or a new exact
binding is required before landing on main. This change opens no daemon service
and changes no consensus or activation rule.

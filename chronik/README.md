<!-- SPDX-License-Identifier: MIT -->

# Chronik boundaries

Chronik remains optional and outside node consensus. Both build options default
to `OFF`:

- `BUILD_CHRONIK_BUILD_ONLY` compiles and tests isolated Rust primitives. It
  does not link them into the node.
- `BUILD_CHRONIK_OBSERVER` links a small Rust/C++ block-event observer into
  `bitcoind`. Its runtime flag also defaults to off and is accepted only on the
  exact local `regtest` profile.

With both options off, CMake does not enter this directory, discover Rust,
invoke Cargo, add a node definition, or change an executable link graph. The
node remains correct and standalone with Chronik compiled out.

## Build-only foundation

When `BUILD_CHRONIK_BUILD_ONLY=ON`, the `chronik_build_only` target compiles
only these dormant packages:

- `abc-rust-error`
- `abc-rust-lint`
- `bitcoinsuite-core`
- `bitcoinsuite-slp`

The observer crate is explicitly excluded from this target. No C++ bridge,
callback, thread, socket, database, data path, HTTP or WebSocket server,
protobuf API, or plugin is built or linked.

## Volatile block observer

When `BUILD_CHRONIK_OBSERVER=ON`, `bitcoind` contains an in-memory observer and
exposes the debug-only `-chronikobserver` flag. Launching without that flag
registers no callback and creates no Chronik state. Launching with it is
fail-closed outside the exact local `regtest` profile.

The enabled observer receives only serialized block-connected and
block-disconnected callbacks. It records a volatile sequence number and a
deterministic diagnostic fingerprint, then writes normalized event lines to
the existing node log. It returns no decision to validation. Shutdown drains
the validation callback queue before unregistering the observer and destroying
its Rust handle.

This boundary creates no file, database, socket, API, service, or Rust thread.
Its state is deliberately lost at shutdown. It is observation, not an index,
and it does not prove restart, reindex, pruning, or deep-reorganization
reconstruction.

## Native-assets boundary

- Families recognized or observed in transactions and blocks: none. The
  opt-in observer receives block hash, height, and connection direction only;
  it receives no transaction bytes and performs no SLP, ALP, or CashTokens
  classification. Dormant SLP and ALP parsing primitives remain unreachable
  from node callbacks.
- Indexed or reconstructed data: none.
- Authoritative token validation or token state: none.
- Governed consensus activation: none.

## Provenance

The four foundation-crate source sets are exact regular-file bytes from the
public Bitcoin ABC commit
`38a7a4dc23a574f2747265fcdf33242648dd2ce1`, tree
`68d559f78c90ed38066283dc87f8652258a1415a`, under preserved MIT notices. The
donor MIT terms remain verbatim in `COPYING`.

The workspace manifest, lockfile, CMake adapters, observer crate, C++ adapter,
tests, and this README are independently authored MIT files. `Cargo.lock` is
generated from this reduced public workspace and contains no Git or external
path dependency. No private history, operator material, or unrelated private
product code is part of this boundary.

## Build and test

Dependency versions are fixed by the committed lockfile. Populate a dedicated
Cargo cache once from the locked public dependency set:

```sh
CARGO_HOME=/absolute/path/cargo-home \
  cargo fetch --locked --manifest-path chronik/Cargo.toml
```

The governed build and tests are offline. Build-only:

```sh
cmake -S . -B /absolute/path/build-only -GNinja \
  -DBUILD_CHRONIK_BUILD_ONLY=ON \
  -DCHRONIK_CARGO_HOME=/absolute/path/cargo-home
cmake --build /absolute/path/build-only --target chronik_build_only
cmake --build /absolute/path/build-only --target check-chronik-build-only
```

Compiled-in observer, runtime disabled by default:

```sh
cmake -S . -B /absolute/path/build-observer -GNinja \
  -DBUILD_CHRONIK_OBSERVER=ON \
  -DCHRONIK_CARGO_HOME=/absolute/path/cargo-home
cmake --build /absolute/path/build-observer --target bitcoind
cmake --build /absolute/path/build-observer --target check-chronik-observer
```

Run the opt-in functional boundary only with the compiled-in build:

```sh
python3 test/functional/feature_chronik_block_observer.py \
  --configfile=/absolute/path/build-observer/test/config.ini
```

Build directories, Cargo caches, and test datadirs belong outside the source
tree. Compiling or enabling this observer is not evidence of runtime indexing,
API compatibility, native-asset validation, or consensus behavior.

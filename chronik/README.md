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

## Bounded block projection

When `BUILD_CHRONIK_OBSERVER=ON`, `bitcoind` contains an in-memory observer and
exposes the debug-only `-chronikobserver` flag. Launching without that flag
registers no callback and creates no Chronik state. Launching with it is
fail-closed outside the exact local `regtest` profile.

For each accepted block-connected callback, C++ serializes the immutable
`CBlock` once with the canonical network serializer. Rust owns one copy, checks
that the 80-byte header hashes to the callback block identity, deserializes the
non-empty transaction vector without trailing bytes, and independently checks
its Merkle root against header bytes 36 through 68. These checks protect the
observer boundary; they do not revalidate or overrule the node.

The observer keeps a reversible projection of at most 288 active-chain blocks,
matching the legacy `MIN_BLOCKS_TO_KEEP` suffix. It records block identity,
height, and transaction count. A connect must name the exact retained tip as
its parent and advance one height. A disconnect must match the exact LIFO tip.
Checked arithmetic and parse failures reject the observer event without
changing its sequence or projection.

At normal startup, the node reads the available active suffix into a separate
staging observer and adopts it only after the complete reconstruction passes.
During `-reindex`, the observer starts from the empty active chain and rebuilds
through accepted ordered callbacks. Disconnecting beyond the retained anchor
enters a fail-closed `rebuild-required` state; further events are rejected by
the observer until a normal restart reconstructs the new active suffix.

The state remains volatile: there is no persistent index to trust or migrate.
This boundary creates no Chronik file, database, socket, API, service, or Rust
thread, and it returns no decision to validation. Shutdown drains the
validation callback queue before unregistering the observer and destroying its
Rust handle. Dedicated `-reindex-chainstate` and actually pruned-datadir
canaries exercise the same reconstruction boundary without adding a second
state path. The chainstate canary replays genesis through height 288 and checks
the unchanged active tip and UTXO-set hash. The pruning canary physically
removes an old block file at height 1001, proves the old body unavailable, and
then reconstructs exactly the still-readable heights 714 through 1001.

## Native-assets boundary

- Families recognized or observed in transactions and blocks: none. The
  opt-in observer structurally deserializes accepted blocks but does not inspect
  transaction outputs or perform SLP, ALP, or CashTokens classification.
  Dormant SLP and ALP parsing primitives remain unreachable from node callbacks.
- Indexed or reconstructed data: only the bounded active-chain sequence of
  block identities, heights, and aggregate transaction counts. It is rebuilt
  from public node block inputs and is not a durable or queryable index.
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
  --configfile=/absolute/path/build-observer/test/config.ini \
  --hermetic-child-env

python3 test/functional/feature_chronik_pruned_observer.py \
  --configfile=/absolute/path/build-observer/test/config.ini \
  --hermetic-child-env
```

The pruning canary uses a fresh isolated manual-pruning regtest datadir. It
creates only enough large blocks to cross one 128 MiB block-file boundary,
then uses small blocks to pass the legacy pruning height. Expect roughly
250 MiB of temporary disk at most; this is separate from the much larger
general pruning test.

Build directories, Cargo caches, and test datadirs belong outside the source
tree. Peak parsing memory includes the C++ serialization, one Rust-owned block
buffer, and parsed transaction structures; startup reconstruction is currently
synchronous. Compiling or enabling this observer is not evidence of a durable
production index, API compatibility, native-asset validation, or consensus
behavior.

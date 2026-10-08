<!-- SPDX-License-Identifier: MIT -->

# Chronik boundaries

Chronik remains optional and outside node consensus. All three build options
default to `OFF`:

- `BUILD_CHRONIK_BUILD_ONLY` compiles and tests isolated Rust primitives. It
  does not link them into the node.
- `BUILD_CHRONIK_PORT_CORE` compiles and tests the token, database, protobuf
  and persistent runtime core. It does not link them into the node or start a
  service.
- `BUILD_CHRONIK_OBSERVER` links the Rust/C++ block-event observer and the
  persistent ALP/SLP token runtime into `bitcoind`. Its runtime and narrow HTTP
  flags also default to off and are accepted only on the exact local `regtest`
  profile.

With all three options off, CMake does not enter this directory, discover Rust,
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

## Dormant port core

`BUILD_CHRONIK_PORT_CORE=ON` adds a separate executable corpus for the next
Chronik integration stage. It builds and tests exactly these default packages:

- `bitcoinsuite-core`
- `bitcoinsuite-slp`, including the complete imported ALP/SLP test corpus
- `chronik-db`, including token ancestry, UTXO, mempool and rollback units
- `chronik-proto`, generated from the pinned `chronik.proto` input
- `chronik-runtime`, an Ergon host-neutral adapter that atomically commits
  accepted blocks, transaction identities and ALP/SLP ancestry to RocksDB

The database's default-disabled plugin facade and supporting utility crates are
workspace dependencies. The imported bridge, full indexer, HTTP, service
library and Python plugin implementation remain excluded from the executable
workspace. Their donor-bound sources are present for review and selective
adaptation, but no current CMake target links them into the node.

The persistent runtime core is the first Lot B slice. It opens a caller-chosen
RocksDB path, accepts only a non-empty genesis-first chain, requires every
connect to extend the exact stored tip, and requires every disconnect body to
match that exact tip and its ordered transaction IDs. One RocksDB write batch
makes the block identity, transaction reverse lookups, ALP/SLP verification
results and token ancestry visible together. Reopening the same path preserves
the indexed tip and token records. Tip rollback removes the same data
atomically. A failed ordering, identity or token-verification check commits
nothing.

The crate remains host-neutral: it owns no validation callback, datadir
selection, socket, HTTP handler or consensus decision. The optional node
adapter described below supplies accepted active-chain blocks and owns startup,
restart, reindex and reorganization reconciliation.

The runtime also exposes a read-only confirmed-token query core derived from
Chronik's upstream query representation. Given a token genesis transaction ID,
it reads the accepted database and produces the canonical Chronik `TokenInfo`
protobuf, including token type, genesis fields and confirmed block metadata.
Non-genesis and unknown transaction IDs return no result. The node-linked C ABI
uses a bounded two-pass contract: first obtain the exact protobuf size, then
copy into a caller-owned buffer. It writes nothing when the buffer is too
small, retains no caller pointer and carries no path or host metadata in the
payload. The optional node adapter can expose those same canonical bytes at the
upstream-compatible `GET /token/:txid` route. No second token representation is
introduced.

## Optional node adapter

When `BUILD_CHRONIK_OBSERVER=ON`, `bitcoind` contains the bounded observer and
persistent token runtime and exposes the debug-only `-chronikobserver` flag.
Launching without that flag registers no callback and creates no Chronik state.
Launching with it is fail-closed outside the exact local `regtest` profile.

The separate `-chronikbind=<ip:port>` flag enables one narrow HTTP surface and
requires `-chronikobserver`. It accepts loopback addresses only and has no
default port, so compiling or enabling the observer alone opens no socket. The
route and response contract match upstream Chronik: `GET /token/:txid` returns
`application/x-protobuf` with canonical `TokenInfo`; malformed transaction IDs
return protobuf error 400, and unknown or non-genesis IDs return protobuf error
404. The initial surface is confirmed-state only. It contains no mempool,
transaction, UTXO, history, WebSocket, plugin, wallet or consensus API.

For each accepted block-connected callback, C++ serializes the immutable
`CBlock` once with the canonical network serializer. The C ABI copies those
bytes once into an owned Rust buffer and moves that buffer through a bounded
512-event envelope. Validation callbacks never wait for indexing. A single
Rust worker thread exclusively owns the live observer state, preserves event
order, and disables observation on overflow. The worker
checks that the 80-byte header hashes to the callback block identity,
deserializes the non-empty transaction vector without trailing bytes, and
independently checks its Merkle root against header bytes 36 through 68. These
checks protect the observer boundary; they do not revalidate or overrule the
node.

The observer keeps a reversible projection of at most 288 active-chain blocks,
matching the legacy `MIN_BLOCKS_TO_KEEP` suffix. Each retained block owns one
fixed-size record per confirmed transaction: transaction ID, block position,
serialized size, a non-cryptographic payload fingerprint, two observed-family
flags, parser/coloring diagnostic counts, and a CashTokens-prefix output count.
It does not retain transaction bodies, inputs, outputs, prevouts, or token/UTXO
relationships. Block and projection fingerprints in debug logs commit to the
ordered records for test comparison only; they are not stable APIs, security
claims, or validity decisions. The record fingerprint is computed once per
block; projection fingerprints compose at most 288 cached block fingerprints
instead of rescanning retained transactions. A connect must name the exact
retained tip as its parent and advance one height. A disconnect must match the
exact LIFO tip. Checked arithmetic and block parse failures reject the observer
event without changing its sequence or projection.

At normal startup, the node reads the available active suffix through a
separate staging worker and adopts its state only after the complete
reconstruction passes. The staging worker is then closed and joined. During
`-reindex`, the live worker starts from the empty active chain and rebuilds
through accepted ordered callbacks. Disconnecting beyond the retained anchor,
or an unwind caught after worker mutation, enters a fail-closed
`rebuild-required` state; further events are rejected by the observer until a
normal restart reconstructs the new active suffix.

Alongside that volatile suffix, the adapter stores the complete accepted
ALP/SLP token projection under `blocks/index/chronik`. Startup resumes only
when the database tip exactly matches the active-chain tip. A fresh database
reconstructs from genesis when every required block is readable. Full reindex
and chainstate reindex explicitly reset the database and rebuild through
ordered accepted callbacks. A tip mismatch, unreadable historical block,
payload contradiction or RocksDB error disables only the persistent runtime;
the node and bounded observer continue, and recovery requires reindex. Runtime
failure never returns a validation decision.

The HTTP service shares the accepted runtime handle instead of reopening or
copying the database. Runtime failure stops the service before closing RocksDB,
so it cannot continue serving stale state. The event backlog is bounded and
payload ownership remains explicit. Shutdown drains the validation callback
queue before unregistering the observer, closes the command channel, joins the
HTTP and Rust workers, flushes RocksDB, and destroys both Rust handles.
Dedicated `-reindex-chainstate` and actually pruned-datadir canaries exercise
the same reconstruction boundary without adding a second state path. The
chainstate canary replays genesis through height 288 and checks the unchanged
active tip and UTXO-set hash. The pruning canary physically removes an old
block file at height 1001, proves the old body unavailable, and then
reconstructs exactly the still-readable heights 714 through 1001.

## Native-assets boundary

- Families recognized or observed in transactions and blocks: the explicit
  local-regtest observer uses the inherited `bitcoinsuite-slp` parser/colorer
  to count transactions containing recognized SLP or ALP family sections. It
  also counts parser and coloring failures as diagnostics. Separately, it
  counts every output whose serialized locking script starts with `0xef` as a
  CashTokens *prefix candidate*, including malformed candidates. These are
  observations of confirmed block bytes, not validity statements.
- Indexed or reconstructed data: the node-linked runtime stores active-chain
  block and transaction identities plus verified ALP/SLP token ancestry,
  metadata, genesis payloads, mint, send and burn state. It survives restart
  and applies exact-tip rollback. Confirmed token-genesis metadata is readable
  through both the bounded C ABI and the opt-in, loopback-only upstream
  `GET /token/:txid` protobuf route. The separate 288-block projection remains
  a bounded diagnostic view.
- Authoritative token validation or token state: none. Chronik resolves and
  verifies ALP/SLP ancestry for its own accepted-block index, but that result
  cannot accept or reject a node transaction or block, alter chain selection,
  or activate consensus. CashTokens remains prefix observation only.
- Governed consensus activation: none. No activation height, chain parameter,
  testnet rule, or mainnet rule is added. Observing SLP, ALP, or an `0xef`
  prefix does not make that asset family native or active in Ergon consensus.

## Provenance

The import contains 206 exact regular-file bytes from the public Bitcoin ABC
commit
`784d83de2e19eab726898d77bfe7465410d27a9c`, tree
`763739eb3c255e90a6122a2679c667089f9efad2`, under preserved MIT notices. This
coherently rebases the four earlier foundation crates and adds the reviewed
Chronik source set and missing ALP/SLP corpus from one donor identity. The donor
MIT terms remain verbatim in `COPYING`.

Three additional donor files are bound by exact preimage and postimage while
removing four trailing ASCII spaces from comments. No executable token,
database, protobuf or runtime semantics change in those adaptations.

`docs/engineering/chronik/chronik-port-lot-a-donor-v1.json` binds every local
destination to its donor path, Git blob, mode, byte count and raw SHA-256. The
dependency-free checker rejects path, byte, mode, workspace, lockfile or build
boundary drift:

```sh
python3 -B tools/engineering/check_chronik_port_core.py check
```

The reduced workspace manifest, lockfile, CMake adapters, observer crate,
persistent runtime crate, C++ adapter, checker, tests and this README are
independently authored MIT files.
`Cargo.lock` is generated from the public workspace and contains no Git or
external path dependency. No private history, operator material or unrelated
private product code is part of this boundary.

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

Dormant token/DB/protobuf port core:

```sh
cmake -S . -B /absolute/path/build-port-core -GNinja \
  -DBUILD_CHRONIK_PORT_CORE=ON \
  -DCHRONIK_CARGO_HOME=/absolute/path/cargo-home \
  -DCHRONIK_LIBCLANG_DIR=/absolute/path/to/libclang
cmake --build /absolute/path/build-port-core --target chronik_port_core
cmake --build /absolute/path/build-port-core --target check-chronik-port-core
```

`CHRONIK_LIBCLANG_DIR` is needed only when the native RocksDB binding cannot
discover `libclang` itself. Protobuf generation uses the exact `protoc` selected
by CMake. The reduced D1 lock resolves RocksDB 0.24.0 and
`librocksdb-sys` 0.17.3+10.4.2, whose declared minimum supported Rust version
is 1.85.0. That dependency constraint supersedes the donor workspace's stale
1.80.0 declaration for this port. The CI job installs Rust 1.85.0 explicitly,
fails if Cargo, rustc or the sysroot drift, fetches the committed lock once and
then runs the governed test phase offline.

Compiled-in observer and persistent runtime, disabled by default:

```sh
cmake -S . -B /absolute/path/build-observer -GNinja \
  -DBUILD_CHRONIK_OBSERVER=ON \
  -DCHRONIK_CARGO_HOME=/absolute/path/cargo-home \
  -DCHRONIK_LIBCLANG_DIR=/absolute/path/to/libclang
cmake --build /absolute/path/build-observer --target bitcoind
cmake --build /absolute/path/build-observer --target check-chronik-observer
```

Run the opt-in functional boundary only with the compiled-in build:

```sh
python3 test/functional/feature_chronik_block_observer.py \
  --configfile=/absolute/path/build-observer/test/config.ini \
  --hermetic-child-env

python3 test/functional/feature_chronik_asset_observer.py \
  --configfile=/absolute/path/build-observer/test/config.ini \
  --hermetic-child-env

python3 test/functional/feature_chronik_token_http.py \
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
buffer per caller that has crossed the C ABI, and parsed transaction structures;
startup reconstruction and the bounded event worker are asynchronous from
validation callbacks. This slice tests one served route; it does not claim the
rest of Chronik's API, native-asset consensus activation or production
readiness.

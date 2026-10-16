<!-- SPDX-License-Identifier: MIT -->
# Startup capture with bounded native lock work

Startup previously copied every native block-index row while holding both
`cs_main` and the mempool lock. The owned graph was then built outside those
locks and released after successful resynchronization, but copying work under
one lock acquisition still grew with the complete block index.

The bridge now copies at most 64 rows per acquisition. It releases both locks
and yields between batches. A batch owns only header/scalar values and the
next hash to look up: no native index pointer, hash address or hash-table
iterator crosses a lock boundary. The existing 4096-row buffer is allocated
before locking; graph allocation and skip-link construction remain outside
the locks.

Every batch checks the healthy profile, tip hash, chain and mempool generations,
map size and bucket count. The last batch is followed by another source check
before graph construction. A rehash or header insertion matters even if it
does not advance the active-chain generation. The full-width mempool generation
detects changes hidden by the legacy wrapping transaction counter. Duplicate
hashes and incomplete traversal are rejected. Native block-index lifetime is
still governed by the existing joined service owner; live erase/replacement or
chainstate teardown during a borrowed owner is not supported.

The optional C++ test hook receives the copied row count after each batch.
Tests use another joined thread to acquire both native locks, and separately
exercise native mining, mempool counter wrap, map rehash, cancellation, hook
exceptions and drift after the last batch. Failed capture publishes no graph,
changes no native authority and triggers no automatic retry. All preceding
native cases are retained unchanged. The new group contains five cases and
54 assertions under the existing 60-second process deadline.

## Remaining work before a mainnet shadow service

This change bounds metadata-copy work per lock acquisition; it does not measure
wall-clock lock latency or RSS. The final storage fence still checks the full
owned graph atomically. Splitting that fence requires a storage mutation
generation or an equivalent native contract, so a previously checked disk
position cannot change unnoticed between batches. The complete transient graph
and its value buffer also still scale with the number of indexes.

The runtime remains restricted to unpruned local regtest, height 2000, 4096
indexes/mempool entries and the existing payload/reorganization budgets. A later
operator profile must close the storage-fence and full-history resource questions,
support reviewed plugin identities, and compare a parallel mainnet instance
against legacy before any server replacement. The existing Chronik indexer,
token algorithms and plugin engine remain the integration foundation.

Build the reviewed native profile, run the source gate and its self-test, then
run `tests/compatibility/upstream-chronik/run_repetitions.py` with the existing
source/build/work/report arguments and `--profile regtest-plugins`. The runner
checks the complete inherited matrix plus this new disjoint group. Local
observations and CI results do not automatically promote engineering evidence;
historical timeout counterevidence remains open.

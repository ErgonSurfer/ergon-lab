<!-- SPDX-License-Identifier: MIT -->
# Materialize the owned startup graph outside native locks

**Engineering change 0057 · Open Question · record-review-only**

This bounded successor shortens the node's native-lock hold during Chronik
startup. The same coherent chain and mempool capture point copies immutable
header/scalar metadata into a preallocated value buffer. Both native locks are
then released before allocating the owned `CBlockIndex` map, linking parents
and constructing the owned `CChain`. Only the complete graph is published.

No native block-index, hash, parent or skip pointer is retained in the metadata
rows. Native progress during materialization therefore cannot mutate the
captured point. The existing source-generation and storage-position checks
still decide whether the completed snapshot is admissible.

## Exact public controls

The optional C++ hook exists only for deterministic native fixtures. It runs
after value capture and outside both native locks; no CLI, Rust/CXX wire or
runtime setting exposes it. Three fresh-process controls require:

- independent native progress while graph allocation and linking occur;
- a post-capture storage mutation to remain absent from the owned graph and be
  refused by the existing finish fence;
- an exception before publication to leave no partial graph, followed only by
  an explicit fixture invocation that succeeds.

Run the finite source gate and standard-library self-tests, then the public
native runner against the reviewed `regtest-plugins` build:

```sh
python3 -B tools/engineering/check_chronik_startup_metadata.py check
python3 -B tools/engineering/check_chronik_startup_metadata_self_test.py
python3 -B tests/compatibility/upstream-chronik/startup_metadata_self_test.py
python3 -B tests/compatibility/upstream-chronik/startup_metadata.py \
  --source-root "$PUBLIC_SOURCE" --build-root "$PUBLIC_BUILD" \
  --work-parent "$DISPOSABLE_PARENT" --repetitions 3 --report "$NEW_REPORT"
```

This is nine fresh native processes and 60 assertions. Every process retains
the existing 60-second deadline. The first failure ends the run; no retry or
deadline relaxation is permitted. Reports are new, path-free files outside
source, build and work roots, with before/after source/build/binary bindings.

## Limits and counterevidence

The value copy remains a bounded `O(mapBlockIndex)` scan under both locks. The
full owned graph remains retained for the lifetime required by upstream CXX
borrows. Preallocation adds bounded transient metadata. No RSS, full-chain or
mainnet scalability claim follows, and the 2000-height, 4096-index and
4096-mempool limits remain unchanged.

The owned graph still has no skip links, so upstream ancestor traversal can be
quadratic during a full reconstruction. Adding owned skip links and reducing
the startup owner's lifetime are separate changes requiring their own review.
No upstream indexer, ALP, plugin, dependency, consensus, activation or default
changes here. Historical timeout observations and the earlier private driver
filter-selection failure remain explicit counterevidence; neither is erased by
later success. No deployment or proof promotion occurs automatically.

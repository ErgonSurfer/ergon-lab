<!-- SPDX-License-Identifier: MIT -->
# Owned startup ancestry

**Engineering change 0058 · Open Question · record-review-only**

Chronik's unchanged upstream resync asks a captured block index for ancestors
while it walks from the database tip to the node tip. The bounded Ergon startup
bridge already copies the complete block-index graph so Rust never borrows
native chain pointers while doing database work. Before this change those
copies deliberately had no `pskip` links, so repeated ancestor lookups could
degrade to parent-by-parent traversal.

This successor constructs the existing native `CBlockIndex` skip structure on
the owned graph. Every parent is linked first. Owned indexes from the active
chain and side branches are then sorted by height and `BuildSkip()` is called
before the graph is published. The test oracle requires the same logical skip
target as the native graph, a different address, an owned hash lookup and a
strictly lower height.

The change does not alter Chronik's Rust indexer, ALP or plugin engines. It does
not change consensus, dependencies, service defaults, limits or admission. The
owned graph is still retained until bridge destruction; shortening that owner
lifetime requires a separate borrow-lifetime analysis and is not implied here.

The existing `owned-startup` group remains exactly three cases and 593 passing
assertions under the same 60-second deadline. All other native, service and
plugin matrices remain unchanged. These are bounded regtest component checks,
not full-chain performance evidence and not a mainnet-readiness claim.

Validate the source contract with:

```sh
python3 -B tools/engineering/check_chronik_owned_skip_links.py check
python3 -B tools/engineering/check_chronik_owned_skip_links_self_test.py
```

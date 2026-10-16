<!-- SPDX-License-Identifier: MIT -->
# Release the startup graph at the publication boundary

**Engineering change 0059 · Open Question · record-review-only**

The native startup bridge owns a complete `CBlockIndex` graph while upstream
Chronik performs its synchronous database resync. That ownership prevents Rust
from borrowing the live node graph while database work is in progress. Once a
successful finish fence has revalidated the native source and storage, every
startup borrow has ended and the bridge is still unpublished.

This successor releases that owned graph immediately after the successful
fence. Validation still runs under `cs_main` and the mempool lock. Destruction
of the graph runs after both locks have been released, so allocator work does
not extend the native critical section. Cancellation and every failed fence
retain the snapshot for the existing retry or destruction path.

Two native-only diagnostics make the lifetime observable in tests: the number
of retained owned indexes and a portable lower bound on retained bytes. The
lower bound counts the snapshot object, owned `CBlockIndex` values and logical
active-chain pointers. It deliberately excludes map-node and allocator
overhead, and it is not an RSS measurement.

The strict fixture and service finish paths must both begin with a non-empty
owned graph, finish successfully, release it to zero, and then read the live
native tip. The governed `owned-startup` group is now exactly four cases and
607 assertions under the unchanged 60-second process deadline.

This changes no upstream Rust engine, ALP or plugin behavior, consensus rule,
network identity, dependency, default, service limit or activation state. It
is a bounded local-regtest component contract, not a production memory claim
or mainnet-readiness evidence.

Validate the source contract with:

```sh
python3 -B tools/engineering/check_chronik_startup_release.py check
python3 -B tools/engineering/check_chronik_startup_release_self_test.py
```

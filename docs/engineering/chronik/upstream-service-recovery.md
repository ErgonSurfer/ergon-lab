<!-- SPDX-License-Identifier: MIT -->
# Regtest service: recover an owned index after process death

The experimental daemon profile now owns a persistent regular lock-file inode
through the operating system's exclusive file lock. A real `SIGKILL` releases
that lock. A new process can reopen the same identity-bound index through
unchanged upstream Chronik and RocksDB setup/resync. No stale-PID guess,
automatic database deletion, lock-file unlink or identity rewrite is involved.

The lock file remains after a positive stop. Replacing it while owners are
alive would split lock ownership across different inodes and is forbidden.
The older fixture-only `create_new` lease is intentionally unchanged.
Unknown, partial, redirected or hard-linked service leases are refused.
An explicit `probe.failed` marker or mismatched index identity is still a
refusal, not a request to repair or adopt the database. Native mining continues.

The real-daemon witness uses a fresh disposable unpruned local regtest datadir:
create pending zero-value parent/child transactions, reopen their native pool,
confirm/disconnect/reconsider them, reopen the confirmed view, kill the process
without graceful shutdown, and reopen that same root. It verifies raw bytes,
native/indexed tip agreement, stable lease inode and identity, then mines and
indexes one more block. Separate native cases check competing owners and an
unknown lease before any database is opened. No incoming report is evidence.

The host `chronik_lib` crate declares Rust 1.89.0 for the standard-library
[file-lock API](https://doc.rust-lang.org/std/fs/struct.File.html#method.try_lock).
The inherited CI pin remains 1.93.1. No crate, registry tuple, checksum or lock
dependency edge is added; the workspace's other members keep their own minimum.

Run `regtest_service.py` using the documented regtest build from
[the initial service profile](upstream-regtest-service.md). The same runner
executes the exact native cases (9 and 6 assertions) in fresh processes and
fails closed on missing assertions, nonzero exits, timeout or cleanup failure.
The ordinary runtime-default-OFF, wrong-network and pruning controls remain.

This is a bounded process-death witness, **not** power-loss durability, disk
corruption recovery, migration, arbitrary history or production readiness.
Startup remains synchronous in this slice. Dependencies and original native
limits are unchanged; system build inputs are not fully locked. The parent
`a8f3854` is a signed local review checkpoint, not assumed public `main`.
The initial unexplained stop/reopen matrix refusal in change 0049 remains
counterevidence; this work does not establish its cause.
This slice also encountered a 60-second stop/reopen group timeout. A diagnostic
rerun localized another timeout after the identity-refusal case started;
isolating that case and a later detailed whole-group run passed. The exact
underlying cause is still unestablished. No timeout or assertion was relaxed.
The runner now emits fixed path-free refusal categories with the group and
repetition. It never prints exception commands, paths or raw process output.
A supplementary single-repetition run on the final source also timed out in
that group. The final three-cycle crash witness passes, but the complete
non-regression matrix is not green: this remains a local review checkpoint,
**not PR-ready**. No repeated-run requirement is replaced by an isolated pass.

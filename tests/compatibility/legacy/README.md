# Legacy compatibility matrix

This compatibility program is limited to unchanged legacy consensus and honest
coexistence with Bitcoin Static v24.0.5. It contains no optional indexer,
activation logic, testnet rule change, or mainnet rule change.

The governed compatibility test lives outside `test/functional`. The runner
requires the reviewed candidate commit and tree, proves that its direct parent
is the exact signed public integration parent supplied through two mandatory
reviewed CLI identities, and accepts only the exact technical path operations
plus the governance record declared by the current change. It cryptographically
verifies the public root, integration parent and
candidate against one embedded ED25519 public key, principal and fingerprint,
with system/global Git configuration and replace objects disabled. It requires
complete non-shallow history, no grafts or replace refs, and candidate
reachability from the freshly fetched `refs/remotes/origin/main`. It also proves
that the signed parentless public root has the exact baseline tree and that the
integration parent did not change any baseline-controlled node or build path.
The parent commit and tree have no compiled default, environment fallback,
configuration fallback, branch inference or remote-tip inference. They must
match the accepted public record before any build or execution.
That record must use public schema version `1.1` and the repository standing
`PUBLICATION_POLICY.md` provenance inventory; the superseded per-record
`authorship` object and schema version `1.0` fail closed.
It then compares the complete
`test/functional/*.py`, `NON_SCRIPTS`,
and `TEST_PARAMS` inventories plus the byte hashes of `test_runner.py` and
`timing.json`. Any inherited selection delta or unexpected path fails the run.

The matrix requires clean, distinct source trees and builds for the exact
baseline and candidate. Those are the only build-role identifiers; there are no
aliases or implicit role defaults. It executes exactly:

- `mixed-node-coexistence`: honest two-node legacy/candidate coexistence with
  alternating mining and clean same-datadir restarts, followed on both roles by
  a full `-reindex` and a chainstate-only `-reindex-chainstate`. Each lifecycle
  requires its daemon log markers after the restart offset, restores the exact
  pre-rebuild chain, UTXO commitment and raw-tip snapshot, then accepts one new
  block mined by each role. Only after both reindex lifecycles, it restarts the
  same datadirs with manual pruning enabled, submits the same 150 near-megabyte,
  baseline-template-bound blocks to both disconnected roles, mines small blocks
  to height 1001, and manually prunes both datadirs. The pruning oracle requires
  fresh daemon markers, physical removal of `blk00000.dat` and `rev00000.dat`,
  retention of the next file pair, reduced reported storage, preserved headers
  but unavailable pruned block data, exact tip and UTXO state, clean restart,
  and post-prune mining by each role;
- `legacy-mining-baseline`: the corrected zero-subsidy mining fixture against
  the baseline daemon;
- `legacy-mining-candidate`: the same fixture against the candidate daemon;
- `inherited-functional-default-launch`: one unchanged inherited functional
  test through the default node-launch path.

All subprocess parents receive only `LANG=C`, `LC_ALL=C`, `NO_COLOR=1`, the
fixed system `PATH`, `TERM=dumb`, `TZ=UTC`, and a unique absolute `TMPDIR`.
Each execution uses a distinct datadir and port seed. A skip, timeout, nonzero
exit, missing success marker, aliased path or inode, source/build mismatch,
changed inherited selection, incomplete cleanup, or incomplete result fails
closed.

Run from a clean public candidate checkout:

```sh
git fetch --prune origin \
  +refs/heads/main:refs/remotes/origin/main

python3 tests/compatibility/legacy/run_matrix.py \
  --baseline-source=/public/src/bitcoin-static-v24.0.5 \
  --candidate-source=/public/src/ergon-node \
  --expected-candidate-commit=REVIEWED_PUBLIC_COMMIT \
  --expected-candidate-tree=REVIEWED_PUBLIC_TREE \
  --expected-integration-parent-commit=REVIEWED_PARENT_COMMIT \
  --expected-integration-parent-tree=REVIEWED_PARENT_TREE \
  --expected-accepted-record-sha256=ACCEPTED_RECORD_SHA256 \
  --expected-reviewer-identity=REVIEWER_IDENTITY \
  --expected-decision-date=YYYY-MM-DD \
  --baseline-build=/public/build/legacy \
  --candidate-build=/public/build/candidate \
  --work-root=/public/work/legacy-compatibility \
  --report=/public/evidence/legacy-compatibility.json
```

The report is created only after every execution and cleanup check succeeds.
Its destination must also be outside every source, build and disposable work
root. It excludes raw output, parent environment values, temporary paths, and
input paths, while recording the Git version, observed `origin/main` object ID,
and normalized signature results. Before a reviewed public run, every
compatibility claim remains an Open Question. A private run cannot promote the
public evidence status.

This remains a bounded, clean-shutdown regtest comparison. Its pruning check is
manual (`-prune=1`) and uses synthetic consensus-valid blocks; it does not test
automatic target pruning, corrupt storage, permissions or disk exhaustion,
interrupted pruning or reconstruction, crash recovery, reindex or redownload
after pruning, deep reorganizations across pruned history, historical-chain
replay, sustained operation, public-network peers, or mainnet.

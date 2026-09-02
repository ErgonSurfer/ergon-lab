<!-- SPDX-License-Identifier: MIT -->

# Engineering change evidence

Ergon Lab develops the standalone node in small, reviewable steps. Every
technical change carries enough public evidence for another contributor to
understand its origin, affected surfaces, tests, limits, and current proof
level.

This is a development record, not a transfer protocol. Private workspaces,
operator data, credentials, local paths, binaries, and host-bound reports are
never repository inputs. A change is reconstructed from identified public
sources or independently authored here, then tested again from the public tree.

## Public node journey

This is the reader-facing order of the work. A milestone can contain several
small engineering changes, but it cannot borrow evidence from a later step.

| Milestone | Question answered before advancing |
| --- | --- |
| **0 · Public foundation** | Is the signed repository root exactly the reviewed Bitcoin Static v24.0.5 tree? |
| **1 · Legacy compatibility** | Can honest legacy and candidate nodes mine, relay, validate, and follow the same chain without a consensus change? |
| **2 · Mainnet coexistence** | Can the candidate operate on the existing Ergon mainnet beside legacy nodes with matching history, blocks, restarts, and datadir behavior? |
| **3 · Optional observation** | Is the node correct with indexing absent or disabled, and bounded when explicitly enabled? |
| **4 · Experimental fork testnet** | Do separately reviewed consensus changes behave deterministically across activation, rollback, mixed-peer, and reorganization boundaries? |
| **5 · Future mainnet decision** | Is prolonged testnet and operational evidence sufficient to begin a separate community governance decision? |

The milestones are ordered. Mainnet coexistence here means the new standalone
implementation running under the existing legacy consensus—not a fork.
Native-assets and other consensus research may run in parallel, but can enter
the node only at the experimental-testnet milestone. Mainnet activation remains
outside implementation scope until testnet evidence and governance are
separately reviewed.

## Engineering record stages

The machine-readable schema groups individual records into
`legacy-compatibility`, `optional-indexing`, `testnet-activation`,
`mainnet-readiness`, or `research`. Mainnet-coexistence evidence promotes the
legacy-compatible node without requiring a new consensus-code stage.

## Change record

Machine-readable records use IDs such as `ERGON-CHANGE-0001` or
`ERGON-RESEARCH-0001` and live beside this document as JSON. Each record binds:

- the exact Bitcoin Static baseline and any public prerequisites;
- every changed path and its before/after identity;
- public provenance or independent authorship and license;
- consensus, validation, P2P, storage, RPC, wallet, indexing, build, test,
  documentation, and data reachability;
- build roles, falsification scenarios, expected results, and failure
  conditions;
- a closed child-process environment and repository-relative reports;
- delivery state, knowledge status, evidence ceiling, limitations, and
  counterevidence; and
- the review decision and public commit once known.

For the future optional-indexing stage, the build roles are shown below.
Current public node work remains legacy-only until its compatibility gate is
closed:

```text
compiled-out
compiled-in-disabled
local-regtest-indexing
```

The 288-block checks cover restart, full reindex, chainstate reindex, a pruned
datadir, and deep-reorganization failure behavior. Chronik remains optional,
off by default, local-regtest opt-in only, and observe/index-only.

## Validation

```sh
python3 tools/engineering/check_change.py self-test
python3 tools/engineering/check_change.py check
python3 tools/engineering/check_change.py validate \
  docs/engineering/changes/ergon-change-0001.json
```

The field contract is documented in
[`../schemas/change-evidence.schema.json`](../schemas/change-evidence.schema.json).
The dependency-free validator adds cross-field rules for ordered stages,
authority boundaries, public reports, exact build roles, and required
falsification scenarios.

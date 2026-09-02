<!-- SPDX-License-Identifier: MIT -->

<p align="center">
  <a href="../README.md">Home</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../discover/README.md">Discover</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../insights/README.md">Insights</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../network/README.md">Network</a>
</p>

<p align="center">
  <img src="../docs/assets/brand/node-engineering.jpg" width="100%" alt="An isometric precision model of the standalone Ergon node">
</p>

# Node Engineering

The workshop for the standalone open-source Ergon node: source, builds,
operation, compatibility research, tests, gates, and portable evidence.

## Standalone by design

The node remains the authority for its own consensus validation, chain
selection, mempool policy, and persistent chain state. It must be correct and
usable with Chronik compiled out, disabled, unavailable, or removed.

Chronik may observe and index node-accepted data. It never becomes consensus,
mempool, activation, or chain-selection authority.

## From the public baseline to a possible fork

The node does not jump from an imported codebase to new consensus rules. It
advances through an ordered sequence in which every step must produce evidence
before the next one can begin.

| Step | Public objective | Current state |
| ---: | --- | --- |
| **0 · Foundation** | Preserve the exact Bitcoin Static v24.0.5 tree as the signed public root. | `verified` |
| **1 · Compatibility** | Build the standalone candidate and falsify differences from the legacy node in mining, relay, validation, and chain following. | `active` / `Observed`; public `assembled_component` evidence covers the four bounded legacy scenarios, including clean restart, full reindex, chainstate reindex, and bounded manual physical pruning on both roles in [ERGON-CHANGE-0007](../docs/engineering/changes/ergon-change-0007.json). Automatic pruning, corrupt or inaccessible storage, disk exhaustion, interruption and crash recovery, reindex or redownload after pruning, reorganization across pruned history, operator-build provenance, historical-chain and real-datadir behavior, sustained public-network operation, and mainnet evidence remain open |
| **2 · Mainnet coexistence** | Run the candidate on the existing Ergon mainnet beside legacy nodes, with no consensus change. | `blocked` by compatibility and real-chain evidence |
| **3 · Optional observation** | Prove indexing can be absent, disabled, or explicitly enabled without becoming authoritative. | `blocked` by the preceding node evidence |
| **4 · Experimental fork testnet** | Test separately reviewed consensus changes—including a possible native-assets model—on a governed testnet. | `blocked` by coexistence and an accepted specification |
| **5 · Future mainnet decision** | Consider a mainnet fork only after prolonged testnet operation, independent reproduction, release evidence, and separate community governance. | `blocked`; no activation is proposed |

Native-assets, reward, scaling, and difficulty research may proceed in parallel,
but research does not silently become node behavior. A change enters this path
only through a signed commit and a public engineering record describing its
scope, tests, evidence, limits, and counterevidence.

[Follow the generated roadmap →](../docs/roadmap.md) ·
[Inspect the engineering ledger →](../docs/engineering/changes/README.md)

## Start building

| Task | Entry point |
| --- | --- |
| Installation overview | [`INSTALL.md`](../INSTALL.md) |
| Unix and macOS builds | [`doc/build-unix.md`](../doc/build-unix.md) and [`doc/build-osx.md`](../doc/build-osx.md) |
| Windows build | [`doc/build-windows.md`](../doc/build-windows.md) |
| Developer notes | [`doc/developer-notes.md`](../doc/developer-notes.md) |
| Unit and functional tests | [`doc/unit-tests.md`](../doc/unit-tests.md) and [`doc/functional-tests.md`](../doc/functional-tests.md) |
| Engineering change evidence | [`docs/engineering/changes/README.md`](../docs/engineering/changes/README.md) |
| Contribution boundary | [`CONTRIBUTING.md`](../CONTRIBUTING.md) |
| Security reports | [`SECURITY.md`](../SECURITY.md) |

## Repository map

| Area | Purpose |
| --- | --- |
| `src/` | Node, consensus, networking, policy, RPC, wallet, and unit-test source |
| `test/` | Functional, lint, fuzz, and utility tests |
| `depends/` | Reproducible dependency build framework inherited from the public baseline |
| `doc/` | Upstream-shaped technical documentation |
| `cockpit/` | Canonical delivery, knowledge, boundary, and research state |
| `docs/` | Ergon Lab architecture, roadmap, provenance, and visual material |
| `tools/` | Public validation and cockpit tooling |

The upstream-shaped core directories are intentionally not reorganized for
presentation. This keeps diffs, audits, and future public-baseline comparisons
legible.

## Current engineering gates

| Gate | Delivery | Knowledge |
| --- | --- | --- |
| Verify the public baseline snapshot | `verified` | `Observed` |
| Launch publication gates and cockpit | `verified` | `Explainer` |
| Establish engineering change evidence | `verified` | `Explainer` |
| Validate legacy compatibility | `active` | `Observed` |
| Prove mainnet coexistence with the legacy node | `blocked` | `Open Question` |
| Verify optional indexing | `blocked` | `Open Question` |
| Validate the experimental fork on testnet | `blocked` | `Open Question` |
| Assess a future mainnet fork | `blocked` | `Open Question` |
| Reproduce the zero-subsidy fixture repair | `active` | `Observed` |

These are evidence-scoped labels, not release promises. Consult the
[`canonical cockpit`](../cockpit/cockpit.yaml) and
[`generated roadmap`](../docs/roadmap.md) for the governed state.

## Public-source rule

Only public inputs and independently authored, properly licensed material may
enter this repository. Never copy, summarize, translate, or paraphrase private
material to bypass the publication boundary. Every admitted change needs
reviewed scope, provenance, license, tests, and portable evidence appropriate
to its claim.

[Read the publication policy →](../PUBLICATION_POLICY.md) ·
[Open engineering change evidence →](../docs/engineering/changes/README.md) ·
[Read the consensus boundary →](../docs/architecture/consensus-boundary.md)

---

[Return to Ergon Lab →](../README.md)

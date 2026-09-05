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
| **1 · Public baseline** | Preserve the exact Bitcoin Static v24.0.5 tree as the signed public root. | `verified` |
| **2 · Bounded legacy compatibility** | Falsify differences from the legacy node across synchronization, cross-mining, restart, full reindex, chainstate reindex, physical pruning, and protected reorganization. | `verified` / `Reproduced` through the accepted [ERGON-CHANGE-0015](../docs/engineering/changes/ergon-change-0015.json) public matrix; consensus is unchanged |
| **3 · Optional indexing** | Prove the standalone node behaves the same with indexing compiled out versus compiled in but disabled before any explicit runtime opt-in is considered. | `active` / `Open Question`; indexing remains default-OFF and no implementation bytes are accepted by this transition |
| **4 · Mainnet-compatible deployment** | Run the candidate on the existing Ergon mainnet beside legacy nodes, with no consensus change. | `blocked` / `Open Question`; H288 is an accepted bounded `Observed` result, while H250000 ended inconclusive on candidate timeout with no artifact or divergence claim |
| **5 · Separate research** | Develop native-assets, reward, scaling, and DAA specifications, corpus work, and simulations without silently changing node behavior. | `research`; independently labeled and governed |
| **6 · Activatable testnet fork** | Place separately reviewed consensus changes behind a deterministic, dormant-by-default testnet activation boundary. | `blocked`; no activation packet is accepted |
| **7 · Testnet validation** | Exercise activation, rollback, mixed-peer, and reorganization boundaries over a declared evidence window. | `blocked` by the preceding gates |
| **8 · Mainnet preparation** | Require prolonged testnet evidence, independent reproduction, release evidence, and explicit governance without activating anything. | `blocked` by testnet evidence |
| **9 · Distinct future activation** | Consider a mainnet fork only through a final decision separate from preparation and testnet success. | `blocked`; no mainnet activation is proposed |

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
| Validate legacy compatibility | `verified` | `Reproduced` |
| Verify optional indexing | `active` | `Open Question` |
| Prove mainnet coexistence with the legacy node | `blocked` | `Open Question` |
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

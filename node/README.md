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

## Start building

| Task | Entry point |
| --- | --- |
| Installation overview | [`INSTALL.md`](../INSTALL.md) |
| Unix and macOS builds | [`doc/build-unix.md`](../doc/build-unix.md) and [`doc/build-osx.md`](../doc/build-osx.md) |
| Windows build | [`doc/build-windows.md`](../doc/build-windows.md) |
| Developer notes | [`doc/developer-notes.md`](../doc/developer-notes.md) |
| Unit and functional tests | [`doc/unit-tests.md`](../doc/unit-tests.md) and [`doc/functional-tests.md`](../doc/functional-tests.md) |
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
| Verify the public baseline snapshot | `active` | `Observed` |
| Launch publication gates and cockpit | `active` | `Explainer` |
| Review the zero-subsidy fixture export | `active` | `Open Question` |

These are evidence-scoped labels, not release promises. Consult the
[`canonical cockpit`](../cockpit/cockpit.yaml) and
[`generated roadmap`](../docs/roadmap.md) for the governed state.

## Clean-room rule

Only public inputs and independently authored, properly licensed material may
enter this repository. Never copy, summarize, translate, or paraphrase private
material to bypass the publication boundary. Every admitted change needs
allowlisted scope, provenance, license, tests, and portable evidence appropriate
to its claim.

[Read the publication policy →](../PUBLICATION_POLICY.md) ·
[Read the consensus boundary →](../docs/architecture/consensus-boundary.md)

---

[Return to Ergon Lab →](../README.md)

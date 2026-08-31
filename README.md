<!-- SPDX-License-Identifier: MIT -->

# Ergon Lab

Community research and engineering hub for Ergon.

Ergon Lab develops a standalone open-source node and maintains a public cockpit
for building, operating, learning about, researching, and observing the Ergon
network.

This repository is clean-room public work. Its root baseline is the public
Bitcoin Static v24.0.5 snapshot identified by:

- source commit: `2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b`
- source Git tree: `8a74bb952c2137156214b9fe5888c494bd77aeca`

The source commit and tree are both required identifiers. A version label alone
is not sufficient provenance. Changes after that snapshot are admitted only
through the allowlisted publication process in
[`PUBLICATION_POLICY.md`](PUBLICATION_POLICY.md).

## Standalone consensus boundary

The node is the authority for its own consensus validation, chain selection,
mempool policy, and persistent chain state. It must remain correct and usable
without an indexer.

Chronik, when present, is optional observe-and-index infrastructure. It may
derive and serve indexed views of node-accepted data. It must not decide or
override consensus validity, chain selection, activation, or mempool admission,
and it must not become a prerequisite for node correctness. See
[`docs/architecture/consensus-boundary.md`](docs/architecture/consensus-boundary.md).

## Public cockpit

The cockpit separates four workstreams:

- **Build / Operate** — source, builds, releases, configuration, and operation;
- **Learn** — public explainers and concepts;
- **Research** — hypotheses, simulations, analyses, and reproduction packs;
- **Observatory** — sourced network and ecosystem observations.

Every cockpit item carries two independent labels:

| Axis | Allowed values |
| --- | --- |
| Delivery state | `verified`, `active`, `blocked`, `planned`, `research` |
| Knowledge status | `Explainer`, `Hypothesis`, `Simulation`, `Observed`, `Reproduced`, `Open Question` |

A delivery state describes progress through a publication or implementation
gate. A knowledge status describes what kind of knowledge is being presented.
Neither axis upgrades the other. In particular, `Observed` is not
`Reproduced`, `Simulation` is not observation, and `verified` delivery does not
turn a hypothesis into a fact.

Research will progressively cover protocol-native assets, proportional rewards,
cyphercash, supply/emission, descriptive price context, hashrate responsiveness
and elasticity, difficulty adjustment, block size and propagation, security,
and fee markets. Price material is descriptive context only: no forecasts,
investment recommendations, or financial advice.

Research and observatory entries must disclose sources, methods, public data,
units, assumptions, uncertainty, limitations, counter-evidence, and a portable
reproduction pack. A result cannot use `Reproduced` until a public-input
reproduction has been independently completed and recorded.

## Project status

The machine-readable cockpit is the canonical source for public gate states.
The English visual roadmap is generated from the publication-boundary and gate
projection; it is not hand-edited and is regenerated only when a gate or
boundary in that projection changes.

No roadmap item, issue, or research entry is a release promise. Statuses are
evidence-scoped claims and may move backward when counter-evidence appears.

## Contributing and security

Read [`CONTRIBUTING.md`](CONTRIBUTING.md) before proposing a change. Contributors
must use only public inputs or independently authored material they have the
right to license. Do not copy, summarize, translate, or paraphrase private
material for publication.

For suspected vulnerabilities, follow [`SECURITY.md`](SECURITY.md) and do not
open a public issue with exploit details, credentials, operator data, or other
sensitive information.

## License

The public baseline is distributed under the MIT terms in [`COPYING`](COPYING).
New repository material must declare an SPDX-compatible license and preserve
all applicable upstream notices. Data and reproduction inputs may have separate
licenses recorded alongside their provenance.

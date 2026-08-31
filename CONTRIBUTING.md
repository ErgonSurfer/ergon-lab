<!-- SPDX-License-Identifier: MIT -->

# Contributing to Ergon Lab

Thank you for helping build a standalone public node and an evidence-driven
public cockpit. Contributions are reviewed for technical merit and for whether
they are safe to publish.

## Start from public material

You may contribute:

- work derived from identified public sources under compatible terms; or
- work you authored independently and have the right to license for this
  repository.

You may not contribute private source, history, architecture, paths, logs,
operator information, identities, endpoints, credentials, datasets, fixtures,
corpora, binaries, or donor artifacts. Do not evade this rule by summarizing,
translating, rewriting, or paraphrasing private material. If origin, rights, or
publication safety is uncertain, stop and mark the proposal `blocked`.

The normative baseline is Bitcoin Static v24.0.5 at commit
`2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b`, Git tree
`8a74bb952c2137156214b9fe5888c494bd77aeca`.

## Before opening a pull request

1. Open or reference an issue that states the public purpose and scope.
2. Identify every source and its license, or attest independent authorship.
3. Classify affected surfaces: consensus, validation, P2P, storage, RPC,
   wallet, indexing, UI, build/release, tests, documentation, and data.
4. Describe the standalone/Chronik boundary. Chronik is optional observe/index
   infrastructure and must not become consensus or chain-selection authority.
5. Add focused tests and provide fresh-environment reproduction commands with
   expected results.
6. Record limitations, uncertainty, counter-evidence, and known inherited debt.
7. Scan the exact proposed bytes and generated outputs for secrets, private
   identifiers, private paths, endpoints, and host-bound metadata.
8. Update a machine-readable allowlisted-change record when the publication
   policy requires one.

Do not include generated build products, caches, local configuration, wallets,
chain data, core dumps, coverage databases, or test reports containing local
metadata.

## Pull request contents

Keep each pull request reviewable and single-purpose. Complete the pull request
template, including:

- public purpose and exact scope;
- provenance, authorship, and SPDX license declarations;
- affected-surface and consensus-boundary analysis;
- commands, tool versions, dependency-lock status, and expected results;
- checksums for portable inputs, reports, outputs, or reproduction packs;
- limitations, uncertainty, and counter-evidence;
- confirmation that the patch is relative to a public repository state and
  contains no private history or metadata.

Reviewers may narrow an allowlist or require regeneration from public inputs.
Passing tests does not by itself authorize publication.

## Code and test expectations

- Preserve deterministic behavior where the affected surface permits it.
- Prefer the smallest change that fully addresses the documented public
  purpose, without reducing the intended functionality to fit existing tests.
- Test failure paths and boundary conditions, not only success paths.
- Keep production and test-only reachability explicit.
- State unsupported platforms or toolchains rather than implying coverage.
- Never represent a component observation as system, network, historical, or
  operational parity.

Consensus, validation, P2P, storage, release, and provenance changes require
the strongest review and evidence. Documentation claims must be sourced and
bounded just as code claims are.

## Research and observatory contributions

Use the research issue form before proposing a research result. Every entry
must include:

- sources and source licenses;
- methods and falsifiable questions where applicable;
- public data identifiers and checksums;
- units, definitions, and transformations;
- assumptions and environment details;
- uncertainty and error treatment;
- limitations and scope boundaries;
- counter-evidence and unsuccessful checks;
- a portable reproduction pack with exact commands and expected outputs.

Choose the knowledge status literally: `Explainer`, `Hypothesis`, `Simulation`,
`Observed`, `Reproduced`, or `Open Question`. Simulation is not observation,
and observation is not independent reproduction. Descriptive price context
must not include forecasts, financial advice, or investment recommendations.

## Review outcomes

A publication review may return:

- **ACCEPT** — the exact proposed scope may proceed through normal review;
- **NARROW** — only the stated subset may proceed, with requested evidence;
- **REJECT** — the material is outside the public boundary or cannot be proven
  publishable.

An acceptance applies only to the reviewed bytes and evidence. Later changes
require a new review.

## Reporting vulnerabilities

Do not use contribution issues or pull requests for undisclosed security
vulnerabilities. Follow [`SECURITY.md`](SECURITY.md).

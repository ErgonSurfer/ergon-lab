<!-- SPDX-License-Identifier: MIT -->

<p align="center">
  <a href="README.md">Discover map</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="glossary.md">Glossary</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../insights/README.md">Insights</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../node/README.md">Node</a>
</p>

<p align="center">
  <img src="../docs/assets/brand/discover/glossary-and-sources.jpg" width="100%" alt="A precise material atlas of Ergon concepts and sources">
</p>

# Source atlas

> **Purpose:** show which source can support which kind of statement
> **Last source review:** 1 September 2026
> **Rule:** a source can establish its own claim or implementation; it cannot
> silently establish a stronger economic or empirical conclusion

Discover Ergon is written from public sources. This atlas makes their roles and
limitations explicit without interrupting every paragraph with methodology.

## Source hierarchy

| Priority | Source class | Appropriate use | Does not establish |
| ---: | --- | --- | --- |
| 1 | Exact accepted consensus source and tests | Implemented protocol behavior | Real-world economic outcome |
| 2 | Signed releases and release notes | Version history and declared changes | Independent verification that a fix works |
| 3 | Original technical papers and specifications | Authored model, assumptions, and design | That a model matches the live network |
| 4 | Project website and maintainer statements | Project intent, terminology, and attributed history | Consensus behavior when source differs |
| 5 | Community essays | Arguments, hypotheses, and intellectual context | Unattributed protocol fact |
| 6 | External prior art | Comparative design and known trade-offs | Authority over Ergon activation |
| 7 | Explorers, pools, and market data | Time-bound observations with a method | Timeless or exact network truth by themselves |

## A. Consensus and implementation

### Bitcoin Static v24.0.5

- **Repository:** [Ergon-moe/Bitcoin-Static](https://github.com/Ergon-moe/Bitcoin-Static)
- **Commit:** [`2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b`](https://github.com/Ergon-moe/Bitcoin-Static/commit/2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b)
- **Git tree:** `8a74bb952c2137156214b9fe5888c494bd77aeca`
- **License:** MIT, with inherited notices
- **Role:** authoritative public baseline for the new standalone node

Frequently cited anchors:

| Path | What it supports |
| --- | --- |
| [`src/chainparams.cpp`](https://github.com/Ergon-moe/Bitcoin-Static/blob/2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b/src/chainparams.cpp) | Genesis, network identity, target spacing, calibration, address prefix, activation parameters |
| [`src/validation.cpp`](https://github.com/Ergon-moe/Bitcoin-Static/blob/2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b/src/validation.cpp) | Subsidy calculation and block validation |
| [`src/pow.cpp`](https://github.com/Ergon-moe/Bitcoin-Static/blob/2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b/src/pow.cpp) | Difficulty and proof-of-work calculations |
| [`src/amount.h`](https://github.com/Ergon-moe/Bitcoin-Static/blob/2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b/src/amount.h) | XRG/fixoshi accounting units |
| [`doc/release-notes.md`](https://github.com/Ergon-moe/Bitcoin-Static/blob/2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b/doc/release-notes.md) | v24.0.5 DAA and correction history |

Source inspection can establish what the software says. Portable builds and
tests are still needed to establish how a particular binary behaves.

## B. Foundational electronic cash

### Bitcoin: A Peer-to-Peer Electronic Cash System

- **Author:** Satoshi Nakamoto
- **Date:** 31 October 2008
- **Source:** [bitcoin.org/bitcoin.pdf](https://bitcoin.org/bitcoin.pdf)
- **Role:** primary description of peer-to-peer electronic cash, signatures,
  proof-of-work ordering, incentives, and probabilistic confirmation

Ergon inherits major architectural ideas from this work. It does not inherit
Bitcoin's ledger or fixed subsidy unchanged.

## C. Ergon's monetary model

### Proportional block reward as a price stabilization mechanism

- **Author:** Karol Trzeszczkowski (Licho)
- **Date:** 17 April 2021
- **Source:** [ergon.moe/prop-reward.pdf](https://ergon.moe/prop-reward.pdf)
- **Role:** primary authored model for proportional reward, miner response,
  supply, demand, and hardware-efficiency correction

The paper discloses simplifying assumptions and presents numerical model
behavior. Its equations can support a reproduced simulation after code and
inputs are made portable. They do not alone establish observed purchasing-power
stability.

## D. Project position and terminology

### Ergon public website

- **Source:** [ergon.moe](https://ergon.moe/en/)
- **Role:** project purpose, FAQ, ecosystem pointers, declared no-premine and
  no-developer-fund position, and current public terminology
- **Limitation:** website language such as “stable” and “fair” expresses intent
  or interpretation unless supported by a declared empirical study

### Cyphercash terminology

- **Source:** [Cyphercash Not Cryptocurrency](https://ergon.moe/blog/Cyphercash%20Not%20Cryptocurrency_Licho_2024-08-28.html)
- **Role:** origin and motivation of the community term
- **Limitation:** terminology does not alter protocol rules or create privacy
  guarantees

## E. Attributed Ergon essays

These sources are useful arguments and research prompts. Discover pages cite
them by title and author rather than absorbing their conclusions as fact.

| Essay | Useful for | Required caution |
| --- | --- | --- |
| [Miner's Guide](https://ergon.moe/blog/Miner%27s%20Guide_Licho_2023-08-19.html) | Plain-language proportional mining and named network parameters | Worked examples are dated observations |
| [Escape Velocity](https://ergon.moe/blog/Escape%20Velocity_Licho_2023-08-17.html) | Conditional future-supply model | Scenario output is not a hard cap or forecast |
| [From Stability to Liquidity](https://ergon.moe/blog/From%20Stability%20To%20Liquidity_Licho_2023-08-18.html) | Liquidity thesis and motivation | Normative/economic argument |
| [Ergon Is Mutual Credit](https://ergon.moe/blog/Ergon%20Is%20Mutual%20Credit_Licho_2023-09-25.html) | Political-economy interpretation of work and ledger money | Interpretation, not consensus specification |
| [Ergon Supply Shrinking](https://ergon.moe/blog/Ergon%20Supply%20Shrinking_Licho_2023-10-17.html) | Candidate mechanisms affecting accessible supply | Lost keys and effective supply are difficult to observe |
| [On Security](https://ergon.moe/blog/On%20security_Licho_2024-02-11.html) | Attack model and proportional-reward security hypotheses | Parametric argument requiring independent review |

## F. Transaction and native-asset prior art

### Bitcoin Cash transaction specification

- **Source:** [Transaction specification](https://upgradespecs.bitcoincashnode.org/transaction/)
- **Role:** public description of the inherited transaction structure
- **Caution:** the page identifies itself as an older specification; accepted
  Ergon source remains authoritative where behavior differs

### Simple Ledger Protocol

- **Source:** [SLP Token Type 1 specification](https://github.com/simpleledger/slp-specifications/blob/master/slp-token-type-1.md)
- **Role:** application-layer token prior art using `OP_RETURN` metadata
- **Boundary:** SLP validation is not Ergon base consensus

### CashTokens

- **Source:** [CashTokens CHIP](https://github.com/cashtokens/cashtokens)
- **Documentation:** [cashtokens.org](https://cashtokens.org/docs/intro/)
- **Role:** consensus-native fungible and non-fungible token prior art,
  including supply, capability, script, wallet, and activation considerations
- **Boundary:** Bitcoin Cash activation and implementation do not activate or
  govern Ergon

ALP material will be added only after an immutable public specification and its
license are bound in the research corpus.

## G. Live network and market sources

Explorers, pool dashboards, and market services change over time. They are not
used on Discover pages to publish a current number without an Observatory
record.

An eligible observation binds:

- chain identity and height;
- retrieval time in UTC;
- endpoint and response digest, or independent node query;
- units and transformations;
- software and method revision;
- uncertainty and missing-data treatment;
- privacy review and redistribution license.

Market data is descriptive historical context only. Ergon Lab does not publish
financial forecasts or investment recommendations.

## Corrections and archival discipline

If a linked source changes, a future evidence record should bind a digest or an
archived public revision before relying on exact wording or data. Corrections to
Discover pages should state what changed and why in Git history.

No source is made stronger by repetition. When code, a paper, a website, and an
essay disagree, the page should expose the disagreement and narrow the claim.

---

[← Glossary](glossary.md) · [Return to Discover&nbsp;→](README.md)

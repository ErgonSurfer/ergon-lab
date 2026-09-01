<!-- SPDX-License-Identifier: MIT -->

<p align="center">
  <a href="README.md">Discover map</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../insights/README.md">Insights</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../network/README.md">Network</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../node/README.md">Node</a>
</p>

<p align="center">
  <img src="../docs/assets/brand/discover/origins-and-lineage.jpg" width="100%" alt="Archival layers lead to a distinct illuminated genesis object">
</p>

# Origins & lineage

> **Knowledge status:** `Explainer`
> **Essential distinction:** Ergon inherits open-source software and protocol
> ideas, but its currency begins on a separate chain with a separate genesis

Open-source monetary software carries several histories at once. Code descends
through earlier projects. Protocol rules are adopted, changed, or rejected.
The ledger itself begins at a particular genesis. Keeping those histories
separate makes Ergon easier to understand.

## The lineage in one view

```text
Bitcoin's peer-to-peer electronic cash design
                    ↓
Bitcoin Core implementation lineage
                    ↓
Bitcoin Cash / Bitcoin ABC / Bitcoin Cash Node lineage
                    ↓
Bitcoin Static: Ergon rules + a distinct genesis
                    ↓
Ergon Lab: standalone node engineering from v24.0.5
```

The arrows describe source and design descent. They do not mean that Ergon
holders received balances copied from Bitcoin or Bitcoin Cash.

## 2008 — the electronic-cash foundation

The Bitcoin paper describes direct electronic payments using signatures, a
peer-to-peer network, and a hash-based proof-of-work history. The core problem
is double-spending without a trusted ledger operator.

Ergon retains that architecture: UTXO transactions, proof-of-work blocks,
independent validation, and selection by accumulated work.

## The Bitcoin Cash software branch

Bitcoin Cash software preserved and evolved a transaction-oriented branch of
the Bitcoin codebase. Bitcoin Static's public README identifies its software as
a descendant of Bitcoin Cash Node, Bitcoin ABC, and Bitcoin Core.

That inheritance supplies a large body of networking, validation, wallet,
testing, and build code. It also creates a maintenance responsibility: inherited
behavior must be distinguished from intentional Ergon rules, and legacy defects
must remain visible until repaired with evidence.

## 2020 — a distinct Ergon genesis

Bitcoin Static's mainnet parameters create the genesis block with timestamp
`1607003022`, corresponding to **3 December 2020 at 13:43:42 UTC**. The source
binds its hash and merkle root and assigns a zero-valued genesis reward.

The project states that Ergon began as a new blockchain rather than a split of
an existing ledger, with no premine and no developer fund.

This produces two precise statements:

- **software descendant:** yes;
- **distribution fork of an earlier chain:** no.

## 2021 — the monetary proposition is written down

The proportional-reward paper, dated 17 April 2021, presents the economic model
behind making block reward a function of mining difficulty. It discusses
hashrate response, production cost, supply, demand, and a correction for mining
efficiency.

The paper is an important project source, but a model is not identical to the
consensus implementation. Ergon Lab will cite the paper for its propositions
and the source tree for exact rules.

## 2022 — DAA repair and the current public baseline

Bitcoin Static v24.0.5 records a mid-2022 incident in which a difficulty
adjustment exploit drove difficulty to minimum and reward to zero. The release
introduced a hard-fork DAA repair and corrected the hardware-efficiency
parameter from an unintended shorter half-life to the intended approximate
2.3-year value.

This release is the authoritative starting point for the new standalone node:

| Identity | Value |
| --- | --- |
| Tag | `v24.0.5` |
| Commit | `2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b` |
| Git tree | `8a74bb952c2137156214b9fe5888c494bd77aeca` |

Both the commit and tree are recorded because a name alone is not a complete
source identity.

## 2026 — a public standalone-node path

Ergon Lab begins from that exact public tree and makes subsequent work visible
as ordered engineering changes. The intended sequence is:

1. reproduce the baseline and inherited behavior;
2. prove honest compatibility with the existing Ergon network;
3. add optional observation and indexing without moving consensus authority;
4. operate the new software on mainnet under legacy-compatible rules;
5. research and simulate candidate protocol changes separately;
6. activate candidate rules on a dedicated testnet only after prerequisites;
7. consider a future mainnet fork through a distinct governed activation.

Running new software on mainnet is not itself a consensus fork. A
legacy-compatible node can coexist with existing nodes while enforcing the same
rules. New consensus behavior requires a later, explicit activation.

## What provenance means here

Open source permits reuse under license; rigorous engineering still records
where every retained part came from.

Ergon Lab distinguishes:

- exact bytes inherited from the public Bitcoin Static baseline;
- independently authored Ergon changes;
- deliberately selected public upstream or prior-art material;
- generated build inputs with reproducible dependency bindings;
- research references that inform design but do not enter production code.

Private histories, operator data, credentials, local machine artifacts, and
unreviewed implementation material are not part of the public lineage.

## History stays revisable

This page records claims supported by currently reviewed public sources. New
primary material may refine dates, authorship, or interpretation. Corrections
should preserve the earlier record in Git rather than silently replacing it.

### Primary trail

- [Bitcoin paper](https://bitcoin.org/bitcoin.pdf)
- [Bitcoin Static public repository](https://github.com/Ergon-moe/Bitcoin-Static)
- [Bitcoin Static v24.0.5 release](https://github.com/Ergon-moe/Bitcoin-Static/releases/tag/v24.0.5)
- [Proportional reward paper](https://ergon.moe/prop-reward.pdf)
- Repository source anchors: `src/chainparams.cpp` and `doc/release-notes.md`
- [Public source provenance policy](../PUBLICATION_POLICY.md)

---

[← Cyphercash](cyphercash.md) ·
[Next: Native assets&nbsp;→](native-assets.md)

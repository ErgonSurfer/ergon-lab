<!-- SPDX-License-Identifier: MIT -->

<p align="center">
  <a href="README.md">Discover map</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../insights/README.md">Insights</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../network/README.md">Network</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../node/README.md">Node</a>
</p>

<p align="center">
  <img src="../docs/assets/brand/discover/what-is-ergon.jpg" width="100%" alt="Two people connected across an open Ergon landscape">
</p>

# What is Ergon?

> **Knowledge status:** `Explainer`
> **Reading time:** about 5 minutes
> **Claim boundary:** public Bitcoin Static v24.0.5 source and attributed
> project material; no claim of price stability or future protocol activation

Ergon is a peer-to-peer proof-of-work network with a native unit, XRG. It is
designed for direct digital payments: one person can create and sign a
transaction, broadcast it to the network, and have independent full nodes
verify the same rules without asking a bank or platform to authorize it.

The project often calls this **cyphercash**: cash for the internet, protected by
cryptography and carried by a peer-to-peer network. The term emphasizes use—pay
and get paid—rather than membership in an investment category.

## Three things called Ergon

The name is used for related but distinct things:

| Layer | Meaning |
| --- | --- |
| **Network** | The peers, miners, blocks, transactions, and shared validation rules |
| **Currency** | XRG, the native unit recorded by the network |
| **Project** | The software, research, tools, and community that maintain and study the system |

The legacy full-node software is named **Bitcoin Static**. Ergon Lab is building
a new standalone open-source node from the exact public Bitcoin Static v24.0.5
source baseline while preserving compatibility before proposing any new
consensus rules.

## Familiar foundations

Ergon inherits the central architecture of Bitcoin-style electronic cash:

- users control keys that authorize spending;
- value lives in unspent transaction outputs, or UTXOs;
- transactions consume existing outputs and create new ones;
- miners gather transactions into proof-of-work blocks;
- full nodes independently validate transactions, blocks, and chain history;
- the valid chain with the most accumulated proof of work is authoritative;
- confirmations are probabilistic: reversing a transaction becomes harder as
  more work accumulates above it.

On the public main network, Bitcoin Static defines SHA-256d proof of work, a
ten-minute target block interval, the `ergon:` CashAddr prefix, and a smallest
accounting unit called the **fixoshi**. One XRG contains 100,000,000 fixoshi.

## The defining change

Many proof-of-work currencies set a block subsidy according to height or time:
the schedule decides how many units a miner receives, largely independently of
the work represented by that block's difficulty.

Ergon instead derives its subsidy from proof of work. In the current source,
the node converts the block's compact difficulty target into expected work,
applies a network calibration and a gradual time correction, and pays the
result in fixoshi.

Plainly stated:

> **More expected work behind a block produces a larger subsidy; less expected
> work produces a smaller one.**

This is **proportional reward**. It ties issuance to the work miners are
expected to perform rather than to a fixed number of coins per block.

## What Ergon is not

Ergon is not:

- a token issued on another chain;
- a company account balance;
- a fiat-backed or algorithmically pegged stablecoin;
- a promise that one XRG will trade at a particular price;
- a system in which an indexer or explorer decides transaction validity;
- a completed answer to every monetary or network-design question.

The official project describes Ergon as an experiment in stable peer-to-peer
cash. The mechanism and the ambition are real; the economic outcome remains a
claim to test against models, history, and network evidence.

## A separate beginning

Ergon is a descendant of Bitcoin Cash software, but it did not begin by
allocating an existing chain's balances to their holders. Its mainnet has a
distinct genesis block dated 3 December 2020. The public source creates that
genesis with a zero-valued reward, and the project states that there was no
premine or developer fund.

That distinction matters: **software lineage is not monetary lineage**.

## One sentence to keep

Ergon is an attempt to make peer-to-peer proof-of-work cash whose issuance
responds to the work securing it, while leaving validation in the hands of
independent nodes.

### Primary trail

- [Ergon project overview](https://ergon.moe/en/)
- [Bitcoin Static public source](https://github.com/Ergon-moe/Bitcoin-Static)
- [Proportional reward paper](https://ergon.moe/prop-reward.pdf)
- [Bitcoin: A Peer-to-Peer Electronic Cash System](https://bitcoin.org/bitcoin.pdf)
- Repository source anchors: `src/chainparams.cpp`, `src/validation.cpp`,
  `src/amount.h`, and `src/pow.cpp`

---

[Next: Why Ergon?&nbsp;→](why-ergon.md) · [Discover map](README.md)

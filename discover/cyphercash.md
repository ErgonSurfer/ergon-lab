<!-- SPDX-License-Identifier: MIT -->

<p align="center">
  <a href="README.md">Discover map</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../insights/README.md">Insights</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../network/README.md">Network</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../node/README.md">Node</a>
</p>

<p align="center">
  <img src="../docs/assets/brand/discover/cyphercash.jpg" width="100%" alt="A maker and a customer connected directly across distance">
</p>

# Cyphercash

> **Knowledge status:** `Explainer` of an attributed community term
> **Meaning here:** peer-to-peer cash for paying and getting paid over the
> internet—not a privacy guarantee, price peg, or investment classification

In August 2024, the maintainer of Ergon's public website proposed using
**cyphercash** instead of “cryptocurrency” when speaking about Ergon. The
community term is deliberately cultural. It says what the system is for before
placing it inside an industry category.

## Cash, carried by cryptography

“Cypher” points to cryptography:

- digital signatures authorize spending;
- hashes identify transactions and blocks;
- proof of work orders history and makes revision costly;
- open verification allows participants to check rules independently.

“Cash” points to a social function:

- a buyer can pay a seller directly;
- receiving does not require permission from a central issuer;
- funds can be held under the user's own keys;
- the same native unit moves across borders and applications;
- settlement does not depend on a platform maintaining an internal balance.

The result is not physical cash reproduced perfectly in software. It is an
attempt to preserve some of cash's directness and bearer-like control over a
communications network.

## Pay and get paid

The simplest Ergon story is not “buy and wait.” It is:

```text
make something
      ↓
name a price
      ↓
receive XRG directly
      ↓
use XRG in the next exchange
```

This orientation matters. A payment system becomes useful through a network of
people willing to price work, goods, and services in its unit—not through a
chart alone.

The Ergon community connects that idea to proportional reward. Miners are paid
according to represented work, while merchants and workers earn units by
offering value to others. Whether this produces broad and durable liquidity is
an economic question, but the intended direction is clear: circulation over
passive appreciation.

## What cyphercash does not imply

### Not anonymous by default

Ergon uses a public UTXO ledger. Addresses are pseudonymous, but transaction
amounts and graph relationships are visible. Network observers and external
information can reduce privacy. “Cypher” should never be read as a promise of
untraceability.

### Not reversible consumer credit

A valid confirmed transfer has no built-in chargeback desk. That can reduce
intermediation, but it also changes risk. Escrow, reputation, contracts,
receipts, and dispute processes remain application and social-layer problems.

### Not a stablecoin

Ergon is not redeemable for a fixed quantity of fiat and does not use reserves
or an oracle peg. Proportional reward is intended to create a different supply
response. Market prices can still change materially.

### Not free of intermediaries by magic

Users can choose custodial wallets, exchanges, payment processors, or hosted
services. Those services may be useful, but their promises are separate from
the chain. Self-custody and independent verification remain available only if
the supporting software is usable and maintained.

### Not an investment recommendation

The term rejects the idea that appreciation is the product's primary purpose.
Ergon Lab provides technical and historical context, never forecasts or
promises of future return.

## The practical standard

Calling Ergon cyphercash creates a demanding usability test. A payment medium
should be:

- understandable enough to price ordinary exchange;
- reliable enough to accept without ceremony;
- inexpensive enough for the payment size;
- recoverable and back-up friendly without surrendering control;
- observable enough to diagnose failures;
- private enough for the context, with limitations made visible;
- supported by independently verifiable node software.

Those properties cannot be declared into existence. They require wallets,
merchant tools, node reliability, documentation, network liquidity, and real
use.

## A term with a boundary

Ergon Lab will use **cyphercash** as an attributed community description, not
as a new technical primitive. Protocol documentation will continue to name the
actual mechanisms—UTXO, scripts, signatures, proof of work, difficulty,
subsidy, and peer-to-peer relay.

That keeps the word useful: it describes the human destination while the source
and evidence describe how far the system has traveled.

### Primary and attributed reading

- [Cyphercash Not Cryptocurrency](https://ergon.moe/blog/Cyphercash%20Not%20Cryptocurrency_Licho_2024-08-28.html)
  — origin and rationale of the community term
- [Ergon project overview](https://ergon.moe/en/)
- [Bitcoin: A Peer-to-Peer Electronic Cash System](https://bitcoin.org/bitcoin.pdf)
- [From Stability to Liquidity](https://ergon.moe/blog/From%20Stability%20To%20Liquidity_Licho_2023-08-18.html)
  — attributed community argument

---

[← Supply and emission](supply-and-emission.md) ·
[Next: Origins and lineage&nbsp;→](origins-and-lineage.md)

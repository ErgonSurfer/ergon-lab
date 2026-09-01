<!-- SPDX-License-Identifier: MIT -->

<p align="center">
  <a href="README.md">Discover map</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../insights/README.md">Insights</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../network/README.md">Network</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../node/README.md">Node</a>
</p>

<p align="center">
  <img src="../docs/assets/brand/discover/supply-and-emission.jpg" width="100%" alt="An open topographic landscape constrained by paths of effort">
</p>

# Supply without a hard cap

> **Knowledge status:** `Explainer`
> **No live value on this page:** current supply belongs in the Network
> Observatory with a source, method, height, timestamp, unit, and uncertainty

Ergon does not encode a single maximum number of XRG that must exist. It also
does not permit arbitrary creation. New units are constrained by valid proof of
work, consensus calibration, the time correction, and integer accounting.

Understanding that design requires separating quantities that are often called
“supply” as if they were interchangeable.

## Four different supply questions

| Quantity | What it asks | Can the chain answer it? |
| --- | --- | --- |
| **Cumulative issuance** | How much block subsidy has consensus created? | Yes, by replaying accepted blocks |
| **Provably spendable set** | How much value remains in unspent outputs? | Yes, with explicit treatment of provably unspendable scripts |
| **Economically accessible supply** | How much can owners actually move? | Not exactly; lost keys are usually indistinguishable from dormant holdings |
| **Future issuance** | How much will miners create later? | Not as one fixed number; it depends on future represented work and correction |

A responsible “existing supply” metric must declare which of these it means.

## Emission is produced, not scheduled

In a fixed-schedule system, a future height can often be mapped directly to a
block subsidy before anyone knows how much hashrate will exist.

In Ergon, height alone is insufficient. A future subsidy also depends on the
difficulty target that will apply to that block. Future difficulty depends on
future mining conditions. This makes emission **emergent** rather than fully
precomputed.

```text
future work is unknown
        ↓
future difficulty is unknown
        ↓
future block subsidy is unknown
        ↓
there is no single protocol hard cap to quote
```

This does not mean issuance is discretionary. A node calculates the allowed
subsidy deterministically once the relevant chain state and block target are
known.

## What constrains creation

New XRG faces several boundaries:

1. **Proof-of-work cost.** Difficulty represents expected hash attempts.
2. **Network calibration.** Mainnet converts represented work into fixoshi at a
   fixed consensus calibration.
3. **Hardware-efficiency correction.** The same represented work earns fewer
   fixoshi as height advances.
4. **Miner economics.** Miners choose whether expected revenue justifies their
   costs; the protocol cannot compel hashrate to appear.
5. **Validation.** A full node rejects a coinbase that claims more than the
   allowed subsidy plus permitted fees.

The first three are protocol facts. Miner participation and market response are
economic behavior.

## No hard cap does not mean infinite supply

“Infinite” can mean several different things:

- no finite maximum is encoded for every possible future;
- issuance continues forever at a positive rate;
- any amount can be produced cheaply;
- actual circulating supply grows without bound.

Only the first statement follows directly from the absence of a hard cap. If
future represented work stayed constant while the correction continued, reward
per unit of work would keep declining and the remaining issuance would form a
convergent series in an idealized continuous model. If hashrate grew quickly
enough, issuance could continue or grow. Neither future is known.

The community's **escape velocity** calculation asks a conditional question:
if hashrate stopped changing at a chosen level and the correction continued,
what total issuance would that scenario approach? It is a useful model output,
not a protocol cap and not a forecast.

## Does supply ever shrink?

The subsidy rule does not contain a general negative-issuance mechanism. Coins
can nevertheless become unavailable:

- a transaction may send value to a provably unspendable script;
- keys can be lost;
- holders can voluntarily destroy access;
- custodial claims can disappear while on-chain units remain unchanged.

Only some of these are objectively measurable. Lost keys usually cannot be
distinguished from long-term saving. A claim that “supply shrank” must say
whether it means consensus issuance, UTXO value, provably burned value, or an
estimate of economically active units.

## Fees complicate the picture

Fees transfer existing XRG from spenders to miners; they do not automatically
create new XRG. The public baseline's fee treatment and coinbase rules must be
measured separately from subsidy.

When subsidy becomes small, fees can become a larger share of miner revenue.
That can affect security incentives and the proportional-reward feedback. Fee
markets are therefore part of the research horizon, not an afterthought.

## How Ergon Lab will publish a supply number

A public metric is eligible only when it carries:

- the exact chain and block height;
- the observation time in UTC;
- the unit—XRG and/or fixoshi;
- the formula and code used;
- treatment of genesis, subsidy, fees, burns, and unspendable outputs;
- source endpoint or independently verified node query;
- uncertainty and known exclusions;
- a reproducible snapshot or procedure.

Until then, the homepage correctly shows a publication gate instead of an
unqualified number.

### Primary and attributed reading

- [Ergon project FAQ](https://ergon.moe/en/)
- [Proportional reward paper](https://ergon.moe/prop-reward.pdf)
- [Escape Velocity](https://ergon.moe/blog/Escape%20Velocity_Licho_2023-08-17.html)
  — conditional community model
- [Ergon Supply Shrinking](https://ergon.moe/blog/Ergon%20Supply%20Shrinking_Licho_2023-10-17.html)
  — attributed community argument
- Repository source anchors: `src/validation.cpp`, `src/amount.h`, and
  `src/chainparams.cpp`

---

[← Proportional reward](proportional-reward.md) ·
[Next: Cyphercash&nbsp;→](cyphercash.md)

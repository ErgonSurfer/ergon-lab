<!-- SPDX-License-Identifier: MIT -->

<p align="center">
  <a href="README.md">Discover map</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../insights/README.md">Insights</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../network/README.md">Network</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../node/README.md">Node</a>
</p>

<p align="center">
  <img src="../docs/assets/brand/discover/proportional-reward.jpg" width="100%" alt="A calibrated light sculpture turns computational work into proportional output">
</p>

# Proportional reward

> **Knowledge status:** `Explainer` for the mechanism; `Hypothesis` for its
> economic effects
> **Protocol anchor:** `GetBlockSubsidy` in the public Bitcoin Static v24.0.5
> source

Proportional reward is Ergon's defining consensus rule. Instead of beginning
with a fixed number of XRG per block, the node begins with the work represented
by the block's difficulty target.

## Start with expected work

Proof-of-work mining repeatedly hashes a block header until a result falls
below a target. A smaller target is harder to satisfy. From that target, a node
can calculate the expected work associated with finding a valid block.

Expected work is statistical. If a target implies an average of many attempts,
one miner may still find a solution on the first try and another may search far
longer than average. Consensus can verify the target and the winning hash; it
cannot know the exact number of discarded attempts or joules used.

That is why the precise statement is:

> **The subsidy is proportional to expected proof of work represented by
> difficulty—not to the measured electricity of one particular block.**

## The mechanism in one line

A useful conceptual form is:

```text
block subsidy = expected work × network calibration × time correction
```

The actual implementation uses integer arithmetic and compact proof-of-work
targets. It:

1. validates and expands the block's compact target;
2. derives expected work using the same form used for block proof;
3. applies a gradual height-based correction;
4. divides by the mainnet calibration constant;
5. returns the result as a whole number of fixoshi.

The complete source, rather than this explanatory equation, is authoritative.
Rounding matters at very small values.

## Why difficulty enters the reward

Imagine two otherwise comparable periods:

| Period | Difficulty | Expected hashes per block | Subsidy under proportional reward |
| --- | ---: | ---: | ---: |
| Lower-work period | 1× | 1× | approximately 1× |
| Higher-work period | 4× | 4× | approximately 4× |

If block cadence stays near its target, four times as much sustained hashrate
supports approximately four times as much issuance per unit of time. The
protocol does not need a price feed to produce this response.

This example intentionally ignores calibration, time correction, DAA lag,
integer rounding, block-time variance, fees, and market response. It explains
the direction of the rule, not a production calculator.

## The hardware-efficiency correction

Computing hardware tends to perform more hashes per unit of resource over time.
Without a correction, the same economic effort could purchase progressively
more hashes and therefore more XRG.

Bitcoin Static applies a small recurring reduction to expected work before
converting it into subsidy. After the mid-2022 protocol repair, the intended
mainnet setting corresponds to an approximate 2.3-year half-life in the number
of fixoshi paid for the same represented work.

This is not a scheduled halving of the entire block reward. Difficulty may rise
or fall at the same time. It is a decay in the **reward per unit of represented
work**, intended to track long-run improvements in hardware efficiency.

The chosen half-life is an empirical assumption. If actual efficiency trends
differ, the economic interpretation changes even though consensus continues to
apply the encoded rule correctly.

## Reward and difficulty adjustment

The DAA and proportional reward use related information for different jobs:

```text
hashrate changes
      ↓
block intervals move away from target
      ↓
DAA changes required difficulty
      ↓
reward formula converts that difficulty into subsidy
```

In an idealized steady state, faster blocks before a difficulty adjustment and
larger rewards after it can produce similar issuance per unit of contributed
hashrate. Real networks are not idealized steady states. Timestamp inputs,
abrupt hashrate movement, miner switching, orphan risk, integer bounds, and DAA
implementation all deserve direct testing.

The v24.0.5 release exists partly because a 2022 DAA exploit drove difficulty
to minimum and reward to zero. The repair and its history are counterevidence
against treating the feedback loop as automatically safe.

## What the whitepaper proposes

The proportional-reward paper models price, hashrate, supply, demand, miner
response, and production cost as an interlocking system. It proposes that
making issuance a function of hashrate can create a feedback loop around
production cost.

Its model explicitly relies on simplifying assumptions, including averaged
quantities, sufficiently slow changes, available compatible hashing capacity,
and parameterized miner response. Numerical solutions within that model are
`Simulation`, not observations of the live Ergon network.

Three statements must therefore remain separate:

| Statement | Status |
| --- | --- |
| Bitcoin Static calculates subsidy from represented work | Protocol fact |
| The paper's declared equations produce particular simulated behavior | Simulation, once publicly reproduced |
| Ergon maintains stable purchasing power in the real world | Empirical hypothesis |

## Questions the mechanism creates

- How elastic is SHA-256d hashrate at Ergon's scale?
- How quickly do miners respond to profitability changes?
- Does the DAA damp or amplify short-lived shocks?
- How closely does the hardware correction match efficiency trends?
- How does proportional issuance behave when fees become a larger part of
  miner revenue?
- Can market depth support the arbitrage assumed by the stability model?
- Which observations would falsify the claimed equilibrium behavior?

Those questions form a research program. They are not defects to hide and not
claims to settle by rhetoric.

### Primary trail

- [Proportional reward paper](https://ergon.moe/prop-reward.pdf)
- [Miner's Guide](https://ergon.moe/blog/Miner%27s%20Guide_Licho_2023-08-19.html)
- [Bitcoin Static v24.0.5 release notes](https://github.com/Ergon-moe/Bitcoin-Static/releases/tag/v24.0.5)
- Repository source anchors: `src/validation.cpp`, `src/pow.cpp`,
  `src/chainparams.cpp`, and `doc/release-notes.md`

---

[← How Ergon works](how-it-works.md) ·
[Next: Supply and emission&nbsp;→](supply-and-emission.md)

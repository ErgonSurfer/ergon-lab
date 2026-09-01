<!-- SPDX-License-Identifier: MIT -->

<p align="center">
  <a href="README.md">Discover map</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../insights/README.md">Insights</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../network/README.md">Network</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../node/README.md">Node</a>
</p>

<p align="center">
  <img src="../docs/assets/brand/discover/why-ergon.jpg" width="100%" alt="A rigid sequence gives way to an adaptive mineral field">
</p>

# Why Ergon?

> **Knowledge status:** `Explainer` with explicit `Hypothesis` boundaries
> **Question:** can proof-of-work issuance respond to economic conditions
> without a peg, issuer, reserve, or central monetary committee?

Bitcoin solved a foundational coordination problem: strangers can agree on a
history of digital payments without appointing a financial institution to keep
the ledger. Ergon begins from that achievement and asks whether the monetary
schedule can be made responsive without reintroducing an authority.

## The fixed-schedule problem

In a conventional fixed-reward design, issuance is mostly determined in
advance. Demand, mining economics, adoption, and the amount of value being
transferred may change dramatically, but the protocol continues producing the
scheduled number of units until the next programmed adjustment.

This design has valuable properties: it is simple to state, easy to audit, and
resistant to discretionary intervention. But predictability is not the same as
responsiveness. A fixed schedule cannot ask whether the market currently wants
more units or fewer.

> **Protocol fact**
> A predetermined block subsidy follows its encoded schedule.

> **Economic question**
> Does a supply curve that ignores changing demand make a currency less useful
> as a medium of exchange and unit of account?

The second statement cannot be proven by inspecting source code. It requires a
model, data, counterexamples, and a theory of how miners and users react.

## Ergon's proposition

Ergon replaces a fixed coin amount with a reward derived from proof-of-work
difficulty. Difficulty is a compact expression of how unlikely a valid block
hash is; it therefore represents the expected number of hash attempts required
to find a block.

When more mining work supports the network and difficulty rises, the block
subsidy rises. When work leaves and difficulty falls, the subsidy falls. A
gradual correction reduces the coins paid for the same expected work over time
to account for assumed improvements in computing efficiency.

The intended feedback loop is:

```text
demand for newly issued XRG
        ↓
mining becomes more or less attractive
        ↓
hashrate and difficulty respond
        ↓
the rate of issuance responds
```

No node observes “demand” directly. No oracle sends a market price into
consensus. Miners react to their own revenues and costs; the protocol only sees
valid proof of work and the resulting difficulty.

## Four ambitions, four evidence burdens

### More responsive liquidity

The thesis is that miners can expand issuance when additional units are wanted
and withdraw when they are not. Whether this produces useful liquidity depends
on miner mobility, market depth, difficulty responsiveness, operational costs,
and the speed of demand shocks.

### A more even cost of entry

With proportional reward, mining during a low-hashrate early period produces
proportionally fewer units. The community argues that this reduces the special
advantage usually available to very early miners.

The code establishes the reward rule. “Fair distribution” remains a normative
interpretation that must be evaluated with actual distribution and mining data.

### Less privileged deep capital

The project argues that a unit representing comparable expected work over time
reduces the formation of unusually cheap early inventories. This is a security
and political-economy hypothesis, not a guaranteed property of ownership:
markets, custody, loss, concentration, and unequal access to hardware can still
produce unequal outcomes.

### Better payment stability

The proportional-reward paper models a feedback mechanism around production
cost. Its result depends on assumptions including averaged quantities, slow
changes, miner responsiveness, sufficient compatible hashing capacity, energy
cost, and market behavior.

Ergon is therefore **designed for stability, but neither pegged nor guaranteed
stable**. Historical prices may move sharply. Ergon Lab publishes no price
forecast or investment recommendation.

## Why proof of work remains central

Proof of work does two jobs here:

1. it makes rewriting confirmed history costly;
2. it provides the measurable input used to calculate new issuance.

The second role makes Ergon different. Work is not only the security mechanism;
it is also the meter attached to the subsidy.

That coupling creates new research questions. How quickly should difficulty
adapt? How elastic is available hashrate? What happens during abrupt entry or
exit? Can feedback create oscillations? How should fees interact with a subsidy
that can become very small? These are core parts of the project, not footnotes.

## What would change our minds?

A serious economic experiment must name its failure modes. Evidence against
the intended mechanism could include:

- sustained instability inconsistent with the declared model;
- miner response too slow or discontinuous for the feedback to work;
- difficulty behavior that creates harmful cadence or reward oscillations;
- concentration patterns inconsistent with the distribution thesis;
- fee and subsidy interactions that weaken security or usability;
- simpler alternative mechanisms that perform better under comparable tests.

Ergon Lab will keep those counter-tests beside supportive evidence.

### Primary and attributed reading

- [Proportional reward paper](https://ergon.moe/prop-reward.pdf) — model and
  assumptions
- [Ergon project overview and FAQ](https://ergon.moe/en/) — project position
- [From Stability to Liquidity](https://ergon.moe/blog/From%20Stability%20To%20Liquidity_Licho_2023-08-18.html)
  — attributed community argument
- [On Security](https://ergon.moe/blog/On%20security_Licho_2024-02-11.html)
  — attributed security argument

---

[← What is Ergon?](what-is-ergon.md) ·
[Next: How Ergon works&nbsp;→](how-it-works.md)

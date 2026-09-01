<!-- SPDX-License-Identifier: MIT -->

<p align="center">
  <a href="README.md">Discover map</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../insights/README.md">Insights</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../network/README.md">Network</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../node/README.md">Node</a>
</p>

<p align="center">
  <img src="../docs/assets/brand/discover/glossary-and-sources.jpg" width="100%" alt="A precise material atlas of Ergon concepts and sources">
</p>

# Ergon glossary

> **Knowledge status:** `Explainer`
> Definitions are scoped to Ergon and the public Bitcoin Static v24.0.5
> baseline. A short definition is a doorway, not a substitute for consensus
> source.

## A

### Activation

A declared rule that determines when participating nodes begin enforcing a new
consensus behavior. Writing code is not activation. Testnet activation is not
mainnet activation.

### Address

A human-facing encoding used by wallets to describe a payment destination.
Ergon mainnet uses the `ergon:` CashAddr prefix. An address is not an account in
the protocol and should not be treated as a stable identity.

### ALP

An application-layer token protocol in the Bitcoin Cash ecosystem. Ergon Lab
may study or recognize ALP data without granting it consensus authority.

## B

### Block

A header plus an ordered set of transactions proposed by a miner. A full node
accepts a block only after validating proof of work, its parent relationship,
all transactions, coinbase limits, and other consensus rules.

### Block reward

The value a miner is permitted to claim in a block's coinbase transaction. It
is composed of the allowed subsidy and the portion of transaction fees
permitted by Ergon consensus.

### Block subsidy

New XRG created by consensus in a valid coinbase transaction. In Ergon, the
subsidy is derived from represented proof of work and a time correction.

## C

### CashAddr

An address encoding with a human-readable network prefix and checksum. Ergon's
mainnet prefix is `ergon`.

### CashTokens

A Bitcoin Cash consensus specification for fungible and non-fungible token
primitives attached to transaction outputs. It is public prior art for Ergon's
native-asset research, not an active Ergon rule.

### Chain selection

The process by which a node chooses among valid competing branches. Ergon nodes
select the valid chain with the most accumulated proof of work.

### Chronik

Optional observe-and-index infrastructure. It may derive searchable views from
node-accepted data. It must never decide consensus validity, activation,
mempool admission, or chain selection.

### Coinbase transaction

The first transaction in a block. It has a special input and may claim no more
than the subsidy and permitted fees allowed by consensus.

### Confirmation

A transaction is confirmed when included in an accepted block. Each accepted
descendant block adds another confirmation. Confirmation is probabilistic, not
an absolute guarantee against reorganization.

### Consensus

The deterministic validity and chain-selection rules independently enforced by
full nodes. Social agreement may choose software and activations; an indexer or
website does not override the rules a node executes.

### Cyphercash

An Ergon community term for peer-to-peer cash protected by cryptography and
intended for paying and getting paid. It does not imply anonymity or a price
peg.

## D

### DAA

Difficulty adjustment algorithm. It changes the proof-of-work target in
response to observed block history, aiming to keep average block cadence near
the network target.

### Difficulty

A relative measure of how hard it is to find a block hash below the required
target. It represents expected work, not the exact attempts or energy used for
one observed block.

### Double spend

Two incompatible attempts to spend the same output. Consensus history can
include at most one of them.

## E

### Emission

The creation of new XRG through block subsidy. Ergon's future emission is not a
height-only schedule because future subsidy depends on future difficulty.

### Expected work

The statistical amount of hashing implied by a proof-of-work target. Bitcoin
Static derives it from the compact target and uses it both for chain work and,
in Ergon's reward logic, subsidy calculation.

### Explorer

A service that presents indexed blockchain data. Explorers are useful views,
not consensus authorities, and can be incomplete or wrong.

## F

### Fee

The difference between a transaction's input value and output value. Ergon's
baseline has its own rules for how fees contribute to the coinbase; fees should
not be silently equated with subsidy.

### Finality

Confidence that an accepted transaction will not be reversed. In Nakamoto
consensus, finality is probabilistic and grows with accumulated work above the
transaction.

### Fixoshi

The smallest integer accounting unit in the public baseline. One XRG equals
100,000,000 fixoshi.

### Full node

Software that validates the chain under its own consensus rules and maintains
the state required to decide future validity. A full node does not outsource
consensus to an explorer or indexer.

## G–H

### Genesis block

The first block of a chain. Ergon mainnet has a distinct genesis dated
3 December 2020 with a zero-valued genesis reward in the public source.

### Hard cap

A protocol-defined finite maximum cumulative issuance. Ergon does not define
one universal future maximum; issuance is constrained by represented work and
the time correction.

### Hash

The fixed-size result of a cryptographic hash function. Mining searches for a
block-header hash below the required target.

### Hashrate

The number of hash attempts performed per unit of time, usually estimated from
observed difficulty and block timing. A network hashrate figure is a model-based
inference, not a directly counted total.

### Hardware-efficiency correction

The gradual reduction in fixoshi paid for the same represented work, intended
to account for long-run improvements in hashes per unit of resource. Its
current intended half-life is approximately 2.3 years.

## I–M

### Indexer

Software that derives queryable structures from accepted node data. It can
reconstruct views but cannot make an invalid block valid.

### Mainnet

The production Ergon network. Running new legacy-compatible node software on
mainnet is distinct from activating a new consensus fork.

### Mempool

A node's local collection of valid, policy-accepted, unconfirmed transaction
candidates. Mempools can differ between nodes and are not consensus history.

### Miner

A participant that constructs candidate blocks and searches for valid proof of
work. Miners propose order; full nodes independently validate the result.

### Native asset

An asset whose essential serialization and state transitions are enforced by
base-node consensus. Merely recognizing token-like metadata does not make an
asset native.

## N–P

### Node

A peer participating in the network. In this guide, “full node” specifically
means a node that independently enforces consensus.

### Observer

A component that reads already accepted events and produces a bounded derived
view. Failure of an observer must not alter node validation or chain selection.

### Peer-to-peer

A network arrangement in which participants communicate directly without one
mandatory central server controlling the ledger.

### Premine

Units allocated or mined under privileged conditions before broad public
participation. The Ergon project states that it had no premine or developer
fund, and the public genesis reward is zero.

### Proof of work

A verifiable result that is computationally expensive to search for. It orders
Ergon's history and supplies the expected-work input to proportional reward.

### Proportional reward

Ergon's rule deriving block subsidy from the expected work represented by
difficulty, after calibration and time correction.

## R–S

### Reorg

A chain reorganization: a node disconnects one valid branch and adopts another
valid branch with more accumulated work.

### Script

The program-like locking and unlocking conditions attached to transaction
inputs and outputs.

### SLP

Simple Ledger Protocol, an application-layer token system using `OP_RETURN`
metadata and token-aware validation. SLP data can be ordered by the base chain
without being an Ergon consensus-native asset.

### Stablecoin

A token or currency designed to track a reference value, often using reserves,
collateral, redemption, or an oracle mechanism. Ergon is not pegged and is not
a stablecoin.

### Supply

A word that must be qualified. It may mean cumulative issuance, unspent value,
provably spendable value, estimated economically accessible units, or a
scenario-dependent future total.

## T–X

### Target

The maximum acceptable numerical block hash. A lower target implies greater
difficulty and more expected work.

### Testnet

A non-production network for testing behavior and activation. Evidence from a
testnet is necessary for risky protocol evolution but does not by itself
authorize a mainnet change.

### Transaction

An ordered data structure that consumes existing outputs and creates new ones,
subject to signatures, scripts, value conservation, and other rules.

### UTXO

Unspent transaction output. A currently spendable output derived from accepted
chain history. Wallet “balances” are views over controlled UTXOs.

### XRG

The ticker and native currency unit of Ergon. One XRG equals 100,000,000
fixoshi.

### ⵟ

A visual symbol used by the Ergon community for the unit or project. It is a
presentation convention, not a separate consensus encoding.

## Reading status vocabulary

### Explainer

A source-grounded explanation of established material.

### Hypothesis

A falsifiable proposition not yet established by the available evidence.

### Simulation

A result produced by declared model equations, code, parameters, and inputs.

### Observed

A bounded result obtained in a declared environment. Observation by the author
is not independent reproduction.

### Reproduced

A result independently repeated using reviewed public inputs and a recorded
environment.

### Open Question

A named question whose answer is not yet supported.

---

[← Native assets](native-assets.md) · [Open the source atlas&nbsp;→](sources.md) ·
[Discover map](README.md)

<!-- SPDX-License-Identifier: MIT -->

<p align="center">
  <a href="README.md">Discover map</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../insights/README.md">Insights</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../network/README.md">Network</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../node/README.md">Node</a>
</p>

<p align="center">
  <img src="../docs/assets/brand/discover/how-ergon-works.jpg" width="100%" alt="A precise model of outputs flowing through a transaction into a verified block">
</p>

# How Ergon works

> **Knowledge status:** `Explainer`
> **Scope:** the public Bitcoin Static v24.0.5 protocol baseline; wallet user
> interfaces and future native-asset rules are outside this chapter

Ergon moves value by changing which transaction outputs can be spent. It does
not update a central table of account balances. Every full node independently
checks the same history and derives the current set of spendable outputs.

## 1. Keys authorize spending

A wallet creates private keys and derives public destinations from them. A
payment locks an output to a script. To spend that output later, the spender
provides data—usually including a digital signature—that satisfies the locking
conditions.

The network does not know a user's legal identity. It verifies cryptographic
conditions. Control of keys is therefore control of funds, with an important
consequence: losing the necessary keys can make an output permanently
unspendable.

Ergon's ledger is public. Pseudonymous addresses are not a guarantee of
privacy, and transaction patterns can reveal relationships.

## 2. Transactions transform outputs

A transaction has inputs and outputs:

```text
previous unspent outputs  →  signed transaction  →  new outputs
```

Each ordinary input points to an earlier output and supplies the data required
to spend it. Each new output declares an amount and the conditions for its next
spend. A valid transaction cannot create ordinary XRG from nothing: input value
must cover output value and any fee.

Once an output is spent, it cannot be spent again on the accepted chain. The
set of all currently unspent outputs is called the **UTXO set**.

## 3. Peers relay candidates

Wallets and nodes broadcast transactions through the peer-to-peer network.
Each receiving node checks them against consensus and its local mempool policy.
Transactions that pass policy may wait in the node's mempool as candidates for
a future block.

A mempool is not the ledger. Different nodes may temporarily hold different
candidate sets, and admission to one mempool does not guarantee confirmation.

## 4. Miners build proof-of-work blocks

Miners assemble transactions into a candidate block, add a special coinbase
transaction for subsidy and fees, and repeatedly hash the block header. A block
is valid only if its hash is below the target encoded by the required
difficulty.

Finding a block is probabilistic. A miner can be lucky and find one quickly or
unlucky and search much longer. Difficulty describes expected work across many
attempts; it is not a meter of the exact electricity consumed by an individual
block.

The public mainnet targets an average interval of ten minutes. Actual intervals
vary.

## 5. Full nodes decide for themselves

When a node receives a block, it verifies, among other things:

- the proof of work and required difficulty;
- block structure and size limits;
- every transaction and script;
- the absence of double spends against the accepted history;
- the coinbase value, including proportional subsidy and fees;
- continuity with a known parent.

The node does not ask an explorer, indexer, miner, website, or maintainer
whether the block is valid. Consensus authority remains inside the standalone
node.

Chronik may later observe node-accepted data and derive searchable views. It
must remain optional and must never decide transaction validity, block
validity, activation, mempool admission, or best-chain selection.

## 6. The chain with the most work wins

Peers can briefly see competing valid tips. Nodes select the valid chain with
the most accumulated proof of work. If another branch overtakes the current
tip, the node disconnects blocks from the old branch and connects blocks from
the stronger one. This is a **reorganization**, or reorg.

A transaction gains confirmations as blocks accumulate above it. Finality is
probabilistic rather than absolute: deeper transactions generally require more
work to reverse, but the appropriate confirmation threshold depends on value,
risk, and network conditions.

## 7. Difficulty and reward form two coupled controls

The difficulty adjustment algorithm tries to keep block production near the
target cadence as hashrate changes. Proportional reward then derives the block
subsidy from the work represented by difficulty.

They are related but not identical:

- the **DAA** regulates how hard the next block should be to find;
- the **reward formula** regulates how many new fixoshi that expected work can
  create.

Ergon's 2022 release history records a DAA failure that pushed difficulty to
minimum and reward to zero, followed by a hard-fork repair. That episode is an
important reminder: coupling difficulty and issuance makes DAA behavior part
of the monetary system's evidence burden.

## Follow one payment

```text
Alice's wallet chooses spendable outputs
        ↓
Alice signs a transaction paying Bob
        ↓
peers and full nodes validate and relay it
        ↓
a miner includes it in a proof-of-work block
        ↓
each full node validates the entire block
        ↓
Bob's new output becomes part of the accepted UTXO set
        ↓
later blocks add probabilistic confirmation
```

No single participant performs every role, and no single participant has to be
trusted to perform the others' role honestly.

### Primary trail

- [Bitcoin: A Peer-to-Peer Electronic Cash System](https://bitcoin.org/bitcoin.pdf)
- [Bitcoin Static public source](https://github.com/Ergon-moe/Bitcoin-Static)
- [Bitcoin Cash transaction specification](https://upgradespecs.bitcoincashnode.org/transaction/)
- Repository source anchors: `src/validation.cpp`, `src/pow.cpp`,
  `src/coins.cpp`, `src/net_processing.cpp`, and `src/script/`

---

[← Why Ergon?](why-ergon.md) ·
[Next: Proportional reward&nbsp;→](proportional-reward.md)

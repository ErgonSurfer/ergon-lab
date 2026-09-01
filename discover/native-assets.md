<!-- SPDX-License-Identifier: MIT -->

<p align="center">
  <a href="README.md">Discover map</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../insights/README.md">Insights</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../network/README.md">Network</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../node/README.md">Node</a>
</p>

<p align="center">
  <img src="../docs/assets/brand/discover/native-assets.jpg" width="100%" alt="Distinct material objects embedded directly in one transparent transaction structure">
</p>

# Native assets: the next field

> **Knowledge status:** `Open Question`
> **Delivery state:** research; no Ergon native-asset consensus rule or
> activation is included in the public baseline
> **Authority boundary:** recognition by a parser or indexer does not create a
> valid asset

Ergon's current native object is XRG. The research question is whether the same
UTXO system should be able to carry additional fungible and non-fungible assets
as first-class protocol objects.

The community's interest is inspired in part by CashTokens and earlier
Bitcoin-style token protocols. Inspiration is a place to begin comparison, not
a license to copy an activation or assume it fits Ergon unchanged.

## What “native” means

An asset is native when the node's consensus rules understand and preserve its
essential state during transaction validation. The asset is not merely a text
message that cooperating applications agree to interpret.

A native design can allow a transaction output to carry:

- a category or identity;
- a fungible amount;
- a non-fungible commitment;
- controlled minting or mutable capabilities;
- script conditions that inspect and constrain those properties.

Exactly which features Ergon should support remains unsettled.

## Three layers that must not be confused

### 1. Recognized bytes

A parser can notice that a transaction contains bytes resembling SLP, ALP, or
a CashTokens prefix. That is an observation about serialization.

Malformed or invalid data can still match a prefix. Recognition proves neither
validity nor ownership.

### 2. Reconstructed application state

An indexer can follow a declared token protocol, connect transactions, and
derive balances or metadata. SLP is an example of an overlay design: token
instructions live in `OP_RETURN`, and token-aware software applies rules beyond
the base node's ordinary XRG validation.

This can be useful, but its validation authority belongs to that token system
and its participants—not automatically to base consensus.

### 3. Consensus-native state

If full nodes enforce asset conservation, capability, and serialization rules,
invalid asset transitions become invalid transactions or blocks for every
upgraded participant. This is a consensus change and requires deliberate
specification, implementation, testing, activation, and operational evidence.

An explorer cannot promote layer 1 or 2 into layer 3.

## Why research native assets for Ergon?

Potential uses include:

- merchant vouchers and loyalty units;
- event tickets and credentials;
- local or mutual-credit instruments;
- claims on goods or services;
- governance rights;
- receipts and provenance markers;
- contract state and coordination primitives;
- representations of externally issued assets.

These are possible constructions, not endorsements. An on-chain token does not
guarantee that an external issuer is solvent, a physical claim is enforceable,
metadata is truthful, or a market is liquid.

Native assets could also broaden Ergon's role as payment infrastructure. They
could increase complexity, attack surface, state growth, wallet risk, and
governance burden. Both sides belong in the design review.

## What prior art teaches

### SLP

Simple Ledger Protocol associates token amounts with ordinary transaction
outputs using `OP_RETURN` metadata. It demonstrates permissionless issuance and
application-layer token validation without changing base consensus. It also
illustrates the coordination and accidental-burn risks of token-unaware
software.

### ALP

ALP is another application-layer token protocol designed around efficient
metadata encoding and multiple token sections. Its parsing and coloring logic
can inform observer research without becoming Ergon consensus authority.

### CashTokens

The CashTokens CHIP specifies fungible and non-fungible primitives enforced by
Bitcoin Cash consensus. It offers concrete work on output encoding,
capabilities, supply definitions, script integration, wallet compatibility,
test vectors, and activation.

Ergon must still answer whether those trade-offs fit its own monetary model,
legacy network, fee behavior, activation constraints, and long-term node
requirements.

## The minimum design questions

Before implementation can be considered activatable, a public specification
must answer:

1. **Identity:** what uniquely defines an asset category?
2. **Conservation:** which transitions can create, transfer, or destroy units?
3. **Capabilities:** who can mint, mutate, or delegate authority?
4. **Serialization:** how are assets represented without ambiguous parsing?
5. **Scripts:** what can contracts inspect and constrain?
6. **Wallet safety:** how does unaware software avoid accidental loss?
7. **Resource cost:** what are the CPU, memory, bandwidth, and UTXO impacts?
8. **Reorg behavior:** how is derived state disconnected and rebuilt?
9. **Compatibility:** how do legacy nodes behave before and during activation?
10. **Activation:** what signal, boundary, rollback plan, and testnet evidence
    are required?

## A deliberately long road to activation

The engineering order is:

```text
public prior-art corpus
        ↓
independently authored Ergon specification
        ↓
parsers and non-authoritative observation
        ↓
test vectors + threat model + resource bounds
        ↓
candidate consensus implementation behind an inactive gate
        ↓
dedicated testnet activation and adversarial testing
        ↓
separate mainnet-readiness decision
        ↓
possible future mainnet activation
```

Mainnet compatibility of the new standalone node comes before any of these new
rules. A testnet success would still not authorize a mainnet fork.

## Current public statement

- XRG remains the only consensus-native asset in the accepted public baseline.
- No native-asset activation height or time is defined by Ergon Lab.
- Chronik, if present, is optional observe-and-index infrastructure.
- SLP, ALP, and CashTokens are research references, not Ergon validation
  authority.
- Any future implementation must arrive with specification, provenance,
  license, tests, compatibility evidence, and a separately governed activation.

### Primary prior art

- [CashTokens CHIP repository](https://github.com/cashtokens/cashtokens)
- [CashTokens introduction](https://cashtokens.org/docs/intro/)
- [SLP Token Type 1 specification](https://github.com/simpleledger/slp-specifications/blob/master/slp-token-type-1.md)
- [Bitcoin Cash transaction specification](https://upgradespecs.bitcoincashnode.org/transaction/)
- [Ergon consensus boundary](../docs/architecture/consensus-boundary.md)

---

[← Origins and lineage](origins-and-lineage.md) ·
[Next: Glossary&nbsp;→](glossary.md)

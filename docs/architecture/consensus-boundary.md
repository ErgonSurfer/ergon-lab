<!-- SPDX-License-Identifier: MIT -->

# Consensus Boundary: Standalone Node and Optional Indexing

## Invariant

The Ergon node is standalone. Its correctness must not depend on Chronik or any
other indexer being compiled, enabled, reachable, synchronized, or correct.

For a given node build and configuration, removing optional indexing must not
change the node's decisions about:

- block or transaction consensus validity;
- active-chain selection or reorganization;
- deployment activation;
- mempool admission, eviction, or conflict handling;
- authoritative chain and UTXO state; or
- peer protocol enforcement.

This document defines an architecture rule. It does not claim that every
possible build or integration has already been tested.

## Authority flow

The node validates peer and local inputs, applies consensus and policy rules,
selects its active chain, and persists authoritative node state. Optional
indexing may consume node-accepted blocks, transactions, and notifications to
build derived query views.

Chronik may:

- observe node-accepted events;
- maintain rebuildable indexes derived from node state;
- expose query and subscription interfaces for derived data; and
- report its own synchronization or index health.

Chronik must not:

- approve or reject consensus inputs on behalf of the node;
- select, rank, or override the active chain;
- decide activation state;
- authorize mempool admission or removal;
- supply authoritative UTXO or chain state back into validation; or
- make node startup, validation, synchronization, or recovery depend on index
  availability.

An index can lag, disagree, or be rebuilt. Such a condition is an indexing
health issue unless node-owned evidence independently demonstrates a node
consensus or state failure.

## Integration rules

Any indexing integration must keep data flow and ownership explicit:

1. Node-owned validation completes before an event becomes index input.
2. Index-derived state is labeled as derived and is not read as consensus
   authority.
3. Index storage can be removed and rebuilt without modifying authoritative
   node state.
4. Index failure is isolated and observable; it cannot silently alter a node
   decision.
5. Public APIs distinguish node-authoritative results from index-derived views
   where confusion is possible.

## Required evidence for boundary changes

A proposal touching this boundary must classify consensus, validation, P2P,
storage, RPC, mempool, indexing, and build reachability. It must provide public,
fresh-environment evidence for the applicable configurations:

- Chronik compiled out;
- Chronik compiled in but disabled;
- Chronik enabled and healthy; and
- Chronik unavailable, interrupted, stale, or rebuilding.

Not every proposal needs every configuration. The allowlisted-change record
must justify omissions and keep claims within tested scope. Tests must include
failure paths and demonstrate that node decisions do not consume index-derived
authority.

Historical or datadir equivalence, operator-binary attestation, and full-system
behavior require their own evidence. Component tests do not establish those
claims.

## Review stop conditions

Mark a proposal `blocked` if:

- node validation or chain selection reads an index result as authority;
- standalone behavior is untested for an affected decision path;
- disabling or losing the index changes a node consensus decision;
- index and node storage ownership is ambiguous; or
- the available evidence cannot distinguish node behavior from index behavior.

The resolution is an architecture or evidence change, not a broader claim.

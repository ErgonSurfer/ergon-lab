<!-- SPDX-License-Identifier: MIT -->

<p align="center">
  <a href="../README.md">Home</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../discover/README.md">Discover</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../insights/README.md">Insights</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="../node/README.md">Node</a>
</p>

<p align="center">
  <img src="../docs/assets/brand/network-observatory.jpg" width="100%" alt="A distributed field of network nodes and propagation waves">
</p>

# Network Observatory

The observatory will turn public network data into sourced, timestamped, and
reproducible context. It observes node-accepted data; it never becomes an
authority for consensus, activation, mempool admission, or chain selection.

## Network Pulse contract

No metric is published merely because a number is available. Every displayed
value must carry a reviewed contract:

| Field | Requirement |
| --- | --- |
| Definition | The exact quantity and what it does not measure |
| Source | A reviewed public endpoint or independently generated public dataset |
| Unit | A declared base unit and display transformation |
| Time | Observation timestamp, timezone, and aggregation window |
| Method | Collection, validation, transformation, and missing-data rules |
| Uncertainty | Measurement error, inference limits, and sensitivity |
| Reproduction | Commands, code, public inputs, and expected result |
| Privacy | No operator identity, private topology, credentials, or private endpoints |

## Planned metrics

| Metric | What must be resolved first | Status |
| --- | --- | --- |
| Existing supply | Consensus-derived equation and historical cross-check | Research |
| Emission | Units, height convention, and rule-derived schedule | Research |
| Hashrate | Inference model, window, variance, and sensitivity | Research |
| Block cadence | Source, interval, reorg handling, and distribution | Planned |
| Difficulty | Exact consensus units and display transformation | Research |
| Block size and propagation | Sampling coverage and observer bias | Research |
| Transaction activity | Inclusion, aggregation, and duplicate semantics | Planned |
| Address activity proxy | Definition and explicit warning that addresses are not people or wallets | Planned |
| Fees | Units, weighting, and confirmation context | Research |

## About “wallet count”

A public blockchain does not reveal a reliable count of people or wallets.
Addresses may be reused, rotated, shared, batched, or controlled in groups.
Ergon Lab may publish a carefully named activity proxy, but it will not relabel
that proxy as a user or wallet count.

## Network updates

No bulletin is currently published. Future updates will separate:

- directly observed chain or network facts;
- inferred quantities such as hashrate;
- ecosystem announcements attributed to their public source;
- interpretation and open questions.

[Inspect the observatory gate →](../cockpit/cockpit.yaml) ·
[Read the consensus boundary →](../docs/architecture/consensus-boundary.md)

---

[Return to Ergon Lab →](../README.md)

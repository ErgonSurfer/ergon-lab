<!-- SPDX-License-Identifier: MIT -->

# Public Source Provenance Policy

This policy is a publication boundary, not merely a contribution preference.
Every tracked byte, history object, generated artifact, release asset, issue,
and evidence pack must be publishable from public inputs.

## 1. Normative origin

The repository begins from the public Bitcoin Static v24.0.5 snapshot:

| Field | Value |
| --- | --- |
| Source commit | `2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b` |
| Source Git tree | `8a74bb952c2137156214b9fe5888c494bd77aeca` |
| Baseline license | MIT |

The public root import must contain exactly that Git tree and no prior history.
All later content must be independently reviewed and traceable to
public sources or independently authored work.

## 2. Absolute deny boundary

Never copy, merge, filter, squash, cherry-pick, export, rewrite, summarize,
translate, or paraphrase private material for publication.

Publication is denied if content contains, reveals, or depends on:

- a private host product, repository, source tree, history, branch, architecture,
  path, remote, log, or build environment;
- operator identities, wallets, endpoints, topology, operational practices, or
  non-public network observations;
- credentials, tokens, secrets, personal data, private datasets, corpora,
  fixtures, expected values, or unreviewed donor artifacts;
- binaries, caches, chain data, generated reports, or host-bound evidence whose
  public provenance cannot be reproduced;
- code or data with unknown origin, unclear contributor rights, missing license,
  or incompatible terms; or
- any mechanism that gives an indexer consensus, validation, activation,
  chain-selection, or mempool-admission authority.

Sanitizing a private-derived artifact in place does not make it public. Rebuild
the artifact from reviewed public inputs. Uncertainty means `blocked`.

## 3. Engineering change evidence

Before technical bytes are accepted, a machine-readable record must bind the
exact proposed change and include:

- public purpose and exact scope;
- baseline-relative paths, file modes, sizes, diffstat, and raw-byte preimage
  and postimage SHA-256 values;
- public sources or an independently authored postimage inventory governed by
  the repository's standing MIT contribution terms;
- SPDX license declarations and compatibility review;
- affected surfaces: consensus, validation, P2P, storage, RPC, wallet, indexing,
  UI, build/release, tests, documentation, and data;
- production and test-only reachability;
- a patch generated solely between identified public states, with no private
  commit metadata;
- fresh-environment commands, tool versions, safe environment variables,
  dependency-lock status, expected results, and output-normalization rules;
- checksums for every portable input, report, output, and reproduction-pack
  file;
- uncertainty, limitations, counter-evidence, failure observations, and known
  inherited debt;
- automated scan results plus human review for secrets, private paths,
  identities, endpoints, symlinks, submodules, executable-bit changes,
  traversal, generated files, and binaries;
- explicit confirmation that the node remains correct without Chronik; and
- the publication decision and resulting public commit, once known.

The review decision is `ACCEPT`, `NARROW`, or `REJECT`. It applies only to the
exact files and evidence reviewed. `NARROW` creates a smaller proposed change;
it does not authorize nearby material.

### Standing contribution and provenance terms

Independently authored material accepted into this repository is contributed
under the repository's MIT license unless a compatible, explicitly recorded
license applies. A change record computes a canonical inventory digest from its
governed postimages and provenance codes. That digest protects integrity and
traceability; it is not a per-change legal attestation and does not require the
maintainer to restate the same license terms.

A signed commit records approval of the exact committed bytes. It does not
authorize a different file set, later integration, publication of private
material, evidence promotion, or consensus activation. Those remain separate
operational decisions. Material with ambiguous third-party rights is blocked
until its license and authority are resolved.

## 4. Evidence and claims

Evidence must be portable and scope-matched. A unit or component check cannot
prove full-node, network, historical-data, operator-binary, or operational
parity. Unsupported, unavailable, or unlocked dependencies must be stated.

Reports derived from private or host-bound environments are not publication
inputs. Regenerate reports from the clean public tree in a fresh environment,
then record commands, versions, normalization, results, and digests.

Known failures and counter-evidence remain visible until a separately reviewed
change and fresh public evidence retire them. A passing test cannot erase an
unrelated failure or broaden the evidence ceiling.

## 5. Standalone and indexer boundary

The node decides consensus validation, chain selection, activation, mempool
admission, and persisted authoritative chain state. Chronik is optional
observe-and-index infrastructure. It consumes node-accepted information and may
serve derived views; it may not become an authority for any node decision.

Publication gates must test the supported standalone configuration without
Chronik. Where Chronik integration is present, its disabled and unavailable
states must not alter node correctness.

### Node delivery order

Node work advances through separately reviewed gates:

1. preserve and verify the exact public Bitcoin Static baseline;
2. falsify bounded compatibility differences with the legacy node using
   isolated, hash-bound roles and fail-closed harnesses;
3. verify optional indexing first with the standalone configuration compiled
   out versus compiled in but disabled, then separately review explicit local-
   regtest opt-in, bounded restart, reindex, chainstate-reindex, pruning, and
   deep-reorganization behavior; indexing remains default-OFF and never gains
   node authority;
4. prove the standalone candidate can be deployed on the existing Ergon
   mainnet beside legacy nodes without changing consensus, including the
   required historical-chain, restart, datadir, and operator-binary evidence;
5. publish native-assets, reward, scaling, and DAA research as separately
   labeled specifications, corpus work, and simulations with no implicit node
   or consensus change;
6. place any separately reviewed fork behind a deterministic,
   dormant-by-default testnet activation boundary;
7. validate that fork on testnet across activation, rollback, mixed-peer, and
   reorganization boundaries;
8. prepare any mainnet proposal only after prolonged testnet evidence,
   independent reproduction, release evidence, and explicit governance; and
9. keep future mainnet activation as a final distinct decision that is never
   implied by preparation or testnet success.

Engineering records use the ordered stages `legacy-compatibility`,
`optional-indexing`, `testnet-activation`, and `mainnet-readiness`. Research,
corpus work, and simulations use separate `research` records and cannot modify
consensus behavior. These record classes support the nine reader-facing gates;
they do not collapse testnet implementation into validation or mainnet
preparation into activation.

## 6. Cockpit semantics

Delivery state and knowledge status are independent required axes.

### Delivery states

- `verified` — the declared gate has current, scope-matched public evidence;
- `active` — work is in progress but the exit condition is not yet met;
- `blocked` — a stated condition prevents progress or publication;
- `planned` — scoped future work has not started;
- `research` — exploratory delivery work with no implementation commitment.

### Knowledge statuses

- `Explainer` — sourced explanatory material, not a new empirical result;
- `Hypothesis` — a falsifiable proposition awaiting sufficient evidence;
- `Simulation` — output from a declared model under stated assumptions;
- `Observed` — a recorded measurement from identified public data and method;
- `Reproduced` — an independent public-input reproduction matching declared
  criteria;
- `Open Question` — an unresolved question with its evidence gap stated.

No mapping is implied between the axes. Each entry must define the scope and
acceptance criteria behind its labels.

## 7. Research and observatory records

Every research or observatory record must expose:

- sources, citations, access dates, versions, and licenses;
- research question or explainer purpose;
- methods, algorithms, and falsification or acceptance criteria;
- public data identifiers, retrieval method, checksums, and retention status;
- units, definitions, transformations, and time bases;
- assumptions, parameters, software/tool versions, and environment;
- uncertainty, sensitivity, error treatment, and confidence limits where
  meaningful;
- limitations and the boundary of any inference;
- counter-evidence, alternative explanations, and unsuccessful checks; and
- a portable reproduction pack with a manifest, exact commands, expected
  results, and per-file checksums.

Research topics may include proportional rewards, cyphercash, supply/emission,
descriptive price context, hashrate responsiveness/elasticity, difficulty
adjustment, block size/propagation, security, and fee markets. Price work is
historical or descriptive context only and must not offer forecasts, financial
advice, or investment recommendations.

## 8. Generated roadmap

The machine-readable cockpit is canonical. The English roadmap is a generated
projection of publication boundaries, gate delivery states, and declared
roadmap placement. It is regenerated only when that projection changes.

Each regeneration must record the triggering event, canonical input-projection
hash, generator version, output hash, and reviewer. Narrative, methods, data,
or evidence that do not change the projected gate or boundary do not trigger a
roadmap regeneration. Generated roadmap files are never hand-edited.

## 9. Publication gates

A change may be merged or released only after the applicable checks are current:

- baseline and provenance binding;
- origin, authorship, SPDX, and license compatibility;
- secret, privacy, identity, endpoint, and path scanning plus human review;
- cockpit schema and semantic validation;
- generated-roadmap integrity;
- build, unit, functional, and integration tests appropriate to the scope;
- standalone operation without Chronik;
- reproducibility and evidence-pack validation; and
- required code-owner and domain review.

### Protected exact-SHA publication

`protected-exact-sha/v1` separates public review from publication without
changing the reviewed commit:

1. The topic branch contains one SSH-signed commit whose sole parent is the
   current public `main` commit.
2. A public pull request runs review and every required status check on that
   exact commit ID.
3. After all required checks succeed on that commit ID, `main` advances only by
   a non-force fast-forward push of the same commit.
4. Publication succeeds only when a fresh fetch proves that `origin/main`
   equals the reviewed commit ID and tree.

Squash and rebase merges are forbidden for this workflow because they replace
the reviewed commit ID and its SSH signature. Pull requests remain the public
review and CI mechanism; the exact fast-forward is the publication mechanism.
The protected branch must continue to require linear history, signed commits,
the declared status checks, restricted deletion, and blocked force pushes.

The GitHub-signed squash commit
`56a18f3bd78e34e78caa7946dc7e1c0a45e8e6b2` preserved the exact reviewed tree
of SSH-signed pull-request commit
`1c728c27094bf52d02c3cea23e06b72a08c5f264` but replaced its identity and
signature. This resolved workflow counterexample motivates the exact-SHA rule;
it does not change any evidence status or supply a legacy-compatibility result.

Release artifacts additionally require a protected source tag, checksums,
provenance, a software bill of materials, and the repository's declared
reproducible-build evidence. A gate is not waived by urgency or by an unrelated
passing check.

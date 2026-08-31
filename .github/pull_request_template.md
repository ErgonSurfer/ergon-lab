<!-- SPDX-License-Identifier: MIT -->

## Public purpose and scope

<!-- State the public purpose, exact scope, and linked issue. -->

## Provenance, authorship, and license

<!-- List every public source with immutable identifier and license, or attest
independent authorship and right to license. Name the SPDX license for new
material. Do not use private-derived material, including paraphrases. -->

- [ ] Every input is public and identified, or independently authored.
- [ ] I have the right to contribute the proposed bytes under the declared
      license.
- [ ] Applicable upstream notices are preserved.

## Affected surfaces

<!-- Mark every affected surface and explain any non-obvious classification. -->

- [ ] Consensus
- [ ] Validation / mempool
- [ ] P2P
- [ ] Storage / datadir
- [ ] RPC / API
- [ ] Wallet
- [ ] Indexing / Chronik
- [ ] Build / release
- [ ] Tests only
- [ ] Documentation / cockpit
- [ ] Data / research

Production reachability:

Test-only reachability:

## Standalone and Chronik boundary

<!-- Explain why the standalone node remains correct with Chronik compiled out,
disabled, unavailable, stale, or rebuilding, as applicable. Chronik may observe
and index but must never become consensus, chain-selection, activation, or
mempool authority. -->

- [ ] This change does not give Chronik or another indexer node authority.
- [ ] The supported standalone configuration is tested, or the omission and
      resulting evidence limit are stated below.

## Verification

Commands and expected results:

Tool and dependency versions:

Dependency lock status (`locked`, `unlocked`, or `not applicable`):

Safe environment variables and output-normalization rules:

Portable input, report, output, and reproduction-pack SHA-256 values:

## Claims and evidence scope

<!-- State exactly what the evidence proves. Do not generalize a component
result to network, historical, operator-binary, or system parity. -->

Delivery state:

Knowledge status (if applicable):

Evidence ceiling:

## Uncertainty and counter-evidence

Known limitations:

Counter-evidence and unsuccessful checks:

Known inherited debt:

## Publication hygiene

- [ ] The diff is relative only to identified public repository states and
      carries no private history or commit metadata.
- [ ] Exact proposed bytes and generated outputs were scanned for secrets,
      private paths, identities, endpoints, and host-bound metadata.
- [ ] Symlinks, submodules, executable-bit changes, traversal, binaries,
      generated files, and caches were rejected or explicitly reviewed.
- [ ] Generated roadmap files were not hand-edited.
- [ ] The allowlisted-change record binds the exact files under review, or this
      pull request explains why no such record is required.

## Reviewer notes

<!-- Publication decision: ACCEPT, NARROW, or REJECT. Maintainers complete this
section; contributor checkboxes do not authorize publication. -->

<!-- SPDX-License-Identifier: MIT -->

# Security Policy

## Reporting a vulnerability

Use GitHub's private vulnerability reporting flow from this repository's
**Security** tab. Do not open a public issue or pull request with vulnerability
details.

If private vulnerability reporting is not available, do not publish exploit
details. Wait for the repository maintainers to provide a verified private
contact method through the repository. Never send credentials, wallet secrets,
private keys, recovery material, or unrelated operator data as part of a
report.

Include only what is needed to assess the issue:

- affected public commit or release identifier;
- affected component and configuration assumptions;
- minimal reproduction steps or proof of concept;
- observed and expected behavior;
- impact and realistic attack prerequisites;
- whether the issue crosses consensus, validation, P2P, storage, RPC, wallet,
  indexing, build/release, or operational boundaries;
- relevant logs with secrets, identities, endpoints, and local paths removed at
  the source; and
- any known mitigations or counter-evidence.

Do not probe systems, nodes, accounts, or infrastructure you do not own or have
explicit permission to test.

## Scope and support

Security support applies only to versions explicitly listed as supported in a
published release policy. Until such a policy and release exist, no version is
represented here as production-supported.

Optional indexers, including Chronik, are outside consensus authority. A report
that shows an index or query discrepancy must not assume a consensus failure;
identify which component produced each result. Conversely, optional indexing
must never be used to mask or override a node consensus result.

## Disclosure process

Maintainers will first validate the report against public source and establish
its affected scope. Fixes must pass the same clean-room provenance, license,
privacy, and publication gates as other changes. Public disclosure should
include affected versions, impact, mitigation, fix identifiers, and credit when
requested, while excluding secrets and operator-identifying data.

No response time or release date is promised until maintainers publish a
versioned security support policy.

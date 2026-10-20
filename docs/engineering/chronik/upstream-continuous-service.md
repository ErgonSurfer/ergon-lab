<!-- SPDX-License-Identifier: MIT -->

# Continuous native Ergon service with upstream Chronik

This is one integrated service milestone: ordered node events, safe HTTP and
WebSocket ownership, full historical startup, trusted plugins and Linux build
hardening. It reuses Chronik's existing token engines, indexer, database,
handlers and subscription protocol. It does not implement another indexer.

The native Bitcoin Static legacy rules remain authoritative. No consensus,
reward, DAA, network identity, testnet activation or mainnet fork is changed.
Chronik can reject an unsafe API submission; it cannot veto native validation,
block selection or admission through other native interfaces.

## Explicit profiles, never automatic adoption

All compile and runtime switches default to OFF. A continuous-service build
additionally enables `BUILD_CHRONIK_UPSTREAM_SERVICE`; the read-only historical
profile uses `BUILD_CHRONIK_UPSTREAM_SHADOW`. Both require the existing joined
service backend. Plugin embedding remains separately optional.

The continuous owner requires `-chronikupstreamservice=1`, an explicit expected
network (`main` or synthetic `regtest`) and canonical native genesis hash via
`-chronikupstreamservicenetwork` and `-chronikupstreamservicegenesis`. Native
configuration and the actual active genesis must agree before opening an index.
Testnet is not admitted. Pruned storage and conflicting profile flags are
refused. `-chronikupstreamserviceport` selects a loopback port, never a public or
wildcard interface; zero requests an ephemeral port.

The owner uses a separate `chronik-upstream-service` root and a distinct
source/network/genesis/profile identity. It does not adopt, wipe or migrate a
fixture, read-only shadow, older source or incompatible plugin index. A failed
index closes its API while the native node continues.

Trusted Python plugins use the previously reviewed flat-bundle contract and
explicit source/configuration/interpreter commitment. The historical binding
has its own network domain; a regtest commitment cannot authorize main inputs.
The four contributed public plugin modules and their ALP/SLP protocols are
unchanged by this milestone. Python remains trusted code, not a sandbox.

## Continuous operation and client contracts

An optional bounded native journal captures accepted mempool inputs, removals,
block connections and disconnections at their synchronous native boundary.
The journal owns its payloads, monotonically sequences events and counts leases
against its 128-event / 64-MiB service budget. Allocation, continuity or budget
failure is terminal for the index, never a native acceptance decision.

The worker anchors the journal atomically at a current startup fence and drains
events through the existing upstream writers outside native locks. It publishes
only after comparing the indexed chain and mempool frontier with a fresh native
source fence. Legacy coalesced snapshot replay is forbidden after the live cut.
Native validation hooks remain inert when no optional journal is attached.

Broadcast uses upstream token/burn verification and the native broadcast path.
`skip_token_checks`, finalization requests and high-fee bypass are refused;
batches contain at most 16 transactions. The native absolute `maxTxFee` remains
the fee ceiling; it is not advertised as the donor's fee-rate policy.

Native acceptance is not index readiness. A successful receipt is HTTP 200 only
when every accepted transaction can be read through a current indexed view.
Otherwise the accepted receipt is HTTP 202 with `x-ergon-index-state: pending`
and a retry hint. Clients must not treat 202 as rejection or blindly rebroadcast.
Temporary index unavailability is a typed refusal, not successful empty data.

WebSockets reuse upstream protobuf subscriptions and existing limits. Source
continuity loss, channel overflow/closure or shutdown ends the session; clients
must refresh an HTTP snapshot before resubscribing. Notifications are not a
durable event log. HTTP/Tokio, subscription guards, workers and native users are
joined before teardown. Healthy shutdown is distinct from index corruption;
genuine continuity failure still wins during shutdown.

Same-host HTTP and HTTPS origins are admitted so an existing loopback reverse
proxy can terminate TLS while preserving `Host`. Foreign origins remain
refused; forwarded-origin headers are not trusted. The synthetic client checks
HTTPS-origin broadcast and WebSocket admission plus an explicit foreign-origin
403. This does not itself qualify a deployed proxy or a cross-host CORS setup.

## Public verification

`ERGON-CHANGE-0066` and `upstream-continuous-service-v1.json` bind the complete
current successor over accepted commit `f11d867dd622a9c7a13a98736a6ff5296e555d07`.
All other parent paths are frozen, including Cargo's dependency lock, the four
plugins, token algorithms and old change records. Previous source validators
are rerun on that exact immutable parent, where their original preimages and
scope laws apply. This does not replace current-source verification: the new
gate checks every current pre/postimage, exact allowed path, record and claim.

The five existing Linux profiles, all inherited cases, assertion counts,
repetitions and 60-second component deadlines remain. Existing daemon and token
oracles execute against the current build, not a predecessor binary. CMake and
the public runner bind the same ordered source inputs and both new profile
switches. The actual four-plugin component adds the network-domain unit case.

Additional native groups require exact source-declared case identities, one
strict positive passing-assertion summary, and three byte-stable fresh-process
observations. Newly observed assertion counts are recorded separately from the
unchanged prebound inherited counts. A missing/duplicate/malformed summary,
skip, failure, timeout, source drift or incomplete cleanup cannot pass.

The first public candidate, PR #71 at `7b746bcc6db2b9dc8d3b1c73d0c38f2807c9fbc4`,
compiled the standalone profile but its new gate refused missing case markers.
The progress observer had lived only in the Chronik archival test translation
unit. It now lives once in the common test entry point, with the identical
callbacks and marker grammar, so compiled-out cases are observable too. No
test body, assertion requirement or failure check is removed. The failed run
remains visible; this correction is not a claim that it passed.

That candidate also changed the legacy unavailable donor-rate sentinel from
`-1` to `0`, contradicting the unchanged native bridge oracle. The sentinel
stays `-1`; the adapter admits only the exact marker derived by the upstream
caller and still delegates to native broadcast with `allowhighfees=false`.
That marker is not interpreted as a native limit. The actual absolute
`maxTxFee`, inherited fee parity cases and assertion counts remain unchanged.

Synthetic regtest exercises the same network/genesis-bound owner, ALP
genesis/mint/send/intentional burn, unintended-burn refusal, upstream WebSocket
events, reorganization, nonempty mempool reopen, historical reconstruction and
read-only capability refusal. Zero-value fixtures explicitly restore artificial
fee priorities before persistence, never refill after reopen or alter native
fee policy. Linux executables must contain exactly one RW, non-executable
`GNU_STACK`; assembler inputs are corrected rather than masking final linker
warnings. Compiled-out native journal controls and default-OFF mining remain.

## Deployment is a separate operational result

This source record contains no incoming private report, binary or operator
configuration. CI success is a bounded synthetic observation, not deployment
readiness or independent reproduction. Rust registry tuples are locked;
system packages and runner images remain unlocked. Earlier timeouts and failed
attempts remain counter-evidence, not silently erased by this milestone.

For replacement, build a fresh installation from the exact accepted GitHub
commit, bind its binary/configuration/plugin identities, and qualify it against
the real historical chain and clients in isolation. Preserve the old service,
binary, configuration, data and proxy target for rollback. Switch traffic only
after catch-up, token/plugin queries, new blocks, WebSockets and joined
stop/reopen pass. A data-integrity contradiction, unsafe shutdown or target
service regression blocks the switch. No CI job deploys or performs a cutover.

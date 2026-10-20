<!-- SPDX-License-Identifier: MIT -->
# Reviewed plugins on the upstream Chronik engine

This change connects explicitly approved local Python bundles to the existing
Chronik plugin context and HTTP handlers. It also contributes four reusable
protocol plugins, their byte-level documentation and synthetic tests. It does
not reconstruct Chronik's token engine, database, dispatcher or version checks.

The exact parent is the signed public commit `385aba7`, integrated by #69.
The machine-readable source inventory is `upstream-external-plugins-v1.json`;
the engineering record is `ERGON-CHANGE-0065`. Earlier inventories remain
immutable. The finite contribution clause in `PUBLICATION_POLICY.md` permits
only the named four modules, not unrelated application or operator material.
Existing module, class and wire identifiers remain intact for compatibility
and provenance; this is not anonymization of a private repository.

## What is included

| Module | Version | Purpose |
| --- | --- | --- |
| `moom_alp_token_index` | 0.1.0 | ALP token/script/genesis projection |
| `moom_nft_index` | 0.2.0 | NFT-like candidate projection for both ALP and SLP |
| `moom_alp_exch_market` | 0.2.1 | Exchange carrier discovery |
| `moom_reward_claim_index` | 0.1.1 | Claim carrier discovery |

See [`plugins/PROTOCOLS.md`](../../../plugins/PROTOCOLS.md) for groups, data,
units, byte order and limitations. In particular, a one-atom output does not
prove unique issuance; exchange and claim carriers are not authorization,
settlement or payout proofs. Binary exchange fields require fixed-width
decoding, not unrestricted splitting on the separator byte.

The market and claim patches narrowly reject malformed script/non-byte inputs
that formerly raised exceptions. Valid projections are retained in the public
corpus. The new versions and bundle commitment must not adopt an old index or
reuse observations for different source bytes.

## Explicit trust and input ownership

Python is trusted executable code, **not a sandbox**. A dedicated canonical
directory contains only one `plugins.toml` and 1–32 flat Python modules. Limits
are 1 MiB per module, 64 KiB for configuration and 4 MiB total. Regular UTF-8
files without NUL are required. Symlinks, hard links, caches, directories,
extra files and group/world-writable inputs are rejected on the Unix profile.

The commitment includes sorted names, complete raw bytes and the configured
interpreter version/embedding identity using length-framed fields. It contains
no absolute path. Startup requires the independently approved exact lowercase
SHA-256 value; it never approves arbitrary inputs by calculating their digest.
Checks before and after import detect ordinary drift, not an adversarial
concurrent filesystem writer or an atomic snapshot. Protect the directory.

The actual loaded module inventory must equal the file inventory, and import
origins must match. One process-once interpreter claim is shared with the
existing synthetic fixture. Context ownership outlives DB/HTTP users and joined
shutdown. There is no Python finalization, reinitialization or automatic retry.
Changed source, configuration, interpreter or mode cannot silently adopt or
erase an incompatible index. Upstream remains responsible for version checks.

## Native and HTTP boundary

Compile-time and runtime defaults stay OFF. The new flags require the existing
unpruned local-regtest service, exclude the synthetic plugin mode and expose
only its ephemeral loopback listener. No testnet/mainnet admission guard,
consensus parameter, node authority or activation rule changes.

Only actually loaded module names receive a capability. Existing upstream
groups/history/confirmed/unconfirmed/UTXO handlers are reused. Group hex is
canonical lowercase, nonempty and at most 512 decoded bytes. Only operator
group enumeration additionally accepts upstream `prefix` and `start` cursors;
unknown/duplicate/encoded keys fail. Query length is capped at 4096 bytes,
group pages at 50, other pages at 200 and page number at 1000. The synthetic
fixture's exact single-module/single-group scope remains unchanged.

Response buffering, source-generation fences, `no-store`, disabled broadcast
and WebSockets, and native mining after index refusal are retained. Chronik
does not gain validation, mempool admission or chain-selection authority.

## Public verification

The lightweight tests include 11 offline bundle checks, 41 checks against the
actual upstream Python data API, component witness rejection tests and service
oracle tests. The opcode corpus includes all 256 bytes in bounded carrier
contexts. These manually constructed API objects are not token-validity proof.

Both Linux plugin profiles build one test-only example from existing locked
dependencies. It compiles the actual bundle/query modules' 28 exact unit cases,
loads all four modules through real `PluginContext`, checks ALP/SLP dispatch,
two carriers and malformed refusals, and emits four scripts from the unchanged
upstream token builders. No new registry or dependency tuple is added.

The regtest-plugins job separately runs `tests/community-plugins/service.py`:

1. Copy only the reviewed public modules and synthetic configuration into an
   owned fixture; bind its exact configured interpreter profile.
2. Submit zero-native-value ALP and SLP GENESIS/SEND transactions plus two
   carriers. Assert exact maps, spent inputs, groups, cursor continuity,
   history and derived UTXOs via real HTTP/protobuf.
3. Positively stop/join, reopen, then retain the first stopped index and let
   upstream reconstruct a distinct new index from native accepted history.
4. Refuse unapproved configuration drift and an explicitly differently bound
   synthetic version against that index without rewriting it. Native mining
   must continue after both refusals; all owned processes are stopped.

The new fixture runs once per CI job. It does not reduce inherited repetitions,
assertions, deadlines, cleanup or any of the seven existing job profiles. The
component executable keeps the inherited 60-second process contract. Daemon
lifecycle checks retain the inherited bounded startup/stop rules. Exclusive
path-free reports are written only after all checks and cleanup succeed.

## Limits

Delivery is `active`, knowledge `Open Question`, ceiling `record-review-only`.
Commands are reproduction obligations, not a claimed daemon result. System
packages and runner images remain unlocked. There is no independent
reproduction or reproducible-build claim. Earlier timeouts, failed CI
candidates and upstream dependency alerts remain visible.

This does not qualify a production server, a deployed client, private operator
configuration, real financial transactions or the full historical projection.
Fresh installation from accepted GitHub source and separate isolated Linux
qualification remain mandatory before a reversible cutover. No deployment,
release, desktop integration, consensus activation or evidence promotion is
authorized by this change.

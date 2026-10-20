<!-- SPDX-License-Identifier: MIT -->
# Trusted operator plugin inputs — candidate contract

Delivery: **planned / local candidate**. Component observations are not a
full-daemon build, an HTTP result, indexer admission proof or deployment approval.

This adapter loads an explicitly approved local bundle through the existing
upstream `PluginContext`. It does not replace Chronik's dispatch, token engine,
plugin writers, database schema or version checks. It depends on the separately
reviewed multi-identifier loader candidate. No private plugin is included.

## Input and trust boundary

Python plugins are trusted executable operator code, not sandboxed scripts.
Review them before approval and protect the bundle from concurrent writers.
Pre/post inspection detects ordinary drift; it is not an atomic filesystem
snapshot and does not claim protection against an adversarial concurrent writer.

Use a dedicated canonical absolute directory with exactly:

- `plugins.toml`, at most 64 KiB;
- one to 32 flat ASCII-identifier `.py` plugin modules, at most 1 MiB each;
- at most 4 MiB of combined source/configuration bytes.

Only UTF-8 text without NUL is accepted. Symlinks, hard links, directories,
caches, native modules, packages and group/world-writable input files are refused
on the supported Unix profiles. Each source must correspond to exactly one
loaded plugin module. Dependencies must be embedded upstream helpers or the
configured Python standard library, not additional files in the bundle.
The Python import origin must match the inventoried source. A successful import
is followed by another exact input check.

The configured interpreter version and embedding identity participate in the
commitment. The v1 domain, length-framed fields, sorted file names and complete
raw file bytes are fixed by `ergon_operator_bundle.rs` and the offline inspector.
Neither absolute directory nor host name is included. The loader requires an
explicit, previously approved lowercase 64-character SHA-256 commitment; it
does not calculate-and-adopt an arbitrary bundle during service startup.

## Offline inspection and local regtest candidate

Obtain `PYTHON_VERSION` and `PYTHON_EMBEDDING_ID` from the exact reviewed build
profile. These placeholders are not automatically discovered from the shell.
Inspection never imports or executes a bundle's Python sources:

```sh
python3 -B tests/compatibility/upstream-chronik/operator_bundle_binding.py \
  --bundle /canonical/approved/plugin-bundle \
  --python-version PYTHON_VERSION \
  --python-id PYTHON_EMBEDDING_ID
```

The candidate service parameters are:

```text
-regtest -chronikupstreamregtest=1
-chronikupstreamregtestpluginbundle=/canonical/approved/plugin-bundle
-chronikupstreamregtestpluginbinding=APPROVED_SHA256
```

Both bundle arguments are required together and cannot be combined with the
built-in synthetic `-chronikupstreamregtestplugins` fixture. They are absent
without the compiled plugin profile. The service remains default OFF, unpruned
local regtest only, on its existing ephemeral loopback HTTP listener. No mainnet
or testnet admission guard is removed. No consensus or activation rule changes.

Synthetic and operator entry points share one process-once interpreter claim.
The owned context is shared with existing DB/worker users and outlives joined
shutdown; there is no Python finalization, reinitialization or automatic retry.
Failed index setup does not veto native transaction/block validation.

Operator commitments use a separate index identity key from synthetic fixtures.
Changed source, configuration, interpreter or fixture mode must not adopt an
existing incompatible database. The existing real indexer remains responsible
for its version checks. There is no automatic wipe/rebuild or migration of an
old operator index in this candidate.

## Required qualification before publication or server use

The component probe exercises the actual upstream context, legacy byte-returning
plugins, multi-identifier dispatch, combined-once behavior, immutable inventory,
pre-import binding refusal, shared ownership and the existing synthetic fixture.
The offline self-tests and cross-language commitment comparison are not token or
daemon evidence. Source-level disabled compilation is not a compiled-out node run.

Still required: complete reviewed source/corpus/CI integration; full enabled and
disabled daemon builds; actual service HTTP/protobuf and ALP/SLP lifecycle tests;
fresh-process restart, reorg and incompatible source/version/index admission
tests; Linux build provenance; and separately governed historical shadow service
and server validation. Preserve previous timeout counterevidence and parent gates.
No component success promotes these results to independently reproduced evidence.


## Operator HTTP capability

Only the module names actually loaded by the approved owned context enter the
HTTP capability. Existing upstream group/history/confirmed/unconfirmed/UTXO
handlers are reused. A group must be nonempty canonical lowercase hex, at most
512 decoded bytes. Unknown modules, invalid or oversized groups, percent-encoded
paths and unsupported methods/endpoints are refused. The synthetic fixture
retains its exact single-module/single-group scope; disabled mode admits no
plugin endpoint. Existing query bounds, response buffering, source-generation
fences, no-store headers and request gating remain unchanged.

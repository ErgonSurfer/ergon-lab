<!-- SPDX-License-Identifier: MIT -->
# Community Chronik plugins

Delivery: **active — under review**. Knowledge: **Open Question**.

Four maintainer-contributed plugins project protocol-shaped transactions through
Chronik's existing plugin engine. They are optional, trusted Python code, not a
sandbox, wallet, token validator, exchange or reward authority. No deployed
configuration, endpoint, operator data or unrelated application source belongs
to this package.

| Module | Class | Version | Identifiers | Projection |
| --- | --- | --- | --- | --- |
| `moom_alp_token_index` | `MoomAlpTokenIndexPlugin` | `0.1.0` | `SLP2` | ALP token, locking-script and genesis groups |
| `moom_nft_index` | `MoomNftIndexPlugin` | `0.2.0` | `SLP\x00`, `SLP2` | ALP and SLP NFT-like output candidates |
| `moom_alp_exch_market` | `MoomAlpExchMarketPlugin` | `0.2.1` | `EXCH` | SELL, PBID and PACC carrier candidates |
| `moom_reward_claim_index` | `MoomRewardClaimIndexPlugin` | `0.1.1` | `MCLM` | Structurally shaped encrypted claim carriers |

Module and class names are compatibility identifiers, not an application source
release or a recommendation to execute a transaction. They are deliberately not
renamed: clients address plugin groups by module name. Existing wire bytes and
valid-transaction projections remain unchanged. The NFT plugin registers both
ALP and SLP; neither is silently removed.

See [the projection protocols](PROTOCOLS.md) for exact groups, data layouts,
preserved edge cases and validation limits. The descriptions are an
implementation contract for these versions, **not a consensus specification**.

## Using the reviewed package

This package and the external-bundle admission adapter are reviewed together.
Wait for an accepted GitHub commit before use. The loader accepts only `plugins.toml` and its
flat Python modules, not this documentation or the test directory. Construct a
new dedicated bundle from the exact accepted files; review and bind its complete
source, configuration and interpreter before loading it. The example below is
synthetic local-regtest configuration, not a production configuration:

```toml
[regtest.plugin.moom_alp_token_index]
class = "MoomAlpTokenIndexPlugin"

[regtest.plugin.moom_nft_index]
class = "MoomNftIndexPlugin"

[regtest.plugin.moom_alp_exch_market]
class = "MoomAlpExchMarketPlugin"

[regtest.plugin.moom_reward_claim_index]
class = "MoomRewardClaimIndexPlugin"
```

Do not adopt an existing incompatible plugin database or overwrite a live
bundle. Source/version changes require a new approved binding and a fresh
separate index reconstruction, followed by projection comparisons and restart
tests. Reversibility includes preserving the old binary, index and configuration.
This package does not authorize a deployment or cutover.

## Tests and evidence boundary

`tests/community-plugins/test_protocols.py` uses the real upstream `Plugin`, `PluginOutput`,
`CScript`, transaction and token Python data types from this repository with
synthetic inputs. It performs no network access or plugin import from operator
directories. Its standalone API import is **not** embedded-interpreter dispatch,
token validity, database, HTTP, chain replay or full-node evidence.

```sh
python3 -B tests/community-plugins/test_protocols.py --upstream-source . --plugins plugins
```

The two parser patch versions reject malformed scripts without exceptions;
they do not broaden price, covenant, payout or token authority. Required before
operational use: actual upstream dispatch, database reopen/reconstruction,
HTTP/protobuf projection checks and full-history qualification of the exact
GitHub-derived build and bundle. Earlier private observations do not automatically
qualify the changed versions. Preserve all earlier timeout counterevidence.

## License and provenance

The maintainer authorized review and publication of these four plugins and their
protocol documentation on 2026-10-20 under the project's MIT contribution terms.
Their existing module/class and application-protocol identifiers are retained
openly for provenance and compatibility; this is not a renamed private codebase.
No other application component or private history is included. Upstream Chronik
API and engine notices remain in their existing source locations.

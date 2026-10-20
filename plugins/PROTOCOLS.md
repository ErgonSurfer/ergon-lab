<!-- SPDX-License-Identifier: MIT -->
# Plugin projection protocols

Knowledge: **Explainer of the candidate implementation**, not independent
reproduction or a specification of network consensus. Source of truth for each
row is the named Python module and the pinned upstream Python data API. Examples
and tests are synthetic. No operator traffic or application policy is a source.

## Common conventions

- Groups and data are byte strings. HTTP represents a group as lowercase hex.
- `||` means raw concatenation, not a separator byte.
- Token IDs in token API fields are 64-character display-order hex. These
  plugins use `bytes.fromhex(token_id)` without reversing the 32 bytes.
- Amounts from the upstream token API are integer **atoms**, never floating
  point. Display units require the token's decimals: `atoms × 10^-decimals`.
- A locking script is not a person, wallet, address count or proof of ownership.
- Chronik supplies token interpretation. These plugins do not reproduce or
  override its ALP/SLP token validation. They never affect Ergon consensus.
- The host's admitted method, endpoint, query, group-size and pagination bounds
  still apply; the plugin does not bypass them.

## ALP registry projection — `moom_alp_token_index` 0.1.0

Identifier: `SLP2`. For every ALP output with positive atoms and no mint baton,
emit at the output's original index:

| Group | Bytes |
| --- | --- |
| Token | `ALPT || token_id[32]` |
| Locking script | `ALPH || token_id[32] || complete_scriptPubKey` |
| Genesis, only when valid entry metadata is present | `ALPG || token_id[32]` |

Data begins with ASCII `atoms=<integer>` and `type=<integer>`. When a matching
non-invalid token entry supplies genesis metadata, append `ticker=<raw bytes>`,
`name=<raw bytes>`, `url=<raw bytes>` and ASCII `decimals=<integer>`.
These values are separate data entries; raw strings are not sanitized for UI
display. A consumer must escape them and must not execute or trust a URL.

The plugin skips SLP, zero amounts and mint batons. It relies on upstream output
token attachment; its own invalid-entry check excludes genesis metadata rather
than independently revalidating output tokens. Do not feed fabricated token
attachments to it as if they were validated chain data.

Summing current unspent `ALPT` output atoms gives a **current projected unspent
amount**, subject to completeness and upstream token interpretation. It is not
total minted supply or an audit of every burn. `ALPG` is genesis-output history,
not a permanently unspent registry. Counting `ALPH` groups is neither active
wallet count nor people count; stale/spent group history also matters.

## NFT-like projection — `moom_nft_index` 0.2.0

Identifiers: both `SLP\x00` and `SLP2`. An eligible output has exactly one atom,
is not a mint baton and references an existing non-invalid token entry. It must
also satisfy at least one existing heuristic:

- ALP with genesis URL starting with the literal bytes `moom://asset-profile/`;
- a non-null group-token ID;
- SLP whose numeric type is neither `1` nor `2`;
- ALP, including ordinary fungible ALP with a one-atom output.

The last condition is intentionally broad: **one atom in an output does not
prove one-supply issuance, uniqueness, NFT authenticity or creator identity**.
No off-chain profile is fetched or verified. The URL marker is a byte-prefix
classification only, not a signature or endorsement.

| Group | Bytes |
| --- | --- |
| Token candidate | `NFT || token_id[32]` |
| Script candidate | `NFTO || token_id[32] || complete_scriptPubKey` |
| Collection, when group-token ID exists | `NFTC || group_token_id[32]` |

Data contains ASCII `protocol=ALP` or `protocol=SLP`, `type=<integer>` and
`profile_asset=0` or `profile_asset=1`. If a collection exists, append
`collection=<display-order hex>`. If genesis metadata is present, append raw
`ticker=`, `name=` and `url=` entries. All outputs retain original output indices.
No NFT transfer, collection or profile authority is created by these groups.

## Exchange carriers — `moom_alp_exch_market` 0.2.1

Only output zero is parsed. Its script must start with the following cooked
upstream script elements: `OP_RETURN`, bytes `EXCH`, one-byte bytes `01`, and
bytes `SELL`, `PBID` or `PACC`. Numeric opcodes are not substituted for pushed
byte fields. Truncated pushdata and non-byte required fields are refused.

Fields below are **opaque fixed-width bytes** at this indexing layer. Numeric
and txid byte-order conventions cannot be inferred from a width check; the
consumer must apply its independently validated transaction protocol. Zero
amounts, arbitrary 33-byte pubkeys and nonexistent referenced outpoints can
still be carrier candidates. No covenant or curve-point validation is performed.

| Kind | Ordered fields after kind, with byte widths |
| --- | --- |
| SELL | lock txid 32; lock vout 4; sell atoms 8; buy sats 8; receive PKH 20; cancel PKH 20 |
| PBID | bid ID 32; token ID 32; buy atoms 8; pay sats 8; secret hash 32; buyer claim pubkey 33; buyer refund pubkey 33; buyer refund locktime 4 |
| PACC | bid ID 32; escrow txid 32; escrow vout 4; token-lock txid 32; token-lock vout 4; token ID 32; sell atoms 8; pay sats 8; secret hash 32; buyer claim pubkey 33; seller refund pubkey 33; seller claim pubkey 33; seller refund locktime 4 |

SELL requires at least these fields and at least two transaction outputs;
additional trailing script elements are ignored. PBID and PACC require exactly
their listed element counts. They attach to output one when present, otherwise
output zero; that preserved single-output behavior is not proof of a spendable
market output.

| Kind | Groups | One data entry |
| --- | --- | --- |
| SELL | `EXCH || 01` | All six listed fields, joined by byte `7c` |
| PBID | `EXCH || 01 || PBID`; `EXCT || token_id[32]` | `PBID`, bid ID, token ID, buy atoms, pay sats, secret hash, refund locktime, joined by byte `7c` |
| PACC | `EXCH || 01 || PACC`; `EXCT || token_id[32]` | `PACC`, bid ID, escrow txid/vout, token-lock txid/vout, token ID, sell atoms, pay sats, secret hash, refund locktime, joined by byte `7c` |

**Never decode this binary entry with an unconstrained `split('|')`.** Any
fixed-width field may itself contain byte `7c`. Decode using the known field
widths and verify each separator at its expected offset. Public keys checked
for width are not all retained in this projection; refetch the original tx.

A market consumer must independently validate referenced transactions, actual
ALP identity/amounts, exact scripts, spend state, amounts/fees, burn protection
and transaction authorization. No listing validity, price, settlement, refund
entitlement or terminal fill is declared by this plugin.

## Claim carriers — `moom_reward_claim_index` 0.1.1

Identifier: `MCLM`. Every output is inspected. Accepted outer forms are an
`OP_RETURN` single pushed envelope, or `OP_RETURN` pushed `MCLM` followed by a
pushed envelope. In the marker form additional script elements are ignored,
provided the whole script parses. Normal direct pushes and PUSHDATA encodings
accepted by the upstream script iterator retain their historical behavior;
minimal/canonical push encoding is not enforced. Malformed/truncated scripts
or non-byte envelopes are refused without an exception.

The envelope layout uses byte offsets starting at zero:

| Offset | Length | Meaning at this layer |
| --- | --- | --- |
| 0 | 4 | literal `MCLM` |
| 4 | 1 | version, exactly `00` |
| 5 | 1 | kind, any byte; no eligibility semantics inferred |
| 6 | 16 | opaque campaign hash |
| 22 | 16 | opaque claimant commitment |
| 38 | 12 | opaque nonce |
| 50 | 1 | ciphertext length `n`, from 1 through 80 |
| 51 | n | opaque ciphertext |
| 51+n | 16 | opaque authentication tag |

Total envelope length must be exactly `67+n`, hence 68 through 147 bytes.
The implementation's extra 223-byte ceiling applies to the envelope, not an
independent validation of the complete outer script's standardness. No hash,
commitment, nonce, ciphertext or tag is cryptographically verified by indexing.

Emit on the carrier's original output index, group bytes `MCLM || 00`, and
separate ASCII data entries `version=0` and `kind=<integer>`. This is discovery
of carrier candidates, not decryption, provider-proof validation, replay or
idempotency protection, claim approval, cap enforcement, signing or payout.
Those responsibilities are outside the node's consensus and this package.

## Verification and upgrade limits

The synthetic Python contract checks exact outputs and byte conventions,
ALP/SLP retention, field-width refusals, known permissive edge cases, malformed
scripts and all 256 opcode bytes in three carrier-prefix contexts with two
suffix lengths. It does not assert token validity from manually built data.

The 0.2.1 and 0.1.1 changes narrowly refuse formerly exceptional malformed
market/claim scripts. Original failures remain counterevidence. Valid-output
projections and module identities must remain stable. A fresh GitHub-derived
Linux build, real upstream plugin execution, fresh-process DB reopen and full
historical projection comparison are separate operational gates. Do not reuse
the prior private bundle's passing results as evidence for these new bytes.

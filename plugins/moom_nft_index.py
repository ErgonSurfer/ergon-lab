# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers

from chronik_plugin.plugin import Plugin, PluginOutput


class MoomNftIndexPlugin(Plugin):
    """Indexes MOOM profile assets and NFT-like ownership candidates.

    This is intentionally read-model only. The current MOOM posture is ALP
    native: one-supply ALP assets with a MOOM Asset Profile commitment are
    treated as NFT/profile candidates. Legacy protocol-shaped NFT entries are
    still grouped when Chronik exposes them, but no product logic depends on
    SLP retrocompat.
    """

    NFT_PREFIX = b"NFT"
    OWNER_PREFIX = b"NFTO"
    COLLECTION_PREFIX = b"NFTC"

    def lokad_id(self):
        return [b"SLP\x00", b"SLP2"]

    def version(self):
        return "0.2.0"

    def run(self, tx):
        outputs = []
        entry_by_idx = {
            idx: entry for idx, entry in enumerate(tx.token_entries)
        }
        for idx, output in enumerate(tx.outputs):
            token = output.token
            if token is None or token.is_mint_baton or token.atoms != 1:
                continue
            entry = entry_by_idx.get(token.entry_idx)
            if entry is None or entry.is_invalid:
                continue
            if not self._looks_like_profile_asset(entry):
                continue
            token_id = bytes.fromhex(token.token_id)
            groups = [
                self.NFT_PREFIX + token_id,
                self.OWNER_PREFIX + token_id + bytes(output.script),
            ]
            data = [
                b"protocol=" + token.token_protocol.encode("ascii"),
                b"type=" + str(token.token_type).encode("ascii"),
                b"profile_asset="
                + (b"1" if self._looks_like_moom_alp_profile(entry) else b"0"),
            ]
            if entry.group_token_id is not None:
                collection = bytes.fromhex(entry.group_token_id)
                groups.append(self.COLLECTION_PREFIX + collection)
                data.append(b"collection=" + entry.group_token_id.encode("ascii"))
            if entry.genesis_info is not None:
                data.extend(
                    [
                        b"ticker=" + entry.genesis_info.token_ticker,
                        b"name=" + entry.genesis_info.token_name,
                        b"url=" + entry.genesis_info.url,
                    ]
                )
            outputs.append(PluginOutput(idx=idx, groups=groups, data=data))
        return outputs

    def _looks_like_profile_asset(self, entry):
        if self._looks_like_moom_alp_profile(entry):
            return True
        if entry.group_token_id is not None:
            return True
        if entry.token_protocol == "SLP" and entry.token_type not in (1, 2):
            return True
        if entry.token_protocol == "ALP":
            return True
        return False

    def _looks_like_moom_alp_profile(self, entry):
        if entry.token_protocol != "ALP" or entry.genesis_info is None:
            return False
        url = entry.genesis_info.url or b""
        return url.startswith(b"moom://asset-profile/")

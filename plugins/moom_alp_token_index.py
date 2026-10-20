# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers

from chronik_plugin.plugin import Plugin, PluginOutput


class MoomAlpTokenIndexPlugin(Plugin):
    """Indexes ALP fungible token registry, supply, holders, and history.

    Chronik plugins expose groups/UTXOs/history; aggregate values are computed
    by clients from those read models:
    - supply: sum unspent token atoms in group b"ALPT" + token_id
    - holders: count groups with prefix b"ALPH" + token_id
    - history: query group b"ALPT" + token_id history
    """

    TOKEN_PREFIX = b"ALPT"
    HOLDER_PREFIX = b"ALPH"
    GENESIS_PREFIX = b"ALPG"

    def lokad_id(self):
        return b"SLP2"

    def version(self):
        return "0.1.0"

    def run(self, tx):
        outputs = []
        genesis_by_token_id = {}
        for entry in tx.token_entries:
            if entry.token_protocol != "ALP" or entry.is_invalid:
                continue
            if entry.genesis_info is not None:
                genesis_by_token_id[entry.token_id] = entry.genesis_info

        for idx, output in enumerate(tx.outputs):
            token = output.token
            if token is None:
                continue
            if token.token_protocol != "ALP" or token.is_mint_baton:
                continue
            if token.atoms <= 0:
                continue
            token_id = bytes.fromhex(token.token_id)
            groups = [
                self.TOKEN_PREFIX + token_id,
                self.HOLDER_PREFIX + token_id + bytes(output.script),
            ]
            data = [
                b"atoms=" + str(token.atoms).encode("ascii"),
                b"type=" + str(token.token_type).encode("ascii"),
            ]
            genesis_info = genesis_by_token_id.get(token.token_id)
            if genesis_info is not None:
                groups.append(self.GENESIS_PREFIX + token_id)
                data.extend(
                    [
                        b"ticker=" + genesis_info.token_ticker,
                        b"name=" + genesis_info.token_name,
                        b"url=" + genesis_info.url,
                        b"decimals="
                        + str(genesis_info.decimals).encode("ascii"),
                    ]
                )
            outputs.append(PluginOutput(idx=idx, groups=groups, data=data))
        return outputs

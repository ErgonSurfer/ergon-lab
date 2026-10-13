# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Public synthetic Chronik plugin. Trusted test code, never operator input.

ERGN is only a fixture LOKAD identifier, not a network/token activation.
The returned data exercises Python/native amounts, parent plugin metadata,
and actual upstream ALP token conversion without implementing token rules.
"""
from chronik_plugin.plugin import Plugin, PluginOutput


class PublicProbePlugin(Plugin):
    def lokad_id(self):
        return b"ERGN"

    def version(self):
        return "1.0.0"

    def run(self, tx):
        parents = sum("ergon_public_probe" in i.plugin for i in tx.inputs)
        return [PluginOutput(i, b"public", [b"synthetic-v1",
                    str(out.sats).encode("ascii"),
                    str(parents).encode("ascii"),
                    str(out.token.atoms if out.token else 0).encode("ascii")])
                for i, out in enumerate(tx.outputs)
                if out.script and out.script[0] != 0x6a]


class ThrowProbePlugin(PublicProbePlugin):
    def run(self, tx):
        raise ValueError("public synthetic plugin exception")


class BadOutputProbePlugin(PublicProbePlugin):
    def run(self, tx):
        return [PluginOutput(len(tx.outputs), b"public", b"invalid-index")]


class VersionTwoProbePlugin(PublicProbePlugin):
    def version(self):
        return "2.0.0"

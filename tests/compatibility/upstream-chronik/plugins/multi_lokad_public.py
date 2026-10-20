# SPDX-License-Identifier: MIT
"""Public synthetic dispatch counter; no token or business protocol logic."""
from chronik_plugin.plugin import Plugin, PluginOutput


class MultiLokadPublicPlugin(Plugin):
    def __init__(self, config):
        super().__init__(config)
        self.calls = 0

    def lokad_id(self):
        return [b"AAAA", b"BBBB"]

    def version(self):
        return "0.1.0"

    def run(self, tx):
        self.calls += 1
        return [PluginOutput(idx=1, groups=[b"public"],
                             data=[str(self.calls).encode("ascii")])]

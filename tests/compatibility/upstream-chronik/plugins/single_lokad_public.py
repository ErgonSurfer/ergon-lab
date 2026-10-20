# SPDX-License-Identifier: MIT
"""Historical bytes-returning plugin admission control."""
from chronik_plugin.plugin import Plugin, PluginOutput


class SingleLokadPublicPlugin(Plugin):
    def lokad_id(self):
        return b"CCCC"

    def version(self):
        return "0.1.0"

    def run(self, tx):
        return [PluginOutput(idx=1, groups=[b"legacy"], data=[b"unchanged"])]

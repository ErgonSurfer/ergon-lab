# SPDX-License-Identifier: MIT
"""Fail-closed synthetic plugin HTTP oracles; not native execution evidence."""
import copy
import unittest
from unittest.mock import patch
from alp_service_self_test import encode
import plugin_service as plugin

PARENT, CHILD = "01" * 32, "02" * 32


def entry(parents=0, atoms=0, name=plugin.NAME):
    data = {1: [plugin.GROUP], 2: [b"synthetic-v1", b"0", str(parents).encode(), str(atoms).encode()]}
    return encode({1: [name], 2: [encode(data)]})


def responses():
    parent = {1: [bytes.fromhex(PARENT)[::-1]],
              4: [b"", encode({6: [entry(atoms=1000)]}), encode({6: [entry()]})]}
    child = {1: [bytes.fromhex(CHILD)[::-1]],
             3: [encode({9: [entry(atoms=1000)]})],
             4: [b"", encode({6: [entry(parents=1)]})]}
    return {
        "/tx/" + PARENT: encode(parent), "/tx/" + CHILD: encode(child),
        "/plugin/ergon_public_probe/groups?page_size=10": encode({1: [encode({1: [b"public"]})]}),
        plugin.PREFIX + "history": encode({1: [encode(parent), encode(child)]}),
        plugin.PREFIX + "unconfirmed-txs": encode({1: [encode(parent), encode(child)]}),
        plugin.PREFIX + "confirmed-txs": b"",
        plugin.PREFIX + "utxos": encode({1: [encode({
            1: [encode({1: [bytes.fromhex(txid)[::-1]], 2: [index]})],
            2: [2 ** 64 - 1], 5: [b"\x51"],
            8: [entry(parents=parents)]}) for txid, index, parents in ((PARENT, 2, 0), (CHILD, 1, 1))]})}


class Contract(unittest.TestCase):
    def check(self, values):
        with patch.object(plugin.alp, "response", side_effect=lambda daemon, path: values[path]):
            plugin.check(None, PARENT, CHILD, "unconfirmed-txs", -1, None)

    def test_exact_public_parent_child_response(self):
        self.check(responses())

    def test_missing_duplicate_wrong_name_or_value_rejected(self):
        valid = {6: [entry(parents=1, atoms=1000)]}
        plugin.plugin_output(valid, 6, 1, 1000)
        for bad in ({}, {6: []}, {6: [entry(), entry()]},
                    {6: [entry(1, 1000, b"other")]},
                    {6: [entry(0, 1000)]}, {6: [entry(1, 999)]}):
            with self.assertRaises(plugin.runner.Rejected):
                plugin.plugin_output(bad, 6, 1, 1000)

    def test_transaction_parent_metadata_and_confirmation_drift(self):
        original = responses()
        value = plugin.service.fields(original["/tx/" + CHILD])
        for field, data in ((1, [bytes(32)]), (3, []), (4, []),
                            (3, [encode({9: [entry(atoms=999)]})]),
                            (8, [encode({1: [102], 2: [bytes(32)]})])):
            bad = copy.deepcopy(original)
            tx = copy.deepcopy(value)
            tx[field] = data
            bad["/tx/" + CHILD] = encode(tx)
            with self.assertRaises(plugin.runner.Rejected): self.check(bad)

    def test_group_history_phase_and_utxo_ambiguity_rejected(self):
        original = responses()
        for path in ("/plugin/ergon_public_probe/groups?page_size=10",
                     plugin.PREFIX + "history", plugin.PREFIX + "unconfirmed-txs",
                     plugin.PREFIX + "utxos"):
            bad = copy.deepcopy(original)
            bad[path] = b""
            with self.assertRaises(plugin.runner.Rejected): self.check(bad)
        for path in (plugin.PREFIX + "utxos", plugin.PREFIX + "history"):
            bad = copy.deepcopy(original)
            bad[path] += bad[path]
            with self.assertRaises(plugin.runner.Rejected): self.check(bad)
        bad = copy.deepcopy(original)
        bad[plugin.PREFIX + "confirmed-txs"] = bad[plugin.PREFIX + "history"]
        with self.assertRaises(plugin.runner.Rejected): self.check(bad)

    def test_utxo_height_value_and_script_rejected(self):
        original = responses()
        utxos = plugin.service.fields(original[plugin.PREFIX + "utxos"])
        for field, values in ((2, [0]), (3, [1]), (4, [1]), (5, [b"\x52"]), (2, [1, 1])):
            bad = copy.deepcopy(original)
            item = plugin.service.fields(utxos[1][0])
            item[field] = values
            bad[plugin.PREFIX + "utxos"] = encode({1: [encode(item), utxos[1][1]]})
            with self.assertRaises(plugin.runner.Rejected): self.check(bad)


if __name__ == "__main__":
    unittest.main()

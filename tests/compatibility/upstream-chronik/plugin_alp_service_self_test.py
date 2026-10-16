# SPDX-License-Identifier: MIT
"""Synthetic rejection oracles only; not native ALP/plugin execution evidence."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from alp_service_self_test import encode
import plugin_alp_service as combined

IDS = [f"{i:02x}" * 32 for i in range(1, 5)]


def metadata(op, color):
    return encode({1: [combined.plugin.NAME], 2: [encode({
        1: [b"public"], 2: [b"synthetic-v1", b"0", str(combined.PARENTS[op]).encode(),
                            str(color[0] if color else 0).encode()]})]})


def colored(color):
    return encode({1: [IDS[0].encode()], 2: [encode({2: [0]})],
                   3: [2 ** 64 - 1], 4: [color[0]], 5: [int(color[1])]})


def responses(height=-1, block_hash=None):
    txs = []
    for op, txid in enumerate(IDS):
        inputs = []
        for point in combined.INPUTS[op]:
            if point is None:
                inputs.append(b"")
            else:
                parent, index = point
                inputs.append(encode({1: [encode({1: [bytes.fromhex(IDS[parent])[::-1]], 2: [index]})],
                                      9: [metadata(parent, combined.alp.COLORS[parent][1][index])]}))
        tx = {1: [bytes.fromhex(txid)[::-1]], 3: inputs,
              4: [b"" if i == 0 else encode({6: [metadata(op, color)]})
                  for i, color in enumerate(combined.alp.COLORS[op][1])]}
        if height >= 0:
            tx[8] = [encode({1: [height], 2: [bytes.fromhex(block_hash)[::-1]]})]
        txs.append(encode(tx))
    page = encode({1: txs, 2: [1], 3: [4]})
    result = {"/tx/" + txid: raw for txid, raw in zip(IDS, txs)}
    result["/plugin/ergon_public_probe/groups?page_size=10"] = encode({1: [encode({1: [b"public"]})]})
    for phase, present in (("history", True), ("confirmed-txs", height >= 0), ("unconfirmed-txs", height < 0)):
        result[combined.plugin.PREFIX + phase] = page if present else b""
    utxos = []
    for op, index in combined.UNSPENT:
        color = combined.alp.COLORS[op][1][index]
        value = {1: [encode({1: [bytes.fromhex(IDS[op])[::-1]], 2: [index]})],
                 2: [height if height >= 0 else 2 ** 64 - 1], 5: [b"\x51"], 8: [metadata(op, color)]}
        if color is not None: value[7] = [colored(color)]
        utxos.append(encode(value))
    result[combined.plugin.PREFIX + "utxos"] = encode({1: utxos})
    return result


class Contract(unittest.TestCase):
    def check(self, values, height=-1, block_hash=None):
        with patch.object(combined.alp, "response", side_effect=lambda daemon, path: values[path]):
            return combined.check_plugin_snapshot(None, IDS, height, block_hash)

    def test_four_operations_pending_confirmed_and_stable_digest(self):
        first = self.check(responses())
        self.assertEqual(first, self.check(responses()))
        block = "05" * 32
        self.assertNotEqual(first, self.check(responses(102, block), 102, block))

    def test_genesis_mint_send_burn_output_metadata_rejections(self):
        for op in range(4):
            path = "/tx/" + IDS[op]
            for replacement in (b"", encode({6: [metadata(op, (999, False))]}),
                                encode({6: [metadata((op + 1) % 4, combined.alp.COLORS[op][1][1])]})):
                values = responses()
                tx = combined.service.fields(values[path])
                tx[4][1] = replacement
                values[path] = encode(tx)
                with self.assertRaises(combined.runner.Rejected): self.check(values)

    def test_input_ancestry_and_parent_plugin_metadata_rejections(self):
        for op in range(1, 4):
            for field, replacement in ((1, [b""]), (9, []), (9, [metadata(op, (999, False))])):
                values = responses()
                path = "/tx/" + IDS[op]
                tx = combined.service.fields(values[path])
                inp = combined.service.fields(tx[3][0])
                inp[field] = replacement
                tx[3][0] = encode(inp)
                values[path] = encode(tx)
                with self.assertRaises(combined.runner.Rejected): self.check(values)

    def test_history_groups_and_page_count_rejections(self):
        paths = ["/plugin/ergon_public_probe/groups?page_size=10"] + [
            combined.plugin.PREFIX + p for p in ("history", "unconfirmed-txs", "utxos")]
        for path in paths:
            values = responses(); values[path] = b""
            with self.assertRaises(combined.runner.Rejected): self.check(values)
        for field in (2, 3):
            values = responses(); path = combined.plugin.PREFIX + "history"
            page = combined.service.fields(values[path]); page[field] = [0]; values[path] = encode(page)
            with self.assertRaises(combined.runner.Rejected): self.check(values)
        values = responses()
        values[combined.plugin.PREFIX + "confirmed-txs"] = values[combined.plugin.PREFIX + "history"]
        with self.assertRaises(combined.runner.Rejected): self.check(values)

    def test_utxo_baton_atoms_heights_duplicates_and_non_token_rejections(self):
        path = combined.plugin.PREFIX + "utxos"
        for index in range(3):
            for field, replacement in ((8, []), (5, [b"\x52"]), (3, [1]), (2, [0]),
                                       (7, [colored((999, False))])):
                values = responses(); utxos = combined.service.fields(values[path])
                item = combined.service.fields(utxos[1][index]); item[field] = replacement
                utxos[1][index] = encode(item); values[path] = encode(utxos)
                with self.assertRaises(combined.runner.Rejected): self.check(values)
        for mutate in (lambda u: u[1].pop(), lambda u: u[1].append(u[1][0])):
            values = responses(); utxos = combined.service.fields(values[path]); mutate(utxos)
            values[path] = encode(utxos)
            with self.assertRaises(combined.runner.Rejected): self.check(values)

    def test_confirmation_identity_and_unexpected_return_metadata_rejections(self):
        for mutate in (lambda t: t.update({1: [bytes(32)]}),
                       lambda t: t.update({8: [encode({1: [102], 2: [bytes(32)]})]}),
                       lambda t: t[4].__setitem__(0, encode({6: [metadata(0, None)]})),
                       lambda t: t[3].__setitem__(0, encode({9: [metadata(0, None)]}))):
            values = responses(); path = "/tx/" + IDS[0]
            tx = combined.service.fields(values[path]); mutate(tx); values[path] = encode(tx)
            with self.assertRaises(combined.runner.Rejected): self.check(values)

    def test_both_existing_ALP_and_plugin_oracles_are_required(self):
        with patch.object(combined.alp, "check_snapshot", return_value="alp") as a, \
             patch.object(combined, "check_plugin_snapshot", return_value="plugin") as p:
            combined.snapshot(None, IDS, (), (), -1, None)
            a.assert_called_once(); p.assert_called_once()
        for name, module in (("check_snapshot", combined.alp), ("check_plugin_snapshot", combined)):
            with patch.object(combined.alp, "check_snapshot", return_value="alp"), \
                 patch.object(combined, "check_plugin_snapshot", return_value="plugin"), \
                 patch.object(module, name, side_effect=combined.runner.Rejected("synthetic refusal")), \
                 self.assertRaises(combined.runner.Rejected):
                combined.snapshot(None, IDS, (), (), -1, None)

    def test_cleanup_on_startup_failure_with_plugin_explicitly_on(self):
        created = []
        class Daemon:
            def __init__(self, *args, public_plugin=False):
                self.plugin = public_plugin; self.cleaned = False; created.append(self)
            def ready(self): raise RuntimeError("synthetic refusal")
            def cleanup(self): self.cleaned = True
        with tempfile.TemporaryDirectory(prefix="synthetic-plugin-alp-") as directory:
            work = Path(directory).resolve()
            with patch.object(combined.service, "Daemon", Daemon), self.assertRaises(RuntimeError):
                combined.observe(Path("synthetic-binary"), work, ())
            self.assertTrue(created[0].plugin and created[0].cleaned)
            self.assertEqual(list(work.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reject broadening service admission, source drift and weakened read/owner fences."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_native_startup as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_layer(self):
        self.assertEqual(gate.validate(self.root), gate.CHANGED)

    def test_contract_and_inventory(self):
        original = gate.load_json
        manifest = original(self.root / gate.MANIFEST)
        for mutate in (lambda d: d["contract"].update(maximum_height=2000000),
                       lambda d: d["contract"].update(network="mainnet"),
                       lambda d: d["contract"].update(knowledge_status="Reproduced"),
                       lambda d: d["contract"]["unsupported"].remove("pruning"),
                       lambda d: d["files"].pop(),
                       lambda d: d["files"][0]["after"].update(sha256="0" * 64)):
            bad = copy.deepcopy(manifest); mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda path:
                    bad if path == self.root / gate.MANIFEST else original(path)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_read_and_owned_contracts(self):
        path = self.root / gate.BRIDGE
        original = path.read_text()
        read = Path.read_text
        for before, after in (("copied < BATCH_LIMIT", "copied < INDEX_LIMIT"),
                              ("    m_startup.reset();", "    // retain"),
                              ("m_node.permits_index_count(mapBlockIndex.size())", "true"),
                              ("LastCommonAncestor(&index, m_native_startup->tip)", "ChainActive().FindFork(&index)"),
                              ("    CheckStorage(index, snapshot);", "    // skip storage recheck")):
            bad = original.replace(before, after)
            self.assertNotEqual(bad, original)
            with patch.object(Path, "read_text", autospec=True, side_effect=lambda p, *a, **kw:
                    bad if p == path else read(p, *a, **kw)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_unknown_scope_and_successor_preimage(self):
        original = gate.git
        with patch.object(gate, "git", side_effect=lambda root, *args:
                original(root, *args) + b"unknown.py\0" if args[0] == "ls-files" else original(root, *args)), \
                self.assertRaises(ValueError):
            gate.validate(self.root)
        before = gate.before_identity(self.root, gate.BRIDGE)
        self.assertEqual(gate.successor_identity(self.root, gate.BRIDGE, before),
                         gate.current_identity(self.root, gate.BRIDGE))
        with self.assertRaises(ValueError):
            gate.successor_identity(self.root, gate.BRIDGE, dict(before, sha256="0" * 64))


if __name__ == "__main__":
    unittest.main()

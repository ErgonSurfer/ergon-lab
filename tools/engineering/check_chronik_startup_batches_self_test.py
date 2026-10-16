#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Reject changed batching bounds, missing fences, unknown scope and proof drift."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_startup_batches as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_layer(self):
        self.assertEqual(gate.validate(self.root), gate.CHANGED)

    def test_manifest_contract_and_inventory(self):
        original = gate.load_json
        manifest = original(self.root / gate.MANIFEST)
        for mutate in (
                lambda d: d["contract"].update(metadata_rows_per_lock=4096),
                lambda d: d["contract"].update(network="mainnet"),
                lambda d: d["contract"].update(final_capture_fence=False),
                lambda d: d["contract"].update(knowledge_status="Reproduced"),
                lambda d: d["files"].pop(),
                lambda d: d["files"][0]["after"].update(sha256="0" * 64)):
            bad = copy.deepcopy(manifest); mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda path:
                    bad if path == self.root / gate.MANIFEST else original(path)), \
                    self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_source_and_final_fence(self):
        read_text = Path.read_text
        path = self.root / gate.BRIDGE
        original = path.read_text()
        for before, after in (
                ("copied < BATCH_LIMIT", "copied < INDEX_LIMIT"),
                ("mapBlockIndex.bucket_count() == bucket_count", "true"),
                ("m_node.mempool.GetSourceGeneration() == owned->revision", "true"),
                ("if (!inserted.second)", "if (false)"),
                ("    m_startup.reset();", "    // retain graph")):
            bad = original.replace(before, after)
            self.assertNotEqual(bad, original)
            with patch.object(Path, "read_text", autospec=True,
                    side_effect=lambda item, *a, **kw: bad if item == path else read_text(item, *a, **kw)), \
                    self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_unknown_scope_and_successor_preimage(self):
        original = gate.git
        with patch.object(gate, "git", side_effect=lambda root, *args:
                original(root, *args) + b"unlisted.py\0" if args[0] == "ls-files"
                else original(root, *args)), self.assertRaises(ValueError):
            gate.validate(self.root)
        path = gate.BRIDGE
        before = gate.before_identity(self.root, path)
        self.assertEqual(gate.successor_identity(self.root, path, before), gate.current_identity(self.root, path))
        with self.assertRaises(ValueError):
            gate.successor_identity(self.root, path, dict(before, sha256="0" * 64))


if __name__ == "__main__":
    unittest.main()

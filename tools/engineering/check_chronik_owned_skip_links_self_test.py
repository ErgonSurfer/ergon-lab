# SPDX-License-Identifier: MIT
"""Reject owned skip-link scope, source, lifetime and proof drift."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_owned_skip_links as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_layer_and_binding(self):
        self.assertEqual(gate.validate(self.root), gate.CHANGED)

    def test_contract_inventory_and_privilege_rejections(self):
        original = gate.load_json
        manifest = original(self.root / gate.MANIFEST)
        for mutate in (
                lambda row: row["contract"].update(runtime_default=True),
                lambda row: row["contract"].update(network="mainnet"),
                lambda row: row["contract"].update(owner_lifetime="released-after-resync"),
                lambda row: row["contract"].update(limit_change=True),
                lambda row: row["contract"].update(automatic_retry=True),
                lambda row: row["contract"].update(knowledge_status="Reproduced"),
                lambda row: row["files"].pop(),
                lambda row: row["files"].reverse(),
                lambda row: row["files"][0]["after"].update(sha256="0" * 64)):
            bad = copy.deepcopy(manifest); mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda path:
                bad if path == self.root / gate.MANIFEST else original(path)), \
                    self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_frozen_inputs_record_and_unlisted_scope_rejected(self):
        original = gate.current_identity
        for path in ("chronik/chronik-indexer/src/indexer.rs", "src/chain.cpp",
                     "tests/compatibility/upstream-chronik/run_repetitions.py",
                     *sorted(gate.MANIFEST_FILES)):
            with patch.object(gate, "current_identity", side_effect=lambda root, name:
                dict(original(root, name), sha256="0" * 64)
                if name == path else original(root, name)), self.assertRaises(ValueError):
                gate.validate(self.root)
        original_json = gate.load_json
        record = original_json(self.root / gate.RECORD)
        for mutate in (lambda row: row["evidence"].update(artifacts=["synthetic.json"]),
                       lambda row: row["evidence"].update(knowledge_status="Reproduced"),
                       lambda row: row["files"].pop()):
            bad = copy.deepcopy(record); mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda path:
                bad if path == self.root / gate.RECORD else original_json(path)), \
                    self.assertRaises(ValueError):
                gate.validate(self.root)
        original_git = gate.git
        with patch.object(gate, "git", side_effect=lambda root, *args:
            original_git(root, *args) + b"unlisted.py\0"
            if args[0] == "ls-files" else original_git(root, *args)), \
                self.assertRaises(ValueError):
            gate.validate(self.root)

    def test_skip_oracle_ordering_and_lifetime_rejected(self):
        read_text = Path.read_text
        bridge = self.root / "chronik/chronik-cpp/chronik_bridge.cpp"
        native = self.root / "src/test/chronik_upstream_archival_tests.cpp"
        original_bridge, original_native = bridge.read_text(), native.read_text()
        mutations = (
            (bridge, original_bridge.replace("index->BuildSkip();", "index->pskip = nullptr;")),
            (bridge, original_bridge.replace(
                "    std::sort(by_height.begin(), by_height.end(),", "    // no height order\n    std::sort(by_height.end(), by_height.end(),")),
            (bridge, original_bridge.replace(
                "    // Keep the graph alive until bridge destruction, including exception\n",
                "    m_startup.reset();\n")),
            (native, original_native.replace(
                "copy.pskip->GetBlockHash() ==", "copy.GetBlockHash() ==")),
        )
        for path, bad in mutations:
            with patch.object(Path, "read_text", autospec=True,
                    side_effect=lambda item, *args, _path=path, _bad=bad, **kwargs:
                    _bad if item == _path else read_text(item, *args, **kwargs)), \
                    self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_successor_requires_exact_preimage(self):
        path = sorted(gate.MANIFEST_FILES)[0]
        before = gate.before_identity(self.root, path)
        self.assertEqual(gate.successor_identity(self.root, path, before),
                         gate.current_identity(self.root, path))
        with self.assertRaises(ValueError):
            gate.successor_identity(self.root, path, dict(before, sha256="0" * 64))


if __name__ == "__main__":
    unittest.main()

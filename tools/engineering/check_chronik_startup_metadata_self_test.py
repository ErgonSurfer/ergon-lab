# SPDX-License-Identifier: MIT
"""Reject startup metadata scope, source, proof and inherited-control drift."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_startup_metadata as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_layer_and_binding(self):
        self.assertEqual(gate.validate(self.root), gate.TECHNICAL | gate.POLICY)
        self.assertEqual(gate.input_binding(self.root), gate.input_binding(self.root))

    def test_contract_inventory_and_privilege_rejections(self):
        original = gate.load_json
        manifest = original(self.root / gate.MANIFEST)
        for mutate in (lambda row: row["contract"].update(compile_default=True),
                       lambda row: row["contract"].update(network="mainnet"),
                       lambda row: row["contract"].update(maximum_height=2001),
                       lambda row: row["contract"].update(native_timeout_seconds=120),
                       lambda row: row["contract"].update(automatic_retry=True),
                       lambda row: row["contract"].update(upstream_engine_change=True),
                       lambda row: row["contract"].update(knowledge_status="Reproduced"),
                       lambda row: row["files"].pop(), lambda row: row["files"].reverse(),
                       lambda row: row["files"][0]["after"].update(sha256="0" * 64)):
            bad = copy.deepcopy(manifest); mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda path:
                bad if path == self.root / gate.MANIFEST else original(path)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_frozen_engines_record_and_unlisted_scope_rejected(self):
        original = gate.current_identity
        for path in ("chronik/chronik-indexer/src/indexer.rs", "chronik/Cargo.lock",
                     "src/validation.cpp", *sorted(gate.TECHNICAL)):
            with patch.object(gate, "current_identity", side_effect=lambda root, name:
                dict(original(root, name), sha256="0" * 64) if name == path else original(root, name)), self.assertRaises(ValueError):
                gate.validate(self.root)
        original_json = gate.load_json
        record = original_json(self.root / gate.RECORD)
        for mutate in (lambda row: row["evidence"].update(artifacts=["synthetic.json"]),
                       lambda row: row["evidence"].update(knowledge_status="Reproduced"),
                       lambda row: row["files"].pop()):
            bad = copy.deepcopy(record); mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda path:
                bad if path == self.root / gate.RECORD else original_json(path)), self.assertRaises(ValueError):
                gate.validate(self.root)
        original_git = gate.git
        with patch.object(gate, "git", side_effect=lambda root, *args:
            original_git(root, *args) + b"unlisted.py\0" if args[0] == "ls-files" else original_git(root, *args)), self.assertRaises(ValueError):
            gate.validate(self.root)

    def test_inherited_native_body_runner_and_ordering_rejected(self):
        native = self.root / "src/test/chronik_upstream_archival_tests.cpp"
        original = native.read_text()
        read_text = Path.read_text
        inherited = next(iter(gate.native_cases(gate.git(self.root, "show", gate.PARENT + ":src/test/chronik_upstream_archival_tests.cpp").decode())))
        with patch.object(Path, "read_text", autospec=True, side_effect=lambda path, *args, **kwargs:
            original.replace("BOOST_AUTO_TEST_CASE(" + inherited + ") {", "BOOST_AUTO_TEST_CASE(" + inherited + ") {\n    ;") if path == native else read_text(path, *args, **kwargs)), self.assertRaises(ValueError):
            gate.validate(self.root)
        bridge = self.root / "chronik/chronik-cpp/chronik_bridge.cpp"
        original_bridge = bridge.read_text()
        callback = "    if (after_metadata)\n        after_metadata();\n"
        late_callback = original_bridge.replace(callback, "").replace(
            "    m_startup = std::move(owned);", callback + "    m_startup = std::move(owned);")
        with patch.object(Path, "read_text", autospec=True, side_effect=lambda path, *args, **kwargs:
            late_callback if path == bridge else read_text(path, *args, **kwargs)), self.assertRaises(ValueError):
            gate.validate(self.root)

    def test_successor_requires_exact_preimage(self):
        path = sorted(gate.COMPILED)[0]
        before = gate.before_identity(self.root, path)
        self.assertEqual(gate.successor_identity(self.root, path, before), gate.current_identity(self.root, path))
        with self.assertRaises(ValueError):
            gate.successor_identity(self.root, path, dict(before, sha256="0" * 64))


if __name__ == "__main__":
    unittest.main()

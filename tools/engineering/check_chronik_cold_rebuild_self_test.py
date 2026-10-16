# SPDX-License-Identifier: MIT
"""Reject cold test-layer scope, evidence and frozen-input contradictions."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_cold_rebuild as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_layer_and_input_binding(self):
        self.assertEqual(gate.validate(self.root), gate.TECHNICAL | gate.POLICY)
        self.assertEqual(gate.input_binding(self.root), gate.input_binding(self.root))
        original = gate.current_identity
        bound = gate.input_binding(self.root)
        with patch.object(gate, "current_identity", side_effect=lambda r, p:
            dict(original(r, p), sha256="0" * 64) if p in gate.TECHNICAL else original(r, p)):
            self.assertNotEqual(gate.input_binding(self.root), bound)

    def test_contract_inventory_and_privilege_rejections(self):
        original = gate.load_json
        manifest = original(self.root / gate.MANIFEST)
        for mutate in (lambda r: r["contract"].update(compile_default=True),
                       lambda r: r["contract"].update(arbitrary_plugins=True),
                       lambda r: r["contract"].update(network="mainnet"),
                       lambda r: r["contract"].update(maximum_height=2001),
                       lambda r: r["contract"].update(historical_first_seen="fabricated"),
                       lambda r: r["contract"].update(automatic_retry=True),
                       lambda r: r["contract"].update(template_assertions=3),
                       lambda r: r["contract"].update(knowledge_status="Reproduced"),
                       lambda r: r["public_parent"].update(commit="0" * 40),
                       lambda r: r["files"].pop(), lambda r: r["files"].reverse(),
                       lambda r: r["files"][0]["after"].update(mode="100755"),
                       lambda r: r["files"][0]["after"].update(sha256="0" * 64)):
            bad = copy.deepcopy(manifest); mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p:
                bad if p == self.root / gate.MANIFEST else original(p)), self.assertRaises(ValueError): gate.validate(self.root)

    def test_frozen_compiled_inputs_oracles_history_and_new_source_rejected(self):
        original = gate.current_identity
        for path in ("src/init.cpp", "chronik/Cargo.lock", "chronik/chronik-cpp/chronik_bridge.cpp",
                     "chronik/chronik-indexer/src/indexer.rs", "docs/engineering/changes/ergon-change-0055.json",
                     "tests/compatibility/upstream-chronik/plugin_alp_service.py", *sorted(gate.TECHNICAL)):
            with patch.object(gate, "current_identity", side_effect=lambda r, p:
                dict(original(r, p), sha256="0" * 64) if p == path else original(r, p)), self.assertRaises(ValueError): gate.validate(self.root)

    def test_record_proof_promotion_and_inventory_rejections(self):
        original = gate.load_json
        for mutate in (lambda r: r["evidence"].update(artifacts=["synthetic.json"]),
                       lambda r: r["evidence"].update(knowledge_status="Reproduced"),
                       lambda r: r["provenance_inventory"].update(inventory_sha256="0" * 64),
                       lambda r: r["files"].pop()):
            bad = original(self.root / gate.RECORD); mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p:
                bad if p == self.root / gate.RECORD else original(p)), self.assertRaises(ValueError): gate.validate(self.root)

    def test_unlisted_scope_and_mutable_parent_rejected(self):
        original = gate.git
        with patch.object(gate, "git", side_effect=lambda r, *a:
            original(r, *a) + b"unlisted.py\0" if a[0] == "ls-files" else original(r, *a)), self.assertRaises(ValueError): gate.validate(self.root)
        with patch.object(gate, "TREE", "0" * 40), self.assertRaises(ValueError): gate.validate(self.root)

    def test_historical_catchup_cannot_hide_new_scope_or_changed_compiled_bytes(self):
        import check_chronik_service_catchup as previous
        self.assertEqual(previous.validate(self.root), previous.TECHNICAL | previous.POLICY | gate.TECHNICAL | gate.POLICY)
        original = previous.current_identity
        path = sorted(previous.COMPILED)[0]
        with patch.object(previous, "current_identity", side_effect=lambda r, p:
            dict(original(r, p), sha256="0" * 64) if p == path else original(r, p)), self.assertRaises(ValueError): previous.validate(self.root)


if __name__ == "__main__":
    unittest.main()

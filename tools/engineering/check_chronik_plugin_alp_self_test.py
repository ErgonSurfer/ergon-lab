# SPDX-License-Identifier: MIT
"""Reject combined client scope, evidence, identity and frozen-input drift."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_plugin_alp as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_test_only_layer(self):
        from check_chronik_service_catchup import TECHNICAL as CATCHUP_TECHNICAL, POLICY as CATCHUP_POLICY
        from check_chronik_cold_rebuild import TECHNICAL as COLD_TECHNICAL, POLICY as COLD_POLICY
        from check_chronik_startup_metadata import TECHNICAL as METADATA_TECHNICAL, POLICY as METADATA_POLICY
        self.assertEqual(gate.validate(self.root), gate.TECHNICAL | gate.POLICY | CATCHUP_TECHNICAL | CATCHUP_POLICY | COLD_TECHNICAL | COLD_POLICY | METADATA_TECHNICAL | METADATA_POLICY)
        self.assertEqual(gate.input_binding(self.root), gate.input_binding(self.root))

    def test_contract_inventory_and_new_file_rejections(self):
        original = gate.load_json
        manifest = original(self.root / gate.MANIFEST)
        for mutate in (lambda r: r["contract"].update(compile_default=True),
                       lambda r: r["contract"].update(arbitrary_plugins=True),
                       lambda r: r["contract"].update(template_assertions=3),
                       lambda r: r["contract"].update(knowledge_status="Reproduced"),
                       lambda r: r["contract"].update(network="mainnet"),
                       lambda r: r["public_parent"].update(commit="0" * 40),
                       lambda r: r["files"].pop(), lambda r: r["files"].reverse(),
                       lambda r: r["files"][0]["after"].update(sha256="0" * 64)):
            bad = copy.deepcopy(manifest); mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p:
                bad if p == self.root / gate.MANIFEST else original(p)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_frozen_engine_oracle_record_and_binding_rejections(self):
        original = gate.current_identity
        for path in ("src/init.cpp", "chronik/Cargo.lock", "chronik/chronik-indexer/src/indexer.rs",
                     "tests/compatibility/upstream-chronik/alp_service.py",
                     "tests/compatibility/upstream-chronik/plugin_service.py",
                     "docs/engineering/changes/ergon-change-0053.json"):
            with patch.object(gate, "current_identity", side_effect=lambda r, p:
                dict(original(r, p), sha256="0" * 64) if p == path else original(r, p)), self.assertRaises(ValueError):
                gate.validate(self.root)
        bound = gate.input_binding(self.root)
        with patch.object(gate, "current_identity", side_effect=lambda r, p:
            dict(original(r, p), sha256="0" * 64) if p in gate.TECHNICAL else original(r, p)):
            self.assertNotEqual(gate.input_binding(self.root), bound)

    def test_record_rejections(self):
        original = gate.load_json
        for mutate in (lambda r: r["evidence"].update(artifacts=["synthetic.json"]),
                       lambda r: r["evidence"].update(knowledge_status="Reproduced"),
                       lambda r: r["provenance_inventory"].update(inventory_sha256="0" * 64),
                       lambda r: r["files"].pop()):
            bad = original(self.root / gate.RECORD); mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p:
                bad if p == self.root / gate.RECORD else original(p)), self.assertRaises(ValueError):
                gate.validate(self.root)


if __name__ == "__main__":
    unittest.main()

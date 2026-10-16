# SPDX-License-Identifier: MIT
"""Reject ALP scope, proof, production and finite-inventory contradictions."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_service_alp as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_finite_test_only_layer(self):
        from check_chronik_service_plugins import TECHNICAL, POLICY
        expected = gate.TECHNICAL | gate.POLICY | TECHNICAL | POLICY
        from check_chronik_plugin_alp import TECHNICAL, POLICY
        from check_chronik_service_catchup import TECHNICAL as CATCHUP_TECHNICAL, POLICY as CATCHUP_POLICY
        from check_chronik_cold_rebuild import TECHNICAL as COLD_TECHNICAL, POLICY as COLD_POLICY
        self.assertEqual(gate.validate(self.root), expected | TECHNICAL | POLICY | CATCHUP_TECHNICAL | CATCHUP_POLICY | COLD_TECHNICAL | COLD_POLICY)

    def test_contract_and_inventory_rejections(self):
        original = gate.load_json
        record = original(self.root / gate.MANIFEST)
        for mutate in (lambda r: r["contract"].update(compile_default=True),
                       lambda r: r["contract"].update(plugins=True),
                       lambda r: r["contract"].update(network="mainnet"),
                       lambda r: r["contract"].update(consensus_change=True),
                       lambda r: r["contract"].update(template_assertions=3),
                       lambda r: r["contract"].update(retained_stop_timeout="fixed"),
                       lambda r: r["contract"].update(knowledge_status="Reproduced"),
                       lambda r: r["files"].pop(), lambda r: r["files"].reverse(),
                       lambda r: r["files"][0]["after"].update(sha256="0" * 64)):
            invalid = copy.deepcopy(record)
            mutate(invalid)
            with patch.object(gate, "load_json", side_effect=lambda p:
                invalid if p == self.root / gate.MANIFEST else original(p)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_frozen_production_and_history_rejections(self):
        original = gate.current_identity
        for path in ("src/validation.cpp", "chronik/Cargo.lock",
                     "chronik/chronik-indexer/src/indexer.rs",
                     "chronik/bitcoinsuite-slp/src/alp/build.rs",
                     "docs/engineering/changes/ergon-change-0051.json"):
            with patch.object(gate, "current_identity", side_effect=lambda r, p:
                dict(original(r, p), sha256="0" * 64) if p == path else original(r, p)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_record_rejections(self):
        original = gate.load_json
        for mutate in (lambda r: r["evidence"].update(artifacts=["synthetic.json"]),
                       lambda r: r["evidence"].update(knowledge_status="Reproduced"),
                       lambda r: r["provenance_inventory"].update(inventory_sha256="0" * 64),
                       lambda r: r["files"].pop()):
            invalid = original(self.root / gate.RECORD)
            mutate(invalid)
            with patch.object(gate, "load_json", side_effect=lambda p:
                invalid if p == self.root / gate.RECORD else original(p)), self.assertRaises(ValueError):
                gate.validate(self.root)


if __name__ == "__main__":
    unittest.main()

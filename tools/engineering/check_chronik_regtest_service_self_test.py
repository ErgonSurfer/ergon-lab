# SPDX-License-Identifier: MIT
"""Reject regtest service boundary, byte, scope and evidence contradictions."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_regtest_service as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_finite_layer(self):
        from check_chronik_service_recovery import TECHNICAL, POLICY
        expected = gate.TECHNICAL | gate.POLICY | TECHNICAL | POLICY
        from check_chronik_service_startup import TECHNICAL, POLICY
        self.assertEqual(gate.validate(self.root), expected | TECHNICAL | POLICY)

    def test_contract_and_inventory_drift(self):
        original = gate.load_json
        record = original(self.root / gate.MANIFEST)
        changes = (lambda r: r["contract"].update(compile_default=True),
                   lambda r: r["contract"].update(runtime_default=True),
                   lambda r: r["contract"].update(plugins=True),
                   lambda r: r["contract"].update(network="mainnet"),
                   lambda r: r["contract"].update(listen="0.0.0.0"),
                   lambda r: r["contract"].update(knowledge_status="Reproduced"),
                   lambda r: r["contract"].update(incoming_evidence=["synthetic.json"]),
                   lambda r: r["files"].pop(), lambda r: r["files"].reverse(),
                   lambda r: r["files"][0]["after"].update(sha256="0" * 64))
        for mutate in changes:
            bad = copy.deepcopy(record)
            mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p:
                bad if p == self.root / gate.MANIFEST else original(p)):
                with self.assertRaises(ValueError):
                    gate.validate(self.root)

    def test_frozen_consensus_dependencies_and_old_record(self):
        original = gate.current_identity
        for target in ("src/validation.cpp", "chronik/Cargo.lock",
                       "chronik/chronik-cpp/ergon_node_access.h",
                       "docs/engineering/changes/ergon-change-0048.json"):
            with patch.object(gate, "current_identity", side_effect=lambda r, p:
                dict(original(r, p), sha256="0" * 64) if p == target else original(r, p)):
                with self.assertRaises(ValueError):
                    gate.validate(self.root)

    def test_governance_proof_and_scope_rejections(self):
        original = gate.load_json
        record = original(self.root / gate.RECORD)
        for mutate in (lambda r: r["evidence"].update(knowledge_status="Reproduced"),
                       lambda r: r["evidence"].update(artifacts=["synthetic.json"]),
                       lambda r: r["files"].pop()):
            bad = copy.deepcopy(record)
            mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p:
                bad if p == self.root / gate.RECORD else original(p)):
                with self.assertRaises(ValueError):
                    gate.validate(self.root)


if __name__ == "__main__":
    unittest.main()

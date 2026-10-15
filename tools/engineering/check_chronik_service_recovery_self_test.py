# SPDX-License-Identifier: MIT
"""Reject recovery scope, lease, proof and exact-inventory contradictions."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_service_recovery as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_finite_layer(self):
        from check_chronik_service_startup import TECHNICAL, POLICY
        expected = gate.TECHNICAL | gate.POLICY | TECHNICAL | POLICY
        from check_chronik_service_alp import TECHNICAL, POLICY
        self.assertEqual(gate.validate(self.root), expected | TECHNICAL | POLICY)

    def test_contract_and_inventory_rejections(self):
        original = gate.load_json
        record = original(self.root / gate.MANIFEST)
        for mutate in (lambda r: r["contract"].update(database_wipe=True),
                       lambda r: r["contract"].update(identity_rewrite=True),
                       lambda r: r["contract"].update(plugins=True),
                       lambda r: r["contract"].update(default_off=False),
                       lambda r: r["contract"].update(knowledge_status="Reproduced"),
                       lambda r: r["contract"].update(incoming_evidence=["synthetic.json"]),
                       lambda r: r["contract"]["native_lease_cases"][0].__setitem__(1, 8),
                       lambda r: r["files"].pop(), lambda r: r["files"].reverse(),
                       lambda r: r["files"][0]["after"].update(sha256="0" * 64)):
            bad = copy.deepcopy(record)
            mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p:
                bad if p == self.root / gate.MANIFEST else original(p)):
                with self.assertRaises(ValueError): gate.validate(self.root)

    def test_frozen_input_rejections(self):
        original = gate.current_identity
        for path in ("CMakeLists.txt", "src/validation.cpp", "chronik/Cargo.lock",
                     "docs/engineering/changes/ergon-change-0049.json"):
            with patch.object(gate, "current_identity", side_effect=lambda r, p:
                dict(original(r, p), sha256="0" * 64) if p == path else original(r, p)):
                with self.assertRaises(ValueError): gate.validate(self.root)

    def test_record_rejections(self):
        original = gate.load_json
        for mutate in (lambda r: r["evidence"].update(artifacts=["synthetic.json"]),
                       lambda r: r["evidence"].update(knowledge_status="Reproduced"),
                       lambda r: r["files"].pop()):
            bad = original(self.root / gate.RECORD)
            mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p:
                bad if p == self.root / gate.RECORD else original(p)):
                with self.assertRaises(ValueError): gate.validate(self.root)


if __name__ == "__main__":
    unittest.main()

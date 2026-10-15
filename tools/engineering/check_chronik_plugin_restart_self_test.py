# SPDX-License-Identifier: MIT
"""Public restart source/proof boundary rejection tests only."""
import copy
import ast
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_plugin_restart as gate


class Contract(unittest.TestCase):
    def test_exact_phase_case_and_assertion_law(self):
        valid = gate.EXPECTED_PHASES
        gate.validate_phases(ast.parse("PHASES = " + repr(valid)))
        bad = (
            valid[:-1],
            (valid[1], valid[0], *valid[2:]),
            (("unknown-role", valid[0][1], valid[0][2]), *valid[1:]),
            ((valid[0][0], "unknown_case", valid[0][2]), *valid[1:]),
            ((valid[0][0], valid[0][1], valid[0][2] - 1), *valid[1:]),
            ((valid[0][0], valid[0][1], float(valid[0][2])), *valid[1:]),
        )
        for value in bad:
            with self.assertRaises(ValueError):
                gate.validate_phases(ast.parse("PHASES = " + repr(value)))
        with self.assertRaises(ValueError):
            gate.validate_phases(ast.parse("PHASES = " + repr(valid) + "\nPHASES = " + repr(valid)))

    def test_exact_layer(self):
        root = Path(__file__).resolve().parents[2]
        from check_chronik_regtest_service import MANIFEST, TECHNICAL, POLICY
        expected = gate.TECHNICAL | gate.POLICY
        if (root / MANIFEST).exists():
            expected |= TECHNICAL | POLICY
        from check_chronik_service_recovery import MANIFEST, TECHNICAL, POLICY
        if (root / MANIFEST).exists():
            expected |= TECHNICAL | POLICY
        self.assertEqual(gate.validate(root), expected)

    def test_manifest_rejections(self):
        root = Path(__file__).resolve().parents[2]
        original = gate.load_json
        record = original(root / gate.MANIFEST)
        for mutate in (lambda r: r["contract"].update(daemon_admission=True),
                       lambda r: r["contract"].update(knowledge_status="Reproduced"),
                       lambda r: r["contract"].update(incoming_evidence=["synthetic.json"]),
                       lambda r: r["contract"].update(rebuild="wipe-original"),
                       lambda r: r["public_parent"].update(tree="0" * 40),
                       lambda r: r["files"].pop(), lambda r: r["files"].reverse(),
                       lambda r: r["files"][0].update(path="../escape"),
                       lambda r: r["files"][0]["after"].update(sha256="0" * 64)):
            bad = copy.deepcopy(record)
            mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p: bad if p == root / gate.MANIFEST else original(p)):
                with self.assertRaises(ValueError): gate.validate(root)

    def test_governance_and_frozen_input_rejections(self):
        root = Path(__file__).resolve().parents[2]
        original = gate.load_json
        record_path = root / "docs/engineering/changes/ergon-change-0048.json"
        for mutate in (lambda r: r["evidence"].update(knowledge_status="Reproduced"),
                       lambda r: r["evidence"].update(artifacts=["synthetic.json"]),
                       lambda r: r["files"].pop()):
            bad = original(record_path)
            mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p: bad if p == record_path else original(p)):
                with self.assertRaises(ValueError): gate.validate(root)
        observed = gate.current_identity
        with patch.object(gate, "current_identity", side_effect=lambda r, p:
            dict(observed(r, p), sha256="0" * 64) if p == "chronik/Cargo.lock" else observed(r, p)):
            with self.assertRaises(ValueError): gate.validate(root)


if __name__ == "__main__":
    unittest.main()

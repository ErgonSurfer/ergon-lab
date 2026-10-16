# SPDX-License-Identifier: MIT
"""Reject service-plugin scope, identity, proof and finite-path drift."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_service_plugins as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_finite_layer(self):
        from check_chronik_plugin_alp import TECHNICAL, POLICY
        from check_chronik_service_catchup import TECHNICAL as CATCHUP_TECHNICAL, POLICY as CATCHUP_POLICY
        from check_chronik_cold_rebuild import TECHNICAL as COLD_TECHNICAL, POLICY as COLD_POLICY
        from check_chronik_startup_metadata import TECHNICAL as METADATA_TECHNICAL, POLICY as METADATA_POLICY
        self.assertEqual(gate.validate(self.root), gate.TECHNICAL | gate.POLICY | TECHNICAL | POLICY | CATCHUP_TECHNICAL | CATCHUP_POLICY | COLD_TECHNICAL | COLD_POLICY | METADATA_TECHNICAL | METADATA_POLICY)

    def test_contract_and_inventory_rejections(self):
        original = gate.load_json
        manifest = original(self.root / gate.MANIFEST)
        for mutate in (lambda r: r["contract"].update(compile_default=True),
                       lambda r: r["contract"].update(runtime_default=True),
                       lambda r: r["contract"].update(arbitrary_plugins=True),
                       lambda r: r["contract"].update(Python_security_sandbox=True),
                       lambda r: r["contract"].update(network="mainnet"),
                       lambda r: r["contract"].update(owner_assertions=11),
                       lambda r: r["contract"].update(knowledge_status="Reproduced"),
                       lambda r: r["contract"].update(dependency_change=True),
                       lambda r: r["files"].pop(), lambda r: r["files"].reverse(),
                       lambda r: r["files"][0]["after"].update(sha256="0" * 64)):
            bad = copy.deepcopy(manifest)
            mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p:
                bad if p == self.root / gate.MANIFEST else original(p)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_current_and_frozen_source_rejections(self):
        original = gate.current_identity
        for path in ("CMakeLists.txt", "src/init.cpp", "src/validation.cpp", "chronik/Cargo.lock",
                     "chronik/chronik-lib/src/ergon_plugin_fixture.rs",
                     "chronik/chronik-plugin-impl/src/context.rs",
                     "docs/engineering/changes/ergon-change-0052.json"):
            with patch.object(gate, "current_identity", side_effect=lambda r, p:
                dict(original(r, p), sha256="0" * 64) if p == path else original(r, p)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_record_rejections(self):
        original = gate.load_json
        for mutate in (lambda r: r["evidence"].update(artifacts=["synthetic.json"]),
                       lambda r: r["evidence"].update(knowledge_status="Reproduced"),
                       lambda r: r["provenance_inventory"].update(inventory_sha256="0" * 64),
                       lambda r: r["files"].pop()):
            bad = original(self.root / gate.RECORD)
            mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p:
                bad if p == self.root / gate.RECORD else original(p)), self.assertRaises(ValueError):
                gate.validate(self.root)


if __name__ == "__main__":
    unittest.main()

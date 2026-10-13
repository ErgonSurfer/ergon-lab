# SPDX-License-Identifier: MIT
"""Source-policy rejection tests only; no native/plugin execution evidence."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import check_chronik_plugin_fixtures as gate


class Helpers(unittest.TestCase):
    def setUp(self):
        self.source = Path(__file__).resolve().parents[2]
        self.record = json.loads((self.source / "docs/engineering/chronik/upstream-plugin-helpers-v1.json").read_text())
        self.original = gate.git(self.source, "show", gate.PARENT + ":chronik/chronik-plugin-impl/src/module.rs")
        self.directory = tempfile.TemporaryDirectory(prefix="ergon-public-helper-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        for row in self.record["files"]:
            p = self.root / row["path"]
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes((self.source / row["path"]).read_bytes())
        module = "chronik/chronik-plugin-impl/src/module.rs"
        (self.root / module).parent.mkdir(parents=True, exist_ok=True)
        (self.root / module).write_bytes((self.source / module).read_bytes())

    def tearDown(self):
        self.directory.cleanup()

    def check(self, record=None):
        with patch.object(gate, "load_json", return_value=record or self.record), \
             patch.object(gate, "git", return_value=self.original):
            gate.check_helpers(self.root)

    def test_exact_helper_closure(self):
        self.check()

    def test_manifest_and_selection_rejections(self):
        mutations = [lambda r: r.update(license="other"),
            lambda r: r.update(knowledge_status="Reproduced"),
            lambda r: r.update(evidence_inputs=["synthetic-incoming.json"]),
            lambda r: r["donor"].update(commit="0" * 40),
            lambda r: r["donor"].update(tree="0" * 40),
            lambda r: r["public_parent"].update(tree="0" * 40),
            lambda r: r["boundaries"].update(daemon_admission=True),
            lambda r: r["files"].reverse(), lambda r: r["files"].pop(),
            lambda r: r["files"].__setitem__(1, copy.deepcopy(r["files"][0])),
            lambda r: r["files"][0].update(path="../escaping.py"),
            lambda r: r["files"][0].update(sha256="0" * 64)]
        for mutate in mutations:
            record = copy.deepcopy(self.record)
            mutate(record)
            with self.assertRaises(ValueError): self.check(record)

    def test_source_mode_and_alias_rejections(self):
        p = self.root / self.record["files"][0]["path"]
        original = p.read_bytes()
        p.write_bytes(original + b"# synthetic drift\n")
        with self.assertRaises(ValueError): self.check()
        p.write_bytes(original)
        p.chmod(0o755)
        with self.assertRaises(ValueError): self.check()
        p.chmod(0o644)
        p.unlink()
        p.symlink_to(self.source / self.record["files"][0]["path"])
        with self.assertRaises(ValueError): self.check()

    def test_only_two_include_paths_may_change(self):
        p = self.root / "chronik/chronik-plugin-impl/src/module.rs"
        p.write_bytes(p.read_bytes() + b"\n// synthetic unrelated engine delta\n")
        with self.assertRaises(ValueError): self.check()


class CurrentLayer(unittest.TestCase):
    def test_manifest_proof_inventory_and_governance_rejections(self):
        root = Path(__file__).resolve().parents[2]
        loader = gate.load_json
        record = loader(root / gate.MANIFEST)
        for mutate in (lambda r: r["contract"].update(daemon_admission=True),
                       lambda r: r["contract"].update(knowledge_status="Reproduced"),
                       lambda r: r["contract"].update(incoming_evidence=["synthetic.json"]),
                       lambda r: r["files"].pop(),
                       lambda r: r["files"][0]["after"].update(sha256="0" * 64)):
            invalid = copy.deepcopy(record)
            mutate(invalid)
            with patch.object(gate, "load_json", side_effect=lambda p: invalid if p == root / gate.MANIFEST else loader(p)), \
                 self.assertRaises(ValueError):
                gate.validate(root)
        change = loader(root / "docs/engineering/changes/ergon-change-0047.json")
        change["evidence"]["artifacts"] = ["synthetic-incoming.json"]
        with patch.object(gate, "load_json", side_effect=lambda p: change if p.name == "ergon-change-0047.json" else loader(p)), \
             self.assertRaises(ValueError):
            gate.validate(root)


if __name__ == "__main__": unittest.main()

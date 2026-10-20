# SPDX-License-Identifier: MIT
import copy
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_multi_lokad as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_layer_and_legacy_loader_semantics(self):
        from check_chronik_external_plugins import additional_paths
        self.assertEqual(gate.validate(self.root), gate.CHANGED | additional_paths(self.root))

    def test_metadata_limits_and_loader_construction_are_not_relaxed(self):
        before = gate.git(self.root, "show", gate.PARENT + ":" + gate.LOADER).decode()
        after = (self.root / gate.LOADER).read_text()
        gate.validate_loader(before, after)
        for old, new in (('!(1..=32).contains(&list.len())', '!(0..=64).contains(&list.len())'),
                         ('if ids.contains(&id)', 'if false'),
                         ('raw.try_into()', 'Some(*b"AAAA")'),
                         ('class.call1((config,))?', 'class.call0()?'),
                         ('is_instance(plugin_cls)', 'is_instance(&class)'),
                         ('legacy_bytes_unchanged', 'renamed_case')):
            self.assertIn(old, after)
            with self.assertRaises(ValueError): gate.validate_loader(before, after.replace(old, new))

    def test_contract_drift_or_missing_case_fails(self):
        original = gate.load_json
        manifest = original(self.root / gate.MANIFEST)
        for key, value in (("list_maximum", 64), ("compile_default", True),
                           ("dispatch_transactions", 5), ("consensus_change", True),
                           ("knowledge_status", "Reproduced")):
            bad = copy.deepcopy(manifest); bad["contract"][key] = value
            with patch.object(gate, "load_json", side_effect=lambda p:
                    bad if p == self.root / gate.MANIFEST else original(p)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_extra_path_and_source_postimage_drift_fail(self):
        original_git, original_id = gate.git, gate.current_identity
        with patch.object(gate, "git", side_effect=lambda root, *args:
                original_git(root, *args) + b"extra.py\0" if args[0] == "ls-files" else original_git(root, *args)), \
                self.assertRaises(ValueError): gate.validate(self.root)
        with patch.object(gate, "current_identity", side_effect=lambda root, p:
                dict(original_id(root, p), sha256="0" * 64) if p == gate.LOADER else original_id(root, p)), \
                self.assertRaises(ValueError): gate.validate(self.root)

    def test_wrong_successor_preimage_rejected(self):
        pre = gate.before_identity(self.root, gate.LOADER)
        self.assertEqual(gate.successor_identity(self.root, gate.LOADER, pre), gate.current_identity(self.root, gate.LOADER))
        with self.assertRaises(ValueError): gate.successor_identity(self.root, gate.LOADER, dict(pre, sha256="0" * 64))

    def test_runner_normalization_is_literal_and_preserves_original(self):
        path = "tests/compatibility/upstream-chronik/run_repetitions.py"
        before = gate.git(self.root, "show", gate.PARENT + ":" + path).decode()
        after = (self.root / path).read_text()
        self.assertEqual(gate.normalize_runner(after), before)
        for bad in (after.replace('"multi-lokad projection preimage drift"', '"changed"'),
                    after + gate.PROJECTION_BLOCK,
                    after.replace('    "chronik/chronik-plugin-impl/src/plugin.py",\n', ''),
                    after.replace(gate.PLUGIN_IDENTITY_BLOCK, gate.PLUGIN_IDENTITY_BLOCK * 2)):
            with self.assertRaises(ValueError): gate.normalize_runner(bad)
        # Existing deadline, environment and cleanup text is never normalized.
        for old, new in (("timeout=60", "timeout=120"), ("stop_group(proc)", "pass"),
                         ('"LANG": "C"', '"LANG": "en_US"')):
            changed = after.replace(old, new)
            self.assertNotEqual(changed, after)
            self.assertNotEqual(gate.normalize_runner(changed), before)

    def test_plugin_adapter_identity_matches_cmake_order_exactly(self):
        path = self.root / "tests/compatibility/upstream-chronik/run_repetitions.py"
        spec = importlib.util.spec_from_file_location("multi_lokad_identity_runner", path)
        runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        cmake = (self.root / "chronik/UpstreamLink.cmake").read_text()
        block = cmake.split("list(APPEND _identity_inputs chronik/chronik-plugin-impl/src/module.rs", 1)[1].split(")", 1)[0]
        cmake_inputs = ("chronik/chronik-plugin-impl/src/module.rs", *block.split())
        self.assertEqual(runner.PLUGIN_ENABLED_INPUTS, cmake_inputs)
        self.assertEqual(len(cmake_inputs), 5)
        self.assertEqual(len(set(cmake_inputs)), 5)


if __name__ == "__main__":
    unittest.main()

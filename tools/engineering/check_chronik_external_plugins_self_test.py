# SPDX-License-Identifier: MIT
import copy
import importlib.util
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import check_chronik_external_plugins as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_source_and_record_layer(self):
        self.assertEqual(gate.validate(self.root), gate.CHANGED)

    def test_extra_file_or_source_drift_rejected(self):
        original = gate.git
        with patch.object(gate, "git", side_effect=lambda root, *args:
                original(root, *args) + b"extra.py\0" if args[0] == "ls-files" else original(root, *args)), \
                self.assertRaises(ValueError):
            gate.validate(self.root)
        original_id = gate.current_identity
        with patch.object(gate, "current_identity", side_effect=lambda root, name:
                dict(original_id(root, name), sha256="0" * 64) if name == "src/init.cpp" else original_id(root, name)), \
                self.assertRaises(ValueError):
            gate.validate(self.root)

    def test_disabled_bounds_claims_cannot_promote(self):
        original = gate.load_json
        value = original(self.root / gate.MANIFEST)
        for key, replacement in (("compile_default", True), ("runtime_default", True),
                                  ("network", "mainnet"), ("maximum_modules", 64),
                                  ("group_page_size", 200), ("knowledge_status", "Reproduced"),
                                  ("execution_deadline_seconds", 120), ("component_unit_cases", 27)):
            bad = copy.deepcopy(value); bad["contract"][key] = replacement
            with patch.object(gate, "load_json", side_effect=lambda p:
                    bad if p == self.root / gate.MANIFEST else original(p)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_successor_requires_exact_public_preimage(self):
        name = "chronik/chronik-lib/src/ergon_plugin_fixture.rs"
        before = gate.before_identity(self.root, name)
        self.assertEqual(gate.successor_identity(self.root, name, before), gate.current_identity(self.root, name))
        with self.assertRaises(ValueError):
            gate.successor_identity(self.root, name, dict(before, sha256="0" * 64))

    def test_runner_normalization_never_changes_execution(self):
        path = "tests/compatibility/upstream-chronik/run_repetitions.py"
        before = gate.git(self.root, "show", gate.PARENT + ":" + path).decode()
        after = (self.root / path).read_text()
        self.assertEqual(gate.normalize_runner(after), before)
        for old, new in (("timeout=60", "timeout=120"), ("stop_group(proc)", "pass"),
                         ('"LANG": "C"', '"LANG": "en_US"')):
            changed = after.replace(old, new)
            self.assertNotEqual(changed, after)
            self.assertNotEqual(gate.normalize_runner(changed), before)
        for bad in (after + gate.PROJECTION_BLOCK,
                    after.replace("external plugin projection drift", "changed"),
                    after.replace(gate.RUNNER_IDENTITY_BLOCK, '    "src/test/main.cpp",\n')):
            with self.assertRaises(ValueError): gate.normalize_runner(bad)

    def test_full_ordered_adapter_identity_matches_both_cmake_profiles(self):
        path = self.root / "tests/compatibility/upstream-chronik/run_repetitions.py"
        spec = importlib.util.spec_from_file_location("external_identity_runner", path)
        runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        cmake = (self.root / "chronik/UpstreamLink.cmake").read_text()
        block = cmake.split("    set(_identity_inputs\n", 1)[1].split('    set(_identity "', 1)[0]
        common = tuple(block.split(")", 1)[0].split())
        additions = re.findall(r"list\(APPEND _identity_inputs ([^)]*)\)", block)
        self.assertTrue(additions)
        for plugins in (False, True):
            actual = common + tuple(p for group in additions for p in group.split()
                if plugins or not p.startswith("chronik/chronik-plugin-impl/"))
            expected = runner.ADAPTER_INPUTS + runner.PLUGIN_ADAPTER_INPUTS + runner.SERVICE_ADAPTER_INPUTS
            if plugins:
                expected += runner.PLUGIN_ENABLED_INPUTS
            self.assertEqual(actual, expected)
            self.assertEqual(len(actual), len(set(actual)))

    def test_cmake_and_cargo_normalization_is_finite(self):
        cmake = (self.root / "chronik/UpstreamLink.cmake").read_text()
        self.assertEqual(gate.normalize_cmake(cmake),
                         gate.git(self.root, "show", gate.PARENT + ":chronik/UpstreamLink.cmake").decode())
        with self.assertRaises(ValueError): gate.normalize_cmake(cmake + gate.CMAKE_BLOCK)
        before = {"package": {"name": "same"}, "example": [{"name": "old", "path": "old.rs"}]}
        after = copy.deepcopy(before); after["example"].append(gate.EXAMPLE)
        self.assertEqual(gate.normalize_cargo(after), before)
        after["example"].append(gate.EXAMPLE)
        with self.assertRaises(ValueError): gate.normalize_cargo(after)


if __name__ == "__main__":
    unittest.main()

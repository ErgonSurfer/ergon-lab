#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import ast
import copy
import importlib.util
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import check_ergon_testnet as gate

ROOT = Path(__file__).resolve().parents[2]


class TestnetContract(unittest.TestCase):
    def test_exact_current_inventory_and_record(self):
        self.assertEqual(gate.validate(ROOT), gate.CHANGED)

    def test_mainnet_class_and_critical_native_inputs_are_frozen(self):
        before = gate.git(ROOT, "show", gate.PARENT + ":src/chainparams.cpp")
        after = (ROOT / "src/chainparams.cpp").read_bytes()
        self.assertEqual(gate.mainnet_class(before), gate.mainnet_class(after))
        for name in ("src/validation.cpp", "src/consensus/activation.cpp", "src/amount.cpp",
                     "src/amount.h", "chronik/Cargo.lock", "plugins/PROTOCOLS.md"):
            self.assertEqual((ROOT / name).read_bytes(), gate.git(ROOT, "show", gate.PARENT + ":" + name))

    def test_no_manifest_widening_or_proof_promotion(self):
        original = gate.read_json
        for key, value in (("mainnet_consensus_or_activation_change", True),
                           ("reward_floor_or_cap", True), ("cpu_exclusive", True),
                           ("daa_redesign", True), ("token_activation", True),
                           ("knowledge_status", "Reproduced"), ("incoming_evidence", ["private.json"]),
                           ("deployment_by_CI", True)):
            def read(path):
                doc = original(path)
                if path == ROOT / gate.MANIFEST:
                    doc = copy.deepcopy(doc)
                    doc["contract"][key] = value
                return doc
            with self.subTest(key=key), patch.object(gate, "read_json", side_effect=read), self.assertRaises(ValueError):
                gate.validate(ROOT)

    def test_runner_inherited_functions_and_exact_adapter_prefix_preserved(self):
        name = "tests/compatibility/upstream-chronik/run_repetitions.py"
        before = ast.parse(gate.git(ROOT, "show", gate.PARENT + ":" + name).decode())
        after = ast.parse((ROOT / name).read_text())
        def functions(tree):
            return {node.name: ast.dump(node, include_attributes=False) for node in tree.body
                    if isinstance(node, ast.FunctionDef) and node.name != "source_projection"}
        self.assertEqual(functions(before), functions(after))
        def inputs(tree):
            return next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == "ADAPTER_INPUTS" for t in node.targets))
        expected = inputs(before) + ("src/chainparams.cpp", "src/chainparamsbase.cpp", "src/chainparamsbase.h",
            "src/consensus/params.h", "src/pow.cpp", "src/util/system.cpp", "src/rpc/blockchain.cpp", "src/bitcoind.cpp")
        self.assertEqual(inputs(after), expected)
        cmake = (ROOT / "chronik/UpstreamLink.cmake").read_text().split("set(_identity_inputs", 1)[1]
        cmake = cmake.split("if(BUILD_CHRONIK_UPSTREAM_PLUGIN_TESTS)", 1)[0]
        actual = tuple(re.findall(r"(?:chronik|src|tests)/[\w./-]+|CMakeLists\.txt", cmake))
        self.assertEqual(actual, expected)

    def test_existing_native_runtime_ci_is_not_removed_or_relaxed(self):
        name = ".github/workflows/chronik-upstream-link.yml"
        before = gate.git(ROOT, "show", gate.PARENT + ":" + name).decode()
        after = (ROOT / name).read_text()
        marker = "      - name: Build and exercise bounded multi-identifier plugin metadata\n"
        old = before.split(marker, 1)[1]
        old_gate = "python3 -B tools/engineering/check_chronik_continuous_service.py check"
        new_gate = "python3 -B tools/engineering/check_ergon_testnet.py check"
        self.assertEqual(old.count(old_gate), 1)
        self.assertEqual(old.replace(old_gate, new_gate), after.split(marker, 1)[1])
        marker = "      - name: Recheck inherited runner and synthetic oracles on current source\n"
        end = "      - name: Build standalone daemon and native boundary fixtures\n"
        self.assertEqual(before.split(marker, 1)[1].split(end, 1)[0], after.split(marker, 1)[1].split(end, 1)[0])
        self.assertIn("profile: [archival, plugins, compiled-out, regtest, regtest-plugins]", after)
        self.assertIn("--run_test=ergon_testnet_tests", after)
        self.assertIn("--run_test=ergon_testnet_chronik_tests", after)
        self.assertIn('--work-parent "$RUNNER_TEMP/ergon-development-smoke" --chronik', after)

    def test_runtime_clients_change_only_exact_source_gate_import(self):
        prefix = "tests/compatibility/upstream-chronik/"
        old = "from check_chronik_continuous_service import validate"
        new = "from check_ergon_testnet import validate"
        clients = tuple(prefix + name for name in gate.RUNTIME_GATE_CLIENTS) + gate.COMMUNITY_GATE_CLIENTS
        for name in clients:
            with self.subTest(name=name):
                before = gate.git(ROOT, "show", gate.PARENT + ":" + name).decode()
                after = (ROOT / name).read_text()
                self.assertEqual(before.count(old), 1)
                # Exact text equality preserves every runtime case, assertion,
                # timeout, report, cleanup and invocation around the import.
                self.assertEqual(before.replace(old, new), after)
        name = prefix + "cold_rebuild_service_self_test.py"
        before = gate.git(ROOT, "show", gate.PARENT + ":" + name).decode()
        self.assertEqual(before.replace("import check_chronik_continuous_service as boundary",
                                        "import check_ergon_testnet as boundary"),
                         (ROOT / name).read_text())

    def test_no_test_entrypoint_still_imports_predecessor_validator(self):
        for path in (ROOT / "tests").rglob("*.py"):
            with self.subTest(path=path.relative_to(ROOT)):
                tree = ast.parse(path.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and node.module == "check_chronik_continuous_service":
                        self.assertFalse(any(alias.name in ("validate", "*") for alias in node.names))
                    if isinstance(node, ast.Import):
                        self.assertFalse(any(alias.name == "check_chronik_continuous_service" for alias in node.names))

    def test_old_source_gates_are_not_rewritten(self):
        for name in ("tools/engineering/check_chronik_continuous_service.py",
                     "tools/engineering/check_chronik_continuous_service_self_test.py",
                     "docs/engineering/changes/ergon-change-0066.json"):
            self.assertEqual((ROOT / name).read_bytes(), gate.git(ROOT, "show", gate.PARENT + ":" + name))


if __name__ == "__main__":
    unittest.main()

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
        self.assertEqual(before.split(marker, 1)[1], after.split(marker, 1)[1])
        marker = "      - name: Recheck inherited runner and synthetic oracles on current source\n"
        end = "      - name: Build standalone daemon and native boundary fixtures\n"
        self.assertEqual(before.split(marker, 1)[1].split(end, 1)[0], after.split(marker, 1)[1].split(end, 1)[0])
        self.assertIn("profile: [archival, plugins, compiled-out, regtest, regtest-plugins]", after)
        self.assertIn("--run_test=ergon_testnet_tests", after)
        self.assertIn("--run_test=ergon_testnet_chronik_tests", after)
        self.assertIn('--work-parent "$RUNNER_TEMP/ergon-development-smoke" --chronik', after)

    def test_old_source_gates_are_not_rewritten(self):
        for name in ("tools/engineering/check_chronik_continuous_service.py",
                     "tools/engineering/check_chronik_continuous_service_self_test.py",
                     "docs/engineering/changes/ergon-change-0066.json"):
            self.assertEqual((ROOT / name).read_bytes(), gate.git(ROOT, "show", gate.PARENT + ":" + name))


if __name__ == "__main__":
    unittest.main()

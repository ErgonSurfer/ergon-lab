# SPDX-License-Identifier: MIT
import ast
import copy
import importlib.util
from pathlib import Path
import re
import unittest
from unittest.mock import patch
import check_chronik_continuous_service as gate

ROOT = Path(__file__).resolve().parents[2]

class Contract(unittest.TestCase):
    def test_exact_current_source_and_record(self):
        self.assertEqual(gate.validate(ROOT), gate.CHANGED)
        self.assertEqual(len(gate.TECHNICAL), 31)

    def test_contract_cannot_promote_or_widen(self):
        original = gate.json.loads
        for key, value in (("compile_default", True), ("runtime_default", True),
                           ("native_consensus_change", True), ("activation_change", True),
                           ("skip_token_checks", True), ("journal_events", 4096),
                           ("knowledge_status", "Reproduced"), ("incoming_evidence", ["old-report.json"])):
            def changed(raw, **kwargs):
                result = original(raw, **kwargs)
                if isinstance(result, dict) and result.get("schema") == "ergon-upstream-continuous-service/v1":
                    result = copy.deepcopy(result); result["contract"][key] = value
                return result
            with self.subTest(key=key), patch.object(gate.json, "loads", side_effect=changed), self.assertRaises(ValueError):
                gate.validate(ROOT)

    def test_changed_source_cannot_be_reclassified(self):
        original = gate.current_identity
        with patch.object(gate, "current_identity", side_effect=lambda root, name:
            dict(original(root, name), sha256="0" * 64) if name == "src/validation.cpp" else original(root, name)), self.assertRaises(ValueError):
            gate.validate(ROOT)

    def test_extra_native_path_or_untracked_file_is_refused(self):
        original = gate.git
        for path in ("src/chainparams.cpp", "private-config.json"):
            with patch.object(gate, "git", side_effect=lambda root, *args:
                original(root, *args) + path.encode() + b"\0" if args[0] == "ls-files" else original(root, *args)), self.assertRaises(ValueError):
                gate.validate(ROOT)

    def test_old_inputs_are_unchanged_except_finite_successor(self):
        for name in ("PUBLICATION_POLICY.md", "chronik/Cargo.lock", "chronik/Cargo.toml",
                     "src/chainparams.cpp", "src/txmempool.cpp",
                     "plugins/moom_alp_token_index.py", "plugins/moom_nft_index.py",
                     "plugins/moom_alp_exch_market.py", "plugins/moom_reward_claim_index.py"):
            self.assertNotIn(name, gate.CHANGED)
            self.assertEqual((ROOT / name).read_bytes(), gate.git(ROOT, "show", gate.PARENT + ":" + name))

    def test_network_scope_does_not_relax_authority_or_activate_a_fork(self):
        from check_change import validate, CONTINUOUS_BOUNDARIES, BOUNDARIES, sample, ValidationError
        record = sample("optional-indexing")
        record["boundaries"] = copy.deepcopy(CONTINUOUS_BOUNDARIES)
        validate(record)
        self.assertEqual({k: v for k, v in CONTINUOUS_BOUNDARIES.items() if k != "chronik_enabled_scope"},
                         {k: v for k, v in BOUNDARIES.items() if k != "chronik_enabled_scope"})
        for name in ("chronik_consensus_authority", "chronik_mempool_authority",
                     "chronik_activation_authority", "chronik_chain_selection_authority",
                     "chronik_required_for_correctness", "mainnet_parameters_modified"):
            bad = copy.deepcopy(record); bad["boundaries"][name] = True
            with self.assertRaises(ValidationError): validate(bad)

    def test_full_adapter_identity_order_matches_both_profiles(self):
        spec = importlib.util.spec_from_file_location("continuous_runner", ROOT / "tests/compatibility/upstream-chronik/run_repetitions.py")
        runner = importlib.util.module_from_spec(spec); spec.loader.exec_module(runner)
        block = (ROOT / "chronik/UpstreamLink.cmake").read_text().split("    set(_identity_inputs\n", 1)[1].split('    set(_identity "', 1)[0]
        common = tuple(block.split(")", 1)[0].split())
        additions = re.findall(r"list\(APPEND _identity_inputs ([^)]*)\)", block)
        for plugins in (False, True):
            actual = common + tuple(p for group in additions for p in group.split()
                                    if plugins or not p.startswith("chronik/chronik-plugin-impl/"))
            expected = runner.ADAPTER_INPUTS + runner.PLUGIN_ADAPTER_INPUTS + runner.SERVICE_ADAPTER_INPUTS
            if plugins: expected += runner.PLUGIN_ENABLED_INPUTS
            self.assertEqual(actual, expected)
            self.assertEqual(len(actual), len(set(actual)))

    def test_inherited_execution_law_is_not_rewritten(self):
        path = "tests/compatibility/upstream-chronik/run_repetitions.py"
        def functions(text):
            return {node.name: ast.dump(node, include_attributes=False) for node in ast.parse(text).body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        before = functions(gate.git(ROOT, "show", gate.PARENT + ":" + path).decode())
        after = functions((ROOT / path).read_text())
        self.assertEqual(set(before), set(after))
        for name in before.keys() - {"source_projection", "bind_build"}:
            self.assertEqual(before[name], after[name], name)
        # Existing group bodies, counts, environment, cleanup and repetitions
        # are also frozen byte-for-byte, not just their execution functions.
        old = ast.parse(gate.git(ROOT, "show", gate.PARENT + ":" + path).decode())
        new = ast.parse((ROOT / path).read_text())
        def assignments(tree):
            return {node.targets[0].id: ast.dump(node.value, include_attributes=False)
                    for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)}
        a, b = assignments(old), assignments(new)
        for name in a.keys() - {"ADAPTER_INPUTS", "PLUGIN_ADAPTER_INPUTS", "PLUGIN_ENABLED_INPUTS", "SERVICE_ADAPTER_INPUTS"}:
            self.assertEqual(a[name], b[name], name)

    def test_inherited_scenarios_and_matrix_remain_present(self):
        path = ".github/workflows/chronik-upstream-link.yml"
        before = gate.git(ROOT, "show", gate.PARENT + ":" + path).decode()
        after = (ROOT / path).read_text()
        old_steps = re.findall(r"^      - name: (.*)$", before, re.M)
        new_steps = re.findall(r"^      - name: (.*)$", after, re.M)
        removed = set(old_steps) - set(new_steps)
        self.assertEqual(removed, {"Validate exact current adaptations and historical donor preimages"})
        self.assertIn("profile: [archival, plugins, compiled-out, regtest, regtest-plugins]", after)
        for token in ("--repetitions 10", "--repetitions 3", "timeout-minutes: 75", "fail-fast: false"):
            self.assertEqual(before.count(token), after.count(token))
        self.assertIn("check_chronik_continuous_service.py historical", after)

if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Self-test the fail-closed legacy-compatibility matrix contract."""

import ast
import importlib.util
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("run_matrix.py")
FEATURE_PATH = Path(__file__).with_name(
    "feature_ergon_legacy_compatibility.py"
)
SPEC = importlib.util.spec_from_file_location("legacy_run_matrix", MODULE_PATH)
MATRIX = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MATRIX)
FEATURE_SPEC = importlib.util.spec_from_file_location(
    "legacy_feature", FEATURE_PATH
)
FEATURE = importlib.util.module_from_spec(FEATURE_SPEC)
FEATURE_SPEC.loader.exec_module(FEATURE)


def module_literal(path, name):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        if any(
            isinstance(target, ast.Name) and target.id == name
            for target in statement.targets
        ):
            return ast.literal_eval(statement.value)
    raise AssertionError(f"{path.name} omitted {name}")


def function_source(path, name):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for statement in tree.body:
        if isinstance(statement, (ast.FunctionDef, ast.ClassDef)) \
                and statement.name == name:
            return ast.get_source_segment(source, statement)
        if isinstance(statement, ast.ClassDef):
            for member in statement.body:
                if isinstance(member, ast.FunctionDef) and member.name == name:
                    return ast.get_source_segment(source, member)
    raise AssertionError(f"{path.name} omitted function {name}")


def accepted_record(parent_commit, parent_tree):
    return {
        "$comment": "SPDX-License-Identifier: MIT",
        "$schema": "../schemas/change-evidence.schema.json",
        "schema_version": MATRIX.PUBLIC_SCHEMA_VERSION,
        "change_id": MATRIX.CHANGE_ID,
        "record_path": MATRIX.PUBLIC_RECORD_PATH,
        "stage": "legacy-compatibility",
        "status": "accepted",
        "purpose": {
            "summary": "Test record.",
            "scope": ["Legacy compatibility."],
            "exclusions": ["No consensus change."],
        },
        "baseline": {
            "commit": MATRIX.BASE_COMMIT,
            "license": "MIT",
            "project": "Bitcoin Static",
            "tree": MATRIX.BASE_TREE,
            "version": "24.0.5",
        },
        "public_lineage": {
            "candidate_parent_law": "direct-child",
            "integration_parent": {"commit": parent_commit, "tree": parent_tree},
            "public_root": {
                "commit": MATRIX.PUBLIC_ROOT_COMMIT,
                "parentless": True,
                "tree": MATRIX.PUBLIC_ROOT_TREE,
            },
            "remote_ref": MATRIX.PUBLIC_MAIN_REF,
        },
        "signature_policy": MATRIX.SIGNATURE_CONTRACT,
        "provenance_inventory": {
            "exclusions": ["No private material."],
            "implicit_operational_authorization": False,
            "inventory_schema": "ergon-authorship-inventory/v1",
            "inventory_sha256": "0" * 64,
            "license": "MIT",
            "standing_policy": MATRIX.STANDING_PROVENANCE_POLICY,
        },
        "prerequisites": [],
        "files": [],
        "surfaces": {
            "build_release": False,
            "consensus": False,
            "data_research": False,
            "documentation_cockpit": True,
            "indexing_chronik": False,
            "p2p": False,
            "rpc_api": False,
            "storage_datadir": False,
            "tests": True,
            "ui": False,
            "validation_mempool": False,
            "wallet": False,
        },
        "boundaries": MATRIX.PUBLIC_BOUNDARIES,
        "verification": {
            "builds": [{"role": role} for role in MATRIX.BUILD_ROLES],
            "integration_parent_binding": MATRIX.INTEGRATION_PARENT_BINDING,
            "scenarios": [{"id": item["id"]} for item in MATRIX.EXECUTIONS],
        },
        "evidence": {
            "artifacts": [],
            "ceiling": "record-review-only",
            "delivery_state": "planned",
            "knowledge_status": "Open Question",
        },
        "limitations": ["No public execution evidence exists."],
        "counterevidence": ["Missing public execution evidence."],
        "decision": {
            "date": "2026-09-01",
            "reviewer": "ErgonSurfer",
            "status": "accepted",
        },
    }


class MatrixContractTest(unittest.TestCase):
    def test_exact_execution_inventory(self):
        self.assertEqual(
            [execution["id"] for execution in MATRIX.EXECUTIONS],
            [
                "mixed-node-coexistence",
                "legacy-mining-baseline",
                "legacy-mining-candidate",
                "inherited-functional-default-launch",
            ],
        )
        self.assertFalse(MATRIX.EXECUTIONS[-1]["hermetic_nodes"])
        self.assertEqual(
            MATRIX.EXECUTIONS[0]["required_output_markers"],
            MATRIX.MIXED_NODE_SUCCESS_MARKERS,
        )
        self.assertTrue(all(
            "required_output_markers" not in execution
            for execution in MATRIX.EXECUTIONS[1:]
        ))

    def test_reindex_lifecycle_contract(self):
        self.assertEqual(
            module_literal(FEATURE_PATH, "CHAIN_SNAPSHOT_FIELDS"),
            ("blocks", "headers", "bestblockhash", "chainwork"),
        )
        self.assertEqual(
            module_literal(FEATURE_PATH, "UTXO_SNAPSHOT_FIELDS"),
            ("height", "bestblock", "txouts", "bogosize", "total_amount"),
        )
        self.assertEqual(
            module_literal(FEATURE_PATH, "UTXO_COMMITMENT_FIELDS"),
            ("hash_serialized_2", "hash_serialized"),
        )
        lifecycles = module_literal(FEATURE_PATH, "REINDEX_LIFECYCLES")
        self.assertEqual(
            lifecycles,
            (
                (
                    "full-reindex",
                    "-reindex",
                    (
                        "Reindexing block file blk00000.dat...",
                        "Reindexing finished",
                    ),
                    "ERGON_LEGACY_LIFECYCLE_OK full-reindex",
                ),
                (
                    "chainstate-reindex",
                    "-reindex-chainstate",
                    ("Wiping LevelDB in",),
                    "ERGON_LEGACY_LIFECYCLE_OK chainstate-reindex",
                ),
            ),
        )
        self.assertEqual(
            tuple(item[3].encode("ascii") for item in lifecycles),
            MATRIX.MIXED_NODE_SUCCESS_MARKERS[:2],
        )

    def test_default_protected_reorg_contract(self):
        feature_source = FEATURE_PATH.read_text(encoding="utf-8")
        self.assertEqual(FEATURE.REORG_DEPTH, 3)
        self.assertEqual(FEATURE.REORG_UNPARK_MARGIN, 2)
        self.assertEqual(FEATURE.REORG_REPLACEMENT_BLOCKS, 5)
        self.assertEqual(
            FEATURE.REORG_SUCCESS_MARKER,
            "ERGON_LEGACY_LIFECYCLE_OK default-protected-reorg",
        )
        self.assertEqual(
            MATRIX.MIXED_NODE_SUCCESS_MARKERS[2],
            FEATURE.REORG_SUCCESS_MARKER.encode("ascii"),
        )
        feature_lines = [line.strip() for line in feature_source.splitlines()]
        self.assertEqual(
            feature_lines.count("connect_nodes(self.nodes[0], self.nodes[1])"),
            0,
        )
        self.assertEqual(
            feature_source.count(
                "connect_nodes_bi(self.nodes[0], self.nodes[1])"
            ),
            8,
        )
        self.assertEqual(
            feature_lines.count("connect_nodes(self.nodes[0], generator)"),
            1,
        )

        setup_network = function_source(FEATURE_PATH, "setup_network")
        for required in (
            "self.setup_nodes()",
            "connect_nodes_bi(self.nodes[0], self.nodes[1])",
            "self.sync_all([self.nodes[:2]])",
        ):
            self.assertIn(required, setup_network)

        bundle = function_source(FEATURE_PATH, "generate_reorg_bundle")
        for required in (
            "generator = self.nodes[2]",
            "connect_nodes(self.nodes[0], generator)",
            "self.sync_all([[self.nodes[0], generator]])",
            "disconnect_nodes(self.nodes[0], generator)",
            "REORG_DEPTH, incumbent_address",
            '"raw": generator.getblock(block_hash, 0)',
            'generator.invalidateblock(incumbent[0]["hash"])',
            "REORG_REPLACEMENT_BLOCKS, replacement_address",
            "self.stop_node(2)",
        ):
            self.assertIn(required, bundle)

        reorg = function_source(
            FEATURE_PATH, "default_protected_reorg_and_compare"
        )
        for required in (
            "disconnect_nodes(self.nodes[0], self.nodes[1])",
            'proposal = {"data": record["raw"], "mode": "proposal"}',
            "for node in self.nodes[:2]",
            'branch_tip_status(node, record["hash"], "equal-work")',
            'branch_tip_status(node, record["hash"], "one-block-lead")',
            'branch_tip_status(node, incumbent_tip, "incumbent")',
            '"parked"',
            '"valid-fork"',
            "self.assert_common_chain()",
            "connect_nodes_bi(self.nodes[0], self.nodes[1])",
            "self.mine_and_compare(0, 1, address)",
            "self.mine_and_compare(1, 1, address)",
            "self.log.info(REORG_SUCCESS_MARKER)",
        ):
            self.assertIn(required, reorg)

        submit = function_source(FEATURE_PATH, "submit_branch_block")
        for required in (
            'result = node.submitblock(record["raw"])',
            'result == "inconclusive"',
            'header["hash"]',
            'header["confirmations"]',
        ):
            self.assertIn(required, submit)

    def test_physical_pruning_contract(self):
        feature_source = FEATURE_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            module_literal(FEATURE_PATH, "NODE_ARGS"),
            (
                "-connect=0",
                "-disablewallet",
            ),
        )
        self.assertIn(
            'PRUNE_NODE_ARGS = (*NODE_ARGS, "-prune=1")',
            feature_source,
        )
        self.assertIn(
            "from test_framework.mininode import P2PInterface",
            feature_source,
        )
        self.assertEqual(module_literal(FEATURE_PATH, "PRUNE_AFTER_HEIGHT"), 1000)
        self.assertEqual(module_literal(FEATURE_PATH, "MIN_BLOCKS_TO_KEEP"), 288)
        self.assertEqual(
            module_literal(FEATURE_PATH, "LARGE_COINBASE_SCRIPT_NOPS"),
            950000,
        )
        self.assertEqual(module_literal(FEATURE_PATH, "LARGE_BLOCK_COUNT"), 150)
        marker = module_literal(
            FEATURE_PATH, "PHYSICAL_PRUNING_SUCCESS_MARKER"
        )
        self.assertEqual(
            marker, "ERGON_LEGACY_LIFECYCLE_OK physical-pruning"
        )
        self.assertEqual(
            MATRIX.MIXED_NODE_SUCCESS_MARKERS,
            (
                b"ERGON_LEGACY_LIFECYCLE_OK full-reindex",
                b"ERGON_LEGACY_LIFECYCLE_OK chainstate-reindex",
                b"ERGON_LEGACY_LIFECYCLE_OK default-protected-reorg",
                marker.encode("ascii"),
            ),
        )

        mining = function_source(FEATURE_PATH, "mine_large_blocks")
        for required in (
            'template = baseline.getblocktemplate()',
            'template["height"]',
            'template["coinbasevalue"]',
            'template["version"]',
            'template["previousblockhash"]',
            'template["curtime"]',
            'template["bits"]',
            'baseline.getblocktemplate(proposal)',
            'candidate.getblocktemplate(proposal)',
            'baseline.submitblock(raw_block)',
            'candidate.submitblock(raw_block)',
        ):
            self.assertIn(required, mining)
        self.assertLess(
            mining.index('coinbase.vout[0].nValue'),
            mining.index('coinbase.rehash()'),
        )
        self.assertLess(
            mining.index('coinbase.vin[0].nSequence = 0xFFFFFFFF'),
            mining.index('coinbase.rehash()'),
        )
        self.assertLess(
            mining.index('candidate.getblocktemplate(proposal)'),
            mining.index('baseline.submitblock(raw_block)'),
        )

        advance = function_source(FEATURE_PATH, "advance_to_prune_boundary")
        for required in (
            "disconnect_nodes(self.nodes[0], self.nodes[1])",
            "self.nodes[0].add_p2p_connection(P2PInterface())",
            "self.nodes[0].disconnect_p2ps()",
        ):
            self.assertIn(required, advance)
        self.assertLess(
            advance.index("add_p2p_connection"),
            advance.index("mine_large_blocks"),
        )
        self.assertLess(
            advance.index("mine_large_blocks"),
            advance.index("disconnect_p2ps"),
        )
        self.assertLess(
            advance.index("disconnect_p2ps"),
            advance.index("\n        connect_nodes_bi"),
        )
        self.assertLess(
            advance.index("\n        connect_nodes_bi"),
            advance.index("mine_and_compare"),
        )
        enable = function_source(FEATURE_PATH, "enable_pruning")
        for required in (
            "directory_identity(Path(node.datadir))",
            "self.restart_node(0, extra_args=list(PRUNE_NODE_ARGS))",
            "self.restart_node(1, extra_args=list(PRUNE_NODE_ARGS))",
            'assert_equal(chain["pruneheight"], 0)',
        ):
            self.assertIn(required, enable)
        pruning = function_source(FEATURE_PATH, "physical_prune_and_compare")
        for required in (
            "node.pruneblockchain(tip_height)",
            "assert_file_pair_absent(old_pair)",
            '"Prune: UnlinkPrunedFiles deleted blk/rev (00000)"',
            '"Prune (Manual): prune_height="',
            'f"{expected_prune_height} removed "',
            'chain["size_on_disk"] >= pre_prune_usage[node_index]',
            "for path in retained_pair:",
            "1 < prune_height <= expected_prune_height",
            "assert_equal(prune_heights[1], prune_heights[0])",
            'assert_equal(chain["pruneheight"], durable_prune_height)',
            "directory_identity(Path(node.datadir))",
            'node.getblockheader(old_hash)',
            '"Block not available (pruned data)"',
            "self.restart_node(0",
            "self.restart_node(1",
            "self.mine_and_compare(0, 1, address)",
            "self.mine_and_compare(1, 1, address)",
            "self.log.info(PHYSICAL_PRUNING_SUCCESS_MARKER)",
        ):
            self.assertIn(required, pruning)
        self.assertLess(
            pruning.rindex("assert_file_pair_absent(old_pair)"),
            pruning.index("self.mine_and_compare(0, 1, address)"),
        )
        self.assertLess(
            pruning.index("self.mine_and_compare(1, 1, address)"),
            pruning.index("self.log.info(PHYSICAL_PRUNING_SUCCESS_MARKER)"),
        )
        run_test = function_source(FEATURE_PATH, "run_test")
        self.assertLess(
            run_test.index("for lifecycle in REINDEX_LIFECYCLES"),
            run_test.index("self.default_protected_reorg_and_compare(address)"),
        )
        self.assertLess(
            run_test.index("self.default_protected_reorg_and_compare(address)"),
            run_test.index("self.enable_pruning()"),
        )
        self.assertLess(
            run_test.index("self.enable_pruning()"),
            run_test.index("self.advance_to_prune_boundary(address)"),
        )

    def test_pruning_filesystem_helpers(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            root_stat = root_path.stat()
            self.assertEqual(
                FEATURE.directory_identity(root_path),
                (root_stat.st_dev, root_stat.st_ino),
            )

            regular = root_path / "regular.dat"
            regular.write_bytes(b"x")
            regular_stat = regular.stat()
            self.assertEqual(
                FEATURE.regular_file_identity(regular),
                (regular_stat.st_dev, regular_stat.st_ino, 1),
            )
            with self.assertRaisesRegex(AssertionError, "not a directory"):
                FEATURE.directory_identity(regular)

            empty = root_path / "empty.dat"
            empty.touch()
            with self.assertRaisesRegex(AssertionError, "nonempty file"):
                FEATURE.regular_file_identity(empty)
            with self.assertRaisesRegex(AssertionError, "missing"):
                FEATURE.regular_file_identity(root_path / "missing.dat")

            symlink = root_path / "linked.dat"
            symlink.symlink_to(regular)
            with self.assertRaisesRegex(AssertionError, "symlink"):
                FEATURE.regular_file_identity(symlink)
            with self.assertRaisesRegex(AssertionError, "symlink"):
                FEATURE.directory_identity(symlink)

            absent = (root_path / "absent-blk", root_path / "absent-rev")
            FEATURE.assert_file_pair_absent(absent)
            absent[0].write_bytes(b"x")
            with self.assertRaisesRegex(AssertionError, "survived"):
                FEATURE.assert_file_pair_absent(absent)

    def test_exact_change_inventory(self):
        self.assertEqual(
            MATRIX.TECHNICAL_CHANGE_ENTRIES,
            (
                ("M", "tests/compatibility/legacy/run_matrix.py"),
                ("M", "tests/compatibility/legacy/self_test.py"),
            ),
        )
        self.assertEqual(
            MATRIX.PUBLIC_CHANGE_ENTRIES,
            (("A", MATRIX.PUBLIC_RECORD_PATH), *MATRIX.TECHNICAL_CHANGE_ENTRIES),
        )
        self.assertEqual(MATRIX.BUILD_ROLES, ("baseline", "candidate"))
        self.assertEqual(
            MATRIX.INTEGRATION_PARENT_PREIMAGES,
            {
                "tests/compatibility/legacy/run_matrix.py":
                    "735442bf32fddb8675ba49d7c196aada50314f56",
                "tests/compatibility/legacy/self_test.py":
                    "5e0e3cb7e04877918fee4813b9ab912d8816c8d6",
            },
        )

    def test_prior_records_bind_preimages(self):
        source_root = MODULE_PATH.parents[3]
        expected_by_record = {
            "ergon-change-0009.json": {
                "tests/compatibility/legacy/run_matrix.py",
                "tests/compatibility/legacy/self_test.py",
            },
        }
        observed = {}
        for record_name, paths in expected_by_record.items():
            record = json.loads(
                (
                    source_root / "docs/engineering/changes" / record_name
                ).read_text(encoding="utf-8")
            )
            for file_record in record["files"]:
                if file_record["path"] in paths:
                    observed[file_record["path"]] = \
                        file_record["after"]["git_blob"]
        self.assertEqual(observed, MATRIX.INTEGRATION_PARENT_PREIMAGES)

    def test_previous_public_reindex_evidence_is_visible(self):
        source_root = MODULE_PATH.parents[3]
        record = json.loads(
            (
                source_root
                / "docs/engineering/changes/ergon-change-0006.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(record["change_id"], "ERGON-CHANGE-0006")
        self.assertEqual(record["status"], "landed")
        self.assertEqual(record["evidence"]["knowledge_status"], "Observed")
        self.assertEqual(record["evidence"]["ceiling"], "assembled_component")

    def test_current_record_binds_reviewed_file_metadata(self):
        source_root = MODULE_PATH.parents[3]
        record = json.loads(
            (source_root / MATRIX.PUBLIC_RECORD_PATH).read_text(encoding="utf-8")
        )
        metadata_fields = (
            "production_reachability",
            "provenance",
            "role",
            "spdx",
            "test_reachability",
        )
        recorded_metadata = {
            file_record["path"]: {
                field: file_record[field]
                for field in metadata_fields
            }
            for file_record in record["files"]
        }
        runner_metadata = {
            path: {
                field: MATRIX.REVIEWED_FILE_METADATA[path][field]
                for field in metadata_fields
            }
            for _operation, path in MATRIX.TECHNICAL_CHANGE_ENTRIES
        }
        self.assertEqual(recorded_metadata, runner_metadata)

    def test_public_framework_imports_against_legacy_helpers(self):
        source_root = MODULE_PATH.parents[3]
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    (
                        "import inspect, sys; "
                        "sys.path.insert(0, 'test/functional'); "
                        "from test_framework import util; "
                        "from test_framework.test_framework import BitcoinTestFramework; "
                        "from test_framework.test_node import TestNode; "
                        "assert str(inspect.signature(util.initialize_datadir)) == '(dirname, n)'; "
                        "assert str(inspect.signature(util.rpc_url)) == '(datadir, host, port)'; "
                        "assert str(inspect.signature(util.delete_cookie_file)) == '(datadir)'; "
                        "assert 'chain' not in inspect.signature(TestNode.__init__).parameters; "
                        "assert 'hermetic_env' in inspect.signature(TestNode.__init__).parameters; "
                        "assert not hasattr(BitcoinTestFramework, 'is_chronik_observer_compiled'); "
                        "print('imports-ok')"
                    ),
                ],
                cwd=source_root,
                env=MATRIX.child_environment(tmpdir),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertEqual(result.stdout, b"imports-ok\n")

    def test_parent_cli_identity_is_mandatory_unique_and_lowercase(self):
        base = [
            "run_matrix.py",
            "--baseline-source=baseline",
            "--candidate-source=candidate",
            f"--expected-candidate-commit={'2' * 40}",
            f"--expected-candidate-tree={'3' * 40}",
            f"--expected-accepted-record-sha256={'4' * 64}",
            "--expected-reviewer-identity=reviewer",
            "--expected-decision-date=2026-09-01",
            "--baseline-build=baseline-build",
            "--candidate-build=candidate-build",
            "--work-root=work",
            "--report=report.json",
        ]

        def parse(extra):
            with mock.patch.object(sys, "argv", base + extra), \
                    contextlib.redirect_stderr(io.StringIO()):
                return MATRIX.parse_args()

        with self.assertRaises(SystemExit):
            parse([])
        with self.assertRaises(SystemExit):
            parse([
                f"--expected-integration-parent-commit={'6' * 40}",
                f"--expected-integration-parent-commit={'7' * 40}",
                f"--expected-integration-parent-tree={'8' * 40}",
            ])
        with self.assertRaises(SystemExit):
            parse([
                f"--expected-integration-parent-commit={'A' * 40}",
                f"--expected-integration-parent-tree={'8' * 40}",
            ])
        with self.assertRaises(SystemExit):
            parse([
                f"--expected-integration-parent-c={'6' * 40}",
                f"--expected-integration-parent-t={'7' * 40}",
            ])
        parsed = parse([
            f"--expected-integration-parent-commit={'6' * 40}",
            f"--expected-integration-parent-tree={'7' * 40}",
        ])
        self.assertEqual(parsed.expected_integration_parent_commit, "6" * 40)
        self.assertEqual(parsed.expected_integration_parent_tree, "7" * 40)

    def test_candidate_identity_gate(self):
        commit = "2" * 40
        tree = "3" * 40
        parent_commit = "6" * 40
        parent_tree = "7" * 40
        diff = "\n".join(
            f"{operation}\t{path}"
            for operation, path in MATRIX.PUBLIC_CHANGE_ENTRIES
        )

        def governed_git_output(_root, *args):
            if args == ("status", "--porcelain=v1", "--untracked-files=all"):
                return ""
            if args == ("rev-parse", "HEAD"):
                return commit
            if args == ("rev-parse", "HEAD^{tree}"):
                return tree
            if args == ("rev-parse", f"{MATRIX.PUBLIC_ROOT_COMMIT}^{{tree}}"):
                return MATRIX.PUBLIC_ROOT_TREE
            if args == (
                "rev-list", "--parents", "-n", "1", MATRIX.PUBLIC_ROOT_COMMIT
            ):
                return MATRIX.PUBLIC_ROOT_COMMIT
            if args == (
                "rev-parse", f"{parent_commit}^{{tree}}"
            ):
                return parent_tree
            if args[0] == "rev-parse" and len(args) == 2:
                revision_path = args[1]
                prefix = f"{parent_commit}:"
                if revision_path.startswith(prefix):
                    return MATRIX.INTEGRATION_PARENT_PREIMAGES[
                        revision_path.removeprefix(prefix)
                    ]
            if args == ("rev-list", "--parents", "-n", "1", commit):
                return f"{commit} {parent_commit}"
            if args == ("rev-parse", MATRIX.PUBLIC_MAIN_REF):
                return "5" * 40
            if args == (
                "diff", "--name-status", "--no-renames",
                parent_commit, commit,
            ):
                return diff
            if args == (
                "diff", "--check", parent_commit, commit
            ):
                return ""
            raise AssertionError(args)

        expected_returncode_calls = [
            (
                "merge-base", "--is-ancestor", MATRIX.PUBLIC_ROOT_COMMIT,
                parent_commit,
            ),
            (
                "diff", "--quiet", MATRIX.PUBLIC_ROOT_COMMIT, parent_commit,
                "--", *MATRIX.BASELINE_CONTROLLED_PATHS,
            ),
            (
                "merge-base", "--is-ancestor", parent_commit,
                MATRIX.PUBLIC_MAIN_REF,
            ),
            (
                "merge-base", "--is-ancestor", commit,
                MATRIX.PUBLIC_MAIN_REF,
            ),
        ]
        observed_returncode_calls = []

        def governed_git_returncode(_root, *args):
            observed_returncode_calls.append(args)
            if args not in expected_returncode_calls:
                raise AssertionError(args)
            return 0

        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            MATRIX, "git_output", side_effect=governed_git_output
        ), mock.patch.object(
            MATRIX, "git_returncode", side_effect=governed_git_returncode
        ), mock.patch.object(
            MATRIX, "require_complete_git_history"
        ), mock.patch.object(
            MATRIX, "verify_commit_signature",
            return_value={"status": "G"},
        ), mock.patch.object(
            MATRIX, "git_version", return_value="git version test"
        ), mock.patch.object(
            MATRIX, "load_change_record", return_value=({}, "4" * 64)
        ), mock.patch.object(MATRIX, "validate_recorded_files") as validate:
            _root, identity, record_sha256, public_git = \
                MATRIX.candidate_source_identity(
                root,
                expected_commit=commit,
                expected_tree=tree,
                expected_record_sha256="4" * 64,
                expected_reviewer_identity="reviewer",
                expected_decision_date="2026-09-01",
                expected_integration_parent_commit=parent_commit,
                expected_integration_parent_tree=parent_tree,
            )
            self.assertEqual(identity["commit"], commit)
            self.assertEqual(record_sha256, "4" * 64)
            self.assertEqual(public_git["origin_main_oid"], "5" * 40)
            validate.assert_called_once_with(
                Path(root).resolve(), parent_commit, commit, {}
            )
            self.assertEqual(
                observed_returncode_calls, expected_returncode_calls
            )

        with tempfile.TemporaryDirectory() as root:
            resolved_root = Path(root).resolve()
            initial_identity = {
                "clean": True,
                "commit": commit,
                "tree": tree,
            }
            changed_identity = {
                "clean": False,
                "commit": commit,
                "tree": tree,
            }
            with mock.patch.object(
                MATRIX, "source_identity", side_effect=[
                    (resolved_root, initial_identity),
                    (resolved_root, changed_identity),
                ]
            ), mock.patch.object(
                MATRIX, "git_output", side_effect=governed_git_output
            ), mock.patch.object(
                MATRIX, "git_returncode", return_value=0
            ), mock.patch.object(
                MATRIX, "require_complete_git_history"
            ), mock.patch.object(
                MATRIX, "verify_commit_signature", return_value={"status": "G"}
            ), mock.patch.object(
                MATRIX, "load_change_record", return_value=({}, "4" * 64)
            ):
                with self.assertRaisesRegex(
                    MATRIX.MatrixError,
                    "source changed during record validation",
                ):
                    MATRIX.candidate_source_identity(
                        root,
                        expected_commit=commit,
                        expected_tree=tree,
                        expected_record_sha256="4" * 64,
                        expected_reviewer_identity="reviewer",
                        expected_decision_date="2026-09-01",
                        expected_integration_parent_commit=parent_commit,
                        expected_integration_parent_tree=parent_tree,
                    )

        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            MATRIX, "git_output", side_effect=["", commit, tree]
        ):
            with self.assertRaisesRegex(MATRIX.MatrixError, "reviewed identity"):
                MATRIX.candidate_source_identity(
                    root,
                    expected_commit="4" * 40,
                    expected_tree=tree,
                    expected_record_sha256="4" * 64,
                    expected_reviewer_identity="reviewer",
                    expected_decision_date="2026-09-01",
                    expected_integration_parent_commit=parent_commit,
                    expected_integration_parent_tree=parent_tree,
                )

    def test_exact_baseline_identity_gate(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            MATRIX, "git_output",
            side_effect=["", MATRIX.BASE_COMMIT, MATRIX.BASE_TREE],
        ):
            _root, identity = MATRIX.source_identity(root, exact_base=True)
            self.assertEqual(identity["tree"], MATRIX.BASE_TREE)
        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            MATRIX, "git_output", side_effect=["", "0" * 40, MATRIX.BASE_TREE]
        ):
            with self.assertRaisesRegex(MATRIX.MatrixError, "exact Bitcoin Static"):
                MATRIX.source_identity(root, exact_base=True)

    def test_parent_tree_signature_and_single_parent_fail_closed(self):
        commit = "2" * 40
        tree = "3" * 40
        parent_commit = "6" * 40
        parent_tree = "7" * 40
        identity = {"clean": True, "commit": commit, "tree": tree}

        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            MATRIX, "source_identity",
            return_value=(Path(root).resolve(), identity),
        ), mock.patch.object(
            MATRIX, "require_complete_git_history"
        ), mock.patch.object(
            MATRIX, "verify_commit_signature", return_value={"status": "G"}
        ), mock.patch.object(
            MATRIX, "git_output", side_effect=[
                MATRIX.PUBLIC_ROOT_TREE,
                MATRIX.PUBLIC_ROOT_COMMIT,
                "8" * 40,
            ]
        ):
            with self.assertRaisesRegex(MATRIX.MatrixError, "tree does not match"):
                MATRIX.candidate_source_identity(
                    root,
                    expected_commit=commit,
                    expected_tree=tree,
                    expected_record_sha256="4" * 64,
                    expected_reviewer_identity="reviewer",
                    expected_decision_date="2026-09-01",
                    expected_integration_parent_commit=parent_commit,
                    expected_integration_parent_tree=parent_tree,
                )

        def reject_parent_signature(_root, revision):
            if revision == parent_commit:
                raise MATRIX.MatrixError("reviewed Git commit signature is invalid")
            return {"status": "G"}

        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            MATRIX, "source_identity",
            return_value=(Path(root).resolve(), identity),
        ), mock.patch.object(
            MATRIX, "require_complete_git_history"
        ), mock.patch.object(
            MATRIX, "verify_commit_signature", side_effect=reject_parent_signature
        ):
            with self.assertRaisesRegex(MATRIX.MatrixError, "signature is invalid"):
                MATRIX.candidate_source_identity(
                    root,
                    expected_commit=commit,
                    expected_tree=tree,
                    expected_record_sha256="4" * 64,
                    expected_reviewer_identity="reviewer",
                    expected_decision_date="2026-09-01",
                    expected_integration_parent_commit=parent_commit,
                    expected_integration_parent_tree=parent_tree,
                )

        outputs = [
            MATRIX.PUBLIC_ROOT_TREE,
            MATRIX.PUBLIC_ROOT_COMMIT,
            parent_tree,
            *MATRIX.INTEGRATION_PARENT_PREIMAGES.values(),
            f"{commit} {parent_commit} {'9' * 40}",
        ]
        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            MATRIX, "source_identity",
            return_value=(Path(root).resolve(), identity),
        ), mock.patch.object(
            MATRIX, "require_complete_git_history"
        ), mock.patch.object(
            MATRIX, "verify_commit_signature", return_value={"status": "G"}
        ), mock.patch.object(
            MATRIX, "git_output", side_effect=outputs
        ), mock.patch.object(
            MATRIX, "git_returncode", return_value=0
        ):
            with self.assertRaisesRegex(MATRIX.MatrixError, "directly follow"):
                MATRIX.candidate_source_identity(
                    root,
                    expected_commit=commit,
                    expected_tree=tree,
                    expected_record_sha256="4" * 64,
                    expected_reviewer_identity="reviewer",
                    expected_decision_date="2026-09-01",
                    expected_integration_parent_commit=parent_commit,
                    expected_integration_parent_tree=parent_tree,
                )

    def test_candidate_identity_rejects_an_unexpected_public_path(self):
        commit = "2" * 40
        tree = "3" * 40
        parent_commit = "6" * 40
        parent_tree = "7" * 40
        changed = "\n".join([
            *(
                f"{operation}\t{path}"
                for operation, path in MATRIX.PUBLIC_CHANGE_ENTRIES
            ),
            "M\tsrc/validation.cpp",
        ])
        outputs = [
            MATRIX.PUBLIC_ROOT_TREE,
            MATRIX.PUBLIC_ROOT_COMMIT,
            parent_tree,
            *MATRIX.INTEGRATION_PARENT_PREIMAGES.values(),
            f"{commit} {parent_commit}",
            "5" * 40,
            changed,
        ]
        identity = {"clean": True, "commit": commit, "tree": tree}
        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            MATRIX,
            "source_identity",
            return_value=(Path(root).resolve(), identity),
        ), mock.patch.object(
            MATRIX, "git_output", side_effect=outputs
        ), mock.patch.object(
            MATRIX, "git_returncode", return_value=0
        ), mock.patch.object(
            MATRIX, "require_complete_git_history"
        ), mock.patch.object(
            MATRIX, "verify_commit_signature", return_value={"status": "G"}
        ):
            with self.assertRaisesRegex(MATRIX.MatrixError, "exact public change"):
                MATRIX.candidate_source_identity(
                    root,
                    expected_commit=commit,
                    expected_tree=tree,
                    expected_record_sha256="4" * 64,
                    expected_reviewer_identity="reviewer",
                    expected_decision_date="2026-09-01",
                    expected_integration_parent_commit=parent_commit,
                    expected_integration_parent_tree=parent_tree,
                )

    def test_public_record_contract_rejects_mismatches_and_private_state(self):
        parent_commit = "6" * 40
        parent_tree = "7" * 40
        record = accepted_record(parent_commit, parent_tree)

        def load(root, expected_sha256, **overrides):
            return MATRIX.load_change_record(
                root,
                expected_sha256=expected_sha256,
                expected_reviewer_identity=overrides.get(
                    "reviewer", "ErgonSurfer"
                ),
                expected_decision_date=overrides.get("date", "2026-09-01"),
                expected_integration_parent_commit=overrides.get(
                    "parent_commit", parent_commit
                ),
                expected_integration_parent_tree=overrides.get(
                    "parent_tree", parent_tree
                ),
            )

        with tempfile.TemporaryDirectory() as root, mock.patch.object(
            MATRIX, "run_public_record_validator"
        ) as validator:
            record_path = Path(root) / MATRIX.PUBLIC_RECORD_PATH
            record_path.parent.mkdir(parents=True)
            record_path.write_text(json.dumps(record), encoding="utf-8")
            with self.assertRaisesRegex(MATRIX.MatrixError, "accepted digest"):
                load(root, "0" * 64)
            observed, _digest = load(root, MATRIX.sha256_file(record_path))
            self.assertEqual(observed, record)

            legacy_schema = json.loads(json.dumps(record))
            legacy_schema["schema_version"] = "1.0"
            record_path.write_text(json.dumps(legacy_schema), encoding="utf-8")
            with self.assertRaisesRegex(MATRIX.MatrixError, "schema_version"):
                load(root, MATRIX.sha256_file(record_path))

            policy_drift = json.loads(json.dumps(record))
            policy_drift["provenance_inventory"]["standing_policy"] = \
                "OTHER.md"
            record_path.write_text(json.dumps(policy_drift), encoding="utf-8")
            with self.assertRaisesRegex(
                MATRIX.MatrixError, "provenance inventory"
            ):
                load(root, MATRIX.sha256_file(record_path))

            record_path.write_text(json.dumps(record), encoding="utf-8")

            accepted_digest = MATRIX.sha256_file(record_path)

            def mutate_record(_source_root, validated_record_path):
                validated_record_path.write_text("{}\n", encoding="utf-8")

            validator.side_effect = mutate_record
            with self.assertRaisesRegex(
                MATRIX.MatrixError, "changed during validation"
            ):
                load(root, accepted_digest)
            validator.side_effect = None
            record_path.write_text(json.dumps(record), encoding="utf-8")

            with self.assertRaisesRegex(MATRIX.MatrixError, "accepted review"):
                load(
                    root, MATRIX.sha256_file(record_path),
                    reviewer="different-reviewer",
                )
            with self.assertRaisesRegex(MATRIX.MatrixError, "public lineage"):
                load(
                    root, MATRIX.sha256_file(record_path),
                    parent_tree="8" * 40,
                )
            with self.assertRaisesRegex(MATRIX.MatrixError, "public lineage"):
                load(
                    root, MATRIX.sha256_file(record_path),
                    parent_commit="8" * 40,
                )

            wrong_change = json.loads(json.dumps(record))
            wrong_change["change_id"] = "ERGON-CHANGE-0005"
            record_path.write_text(json.dumps(wrong_change), encoding="utf-8")
            with self.assertRaisesRegex(MATRIX.MatrixError, "invalid change_id"):
                load(root, MATRIX.sha256_file(record_path))

            for decision in (
                {"status": "pending"},
                {
                    "date": "2026-09-01",
                    "reviewer": "ErgonSurfer",
                    "status": "rejected",
                },
                {
                    "date": "2026-09-01",
                    "status": "accepted",
                },
            ):
                unresolved = json.loads(json.dumps(record))
                unresolved["decision"] = decision
                record_path.write_text(json.dumps(unresolved), encoding="utf-8")
                with self.assertRaisesRegex(
                    MATRIX.MatrixError, "accepted review"
                ):
                    load(root, MATRIX.sha256_file(record_path))

            malformed_date = json.loads(json.dumps(record))
            malformed_date["decision"]["date"] = "2026-9-1"
            record_path.write_text(json.dumps(malformed_date), encoding="utf-8")
            with self.assertRaisesRegex(
                MATRIX.MatrixError, "review date"
            ):
                load(
                    root,
                    MATRIX.sha256_file(record_path),
                    date="2026-9-1",
                )

            for private_path in (
                "/Users/operator/private",
                "/private/var/operator-sensitive",
                "/tmp/operator-sensitive",
                "see (/private/var/operator-sensitive)",
                r"C:\Users\operator\sensitive",
                r"see (C:\Users\operator\sensitive)",
                r"\\server\share\operator-sensitive",
            ):
                private_record = json.loads(json.dumps(record))
                private_record["limitations"] = [private_path]
                record_path.write_text(
                    json.dumps(private_record), encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    MATRIX.MatrixError, "host-specific path"
                ):
                    load(root, MATRIX.sha256_file(record_path))

            public_url_record = json.loads(json.dumps(record))
            public_url_record["limitations"] = [
                "See https://example.invalid/public-evidence for context.",
                "The roles are baseline / candidate.",
            ]
            record_path.write_text(
                json.dumps(public_url_record), encoding="utf-8"
            )
            observed, _digest = load(root, MATRIX.sha256_file(record_path))
            self.assertEqual(observed, public_url_record)

            aliased = json.loads(json.dumps(record))
            aliased["verification"]["scenarios"][0]["id"] = \
                "legacy-coexistence"
            record_path.write_text(json.dumps(aliased), encoding="utf-8")
            with self.assertRaisesRegex(MATRIX.MatrixError, "scenario IDs"):
                load(root, MATRIX.sha256_file(record_path))

            record_path.write_text(
                '{"schema_version":1,"schema_version":2}', encoding="utf-8"
            )
            with self.assertRaisesRegex(MATRIX.MatrixError, "duplicate JSON key"):
                load(root, MATRIX.sha256_file(record_path))

    def test_public_record_validator_is_mandatory_and_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            record = root / MATRIX.PUBLIC_RECORD_PATH
            record.parent.mkdir(parents=True)
            record.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(MATRIX.MatrixError, "unavailable"):
                MATRIX.run_public_record_validator(root, record)

            validator = root / MATRIX.PUBLIC_VALIDATOR_PATH
            validator.parent.mkdir(parents=True)
            validator.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            with mock.patch.object(
                MATRIX.subprocess, "run", return_value=mock.Mock(returncode=1)
            ):
                with self.assertRaisesRegex(MATRIX.MatrixError, "failed"):
                    MATRIX.run_public_record_validator(root, record)
            with mock.patch.object(
                MATRIX.subprocess, "run", return_value=mock.Mock(returncode=0)
            ) as run:
                MATRIX.run_public_record_validator(root, record)
                self.assertEqual(run.call_args.kwargs["cwd"], root)
                self.assertEqual(
                    set(run.call_args.kwargs["env"]),
                    set(MATRIX.CHILD_ENVIRONMENT) | {"TMPDIR"},
                )

    def test_recorded_files_bind_declarations_and_provenance(self):
        before = {}
        after = {}
        reviewed_files = []
        provenance_entries = []
        for index, (operation, path) in enumerate(
            MATRIX.TECHNICAL_CHANGE_ENTRIES, start=1
        ):
            after[path] = {
                "mode": "100755" if path != "tests/compatibility/legacy/README.md"
                else "100644",
                "bytes": 1000 + index,
                "git_blob": f"{index:040x}",
                "sha256": f"{index:064x}",
            }
            before[path] = None if operation == "A" else {
                "mode": "100755",
                "bytes": 900 + index,
                "git_blob": f"{index + 20:040x}",
                "sha256": f"{index + 20:064x}",
            }
            metadata = MATRIX.REVIEWED_FILE_METADATA[path]
            reviewed_files.append({
                "action": MATRIX.GIT_OPERATION_NAMES[operation],
                "after": after[path],
                "before": before[path],
                "path": path,
                **metadata,
            })
            provenance_entries.append({
                "blob": after[path]["git_blob"],
                "bytes": after[path]["bytes"],
                "mode": after[path]["mode"],
                "path": path,
                "provenance": metadata["provenance"]["code"],
                "sha256": after[path]["sha256"],
            })
        inventory = {
            "change_id": MATRIX.CHANGE_ID,
            "entries": provenance_entries,
            "schema": "ergon-authorship-inventory/v1",
        }
        record = {
            "provenance_inventory": {
                "inventory_sha256": MATRIX.canonical_json_sha256(inventory),
            },
            "files": reviewed_files,
        }

        parent_commit = "parent"

        def file_identity(_root, revision, path):
            if revision == parent_commit:
                return before[path]
            return after[path]

        with mock.patch.object(
            MATRIX, "git_file_identity", side_effect=file_identity
        ):
            MATRIX.validate_recorded_files(
                "source", parent_commit, "candidate", record
            )
            tampered = json.loads(json.dumps(record))
            tampered["files"][0]["role"] = "unreviewed role"
            with self.assertRaisesRegex(MATRIX.MatrixError, "declaration mismatch"):
                MATRIX.validate_recorded_files(
                    "source", parent_commit, "candidate", tampered
                )

    def test_inherited_selection_must_be_byte_for_byte_equivalent(self):
        inventory = {
            "python_files": ["a.py", "test_runner.py"],
            "non_scripts": ["test_runner.py"],
            "test_params": {},
            "selected_scripts": ["a.py"],
            "test_runner_sha256": "0" * 64,
            "timing_sha256": "1" * 64,
        }
        with mock.patch.object(
            MATRIX, "functional_inventory", side_effect=[inventory, inventory]
        ):
            self.assertEqual(
                MATRIX.require_unchanged_functional_selection("a", "b"), inventory
            )
        changed = dict(inventory, selected_scripts=[])
        with mock.patch.object(
            MATRIX, "functional_inventory", side_effect=[inventory, changed]
        ):
            with self.assertRaisesRegex(MATRIX.MatrixError, "selection changed"):
                MATRIX.require_unchanged_functional_selection("a", "b")

    def test_child_environment_is_closed_and_tmpdir_is_unique(self):
        with tempfile.TemporaryDirectory() as root:
            first = Path(root) / "first"
            second = Path(root) / "second"
            first.mkdir()
            second.mkdir()
            os.environ["ERGON_PARENT_ONLY_SECRET"] = "must-not-cross"
            first_env = MATRIX.child_environment(first)
            second_env = MATRIX.child_environment(second)
            self.assertEqual(
                set(first_env), set(MATRIX.CHILD_ENVIRONMENT) | {"TMPDIR"}
            )
            self.assertNotIn("ERGON_PARENT_ONLY_SECRET", first_env)
            self.assertNotEqual(first_env["TMPDIR"], second_env["TMPDIR"])

    def test_execution_cache_and_bytecode_are_confined(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            candidate_source = root / "candidate-source"
            candidate_source.mkdir()
            work_root = root / "work"
            work_root.mkdir()
            builds = {
                role: {
                    "binary": root / f"{role}-build" / "src" / "bitcoind",
                    "config": root / f"{role}-build" / "test" / "config.ini",
                }
                for role in MATRIX.BUILD_ROLES
            }
            process = mock.Mock(pid=12345, returncode=0)
            process.communicate.return_value = (b"Tests successful\n", b"")
            commands = []
            with mock.patch.object(
                MATRIX.subprocess, "Popen", return_value=process
            ) as popen, mock.patch.object(MATRIX, "terminate_process_group"):
                for offset, execution in enumerate(MATRIX.EXECUTIONS[1:3]):
                    MATRIX.run_execution(
                        execution,
                        candidate_source=candidate_source,
                        builds=builds,
                        work_root=work_root,
                        portseed=700 + offset,
                        timeout=10,
                    )
                    commands.append(popen.call_args.args[0])

            cachedirs = []
            datadirs = []
            for execution, command in zip(MATRIX.EXECUTIONS[1:3], commands):
                execution_root = work_root / execution["id"]
                self.assertEqual(command[:2], [sys.executable, "-B"])
                cachedir = f"--cachedir={execution_root / 'cache'}"
                datadir = f"--tmpdir={execution_root / 'datadir'}"
                self.assertIn(cachedir, command)
                self.assertIn(datadir, command)
                self.assertFalse(any(
                    str(candidate_source / "test" / "cache") in argument
                    for argument in command
                ))
                cachedirs.append(cachedir)
                datadirs.append(datadir)
            self.assertEqual(len(set(cachedirs)), 2)
            self.assertEqual(len(set(datadirs)), 2)

    def test_source_identities_are_revalidated_after_execution(self):
        with tempfile.TemporaryDirectory() as root:
            baseline = (Path(root) / "baseline").resolve()
            candidate = (Path(root) / "candidate").resolve()
            baseline.mkdir()
            candidate.mkdir()
            baseline_identity = {
                "clean": True,
                "commit": MATRIX.BASE_COMMIT,
                "tree": MATRIX.BASE_TREE,
            }
            candidate_identity = {
                "clean": True,
                "commit": "1" * 40,
                "tree": "2" * 40,
            }
            with mock.patch.object(
                MATRIX,
                "source_identity",
                side_effect=[
                    (baseline, baseline_identity),
                    (candidate, candidate_identity),
                ],
            ):
                MATRIX.require_source_identities_unchanged(
                    baseline,
                    baseline_identity,
                    candidate,
                    candidate_identity,
                )
            changed = dict(candidate_identity, clean=False)
            with mock.patch.object(
                MATRIX,
                "source_identity",
                side_effect=[
                    (baseline, baseline_identity),
                    (candidate, changed),
                ],
            ):
                with self.assertRaisesRegex(
                    MATRIX.MatrixError, "source tree changed"
                ):
                    MATRIX.require_source_identities_unchanged(
                        baseline,
                        baseline_identity,
                        candidate,
                        candidate_identity,
                    )
            self.assertIn(
                "require_source_identities_unchanged",
                function_source(MODULE_PATH, "main"),
            )

    def test_reviewed_signing_trust_root_is_exact(self):
        MATRIX.verify_signing_trust_root()

    def test_git_commands_trust_only_the_resolved_input_repository(self):
        with tempfile.TemporaryDirectory() as root:
            repository = Path(root) / "repository"
            repository.mkdir()
            self.assertEqual(
                MATRIX.git_command(repository, "status", "--porcelain=v1"),
                [
                    "git",
                    "-c",
                    f"safe.directory={repository.resolve()}",
                    "-C",
                    str(repository.resolve()),
                    "status",
                    "--porcelain=v1",
                ],
            )
            environment = MATRIX.git_environment(root)
            self.assertEqual(environment["GIT_CONFIG_GLOBAL"], "/dev/null")
            self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(environment["GIT_NO_REPLACE_OBJECTS"], "1")
            self.assertNotIn("HOME", environment)

    def test_incomplete_or_rewritten_git_history_fails_closed(self):
        with mock.patch.object(MATRIX, "git_output", return_value="true"):
            with self.assertRaisesRegex(MATRIX.MatrixError, "non-shallow"):
                MATRIX.require_complete_git_history("source")
        with mock.patch.object(
            MATRIX, "git_output", side_effect=["false", "refs/replace/object"]
        ):
            with self.assertRaisesRegex(MATRIX.MatrixError, "replace ref"):
                MATRIX.require_complete_git_history("source")
        with tempfile.TemporaryDirectory() as root:
            git_dir = Path(root) / "git-dir"
            (git_dir / "info").mkdir(parents=True)
            (git_dir / "info" / "grafts").write_text(
                "0" * 40 + " " + "1" * 40 + "\n", encoding="ascii"
            )
            with mock.patch.object(
                MATRIX, "git_output",
                side_effect=["false", "", str(git_dir)],
            ):
                with self.assertRaisesRegex(MATRIX.MatrixError, "graft file"):
                    MATRIX.require_complete_git_history("source")

    def test_aliases_and_incomplete_results_fail_closed(self):
        with tempfile.TemporaryDirectory() as root:
            first = Path(root) / "first"
            second = Path(root) / "second"
            first.write_bytes(b"one")
            os.link(first, second)
            with self.assertRaises(MATRIX.MatrixError):
                MATRIX.require_distinct([first, second], "fixture")
        for returncode, stdout, stderr in (
            (1, b"Tests successful", b""),
            (0, b"Test skipped", b""),
            (0, b"", b""),
        ):
            with self.assertRaises(MATRIX.MatrixError):
                MATRIX.validate_test_output(returncode, stdout, stderr)

    def test_reindex_lifecycle_markers_fail_closed(self):
        success = b"Tests successful\n" + b"\n".join(
            MATRIX.MIXED_NODE_SUCCESS_MARKERS
        )
        MATRIX.validate_test_output(
            0, success, b"", MATRIX.MIXED_NODE_SUCCESS_MARKERS
        )
        for missing in MATRIX.MIXED_NODE_SUCCESS_MARKERS:
            incomplete = b"Tests successful\n" + b"\n".join(
                marker
                for marker in MATRIX.MIXED_NODE_SUCCESS_MARKERS
                if marker != missing
            )
            with self.assertRaisesRegex(
                MATRIX.MatrixError, "required lifecycle marker"
            ):
                MATRIX.validate_test_output(
                    0,
                    incomplete,
                    b"",
                    MATRIX.MIXED_NODE_SUCCESS_MARKERS,
                )

    def test_source_build_roots_and_build_files_are_disjoint(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "source"
            nested_build = source / "ignored-build"
            sibling_build = root / "sibling-build"
            source.mkdir()
            nested_build.mkdir()
            sibling_build.mkdir()
            with self.assertRaisesRegex(MATRIX.MatrixError, "contain one another"):
                MATRIX.require_disjoint_roots(
                    [source, nested_build], "source and build roots"
                )
            self.assertEqual(
                MATRIX.require_disjoint_roots(
                    [source, sibling_build], "source and build roots"
                ),
                [source.resolve(), sibling_build.resolve()],
            )

            outside = root / "outside-bitcoind"
            outside.write_bytes(b"binary")
            escaped = sibling_build / "bitcoind"
            escaped.symlink_to(outside)
            with self.assertRaisesRegex(MATRIX.MatrixError, "declared root"):
                MATRIX.require_strict_descendant(
                    escaped, sibling_build, "daemon file"
                )

    def test_report_cannot_be_deleted_with_work_root(self):
        with tempfile.TemporaryDirectory() as root:
            work_root = Path(root) / "work"
            work_root.mkdir()
            with self.assertRaises(MATRIX.MatrixError):
                MATRIX.require_report_outside_work_root(
                    work_root / "report.json", work_root
                )
            report = MATRIX.require_report_outside_work_root(
                Path(root) / "report.json", work_root
            )
            self.assertEqual(report, (Path(root) / "report.json").resolve())
            source = Path(root) / "source"
            source.mkdir()
            with self.assertRaisesRegex(MATRIX.MatrixError, "input root"):
                MATRIX.require_report_outside_inputs(
                    source / "report.json", [source]
                )

    def test_disposable_work_root_cannot_overlap_inputs(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root) / "source"
            source.mkdir()
            with self.assertRaises(MATRIX.MatrixError):
                MATRIX.require_work_root_outside_inputs(source / "work", [source])

    def test_process_group_cleanup_remains_bounded_on_permission_error(self):
        process = mock.Mock(pid=12345)
        process.poll.return_value = None
        process.wait.side_effect = [None, subprocess.TimeoutExpired("wait", 10)]
        with mock.patch.object(MATRIX.os, "killpg", side_effect=PermissionError), \
                mock.patch.object(
                    MATRIX, "wait_process_group_gone", return_value=False):
            with self.assertRaisesRegex(MATRIX.MatrixError, "process-group cleanup"):
                MATRIX.terminate_process_group(process)

    def test_public_report_excludes_private_state(self):
        build = {
            "public": {
                "bitcoind_bytes": 1,
                "bitcoind_sha256": "0" * 64,
                "config_sha256": "1" * 64,
                "config_source_binding_checked": True,
                "binary_to_source_provenance": "external_build_record_required",
            }
        }
        inventory = {
            "python_files": ["a.py"],
            "non_scripts": [],
            "test_params": {},
            "selected_scripts": ["a.py"],
            "test_runner_sha256": "0" * 64,
            "timing_sha256": "1" * 64,
        }
        report = MATRIX.public_report(
            {"commit": MATRIX.BASE_COMMIT, "tree": MATRIX.BASE_TREE, "clean": True},
            {"commit": "2" * 40, "tree": "3" * 40, "clean": True},
            "4" * 64,
            {
                "git_version": "git version test",
                "integration_parent": {
                    "candidate_direct_parent": True,
                    "commit": "6" * 40,
                    "controlled_paths_unchanged": True,
                    "explicit_reviewed_cli": True,
                    "preimages_verified": True,
                    "record_match": True,
                    "remote_ancestor": True,
                    "tree": "7" * 40,
                },
                "origin_main_oid": "5" * 40,
                "signatures": {},
                "trust_root": MATRIX.SIGNATURE_CONTRACT,
            },
            {"baseline": build, "candidate": build},
            inventory,
            [execution["id"] for execution in MATRIX.EXECUTIONS],
        )
        encoded = json.dumps(report, sort_keys=True)
        for host_prefix in ("/Users/", "/home/", "/Volumes/"):
            self.assertNotIn(host_prefix, encoded)
        if Path.home() != Path("/"):
            self.assertNotIn(str(Path.home()), encoded)
        self.assertFalse(report["raw_output_retained"])
        self.assertFalse(report["host_specific_absolute_paths_retained"])
        self.assertFalse(report["parent_environment_retained"])
        self.assertEqual(set(report["executions"].values()), {"pass"})
        self.assertEqual(
            report["node_launch_modes"]["inherited-functional-default-launch"],
            "framework-default-inherits-closed-runner-environment",
        )
        with self.assertRaisesRegex(MATRIX.MatrixError, "every exact execution"):
            MATRIX.public_report(
                {"commit": MATRIX.BASE_COMMIT, "tree": MATRIX.BASE_TREE},
                {"commit": "2" * 40, "tree": "3" * 40},
                "4" * 64,
                {},
                {"baseline": build, "candidate": build},
                inventory,
                [MATRIX.EXECUTIONS[0]["id"]],
            )


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""Reject finite frontier scope, source, evidence and old-control drift."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_service_catchup as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_layer(self):
        from check_chronik_cold_rebuild import TECHNICAL, POLICY
        expected = gate.TECHNICAL | gate.POLICY | TECHNICAL | POLICY
        from check_chronik_startup_metadata import TECHNICAL, POLICY
        self.assertEqual(gate.validate(self.root), expected | TECHNICAL | POLICY)
        self.assertEqual(gate.input_binding(self.root), gate.input_binding(self.root))

    def test_contract_and_inventory_rejections(self):
        original = gate.load_json
        manifest = original(self.root / gate.MANIFEST)
        for mutate in (lambda r: r["contract"].update(compile_default=True),
                       lambda r: r["contract"].update(upstream_engine_change=True),
                       lambda r: r["contract"].update(native_timeout_seconds=120),
                       lambda r: r["contract"].update(native_leaf_assertions=71),
                       lambda r: r["contract"].update(rapid_blocks=299),
                       lambda r: r["contract"].update(knowledge_status="Reproduced"),
                       lambda r: r["public_parent"].update(commit="0" * 40),
                       lambda r: r["files"].pop(), lambda r: r["files"].reverse(),
                       lambda r: r["files"][0]["after"].update(sha256="0" * 64)):
            bad = copy.deepcopy(manifest); mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p:
                bad if p == self.root / gate.MANIFEST else original(p)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_frozen_inputs_and_changed_source_refused(self):
        original = gate.current_identity
        for path in ("src/init.cpp", "chronik/Cargo.lock", "chronik/chronik-indexer/src/indexer.rs",
                     "tests/compatibility/upstream-chronik/plugin_alp_service.py",
                     "docs/engineering/changes/ergon-change-0054.json", *sorted(gate.COMPILED)):
            with patch.object(gate, "current_identity", side_effect=lambda r, p:
                dict(original(r, p), sha256="0" * 64) if p == path else original(r, p)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_record_rejections(self):
        original = gate.load_json
        for mutate in (lambda r: r["evidence"].update(artifacts=["synthetic.json"]),
                       lambda r: r["evidence"].update(knowledge_status="Reproduced"),
                       lambda r: r["provenance_inventory"].update(inventory_sha256="0" * 64),
                       lambda r: r["files"].pop()):
            bad = original(self.root / gate.RECORD); mutate(bad)
            with patch.object(gate, "load_json", side_effect=lambda p:
                bad if p == self.root / gate.RECORD else original(p)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_successor_requires_exact_preimage_and_does_not_free_unlisted_paths(self):
        path = sorted(gate.COMPILED)[0]
        before = gate.before_identity(self.root, path)
        self.assertEqual(gate.successor_identity(self.root, path, before), gate.current_identity(self.root, path))
        with self.assertRaises(ValueError): gate.successor_identity(self.root, path, dict(before, sha256="0" * 64))
        # init.cpp now has an exact successor for service help text. Use an
        # input that remains outside every reviewed technical successor.
        frozen = "src/chainparams.cpp"
        previous = gate.before_identity(self.root, frozen)
        self.assertEqual(gate.successor_identity(self.root, frozen, previous), previous)

    def test_inherited_matrix_deadline_and_native_body_cannot_be_rewritten(self):
        runner = "tests/compatibility/upstream-chronik/run_repetitions.py"
        native = "src/test/chronik_upstream_archival_tests.cpp"
        old_runner = gate.git(self.root, "show", gate.PARENT + ":" + runner).decode()
        old_native = gate.git(self.root, "show", gate.PARENT + ":" + native).decode()
        from check_chronik_cold_rebuild import PARENT as accepted_successor
        new_runner = gate.git(self.root, "show", accepted_successor + ":" + runner).decode()
        new_native = gate.git(self.root, "show", accepted_successor + ":" + native).decode()
        gate.validate_retained_controls(old_runner, new_runner, old_native, new_native)
        for bad in (new_runner.replace("timeout=60", "timeout=120"),
                    new_runner.replace("timeout=60", "timeout=59")):
            with self.assertRaises(ValueError): gate.validate_retained_controls(old_runner, bad, old_native, new_native)
        name = gate.re.findall(r"BOOST_AUTO_TEST_CASE\((\w+)\)", old_native)[0]
        bad = new_native.replace("BOOST_AUTO_TEST_CASE(" + name + ") {", "BOOST_AUTO_TEST_CASE(" + name + ") {\n    ;")
        with self.assertRaises(ValueError): gate.validate_retained_controls(old_runner, new_runner, old_native, bad)

    def test_cache_is_immutable_revision_only_and_has_no_mutable_result_alias(self):
        root, revision = Path("synthetic-only-cache-root"), "f" * 40
        def git(r, *args):
            return b"100644 blob synthetic\tfile\n" if args[0] == "ls-tree" else b"synthetic bytes\n"
        with patch.object(gate, "git", side_effect=git) as command:
            first = gate.revision_identity(root, revision, "file")
            expected = dict(first)
            first["sha256"] = "0" * 64
            self.assertEqual(gate.revision_identity(root, revision, "file"), expected)
            self.assertEqual(command.call_count, 4)
        for mutable in ("HEAD", "main", "origin/main", "f" * 39):
            with self.assertRaises(ValueError): gate.revision_identity(root, mutable, "file")

    def test_shared_git_cache_never_caches_mutable_refs_or_live_checks(self):
        import check_chronik_upstream_link as link
        root, revision = Path("synthetic-shared-cache-root"), "e" * 40
        with patch.object(link, "_run_git", return_value=b"synthetic") as command:
            for args in (("ls-tree", revision, "--", "file"), ("show", revision + ":file")):
                link.git(root, *args); link.git(root, *args)
            self.assertEqual(command.call_count, 2)
            for args in (("show", "HEAD:file"), ("ls-tree", "main", "--", "file"),
                         ("rev-parse", revision + "^{tree}"), ("diff", "--name-only", revision),
                         ("verify-commit", revision), ("merge-base", "--is-ancestor", revision, "HEAD")):
                before = command.call_count
                link.git(root, *args); link.git(root, *args)
                self.assertEqual(command.call_count - before, 2)


if __name__ == "__main__":
    unittest.main()

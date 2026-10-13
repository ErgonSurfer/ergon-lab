#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic negative source-policy tests; no node, database or evidence input."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import check_chronik_chainstate_reads as gate


class Contract(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[2]
        self.synthetic_root = tempfile.TemporaryDirectory(prefix="synthetic-read-policy-")
        self.addCleanup(self.synthetic_root.cleanup)
        self.before = gate.identity(b"synthetic preimage\n", "100644")
        self.after = gate.identity(b"synthetic postimage\n", "100644")
        self.record = {
            "$comment": "SPDX-License-Identifier: MIT",
            "schema": "ergon-owned-chainstate-reads/v1",
            "public_parent": {"commit": gate.PARENT, "tree": gate.PARENT_TREE},
            "parent_status": "public-review-branch-not-assumed-accepted",
            "license": "MIT", "contract": copy.deepcopy(gate.CONTRACT),
            "files": [{"path": path, "before": self.before, "after": self.after,
                       "provenance": "PUBLIC+A1/MIT",
                       "reachability": "native-storage-api-always-built" if path in {
                           "src/coins.cpp", "src/coins.h", "src/dbwrapper.cpp",
                           "src/dbwrapper.h", "src/txdb.cpp", "src/txdb.h"
                       } else "opt-in-build-or-test-only"}
                      for path in sorted(gate.TECHNICAL)],
        }
        self.extra = ""
        self.tree = gate.PARENT_TREE
        def record_identity(value):
            return {("git_blob" if k == "git_blob_sha1" else k): v for k, v in value.items()}
        self.change = {"change_id": "ERGON-CHANGE-0046", "files": [
            {"path": path, "before": record_identity(self.before),
             "after": record_identity(self.after if path in gate.TECHNICAL else self.before)}
            for path in sorted((gate.TECHNICAL | gate.POLICY) - {
                "docs/engineering/changes/ergon-change-0046.json"})]}

    def git(self, root, *args):
        if args[0] == "rev-parse":
            return (self.tree + "\n").encode()
        if args[0] == "merge-base":
            return b""
        if args[0] == "diff":
            names = sorted(gate.TECHNICAL | gate.POLICY | ({self.extra} if self.extra else set()))
            return b"\0".join(p.encode() for p in names) + b"\0"
        if args[0] == "ls-files":
            return b""
        if args[0] == "ls-tree":
            return b"100644 synthetic\n"
        raise AssertionError("unexpected synthetic Git operation")

    def run_gate(self, record=None, current=None):
        def observed(root, path):
            if current is not None and path == sorted(gate.TECHNICAL)[0]:
                return current
            return self.after if path in gate.TECHNICAL else self.before
        def loaded(path):
            return self.change if path.name == "ergon-change-0046.json" else record or self.record
        with patch.object(gate, "git", self.git), patch.object(gate, "load_json", side_effect=loaded), \
             patch.object(gate, "before_identity", return_value=self.before), \
             patch.object(gate, "current_identity", observed):
            return gate.validate(Path(self.synthetic_root.name))

    def test_exact_layer(self):
        self.assertEqual(self.run_gate(), gate.TECHNICAL | gate.POLICY)
        self.assertEqual(len(gate.TECHNICAL), 13)
        self.assertEqual(len(gate.POLICY), 7)

    def test_boundary_and_inventory_rejections(self):
        mutations = (
            lambda r: r.update(license="other"),
            lambda r: r.update(schema="other"),
            lambda r: r.update(parent_status="accepted"),
            lambda r: r["public_parent"].update(commit="0" * 40),
            lambda r: r["contract"].update(daemon_admission=True),
            lambda r: r["contract"].update(consensus_change=True),
            lambda r: r["contract"].update(knowledge_status="Reproduced"),
            lambda r: r["contract"].update(incoming_evidence=["synthetic.json"]),
            lambda r: r["files"].pop(),
            lambda r: r["files"].reverse(),
            lambda r: r["files"][0].update(path="../escaping"),
            lambda r: r["files"][0].update(before=self.after),
            lambda r: r["files"][0].update(after=self.before),
            lambda r: r["files"][0].update(provenance="donor"),
            lambda r: r["files"][0].update(reachability="consensus"),
        )
        for index, mutate in enumerate(mutations):
            record = copy.deepcopy(self.record)
            mutate(record)
            with self.subTest(mutation=index), self.assertRaises(ValueError):
                self.run_gate(record)

    def test_source_and_path_rejections(self):
        with self.assertRaises(ValueError):
            self.run_gate(current=self.before)
        self.extra = "src/validation.cpp"
        with self.assertRaises(ValueError):
            self.run_gate()
        self.extra = ""
        self.tree = "0" * 40
        with self.assertRaises(ValueError):
            self.run_gate()

    def test_policy_record_rejections(self):
        self.change["files"][0]["after"]["sha256"] = "0" * 64
        with self.assertRaises(ValueError):
            self.run_gate()

    def test_combined_runner_projection(self):
        sys.path.insert(0, str(self.root / "tests/compatibility/upstream-chronik"))
        import run_repetitions as runner
        names = [r["path"] for r in json.loads((self.root /
                 "docs/engineering/chronik/upstream-host-adaptation-v1.json").read_text())["files"]]
        raw = b"synthetic\n"
        identity = gate.identity(raw, "100644")
        rows = [{"path": p, "after": identity} for p in names]
        layer = {"schema": "ergon-owned-chainstate-reads/v1",
                 "public_parent": {"commit": gate.PARENT, "tree": gate.PARENT_TREE},
                 "files": [{"path": p, "before": identity if p in names else None,
                            "after": identity} for p in sorted(gate.TECHNICAL)]}
        with tempfile.TemporaryDirectory(prefix="synthetic-read-projection-") as name:
            root = Path(name).resolve()
            source = root / "source"
            source.mkdir()
            for path in set(names) | gate.TECHNICAL:
                target = source / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            host, read = root / "host.json", root / "reads.json"
            host.write_bytes(runner.canonical({"files": rows}))
            read.write_bytes(runner.canonical(layer))
            first = runner.source_projection(source, host, read)
            self.assertEqual(len(first), 64)
            for mutate in (
                lambda r: r["files"].pop(),
                lambda r: r["files"].reverse(),
                lambda r: r["public_parent"].update(tree="0" * 40),
                lambda r: r["files"][0].update(before=None),
            ):
                invalid = copy.deepcopy(layer)
                mutate(invalid)
                read.write_bytes(runner.canonical(invalid))
                with self.assertRaises(runner.Rejected):
                    runner.source_projection(source, host, read)
            read.write_bytes(runner.canonical(layer))
            target = source / "src/coins.cpp"
            target.write_bytes(b"synthetic drift\n")
            with self.assertRaises(runner.Rejected):
                runner.source_projection(source, host, read)
            target.unlink()
            target.symlink_to(source / "src/coins.h")
            with self.assertRaises(runner.Rejected):
                runner.source_projection(source, host, read)


if __name__ == "__main__":
    unittest.main()

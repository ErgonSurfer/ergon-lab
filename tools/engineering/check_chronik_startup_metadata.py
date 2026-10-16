# SPDX-License-Identifier: MIT
"""Finite startup-metadata successor; upstream Chronik engines stay unchanged."""
import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, load_json, require
from check_chronik_service_catchup import revision_identity

PARENT = "de29766281df972e33d1e60ae77db4d356fb968d"
TREE = "e399a8e4ce142843ab4177330b84e07bb3377f05"
MANIFEST = "docs/engineering/chronik/upstream-startup-metadata-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0057.json"
COMPILED = frozenset({"chronik/chronik-cpp/chronik_bridge.cpp",
    "chronik/chronik-cpp/chronik_bridge.h", "src/test/chronik_upstream_archival_tests.cpp"})
TECHNICAL = COMPILED | {"tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/startup_metadata.py",
    "tests/compatibility/upstream-chronik/startup_metadata_self_test.py"}
POLICY = frozenset({MANIFEST, RECORD, "docs/engineering/chronik/upstream-startup-metadata.md",
    "tools/engineering/check_chronik_startup_metadata.py",
    "tools/engineering/check_chronik_startup_metadata_self_test.py",
    "tools/engineering/check_chronik_cold_rebuild.py",
    "tools/engineering/check_chronik_cold_rebuild_self_test.py",
    "tools/engineering/check_chronik_service_catchup.py",
    "tools/engineering/check_chronik_service_catchup_self_test.py",
    "tools/engineering/check_chronik_plugin_alp_self_test.py",
    "tools/engineering/check_chronik_service_alp_self_test.py",
    "tools/engineering/check_chronik_regtest_service_self_test.py",
    "tools/engineering/check_chronik_plugin_restart_self_test.py",
    "tools/engineering/check_chronik_service_startup_self_test.py",
    "tools/engineering/check_chronik_service_recovery_self_test.py",
    "tools/engineering/check_chronik_service_plugins_self_test.py",
    ".github/workflows/chronik-upstream-link.yml"})
REUSED = ("tests/compatibility/upstream-chronik/cold_rebuild_service.py",
    "tests/compatibility/upstream-chronik/catchup_service.py",
    "tests/compatibility/upstream-chronik/plugin_alp_service.py",
    "chronik/chronik-lib/src/ergon_archival.rs", "chronik/chronik-indexer/src/indexer.rs")
CASES = {"startup_materialization_allows_native_progress_without_borrowing": 9,
    "startup_materialization_storage_drift_is_not_copied_or_accepted": 5,
    "startup_materialization_exception_keeps_graph_unpublished": 6}
CONTRACT = {"compile_default": False, "runtime_default": False,
    "profile": "regtest-plugins", "network": "bounded-unpruned-regtest",
    "maximum_height": 2000, "maximum_indexes": 4096, "maximum_mempool": 4096,
    "native_lock_scope": "coherent-value-copy-only-no-graph-map-allocation",
    "materialization": "owned-index-map-parent-links-and-chain-built-after-both-native-locks",
    "publication": "complete-owned-graph-only",
    "test_hook": "native-cpp-fixture-only-after-value-copy-before-materialization",
    "native_leaves": CASES, "native_timeout_seconds": 60,
    "default_repetitions": 3, "fresh_processes": 9, "passing_assertions": 60,
    "retained_controls": "all-53-inherited-native-cases-and-existing-runners-clients-budgets-deadlines",
    "automatic_retry": False, "upstream_engine_change": False,
    "dependency_change": False, "consensus_change": False,
    "incoming_evidence": [], "knowledge_status": "Open Question",
    "evidence_ceiling": "record-review-only"}


def before_identity(root, path):
    return revision_identity(root, PARENT, path)


def successor_identity(root, path, previous):
    if not (root / MANIFEST).exists():
        return previous
    rows = [row for row in load_json(root / MANIFEST)["files"] if row["path"] == path]
    if not rows:
        return previous
    require(path in TECHNICAL and len(rows) == 1 and rows[0]["before"] == previous,
            "startup metadata successor preimage drift")
    return rows[0]["after"]


def input_binding(root):
    rows = [{"path": path, "identity": current_identity(root, path)}
            for path in sorted(TECHNICAL | set(REUSED))]
    raw = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    return hashlib.sha256(raw.encode()).hexdigest()


def native_cases(text):
    matches = list(re.finditer(r"^BOOST_AUTO_TEST_CASE\(\s*(\w+)\s*\) \{\n.*?^\}", text, re.M | re.S))
    rows = {match[1]: match[0] for match in matches}
    require(len(rows) == len(matches), "ambiguous native startup case")
    return rows


def without_projection(text):
    nodes = [node for node in ast.parse(text).body
             if isinstance(node, ast.FunctionDef) and node.name == "source_projection"]
    require(len(nodes) == 1, "ambiguous source projection")
    lines, node = text.splitlines(keepends=True), nodes[0]
    return "".join(lines[:node.lineno - 1] + lines[node.end_lineno:])


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"",
            "startup metadata parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and
            manifest["schema"] == "ergon-upstream-startup-metadata/v1" and
            manifest["license"] == "MIT" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT and
            [row["path"] for row in manifest["files"]] == sorted(TECHNICAL),
            "startup metadata manifest drift")
    for row in manifest["files"]:
        before = before_identity(root, row["path"])
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["before"] == before and row["after"] == current_identity(root, row["path"]) and
                row["after"] != before and row["after"]["mode"] == "100644" and
                row["provenance"] == ("A1/MIT" if before is None else "PUBLIC+A1/MIT") and
                row["reachability"] == ("bounded-regtest-native-or-test-only" if row["path"] in COMPILED else "test-only"),
                "startup metadata source drift")
    changed = {path.decode() for path in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if path}
    changed |= {path.decode() for path in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if path}
    require(changed == TECHNICAL | POLICY, "unexpected startup metadata path")
    from check_change import ValidationError, validate as validate_change
    record = load_json(root / RECORD)
    try:
        validate_change(record, RECORD)
    except ValidationError as error:
        raise ValueError("startup metadata governance schema drift") from error
    require(record["change_id"] == "ERGON-CHANGE-0057" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question",
                                   "ceiling": "record-review-only", "artifacts": []} and
            [row["path"] for row in record["files"]] == sorted(changed - {RECORD}),
            "startup metadata governance drift")
    def governed(value):
        return None if value is None else {("git_blob_sha1" if key == "git_blob" else key): item
                                           for key, item in value.items()}
    for row in record["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                governed(row["after"]) == current_identity(root, row["path"]),
                "startup metadata policy drift")
    old_native = git(root, "show", PARENT + ":src/test/chronik_upstream_archival_tests.cpp").decode()
    new_native = (root / "src/test/chronik_upstream_archival_tests.cpp").read_text()
    before, after = native_cases(old_native), native_cases(new_native)
    require(len(before) == 53 and set(after) - set(before) == set(CASES) and
            all(after.get(name) == body for name, body in before.items()),
            "startup metadata changed inherited native case body")
    runner_path = "tests/compatibility/upstream-chronik/run_repetitions.py"
    require(without_projection(git(root, "show", PARENT + ":" + runner_path).decode()) ==
            without_projection((root / runner_path).read_text()),
            "startup metadata changed inherited runner behavior")
    client = ast.parse((root / "tests/compatibility/upstream-chronik/startup_metadata.py").read_text())
    assignments = {target.id: ast.literal_eval(node.value) for node in client.body
                   if isinstance(node, ast.Assign) for target in node.targets
                   if isinstance(target, ast.Name) and target.id == "CASES"}
    require(assignments == {"CASES": tuple(CASES.items())}, "startup metadata public cases drift")
    bridge = (root / "chronik/chronik-cpp/chronik_bridge.cpp").read_text()
    begin = bridge.index("void ChronikBridge::capture_startup_snapshot")
    end = bridge.index("bool ChronikBridge::finish_startup_snapshot", begin)
    capture = bridge[begin:end]
    locked = capture.index("LOCK2(cs_main, m_node.mempool.cs)")
    unlocked = capture.index("AssertLockNotHeld(cs_main)", locked)
    require(locked < unlocked < capture.index("after_metadata()") <
            capture.index("std::make_unique<CBlockIndex>") < capture.index("m_startup = std::move(owned)") and
            "index->pprev" in capture and "Metadata" in capture,
            "startup metadata lock/materialization/publication ordering drift")
    for path in ("chronik/chronik-indexer/src/indexer.rs", "chronik/bitcoinsuite-slp/src/alp/build.rs",
                 "chronik/chronik-plugin-impl/src/context.rs", "chronik/Cargo.lock", "src/init.cpp",
                 "src/validation.cpp", "src/txmempool.cpp"):
        require(current_identity(root, path) == before_identity(root, path),
                "startup metadata changed frozen engine/consensus/dependency input")
    return TECHNICAL | POLICY


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: startup value capture bounded; owned graph materialized outside native locks")

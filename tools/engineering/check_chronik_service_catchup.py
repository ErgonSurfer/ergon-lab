# SPDX-License-Identifier: MIT
"""Finite compact-invalidation-frontier layer; no upstream engine replacement."""
import argparse
import ast
import hashlib
import json
import re
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, identity, load_json, require

PARENT = "bba9361bec898d9543a6ca479af15ccbb2594b8b"
TREE = "e9249fdf79dbe83d2b8ffc4972675f6e2c3bd4f8"
MANIFEST = "docs/engineering/chronik/upstream-service-catchup-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0055.json"
COMPILED = frozenset({"chronik/chronik-cpp/ergon_archival_worker.cpp",
    "chronik/chronik-cpp/ergon_archival_worker.h", "chronik/chronik-cpp/ergon_regtest_service.cpp",
    "src/test/chronik_upstream_archival_tests.cpp"})
TECHNICAL = COMPILED | {"tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/catchup_service.py",
    "tests/compatibility/upstream-chronik/catchup_service_self_test.py"}
POLICY = frozenset({MANIFEST, RECORD, "docs/engineering/chronik/upstream-service-catchup.md",
    "tools/engineering/check_chronik_service_catchup.py", "tools/engineering/check_chronik_upstream_link.py",
    "tools/engineering/check_chronik_service_catchup_self_test.py",
    "tools/engineering/check_chronik_plugin_alp.py", "tools/engineering/check_chronik_plugin_alp_self_test.py",
    "tools/engineering/check_chronik_service_plugins.py", "tools/engineering/check_chronik_service_plugins_self_test.py",
    "tools/engineering/check_chronik_service_alp_self_test.py",
    "tools/engineering/check_chronik_service_startup_self_test.py",
    "tools/engineering/check_chronik_service_recovery_self_test.py",
    "tools/engineering/check_chronik_regtest_service_self_test.py",
    "tools/engineering/check_chronik_plugin_restart_self_test.py",
    ".github/workflows/chronik-upstream-link.yml"})
REUSED = ("tests/compatibility/upstream-chronik/alp_service.py",
    "tests/compatibility/upstream-chronik/plugin_service.py",
    "tests/compatibility/upstream-chronik/plugin_alp_service.py",
    "tests/compatibility/upstream-chronik/regtest_service.py",
    "tests/compatibility/upstream-chronik/plugins/ergon_public_probe.py",
    "src/test/chronik_upstream_regtest_tests.cpp")
CONTRACT = {"parent_status": "published-PR57-head-not-assumed-merged",
    "compile_default": False, "runtime_default": False, "profile": "regtest-plugins",
    "network": "bounded-unpruned-regtest", "maximum_height": 2000,
    "service_notifications": "metadata-only-monotonic-accepted-completed-frontier-no-ticket-deque",
    "strict_fixture_notifications": "unchanged-bounded-deque-capacity-failure",
    "fences": "unchanged-source-generation-epoch-reconcile-and-ready-law",
    "failure_diagnostics": "fixed-first-reason-and-bounded-counter-fields-no-exception-text",
    "native_leaves": {"callback_capacity_reason_survives_drain": 10,
        "callback_notification_reason_is_not_relabelled": 8,
        "service_frontier_coalesces_ordered_invalidations": 25,
        "service_frontier_stop_discards_unpublished_range": 21,
        "service_frontier_notification_failure_is_terminal": 8},
    "native_timeout_seconds": 60, "native_leaf_assertions": 72,
    "runtime": "existing-upstream-ALP-and-public-plugin-oracles-confirm102-advance300-reopen402",
    "rapid_blocks": 300, "final_height": 402, "default_repetitions": 3,
    "daemon_processes_per_repetition": 2, "unchanged_daemon_deadlines": True,
    "retained_controls": "all-five-profiles-and-previous-matrices-selections-assertions-clients-and-deadlines",
    "consensus_change": False, "upstream_engine_change": False, "dependency_change": False,
    "arbitrary_plugins": False, "incoming_evidence": [],
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}


def revision_identity(root, revision, path):
    # Shared Git reader caches immutable bytes only; this dictionary is fresh.
    require(re.fullmatch(r"[0-9a-f]{40}", revision) is not None, "immutable revision required")
    row = git(root, "ls-tree", revision, "--", path).decode().split()
    return identity(git(root, "show", revision + ":" + path), row[0]) if row else None


def before_identity(root, path):
    return revision_identity(root, PARENT, path)


def input_binding(root):
    rows = [{"path": p, "identity": current_identity(root, p)} for p in sorted(TECHNICAL | set(REUSED))]
    return hashlib.sha256((json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()).hexdigest()


def successor_identity(root, path, previous):
    """Old layer may change only through this exact bound successor row."""
    if not (root / MANIFEST).exists():
        return previous
    rows = [r for r in load_json(root / MANIFEST)["files"] if r["path"] == path]
    if not rows:
        return previous
    require(path in TECHNICAL and len(rows) == 1 and rows[0]["before"] == previous,
            "catchup successor preimage drift")
    return rows[0]["after"]


def validate_retained_controls(old_runner, new_runner, old_native, new_native):
    def without_projection(text):
        functions = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == "source_projection"]
        require(len(functions) == 1, "ambiguous source projection")
        node = functions[0]
        lines = text.splitlines(keepends=True)
        return "".join(lines[:node.lineno - 1] + lines[node.end_lineno:])
    require(without_projection(old_runner) == without_projection(new_runner),
            "catchup changed inherited matrix/assertions/deadlines/process handling")
    def cases(text):
        rows = re.findall(r"^BOOST_AUTO_TEST_CASE\(\s*(\w+)\s*\) \{\n.*?^\}", text, re.M | re.S)
        bodies = re.findall(r"^BOOST_AUTO_TEST_CASE\(\s*\w+\s*\) \{\n.*?^\}", text, re.M | re.S)
        require(len(rows) == len(set(rows)), "ambiguous native case")
        return dict(zip(rows, bodies))
    before, after = cases(old_native), cases(new_native)
    require(len(before) == 48 and set(after) - set(before) == set(CONTRACT["native_leaves"]) and
            all(after.get(name) == body for name, body in before.items()), "catchup changed inherited native case body")


def validate(root, historical=False):
    root = root.resolve(strict=True)
    from check_chronik_cold_rebuild import MANIFEST as NEXT_MANIFEST, PARENT as NEXT_PARENT, validate as validate_next
    if not historical and (root / NEXT_MANIFEST).exists():
        return validate(root, historical=True) | validate_next(root)
    def observed(path):
        return revision_identity(root, NEXT_PARENT, path) if historical else current_identity(root, path)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "catchup parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and manifest["license"] == "MIT" and
            manifest["schema"] == "ergon-upstream-service-catchup/v1" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT, "catchup contract drift")
    require([r["path"] for r in manifest["files"]] == sorted(TECHNICAL), "catchup inventory drift")
    for row in manifest["files"]:
        before = before_identity(root, row["path"])
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["before"] == before and row["after"] == observed(row["path"]) and
                row["after"] == current_identity(root, row["path"]) and
                row["after"] != before and row["after"]["mode"] == "100644" and
                row["provenance"] == ("A1/MIT" if before is None else "PUBLIC+A1/MIT") and
                row["reachability"] == "bounded-regtest-service-or-test-only", "catchup source drift")
    revisions = (PARENT, NEXT_PARENT) if historical else (PARENT,)
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", *revisions).split(b"\0") if p}
    if not historical:
        changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == TECHNICAL | POLICY, "unexpected catchup path")
    from check_change import ValidationError, validate as validate_change
    record = load_json(root / RECORD)
    try:
        validate_change(record, RECORD)
    except ValidationError as error:
        raise ValueError("catchup governance schema drift") from error
    require(record["change_id"] == "ERGON-CHANGE-0055" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question", "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(changed - {RECORD}), "catchup governance drift")
    def governed(v):
        return None if v is None else {("git_blob_sha1" if k == "git_blob" else k): value for k, value in v.items()}
    for row in record["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                governed(row["after"]) == observed(row["path"]), "catchup policy drift")
    for path in REUSED + ("CMakeLists.txt", "chronik/UpstreamLink.cmake", "src/init.cpp", "src/validation.cpp",
            "src/txmempool.cpp", "chronik/Cargo.lock", "chronik/chronik-lib/src/ergon_archival.rs",
            "chronik/chronik-lib/src/ergon_service.rs", "chronik/chronik-plugin-impl/src/context.rs",
            "chronik/chronik-indexer/src/indexer.rs", "chronik/bitcoinsuite-slp/src/alp/build.rs",
            "docs/engineering/changes/ergon-change-0054.json", "docs/engineering/chronik/upstream-plugin-alp-v1.json"):
        require(current_identity(root, path) == before_identity(root, path), "catchup changed frozen engine/oracle/history")
    runner_path = "tests/compatibility/upstream-chronik/run_repetitions.py"
    native_path = "src/test/chronik_upstream_archival_tests.cpp"
    validate_retained_controls(git(root, "show", PARENT + ":" + runner_path).decode(), (root / runner_path).read_text(),
                               git(root, "show", PARENT + ":" + native_path).decode(), (root / native_path).read_text())
    client = ast.parse((root / "tests/compatibility/upstream-chronik/catchup_service.py").read_text())
    constants = {t.id: ast.literal_eval(n.value) for n in client.body if isinstance(n, ast.Assign)
                 for t in n.targets if isinstance(t, ast.Name) and t.id in ("NATIVE_CASES", "RAPID_BLOCKS", "FINAL_HEIGHT")}
    require(constants == {"NATIVE_CASES": tuple(CONTRACT["native_leaves"].items()), "RAPID_BLOCKS": 300, "FINAL_HEIGHT": 402},
            "catchup exact public cases/assertions/scenario drift")
    return TECHNICAL | POLICY


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: finite compact service frontier; upstream engines, defaults and old controls retained")

# SPDX-License-Identifier: MIT
"""Exact test-only cold reconstruction layer over unchanged upstream engines."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, load_json, require
from check_chronik_service_catchup import revision_identity

PARENT = "e70c7c2063e786663a0164a0a6c8320cb015eae8"
TREE = "d67c2dcfbd74a1fae0080f74079ee99141d8a584"
MANIFEST = "docs/engineering/chronik/upstream-cold-rebuild-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0056.json"
TECHNICAL = frozenset({"tests/compatibility/upstream-chronik/cold_rebuild_service.py",
                       "tests/compatibility/upstream-chronik/cold_rebuild_service_self_test.py"})
POLICY = frozenset({MANIFEST, RECORD, "docs/engineering/chronik/upstream-cold-rebuild.md",
    "tools/engineering/check_chronik_cold_rebuild.py", "tools/engineering/check_chronik_cold_rebuild_self_test.py",
    "tools/engineering/check_chronik_service_catchup.py", "tools/engineering/check_chronik_service_catchup_self_test.py",
    "tools/engineering/check_chronik_plugin_alp_self_test.py",
    "tools/engineering/check_chronik_service_plugins_self_test.py",
    "tools/engineering/check_chronik_service_alp_self_test.py",
    "tools/engineering/check_chronik_service_startup_self_test.py",
    "tools/engineering/check_chronik_service_recovery_self_test.py",
    "tools/engineering/check_chronik_regtest_service_self_test.py",
    "tools/engineering/check_chronik_plugin_restart_self_test.py",
    ".github/workflows/chronik-upstream-link.yml"})
REUSED = ("tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/alp_service.py", "tests/compatibility/upstream-chronik/plugin_service.py",
    "tests/compatibility/upstream-chronik/plugin_alp_service.py", "tests/compatibility/upstream-chronik/regtest_service.py",
    "tests/compatibility/upstream-chronik/catchup_service.py",
    "tests/compatibility/upstream-chronik/plugins/ergon_public_probe.py")
CONTRACT = {"scope": "test-only-no-production-or-dependency-change", "profile": "regtest-plugins",
    "compile_default": False, "runtime_default": False, "network": "bounded-unpruned-regtest",
    "maximum_height": 2000, "corpus_height": 102, "first_index_height": 402, "final_height": 403,
    "retained_capture_window": 288, "default_repetitions": 3, "daemon_processes_per_repetition": 3,
    "template_case": "chronik_upstream_regtest_tests/export_upstream_alp_templates", "template_assertions": 4,
    "input_corpus": "unchanged-upstream-ALP-templates-and-existing-public-ERGN-plugin-fixture",
    "role_binding": "native-outpoint-ancestry-not-canonical-block-order",
    "lifecycle": ["native-only-history-no-index", "first-upstream-index-reconstruction", "same-index-process-reopen", "native-progress"],
    "historical_first_seen": "upstream-explicit-unknown-zero-not-original-mempool-times",
    "oracles": "unchanged-existing-full-ALP-AND-public-plugin-HTTP-oracles",
    "cleanup": "all-owned-daemons-attempted-retain-root-on-unverified-cleanup-no-passing-report",
    "unchanged_deadlines": True, "automatic_retry": False, "arbitrary_plugins": False,
    "native_value": "zero-input-output-fee-not-funded-economics", "consensus_change": False,
    "upstream_engine_change": False, "dependency_change": False, "incoming_evidence": [],
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}


def before_identity(root, path):
    return revision_identity(root, PARENT, path)


def input_binding(root):
    rows = [{"path": p, "identity": current_identity(root, p)} for p in sorted(TECHNICAL | set(REUSED))]
    return hashlib.sha256((json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()).hexdigest()


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "cold parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and manifest["license"] == "MIT" and
            manifest["schema"] == "ergon-upstream-cold-rebuild/v1" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT, "cold contract drift")
    require([r["path"] for r in manifest["files"]] == sorted(TECHNICAL), "cold inventory drift")
    for row in manifest["files"]:
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["before"] is None and before_identity(root, row["path"]) is None and
                row["after"] == current_identity(root, row["path"]) and
                row["after"]["mode"] == "100644" and row["provenance"] == "A1/MIT" and
                row["reachability"] == "test-only", "cold source drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == TECHNICAL | POLICY, "unexpected cold path")
    from check_change import ValidationError, validate as validate_change
    record = load_json(root / RECORD)
    try:
        validate_change(record, RECORD)
    except ValidationError as error:
        raise ValueError("cold governance schema drift") from error
    require(record["change_id"] == "ERGON-CHANGE-0056" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question", "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(changed - {RECORD}), "cold governance drift")
    def governed(v):
        return None if v is None else {("git_blob_sha1" if k == "git_blob" else k): value for k, value in v.items()}
    for row in record["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                governed(row["after"]) == current_identity(root, row["path"]), "cold policy drift")
    for path in REUSED + ("CMakeLists.txt", "chronik/UpstreamLink.cmake", "src/init.cpp", "src/validation.cpp",
            "src/txmempool.cpp", "chronik/Cargo.lock", "chronik/chronik-cpp/chronik_bridge.cpp",
            "chronik/chronik-cpp/chronik_bridge.h", "chronik/chronik-cpp/ergon_archival_worker.cpp",
            "chronik/chronik-cpp/ergon_regtest_service.cpp", "src/test/chronik_upstream_archival_tests.cpp",
            "src/test/chronik_upstream_regtest_tests.cpp", "chronik/chronik-lib/src/ergon_archival.rs",
            "chronik/chronik-lib/src/ergon_service.rs", "chronik/chronik-plugin-impl/src/context.rs",
            "chronik/chronik-indexer/src/indexer.rs", "chronik/bitcoinsuite-slp/src/alp/build.rs",
            "docs/engineering/changes/ergon-change-0055.json", "docs/engineering/chronik/upstream-service-catchup-v1.json"):
        require(current_identity(root, path) == before_identity(root, path), "cold changed frozen engine/oracle/history")
    client = ast.parse((root / "tests/compatibility/upstream-chronik/cold_rebuild_service.py").read_text())
    constants = {t.id: ast.literal_eval(n.value) for n in client.body if isinstance(n, ast.Assign)
                 for t in n.targets if isinstance(t, ast.Name) and t.id in ("CORPUS_HEIGHT", "FINAL_HEIGHT", "RETAINED_WINDOW")}
    require(constants == {"CORPUS_HEIGHT": 102, "FINAL_HEIGHT": 402, "RETAINED_WINDOW": 288}, "cold scenario drift")
    return TECHNICAL | POLICY


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: test-only upstream cold reconstruction; inherited engines, oracles and limits unchanged")

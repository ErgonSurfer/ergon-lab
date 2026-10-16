# SPDX-License-Identifier: MIT
"""Finite test-only combined upstream ALP/plugin daemon lifecycle."""
import argparse
import hashlib
import json
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, identity, load_json, require

PARENT = "95143c437e45f64ea74a695251d4f6cd474ef8de"
TREE = "3555dfb168ee12f6c07bfa563fe305d21af72a55"
MANIFEST = "docs/engineering/chronik/upstream-plugin-alp-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0054.json"
TECHNICAL = frozenset({"tests/compatibility/upstream-chronik/plugin_alp_service.py",
                       "tests/compatibility/upstream-chronik/plugin_alp_service_self_test.py"})
POLICY = frozenset({MANIFEST, RECORD, "docs/engineering/chronik/upstream-plugin-alp.md",
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
    "tests/compatibility/upstream-chronik/regtest_service.py",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/plugins/ergon_public_probe.py",
    "src/test/chronik_upstream_regtest_tests.cpp")
CONTRACT = {"scope": "test-only-no-production-or-dependency-change", "profile": "regtest-plugins",
    "compile_default": False, "runtime_default": False, "maximum_height": 2000,
    "network": "bounded-unpruned-regtest", "plugin": "exact-existing-public-ergon_public_probe-mode0",
    "template_case": "chronik_upstream_regtest_tests/export_upstream_alp_templates", "template_assertions": 4,
    "cycle": ["GENESIS-1000-plus-baton", "MINT-500-plus-baton", "SEND-1400-and-100", "BURN-100"],
    "inputs": "upstream-builder-templates-only-plus-existing-ERGN-LOKAD-push",
    "oracles": "existing-ALP-native-wire-colors-spenders-token-history-UTXOs-AND-exact-plugin-ancestry-output-history-UTXOs",
    "lifecycle": ["pending", "pending-process-reopen", "confirm", "disconnect", "reconsider", "confirmed-process-reopen"],
    "daemon_processes_per_repetition": 3, "default_repetitions": 3,
    "native_value": "zero-inputs-outputs-fees-not-funded-token-economics",
    "arbitrary_plugins": False, "Python_security_sandbox": False,
    "retained_controls": "all-five-profiles-exact-matrices-deadlines-cleanup-and-separate-service-clients",
    "source_binding": "unchanged-compiled-adapter-projection-plus-exact-test-and-reused-input-digest",
    "consensus_change": False, "incoming_evidence": [],
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}


def before_identity(root, path):
    row = git(root, "ls-tree", PARENT, "--", path).decode().split()
    return identity(git(root, "show", PARENT + ":" + path), row[0]) if row else None


def input_binding(root):
    rows = [{"path": p, "identity": current_identity(root, p)} for p in sorted(TECHNICAL | set(REUSED))]
    return hashlib.sha256((json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()).hexdigest()


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "combined parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and manifest["license"] == "MIT" and
            manifest["schema"] == "ergon-upstream-plugin-alp/v1" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT, "combined contract drift")
    require([r["path"] for r in manifest["files"]] == sorted(TECHNICAL), "combined inventory drift")
    for row in manifest["files"]:
        before = before_identity(root, row["path"])
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["before"] == before and row["after"] == current_identity(root, row["path"]) and
                before is None and row["after"]["mode"] == "100644" and
                row["provenance"] == "A1/MIT" and row["reachability"] == "test-only", "combined test source drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == TECHNICAL | POLICY, "unexpected combined path")
    from check_change import ValidationError, validate as validate_change
    record = load_json(root / RECORD)
    try:
        validate_change(record, RECORD)
    except ValidationError as error:
        raise ValueError("combined governance schema drift") from error
    require(record["change_id"] == "ERGON-CHANGE-0054" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question", "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(changed - {RECORD}), "combined governance drift")
    def governed(v):
        return None if v is None else {("git_blob_sha1" if k == "git_blob" else k): value for k, value in v.items()}
    for row in record["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                governed(row["after"]) == current_identity(root, row["path"]), "combined policy drift")
    for path in REUSED + ("CMakeLists.txt", "chronik/UpstreamLink.cmake", "src/init.cpp", "src/validation.cpp",
                         "src/txmempool.cpp", "chronik/Cargo.lock", "chronik/chronik-lib/src/ergon_archival.rs",
                         "chronik/chronik-cpp/ergon_regtest_service.cpp", "chronik/chronik-plugin-impl/src/context.rs",
                         "chronik/chronik-indexer/src/indexer.rs", "chronik/bitcoinsuite-slp/src/alp/build.rs",
                         "docs/engineering/changes/ergon-change-0053.json",
                         "docs/engineering/chronik/upstream-service-plugins-v1.json"):
        require(current_identity(root, path) == before_identity(root, path), "combined changed frozen engine/oracle/history")
    return TECHNICAL | POLICY


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: test-only upstream ALP/public-plugin lifecycle; engines and defaults unchanged")

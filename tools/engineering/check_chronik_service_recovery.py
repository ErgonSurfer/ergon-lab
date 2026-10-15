# SPDX-License-Identifier: MIT
"""Finite OS-lease recovery layer on the signed local regtest service checkpoint."""
import argparse
import ast
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, identity, load_json, require

PARENT = "a8f38542290b4fa84ec1fd27b70b54fd78c04a97"
TREE = "dde63dc309410d53fd22d3967d6a6c78ac47b1d7"
MANIFEST = "docs/engineering/chronik/upstream-service-recovery-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0050.json"
TECHNICAL = frozenset({
    "chronik/chronik-lib/Cargo.toml",
    "chronik/chronik-lib/src/ergon_archival.rs",
    "chronik/chronik-cpp/ergon_archival_worker.cpp",
    "chronik/chronik-cpp/ergon_archival_worker.h",
    "chronik/chronik-cpp/ergon_regtest_service.cpp",
    "src/test/chronik_upstream_regtest_tests.cpp",
    "tests/compatibility/upstream-chronik/regtest_service.py",
    "tests/compatibility/upstream-chronik/regtest_service_self_test.py",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
})
POLICY = frozenset({MANIFEST, RECORD,
    "docs/engineering/chronik/upstream-service-recovery.md",
    "tools/engineering/check_chronik_service_recovery.py",
    "tools/engineering/check_chronik_service_recovery_self_test.py",
    "tools/engineering/check_chronik_regtest_service.py",
    "tools/engineering/check_chronik_regtest_service_self_test.py",
    "tools/engineering/check_chronik_plugin_restart_self_test.py",
    "tools/engineering/check_chronik_plugin_restart.py",
    ".github/workflows/chronik-upstream-link.yml"})
CONTRACT = {
    "parent_status": "signed-local-review-checkpoint-not-assumed-public-main",
    "network": "bounded-unpruned-regtest", "default_off": True, "plugins": False,
    "lease": "persistent-regular-inode-exclusive-OS-lock",
    "lease_marker": "ergon-regtest-service-os-lock/v1",
    "unknown_or_partial_lease": "refuse-preserve-no-PID-guess-no-unlink",
    "failure_marker": "refuse-preserve-native-mining-continues",
    "crash": "real-SIGKILL-reopen-same-root-and-inode-upstream-RocksDB-recovery",
    "database_wipe": False, "identity_rewrite": False,
    "host_crate_minimum_rust": "1.89.0", "CI_rust_unchanged": "1.93.1",
    "native_lease_cases": [
        ["chronik_upstream_regtest_tests/service_os_lease_excludes_other_owners_and_preserves_inode", 9],
        ["chronik_upstream_regtest_tests/service_unknown_or_partial_lease_is_not_adopted", 6]],
    "consensus_change": False, "dependency_change": False, "incoming_evidence": [],
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}


def before_identity(root, path):
    entry = git(root, "ls-tree", PARENT, "--", path).decode().split()
    return identity(git(root, "show", PARENT + ":" + path), entry[0]) if entry else None


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "recovery parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and manifest["license"] == "MIT" and
            manifest["schema"] == "ergon-upstream-service-recovery/v1" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT, "recovery contract drift")
    require([r["path"] for r in manifest["files"]] == sorted(TECHNICAL), "recovery finite inventory drift")
    for row in manifest["files"]:
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["provenance"] == "PUBLIC+A1/MIT" and
                row["reachability"] == "bounded-regtest-opt-in-or-test-only" and
                row["before"] == before_identity(root, row["path"]) and
                row["after"] == current_identity(root, row["path"]) and
                row["before"] != row["after"] and row["after"]["mode"] == "100644", "recovery source drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == TECHNICAL | POLICY, "unexpected recovery path")
    record = load_json(root / RECORD)
    require(record["change_id"] == "ERGON-CHANGE-0050" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question",
                                  "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(changed - {RECORD}), "recovery governance drift")
    def governed(v):
        return None if v is None else {("git_blob_sha1" if k == "git_blob" else k): x for k, x in v.items()}
    for row in record["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                governed(row["after"]) == current_identity(root, row["path"]), "recovery policy drift")
    driver = ast.parse((root / "tests/compatibility/upstream-chronik/regtest_service.py").read_text())
    selections = [ast.literal_eval(n.value) for n in driver.body if isinstance(n, ast.Assign) and
                  any(isinstance(t, ast.Name) and t.id == "LEASE_CASES" for t in n.targets)]
    require(selections == [tuple(tuple(c) for c in CONTRACT["native_lease_cases"])], "recovery exact native selections drift")
    for path in ("CMakeLists.txt", "src/init.cpp", "chronik/UpstreamLink.cmake",
                 "chronik/Cargo.lock", "chronik/Cargo.toml",
                 "src/validation.cpp", "src/validation.h", "src/chainparams.cpp",
                 "src/chain.cpp", "src/chain.h", "src/txmempool.cpp", "src/txmempool.h",
                 "chronik/chronik-cpp/ergon_node_access.h", "chronik/chronik-bridge/src/ffi.rs",
                 "chronik/chronik-cpp/chronik_bridge.cpp", "chronik/chronik-cpp/chronik_bridge.h",
                 "docs/engineering/changes/ergon-change-0049.json",
                 "docs/engineering/chronik/upstream-regtest-service-v1.json"):
        require(current_identity(root, path) == before_identity(root, path), "recovery changed frozen input")
    cargo = "chronik/chronik-lib/Cargo.toml"
    original = git(root, "show", PARENT + ":" + cargo).decode()
    require((root / cargo).read_text() == original.replace(
        "rust-version.workspace = true", "# Standard-library OS file locks used by the Ergon host adapter.\nrust-version = \"1.89.0\""),
        "recovery Cargo change exceeds explicit host-crate MSRV declaration")
    return TECHNICAL | POLICY


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: finite bounded-regtest OS lease recovery; no deployment or proof promotion")

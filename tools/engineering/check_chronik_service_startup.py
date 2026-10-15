# SPDX-License-Identifier: MIT
"""Finite owned nonblocking startup; not general daemon or proof activation."""
import argparse
import ast
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, identity, load_json, require

PARENT = "509a0e9c6e2267966bb21a6af47911b6d26cae2b"
TREE = "2220f0cd43bcafa689eaec51ee5746da15544cb6"
MANIFEST = "docs/engineering/chronik/upstream-service-startup-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0051.json"
TECHNICAL = frozenset({
    "src/init.cpp", "chronik/chronik-cpp/chronik_bridge.cpp", "chronik/chronik-cpp/chronik_bridge.h",
    "chronik/chronik-cpp/ergon_archival_probe.cpp", "chronik/chronik-cpp/ergon_archival_probe.h",
    "chronik/chronik-cpp/ergon_archival_worker.cpp", "chronik/chronik-cpp/ergon_archival_worker.h",
    "chronik/chronik-cpp/ergon_regtest_service.cpp", "chronik/chronik-cpp/ergon_regtest_service.h",
    "chronik/chronik-lib/src/ergon_archival.rs", "src/test/chronik_upstream_regtest_tests.cpp",
    "tests/compatibility/upstream-chronik/regtest_service.py",
    "tests/compatibility/upstream-chronik/regtest_service_self_test.py",
    "tests/compatibility/upstream-chronik/run_repetitions.py"})
POLICY = frozenset({MANIFEST, RECORD, "docs/engineering/chronik/upstream-service-startup.md",
    "tools/engineering/check_chronik_service_startup.py",
    "tools/engineering/check_chronik_service_startup_self_test.py",
    "tools/engineering/check_chronik_service_recovery.py",
    "tools/engineering/check_chronik_service_recovery_self_test.py",
    "tools/engineering/check_chronik_regtest_service.py",
    "tools/engineering/check_chronik_regtest_service_self_test.py",
    "tools/engineering/check_chronik_plugin_restart.py",
    "tools/engineering/check_chronik_plugin_restart_self_test.py",
    "tools/engineering/check_chronik_upstream_link.py",
    ".github/workflows/chronik-upstream-link.yml"})
CONTRACT = {
    "parent_status": "signed-local-review-checkpoint-not-assumed-public-main",
    "compile_default": False, "runtime_default": False, "plugins": False,
    "network": "bounded-unpruned-regtest", "maximum_height": 2000,
    "startup": "owned-thread-waits-native-import-and-real-mempool-not-AppInitMain",
    "initial_branch": "owned-288-block-suffix-checked-before-live-transition",
    "progress_and_reorg": "existing-upstream-writers-over-retained-old-and-current-native-captures",
    "cancellation": "joined-no-false-failure-marker-no-wipe-or-identity-rewrite",
    "read_admission": "unchanged-source-and-epoch-gate-503-until-complete-current-publication",
    "stop": "join-before-native-scheduler-and-owner-teardown-no-detach-or-fake-timeout",
    "historical_fixture_startup": "strict-source-and-storage-fence-unchanged",
    "native_cases": [
        ["chronik_upstream_regtest_tests/service_owned_startup_reconciles_progress_and_reorg", 7],
        ["chronik_upstream_regtest_tests/service_startup_cancellation_preserves_reopenable_root", 5],
        ["chronik_upstream_regtest_tests/asynchronous_owner_does_not_wait_for_native_import", 6],
        ["chronik_upstream_regtest_tests/asynchronous_owner_allows_native_progress_before_open", 9],
        ["chronik_upstream_regtest_tests/asynchronous_owner_stop_joins_pending_startup", 9]],
    "retained_stop_timeout": "unresolved-not-PR-ready-no-oracle-relaxation",
    "consensus_change": False, "dependency_change": False, "incoming_evidence": [],
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}


def before_identity(root, path):
    row = git(root, "ls-tree", PARENT, "--", path).decode().split()
    return identity(git(root, "show", PARENT + ":" + path), row[0]) if row else None


def validate(root, historical=False):
    root = root.resolve(strict=True)
    from check_chronik_service_plugins import MANIFEST as PLUGINS_MANIFEST, TECHNICAL as PLUGINS_TECHNICAL
    plugin_successor = PLUGINS_TECHNICAL if (root / PLUGINS_MANIFEST).exists() else frozenset()
    from check_chronik_service_alp import MANIFEST as NEXT_MANIFEST, PARENT as NEXT_PARENT
    from check_chronik_service_alp import validate as validate_next
    if not historical and (root / NEXT_MANIFEST).exists():
        return validate_next(root) | validate(root, historical=True)
    def observed(path):
        if not historical: return current_identity(root, path)
        row = git(root, "ls-tree", NEXT_PARENT, "--", path).decode().split()
        return identity(git(root, "show", NEXT_PARENT + ":" + path), row[0])
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "startup parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"}
            and manifest["$comment"] == "SPDX-License-Identifier: MIT" and manifest["license"] == "MIT"
            and manifest["schema"] == "ergon-upstream-service-startup/v1"
            and manifest["public_parent"] == {"commit": PARENT, "tree": TREE}
            and manifest["contract"] == CONTRACT, "startup contract drift")
    require([row["path"] for row in manifest["files"]] == sorted(TECHNICAL), "startup inventory drift")
    for row in manifest["files"]:
        require(set(row) == {"path", "before", "after", "provenance", "reachability"}
                and row["before"] == before_identity(root, row["path"])
                and row["after"] == observed(row["path"])
                and row["before"] != row["after"] and row["after"]["mode"] == "100644"
                and row["provenance"] == "PUBLIC+A1/MIT"
                and row["reachability"] == "bounded-regtest-opt-in-or-test-only", "startup source drift")
    revisions = (PARENT, NEXT_PARENT) if historical else (PARENT,)
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", *revisions).split(b"\0") if p}
    if not historical:
        changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == TECHNICAL | POLICY, "unexpected startup path")
    record = load_json(root / RECORD)
    require(record["change_id"] == "ERGON-CHANGE-0051" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question",
                                  "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(changed - {RECORD}), "startup governance drift")
    def governed(value):
        return None if value is None else {("git_blob_sha1" if k == "git_blob" else k): v for k, v in value.items()}
    for row in record["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                governed(row["after"]) == observed(row["path"]), "startup policy drift")
    driver = ast.parse((root / "tests/compatibility/upstream-chronik/regtest_service.py").read_text())
    cases = [ast.literal_eval(n.value) for n in driver.body if isinstance(n, ast.Assign) and
             any(isinstance(t, ast.Name) and t.id == "STARTUP_CASES" for t in n.targets)]
    require(cases == [tuple(tuple(c) for c in CONTRACT["native_cases"])], "startup exact case/assertion drift")
    init = (root / "src/init.cpp").read_text()
    require("while (!g_mempool.IsLoaded() && !ShutdownRequested())" not in init and
            "ergon_regtest::Start(config);" in init, "blocking native initialization restored")
    shutdown = init[init.index("void Shutdown("):init.index("static void HandleSIGTERM")]
    require(shutdown.index("ergon_regtest::Stop();") < shutdown.index("threadGroup.join_all();") <
            shutdown.index("pcoinsTip.reset();"), "startup stop order drift")
    for path in ("CMakeLists.txt", "chronik/UpstreamLink.cmake", "chronik/Cargo.lock", "chronik/Cargo.toml",
                 "chronik/chronik-lib/Cargo.toml", "src/validation.cpp", "src/validation.h", "src/chainparams.cpp",
                 "src/chain.cpp", "src/chain.h", "src/txmempool.cpp", "src/txmempool.h",
                 "chronik/chronik-cpp/ergon_node_access.h", "chronik/chronik-bridge/src/ffi.rs",
                 "chronik/chronik-lib/src/ergon_service.rs", "chronik/chronik-http/src/server.rs",
                 "docs/engineering/changes/ergon-change-0049.json", "docs/engineering/changes/ergon-change-0050.json",
                 "docs/engineering/chronik/upstream-regtest-service-v1.json",
                 "docs/engineering/chronik/upstream-service-recovery-v1.json"):
        require((observed(path) if historical and path in plugin_successor else current_identity(root, path))
                == before_identity(root, path), "startup changed frozen authority/history/input")
    return TECHNICAL | POLICY


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: finite owned nonblocking regtest startup; retained timeout still blocks PR")

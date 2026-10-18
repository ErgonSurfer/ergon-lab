# SPDX-License-Identifier: MIT
"""Finite opt-in real-daemon regtest service layer, not production activation."""
import argparse
import ast
import re
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, identity, load_json, require

PARENT = "723f57ff092bed5be122d69f319e2e90a4a1c00a"
TREE = "e00b2b4b6e3da6bf049a8d11039f7b47bbb37815"
MANIFEST = "docs/engineering/chronik/upstream-regtest-service-v1.json"
TECHNICAL = frozenset({
    "CMakeLists.txt", "src/init.cpp", "chronik/UpstreamLink.cmake",
    "src/test/chronik_upstream_regtest_tests.cpp",
    "chronik/chronik-cpp/ergon_archival_probe.cpp",
    "chronik/chronik-cpp/ergon_archival_worker.cpp",
    "chronik/chronik-cpp/ergon_regtest_service.h", "chronik/chronik-cpp/ergon_regtest_service.cpp",
    "chronik/chronik-http/src/server.rs", "chronik/chronik-lib/src/ergon_archival.rs",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/self_test.py",
    "tests/compatibility/upstream-chronik/regtest_service.py",
    "tests/compatibility/upstream-chronik/regtest_service_self_test.py",
})
RECORD = "docs/engineering/changes/ergon-change-0049.json"
POLICY = frozenset({MANIFEST, RECORD, "docs/engineering/chronik/upstream-regtest-service.md",
    "tools/engineering/check_chronik_regtest_service.py",
    "tools/engineering/check_chronik_regtest_service_self_test.py",
    "tools/engineering/check_chronik_plugin_restart.py",
    "tools/engineering/check_chronik_plugin_restart_self_test.py",
    "tools/engineering/check_chronik_upstream_link.py",
    ".github/workflows/chronik-upstream-link.yml"})
CONTRACT = {"daemon_admission": "explicit-build-and-runtime-opt-in-local-regtest-only",
    "compile_default": False, "runtime_default": False, "plugins": False,
    "network": "unpruned-regtest", "maximum_height": 2000,
    "retained_capture_blocks": 288, "listen": "ephemeral-ipv4-loopback-only",
    "startup": "after-native-import-and-mempool-loaded-before-peers",
    "stop": "join-before-native-scheduler-and-resource-teardown",
    "native_shutdown": ["chronik_upstream_regtest_tests/shutdown_cancels_inflight_capture_without_failed_root", 9],
    "index_failure": "native-mining-continues-no-automatic-wipe-or-rebuild",
    "upstream_index_and_router_reused": True, "consensus_change": False,
    "dependency_change": False, "incoming_evidence": [],
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}


def before_identity(root, path):
    entry = git(root, "ls-tree", PARENT, "--", path).decode().split()
    return identity(git(root, "show", PARENT + ":" + path), entry[0]) if entry else None


def validate(root, historical=False):
    root = root.resolve(strict=True)
    from check_chronik_service_recovery import MANIFEST as NEXT_MANIFEST, PARENT as NEXT_PARENT, TECHNICAL as NEXT_TECHNICAL
    from check_chronik_service_recovery import validate as validate_next
    if not historical and (root / NEXT_MANIFEST).exists():
        return validate_next(root) | validate(root, historical=True)
    def observed(path):
        if not historical:
            return current_identity(root, path)
        entry = git(root, "ls-tree", NEXT_PARENT, "--", path).decode().split()
        return identity(git(root, "show", NEXT_PARENT + ":" + path), entry[0])
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "service public parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and manifest["license"] == "MIT" and
            manifest["schema"] == "ergon-upstream-regtest-service/v1" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT, "service manifest contract drift")
    require([r["path"] for r in manifest["files"]] == sorted(TECHNICAL), "service finite inventory drift")
    for row in manifest["files"]:
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["provenance"] == "PUBLIC+A1/MIT" and
                row["reachability"] == "bounded-regtest-opt-in-or-test-only" and
                row["before"] == before_identity(root, row["path"]) and
                row["after"] == observed(row["path"]) and
                row["before"] != row["after"] and row["after"]["mode"] == "100644", "service postimage drift")
    revisions = (PARENT, NEXT_PARENT) if historical else (PARENT,)
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", *revisions).split(b"\0") if p}
    if not historical:
        changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == TECHNICAL | POLICY, "unexpected service path")
    record = load_json(root / RECORD)
    require(record["change_id"] == "ERGON-CHANGE-0049" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question",
                                  "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(changed - {RECORD}), "service governance drift")
    def governed(value):
        return None if value is None else {("git_blob_sha1" if k == "git_blob" else k): v for k, v in value.items()}
    for row in record["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                governed(row["after"]) == observed(row["path"]) and
                row["after"]["mode"] == "100644", "service policy byte/mode drift")
    # The new profile must not silently widen upstream's old closed admission,
    # native consensus, dependencies, helper/donor bytes or historical records.
    for path in ("chronik/Cargo.lock", "chronik/Cargo.toml", "chronik/chronik-lib/Cargo.toml",
                 "chronik/chronik-cpp/ergon_node_access.h", "src/validation.cpp", "src/validation.h",
                 "src/chainparams.cpp", "src/chain.cpp", "src/chain.h", "src/txmempool.cpp",
                 "src/txmempool.h", "chronik/chronik-lib/src/ffi.rs", "chronik/chronik-bridge/src/ffi.rs",
                 "docs/engineering/changes/ergon-change-0048.json",
                 "docs/engineering/chronik/upstream-plugin-restart-v1.json"):
        require(observed(path) == before_identity(root, path), "service changed frozen boundary")
        if historical and path not in NEXT_TECHNICAL:
            from check_chronik_long_history import successor_identity as history_successor
            require(current_identity(root, path) == history_successor(root, path, observed(path)), "successor changed frozen service input")
    def text_at_boundary(path):
        return git(root, "show", NEXT_PARENT + ":" + path).decode() if historical else (root / path).read_text()
    init = text_at_boundary("src/init.cpp")
    driver = ast.parse(text_at_boundary("tests/compatibility/upstream-chronik/regtest_service.py"))
    selections = [ast.literal_eval(n.value) for n in driver.body if isinstance(n, ast.Assign) and
                  any(isinstance(t, ast.Name) and t.id == "SHUTDOWN" for t in n.targets)]
    require(selections == [tuple(CONTRACT["native_shutdown"])], "exact native shutdown selection drift")
    cmake = (root / "CMakeLists.txt").read_text()
    require(re.search(r'option\(BUILD_CHRONIK_UPSTREAM_REGTEST\s+"[^"]+"\s+OFF\s*\)', cmake) and
            'gArgs.GetBoolArg("-chronikupstreamregtest", false)' in init,
            "service default-OFF law drift")
    shutdown = init[init.index("void Shutdown("):init.index("static void HandleSIGTERM")]
    require(shutdown.index("ergon_regtest::Stop();") < shutdown.index("threadGroup.join_all();") <
            shutdown.index("pcoinsTip.reset();"), "service stop order drift")
    require("while (!g_mempool.IsLoaded() && !ShutdownRequested())" in init and
            "-chronikupstreamregtest requires unpruned local regtest" in init, "service native startup/scope gate missing")
    return TECHNICAL | POLICY


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: bounded opt-in regtest service only; no production or proof promotion")

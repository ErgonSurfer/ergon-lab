# SPDX-License-Identifier: MIT
"""Finite fresh-process plugin witnesses over the exact public PR 53 head."""
import argparse
import ast
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, identity, load_json, require

PARENT = "ac40aa814528333ddb88049ea3ec3bb7e7a34cd3"
TREE = "df700d15336b8d02e5de50df03ffc1b21b32ca68"
MANIFEST = "docs/engineering/chronik/upstream-plugin-restart-v1.json"
TECHNICAL = frozenset({
    "chronik/UpstreamLink.cmake", "chronik/chronik-lib/src/ergon_plugin_fixture.rs",
    "chronik/chronik-lib/src/ergon_archival.rs", "src/test/main.cpp",
    "src/test/chronik_upstream_archival_tests.cpp",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/self_test.py",
    "tests/compatibility/upstream-chronik/plugin_restart.py",
    "tests/compatibility/upstream-chronik/plugin_restart_self_test.py",
})
POLICY = frozenset({MANIFEST, "docs/engineering/chronik/upstream-plugin-restart.md",
    "docs/engineering/changes/ergon-change-0048.json",
    "tools/engineering/check_chronik_plugin_restart.py",
    "tools/engineering/check_chronik_plugin_restart_self_test.py",
    "tools/engineering/check_chronik_plugin_fixtures.py",
    ".github/workflows/chronik-upstream-link.yml"})
CONTRACT = {"daemon_admission": False, "consensus_change": False,
    "dependency_change": False, "incoming_evidence": [],
    "plugin_engine": "existing-upstream", "plugins": "public-synthetic-only",
    "restart": "distinct-child-processes-and-interpreters",
    "source": "public-native-block-corpus-validated-in-every-process",
    "version_refusal": "fixture-identity-and-exact-upstream-PluginVersionMismatch-separated",
    "rebuild": "upstream-setup-and-resync-in-new-empty-root-only",
    "original_index": "identity-preserved-and-fresh-v1-reopen-required",
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}
EXPECTED_PHASES = (
    ("seed-v1", "seed_v1", 239),
    ("reopen-v1", "reopen_v1", 237),
    ("refuse-v2", "refuse_v2", 222),
    ("reopen-v1-after-refusal", "reopen_v1", 237),
    ("interrupted-rebuild", "interrupted_rebuild", 222),
    ("failed-reopen-refused", "failed_reopen_refused", 218),
    ("rebuild-v2", "rebuild_v2", 236),
    ("reopen-v2", "reopen_v2", 237),
)


def validate_phases(driver):
    phases = [ast.literal_eval(node.value) for node in driver.body
              if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "PHASES" for t in node.targets)]
    require(len(phases) == 1 and phases[0] == EXPECTED_PHASES and
            all(type(p[2]) is int for p in phases[0]), "restart exact phase/case/assertion selections drift")


def before_identity(root, path):
    entry = git(root, "ls-tree", PARENT, "--", path).decode().split()
    return identity(git(root, "show", PARENT + ":" + path), entry[0]) if entry else None


def validate(root, historical=False):
    root = root.resolve(strict=True)
    from check_chronik_regtest_service import MANIFEST as SERVICE_MANIFEST, PARENT as SERVICE_PARENT, TECHNICAL as SERVICE_TECHNICAL
    from check_chronik_regtest_service import validate as validate_service
    from check_chronik_service_recovery import TECHNICAL as RECOVERY_TECHNICAL
    if not historical and (root / SERVICE_MANIFEST).exists():
        return validate_service(root) | validate(root, historical=True)
    def observed(path):
        if not historical:
            return current_identity(root, path)
        entry = git(root, "ls-tree", SERVICE_PARENT, "--", path).decode().split()
        return identity(git(root, "show", SERVICE_PARENT + ":" + path), entry[0])
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "restart parent identity/ancestry drift")
    record = load_json(root / MANIFEST)
    require(set(record) == {"$comment", "schema", "license", "public_parent", "parent_status", "contract", "files"}
            and record["$comment"] == "SPDX-License-Identifier: MIT" and record["license"] == "MIT"
            and record["schema"] == "ergon-upstream-plugin-restart/v1"
            and record["public_parent"] == {"commit": PARENT, "tree": TREE}
            and record["parent_status"] == "public-review-branch-not-assumed-main"
            and record["contract"] == CONTRACT, "restart contract/schema drift")
    require([r["path"] for r in record["files"]] == sorted(TECHNICAL), "restart finite technical inventory drift")
    for row in record["files"]:
        require(set(row) == {"path", "before", "after", "provenance", "reachability"}
                and row["provenance"] == "PUBLIC+A1/MIT" and row["reachability"] == "opt-in-build-or-test-only"
                and row["before"] == before_identity(root, row["path"])
                and row["after"] == observed(row["path"])
                and row["before"] != row["after"] and row["after"]["mode"] == "100644", "restart pre/postimage drift")
    revisions = (PARENT, SERVICE_PARENT) if historical else (PARENT,)
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", *revisions).split(b"\0") if p}
    if not historical:
        changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == TECHNICAL | POLICY, "unexpected restart technical/policy path")
    driver_path = "tests/compatibility/upstream-chronik/plugin_restart.py"
    driver = ast.parse(git(root, "show", SERVICE_PARENT + ":" + driver_path).decode()
                       if historical else (root / driver_path).read_text())
    validate_phases(driver)
    record_path = "docs/engineering/changes/ergon-change-0048.json"
    change = load_json(root / record_path)
    require(change["change_id"] == "ERGON-CHANGE-0048" and
            change["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            change["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question",
                                  "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in change["files"]] == sorted(changed - {record_path}), "restart governance drift")
    def governed(value):
        return None if value is None else {("git_blob_sha1" if k == "git_blob" else k): v for k, v in value.items()}
    for row in change["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                governed(row["after"]) == observed(row["path"]) and
                row["after"]["mode"] == "100644", "restart policy byte/mode drift")
    # No donor, dependency, native production, default or earlier-record edits.
    for path in ("CMakeLists.txt", "chronik/Cargo.lock", "chronik/Cargo.toml",
                 "chronik/chronik-lib/Cargo.toml", "chronik/chronik-lib/build.rs",
                 "chronik/chronik-db/src/mem/mempool.rs", "chronik/chronik-cpp/ergon_node_access.h",
                 "src/coins.cpp", "src/coins.h", "src/dbwrapper.cpp", "src/dbwrapper.h",
                 "src/txdb.cpp", "src/txdb.h", "src/chain.cpp", "src/chain.h", "src/txmempool.cpp", "src/txmempool.h",
                 "chronik/chronik-plugin-impl/src/module.rs",
                 "chronik/chronik-plugin-impl/python/upstream/script.py",
                 "chronik/chronik-plugin-impl/python/upstream/slp.py",
                 "docs/engineering/changes/ergon-change-0047.json",
                 "docs/engineering/chronik/upstream-plugin-fixtures-v1.json",
                 "docs/engineering/chronik/upstream-plugin-helpers-v1.json"):
        require(observed(path) == before_identity(root, path), "restart changed frozen history/default/native/donor input")
        if historical and path not in SERVICE_TECHNICAL | RECOVERY_TECHNICAL:
            require(current_identity(root, path) == observed(path), "successor changed unreviewed frozen restart input")
    return TECHNICAL | POLICY


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: historical plugin restart/rebuild witnesses and finite successors; no production or proof promotion")

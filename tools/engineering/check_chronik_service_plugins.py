# SPDX-License-Identifier: MIT
"""Finite public synthetic plugin admission in the bounded regtest service."""
import argparse
import ast
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, identity, load_json, require

PARENT = "13cdd9dfc616477fe5529dc75efad6787111b72f"
TREE = "8e6f80a92e86040b456b5d3c1fd4884f160ad40b"
MANIFEST = "docs/engineering/chronik/upstream-service-plugins-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0053.json"
TECHNICAL = frozenset({"CMakeLists.txt", "chronik/UpstreamLink.cmake",
    "chronik/chronik-cpp/ergon_archival_worker.cpp",
    "chronik/chronik-cpp/ergon_regtest_service.cpp",
    "chronik/chronik-cpp/ergon_regtest_service.h",
    "chronik/chronik-lib/src/ergon_archival.rs", "src/init.cpp",
    "src/test/chronik_upstream_regtest_tests.cpp",
    "tests/compatibility/upstream-chronik/regtest_service.py",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/self_test.py",
    "tests/compatibility/upstream-chronik/plugin_service.py",
    "tests/compatibility/upstream-chronik/plugin_service_self_test.py"})
POLICY = frozenset({MANIFEST, RECORD, "docs/engineering/chronik/upstream-service-plugins.md",
    "tools/engineering/check_chronik_service_plugins.py",
    "tools/engineering/check_chronik_service_plugins_self_test.py",
    "tools/engineering/check_chronik_service_alp.py",
    "tools/engineering/check_chronik_service_alp_self_test.py",
    "tools/engineering/check_chronik_service_startup.py",
    "tools/engineering/check_chronik_service_recovery.py",
    "tools/engineering/check_chronik_service_startup_self_test.py",
    "tools/engineering/check_chronik_service_recovery_self_test.py",
    "tools/engineering/check_chronik_regtest_service_self_test.py",
    "tools/engineering/check_chronik_plugin_restart_self_test.py",
    ".github/workflows/chronik-upstream-link.yml"})
CONTRACT = {"compile_default": False, "runtime_default": False,
    "network": "bounded-unpruned-regtest", "maximum_height": 2000,
    "plugin_admission": "exact-existing-public-synthetic-fixture-only",
    "engine": "existing-upstream-PluginContext-PyO3-and-HTTP-router",
    "context_lifetime": "one-per-process-on-owned-startup-thread-until-after-worker-HTTP-DB-join",
    "inputs": "fixed-two-file-canonical-directory-exact-source-config-before-import",
    "index_identity": "existing-plugin-source-config-Python-bound-identity-no-adoption-no-wipe",
    "owner_case": "chronik_upstream_regtest_tests/asynchronous_owner_loads_public_plugin_and_joins",
    "owner_assertions": 12, "daemon_processes_per_repetition": 6,
    "native_value": "zero-inputs-outputs-fees-not-funded-token-economics",
    "lifecycle": ["runtime-plugin-off", "non-plugin-index-refusal", "pending-parent-child-ALP-metadata", "pending-process-reopen",
                  "confirm", "disconnect", "reconsider", "confirmed-process-reopen", "config-drift-refusal-native-mining"],
    "retained_matrices": "unchanged-exact-selections-assertions-60-second-timeout-and-cleanup",
    "Python_security_sandbox": False, "arbitrary_plugins": False,
    "consensus_change": False, "dependency_change": False, "incoming_evidence": [],
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}


def before_identity(root, path):
    from check_chronik_service_catchup import revision_identity
    return revision_identity(root, PARENT, path)


def validate(root, historical=False):
    root = root.resolve(strict=True)
    from check_chronik_plugin_alp import MANIFEST as NEXT_MANIFEST, PARENT as NEXT_PARENT
    from check_chronik_plugin_alp import validate as validate_next
    if not historical and (root / NEXT_MANIFEST).exists():
        return validate(root, historical=True) | validate_next(root)
    def observed(path):
        if not historical: return current_identity(root, path)
        from check_chronik_service_catchup import revision_identity
        return revision_identity(root, NEXT_PARENT, path)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "service plugin parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and manifest["license"] == "MIT" and
            manifest["schema"] == "ergon-upstream-service-plugins/v1" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT, "service plugin contract drift")
    require([r["path"] for r in manifest["files"]] == sorted(TECHNICAL), "service plugin inventory drift")
    for row in manifest["files"]:
        before = before_identity(root, row["path"])
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["before"] == before and row["after"] == observed(row["path"]) and
                row["before"] != row["after"] and row["after"]["mode"] == "100644" and
                row["provenance"] == ("A1/MIT" if before is None else "PUBLIC+A1/MIT") and
                row["reachability"] == "bounded-regtest-plugin-opt-in-or-test-only", "service plugin source drift")
        from check_chronik_service_catchup import successor_identity
        require(current_identity(root, row["path"]) == successor_identity(root, row["path"], row["after"]),
                "successor changed compiled/plugin input outside exact catchup layer")
    revisions = (PARENT, NEXT_PARENT) if historical else (PARENT,)
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", *revisions).split(b"\0") if p}
    if not historical:
        changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == TECHNICAL | POLICY, "unexpected service plugin path")
    record = load_json(root / RECORD)
    from check_change import ValidationError, validate as validate_change
    try:
        validate_change(record, RECORD)
    except ValidationError as error:
        raise ValueError("service plugin governance schema/inventory drift") from error
    require(record["change_id"] == "ERGON-CHANGE-0053" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question", "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(changed - {RECORD}), "service plugin governance drift")
    def governed(v):
        return None if v is None else {("git_blob_sha1" if k == "git_blob" else k): value for k, value in v.items()}
    for row in record["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                governed(row["after"]) == observed(row["path"]), "service plugin policy drift")
    driver = ast.parse((root / "tests/compatibility/upstream-chronik/plugin_service.py").read_text())
    constants = {t.id: ast.literal_eval(n.value) for n in driver.body if isinstance(n, ast.Assign)
                 for t in n.targets if isinstance(t, ast.Name) and t.id in ("OWNER_CASE", "OWNER_ASSERTIONS")}
    require(constants == {"OWNER_CASE": CONTRACT["owner_case"], "OWNER_ASSERTIONS": 12}, "service plugin owner oracle drift")
    # Whole exact path law forbids all other source changes. Explicitly retain
    # both upstream algorithms and already accepted evidence records.
    for path in ("src/validation.cpp", "src/validation.h", "src/chainparams.cpp",
                 "src/txmempool.cpp", "chronik/Cargo.lock", "chronik/chronik-lib/Cargo.toml",
                 "chronik/chronik-plugin-impl/src/context.rs", "chronik/chronik-lib/src/ergon_plugin_fixture.rs",
                 "chronik/chronik-indexer/src/indexer.rs", "chronik/chronik-lib/src/ergon_service.rs",
                 "chronik/bitcoinsuite-slp/src/alp/build.rs",
                 "tests/compatibility/upstream-chronik/plugins/ergon_public_probe.py",
                 "docs/engineering/changes/ergon-change-0052.json",
                 "docs/engineering/chronik/upstream-service-alp-v1.json"):
        require(current_identity(root, path) == before_identity(root, path), "service plugin changed frozen input")
    return TECHNICAL | POLICY


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: bounded public synthetic plugin service only; no production admission")

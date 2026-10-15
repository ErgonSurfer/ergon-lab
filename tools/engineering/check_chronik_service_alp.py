# SPDX-License-Identifier: MIT
"""Finite test-only actual-daemon ALP coverage, not a token implementation."""
import argparse
import ast
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, identity, load_json, require

PARENT = "6901b93780bc976fd110353efbe30167bc7c8184"
TREE = "7a6e254b80b089ef18098a10ff267e09b66454b6"
MANIFEST = "docs/engineering/chronik/upstream-service-alp-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0052.json"
TECHNICAL = frozenset({"src/test/chronik_upstream_regtest_tests.cpp",
    "tests/compatibility/upstream-chronik/alp_service.py",
    "tests/compatibility/upstream-chronik/alp_service_self_test.py",
    "tests/compatibility/upstream-chronik/run_repetitions.py"})
POLICY = frozenset({MANIFEST, RECORD, "docs/engineering/chronik/upstream-service-alp.md",
    "tools/engineering/check_chronik_service_alp.py",
    "tools/engineering/check_chronik_service_alp_self_test.py",
    "tools/engineering/check_chronik_service_startup.py",
    "tools/engineering/check_chronik_service_startup_self_test.py",
    "tools/engineering/check_chronik_service_recovery_self_test.py",
    "tools/engineering/check_chronik_regtest_service_self_test.py",
    "tools/engineering/check_chronik_plugin_restart_self_test.py",
    ".github/workflows/chronik-upstream-link.yml"})
CONTRACT = {
    "parent_status": "signed-local-review-checkpoint-not-assumed-public-main",
    "compile_default": False, "runtime_default": False, "plugins": False,
    "network": "bounded-unpruned-regtest", "maximum_height": 2000,
    "scope": "test-only-no-production-source-or-dependency-change",
    "template_case": "chronik_upstream_regtest_tests/export_upstream_alp_templates",
    "template_assertions": 4, "template_source": "existing-upstream-ALP-builders-through-fixture_alp_script",
    "placeholder": "exact-one-32-byte-0x42-token-id-replaced-with-native-genesis-txid-little-endian",
    "native_value": "zero-inputs-outputs-fees-not-funded-token-economics",
    "synthetic_fee_priority_fixoshis_per_transaction": 1000000,
    "cycle": ["GENESIS-1000-plus-baton", "MINT-500-plus-baton", "SEND-1400-and-100", "BURN-100"],
    "oracle": "exact-native-first-seen-wire-colors-spenders-history-token-utxos-and-baton",
    "lifecycle": ["pending", "pending-process-reopen", "confirm", "disconnect", "reconsider", "confirmed-process-reopen"],
    "negative_controls": ["native-accepted-mint-without-baton-diagnosed-invalid", "native-accepted-truncated-upstream-metadata-diagnosed-malformed", "native-mining-continues"],
    "retained_matrices": "unchanged-selections-assertions-60-second-deadlines-and-cleanup",
    "retained_stop_timeout": "unresolved-not-PR-ready-no-oracle-relaxation",
    "consensus_change": False, "incoming_evidence": [],
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}


def before_identity(root, path):
    row = git(root, "ls-tree", PARENT, "--", path).decode().split()
    return identity(git(root, "show", PARENT + ":" + path), row[0]) if row else None


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "ALP parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and manifest["license"] == "MIT" and
            manifest["schema"] == "ergon-upstream-service-alp/v1" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT, "ALP contract drift")
    require([r["path"] for r in manifest["files"]] == sorted(TECHNICAL), "ALP inventory drift")
    for row in manifest["files"]:
        expected_before = before_identity(root, row["path"])
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["before"] == expected_before and row["after"] == current_identity(root, row["path"]) and
                row["before"] != row["after"] and row["after"]["mode"] == "100644" and
                row["provenance"] == ("A1/MIT" if expected_before is None else "PUBLIC+A1/MIT") and
                row["reachability"] == "test-only-no-daemon-runtime-change", "ALP source drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == TECHNICAL | POLICY, "unexpected ALP path")
    record = load_json(root / RECORD)
    from check_change import ValidationError, validate as validate_change
    try:
        validate_change(record, RECORD)
    except ValidationError as error:
        raise ValueError("ALP governance schema/inventory drift") from error
    require(record["change_id"] == "ERGON-CHANGE-0052" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question", "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(changed - {RECORD}), "ALP governance drift")
    def governed(v):
        return None if v is None else {("git_blob_sha1" if k == "git_blob" else k): value for k, value in v.items()}
    for row in record["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                governed(row["after"]) == current_identity(root, row["path"]), "ALP policy drift")
    driver = ast.parse((root / "tests/compatibility/upstream-chronik/alp_service.py").read_text())
    constants = {t.id: ast.literal_eval(n.value) for n in driver.body if isinstance(n, ast.Assign)
                 for t in n.targets if isinstance(t, ast.Name) and t.id in ("TEMPLATE_CASE", "TEMPLATE_ASSERTIONS")}
    require(constants == {"TEMPLATE_CASE": CONTRACT["template_case"], "TEMPLATE_ASSERTIONS": 4}, "ALP template case drift")
    # Whole exact path law already excludes all other writes. Explicitly bind
    # authority/history and production invariants even in patched gate tests.
    for path in ("src/init.cpp", "src/validation.cpp", "src/validation.h", "src/chainparams.cpp",
                 "src/txmempool.cpp", "src/txmempool.h", "CMakeLists.txt", "chronik/UpstreamLink.cmake",
                 "chronik/Cargo.lock", "chronik/Cargo.toml", "chronik/chronik-lib/Cargo.toml",
                 "chronik/chronik-lib/src/ergon_archival.rs", "chronik/chronik-lib/src/ergon_service.rs",
                 "chronik/chronik-lib/src/ergon_http_tests.rs", "chronik/chronik-http/src/server.rs",
                 "chronik/chronik-indexer/src/indexer.rs", "chronik/bitcoinsuite-slp/src/alp/build.rs",
                 "tests/compatibility/upstream-chronik/regtest_service.py",
                 "docs/engineering/changes/ergon-change-0051.json",
                 "docs/engineering/chronik/upstream-service-startup-v1.json"):
        require(current_identity(root, path) == before_identity(root, path), "ALP changed frozen production/history/input")
    return TECHNICAL | POLICY


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: finite test-only daemon ALP cycle; retained timeout still blocks PR")

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exact archival-regtest history scope; never mainnet admission."""
import argparse
import ast
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, load_json, require
from check_chronik_service_catchup import revision_identity
from check_chronik_owned_skip_links import native_cases

PARENT = "3c84f5dcda5a5671ea5ead1ebdd7180f6c76838a"
TREE = "5b6438e2780e93b2f9d84edfd0dc2ffe3f44e2bc"
MANIFEST = "docs/engineering/chronik/upstream-long-history-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0062.json"
TECHNICAL = frozenset({"chronik/chronik-cpp/ergon_node_access.h",
    "chronik/chronik-cpp/ergon_regtest_service.cpp", "chronik/chronik-cpp/chronik_bridge.cpp",
    "chronik/chronik-cpp/ergon_archival_probe.cpp", "chronik/chronik-cpp/ergon_archival_probe.h",
    "chronik/chronik-lib/src/ergon_archival.rs", "src/init.cpp",
    "src/test/chronik_upstream_archival_tests.cpp",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/self_test.py",
    "tests/compatibility/upstream-chronik/regtest_service.py",
    "tests/compatibility/upstream-chronik/cold_rebuild_service.py",
    "tests/compatibility/upstream-chronik/long_history_service.py",
    "tests/compatibility/upstream-chronik/long_history_service_self_test.py"})
MANIFEST_FILES = TECHNICAL | {".github/workflows/chronik-upstream-link.yml",
    "docs/engineering/chronik/upstream-long-history.md",
    "tools/engineering/check_chronik_long_history.py",
    "tools/engineering/check_chronik_long_history_self_test.py",
    "tools/engineering/check_chronik_native_startup_self_test.py",
    "tools/engineering/check_chronik_upstream_link.py",
    "tools/engineering/check_chronik_service_catchup_self_test.py",
    *("tools/engineering/check_chronik_" + name + ".py" for name in (
        "native_startup", "startup_metadata", "owned_skip_links", "startup_release", "startup_batches",
        "plugin_fixtures", "regtest_service", "plugin_restart", "service_recovery", "service_startup",
        "service_alp", "service_plugins"))}
CHANGED = MANIFEST_FILES | {MANIFEST, RECORD}
CASES = {"history_scope_default_fixture_bounds_are_unchanged": 8,
    "history_scope_service_keeps_height_arithmetic_and_authority_closed": 8}
CONTRACT = {"compile_default": False, "runtime_default": False, "network": "unpruned-local-regtest",
    "fixture_maximum_height": 2000, "fixture_maximum_indexes": 4096,
    "service_history_cap": "signed-int32-max-minus-one",
    "scope": "immutable-construction-only-selected-by-joined-service-owner",
    "maximum_mempool": 4096, "retained_blocks": 288, "payload_bytes": 67108864,
    "deep_reorg": "fail-closed-below-retained-anchor",
    "native_cases": CASES, "native_process_timeout_seconds": 60,
    "long_history_height": 5000, "long_history_startup_timeout_seconds": 120,
    "inherited_startup_timeout_seconds": 30, "automatic_retry": False,
    "timeout_diagnostic": "failure-only-public-case-whitelist-after-child-stop-no-raw-output",
    "consensus_change": False, "upstream_engine_change": False, "dependency_change": False,
    "mainnet_admission": False, "incoming_evidence": [], "knowledge_status": "Open Question",
    "evidence_ceiling": "record-review-only"}
TIMEOUT_HANDLER = '''    except subprocess.TimeoutExpired:
        # The original inner finally has already stopped the owned child group.
        progress = timeout_progress(run / "stderr")
        print("REFUSED: native-process-timeout; last_started=" + progress["start"] +
              "; last_completed=" + progress["done"], file=sys.stderr, flush=True)
        raise
'''


def validate_runner_protocol(before, after):
    def functions(text):
        try:
            tree = ast.parse(text)
        except SyntaxError as error:
            raise ValueError("malformed native runner protocol") from error
        return {node.name: ast.get_source_segment(text, node) for node in tree.body
                if isinstance(node, ast.FunctionDef)}
    if 'CATALOGUE_PREFIX = ' in after:
        from check_chronik_position_catalogue import normalize_runner
        after = normalize_runner(after)
    old, new = functions(before), functions(after)
    require(set(new) - set(old) == {"timeout_progress"} and not set(old) - set(new),
            "long history added unrelated runner behavior")
    require(new["execute"].count(TIMEOUT_HANDLER) == 1 and
            new["execute"].replace(TIMEOUT_HANDLER, "", 1) == old["execute"],
            "long history changed child wait/stop/cleanup or timeout law")
    require(new["repeat"].replace('== 58, "non-disjoint test groups"',
                                  '== 56, "non-disjoint test groups"') == old["repeat"],
            "long history changed inherited repetition/selection law")
    for name in set(old) - {"source_projection", "execute", "repeat"}:
        require(new[name] == old[name], "long history changed inherited runner protocol")
    helper = new["timeout_progress"]
    for token in ("*GROUPS, *PLUGIN_GROUPS, DISABLED_GROUP", "os.O_NOFOLLOW | os.O_NONBLOCK",
                  "stat.S_ISREG", "2 * 1024 * 1024 + 1", 'errors="strict"',
                  'line[len(prefix):] in names', "os.close(fd)"):
        require(token in helper, "long history widened timeout diagnostic surface")


def before_identity(root, path):
    return revision_identity(root, PARENT, path)


def successor_identity(root, path, previous):
    if not (root / MANIFEST).exists():
        return previous
    rows = [r for r in load_json(root / MANIFEST)["files"] if r["path"] == path]
    result = previous
    if rows:
        require(path in MANIFEST_FILES and len(rows) == 1 and rows[0]["before"] == before_identity(root, path) and
                previous in (rows[0]["before"], rows[0]["after"]), "long history successor preimage drift")
        result = rows[0]["after"]
    from check_chronik_position_catalogue import successor_identity as catalogue_successor
    return catalogue_successor(root, path, result)


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "long history parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and
            manifest["schema"] == "ergon-upstream-long-history/v1" and manifest["license"] == "MIT" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and manifest["contract"] == CONTRACT and
            [r["path"] for r in manifest["files"]] == sorted(MANIFEST_FILES), "long history manifest drift")
    for row in manifest["files"]:
        before = before_identity(root, row["path"])
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and row["before"] == before and
                row["after"] != before and successor_identity(root, row["path"], row["after"]) == current_identity(root, row["path"]) and
                row["after"]["mode"] == "100644" and row["provenance"] == ("A1/MIT" if before is None else "PUBLIC+A1/MIT") and
                row["reachability"] == ("regtest-native-or-test-only" if row["path"] in TECHNICAL else "test-only"),
                "long history source drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    expected = CHANGED
    if (root / "docs/engineering/chronik/upstream-position-catalogue-v1.json").exists():
        from check_chronik_position_catalogue import CHANGED as catalogue_changed, validate as validate_catalogue
        expected |= catalogue_changed
        validate_catalogue(root)
    require(changed == expected, "unexpected long history path")
    access = (root / "chronik/chronik-cpp/ergon_node_access.h").read_text()
    for token in ("const NativeReadScope m_scope", "scope = NativeReadScope::BoundedFixture",
                  "height < std::numeric_limits<int32_t>::max()", "height <= 2000", "count <= 4096"):
        require(token in access, "long history immutable/fixture/arithmetic law drift")
    owner = (root / "chronik/chronik-cpp/ergon_regtest_service.cpp").read_text()
    require("chronik_bridge::NativeReadScope::ArchivalRegtestService" in owner and
            "thread.join();" in owner, "long history owner selection/join drift")
    native = "src/test/chronik_upstream_archival_tests.cpp"
    before = native_cases(git(root, "show", PARENT + ":" + native).decode())
    after = native_cases((root / native).read_text())
    require(len(before) == 68 and set(after) - set(before) == set(CASES) and
            all(after.get(name) == body for name, body in before.items()), "long history inherited case drift")
    from check_chronik_native_startup import section
    bridge = (root / "chronik/chronik-cpp/chronik_bridge.cpp").read_text()
    old = git(root, "show", PARENT + ":chronik/chronik-cpp/chronik_bridge.cpp").decode()
    for start, end in (("void ChronikBridge::capture_startup_snapshot", "bool ChronikBridge::finish_startup_snapshot"),
                       ("bool ChronikBridge::finish_startup_snapshot", "NativePositionStats")):
        require(section(bridge, start, end) == section(old, start, end), "long history changed strict owned startup")
    probe = (root / "chronik/chronik-cpp/ergon_archival_probe.cpp").read_text()
    require(probe.count('NetworkIDString() != "regtest"') == 2 and
            "constexpr int WINDOW = 288" in probe and "64 * 1024 * 1024 - bytes" in probe and
            "node.mempool.size() > 4096" in probe, "long history widened network/capture/pool admission")
    helper = (root / "tests/compatibility/upstream-chronik/regtest_service.py").read_text()
    require("def ready(self, available=True, *, startup_timeout=30):" in helper and
            "def wait_for(check, timeout=30):" in helper, "long history weakened inherited startup deadlines")
    driver = (root / "tests/compatibility/upstream-chronik/long_history_service.py").read_text()
    require("HISTORY_HEIGHT = 5000" in driver and "STARTUP_TIMEOUT_SECONDS = 120" in driver and
            "cold.observe(binary, work, scripts" in driver, "long history forked upstream reproduction driver")
    runner_path = "tests/compatibility/upstream-chronik/run_repetitions.py"
    validate_runner_protocol(git(root, "show", PARENT + ":" + runner_path).decode(),
                             (root / runner_path).read_text())
    for path in ("src/validation.cpp", "src/validation.h", "src/chainparams.cpp", "src/chain.cpp", "src/chain.h",
                 "src/txmempool.cpp", "chronik/Cargo.lock", "chronik/UpstreamLink.cmake",
                 "chronik/chronik-indexer/src/indexer.rs", "chronik/chronik-plugin-impl/src/context.rs"):
        require(current_identity(root, path) == before_identity(root, path), "long history changed frozen native/engine input")
    from check_change import validate as validate_change
    record = load_json(root / RECORD); validate_change(record, RECORD)
    require(record["change_id"] == "ERGON-CHANGE-0062" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question",
                                   "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(CHANGED - {RECORD}), "long history record drift")
    for row in record["files"]:
        normalize = lambda v: None if v is None else {("git_blob_sha1" if k == "git_blob" else k): i for k, i in v.items()}
        require(normalize(row["before"]) == before_identity(root, row["path"]) and
                successor_identity(root, row["path"], normalize(row["after"])) == current_identity(root, row["path"]), "long history record identity drift")
    return CHANGED


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: immutable archival-regtest history scope; inherited fixture and live-resource budgets preserved")

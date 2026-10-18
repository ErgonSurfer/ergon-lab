#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Finite service-native startup successor, not a mainnet admission gate."""
import argparse
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, load_json, require
from check_chronik_service_catchup import revision_identity
from check_chronik_owned_skip_links import native_cases

PARENT = "2fd87f2267806438e0153cab735ab763fc6987e6"
TREE = "c80883119232f98f4467eaf41f1567d04cffefc5"
MANIFEST = "docs/engineering/chronik/upstream-native-startup-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0061.json"
BRIDGE = "chronik/chronik-cpp/chronik_bridge.cpp"
NATIVE = "src/test/chronik_upstream_archival_tests.cpp"
RUNNER = "tests/compatibility/upstream-chronik/run_repetitions.py"
TECHNICAL = frozenset({BRIDGE, "chronik/chronik-cpp/chronik_bridge.h",
    "chronik/chronik-cpp/ergon_archival_probe.cpp",
    "chronik/chronik-cpp/ergon_archival_probe.h",
    "chronik/chronik-lib/src/ergon_archival.rs", NATIVE, RUNNER,
    "tests/compatibility/upstream-chronik/self_test.py"})
MANIFEST_FILES = TECHNICAL | {
    ".github/workflows/chronik-upstream-link.yml",
    "docs/engineering/chronik/upstream-native-startup.md",
    "tools/engineering/check_chronik_native_startup.py",
    "tools/engineering/check_chronik_native_startup_self_test.py",
    "tools/engineering/check_chronik_owned_skip_links.py",
    "tools/engineering/check_chronik_startup_metadata.py",
    "tools/engineering/check_chronik_startup_release.py",
    "tools/engineering/check_chronik_startup_batches.py"}
MANIFEST_FILES |= {"tools/engineering/check_chronik_service_catchup.py"}
CHANGED = MANIFEST_FILES | {MANIFEST, RECORD}
CASES = {
    "native_startup_header_loan_has_no_owned_graph": 14,
    "native_startup_captured_branch_survives_progress_and_reorg": 18,
    "native_startup_reads_reject_mid_read_storage_drift": 10,
    "native_startup_cancellation_pruning_and_owner_fail_closed": 11,
    "native_startup_foreign_index_and_borrow_lifetime": 7,
    "native_startup_upstream_rewinds_offline_branch_and_reopens": 14}
CONTRACT = {
    "compile_default": False, "runtime_default": False,
    "network": "bounded-unpruned-regtest", "maximum_height": 2000,
    "maximum_indexes": 4096, "maximum_mempool": 4096,
    "service_capture": "constant-size-native-tip-chain-and-pool-generation",
    "immutable_read_set": ["phashBlock", "pprev", "pskip", "nHeight",
                           "nVersion", "hashMerkleRoot", "nTime", "nBits", "nNonce"],
    "lifetime": "node-owners-outlive-joined-startup-worker-http-and-bridge",
    "unsupported": ["live-index-unload", "live-index-reload", "pruning",
                    "native-header-replacement", "storage-relocation"],
    "mutable_positions": "cs-main-snapshot-unlocked-read-cs-main-recheck",
    "fork": "last-common-ancestor-against-captured-tip-not-active-chain",
    "owned_fixture_startup": "unchanged-including-atomic-final-storage-fence",
    "service_final_fence": "constant-size-native-owner-and-health-check-no-copied-positions",
    "native_cases": CASES, "per_process_timeout_seconds": 60,
    "upstream_engine_change": False, "dependency_change": False,
    "consensus_change": False, "limit_change": False,
    "incoming_evidence": [], "knowledge_status": "Open Question",
    "evidence_ceiling": "record-review-only"}


def before_identity(root, path):
    return revision_identity(root, PARENT, path)


def successor_identity(root, path, previous):
    rows = [r for r in load_json(root / MANIFEST)["files"] if r["path"] == path]
    result = previous
    if rows:
        require(path in MANIFEST_FILES and len(rows) == 1 and
                rows[0]["before"] == before_identity(root, path) and
                previous in (rows[0]["before"], rows[0]["after"]),
                "native startup successor preimage drift")
        result = rows[0]["after"]
    from check_chronik_long_history import successor_identity as history_successor
    return history_successor(root, path, result)


def section(text, start, end):
    begin = text.index(start)
    return text[begin:text.index(end, begin)]


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"",
            "native startup parent drift")
    manifest = load_json(root / MANIFEST)
    history_present = (root / "docs/engineering/chronik/upstream-long-history-v1.json").exists()
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and
            manifest["schema"] == "ergon-upstream-native-startup/v1" and
            manifest["license"] == "MIT" and manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT and
            [r["path"] for r in manifest["files"]] == sorted(MANIFEST_FILES),
            "native startup manifest drift")
    for row in manifest["files"]:
        before = before_identity(root, row["path"])
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["before"] == before and row["after"] != before and
                successor_identity(root, row["path"], row["after"]) == current_identity(root, row["path"]) and row["after"]["mode"] == "100644" and
                row["provenance"] == ("A1/MIT" if before is None else "PUBLIC+A1/MIT") and
                row["reachability"] == ("bounded-regtest-native-or-test-only"
                    if row["path"] in TECHNICAL else "test-only"),
                "native startup source identity drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    expected = CHANGED
    if history_present:
        from check_chronik_long_history import CHANGED as history_changed, validate as validate_history
        expected |= history_changed
        validate_history(root)
    require(changed == expected, "unexpected native startup path")
    old = git(root, "show", PARENT + ":" + BRIDGE).decode()
    bridge = (root / BRIDGE).read_text()
    for start, end in (("void ChronikBridge::capture_startup_snapshot", "bool ChronikBridge::finish_startup_snapshot"),
                       ("bool ChronikBridge::finish_startup_snapshot", "NativePositionStats")):
        require(section(old, start, end) == section(bridge, start, end).replace(
            "if (m_startup || m_native_startup)", "if (m_startup)"),
            "native startup changed owned fixture startup")
    capture = section(bridge, "void ChronikBridge::capture_native_startup", "bool ChronikBridge::finish_native_startup")
    require("std::make_unique<NativeStartup>()" in capture and
            capture.index("std::make_unique<NativeStartup>()") < capture.index("LOCK2(") and
            "captured->tip = tip" in capture and
            ("m_node.permits_index_count(mapBlockIndex.size())" if history_present else "mapBlockIndex.size() > 4096") in capture and
            all(token not in capture for token in ("for (", "while (", "CChain ", "std::vector", "std::map")),
            "native startup complete graph copy or admission drift")
    fork = section(bridge, "const CBlockIndex &ChronikBridge::find_fork", "void ChronikBridge::lookup_spent_coins")
    require("LastCommonAncestor(&index, m_native_startup->tip)" in fork and
            "LookupBlockIndex(index.GetBlockHash()) != &index" in fork,
            "native startup captured fork binding drift")
    for method, end in (("ChronikBridge::load_block(const", "std::unique_ptr<CBlockUndo>"),
                        ("ChronikBridge::load_block_undo(const", "Tx ChronikBridge::load_tx")):
        read = section(bridge, method, end)
        require(read.index("Snapshot(index)") < read.index("m_block_read_hook()") <
                read.index("CheckStorage(index, snapshot)"),
                "native startup disk read fence drift")
    before = native_cases(git(root, "show", PARENT + ":" + NATIVE).decode())
    after = native_cases((root / NATIVE).read_text())
    additions = set(CASES)
    if history_present:
        from check_chronik_long_history import CASES as history_cases
        additions |= set(history_cases)
    require(len(before) == 62 and set(after) - set(before) == additions and
            all(after.get(name) == body for name, body in before.items()),
            "native startup changed inherited native case")
    host = (root / "chronik/chronik-lib/src/ergon_archival.rs").read_text()
    require("bridge: if service { ffi::make_service_startup_bridge(access)? }" in host and
            "else { ffi::make_startup_bridge(access)? }" in host,
            "native startup service/fixture factory drift")
    runner = (root / RUNNER).read_text()
    require(all('"' + case + '"' in runner for case in CASES) and
            '("native-startup", NATIVE_STARTUP, 74)' in runner and
            '("owned-startup", STARTUP, 607)' in runner,
            "native startup exact runner drift")
    # These immutable-writer, unpruned-storage and teardown inputs are pinned,
    # not inferred from a new runtime flag. Changes require another review.
    for path in ("src/chain.cpp", "src/chain.h", "src/validation.cpp", "src/validation.h",
                 "src/txdb.cpp", "src/init.cpp", "src/txmempool.cpp", "chronik/Cargo.lock",
                 "chronik/chronik-cpp/ergon_regtest_service.cpp",
                 "chronik/chronik-cpp/ergon_archival_worker.cpp",
                 "chronik/chronik-cpp/ergon_node_access.h",
                 "chronik/chronik-indexer/src/indexer.rs",
                 "chronik/chronik-plugin-impl/src/context.rs"):
        require(current_identity(root, path) == successor_identity(root, path, before_identity(root, path)),
                "native startup changed immutable/storage/lifetime or upstream contract")
    from check_change import validate as validate_change
    record = load_json(root / RECORD)
    validate_change(record, RECORD)
    require(record["change_id"] == "ERGON-CHANGE-0061" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question",
                                   "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(CHANGED - {RECORD}),
            "native startup governance drift")
    for row in record["files"]:
        normalized = lambda v: None if v is None else {("git_blob_sha1" if k == "git_blob" else k): i for k, i in v.items()}
        require(normalized(row["before"]) == before_identity(root, row["path"]) and
                successor_identity(root, row["path"], normalized(row["after"])) == current_identity(root, row["path"]),
                "native startup governance identity drift")
    return CHANGED


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: service startup borrows the captured native branch; owned fixtures and runtime limits preserved")

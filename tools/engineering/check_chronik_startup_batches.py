#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bind the finite batched startup capture to its reviewed public parent."""
import argparse
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, load_json, require
from check_chronik_service_catchup import revision_identity
from check_chronik_owned_skip_links import native_cases

PARENT = "dd349dca3e0429b4c710e17a9e0bd513947f3265"
TREE = "29c4c5a47633faaa396a9f8ba9d52c415552ca12"
MANIFEST = "docs/engineering/chronik/upstream-startup-batches-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0060.json"
BRIDGE = "chronik/chronik-cpp/chronik_bridge.cpp"
NATIVE = "src/test/chronik_upstream_archival_tests.cpp"
RUNNER = "tests/compatibility/upstream-chronik/run_repetitions.py"
TECHNICAL = frozenset({BRIDGE, "chronik/chronik-cpp/chronik_bridge.h", NATIVE,
    RUNNER, "tests/compatibility/upstream-chronik/self_test.py"})
MANIFEST_FILES = TECHNICAL | {
    ".github/workflows/chronik-upstream-link.yml",
    "docs/engineering/chronik/upstream-startup-batches.md",
    "tools/engineering/check_chronik_owned_skip_links.py",
    "tools/engineering/check_chronik_startup_metadata.py",
    "tools/engineering/check_chronik_startup_release.py",
    "tools/engineering/check_chronik_startup_batches.py",
    "tools/engineering/check_chronik_startup_batches_self_test.py"}
CHANGED = MANIFEST_FILES | {MANIFEST, RECORD}
CASES = {
    "startup_batches_release_both_locks_and_cover_every_index": 10,
    "startup_batches_reject_native_progress_before_publication": 8,
    "startup_batches_reject_pool_aba_and_index_rehash": 16,
    "startup_batches_cancel_and_hook_exception_leave_no_owner": 14,
    "startup_batches_final_fence_rejects_last_batch_drift": 6}
CONTRACT = {
    "compile_default": False, "runtime_default": False,
    "network": "bounded-unpruned-regtest", "maximum_height": 2000,
    "maximum_indexes": 4096, "maximum_mempool": 4096,
    "metadata_rows_per_lock": 64,
    "cursor": "owned-next-hash-no-native-pointer-or-iterator-between-batches",
    "capture_fences": ["healthy-profile", "tip-hash", "chain-generation",
                       "mempool-generation", "map-size", "bucket-count"],
    "final_capture_fence": True, "final_storage_fence": "unchanged-atomic",
    "publication": "complete-owned-graph-only-after-final-capture-fence",
    "failure": "unpublished-owner-destroyed-no-automatic-retry",
    "native_cases": CASES, "per_process_timeout_seconds": 60,
    "incoming_evidence": [], "knowledge_status": "Open Question",
    "evidence_ceiling": "record-review-only"}


def before_identity(root, path):
    return revision_identity(root, PARENT, path)


def successor_identity(root, path, previous):
    rows = [row for row in load_json(root / MANIFEST)["files"] if row["path"] == path]
    result = previous
    if rows:
        require(path in MANIFEST_FILES and len(rows) == 1 and
                previous in (rows[0]["before"], rows[0]["after"]),
                "startup batches successor preimage drift")
        result = rows[0]["after"]
    if (root / "docs/engineering/chronik/upstream-native-startup-v1.json").exists():
        from check_chronik_native_startup import successor_identity as native_successor
        result = native_successor(root, path, result)
    return result


def without_capture(text):
    start = text.index("void ChronikBridge::capture_startup_snapshot")
    end = text.index("bool ChronikBridge::finish_startup_snapshot", start)
    return text[:start].replace("#include <thread>\n", "") + text[end:]


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"",
            "startup batches parent drift")
    manifest = load_json(root / MANIFEST)
    native_present = (root / "docs/engineering/chronik/upstream-native-startup-v1.json").exists()
    require(set(manifest) == {"$comment", "schema", "license", "public_parent",
                             "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and
            manifest["schema"] == "ergon-upstream-startup-batches/v1" and
            manifest["license"] == "MIT" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT and
            [r["path"] for r in manifest["files"]] == sorted(MANIFEST_FILES),
            "startup batches manifest drift")
    for row in manifest["files"]:
        before = before_identity(root, row["path"])
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["before"] == before and row["after"] != before and
                successor_identity(root, row["path"], row["after"]) == current_identity(root, row["path"]) and
                row["after"]["mode"] == "100644" and
                row["provenance"] == ("A1/MIT" if before is None else "PUBLIC+A1/MIT") and
                row["reachability"] == ("bounded-regtest-native-or-test-only"
                    if row["path"] in TECHNICAL else "test-only"),
                "startup batches file identity drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    expected = CHANGED
    if native_present:
        from check_chronik_native_startup import CHANGED as native_changed
        from check_chronik_native_startup import PARENT as native_parent, validate as validate_native
        expected |= native_changed
        validate_native(root)
    require(changed == expected, "unexpected startup batches path")

    old = git(root, "show", PARENT + ":" + BRIDGE).decode()
    bridge = (git(root, "show", native_parent + ":" + BRIDGE).decode()
              if native_present else (root / BRIDGE).read_text())
    require(without_capture(old) == without_capture(bridge),
            "startup batches changed final storage fence or unrelated bridge behavior")
    capture = bridge[bridge.index("void ChronikBridge::capture_startup_snapshot"):
                     bridge.index("bool ChronikBridge::finish_startup_snapshot")]
    for token in ("constexpr size_t BATCH_LIMIT = 64", "copied < BATCH_LIMIT",
                  "mapBlockIndex.size() == index_count",
                  "mapBlockIndex.bucket_count() == bucket_count",
                  "mapBlockIndex.find(next_hash)", "if (!inserted.second)",
                  "ChainActive().GetSourceGeneration() == owned->generation",
                  "m_node.mempool.GetSourceGeneration() == owned->revision"):
        require(token in capture, "startup batches capture fence drift")
    handoff = capture.index("if (after_batch)")
    require(capture.index("next_hash = cursor->first;") <
            capture.index("AssertLockNotHeld(cs_main)", capture.index("while (metadata.size()")) <
            handoff < capture.rindex("if (!unchanged())") < capture.index("after_metadata()") <
            capture.index("std::make_unique<CBlockIndex>") < capture.index("m_startup = std::move(owned)"),
            "startup batches publication/handoff ordering drift")
    before = native_cases(git(root, "show", PARENT + ":" + NATIVE).decode())
    after = native_cases((root / NATIVE).read_text())
    additions = set(CASES)
    if native_present:
        from check_chronik_native_startup import CASES as native_cases_added
        additions |= set(native_cases_added)
    require(len(before) == 57 and set(after) - set(before) == additions and
            all(after.get(name) == body for name, body in before.items()),
            "startup batches changed inherited native cases")
    runner = (root / RUNNER).read_text()
    require(all('"' + case + '"' in runner for case in CASES) and
            '("startup-metadata-batches", BATCHES, 54)' in runner and
            '("owned-startup", STARTUP, 607)' in runner,
            "startup batches selection drift")
    for path in ("src/init.cpp", "src/chain.h", "src/chain.cpp", "src/validation.cpp",
                 "src/txmempool.cpp", "chronik/Cargo.lock",
                 "chronik/chronik-lib/src/ergon_archival.rs",
                 "chronik/chronik-indexer/src/indexer.rs",
                 "chronik/chronik-plugin-impl/src/context.rs"):
        require(current_identity(root, path) == successor_identity(root, path, before_identity(root, path)),
                "startup batches changed frozen native/engine/dependency input")

    from check_change import validate as validate_change
    record = load_json(root / RECORD)
    validate_change(record, RECORD)
    require(record["change_id"] == "ERGON-CHANGE-0060" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question",
                                   "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(CHANGED - {RECORD}),
            "startup batches governance drift")
    for row in record["files"]:
        normalize = lambda value: None if value is None else {
            ("git_blob_sha1" if k == "git_blob" else k): v for k, v in value.items()}
        require(normalize(row["before"]) == before_identity(root, row["path"]) and
                successor_identity(root, row["path"], normalize(row["after"])) == current_identity(root, row["path"]),
                "startup batches governance identity drift")
    return CHANGED


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: startup capture releases native locks after at most 64 metadata rows")

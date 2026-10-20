#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Finite native-position catalogue successor; no upstream-engine change."""
import argparse
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, load_json, require
from check_chronik_service_catchup import revision_identity
from check_chronik_owned_skip_links import native_cases

PARENT = "7cb90b95c34133163b8452a56c3239a7b976f5fd"
TREE = "c599903c2f2b1887466d1593fe23f813bc3ca24f"
MANIFEST = "docs/engineering/chronik/upstream-position-catalogue-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0063.json"
READER = "chronik/chronik-cpp/ergon_position_reader.cpp"
HEADER = "chronik/chronik-cpp/ergon_position_reader.h"
NATIVE = "src/test/chronik_native_upstream_bridge_tests.cpp"
RUNNER = "tests/compatibility/upstream-chronik/run_repetitions.py"
TECHNICAL = frozenset({READER, HEADER, NATIVE, RUNNER,
    "tests/compatibility/upstream-chronik/self_test.py"})
MANIFEST_FILES = TECHNICAL | {
    ".github/workflows/chronik-upstream-link.yml",
    "docs/engineering/chronik/upstream-position-catalogue.md",
    "tools/engineering/check_chronik_position_catalogue.py",
    "tools/engineering/check_chronik_position_catalogue_self_test.py",
    *("tools/engineering/check_chronik_" + name + ".py" for name in (
        "upstream_link", "long_history", "native_startup",
        "owned_skip_links", "startup_release", "startup_batches", "startup_metadata"))}
CHANGED = MANIFEST_FILES | {MANIFEST, RECORD}
CASES = {
    "position_catalogue_batches_release_native_locks": 12,
    "position_catalogue_staging_rejects_native_aba": 8,
    "position_catalogue_final_fence_rejects_cancelled_source": 7,
    "position_catalogue_final_fence_rejects_native_progress": 7,
    "position_catalogue_storage_drift_is_not_a_generation_fence": 5,
    "position_catalogue_concurrent_refresh_waits_without_native_locks": 11}
CONTRACT = {"parent_status": "published-PR65-head-not-assumed-merged",
    "compile_default": False, "runtime_default": False, "network": "unpruned-local-regtest",
    "catalogue_batch_rows": 64, "maximum_ancestry_probes": 31, "cached_layouts": 8,
    "construction": "exclusive-staging-owner-outside-native-locks",
    "refresh_wait": "serialize-after-releasing-native-and-metadata-locks",
    "fences": "native-health-tip-generation-each-batch-and-final-publication",
    "storage_identity": "selected-position-plus-unchanged-before-and-after-disk-read-checks",
    "abort": "discard-incomplete-staging-outside-native-locks-no-node-poisoning",
    "initial_total_work_and_memory": "still-O(height)", "normal_suffix_work": "incremental",
    "native_cases": CASES, "new_assertions": 50, "inherited_bridge_cases": 8,
    "inherited_bridge_assertions": 166, "native_process_timeout_seconds": 60,
    "inherited_groups": "exactly-unchanged-in-relative-order-counts-and-bodies",
    "new_case_processes": "six-independent-leaf-processes-per-repetition",
    "ci_repetitions": 10, "long_history_blocks": 5000,
    "long_history_startup_timeout_seconds": 120, "automatic_retry": False,
    "consensus_change": False, "upstream_engine_change": False,
    "dependency_change": False, "mainnet_admission": False,
    "incoming_evidence": [], "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}

CATALOGUE_BLOCK = '''CATALOGUE_PREFIX = "chronik_native_position_catalogue_tests/"
CATALOGUE_GROUPS = tuple((name, (CATALOGUE_PREFIX + name,), count) for name, count in (
    ("position_catalogue_batches_release_native_locks", 12),
    ("position_catalogue_staging_rejects_native_aba", 8),
    ("position_catalogue_final_fence_rejects_cancelled_source", 7),
    ("position_catalogue_final_fence_rejects_native_progress", 7),
    ("position_catalogue_storage_drift_is_not_a_generation_fence", 5),
    ("position_catalogue_concurrent_refresh_waits_without_native_locks", 11),
))
'''
PROJECTION_BLOCK = '''    catalogue_manifest = source / "docs/engineering/chronik/upstream-position-catalogue-v1.json"
    if catalogue_manifest.exists():
        from check_chronik_position_catalogue import PARENT, TREE, MANIFEST_FILES
        layer = json.loads(regular(catalogue_manifest))
        require(layer["schema"] == "ergon-upstream-position-catalogue/v1" and
                layer["public_parent"] == {"commit": PARENT, "tree": TREE} and
                [r["path"] for r in layer["files"]] == sorted(MANIFEST_FILES), "position catalogue projection drift")
        merged = {r["path"]: r for r in rows}
        for row in layer["files"]:
            if row["path"] in merged:
                require(row["before"] == merged[row["path"]]["after"], "position catalogue projection preimage drift")
            merged[row["path"]] = row
        rows = [merged[p] for p in sorted(merged)]
'''
FILTER_BLOCK = '''    if cases in tuple(group[1] for group in CATALOGUE_GROUPS):
        return cases[0]
'''


def normalize_runner(text):
    """Remove only the literal new selection/projection; never scrub protocol."""
    from check_chronik_multi_lokad import normalize_runner as normalize_multi_lokad
    text = normalize_multi_lokad(text)
    for new, old in ((CATALOGUE_BLOCK, ""), (PROJECTION_BLOCK, ""), (FILTER_BLOCK, ""),
            ("(*GROUPS, *CATALOGUE_GROUPS, *PLUGIN_GROUPS, DISABLED_GROUP)",
             "(*GROUPS, *PLUGIN_GROUPS, DISABLED_GROUP)"),
            ("groups = GROUPS + CATALOGUE_GROUPS + (PLUGIN_GROUPS",
             "groups = GROUPS + (PLUGIN_GROUPS")):
        require(text.count(new) == 1, "catalogue runner selection/projection drift")
        text = text.replace(new, old, 1)
    return text


def validate_runner_protocol(before, after):
    require(normalize_runner(after) == before,
            "catalogue changed inherited runner protocol, globals, counts, deadlines or cleanup")


def validate_reader_source(before, after):
    # Full disk and authentication paths are not part of this change.
    start = "uint32_t Position("
    require(before[before.index(start):before.index("} // namespace")] ==
            after[after.index(start):after.index("} // namespace")], "catalogue changed native disk authentication")
    start = "    void Check(const Reference &ref) const {"
    require(before[before.index(start):before.rindex("} // namespace chronik_bridge")] ==
            after[after.index(start):after.index("void NativePositionReader::AfterCatalogueBatchForTesting")],
            "catalogue changed read/cache/undo/final-source law")
    for token in ("constexpr size_t CATALOGUE_BATCH = 64;", "constexpr size_t LAYOUT_LIMIT = 8;",
                  "AssertLockNotHeld(cs_main);", "AssertLockNotHeld(node.mempool.cs);",
                  "std::lock_guard<std::mutex> refresh(refresh_mutex);",
                  "std::unique_ptr<Catalogue> working;", "working = std::move(catalogue);",
                  "std::array<Key, CATALOGUE_BATCH> batch;", "SameSource(current, tip);",
                  "working->heights.reserve(size_t(height) + 1);", "catalogue = std::move(working);",
                  "retired = layouts.extract(key);", "working->directory.clear();",
                  "working->heights.clear();", "++stats.catalogue_aborts;",
                  "ChainActive().Height() == std::numeric_limits<int>::max()",
                  "old->second->GetAncestor(mid) == ChainActive()[mid]"):
        require(token in after, "catalogue ownership, batching, queue or abort contract drift")
    require(after.index("AssertLockNotHeld(cs_main);") < after.index("refresh(refresh_mutex)") and
            after.count("SameSource(current, tip);") == 2, "catalogue wait/final fence drift")
    capture = after[after.index("                {\n                    LOCK(cs_main);"):
                    after.index("                for (size_t i = 0; i < count; ++i) {\n                    const auto key")]
    require(all(token not in capture for token in ("reserve(", "push_back(", "emplace(", "clear(", "hook")),
            "catalogue allocates, destroys or calls hooks inside native batch")


def before_identity(root, path):
    return revision_identity(root, PARENT, path)


def successor_identity(root, path, previous):
    result = previous
    if (root / MANIFEST).exists():
        rows = [r for r in load_json(root / MANIFEST)["files"] if r["path"] == path]
        if rows:
            require(path in MANIFEST_FILES and len(rows) == 1 and
                    rows[0]["before"] == before_identity(root, path) and
                    previous in (rows[0]["before"], rows[0]["after"]), "catalogue successor preimage drift")
            result = rows[0]["after"]
    from check_chronik_multi_lokad import successor_identity as multi_successor
    return multi_successor(root, path, result)


def validate(root):
    root = root.resolve(strict=True)
    from check_chronik_multi_lokad import additional_paths, validate as validate_multi
    extra = additional_paths(root)
    if extra:
        validate_multi(root)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "catalogue parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and manifest["license"] == "MIT" and
            manifest["schema"] == "ergon-upstream-position-catalogue/v1" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and manifest["contract"] == CONTRACT and
            [r["path"] for r in manifest["files"]] == sorted(MANIFEST_FILES), "catalogue manifest drift")
    for row in manifest["files"]:
        before = before_identity(root, row["path"])
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["before"] == before and row["after"] != before and
                successor_identity(root, row["path"], row["after"]) == current_identity(root, row["path"]) and row["after"]["mode"] == "100644" and
                row["provenance"] == ("A1/MIT" if before is None else "PUBLIC+A1/MIT") and
                row["reachability"] == ("regtest-native-or-test-only" if row["path"] in TECHNICAL else "test-only"),
                "catalogue source identity drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == CHANGED | extra, "unexpected catalogue path")
    validate_reader_source(git(root, "show", PARENT + ":" + READER).decode(), (root / READER).read_text())
    validate_runner_protocol(git(root, "show", PARENT + ":" + RUNNER).decode(), (root / RUNNER).read_text())
    before, after = (native_cases(text) for text in
        (git(root, "show", PARENT + ":" + NATIVE).decode(), (root / NATIVE).read_text()))
    require(len(before) == 8 and set(after) - set(before) == set(CASES) and
            all(after.get(name) == body for name, body in before.items()), "catalogue changed inherited native cases")
    # Exact changed-path equality also protects every untouched upstream engine,
    # native validation source, lock, service owner and daemon reproduction client.
    from check_change import validate as validate_change
    record = load_json(root / RECORD); validate_change(record, RECORD)
    require(record["change_id"] == "ERGON-CHANGE-0063" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question",
                                   "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(CHANGED - {RECORD}), "catalogue record drift")
    for row in record["files"]:
        normalize = lambda v: None if v is None else {("git_blob_sha1" if k == "git_blob" else k): i for k, i in v.items()}
        require(normalize(row["before"]) == before_identity(root, row["path"]) and
                successor_identity(root, row["path"], normalize(row["after"])) == current_identity(root, row["path"]), "catalogue record identity drift")
    return CHANGED | extra


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: native position catalogue captures at most 64 rows per lock hold; inherited engines and controls unchanged")

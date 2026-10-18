#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Finite successor gate for owned startup ancestor skip links."""
import argparse
import ast
import json
import re
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, load_json, require
from check_chronik_service_catchup import revision_identity

PARENT = "4e75154cdf29ac79897f9e21034de9f04da57372"
TREE = "20bc3aaae9991b87ae9c17ba6d45bcb6d00dd4e6"
MANIFEST = "docs/engineering/chronik/upstream-owned-skip-links-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0058.json"
TECHNICAL = frozenset({"chronik/chronik-cpp/chronik_bridge.cpp",
                       "src/test/chronik_upstream_archival_tests.cpp"})
MANIFEST_FILES = TECHNICAL | {
    ".github/workflows/chronik-upstream-link.yml",
    "docs/engineering/chronik/upstream-owned-skip-links.md",
    "tools/engineering/check_chronik_owned_skip_links.py",
    "tools/engineering/check_chronik_owned_skip_links_self_test.py",
    "tools/engineering/check_chronik_startup_metadata.py",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
}
CHANGED = MANIFEST_FILES | {MANIFEST, RECORD}
TARGET_CASE = "startup_owned_graph_and_source_storage_fences"
CONTRACT = {
    "compile_default": False,
    "runtime_default": False,
    "profile": "regtest-plugins",
    "network": "bounded-unpruned-regtest",
    "maximum_height": 2000,
    "maximum_indexes": 4096,
    "maximum_mempool": 4096,
    "skip_link_scope": "all-owned-active-and-side-branch-indexes",
    "construction_order": "all-owned-parents-linked-then-height-sorted-BuildSkip-before-publication",
    "owner_lifetime": "unchanged-retained-until-bridge-destruction",
    "owned_startup_cases": 3,
    "owned_startup_assertions": 593,
    "per_process_timeout_seconds": 60,
    "automatic_retry": False,
    "upstream_engine_change": False,
    "dependency_change": False,
    "consensus_change": False,
    "limit_change": False,
    "incoming_evidence": [],
    "knowledge_status": "Open Question",
    "evidence_ceiling": "record-review-only",
}


def before_identity(root, path):
    return revision_identity(root, PARENT, path)


def successor_identity(root, path, previous):
    result = previous
    if (root / MANIFEST).exists():
        rows = [row for row in load_json(root / MANIFEST)["files"]
                if row["path"] == path]
        if rows:
            require(path in MANIFEST_FILES and len(rows) == 1,
                    "owned skip-link successor path drift")
            if result != rows[0]["after"]:
                require(rows[0]["before"] == result,
                        "owned skip-link successor preimage drift")
                result = rows[0]["after"]
    if (root / "docs/engineering/chronik/upstream-startup-release-v1.json").exists():
        from check_chronik_startup_release import successor_identity as release_successor
        result = release_successor(root, path, result)
    return result


def native_cases(text):
    matches = list(re.finditer(
        r"^BOOST_AUTO_TEST_CASE\(\s*(\w+)\s*\) \{\n.*?^\}", text,
        re.M | re.S))
    rows = {match[1]: match[0] for match in matches}
    require(len(rows) == len(matches), "ambiguous owned skip-link case")
    return rows


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"",
            "owned skip-link parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent",
                              "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and
            manifest["schema"] == "ergon-upstream-owned-skip-links/v1" and
            manifest["license"] == "MIT" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT and
            [row["path"] for row in manifest["files"]] == sorted(MANIFEST_FILES),
            "owned skip-link manifest drift")
    for row in manifest["files"]:
        before = before_identity(root, row["path"])
        require(set(row) == {"path", "before", "after", "provenance",
                             "reachability"} and
                row["before"] == before and
                successor_identity(root, row["path"], row["after"]) ==
                    current_identity(root, row["path"]) and
                row["after"] != before and row["after"]["mode"] == "100644" and
                row["provenance"] == ("A1/MIT" if before is None else "PUBLIC+A1/MIT") and
                row["reachability"] ==
                    ("bounded-regtest-native-or-test-only"
                     if row["path"] in TECHNICAL else "test-only"),
                "owned skip-link source drift")
    changed = {path.decode() for path in
               git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if path}
    changed |= {path.decode() for path in
                git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
                if path}
    expected_changed = CHANGED
    if (root / "docs/engineering/chronik/upstream-startup-release-v1.json").exists():
        from check_chronik_startup_release import CHANGED as release_changed
        expected_changed |= release_changed
    if (root / "docs/engineering/chronik/upstream-startup-batches-v1.json").exists():
        from check_chronik_startup_batches import CHANGED as batches_changed
        expected_changed |= batches_changed
    if (root / "docs/engineering/chronik/upstream-native-startup-v1.json").exists():
        from check_chronik_native_startup import CHANGED as native_changed
        expected_changed |= native_changed
    if (root / "docs/engineering/chronik/upstream-long-history-v1.json").exists():
        from check_chronik_long_history import CHANGED as history_changed
        expected_changed |= history_changed
    require(changed == expected_changed, "unexpected owned skip-link path")

    from check_change import ValidationError, validate as validate_change
    record = load_json(root / RECORD)
    try:
        validate_change(record, RECORD)
    except ValidationError as error:
        raise ValueError("owned skip-link governance schema drift") from error
    require(record["change_id"] == "ERGON-CHANGE-0058" and
            record["public_lineage"]["integration_parent"] ==
                {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active",
                                   "knowledge_status": "Open Question",
                                   "ceiling": "record-review-only",
                                   "artifacts": []} and
            [row["path"] for row in record["files"]] == sorted(CHANGED - {RECORD}),
            "owned skip-link governance drift")
    def governed(value):
        return None if value is None else {
            ("git_blob_sha1" if key == "git_blob" else key): item
            for key, item in value.items()}
    for row in record["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                successor_identity(root, row["path"], governed(row["after"])) ==
                    current_identity(root, row["path"]),
                "owned skip-link policy drift")

    old_cases = native_cases(git(root, "show", PARENT +
        ":src/test/chronik_upstream_archival_tests.cpp").decode())
    new_cases = native_cases((root / "src/test/chronik_upstream_archival_tests.cpp").read_text())
    release_case = "startup_finish_releases_owned_graph_before_publication"
    expected_additions = ({release_case}
        if (root / "docs/engineering/chronik/upstream-startup-release-v1.json").exists()
        else set())
    if (root / "docs/engineering/chronik/upstream-startup-batches-v1.json").exists():
        from check_chronik_startup_batches import CASES
        expected_additions |= set(CASES)
    if (root / "docs/engineering/chronik/upstream-native-startup-v1.json").exists():
        from check_chronik_native_startup import CASES as native_cases_added
        expected_additions |= set(native_cases_added)
    if (root / "docs/engineering/chronik/upstream-long-history-v1.json").exists():
        from check_chronik_long_history import CASES as history_cases
        expected_additions |= set(history_cases)
    require(len(old_cases) == 56 and
            set(new_cases) - set(old_cases) == expected_additions and
            set(old_cases) - set(new_cases) == set() and
            old_cases[TARGET_CASE] != new_cases[TARGET_CASE] and
            all(new_cases[name] == body for name, body in old_cases.items()
                if name != TARGET_CASE),
            "owned skip-link changed unrelated native case")
    target = new_cases[TARGET_CASE]
    require("copy.pskip != ChainActive()[h]->pskip" in target and
            "copy.pskip->GetBlockHash() ==" in target and
            "lookup_block_index(" in target and "copy.pskip->nHeight < h" in target,
            "owned skip-link pointer/hash/height oracle drift")

    bridge = (root / "chronik/chronik-cpp/chronik_bridge.cpp").read_text()
    begin = bridge.index("void ChronikBridge::capture_startup_snapshot")
    end = bridge.index("bool ChronikBridge::finish_startup_snapshot", begin)
    capture = bridge[begin:end]
    require(capture.index("copy->pprev = parent->second.get()") <
            capture.index("owned->chain.SetTip") <
            capture.index("std::sort(by_height.begin()") <
            capture.index("index->BuildSkip()") <
            capture.index("m_startup = std::move(owned)") and
            "by_height.reserve(owned->indexes.size())" in capture,
            "owned skip-link construction/publication ordering drift")
    finish = bridge[end:bridge.index("NativePositionStats", end)]
    if (root / "docs/engineering/chronik/upstream-startup-release-v1.json").exists():
        require("m_startup.reset()" in finish and
                "m_startup_active = false" in finish,
                "owned startup successor release drift")
    else:
        require("Keep the graph alive until bridge destruction" in finish and
                "m_startup_active = false" in finish,
                "owned startup lifetime changed in skip-link lot")
    for path in ("chronik/chronik-indexer/src/indexer.rs",
                 "chronik/chronik-lib/src/ergon_archival.rs",
                 "chronik/Cargo.lock", "src/chain.cpp", "src/chain.h",
                 "src/validation.cpp", "src/txmempool.cpp"):
        require(current_identity(root, path) == successor_identity(root, path, before_identity(root, path)),
                "owned skip-link changed frozen engine/consensus/runner input")
    return CHANGED


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path,
                        default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: owned startup skip links preserve exact ancestry without native borrows")

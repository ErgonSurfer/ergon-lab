#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Finite successor gate for releasing the owned startup graph."""
import argparse
import json
import re
from pathlib import Path
from check_chronik_upstream_link import current_identity, git, load_json, require
from check_chronik_service_catchup import revision_identity

PARENT = "d68d62572760f73f0c9959f81961821d3936f94e"
TREE = "62e4905e05cb4f7a6e15b8eef1e6de3634150735"
MANIFEST = "docs/engineering/chronik/upstream-startup-release-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0059.json"
TECHNICAL = frozenset({
    "chronik/chronik-cpp/chronik_bridge.cpp",
    "chronik/chronik-cpp/chronik_bridge.h",
    "src/test/chronik_upstream_archival_tests.cpp",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/self_test.py",
})
MANIFEST_FILES = TECHNICAL | {
    ".github/workflows/chronik-upstream-link.yml",
    "docs/engineering/chronik/upstream-startup-release.md",
    "tools/engineering/check_chronik_owned_skip_links.py",
    "tools/engineering/check_chronik_owned_skip_links_self_test.py",
    "tools/engineering/check_chronik_startup_metadata.py",
    "tools/engineering/check_chronik_startup_release.py",
    "tools/engineering/check_chronik_startup_release_self_test.py",
}
CHANGED = MANIFEST_FILES | {MANIFEST, RECORD}
TARGET_CASE = "startup_finish_releases_owned_graph_before_publication"
CONTRACT = {
    "compile_default": False,
    "runtime_default": False,
    "profile": "regtest-plugins",
    "network": "bounded-unpruned-regtest",
    "maximum_height": 2000,
    "maximum_indexes": 4096,
    "maximum_mempool": 4096,
    "publication_boundary": "successful-finish-before-bridge-publication",
    "release_order": "validate-under-cs-main-and-mempool-then-reset-outside-both-locks",
    "failure_lifetime": "retain-on-cancellation-or-exception-until-retry-or-destruction",
    "measurement": "native-only-index-count-and-portable-byte-floor-not-rss",
    "owned_startup_cases": 4,
    "owned_startup_assertions": 607,
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
    if not (root / MANIFEST).exists():
        return previous
    rows = [row for row in load_json(root / MANIFEST)["files"]
            if row["path"] == path]
    result = previous
    if rows:
        require(path in MANIFEST_FILES and len(rows) == 1,
                "startup release successor path drift")
        if result != rows[0]["after"]:
            require(rows[0]["before"] == result,
                    "startup release successor preimage drift")
            result = rows[0]["after"]
    if (root / "docs/engineering/chronik/upstream-startup-batches-v1.json").exists():
        from check_chronik_startup_batches import successor_identity as batches_successor
        result = batches_successor(root, path, result)
    return result


def native_cases(text):
    matches = list(re.finditer(
        r"^BOOST_AUTO_TEST_CASE\(\s*(\w+)\s*\) \{\n.*?^\}", text,
        re.M | re.S))
    rows = {match[1]: match[0] for match in matches}
    require(len(rows) == len(matches), "ambiguous startup release case")
    return rows


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"",
            "startup release parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent",
                              "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and
            manifest["schema"] == "ergon-upstream-startup-release/v1" and
            manifest["license"] == "MIT" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT and
            [row["path"] for row in manifest["files"]] == sorted(MANIFEST_FILES),
            "startup release manifest drift")
    for row in manifest["files"]:
        before = before_identity(root, row["path"])
        reachability = ("bounded-regtest-native-or-test-only"
                        if row["path"] in TECHNICAL else "test-only")
        require(set(row) == {"path", "before", "after", "provenance",
                             "reachability"} and
                row["before"] == before and
                successor_identity(root, row["path"], row["after"]) ==
                    current_identity(root, row["path"]) and
                row["after"] != before and row["after"]["mode"] == "100644" and
                row["provenance"] ==
                    ("A1/MIT" if before is None else "PUBLIC+A1/MIT") and
                row["reachability"] == reachability,
                "startup release source drift")
    changed = {path.decode() for path in
               git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if path}
    changed |= {path.decode() for path in
                git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
                if path}
    expected_changed = CHANGED
    batches_present = (root / "docs/engineering/chronik/upstream-startup-batches-v1.json").exists()
    if batches_present:
        from check_chronik_startup_batches import CHANGED as batches_changed
        expected_changed |= batches_changed
    native_present = (root / "docs/engineering/chronik/upstream-native-startup-v1.json").exists()
    if native_present:
        from check_chronik_native_startup import CHANGED as native_changed
        expected_changed |= native_changed
    history_present = (root / "docs/engineering/chronik/upstream-long-history-v1.json").exists()
    if history_present:
        from check_chronik_long_history import CHANGED as history_changed
        expected_changed |= history_changed
    if (root / "docs/engineering/chronik/upstream-position-catalogue-v1.json").exists():
        from check_chronik_position_catalogue import CHANGED as catalogue_changed
        expected_changed |= catalogue_changed
    from check_chronik_multi_lokad import additional_paths
    expected_changed |= additional_paths(root)
    require(changed == expected_changed, "unexpected startup release path")

    from check_change import ValidationError, validate as validate_change
    record = load_json(root / RECORD)
    try:
        validate_change(record, RECORD)
    except ValidationError as error:
        raise ValueError("startup release governance schema drift") from error
    require(record["change_id"] == "ERGON-CHANGE-0059" and
            record["public_lineage"]["integration_parent"] ==
                {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active",
                                   "knowledge_status": "Open Question",
                                   "ceiling": "record-review-only",
                                   "artifacts": []} and
            [row["path"] for row in record["files"]] == sorted(CHANGED - {RECORD}),
            "startup release governance drift")
    def governed(value):
        return None if value is None else {
            ("git_blob_sha1" if key == "git_blob" else key): item
            for key, item in value.items()}
    for row in record["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                successor_identity(root, row["path"], governed(row["after"])) ==
                    current_identity(root, row["path"]),
                "startup release policy drift")

    old_cases = native_cases(git(root, "show", PARENT +
        ":src/test/chronik_upstream_archival_tests.cpp").decode())
    new_cases = native_cases((root / "src/test/chronik_upstream_archival_tests.cpp").read_text())
    from check_chronik_startup_batches import CASES as batches_cases
    native_additions = set()
    if native_present:
        from check_chronik_native_startup import CASES as native_cases_added
        native_additions = set(native_cases_added)
    if history_present:
        from check_chronik_long_history import CASES as history_cases
        native_additions |= set(history_cases)
    require(len(old_cases) == 56 and
            set(new_cases) - set(old_cases) == {TARGET_CASE} |
                (set(batches_cases) if batches_present else set()) | native_additions and
            set(old_cases) - set(new_cases) == set() and
            all(new_cases[name] == body for name, body in old_cases.items()),
            "startup release changed inherited native case")
    target = new_cases[TARGET_CASE]
    for token in ("startup_snapshot_index_count_for_testing()",
                  "startup_snapshot_retained_bytes_floor_for_testing()",
                  "finish_startup_bridge(*bridge)",
                  "finish_service_startup_bridge(*service)",
                  "&bridge->get_chain_tip() == native_tip",
                  "&service->get_chain_tip() == native_tip"):
        require(token in target, "startup release oracle drift")

    bridge = (root / "chronik/chronik-cpp/chronik_bridge.cpp").read_text()
    begin = bridge.index("bool ChronikBridge::finish_startup_snapshot")
    end = bridge.index("NativePositionStats", begin)
    finish = bridge[begin:end]
    lock = finish.index("LOCK2(cs_main, m_node.mempool.cs)")
    inactive = finish.index("m_startup_active = false", lock)
    unlock = finish.index("\n    }\n    // Startup is synchronous", inactive)
    release = finish.index("m_startup.reset()", unlock)
    require(lock < inactive < unlock < release and
            finish.count("m_startup.reset()") == 1 and
            finish.index("return false", lock) < release and
            finish.index("throw std::runtime_error", lock) < release,
            "startup release fence/lock/failure ordering drift")
    require("std::map node and allocator overhead" in bridge and
            "not an RSS measurement" in bridge and
            "sizeof(StartupSnapshot)" in bridge and
            "sizeof(CBlockIndex)" in bridge,
            "startup retained-byte floor drift")
    header = (root / "chronik/chronik-cpp/chronik_bridge.h").read_text()
    require("mutable std::unique_ptr<StartupSnapshot> m_startup" in header and
            "startup_snapshot_index_count_for_testing() const" in header and
            "startup_snapshot_retained_bytes_floor_for_testing() const" in header,
            "startup release native-only surface drift")
    runner = (root / "tests/compatibility/upstream-chronik/run_repetitions.py").read_text()
    runner_self = (root / "tests/compatibility/upstream-chronik/self_test.py").read_text()
    require(('"' + TARGET_CASE + '"') in runner and
            '("owned-startup", STARTUP, 607)' in runner and
            ('== ' + ('58' if history_present else '56' if native_present else '50' if batches_present else '45') +
             ', "non-disjoint test groups"') in runner and
            "assertions=607" in runner_self and
            "runner.STARTUP, 607" in runner_self,
            "startup release runner binding drift")
    for path in ("chronik/chronik-indexer/src/indexer.rs",
                 "chronik/chronik-lib/src/ergon_archival.rs",
                 "chronik/chronik-plugin-impl/src/context.rs",
                 "chronik/Cargo.lock", "src/chain.cpp", "src/chain.h",
                 "src/validation.cpp", "src/txmempool.cpp"):
        require(current_identity(root, path) == successor_identity(root, path, before_identity(root, path)),
                "startup release changed frozen engine/consensus/dependency input")
    return CHANGED


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path,
                        default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: owned startup graph released outside native locks before publication")

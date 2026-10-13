#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exact additive read-session layer over the public PR #51 source checkpoint.

This does not replace or rewrite the original upstream/donor adaptation record.
The parent is a public review branch, NOT an assertion that main accepted it.
"""
import argparse
from pathlib import Path
from check_chronik_upstream_link import (
    current_identity, git, identity, load_json, require,
)

PARENT = "8fc7aec5a3935533fe7d4e0ccbfdf14b276abc37"
PARENT_TREE = "0a97f74862c1f7ff1842ce60aa7969837b31a887"
MANIFEST = "docs/engineering/chronik/owned-chainstate-reads-v1.json"
TECHNICAL = frozenset({
    "chronik/UpstreamLink.cmake",
    "chronik/chronik-cpp/ergon_archival_probe.cpp",
    "chronik/chronik-cpp/ergon_archival_probe.h",
    "chronik/chronik-lib/src/ergon_archival.rs",
    "src/coins.cpp", "src/coins.h", "src/dbwrapper.cpp", "src/dbwrapper.h",
    "src/txdb.cpp", "src/txdb.h", "src/test/chronik_upstream_archival_tests.cpp",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/self_test.py",
})
POLICY = frozenset({
    MANIFEST, "docs/engineering/chronik/owned-chainstate-reads.md",
    "docs/engineering/changes/ergon-change-0046.json",
    "tools/engineering/check_chronik_chainstate_reads.py",
    "tools/engineering/check_chronik_chainstate_reads_self_test.py",
    "tools/engineering/check_chronik_upstream_link.py",
    ".github/workflows/chronik-upstream-link.yml",
})
CONTRACT = {
    "cold_coin_io": "outside-both-native-locks",
    "source": "owned-LevelDB-snapshot-plus-copied-native-cache-overlay",
    "spent_overlay": "never-fall-back-to-database",
    "fences": "chain-generation,pool-generation,cache-owner,database-owner",
    "shutdown": "positive-reader-join-before-native-storage-reopen",
    "daemon_admission": False,
    "consensus_change": False,
    "dependency_change": False,
    "incoming_evidence": [],
    "knowledge_status": "Open Question",
    "evidence_ceiling": "record-review-only",
}


def before_identity(root, path):
    mode = git(root, "ls-tree", PARENT, "--", path).decode().split()[0]
    return identity(git(root, "show", PARENT + ":" + path), mode)


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == PARENT_TREE,
            "read-session public parent identity mismatch")
    require(git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"",
            "read-session parent is not an ancestor")
    record = load_json(root / MANIFEST)
    require(set(record) == {"$comment", "schema", "public_parent", "parent_status",
                            "license", "contract", "files"}, "unexpected read-session fields")
    require(record["$comment"] == "SPDX-License-Identifier: MIT" and
            record["schema"] == "ergon-owned-chainstate-reads/v1" and record["license"] == "MIT",
            "read-session schema/license mismatch")
    require(record["public_parent"] == {"commit": PARENT, "tree": PARENT_TREE} and
            record["parent_status"] == "public-review-branch-not-assumed-accepted",
            "read-session parent/status mismatch")
    require(record["contract"] == CONTRACT, "read-session contract/proof boundary mismatch")
    rows = record["files"]
    require([r["path"] for r in rows] == sorted(TECHNICAL), "read-session allowlist mismatch")
    for row in rows:
        require(set(row) == {"path", "before", "after", "provenance", "reachability"},
                "unexpected read-session inventory fields")
        require(row["provenance"] == "PUBLIC+A1/MIT" and
                row["reachability"] == ("native-storage-api-always-built" if row["path"] in {
                    "src/coins.cpp", "src/coins.h", "src/dbwrapper.cpp", "src/dbwrapper.h",
                    "src/txdb.cpp", "src/txdb.h"} else "opt-in-build-or-test-only"),
                "read-session provenance/reachability mismatch")
        require(row["before"] == before_identity(root, row["path"]), "read-session preimage drift")
        require(row["after"] == current_identity(root, row["path"]), "read-session postimage drift")
        require(row["before"] != row["after"] and row["after"]["mode"] == "100644",
                "read-session redundant delta/mode mismatch")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == TECHNICAL | POLICY, "read-session changed paths differ from exact layer")
    for path in changed:
        require(current_identity(root, path)["mode"] == "100644", "read-session nonregular/executable path")
    # Bind policy/docs/CI bytes as well as executable inputs. The governance
    # record alone is excluded from its own inventory (no circular digest).
    record_path = "docs/engineering/changes/ergon-change-0046.json"
    change = load_json(root / record_path)
    require(change["change_id"] == "ERGON-CHANGE-0046" and
            [f["path"] for f in change["files"]] == sorted(changed - {record_path}),
            "read-session governance inventory mismatch")
    def change_identity(value):
        return None if value is None else {
            ("git_blob_sha1" if k == "git_blob" else k): v for k, v in value.items()}
    for row in change["files"]:
        path = row["path"]
        parent = before_identity(root, path) if git(root, "ls-tree", PARENT, "--", path).strip() else None
        require(change_identity(row["before"]) == parent and
                change_identity(row["after"]) == current_identity(root, path),
                "read-session governance pre/postimage drift")
    for frozen in (
        "docs/engineering/chronik/upstream-host-adaptation-v1.json",
        "docs/engineering/chronik/chronik-port-lot-a-donor-v1.json",
        "docs/engineering/changes/ergon-change-0045.json",
        "chronik/Cargo.lock", "chronik/Cargo.toml", "CMakeLists.txt",
        "chronik/chronik-cpp/ergon_node_access.h",
    ):
        require(current_identity(root, frozen) == before_identity(root, frozen),
                "read-session changed a frozen dependency/parent/default boundary")
    return TECHNICAL | POLICY


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    validate(args.repository_root)
    print("Owned chainstate read layer: exact 13 technical + 7 policy paths; no activation or evidence promotion.")


if __name__ == "__main__":
    main()

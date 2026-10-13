# SPDX-License-Identifier: MIT
"""Finite public synthetic-plugin layer; never daemon activation or evidence."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import tomllib
from check_chronik_upstream_link import current_identity, git, identity, load_json, require

PARENT = "eaa73b03ed060af5623eb30ff4a08502d2a73c7a"
TREE = "85ca3e2bd4a0d5017cbad506aa26539f99e38d2b"
MANIFEST = "docs/engineering/chronik/upstream-plugin-fixtures-v1.json"
TECHNICAL = frozenset({
    "CMakeLists.txt", "chronik/Cargo.lock", "chronik/UpstreamLink.cmake",
    "chronik/chronik-db/src/mem/mempool.rs",
    "chronik/chronik-cpp/ergon_archival_worker.cpp", "chronik/chronik-cpp/ergon_archival_worker.h",
    "chronik/chronik-lib/Cargo.toml", "chronik/chronik-lib/build.rs",
    "chronik/chronik-lib/src/ergon_archival.rs", "chronik/chronik-lib/src/ergon_plugin_fixture.rs",
    "chronik/chronik-lib/src/ergon_service.rs", "chronik/chronik-lib/src/lib.rs",
    "chronik/chronik-plugin-impl/src/module.rs",
    "chronik/chronik-plugin-impl/python/upstream/script.py",
    "chronik/chronik-plugin-impl/python/upstream/slp.py",
    "src/test/chronik_upstream_archival_tests.cpp",
    "tests/compatibility/upstream-chronik/plugins/ergon_public_probe.py",
    "tests/compatibility/upstream-chronik/plugins/self_test.py",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/self_test.py",
})
POLICY = frozenset({MANIFEST, "docs/engineering/chronik/upstream-plugin-helpers-v1.json",
    "tools/engineering/check_chronik_plugin_fixtures.py",
    "tools/engineering/check_chronik_plugin_fixtures_self_test.py",
    "tools/engineering/check_chronik_upstream_link.py",
    "tools/engineering/check_chronik_chainstate_reads.py",
    "tools/engineering/check_chronik_chainstate_reads_self_test.py",
    ".github/workflows/chronik-upstream-link.yml",
    "docs/engineering/chronik/upstream-plugin-fixtures.md",
    "docs/engineering/changes/ergon-change-0047.json"})
HELPERS = {"script.py": "89d41426d7466222a5eeded22e5caf980366f198a4909ea0b47e7f0bea24eafd",
           "slp.py": "0452a00bfec362c15d1ae663fcedd38adfd63d73c9eb559f115feebd5b1ad5c3"}
CONTRACT = {"daemon_admission": False, "consensus_change": False,
    "registry_tuple_change": False, "incoming_evidence": [],
    "plugin_engine": "existing-upstream", "plugins": "public-synthetic-fixture-only",
    "python": "configured-exact-CPython-3.12-embedding", "context": "process-once-owned-shared",
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}


def before_identity(root, path):
    entry = git(root, "ls-tree", PARENT, "--", path).decode().strip()
    return identity(git(root, "show", PARENT + ":" + path), entry.split()[0]) if entry else None


def check_helpers(root):
    manifest = load_json(root / "docs/engineering/chronik/upstream-plugin-helpers-v1.json")
    require(manifest["license"] == "MIT" and manifest["knowledge_status"] == "Open Question"
            and manifest["evidence_ceiling"] == "record-review-only" and manifest["evidence_inputs"] == [],
            "helper license/proof boundary drift")
    require(manifest["schema"] == "ergon-upstream-plugin-helpers/v1"
            and manifest["public_parent"] == {"commit": PARENT, "tree": TREE}
            and manifest["donor"] == {"repository": "https://github.com/Bitcoin-ABC/bitcoin-abc.git",
                "commit": "784d83de2e19eab726898d77bfe7465410d27a9c",
                "tree": "763739eb3c255e90a6122a2679c667089f9efad2",
                "authority": "technical-donor-only", "license": "MIT"}
            and manifest["boundaries"] == {"consensus_change": False, "daemon_admission": False,
                "native_functional_helpers_modified": False, "plugin_algorithms_replaced": False,
                "operator_plugins": False}
            and len(manifest["files"]) == 2, "public helper donor/inventory drift")
    require([r["path"] for r in manifest["files"]] == [
        "chronik/chronik-plugin-impl/python/upstream/" + name for name in sorted(HELPERS)],
        "public helper path/selection drift")
    for row in manifest["files"]:
        path = Path(row["path"])
        require(path.as_posix() == "chronik/chronik-plugin-impl/python/upstream/" + path.name
                and path.name in HELPERS, "unexpected helper destination")
        observed = current_identity(root, row["path"])
        require(observed == {"mode": row["mode"], "bytes": row["bytes"],
            "git_blob_sha1": row["git_blob"], "sha256": row["sha256"]}
            and observed["sha256"] == HELPERS[path.name], "helper byte/mode identity drift")
        raw = (root / path).read_bytes()
        require(b"MIT software license" in raw[:250], "upstream notice missing")
        ast.parse(raw)
    path = "chronik/chronik-plugin-impl/src/module.rs"
    expected = git(root, "show", PARENT + ":" + path).decode().replace(
        "../../../test/functional/test_framework/script.py", "../python/upstream/script.py").replace(
        "../../../test/functional/test_framework/chronik/slp.py", "../python/upstream/slp.py")
    require((root / path).read_text() == expected, "plugin engine delta exceeds exact include-path adaptation")


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE,
            "public plugin parent tree drift")
    require(git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "parent not an ancestor")
    record = load_json(root / MANIFEST)
    require(set(record) == {"$comment", "schema", "license", "public_parent", "parent_status", "contract", "files"}
            and record["$comment"] == "SPDX-License-Identifier: MIT"
            and record["schema"] == "ergon-upstream-plugin-fixtures/v1" and record["license"] == "MIT"
            and record["public_parent"] == {"commit": PARENT, "tree": TREE}
            and record["parent_status"] == "public-review-branch-not-assumed-main"
            and record["contract"] == CONTRACT, "plugin boundary/schema drift")
    require([r["path"] for r in record["files"]] == sorted(TECHNICAL), "plugin finite inventory drift")
    for row in record["files"]:
        require(set(row) == {"path", "before", "after", "provenance", "reachability"}
                and row["provenance"] == ("ABC784D-exact/MIT" if row["path"].startswith(
                    "chronik/chronik-plugin-impl/python/upstream/") else "PUBLIC+A1/MIT")
                and row["reachability"] == "opt-in-build-or-test-only", "plugin provenance/row drift")
        require(row["before"] == before_identity(root, row["path"])
                and row["after"] == current_identity(root, row["path"])
                and row["after"]["mode"] == "100644", "plugin pre/postimage drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == TECHNICAL | POLICY, "unexpected plugin implementation/policy path")
    for path in changed:
        require(current_identity(root, path)["mode"] == "100644", "nonregular or executable plugin path")
    change_path = "docs/engineering/changes/ergon-change-0047.json"
    change = load_json(root / change_path)
    require(change["change_id"] == "ERGON-CHANGE-0047" and
            change["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            change["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question",
                                   "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in change["files"]] == sorted(changed - {change_path}),
            "plugin governance parent/inventory/proof drift")
    def governed(value):
        return None if value is None else {
            ("git_blob_sha1" if k == "git_blob" else k): v for k, v in value.items()}
    for row in change["files"]:
        require(governed(row["before"]) == before_identity(root, row["path"]) and
                governed(row["after"]) == current_identity(root, row["path"]),
                "plugin governance pre/postimage drift")
    for path in ("src/coins.cpp", "src/coins.h", "src/dbwrapper.cpp", "src/dbwrapper.h",
                 "src/txdb.cpp", "src/txdb.h", "src/chain.cpp", "src/chain.h", "src/txmempool.cpp",
                 "src/txmempool.h", "test/functional/test_framework/script.py",
                 "chronik/chronik-cpp/ergon_node_access.h",
                 "docs/engineering/changes/ergon-change-0045.json",
                 "docs/engineering/changes/ergon-change-0046.json",
                 "docs/engineering/chronik/owned-chainstate-reads-v1.json",
                 "docs/engineering/chronik/upstream-host-adaptation-v1.json",
                 "docs/engineering/chronik/chronik-port-lot-a-donor-v1.json"):
        require(current_identity(root, path) == before_identity(root, path), "plugin changed frozen native/history boundary")
    before = tomllib.loads(git(root, "show", PARENT + ":chronik/Cargo.lock").decode())["package"]
    after = tomllib.loads((root / "chronik/Cargo.lock").read_text())["package"]
    require([p for p in before if "source" in p] == [p for p in after if "source" in p],
            "plugin changed registry version/source/checksum closure")
    for old, new in zip(before, after):
        expected = dict(old)
        if old["name"] == "chronik_lib": expected["dependencies"] = sorted(old["dependencies"] + ["pyo3"])
        require(expected == new, "plugin lock delta exceeds local existing-pyo3 edge")
    require(len(before) == len(after), "plugin added a package")
    require('"Enable public synthetic upstream plugin fixtures (no daemon admission)" OFF' in
            (root / "CMakeLists.txt").read_text(), "plugin default is not OFF")
    check_helpers(root)
    return TECHNICAL | POLICY


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    validate(args.repository_root)
    print("PASS: finite public plugin fixture layer, exact helpers and registry closure; daemon admission closed")

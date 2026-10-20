#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exact continuous-service successor; historical checks retain their public inputs.

Old change validators describe immutable predecessor boundaries. They are rerun
on the exact accepted public parent, not weakened to accept arbitrary successors.
This gate separately binds every current changed byte and freezes the rest of
that parent. Runtime tests continue to execute against the current source/build.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess
import tempfile

PARENT = "f11d867dd622a9c7a13a98736a6ff5296e555d07"
TREE = "3d59994e7f9d692f655de733558004128f08b774"
MANIFEST = "docs/engineering/chronik/upstream-continuous-service-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0066.json"
TECHNICAL = frozenset({
    "CMakeLists.txt", "chronik/UpstreamLink.cmake",
    *("chronik/chronik-cpp/" + name for name in (
        "chronik_bridge.cpp", "ergon_archival_probe.cpp", "ergon_archival_probe.h",
        "ergon_archival_worker.cpp", "ergon_archival_worker.h", "ergon_journal_replay.cpp",
        "ergon_journal_replay.h", "ergon_node_access.h", "ergon_regtest_service.cpp", "ergon_regtest_service.h")),
    "chronik/chronik-http/src/ws.rs", "chronik/chronik-indexer/src/subs.rs",
    *("chronik/chronik-lib/src/" + name for name in (
        "ergon_archival.rs", "ergon_operator_bundle.rs", "ergon_plugin_fixture.rs",
        "ergon_service.rs", "ergon_submission.rs", "lib.rs")),
    "src/init.cpp", "src/validation.cpp", "src/validation_event_journal.h",
    "src/validationinterface.cpp", "src/validationinterface.h",
    "src/test/chronik_upstream_regtest_tests.cpp", "src/test/validation_tests.cpp",
    "src/test/main.cpp", "src/test/chronik_upstream_archival_tests.cpp",
    *("tests/compatibility/upstream-chronik/" + name for name in (
        "historical_client_service.py", "historical_shadow_service.py",
        "operator_bundle_binding.py", "operator_bundle_binding_self_test.py"))})
DRIVERS = frozenset("tests/compatibility/upstream-chronik/" + name for name in (
    "regtest_service.py", "alp_service.py", "plugin_service.py", "plugin_alp_service.py",
    "catchup_service.py", "cold_rebuild_service.py", "long_history_service.py",
    "startup_metadata.py", "plugin_restart.py", "multi_lokad_component.py"))
POLICY = DRIVERS | frozenset({
    ".github/workflows/chronik-upstream-link.yml", ".github/workflows/chronik-port-core.yml",
    "tools/engineering/check_chronik_continuous_service.py",
    "tools/engineering/check_chronik_continuous_service_self_test.py",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/self_test.py",
    "tests/compatibility/upstream-chronik/cold_rebuild_service_self_test.py",
    "tests/compatibility/upstream-chronik/deployment_service.py",
    "tests/compatibility/upstream-chronik/deployment_service_self_test.py",
    "tests/community-plugins/component.py", "tests/community-plugins/component_self_test.py",
    "tests/community-plugins/service.py",
    "tools/engineering/check_change.py",
    "docs/engineering/schemas/change-evidence.schema.json",
    "docs/engineering/chronik/upstream-continuous-service.md"})
FILES = TECHNICAL | POLICY
CHANGED = FILES | {MANIFEST, RECORD}
CONTRACT = {"compile_default": False, "runtime_default": False,
    "native_consensus_change": False, "activation_change": False,
    "networks": ["main", "regtest"], "testnet_admission": False,
    "native_authority": "Bitcoin-Static-legacy-unchanged",
    "indexer": "existing-upstream-writers-token-engines-plugins-database-router",
    "admission": "explicit-network-genesis-and-trusted-plugin-commitment",
    "listener": "loopback-only", "index_adoption_or_wipe": False,
    "journal_events": 128, "journal_maximum_bytes": 67108864,
    "submission_batch_maximum": 16, "skip_token_checks": False,
    "native_fee_policy": "existing-absolute-maxTxFee-no-allowhighfees",
    "receipts": "indexed200-or-accepted-pending202-never-reclassify-acceptance",
    "WS": "existing-upstream-protobuf-bounded-close-on-gap-and-shutdown",
    "inherited_assertions_repetitions_and_deadlines": "unchanged",
    "Rust_registry_changes": False, "system_dependencies_locked": False,
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only",
    "incoming_evidence": [], "deployment_by_CI": False}

def require(ok, reason):
    if not ok:
        raise ValueError(reason)

def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])

def identity(raw, mode="100644"):
    return {"mode": mode, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "git_blob_sha1": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}

def current_identity(root, name):
    p = PurePosixPath(name)
    require(str(p) == name and not p.is_absolute() and ".." not in p.parts, "unsafe source path")
    path = root / name
    require(not any(p.is_symlink() for p in (path, *path.parents)), "source alias refused")
    meta = path.lstat()
    expected_mode = 0o755 if name == "tools/engineering/check_change.py" else 0o644
    require(stat.S_ISREG(meta.st_mode) and stat.S_IMODE(meta.st_mode) == expected_mode and meta.st_nlink == 1,
            "source type/mode/link refused")
    raw = path.read_bytes(); raw.decode("utf8", "strict")
    require(b"\0" not in raw, "binary source refused")
    return identity(raw, "100" + format(expected_mode, "03o"))

def before_identity(root, name):
    rows = git(root, "ls-tree", "-z", PARENT, "--", name).split(b"\0")
    rows = [row for row in rows if row]
    if not rows:
        return None
    require(len(rows) == 1, "ambiguous parent input")
    meta, actual = rows[0].split(b"\t", 1)
    mode, kind, blob = meta.decode().split()
    expected_mode = "100755" if name == "tools/engineering/check_change.py" else "100644"
    require(actual.decode() == name and kind == "blob" and mode == expected_mode, "parent type/mode drift")
    return identity(git(root, "cat-file", "blob", blob), mode)

def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE,
            "accepted parent tree drift")
    git(root, "merge-base", "--is-ancestor", PARENT, "HEAD")
    manifest = json.loads((root / MANIFEST).read_bytes())
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"}
            and manifest["$comment"] == "SPDX-License-Identifier: MIT"
            and manifest["schema"] == "ergon-upstream-continuous-service/v1"
            and manifest["license"] == "MIT"
            and manifest["public_parent"] == {"commit": PARENT, "tree": TREE}
            and manifest["contract"] == CONTRACT
            and [r["path"] for r in manifest["files"]] == sorted(FILES), "continuous manifest drift")
    for row in manifest["files"]:
        before = before_identity(root, row["path"])
        require(set(row) == {"path", "before", "after", "provenance", "reachability"}
                and row["before"] == before and row["after"] == current_identity(root, row["path"])
                and before != row["after"]
                and row["provenance"] == ("PUBLIC+A1/MIT" if before else "A1/MIT")
                and row["reachability"] == ("optional-service-or-native-observer-hook" if row["path"] in TECHNICAL
                    and not row["path"].startswith(("tests/", "src/test/")) else "test-build-policy-or-doc"),
                "continuous source/preimage/provenance drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == CHANGED, "unexpected continuous path; all other parent inputs frozen")
    from check_change import validate as validate_record, CONTINUOUS_BOUNDARIES
    record = json.loads((root / RECORD).read_bytes()); validate_record(record, RECORD)
    require(record["change_id"] == "ERGON-CHANGE-0066"
            and record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE}
            and record["boundaries"] == CONTINUOUS_BOUNDARIES
            and record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question",
                "ceiling": "record-review-only", "artifacts": []}
            and [r["path"] for r in record["files"]] == sorted(CHANGED - {RECORD}), "continuous record drift")
    for row in record["files"]:
        def normalized(value):
            return None if value is None else {("git_blob_sha1" if k == "git_blob" else k): v for k, v in value.items()}
        require(normalized(row["before"]) == before_identity(root, row["path"])
                and normalized(row["after"]) == current_identity(root, row["path"]), "continuous record identity drift")
    # The entire predecessor tree is frozen outside CHANGED, including the four
    # contributed modules, all old records/validators, Cargo lock and engines.
    return CHANGED

def historical_checks(root):
    """Rerun all inherited source checks on their exact accepted public tree."""
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE, "historical tree drift")
    old_workflow = git(root, "show", PARENT + ":.github/workflows/chronik-upstream-link.yml").decode()
    block = old_workflow.split("      - name: Validate exact current adaptations and historical donor preimages\n", 1)[1].split("      - name: Install native prerequisites\n", 1)[0]
    commands = [shlex.split(line.strip()) for line in block.splitlines() if line.strip().startswith("python3 -B ")]
    require(len(commands) == 58, "historical command selection drift")
    with tempfile.TemporaryDirectory(prefix="ergon-public-parent-") as temporary:
        source = Path(temporary) / "source"
        subprocess.run(["git", "clone", "--shared", "--no-checkout", "--", str(root), str(source)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", PARENT],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        require(git(source, "rev-parse", "HEAD^{tree}").decode().strip() == TREE
                and not git(source, "status", "--porcelain"), "historical materialization drift")
        for number, command in enumerate(commands, 1):
            result = subprocess.run(command, cwd=source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
            require(result.returncode == 0, "historical source check refused: " + " ".join(command))
            require(len(result.stdout) + len(result.stderr) <= 1024 * 1024, "historical diagnostic bound")
            print("PASS historical " + str(number) + "/58: " + " ".join(command), flush=True)
        require(not git(source, "diff", "--name-only"), "historical check changed tracked source")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "historical"))
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    validate(args.repository_root)
    if args.command == "historical":
        historical_checks(args.repository_root)
        validate(args.repository_root)
    print("PASS: exact continuous-service successor; no deployment or evidence promotion")

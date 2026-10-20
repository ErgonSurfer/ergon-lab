#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exact development-testnet successor, preserving accepted mainnet source and tests."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile

PARENT = "1029eb3295fa9ba986cac0a0020c74f74b9a6914"
TREE = "e03012dbae6d0fac5095f25d2102e6bb28a65dad"
MANIFEST = "docs/engineering/testnet/source-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0067.json"
RUNTIME_GATE_CLIENTS = (
    "alp_service.py", "catchup_service.py", "cold_rebuild_service.py",
    "deployment_service.py", "long_history_service.py", "multi_lokad_component.py",
    "plugin_alp_service.py", "plugin_restart.py", "plugin_service.py",
    "regtest_service.py", "startup_metadata.py",
)
COMMUNITY_GATE_CLIENTS = ("tests/community-plugins/component.py", "tests/community-plugins/service.py")
FILES = frozenset({
    ".github/workflows/chronik-upstream-link.yml", ".github/workflows/chronik-port-core.yml",
    "chronik/UpstreamLink.cmake", "chronik/chronik-cpp/ergon_node_access.h",
    "chronik/chronik-lib/src/ergon_archival.rs", "chronik/chronik-lib/src/ergon_operator_bundle.rs",
    "chronik/chronik-lib/src/ergon_plugin_fixture.rs",
    "src/Makefile.test.include", "src/chainparams.cpp", "src/chainparamsbase.cpp", "src/chainparamsbase.h",
    "src/consensus/params.h", "src/init.cpp", "src/pow.cpp", "src/util/system.cpp", "src/rpc/blockchain.cpp", "src/bitcoind.cpp",
    "src/test/CMakeLists.txt", "src/test/ergon_testnet_tests.cpp", "src/test/chronik_upstream_archival_tests.cpp",
    "tests/compatibility/upstream-chronik/operator_bundle_binding.py",
    "tests/compatibility/upstream-chronik/operator_bundle_binding_self_test.py",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    *("tests/compatibility/upstream-chronik/" + name for name in RUNTIME_GATE_CLIENTS),
    *COMMUNITY_GATE_CLIENTS,
    "tests/compatibility/upstream-chronik/cold_rebuild_service_self_test.py",
    "docs/engineering/schemas/change-evidence.schema.json", "tools/engineering/check_change.py",
    "tools/engineering/check_ergon_testnet.py", "tools/engineering/check_ergon_testnet_self_test.py",
    "contrib/testnet/ergon-testnet-v1.conf.example", "docs/engineering/testnet/ergon-testnet-v1.json",
    "docs/engineering/testnet/README.md", "docs/engineering/testnet/REWARD_THRESHOLDS.md",
    *("tools/testnet/" + name for name in ("genesis.py", "model.py", "thresholds.py", "self_test.py",
                                         "run_smoke.py", "smoke_self_test.py")),
})
CHANGED = FILES | {MANIFEST, RECORD}
CONSENSUS = {
    "pow": "sha256d", "daa": "existing-ergon-ema", "bootstrap_bits": "1e00ffff",
    "pow_limit_hex": "000000ffff000000000000000000000000000000000000000000000000000000",
    "target_spacing_seconds": 600, "decay_interval_blocks": 144, "value_calibration": 7100000000000,
    "ema_activation_time": 0, "reward_model": "existing-proportional-integer-without-floor-or-cap",
    "coinbase_maturity_blocks": 100, "cashtokens_activation": "not-implemented"}
CONTRACT = {
    "network": "ergon-testnet-v1", "genesis": "00000066119ff801d756659ca11217fe21c0b5f66779eaf9fbc87a8945aa8b4c",
    "asset_display": {"name": "Ergon Testnet", "ticker": "tXRG", "decimals": 8},
    "mainnet_source": "CMainParams-byte-identical-to-accepted-parent",
    "mainnet_consensus_or_activation_change": False,
    "testnet_consensus": CONSENSUS,
    "explicit_differences": ["independent-identity-and-genesis", "easier-bootstrap-and-pow-limit",
                             "current-rules-at-chain-origin", "no-inherited-seeds-or-history-assumptions"],
    "reward_floor_or_cap": False, "cpu_exclusive": False, "universal_signature_replay_protection": False,
    "network_bridge_or_mainnet_fallback": False, "daa_redesign": False, "token_activation": False,
    "chronik": "optional-explicit-native-network-genesis-bound-observe-index-and-upstream-client-checks",
    "chronik_compile_or_runtime_default_enabled": False,
    "existing_runtime_cases_repetitions_assertions_deadlines": "unchanged",
    "historical_source_gates": "exact-accepted-parent-not-weakened",
    "Rust_registry_changes": False, "system_dependencies_locked": False,
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only",
    "incoming_evidence": [], "deployment_by_CI": False, "desktop_integration": False}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args])


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON field")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def identity(raw, mode="100644"):
    return {"mode": mode, "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "git_blob_sha1": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()}


def before_identity(root, name):
    rows = [r for r in git(root, "ls-tree", "-z", PARENT, "--", name).split(b"\0") if r]
    if not rows:
        return None
    require(len(rows) == 1, "ambiguous parent source")
    meta, actual = rows[0].split(b"\t", 1)
    mode, kind, blob = meta.decode().split()
    require(actual.decode() == name and kind == "blob" and mode in ("100644", "100755"), "parent type drift")
    return identity(git(root, "cat-file", "blob", blob), mode)


def current_identity(root, name):
    relative = PurePosixPath(name)
    require(str(relative) == name and not relative.is_absolute() and ".." not in relative.parts,
            "unsafe public source path")
    path = root / name
    require(not any(p.is_symlink() for p in (path, *path.parents)), "source symlink refused")
    meta = path.lstat()
    mode = "100755" if name == "tools/engineering/check_change.py" else "100644"
    require(stat.S_ISREG(meta.st_mode) and meta.st_nlink == 1 and
            stat.S_IMODE(meta.st_mode) == int(mode[-3:], 8), "source type/mode/alias drift")
    raw = path.read_bytes()
    raw.decode("utf8", "strict")
    require(b"\0" not in raw, "binary source refused")
    return identity(raw, mode)


def mainnet_class(raw):
    start = raw.index(b"class CMainParams : public CChainParams {")
    return raw[start:raw.index(b"\n};", start) + 3]


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE, "accepted parent tree drift")
    git(root, "merge-base", "--is-ancestor", PARENT, "HEAD")
    doc = read_json(root / MANIFEST)
    require(set(doc) == {"$comment", "schema", "license", "public_parent", "contract", "files"}
            and doc["$comment"] == "SPDX-License-Identifier: MIT" and doc["license"] == "MIT"
            and doc["schema"] == "ergon-development-source/v1"
            and doc["public_parent"] == {"commit": PARENT, "tree": TREE}
            and doc["contract"] == CONTRACT
            and [row["path"] for row in doc["files"]] == sorted(FILES), "testnet manifest contract/scope drift")
    for row in doc["files"]:
        before = before_identity(root, row["path"])
        require(set(row) == {"path", "before", "after", "provenance"}
                and row["before"] == before and row["after"] == current_identity(root, row["path"])
                and before != row["after"]
                and row["provenance"] == ("PUBLIC+A1/MIT" if before else "A1/MIT"), "testnet source identity/provenance drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == CHANGED, "unexpected testnet path; other accepted inputs frozen")
    require(mainnet_class((root / "src/chainparams.cpp").read_bytes()) ==
            mainnet_class(git(root, "show", PARENT + ":src/chainparams.cpp")), "mainnet class changed")
    profile = read_json(root / "docs/engineering/testnet/ergon-testnet-v1.json")
    require(profile["consensus"] == CONSENSUS and profile["genesis"]["hash"] == CONTRACT["genesis"]
            and profile["asset"] == CONTRACT["asset_display"]
            and profile["status"] == "candidate-not-deployed" and profile["knowledge_status"] == "Open Question"
            and profile["identity"]["builtin_seeds"] == []
            and profile["fidelity"]["extreme_scenarios_define_consensus"] is False,
            "profile fidelity or claim drift")
    from check_change import validate as validate_record, TESTNET_BOUNDARIES
    record = read_json(root / RECORD)
    validate_record(record, RECORD)
    require(record["change_id"] == "ERGON-CHANGE-0067" and record["stage"] == "testnet-preparation"
            and record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE}
            and record["boundaries"] == TESTNET_BOUNDARIES
            and record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question",
                "ceiling": "record-review-only", "artifacts": []}
            and [r["path"] for r in record["files"]] == sorted(CHANGED - {RECORD}), "testnet engineering record drift")
    for row in record["files"]:
        def normalized(value):
            return None if value is None else {("git_blob_sha1" if k == "git_blob" else k): v for k, v in value.items()}
        require(normalized(row["before"]) == before_identity(root, row["path"])
                and normalized(row["after"]) == current_identity(root, row["path"]), "record source identity drift")
    return CHANGED


def historical_checks(root):
    """Keep old source gates on their accepted public inputs, not on new rules."""
    with tempfile.TemporaryDirectory(prefix="ergon-testnet-parent-") as temporary:
        source = Path(temporary) / "source"
        subprocess.run(["git", "clone", "--shared", "--no-checkout", "--", str(root.resolve()), str(source)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", PARENT],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        require(git(source, "rev-parse", "HEAD^{tree}").decode().strip() == TREE and
                not git(source, "status", "--porcelain"), "historical materialization drift")
        for command in (
            ["python3", "-B", "tools/engineering/check_chronik_continuous_service.py", "historical"],
            ["python3", "-B", "tools/engineering/check_chronik_continuous_service_self_test.py"],
            ["python3", "-B", "tools/engineering/check_change.py", "check"],
            ["python3", "-B", "tools/engineering/check_change.py", "self-test"],
        ):
            # This is an aggregate of 58 immutable source checks, not a
            # native component execution. Their individual limits remain
            # unchanged; do not confuse this budget with the 60s run gates.
            result = subprocess.run(command, cwd=source, capture_output=True, timeout=900)
            require(result.returncode == 0 and len(result.stdout) + len(result.stderr) <= 1024 * 1024,
                    "accepted predecessor source check refused: " + " ".join(command))
            print("PASS exact accepted parent: " + " ".join(command), flush=True)
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
    print("PASS: exact dedicated testnet candidate; no mainnet mutation, deployment or proof promotion")

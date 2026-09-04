#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build, validate, and compare the bounded legacy-compatibility reproduction."""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Any


BASELINE_URL = "https://github.com/Ergon-moe/Bitcoin-Static.git"
BASELINE_COMMIT = "2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b"
BASELINE_TREE = "8a74bb952c2137156214b9fe5888c494bd77aeca"
CANDIDATE_URL = "https://github.com/ErgonSurfer/ergon-lab.git"
CANDIDATE_COMMIT = "889212482c964ec98dcc2ac55321cb0e04e17666"
CANDIDATE_TREE = "2f55a7d6c972d4100a2913f015aac91cdeacaf53"
INTEGRATION_PARENT_COMMIT = "681a801e9694aedf4ef6a8e95605459bad673dd0"
INTEGRATION_PARENT_TREE = "c2c33c6385ab034f0b904eac6ce260f324345eca"
ACCEPTED_RECORD_SHA256 = (
    "0f9848a6c3d8fb48ec09547255fe4abd1adcf81cd05783788bf8a114d64c6ec5"
)
REVIEWER_IDENTITY = "Ergon Public Node GitHub Cockpit"
DECISION_DATE = "2026-09-04"
OBSERVED_REPORT_SHA256 = (
    "95bc5452231deb1a6be72909910c94bea3a52ee5748ceb857b8f7ca0dda8b757"
)
OBSERVED_PROJECTION_SHA256 = (
    "74a8f8997745e137f5ba67111d8d06eacc1728899e84584b1b0b29989efc8a52"
)
REPORT_RELATIVE_PATH = Path(
    "docs/engineering/evidence/ergon-change-0010/legacy-compatibility.json"
)
LOCK_RELATIVE_PATH = Path(
    "contrib/reproducibility/legacy-ubuntu22-arm64.lock.json"
)
CONTAINERFILE_RELATIVE_PATH = Path(
    "contrib/reproducibility/legacy-ubuntu22-arm64.Containerfile"
)
LOCK_SHA256 = (
    "a625d09aaa97e54f5fa7487f1000b139dcdf93472bc984425a25e2bf3777eab0"
)
DOCKERFILE_FRONTEND = (
    "docker/dockerfile:1.7@sha256:"
    "a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e"
)
CONTAINER_MANIFEST_DIGEST = (
    "sha256:2edbbc5dc405e9612ba3584ce95480277e3eb374407b5505fe26f17df77c7dbc"
)
APT_SNAPSHOT = "20260901T000000Z"
APT_SNAPSHOT_URL = "https://snapshot.ubuntu.com/ubuntu/20260901T000000Z"
TLS_BOOTSTRAP = {
    "package": "ca-certificates",
    "sha256": "6e8cdcc8c86103acd4fc14649eac62ff2037108389074a7b167567af33c32245",
    "url": (
        "https://snapshot.ubuntu.com/ubuntu/20260901T000000Z/pool/main/"
        "c/ca-certificates/ca-certificates_20260601~22.04.1_all.deb"
    ),
    "version": "20260601~22.04.1",
}
DIRECT_PACKAGES = {
    "build-essential": "12.9ubuntu3",
    "ca-certificates": "20260601~22.04.1",
    "cmake": "3.22.1-1ubuntu1.22.04.2",
    "git": "1:2.34.1-1ubuntu1.17",
    "libboost-chrono-dev": "1.74.0.3ubuntu7",
    "libboost-filesystem-dev": "1.74.0.3ubuntu7",
    "libboost-test-dev": "1.74.0.3ubuntu7",
    "libboost-thread-dev": "1.74.0.3ubuntu7",
    "libdb++-dev": "1:5.3.21~exp1ubuntu4",
    "libdb-dev": "1:5.3.21~exp1ubuntu4",
    "libevent-dev": "2.1.12-stable-1build3",
    "libssl-dev": "3.0.2-0ubuntu1.29",
    "ninja-build": "1.10.1-1",
    "openssh-client": "1:8.9p1-3ubuntu0.16",
    "python3": "3.10.6-1~22.04.1",
}
CMAKE_OPTIONS = [
    "-GNinja",
    "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
    "-DBUILD_BITCOIN_QT=OFF",
    "-DBUILD_BITCOIN_WALLET=ON",
    "-DBUILD_BITCOIN_ZMQ=OFF",
    "-DENABLE_UPNP=OFF",
]
SCENARIOS = (
    "mixed-node-coexistence",
    "legacy-mining-baseline",
    "legacy-mining-candidate",
    "inherited-functional-default-launch",
)
BUILD_ROLES = ("baseline", "candidate")
MIXED_NODE_LIFECYCLES = (
    ("full-reindex", b"ERGON_LEGACY_LIFECYCLE_OK full-reindex"),
    ("chainstate-reindex",
     b"ERGON_LEGACY_LIFECYCLE_OK chainstate-reindex"),
    ("default-protected-reorg",
     b"ERGON_LEGACY_LIFECYCLE_OK default-protected-reorg"),
    ("physical-pruning",
     b"ERGON_LEGACY_LIFECYCLE_OK physical-pruning"),
)
MIXED_NODE_FEATURE_FUNCTIONS = (
    "submit_branch_block",
    "branch_tip_status",
    "mine_large_blocks",
    "block_file_pair",
    "regular_file_identity",
    "directory_identity",
    "assert_file_pair_absent",
    "setup_nodes",
    "setup_network",
    "node_snapshot",
    "assert_common_chain",
    "mine_and_compare",
    "rebuild_and_compare",
    "advance_to_prune_boundary",
    "enable_pruning",
    "physical_prune_and_compare",
    "generate_reorg_bundle",
    "default_protected_reorg_and_compare",
    "run_test",
)
MIXED_NODE_FAILURE_KINDS = (
    "child-nonzero",
    "child-reported-skip",
    "framework-success-marker-absent",
    "required-lifecycle-marker-absent",
    "before-output-validation",
)
MIXED_NODE_FRAME_RE = re.compile(
    rb'^  File "[^"\r\n]*/tests/compatibility/legacy/'
    rb'feature_ergon_legacy_compatibility\.py", line [0-9]+, in '
    rb'([A-Za-z_][A-Za-z0-9_]*)$'
)
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ReproductionError(RuntimeError):
    """A public reproduction invariant failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReproductionError(message)


def strict_object(value: Any, keys: set[str], context: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{context} must be an object")
    actual = set(value)
    require(actual == keys, f"{context} keys differ: {sorted(actual ^ keys)}")
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file, object_pairs_hook=_reject_duplicate_pairs)
    except (OSError, json.JSONDecodeError) as error:
        raise ReproductionError(f"cannot read JSON document: {path.name}") from error
    require(isinstance(value, dict), f"{path.name} must contain one object")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReproductionError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ReproductionError(f"cannot hash file: {path.name}") from error
    return digest.hexdigest()


def require_hex(value: Any, pattern: re.Pattern[str], context: str) -> str:
    require(isinstance(value, str) and pattern.fullmatch(value) is not None,
            f"{context} has an invalid digest")
    return value


def require_identity(value: Any, commit: str, tree: str, context: str) -> None:
    identity = strict_object(value, {"clean", "commit", "tree"}, context)
    require(identity == {"clean": True, "commit": commit, "tree": tree},
            f"{context} differs from the governed identity")


def validate_report(report: dict[str, Any]) -> None:
    strict_object(
        report,
        {
            "schema", "knowledge_status", "evidence_ceiling",
            "raw_output_retained", "host_specific_absolute_paths_retained",
            "parent_environment_retained", "candidate_source", "public_git",
            "builds", "runner_environment", "node_launch_modes",
            "change_identity", "inherited_functional_selection", "executions",
            "claims",
        },
        "report",
    )
    require(report["schema"] == "ergon-legacy-compatibility/v1",
            "report schema differs")
    require(report["knowledge_status"] == "Observed",
            "an individual matrix report must remain Observed")
    require(report["evidence_ceiling"] == "assembled_component",
            "report evidence ceiling differs")
    for key in (
        "raw_output_retained", "host_specific_absolute_paths_retained",
        "parent_environment_retained",
    ):
        require(report[key] is False, f"privacy flag {key} must be false")

    require_identity(report["candidate_source"], CANDIDATE_COMMIT,
                     CANDIDATE_TREE, "candidate_source")

    builds = strict_object(report["builds"], set(BUILD_ROLES), "builds")
    for role in BUILD_ROLES:
        build = strict_object(
            builds[role],
            {
                "binary_to_source_provenance", "bitcoind_bytes",
                "bitcoind_sha256", "config_sha256",
                "config_source_binding_checked",
            },
            f"builds.{role}",
        )
        require(build["binary_to_source_provenance"] ==
                "external_build_record_required",
                f"builds.{role} provenance contract differs")
        require(isinstance(build["bitcoind_bytes"], int) and
                build["bitcoind_bytes"] > 0,
                f"builds.{role}.bitcoind_bytes must be positive")
        require_hex(build["bitcoind_sha256"], HEX64,
                    f"builds.{role}.bitcoind_sha256")
        require_hex(build["config_sha256"], HEX64,
                    f"builds.{role}.config_sha256")
        require(build["config_source_binding_checked"] is True,
                f"builds.{role} source binding was not checked")

    change = strict_object(
        report["change_identity"],
        {
            "baseline", "public_root", "integration_parent", "candidate",
            "candidate_direct_parent_is_integration_parent", "record_path",
            "record_sha256", "diff_entries",
        },
        "change_identity",
    )
    require_identity(change["baseline"], BASELINE_COMMIT, BASELINE_TREE,
                     "change_identity.baseline")
    require_identity(change["candidate"], CANDIDATE_COMMIT, CANDIDATE_TREE,
                     "change_identity.candidate")
    require(change["candidate"] == report["candidate_source"],
            "candidate identity copies differ")
    require(change["public_root"] == {
        "commit": "5bcdba149119aa9035830e069d1cae1d9bcddfb4",
        "tree": BASELINE_TREE,
    }, "public-root identity differs")
    require(change["candidate_direct_parent_is_integration_parent"] is True,
            "candidate direct-parent binding is false")
    require(change["record_path"] ==
            "docs/engineering/changes/ergon-change-0010.json",
            "accepted record path differs")
    require(change["record_sha256"] == ACCEPTED_RECORD_SHA256,
            "accepted record digest differs")
    require(change["diff_entries"] == [
        {"operation": "A", "path": "docs/engineering/changes/ergon-change-0010.json"},
        {"operation": "M", "path": "tests/compatibility/legacy/run_matrix.py"},
        {"operation": "M", "path": "tests/compatibility/legacy/self_test.py"},
    ], "technical change inventory differs")

    public_git = strict_object(
        report["public_git"],
        {
            "accepted_record", "git_version", "integration_parent",
            "origin_main_oid", "signatures", "trust_root",
        },
        "public_git",
    )
    require(isinstance(public_git["git_version"], str) and
            public_git["git_version"].strip(), "git version is missing")
    require_hex(public_git["origin_main_oid"], HEX40, "origin_main_oid")
    accepted = strict_object(
        public_git["accepted_record"],
        {"decision_date", "reviewer_identity", "sha256"},
        "public_git.accepted_record",
    )
    require(accepted == {
        "decision_date": DECISION_DATE,
        "reviewer_identity": REVIEWER_IDENTITY,
        "sha256": ACCEPTED_RECORD_SHA256,
    }, "accepted review identity differs")
    require(public_git["integration_parent"] == change["integration_parent"],
            "integration-parent copies differ")
    parent = strict_object(
        change["integration_parent"],
        {
            "candidate_direct_parent", "commit", "controlled_paths_unchanged",
            "explicit_reviewed_cli", "preimages_verified", "record_match",
            "remote_ancestor", "tree",
        },
        "change_identity.integration_parent",
    )
    require(parent == {
        "candidate_direct_parent": True,
        "commit": INTEGRATION_PARENT_COMMIT,
        "controlled_paths_unchanged": True,
        "explicit_reviewed_cli": True,
        "preimages_verified": True,
        "record_match": True,
        "remote_ancestor": True,
        "tree": INTEGRATION_PARENT_TREE,
    }, "integration-parent binding differs")

    trust = strict_object(
        public_git["trust_root"],
        {
            "allowed_signers_bytes", "allowed_signers_sha256",
            "candidate_same_key", "fingerprint", "format", "key_algorithm",
            "principal", "public_key", "public_key_bytes", "public_key_sha256",
        },
        "public_git.trust_root",
    )
    require(trust["candidate_same_key"] is True,
            "candidate signature key differs")
    require(trust["format"] == "ssh" and trust["key_algorithm"] == "ED25519",
            "trust-root algorithm differs")
    require_hex(trust["allowed_signers_sha256"], HEX64,
                "trust_root.allowed_signers_sha256")
    require_hex(trust["public_key_sha256"], HEX64,
                "trust_root.public_key_sha256")
    signatures = strict_object(
        public_git["signatures"],
        {"candidate", "integration_parent", "public_root"},
        "public_git.signatures",
    )
    identities = {
        "candidate": change["candidate"],
        "integration_parent": parent,
        "public_root": change["public_root"],
    }
    for role, identity in identities.items():
        signature = strict_object(
            signatures[role],
            {
                "commit", "fingerprint", "format", "key_algorithm",
                "principal", "status", "tree", "verification_result",
            },
            f"public_git.signatures.{role}",
        )
        require(signature["commit"] == identity["commit"] and
                signature["tree"] == identity["tree"],
                f"{role} signature identity differs")
        for key in ("fingerprint", "format", "key_algorithm", "principal"):
            require(signature[key] == trust[key],
                    f"{role} signature {key} differs from trust root")
        require(signature["status"] == "G" and
                signature["verification_result"] == "valid",
                f"{role} signature was not verified")

    environment = strict_object(
        report["runner_environment"],
        {"LANG", "LC_ALL", "NO_COLOR", "PATH", "TERM", "TMPDIR", "TZ"},
        "runner_environment",
    )
    require(environment == {
        "LANG": "C", "LC_ALL": "C", "NO_COLOR": "1",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TERM": "dumb",
        "TMPDIR": "unique_runner_created_directory", "TZ": "UTC",
    }, "runner environment differs")
    require(set(report["node_launch_modes"]) == set(SCENARIOS),
            "node-launch scenario set differs")
    strict_object(report["node_launch_modes"], set(SCENARIOS),
                  "node_launch_modes")
    strict_object(report["executions"], set(SCENARIOS), "executions")
    require(all(report["executions"][scenario] == "pass"
                for scenario in SCENARIOS), "one matrix scenario did not pass")

    selection = strict_object(
        report["inherited_functional_selection"],
        {
            "baseline", "candidate", "identical", "selection_delta",
            "governed_tests_outside_functional_discovery",
        },
        "inherited_functional_selection",
    )
    require(selection["baseline"] == selection["candidate"],
            "inherited functional selections differ")
    require(selection["identical"] is True and selection["selection_delta"] == [],
            "inherited functional selection is not identical")
    require(selection["governed_tests_outside_functional_discovery"] == [
        "tests/compatibility/legacy/feature_ergon_legacy_compatibility.py"
    ], "governed out-of-discovery test differs")
    strict_object(
        selection["baseline"],
        {
            "non_scripts", "python_files", "selected_scripts", "test_params",
            "test_runner_sha256", "timing_sha256",
        },
        "inherited_functional_selection.baseline",
    )

    require(report["claims"] == {
        "legacy_consensus_changed": False,
        "mainnet_activation": "out-of-scope",
        "optional_indexing_present": False,
        "testnet_activation": "out-of-scope",
    }, "consensus or activation claims differ")


def semantic_projection(report: dict[str, Any]) -> dict[str, Any]:
    validate_report(report)
    change = report["change_identity"]
    public_git = report["public_git"]
    trust = public_git["trust_root"]
    selection = report["inherited_functional_selection"]
    return {
        "schema": "ergon-legacy-semantic-projection/v1",
        "report": {
            "schema": report["schema"],
            "knowledge_status": report["knowledge_status"],
            "evidence_ceiling": report["evidence_ceiling"],
            "raw_output_retained": report["raw_output_retained"],
            "host_specific_absolute_paths_retained":
                report["host_specific_absolute_paths_retained"],
            "parent_environment_retained": report["parent_environment_retained"],
        },
        "identity": {
            "baseline": change["baseline"],
            "public_root": change["public_root"],
            "integration_parent": change["integration_parent"],
            "candidate": change["candidate"],
            "candidate_direct_parent_is_integration_parent":
                change["candidate_direct_parent_is_integration_parent"],
            "diff_entries": change["diff_entries"],
            "record": {
                "path": change["record_path"],
                "sha256": change["record_sha256"],
                "reviewer_identity":
                    public_git["accepted_record"]["reviewer_identity"],
                "decision_date": public_git["accepted_record"]["decision_date"],
            },
        },
        "trust": {
            key: trust[key]
            for key in (
                "format", "key_algorithm", "principal", "fingerprint",
                "public_key_sha256", "allowed_signers_sha256",
                "candidate_same_key",
            )
        },
        "signatures": {
            role: {
                key: public_git["signatures"][role][key]
                for key in ("commit", "tree", "status", "verification_result")
            }
            for role in ("public_root", "integration_parent", "candidate")
        },
        "harness": {
            "build_roles": list(BUILD_ROLES),
            "build_bindings": {
                role: {
                    "binary_to_source_provenance":
                        report["builds"][role]["binary_to_source_provenance"],
                    "config_source_binding_checked":
                        report["builds"][role]["config_source_binding_checked"],
                }
                for role in BUILD_ROLES
            },
            "runner_environment": report["runner_environment"],
            "node_launch_modes": report["node_launch_modes"],
            "functional_selection": {
                "inventory": selection["baseline"],
                "identical": selection["identical"],
                "selection_delta": selection["selection_delta"],
                "governed_tests_outside_functional_discovery":
                    selection["governed_tests_outside_functional_discovery"],
            },
            "executions": report["executions"],
            "claims": report["claims"],
        },
    }


def environment_projection(report: dict[str, Any]) -> dict[str, Any]:
    validate_report(report)
    return {
        "git_version": report["public_git"]["git_version"],
        "origin_main_oid": report["public_git"]["origin_main_oid"],
        "builds": {
            role: {
                key: report["builds"][role][key]
                for key in ("bitcoind_bytes", "bitcoind_sha256", "config_sha256")
            }
            for role in BUILD_ROLES
        },
    }


def differing_pointers(left: Any, right: Any, prefix: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        require(set(left) == set(right), "environment projection keys differ")
        result: list[str] = []
        for key in sorted(left):
            result.extend(differing_pointers(left[key], right[key],
                                              f"{prefix}/{key}"))
        return result
    return [] if left == right else [prefix]


def write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    require(not path.exists(), f"output already exists: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as file:
            json.dump(value, file, indent=2, sort_keys=True)
            file.write("\n")
    except OSError as error:
        raise ReproductionError(f"cannot write output: {path.name}") from error


def validate_lock(lock: dict[str, Any]) -> None:
    strict_object(lock, {"$comment", "schema", "runner", "container", "apt",
                         "build", "claims"}, "lock")
    require(lock["$comment"] == "SPDX-License-Identifier: MIT",
            "lock SPDX marker differs")
    require(lock["schema"] == "ergon-legacy-reproduction-lock/v1",
            "lock schema differs")
    runner = strict_object(
        lock["runner"],
        {"architecture", "github_hosted_label", "runner_image_mutable"},
        "lock.runner",
    )
    require(runner == {
        "architecture": "arm64",
        "github_hosted_label": "ubuntu-22.04-arm",
        "runner_image_mutable": True,
    }, "runner lock differs")
    container = strict_object(
        lock["container"],
        {"dockerfile_frontend", "image", "platform", "reference",
         "manifest_digest"},
        "lock.container",
    )
    require(container["platform"] == "linux/arm64",
            "container architecture differs")
    require(container == {
        "dockerfile_frontend": DOCKERFILE_FRONTEND,
        "image": "ubuntu",
        "platform": "linux/arm64",
        "reference": f"ubuntu@{CONTAINER_MANIFEST_DIGEST}",
        "manifest_digest": CONTAINER_MANIFEST_DIGEST,
    }, "container lock differs")
    require(container["reference"].endswith(container["manifest_digest"]),
            "container digest differs")
    apt = strict_object(
        lock["apt"],
        {"archive_signature_verification", "snapshot", "snapshot_url",
         "tls_bootstrap", "direct_packages"},
        "lock.apt",
    )
    tls_bootstrap = strict_object(
        apt["tls_bootstrap"], {"package", "sha256", "url", "version"},
        "lock.apt.tls_bootstrap",
    )
    require(apt["archive_signature_verification"] is True,
            "archive signature verification must remain enabled")
    require(apt["snapshot"] == APT_SNAPSHOT and
            apt["snapshot_url"] == APT_SNAPSHOT_URL,
            "apt snapshot differs")
    require(tls_bootstrap == TLS_BOOTSTRAP, "TLS bootstrap lock differs")
    direct_packages = strict_object(
        apt["direct_packages"], set(DIRECT_PACKAGES),
        "lock.apt.direct_packages",
    )
    require(direct_packages == DIRECT_PACKAGES, "direct package lock differs")
    build = strict_object(
        lock["build"], {"cmake_options", "parallel_jobs", "target"},
        "lock.build",
    )
    require(build == {
        "cmake_options": CMAKE_OPTIONS,
        "parallel_jobs": 4,
        "target": "bitcoind",
    }, "build lock differs")
    strict_object(
        lock["claims"],
        {"build_reproducibility", "reproduction_dependencies_locked",
         "runner_vm_image_locked"},
        "lock.claims",
    )
    require(lock["claims"] == {
        "build_reproducibility": "not_claimed",
        "reproduction_dependencies_locked": True,
        "runner_vm_image_locked": False,
    }, "lock claims differ")


def capture(command: list[str], env: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=env,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReproductionError(f"command failed: {command[0]}") from error
    return result.stdout.strip()


def run_checked(command: list[str], env: dict[str, str]) -> None:
    try:
        subprocess.run(command, check=True, env=env)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReproductionError(f"command failed: {command[0]}") from error


def classify_mixed_node_output(stdout: bytes, stderr: bytes) -> tuple[str, str]:
    output = stdout + b"\n" + stderr
    lines = output.splitlines()
    lifecycle = "none"
    previous_position = -1
    missing_seen = False
    for identifier, marker in MIXED_NODE_LIFECYCLES:
        count = output.count(marker)
        position = output.find(marker)
        if count > 1 or (count == 1 and missing_seen) \
                or (count == 1 and position <= previous_position):
            lifecycle = "invalid-marker-state"
            break
        if count == 0:
            missing_seen = True
            continue
        lifecycle = identifier
        previous_position = position

    frame = "none"
    for line in lines:
        match = MIXED_NODE_FRAME_RE.fullmatch(line)
        if match is None:
            continue
        candidate = match.group(1).decode("ascii")
        if candidate in MIXED_NODE_FEATURE_FUNCTIONS:
            frame = candidate
    return lifecycle, frame


def mixed_node_detail(failure_kind: str, lifecycle: str,
                      frame: str) -> str:
    return (
        f"failure-kind {failure_kind} "
        f"last-completed-lifecycle {lifecycle} "
        f"last-allowlisted-feature-frame {frame}"
    )


def install_matrix_attribution(module: Any) -> None:
    original_run = module.run_execution
    original_validate = module.validate_test_output
    active_scenario: list[str | None] = [None]

    def attributed_validate(returncode: int, stdout: bytes, stderr: bytes,
                            required_markers: tuple[bytes, ...] = ()) -> None:
        try:
            original_validate(returncode, stdout, stderr, required_markers)
        except module.MatrixError:
            if active_scenario[0] != "mixed-node-coexistence":
                raise
            combined = stdout + b"\n" + stderr
            if returncode != 0:
                failure_kind = "child-nonzero"
            elif b"Test skipped" in combined or b"Tests skipped" in combined:
                failure_kind = "child-reported-skip"
            elif b"Tests successful" not in combined:
                failure_kind = "framework-success-marker-absent"
            else:
                failure_kind = "required-lifecycle-marker-absent"
            lifecycle, frame = classify_mixed_node_output(stdout, stderr)
            raise module.MatrixError(
                mixed_node_detail(failure_kind, lifecycle, frame)
            ) from None

    def attributed(execution: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
        scenario = execution.get("id")
        require(scenario in SCENARIOS, "matrix scenario identity differs")
        require(active_scenario[0] is None, "matrix execution attribution nested")
        active_scenario[0] = scenario
        try:
            return original_run(execution, *args, **kwargs)
        except module.MatrixError as error:
            detail = str(error)
            if scenario == "mixed-node-coexistence" and re.fullmatch(
                r"failure-kind (?:" +
                "|".join(map(re.escape, MIXED_NODE_FAILURE_KINDS[:-1])) +
                r") last-completed-lifecycle (?:" +
                "|".join(re.escape(item[0]) for item in MIXED_NODE_LIFECYCLES) +
                r"|none|invalid-marker-state) "
                r"last-allowlisted-feature-frame (?:" +
                "|".join(map(re.escape, MIXED_NODE_FEATURE_FUNCTIONS)) +
                r"|none)",
                detail,
            ):
                raise module.MatrixError(
                    f"scenario {scenario} {detail}"
                ) from None
            if scenario == "mixed-node-coexistence":
                raise module.MatrixError(
                    f"scenario {scenario} " + mixed_node_detail(
                        "before-output-validation", "none", "none"
                    )
                ) from None
            raise module.MatrixError(f"scenario {scenario} failed") from None
        finally:
            active_scenario[0] = None

    module.validate_test_output = attributed_validate
    module.run_execution = attributed


def run_matrix_driver(matrix_path: Path, matrix_args: list[str]) -> None:
    require(sys.dont_write_bytecode,
            "matrix driver requires disabled bytecode writes")
    matrix_path = matrix_path.resolve(strict=True)
    spec = importlib.util.spec_from_file_location(
        "ergon_reviewed_legacy_matrix", matrix_path
    )
    require(spec is not None and spec.loader is not None,
            "reviewed matrix module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (ImportError, OSError) as error:
        raise ReproductionError("reviewed matrix module cannot be loaded") from error
    require(hasattr(module, "MatrixError") and
            hasattr(module, "EXECUTIONS") and
            hasattr(module, "main") and
            hasattr(module, "run_execution") and
            hasattr(module, "validate_test_output"),
            "reviewed matrix driver contract differs")
    require(tuple(item.get("id") for item in module.EXECUTIONS) == SCENARIOS,
            "reviewed matrix scenario set differs")
    require(hasattr(module, "MIXED_NODE_SUCCESS_MARKERS") and
            tuple(module.MIXED_NODE_SUCCESS_MARKERS) == tuple(
                item[1] for item in MIXED_NODE_LIFECYCLES
            ), "reviewed matrix lifecycle marker set differs")
    install_matrix_attribution(module)
    previous_argv = sys.argv
    sys.argv = [str(matrix_path), *matrix_args]
    try:
        module.main()
    except module.MatrixError as error:
        message = str(error)
        allowed_simple = re.fullmatch(
            r"scenario (?:" + "|".join(map(re.escape, SCENARIOS)) +
            r") failed",
            message,
        )
        allowed_mixed = re.fullmatch(
            r"scenario mixed-node-coexistence failure-kind (?:" +
            "|".join(map(re.escape, MIXED_NODE_FAILURE_KINDS)) +
            r") last-completed-lifecycle (?:" +
            "|".join(re.escape(item[0]) for item in MIXED_NODE_LIFECYCLES) +
            r"|none|invalid-marker-state) "
            r"last-allowlisted-feature-frame (?:" +
            "|".join(map(re.escape, MIXED_NODE_FEATURE_FUNCTIONS)) +
            r"|none)",
            message,
        )
        if allowed_simple is None and allowed_mixed is None:
            raise ReproductionError(
                "matrix failed outside an attributed execution"
            ) from None
        raise ReproductionError(message) from None
    finally:
        sys.argv = previous_argv


def git_identity(path: Path, env: dict[str, str]) -> dict[str, Any]:
    commit = capture(["git", "-C", str(path), "rev-parse", "HEAD^{commit}"], env)
    tree = capture(["git", "-C", str(path), "rev-parse", "HEAD^{tree}"], env)
    clean = capture(["git", "-C", str(path), "status", "--porcelain"], env) == ""
    return {"clean": clean, "commit": commit, "tree": tree}


def clone_exact(url: str, commit: str, tree: str, destination: Path,
                env: dict[str, str], fetch_main: bool = False) -> None:
    require(not destination.exists(), "clone destination already exists")
    run_checked(["git", "clone", "--no-tags", url, str(destination)], env)
    if fetch_main:
        run_checked([
            "git", "-C", str(destination), "fetch", "--force", "origin",
            "+refs/heads/main:refs/remotes/origin/main",
        ], env)
    run_checked(["git", "-C", str(destination), "checkout", "--detach", commit], env)
    identity = git_identity(destination, env)
    require(identity == {"clean": True, "commit": commit, "tree": tree},
            "fresh clone identity differs")


def build_role(source: Path, build: Path, options: list[str], jobs: int,
               env: dict[str, str]) -> None:
    require(not build.exists(), "build root already exists")
    run_checked(["cmake", "-S", str(source), "-B", str(build), *options], env)
    run_checked([
        "cmake", "--build", str(build), "--target", "bitcoind",
        "--parallel", str(jobs),
    ], env)


def package_versions(env: dict[str, str]) -> dict[str, str]:
    output = capture([
        "dpkg-query", "-W", "-f=${Package}\t${Version}\n",
    ], env)
    result: dict[str, str] = {}
    for line in output.splitlines():
        name, version = line.split("\t", 1)
        result[name] = version
    return dict(sorted(result.items()))


def public_ci_identity() -> dict[str, str]:
    names = {
        "job": "ERGON_GITHUB_JOB",
        "repository": "ERGON_GITHUB_REPOSITORY",
        "run_attempt": "ERGON_GITHUB_RUN_ATTEMPT",
        "run_id": "ERGON_GITHUB_RUN_ID",
        "sha": "ERGON_GITHUB_SHA",
        "workflow_ref": "ERGON_GITHUB_WORKFLOW_REF",
    }
    result = {key: os.environ.get(name, "local-preflight")
              for key, name in names.items()}
    return result


def create_build_receipt(
    lock: dict[str, Any], baseline_source: Path, candidate_source: Path,
    baseline_build: Path, candidate_build: Path, report_path: Path,
    lock_path: Path, env: dict[str, str],
) -> dict[str, Any]:
    installed = package_versions(env)
    direct = lock["apt"]["direct_packages"]
    for package, version in direct.items():
        require(installed.get(package) == version,
                f"installed package differs from lock: {package}")
    os_release: dict[str, str] = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os_release[key] = value.strip('"')
    require(platform.machine() == "aarch64", "reproduction requires arm64")
    sources = {
        "baseline": git_identity(baseline_source, env),
        "candidate": git_identity(candidate_source, env),
    }
    require_identity(sources["baseline"], BASELINE_COMMIT, BASELINE_TREE,
                     "receipt.sources.baseline")
    require_identity(sources["candidate"], CANDIDATE_COMMIT, CANDIDATE_TREE,
                     "receipt.sources.candidate")
    builds: dict[str, dict[str, Any]] = {}
    for role, root in (("baseline", baseline_build),
                       ("candidate", candidate_build)):
        binary = root / "src" / "bitcoind"
        config = root / "test" / "config.ini"
        require(binary.is_file() and config.is_file(),
                f"{role} build output is incomplete")
        builds[role] = {
            "bitcoind_bytes": binary.stat().st_size,
            "bitcoind_sha256": sha256_file(binary),
            "config_sha256": sha256_file(config),
        }
    return {
        "schema": "ergon-legacy-build-provenance/v1",
        "knowledge_status": "Observed",
        "technical_target": {
            "commit": CANDIDATE_COMMIT,
            "tree": CANDIDATE_TREE,
            "accepted_record_sha256": ACCEPTED_RECORD_SHA256,
        },
        "freshness": {
            "fresh_github_hosted_vm_required": True,
            "fresh_public_clones": True,
            "preexisting_builds_used": False,
            "actions_cache_used": False,
            "datadir_reuse": False,
        },
        "environment": {
            "architecture": platform.machine(),
            "container_image_id": os.environ.get(
                "ERGON_CONTAINER_IMAGE_ID", "local-preflight"
            ),
            "container_manifest_digest":
                lock["container"]["manifest_digest"],
            "github_runner": {
                "architecture": os.environ.get("ERGON_RUNNER_ARCH", "local"),
                "image_os": os.environ.get("ERGON_IMAGE_OS", "local"),
                "image_version": os.environ.get("ERGON_IMAGE_VERSION", "local"),
                "os": os.environ.get("ERGON_RUNNER_IMAGE_OS", "local"),
            },
            "kernel_release": platform.release(),
            "os": {
                "id": os_release.get("ID", ""),
                "version_id": os_release.get("VERSION_ID", ""),
            },
        },
        "public_ci": public_ci_identity(),
        "dependencies": {
            "locked": True,
            "lock_sha256": sha256_file(lock_path),
            "snapshot": lock["apt"]["snapshot"],
            "direct_packages": direct,
            "installed_packages": installed,
        },
        "tools": {
            "cmake": capture(["cmake", "--version"], env).splitlines()[0],
            "compiler": capture(["c++", "--version"], env).splitlines()[0],
            "git": capture(["git", "--version"], env),
            "ninja": capture(["ninja", "--version"], env),
            "python": capture(["python3", "--version"], env),
        },
        "commands": {
            "configure": ["cmake", "-S", "${SOURCE}", "-B", "${BUILD}",
                          *lock["build"]["cmake_options"]],
            "build": ["cmake", "--build", "${BUILD}", "--target", "bitcoind",
                      "--parallel", str(lock["build"]["parallel_jobs"])],
            "matrix": "exact tests/compatibility/legacy/run_matrix.py at technical_target.commit",
        },
        "sources": sources,
        "builds": builds,
        "matrix_report_sha256": sha256_file(report_path),
        "claims": {
            "build_reproducibility": "not_claimed",
            "operator_binary_equivalence": "not_claimed",
            "mainnet_coexistence": "not_claimed",
        },
        "privacy": {
            "host_specific_absolute_paths_retained": False,
            "parent_environment_retained": False,
            "raw_process_output_retained": False,
        },
    }


def validate_build_receipt(receipt: dict[str, Any]) -> bool:
    """Validate a receipt and return whether it is a main-branch hosted run."""
    strict_object(
        receipt,
        {
            "schema", "knowledge_status", "technical_target", "freshness",
            "environment", "public_ci", "dependencies", "tools", "commands",
            "sources", "builds", "matrix_report_sha256", "claims", "privacy",
        },
        "build_receipt",
    )
    require(receipt["schema"] == "ergon-legacy-build-provenance/v1",
            "build receipt schema differs")
    require(receipt["knowledge_status"] == "Observed",
            "build receipt must remain Observed")
    require(receipt["technical_target"] == {
        "commit": CANDIDATE_COMMIT,
        "tree": CANDIDATE_TREE,
        "accepted_record_sha256": ACCEPTED_RECORD_SHA256,
    }, "build receipt technical target differs")
    require(receipt["freshness"] == {
        "fresh_github_hosted_vm_required": True,
        "fresh_public_clones": True,
        "preexisting_builds_used": False,
        "actions_cache_used": False,
        "datadir_reuse": False,
    }, "build receipt freshness contract differs")

    environment = strict_object(
        receipt["environment"],
        {
            "architecture", "container_image_id", "container_manifest_digest",
            "github_runner", "kernel_release", "os",
        },
        "build_receipt.environment",
    )
    require(environment["architecture"] == "aarch64",
            "build receipt architecture differs")
    require(environment["container_manifest_digest"] ==
            CONTAINER_MANIFEST_DIGEST,
            "build receipt container manifest differs")
    image_id = environment["container_image_id"]
    require(image_id == "local-preflight" or
            (isinstance(image_id, str) and image_id.startswith("sha256:") and
             HEX64.fullmatch(image_id[7:]) is not None),
            "build receipt container image ID differs")
    require(isinstance(environment["kernel_release"], str) and
            environment["kernel_release"].strip(),
            "build receipt kernel release is missing")
    require(environment["os"] == {"id": "ubuntu", "version_id": "22.04"},
            "build receipt container OS differs")
    github_runner = strict_object(
        environment["github_runner"],
        {"architecture", "image_os", "image_version", "os"},
        "build_receipt.environment.github_runner",
    )

    public_ci = strict_object(
        receipt["public_ci"],
        {"job", "repository", "run_attempt", "run_id", "sha", "workflow_ref"},
        "build_receipt.public_ci",
    )
    local_values = all(value == "local-preflight" for value in public_ci.values())
    hosted_values = all(
        isinstance(value, str) and value not in ("", "local-preflight")
        for value in public_ci.values()
    )
    require(local_values or hosted_values,
            "build receipt mixes local and hosted CI identity")
    if local_values:
        require(all(value == "local" for value in github_runner.values()),
                "local receipt has a hosted-runner identity")
    else:
        require(public_ci["job"] == "reproduce",
                "hosted receipt job differs")
        require(public_ci["repository"] == "ErgonSurfer/ergon-lab",
                "hosted receipt repository differs")
        require(public_ci["run_id"].isdigit() and
                public_ci["run_attempt"].isdigit(),
                "hosted receipt run identity differs")
        require_hex(public_ci["sha"], HEX40, "build_receipt.public_ci.sha")
        require(public_ci["workflow_ref"] ==
                "ErgonSurfer/ergon-lab/.github/workflows/legacy-reproduction.yml@refs/heads/main",
                "hosted receipt workflow ref differs")
        require(github_runner["architecture"] == "ARM64" and
                github_runner["os"] == "Linux",
                "hosted runner platform differs")
        require(github_runner["image_os"] not in ("unavailable", "local") and
                github_runner["image_version"] not in ("unavailable", "local"),
                "hosted runner image identity is missing")
        require(image_id != "local-preflight",
                "hosted receipt container image ID is missing")

    dependencies = strict_object(
        receipt["dependencies"],
        {"locked", "lock_sha256", "snapshot", "direct_packages",
         "installed_packages"},
        "build_receipt.dependencies",
    )
    require(dependencies["locked"] is True,
            "reproduction dependencies are not locked")
    require(dependencies["lock_sha256"] == LOCK_SHA256,
            "build receipt lock digest differs")
    require(dependencies["snapshot"] == APT_SNAPSHOT,
            "build receipt apt snapshot differs")
    require(dependencies["direct_packages"] == DIRECT_PACKAGES,
            "build receipt direct package set differs")
    require(isinstance(dependencies["installed_packages"], dict),
            "installed package inventory is not an object")
    for package, version in DIRECT_PACKAGES.items():
        require(dependencies["installed_packages"].get(package) == version,
                f"installed package differs from lock: {package}")

    tools = strict_object(
        receipt["tools"], {"cmake", "compiler", "git", "ninja", "python"},
        "build_receipt.tools",
    )
    require(all(isinstance(value, str) and value.strip()
                for value in tools.values()), "build tool identity is missing")
    require(receipt["commands"] == {
        "configure": ["cmake", "-S", "${SOURCE}", "-B", "${BUILD}",
                      *CMAKE_OPTIONS],
        "build": ["cmake", "--build", "${BUILD}", "--target", "bitcoind",
                  "--parallel", "4"],
        "matrix": "exact tests/compatibility/legacy/run_matrix.py at technical_target.commit",
    }, "build receipt commands differ")
    sources = strict_object(receipt["sources"], set(BUILD_ROLES),
                            "build_receipt.sources")
    require_identity(sources["baseline"], BASELINE_COMMIT, BASELINE_TREE,
                     "build_receipt.sources.baseline")
    require_identity(sources["candidate"], CANDIDATE_COMMIT, CANDIDATE_TREE,
                     "build_receipt.sources.candidate")
    builds = strict_object(receipt["builds"], set(BUILD_ROLES),
                           "build_receipt.builds")
    for role in BUILD_ROLES:
        build = strict_object(
            builds[role], {"bitcoind_bytes", "bitcoind_sha256", "config_sha256"},
            f"build_receipt.builds.{role}",
        )
        require(isinstance(build["bitcoind_bytes"], int) and
                build["bitcoind_bytes"] > 0,
                f"build_receipt.builds.{role}.bitcoind_bytes differs")
        require_hex(build["bitcoind_sha256"], HEX64,
                    f"build_receipt.builds.{role}.bitcoind_sha256")
        require_hex(build["config_sha256"], HEX64,
                    f"build_receipt.builds.{role}.config_sha256")
    require_hex(receipt["matrix_report_sha256"], HEX64,
                "build_receipt.matrix_report_sha256")
    require(receipt["claims"] == {
        "build_reproducibility": "not_claimed",
        "operator_binary_equivalence": "not_claimed",
        "mainnet_coexistence": "not_claimed",
    }, "build receipt claims differ")
    require(receipt["privacy"] == {
        "host_specific_absolute_paths_retained": False,
        "parent_environment_retained": False,
        "raw_process_output_retained": False,
    }, "build receipt privacy contract differs")
    return not local_values


def compare_reports(observed_path: Path, reproduced_path: Path,
                    receipt_path: Path) -> dict[str, Any]:
    require(sha256_file(observed_path) == OBSERVED_REPORT_SHA256,
            "observed report digest differs")
    observed = load_json(observed_path)
    reproduced = load_json(reproduced_path)
    observed_projection = semantic_projection(observed)
    reproduced_projection = semantic_projection(reproduced)
    observed_projection_sha = sha256_bytes(canonical_bytes(observed_projection))
    require(observed_projection_sha == OBSERVED_PROJECTION_SHA256,
            "observed semantic projection implementation drifted")
    reproduced_projection_sha = sha256_bytes(canonical_bytes(reproduced_projection))
    require(observed_projection == reproduced_projection,
            "semantic projections differ")
    receipt = load_json(receipt_path)
    hosted_main_run = validate_build_receipt(receipt)
    require(receipt.get("matrix_report_sha256") == sha256_file(reproduced_path),
            "build receipt does not bind the reproduced report")
    require(receipt.get("sources") == {
        "baseline": reproduced["change_identity"]["baseline"],
        "candidate": reproduced["change_identity"]["candidate"],
    }, "build receipt source identities differ")
    require(receipt.get("builds") == environment_projection(reproduced)["builds"],
            "build receipt does not bind reproduced binaries")
    differences = differing_pointers(
        environment_projection(observed), environment_projection(reproduced)
    )
    return {
        "schema": "ergon-legacy-reproduction-comparison/v1",
        "comparison_result": "semantic-match",
        "proposed_knowledge_status":
            "Reproduced" if hosted_main_run else "Observed",
        "promotion_authority": "external-review-required",
        "execution_context": (
            "independent-github-hosted-main"
            if hosted_main_run else "maintainer-local-preflight"
        ),
        "evidence_ceiling": "assembled_component",
        "technical_target": {
            "commit": CANDIDATE_COMMIT,
            "tree": CANDIDATE_TREE,
            "accepted_record_sha256": ACCEPTED_RECORD_SHA256,
        },
        "observed": {
            "report_sha256": sha256_file(observed_path),
            "projection_sha256": observed_projection_sha,
        },
        "reproduced": {
            "report_sha256": sha256_file(reproduced_path),
            "projection_sha256": reproduced_projection_sha,
            "build_receipt_sha256": sha256_file(receipt_path),
        },
        "semantic_equal": True,
        "allowed_environmental_difference_pointers": differences,
        "claims": {
            "behavioral_matrix_reproduced": hosted_main_run,
            "build_reproducibility": "not_claimed",
            "operator_binary_equivalence": "not_claimed",
            "historical_chain_replay": "not_claimed",
            "public_network_peering": "not_claimed",
            "mainnet_coexistence": "not_claimed",
        },
        "privacy": {
            "host_specific_absolute_paths_retained": False,
            "parent_environment_retained": False,
            "raw_process_output_retained": False,
        },
    }


def run_reproduction(repository_root: Path, work_root: Path,
                     output_dir: Path) -> None:
    repository_root = repository_root.resolve(strict=True)
    work_root = work_root.resolve(strict=False)
    output_dir = output_dir.resolve(strict=True)
    require(not work_root.exists(), "work root must not exist")
    require(output_dir.is_dir() and not any(output_dir.iterdir()),
            "output directory must exist and be empty")
    require(repository_root not in (work_root, output_dir), "roots must differ")
    lock = load_json(repository_root / LOCK_RELATIVE_PATH)
    validate_lock(lock)
    observed_path = repository_root / REPORT_RELATIVE_PATH
    require(sha256_file(observed_path) == OBSERVED_REPORT_SHA256,
            "public observed report differs")
    work_root.mkdir(mode=0o700, parents=True)
    home = work_root / "home"
    temp = work_root / "tmp"
    home.mkdir(mode=0o700)
    temp.mkdir(mode=0o700)
    env = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "HOME": str(home),
        "LANG": "C",
        "LC_ALL": "C",
        "NO_COLOR": "1",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "TERM": "dumb",
        "TMPDIR": str(temp),
        "TZ": "UTC",
    }
    baseline_source = work_root / "baseline-source"
    candidate_source = work_root / "candidate-source"
    baseline_build = work_root / "baseline-build"
    candidate_build = work_root / "candidate-build"
    reproduced_path = output_dir / "legacy-compatibility-reproduced.json"
    receipt_path = output_dir / "build-provenance.json"
    comparison_path = output_dir / "legacy-compatibility-comparison.json"
    try:
        clone_exact(BASELINE_URL, BASELINE_COMMIT, BASELINE_TREE,
                    baseline_source, env)
        clone_exact(CANDIDATE_URL, CANDIDATE_COMMIT, CANDIDATE_TREE,
                    candidate_source, env, fetch_main=True)
        build_role(baseline_source, baseline_build,
                   lock["build"]["cmake_options"],
                   lock["build"]["parallel_jobs"], env)
        build_role(candidate_source, candidate_build,
                   lock["build"]["cmake_options"],
                   lock["build"]["parallel_jobs"], env)
        matrix = candidate_source / "tests/compatibility/legacy/run_matrix.py"
        run_checked([
            "python3", "-B", str(Path(__file__).resolve(strict=True)),
            "matrix-driver", f"--matrix={matrix}", "--",
            f"--baseline-source={baseline_source}",
            f"--candidate-source={candidate_source}",
            f"--expected-candidate-commit={CANDIDATE_COMMIT}",
            f"--expected-candidate-tree={CANDIDATE_TREE}",
            f"--expected-integration-parent-commit={INTEGRATION_PARENT_COMMIT}",
            f"--expected-integration-parent-tree={INTEGRATION_PARENT_TREE}",
            f"--expected-accepted-record-sha256={ACCEPTED_RECORD_SHA256}",
            f"--expected-reviewer-identity={REVIEWER_IDENTITY}",
            f"--expected-decision-date={DECISION_DATE}",
            f"--baseline-build={baseline_build}",
            f"--candidate-build={candidate_build}",
            f"--work-root={work_root / 'matrix-work'}",
            f"--report={reproduced_path}",
        ], env)
        receipt = create_build_receipt(
            lock, baseline_source, candidate_source, baseline_build,
            candidate_build, reproduced_path,
            repository_root / LOCK_RELATIVE_PATH, env,
        )
        write_json_exclusive(receipt_path, receipt)
        comparison = compare_reports(observed_path, reproduced_path, receipt_path)
        write_json_exclusive(comparison_path, comparison)
    except BaseException:
        for output in (reproduced_path, receipt_path, comparison_path):
            try:
                output.unlink(missing_ok=True)
            except OSError:
                pass
        raise
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
    require(not work_root.exists(), "disposable work root survived cleanup")


def self_test(repository_root: Path) -> None:
    observed_path = repository_root / REPORT_RELATIVE_PATH
    require(sha256_file(observed_path) == OBSERVED_REPORT_SHA256,
            "self-test observed report digest differs")
    observed = load_json(observed_path)
    projection = semantic_projection(observed)
    require(sha256_bytes(canonical_bytes(projection)) ==
            OBSERVED_PROJECTION_SHA256,
            "self-test projection digest differs")

    environment_only = copy.deepcopy(observed)
    environment_only["public_git"]["git_version"] = "git version 99.0"
    environment_only["builds"]["baseline"]["bitcoind_sha256"] = "f" * 64
    require(semantic_projection(environment_only) == projection,
            "environment-only mutation changed semantic projection")
    require(differing_pointers(environment_projection(observed),
                               environment_projection(environment_only)) == [
        "/builds/baseline/bitcoind_sha256", "/git_version"
    ], "environment difference allowlist drifted")

    mutations = []
    extra = copy.deepcopy(observed)
    extra["unexpected"] = True
    mutations.append(extra)
    failed_execution = copy.deepcopy(observed)
    failed_execution["executions"][SCENARIOS[0]] = "fail"
    mutations.append(failed_execution)
    changed_identity = copy.deepcopy(observed)
    changed_identity["candidate_source"]["commit"] = "f" * 40
    mutations.append(changed_identity)
    changed_claim = copy.deepcopy(observed)
    changed_claim["claims"]["legacy_consensus_changed"] = True
    mutations.append(changed_claim)
    for index, mutation in enumerate(mutations):
        try:
            semantic_projection(mutation)
        except ReproductionError:
            continue
        raise ReproductionError(f"self-test accepted forbidden mutation {index}")

    lock = load_json(repository_root / LOCK_RELATIVE_PATH)
    validate_lock(lock)
    containerfile = (repository_root / CONTAINERFILE_RELATIVE_PATH).read_text(
        encoding="utf-8"
    )
    for binding in (
        lock["container"]["dockerfile_frontend"],
        lock["container"]["reference"],
        lock["apt"]["snapshot_url"],
        lock["apt"]["tls_bootstrap"]["sha256"],
        lock["apt"]["tls_bootstrap"]["url"],
    ):
        require(binding in containerfile,
                "Containerfile does not bind the reviewed lock")
    for package, version in lock["apt"]["direct_packages"].items():
        require(f"{package}={version}" in containerfile,
                f"Containerfile package differs from lock: {package}")

    receipt = {
        "schema": "ergon-legacy-build-provenance/v1",
        "knowledge_status": "Observed",
        "technical_target": {
            "commit": CANDIDATE_COMMIT,
            "tree": CANDIDATE_TREE,
            "accepted_record_sha256": ACCEPTED_RECORD_SHA256,
        },
        "freshness": {
            "fresh_github_hosted_vm_required": True,
            "fresh_public_clones": True,
            "preexisting_builds_used": False,
            "actions_cache_used": False,
            "datadir_reuse": False,
        },
        "environment": {
            "architecture": "aarch64",
            "container_image_id": "local-preflight",
            "container_manifest_digest": CONTAINER_MANIFEST_DIGEST,
            "github_runner": {
                "architecture": "local", "image_os": "local",
                "image_version": "local", "os": "local",
            },
            "kernel_release": "self-test",
            "os": {"id": "ubuntu", "version_id": "22.04"},
        },
        "public_ci": {
            key: "local-preflight"
            for key in ("job", "repository", "run_attempt", "run_id", "sha",
                        "workflow_ref")
        },
        "dependencies": {
            "locked": True,
            "lock_sha256": LOCK_SHA256,
            "snapshot": APT_SNAPSHOT,
            "direct_packages": DIRECT_PACKAGES,
            "installed_packages": DIRECT_PACKAGES,
        },
        "tools": {
            "cmake": "cmake self-test", "compiler": "c++ self-test",
            "git": "git self-test", "ninja": "ninja self-test",
            "python": "python self-test",
        },
        "commands": {
            "configure": ["cmake", "-S", "${SOURCE}", "-B", "${BUILD}",
                          *CMAKE_OPTIONS],
            "build": ["cmake", "--build", "${BUILD}", "--target", "bitcoind",
                      "--parallel", "4"],
            "matrix": "exact tests/compatibility/legacy/run_matrix.py at technical_target.commit",
        },
        "sources": {
            "baseline": observed["change_identity"]["baseline"],
            "candidate": observed["change_identity"]["candidate"],
        },
        "builds": environment_projection(observed)["builds"],
        "matrix_report_sha256": OBSERVED_REPORT_SHA256,
        "claims": {
            "build_reproducibility": "not_claimed",
            "operator_binary_equivalence": "not_claimed",
            "mainnet_coexistence": "not_claimed",
        },
        "privacy": {
            "host_specific_absolute_paths_retained": False,
            "parent_environment_retained": False,
            "raw_process_output_retained": False,
        },
    }
    require(validate_build_receipt(receipt) is False,
            "local receipt was treated as independent")
    hosted = copy.deepcopy(receipt)
    hosted["environment"]["container_image_id"] = "sha256:" + "a" * 64
    hosted["environment"]["github_runner"] = {
        "architecture": "ARM64", "image_os": "ubuntu22",
        "image_version": "self-test", "os": "Linux",
    }
    hosted["public_ci"] = {
        "job": "reproduce",
        "repository": "ErgonSurfer/ergon-lab",
        "run_attempt": "1",
        "run_id": "123",
        "sha": "a" * 40,
        "workflow_ref": (
            "ErgonSurfer/ergon-lab/.github/workflows/"
            "legacy-reproduction.yml@refs/heads/main"
        ),
    }
    require(validate_build_receipt(hosted) is True,
            "hosted main receipt was not recognized")
    receipt_mutations = []
    extra_receipt = copy.deepcopy(hosted)
    extra_receipt["unexpected"] = True
    receipt_mutations.append(extra_receipt)
    leaking_receipt = copy.deepcopy(hosted)
    leaking_receipt["privacy"]["raw_process_output_retained"] = True
    receipt_mutations.append(leaking_receipt)
    target_drift = copy.deepcopy(hosted)
    target_drift["technical_target"]["commit"] = "f" * 40
    receipt_mutations.append(target_drift)
    lock_drift = copy.deepcopy(hosted)
    lock_drift["dependencies"]["lock_sha256"] = "f" * 64
    receipt_mutations.append(lock_drift)
    overclaim = copy.deepcopy(hosted)
    overclaim["claims"]["mainnet_coexistence"] = "claimed"
    receipt_mutations.append(overclaim)
    branch_run = copy.deepcopy(hosted)
    branch_run["public_ci"]["workflow_ref"] = (
        "ErgonSurfer/ergon-lab/.github/workflows/"
        "legacy-reproduction.yml@refs/heads/topic"
    )
    receipt_mutations.append(branch_run)
    for index, mutation in enumerate(receipt_mutations):
        try:
            validate_build_receipt(mutation)
        except ReproductionError:
            continue
        raise ReproductionError(
            f"self-test accepted forbidden receipt mutation {index}"
        )

    class FakeMatrixError(RuntimeError):
        pass

    class FakeMatrixModule:
        MatrixError = FakeMatrixError
        MIXED_NODE_SUCCESS_MARKERS = tuple(
            item[1] for item in MIXED_NODE_LIFECYCLES
        )

        def __init__(self) -> None:
            self.validation_calls = 0
            self.last_required_markers: tuple[bytes, ...] = ()

        def validate_test_output(self, returncode: int, stdout: bytes,
                                 stderr: bytes,
                                 required_markers: tuple[bytes, ...] = ()) -> None:
            self.validation_calls += 1
            self.last_required_markers = required_markers
            if returncode != 0:
                raise FakeMatrixError("/private/tmp/secret-token")

        def run_execution(self, execution: dict[str, Any], *args: Any,
                          **kwargs: Any) -> None:
            if execution.get("id") == "mixed-node-coexistence":
                self.validate_test_output(
                    1,
                    b"ERGON_LEGACY_LIFECYCLE_OK full-reindex\n"
                    b"ERGON_LEGACY_LIFECYCLE_OK chainstate-reindex\n"
                    b"  File \"/tmp/tests/compatibility/legacy/"
                    b"feature_ergon_legacy_compatibility.py\", line 455, in "
                    b"default_protected_reorg_and_compare\n",
                    b"/private/tmp/secret-token\n"
                    b"  File \"/tmp/untrusted.py\", line 1, in run_test\n",
                    self.MIXED_NODE_SUCCESS_MARKERS,
                )
                return
            raise FakeMatrixError("/private/tmp/secret-token")

    fake_module = FakeMatrixModule()
    install_matrix_attribution(fake_module)
    for scenario in SCENARIOS:
        try:
            fake_module.run_execution({"id": scenario})
        except FakeMatrixError as error:
            expected = (
                "scenario mixed-node-coexistence failure-kind child-nonzero "
                "last-completed-lifecycle chainstate-reindex "
                "last-allowlisted-feature-frame "
                "default_protected_reorg_and_compare"
                if scenario == "mixed-node-coexistence"
                else f"scenario {scenario} failed"
            )
            require(str(error) == expected,
                    "matrix execution attribution differs")
            require("secret-token" not in str(error),
                    "matrix execution attribution exposed raw output")
        else:
            raise ReproductionError("matrix execution failure was accepted")
    fake_module.validate_test_output(
        0, b"Tests successful\n", b"", fake_module.MIXED_NODE_SUCCESS_MARKERS
    )
    require(fake_module.validation_calls == 2 and
            fake_module.last_required_markers is
            fake_module.MIXED_NODE_SUCCESS_MARKERS,
            "matrix output validator invocation count differs")

    for count in range(len(MIXED_NODE_LIFECYCLES) + 1):
        output = b"\n".join(
            b"2026-09-04 TestFramework (INFO): " + item[1]
            for item in MIXED_NODE_LIFECYCLES[:count]
        )
        expected_lifecycle = (
            "none" if count == 0 else MIXED_NODE_LIFECYCLES[count - 1][0]
        )
        lifecycle, _ = classify_mixed_node_output(output, b"")
        require(lifecycle == expected_lifecycle,
                "mixed-node lifecycle prefix classification differs")

    invalid_marker_outputs = (
        (b"INFO: " + MIXED_NODE_LIFECYCLES[0][1] + b"\n" +
         b"INFO: " + MIXED_NODE_LIFECYCLES[0][1] + b"\n",
         "invalid-marker-state"),
        (MIXED_NODE_LIFECYCLES[0][1] + b" " +
         MIXED_NODE_LIFECYCLES[0][1], "invalid-marker-state"),
        (MIXED_NODE_LIFECYCLES[1][1] + b"\n", "invalid-marker-state"),
        (MIXED_NODE_LIFECYCLES[1][1] + b"\n" +
         MIXED_NODE_LIFECYCLES[0][1] + b"\n", "invalid-marker-state"),
    )
    for output, expected_lifecycle in invalid_marker_outputs:
        lifecycle, _ = classify_mixed_node_output(output, b"")
        require(lifecycle == expected_lifecycle,
                "mixed-node lifecycle classification differs")

    _, frame = classify_mixed_node_output(
        b"  File \"/work/tests/compatibility/legacy/"
        b"feature_ergon_legacy_compatibility.py\", line 200, in node_snapshot\n"
        b"  File \"/work/tests/compatibility/legacy/"
        b"feature_ergon_legacy_compatibility.py\", line 232, in mine_and_compare\n"
        b"  File \"/work/tests/compatibility/legacy/"
        b"feature_ergon_legacy_compatibility.py\", line 999, in hostile_unknown\n",
        b"/private/tmp/secret-token",
    )
    require(frame == "mine_and_compare",
            "mixed-node feature-frame classification differs")
    print("Legacy reproduction self-tests passed.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    self_parser = subparsers.add_parser("self-test", allow_abbrev=False)
    self_parser.add_argument("--repository-root", default=None)
    run_parser = subparsers.add_parser("run", allow_abbrev=False)
    run_parser.add_argument("--repository-root", required=True)
    run_parser.add_argument("--work-root", required=True)
    run_parser.add_argument("--output-dir", required=True)
    compare_parser = subparsers.add_parser("compare", allow_abbrev=False)
    compare_parser.add_argument("--observed-report", required=True)
    compare_parser.add_argument("--reproduced-report", required=True)
    compare_parser.add_argument("--build-receipt", required=True)
    compare_parser.add_argument("--output", required=True)
    driver_parser = subparsers.add_parser("matrix-driver", allow_abbrev=False)
    driver_parser.add_argument("--matrix", required=True)
    driver_parser.add_argument("matrix_args", nargs=argparse.REMAINDER)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "self-test":
        root = (Path(args.repository_root).resolve(strict=True)
                if args.repository_root else Path(__file__).resolve().parents[2])
        self_test(root)
    elif args.command == "run":
        run_reproduction(Path(args.repository_root), Path(args.work_root),
                         Path(args.output_dir))
    elif args.command == "compare":
        comparison = compare_reports(
            Path(args.observed_report).resolve(strict=True),
            Path(args.reproduced_report).resolve(strict=True),
            Path(args.build_receipt).resolve(strict=True),
        )
        write_json_exclusive(Path(args.output).resolve(strict=False), comparison)
    else:
        matrix_args = args.matrix_args
        if matrix_args[:1] == ["--"]:
            matrix_args = matrix_args[1:]
        run_matrix_driver(Path(args.matrix), matrix_args)


if __name__ == "__main__":
    try:
        main()
    except ReproductionError as error:
        print(f"legacy reproduction failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    except (OSError, subprocess.SubprocessError):
        print("legacy reproduction failed: operating-system error", file=sys.stderr)
        raise SystemExit(1)

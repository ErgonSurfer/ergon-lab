#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build two exact sources and run the bounded passive mainnet smoke."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
from unittest import mock


BASELINE_URL = "https://github.com/Ergon-moe/Bitcoin-Static.git"
BASELINE_COMMIT = "2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b"
BASELINE_TREE = "8a74bb952c2137156214b9fe5888c494bd77aeca"
CANDIDATE_URL = "https://github.com/ErgonSurfer/ergon-lab.git"
CANDIDATE_COMMIT = "d228bd8720631f0bbaccc7e222cc3b409f04a635"
CANDIDATE_TREE = "f7821808d24d7bdb7b42b58a413e2ba4c8e335c2"
PARENT_COMMIT = "0f763359ee38f5ce24e5c47ad9af51ca28b73bf4"
PARENT_TREE = "7298617b3a1a9f364072877fe5cf2d0a49492072"
PUBLIC_ROOT_COMMIT = "5bcdba149119aa9035830e069d1cae1d9bcddfb4"
SIGNER_PRINCIPAL = "153525861+ErgonSurfer@users.noreply.github.com"
SIGNER_FINGERPRINT = "SHA256:kC/Vx9WJW9ufy4Ttg5tKK6Cw8jEuV9ej2mRCLvZyU3Q"
SIGNER_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFN47Qs8VW9ty+v0tf31kv6pMpyOMxWWLXZ0Pv5MWVCI"
RECORD_PATH = Path("docs/engineering/changes/ergon-change-0020.json")
RECORD_SHA256 = "b9ecfcda83959feed82c0add77412174d8eb6fecc32ee8c00255ca59b4226b86"
RUNNER_PATH = Path("tests/compatibility/mainnet/run_passive_smoke.py")
RUNNER_SHA256 = "ac021360fd55b12f3629e093e7d80fe5ae8ab86b2433aec0f7b8cce6f80586db"
LOCK_PATH = Path("contrib/reproducibility/legacy-ubuntu22-arm64.lock.json")
LOCK_SHA256 = "a625d09aaa97e54f5fa7487f1000b139dcdf93472bc984425a25e2bf3777eab0"
CONTAINERFILE_PATH = Path(
    "contrib/reproducibility/legacy-ubuntu22-arm64.Containerfile"
)
CONTAINERFILE_SHA256 = "f87b5f51a32d5f193f6fa5ffa0422d85774d663f3c3bcfed8b76577e20a13410"
CONTAINER_MANIFEST = (
    "sha256:2edbbc5dc405e9612ba3584ce95480277e3eb374407b5505fe26f17df77c7dbc"
)
APT_SNAPSHOT = "20260901T000000Z"
SCENARIO_ID = "mixed-node-coexistence"
PROFILE = "mainnet-passive-independent-prefix-smoke"
EXPECTED_GENESIS = (
    "000000070e37bfee7e84b94f997f38155a85b22172f5ca25fd4eb3d64c5ad7c5"
)
ROLES = ("baseline", "candidate")
FAILURE_ROLES = frozenset((*ROLES, "comparison", "harness"))
FAILURE_RESULT_BY_REASON = {
    "binary-input-invalid": "harness-error",
    "binary-identity-changed": "harness-error",
    "cleanup-failed": "harness-error",
    "datadir-alias": "harness-error",
    "datadir-identity-changed": "harness-error",
    "network-surface-open": "harness-error",
    "port-alias": "harness-error",
    "process-alias": "harness-error",
    "report-contract-invalid": "harness-error",
    "rpc-contract-invalid": "harness-error",
    "unexpected-error": "harness-error",
    "bounded-prefix-incomplete": "inconclusive",
    "disk-limit-exceeded": "inconclusive",
    "node-exited-before-rpc": "inconclusive",
    "node-exited-nonzero": "inconclusive",
    "timeout": "inconclusive",
    "genesis-mismatch": "contradiction",
    "mainnet-chain-mismatch": "contradiction",
    "snapshot-mismatch": "contradiction",
}
FAILURE_RESULT_BY_EXIT = {
    1: "harness-error",
    2: "inconclusive",
    3: "contradiction",
}
DIAGNOSTIC_UNAVAILABLE = (
    "ERGON_MAINNET_SMOKE_FAILURE role=harness disposition=harness-error "
    "reason_code=diagnostic-unavailable"
)
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")


class ReproductionError(RuntimeError):
    """A fail-closed reproduction invariant failed."""


class DiagnosedSmokeFailure(ReproductionError):
    """The bounded child failure was already projected without raw output."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ReproductionError(message)


def strict_object(value: Any, keys: set[str], context: str) -> dict[str, Any]:
    require(isinstance(value, dict), f"{context} must be an object")
    require(set(value) == keys, f"{context} fields differ")
    return value


def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON field: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as file:
            value = json.load(file, object_pairs_hook=reject_duplicates)
    except (OSError, json.JSONDecodeError) as error:
        raise ReproductionError(f"cannot read JSON: {path.name}") from error
    require(isinstance(value, dict), f"{path.name} must contain an object")
    return value


def write_json_exclusive(path: Path, value: dict[str, Any]) -> None:
    require(not path.exists(), f"output already exists: {path.name}")
    try:
        with path.open("x", encoding="utf-8", newline="\n") as file:
            json.dump(value, file, indent=2, sort_keys=True)
            file.write("\n")
    except OSError as error:
        raise ReproductionError(f"cannot write JSON: {path.name}") from error


def remove_outputs(paths: tuple[Path, ...]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def remove_work_root(work_root: Path, outputs: tuple[Path, ...],
                     remover: Any = shutil.rmtree) -> None:
    remover(work_root, ignore_errors=True)
    if work_root.exists():
        remove_outputs(outputs)
        raise ReproductionError("work root survived cleanup")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ReproductionError(f"cannot hash file: {path.name}") from error
    return digest.hexdigest()


def run_checked(command: list[str], env: dict[str, str]) -> None:
    try:
        subprocess.run(
            command, check=True, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReproductionError(f"command failed: {Path(command[0]).name}") from error


def run_smoke(command: list[str], env: dict[str, str]) -> int:
    try:
        result = subprocess.run(
            command, check=False, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise ReproductionError("command failed: python3") from error
    return result.returncode


def capture(command: list[str], env: dict[str, str]) -> str:
    try:
        result = subprocess.run(
            command, check=True, env=env, text=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReproductionError(f"command failed: {Path(command[0]).name}") from error
    return result.stdout.strip()


def git_identity(path: Path, env: dict[str, str]) -> dict[str, Any]:
    return {
        "clean": capture(["git", "-C", str(path), "status", "--porcelain"], env) == "",
        "commit": capture(["git", "-C", str(path), "rev-parse", "HEAD^{commit}"], env),
        "tree": capture(["git", "-C", str(path), "rev-parse", "HEAD^{tree}"], env),
    }


def clone_exact(url: str, commit: str, tree: str, destination: Path,
                env: dict[str, str]) -> None:
    require(not destination.exists(), "clone destination exists")
    run_checked(["git", "clone", "--no-tags", url, str(destination)], env)
    run_checked(["git", "-C", str(destination), "checkout", "--detach", commit], env)
    require(git_identity(destination, env) == {
        "clean": True, "commit": commit, "tree": tree,
    }, "fresh source identity differs")


def validate_target(source: Path, env: dict[str, str]) -> None:
    parent = capture(["git", "-C", str(source), "rev-parse", "HEAD^"], env)
    parent_tree = capture(
        ["git", "-C", str(source), "rev-parse", "HEAD^^{tree}"], env
    )
    require(parent == PARENT_COMMIT and parent_tree == PARENT_TREE,
            "candidate parent identity differs")
    require(sha256_file(source / RECORD_PATH) == RECORD_SHA256,
            "reviewed record bytes differ")
    require(sha256_file(source / RUNNER_PATH) == RUNNER_SHA256,
            "reviewed runner bytes differ")


def verify_public_history(source: Path, allowed_signers: Path,
                          env: dict[str, str]) -> dict[str, Any]:
    allowed_signers.write_text(
        f"{SIGNER_PRINCIPAL} {SIGNER_KEY}\n", encoding="ascii", newline="\n"
    )
    require(allowed_signers.stat().st_size == 128 and
            sha256_file(allowed_signers) ==
            "4df5711122f5777dbaea2480d2d1fdef81ea294a79d835ab0173ae0065dfa738",
            "allowed-signers identity differs")
    run_checked([
        "git", "-C", str(source), "merge-base", "--is-ancestor",
        PUBLIC_ROOT_COMMIT, CANDIDATE_COMMIT,
    ], env)
    identities = {
        "public_root": (PUBLIC_ROOT_COMMIT, BASELINE_TREE),
        "integration_parent": (PARENT_COMMIT, PARENT_TREE),
        "candidate": (CANDIDATE_COMMIT, CANDIDATE_TREE),
    }
    result = {}
    for role, (commit, tree) in identities.items():
        require(capture(["git", "-C", str(source), "rev-parse",
                         f"{commit}^{{tree}}"], env) == tree,
                f"{role} tree differs")
        run_checked([
            "git", "-C", str(source), "-c",
            f"gpg.ssh.allowedSignersFile={allowed_signers}",
            "verify-commit", commit,
        ], env)
        signature = capture([
            "git", "-C", str(source), "-c",
            f"gpg.ssh.allowedSignersFile={allowed_signers}",
            "log", "-1", "--format=%G?%n%GF%n%GS", commit,
        ], env).splitlines()
        require(signature == ["G", SIGNER_FINGERPRINT, SIGNER_PRINCIPAL],
                f"{role} signature identity differs")
        result[role] = {"commit": commit, "tree": tree, "status": "G"}
    return {
        "format": "ssh", "key_algorithm": "ED25519",
        "principal": SIGNER_PRINCIPAL, "fingerprint": SIGNER_FINGERPRINT,
        "signatures": result,
    }


def validate_lock(repository_root: Path) -> dict[str, Any]:
    lock_path = repository_root / LOCK_PATH
    require(sha256_file(lock_path) == LOCK_SHA256, "build lock bytes differ")
    require(sha256_file(repository_root / CONTAINERFILE_PATH) ==
            CONTAINERFILE_SHA256, "Containerfile bytes differ")
    lock = load_json(lock_path)
    strict_object(lock, {"$comment", "schema", "runner", "container", "apt",
                         "build", "claims"}, "lock")
    require(lock["schema"] == "ergon-legacy-reproduction-lock/v1",
            "build lock schema differs")
    require(lock["runner"] == {
        "architecture": "arm64", "github_hosted_label": "ubuntu-22.04-arm",
        "runner_image_mutable": True,
    }, "runner lock differs")
    require(lock["container"]["manifest_digest"] == CONTAINER_MANIFEST and
            lock["container"]["platform"] == "linux/arm64",
            "container lock differs")
    require(lock["apt"]["archive_signature_verification"] is True and
            lock["apt"]["snapshot"] == APT_SNAPSHOT,
            "apt lock differs")
    require(lock["build"] == {
        "cmake_options": [
            "-GNinja", "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
            "-DBUILD_BITCOIN_QT=OFF", "-DBUILD_BITCOIN_WALLET=ON",
            "-DBUILD_BITCOIN_ZMQ=OFF", "-DENABLE_UPNP=OFF",
        ],
        "parallel_jobs": 4,
        "target": "bitcoind",
    }, "build contract differs")
    require(lock["claims"] == {
        "build_reproducibility": "not_claimed",
        "reproduction_dependencies_locked": True,
        "runner_vm_image_locked": False,
    }, "build claims differ")
    return lock


def build_role(source: Path, build: Path, lock: dict[str, Any],
               env: dict[str, str]) -> None:
    require(not build.exists(), "build root exists")
    run_checked([
        "cmake", "-S", str(source), "-B", str(build),
        *lock["build"]["cmake_options"],
    ], env)
    run_checked([
        "cmake", "--build", str(build), "--target", "bitcoind",
        "--parallel", str(lock["build"]["parallel_jobs"]),
    ], env)


def binary_build(root: Path) -> dict[str, Any]:
    binary = root / "src" / "bitcoind"
    config = root / "test" / "config.ini"
    require(binary.is_file() and os.access(binary, os.X_OK) and config.is_file(),
            "build output is incomplete")
    return {
        "bitcoind_bytes": binary.stat().st_size,
        "bitcoind_sha256": sha256_file(binary),
        "config_sha256": sha256_file(config),
    }


def validate_smoke_report(report: dict[str, Any],
                          builds: dict[str, dict[str, Any]]) -> None:
    strict_object(report, {
        "schema", "scenario_id", "profile", "result", "reason_code",
        "knowledge_status", "evidence_ceiling", "scope", "binaries",
        "cleanup", "claims", "privacy", "limitations", "observations",
    }, "smoke report")
    require(report["schema"] == "ergon-mainnet-passive-smoke/v2" and
            report["scenario_id"] == SCENARIO_ID and report["profile"] == PROFILE,
            "smoke identity differs")
    require(report["result"] == "success" and
            report["reason_code"] == "bounded-mainnet-prefix-matched" and
            report["knowledge_status"] == "Observed" and
            report["evidence_ceiling"] == "assembled_runtime",
            "smoke result is not promotable")
    require(report["scope"] == {
        "maximum_accepted_exit_height": 416,
        "stop_trigger_height": 288,
        "complete_initial_block_download": False,
        "network_source_by_role": {
            "baseline": "public-mainnet",
            "candidate": "public-mainnet",
        },
    }, "smoke scope differs")
    binaries = strict_object(report["binaries"], set(ROLES), "smoke binaries")
    for role in ROLES:
        require(binaries[role] == {
            "bytes": builds[role]["bitcoind_bytes"],
            "sha256": builds[role]["bitcoind_sha256"],
            "binary_to_source_provenance": "external-build-record-required",
        }, f"{role} binary binding differs")
    require(report["cleanup"] == {
        "complete": True, "processes_survived": False,
        "work_root_survived": False,
    }, "smoke cleanup differs")
    require(report["claims"] == {
        "bounded_mainnet_prefix_match": True,
        "independent_public_prefix_acquisition": True,
        "current_tip_agreement": "not_claimed",
        "full_historical_replay": "not_claimed",
        "mainnet_coexistence": "not_claimed",
        "operator_binary_parity": "not_claimed",
        "sustained_operation": "not_claimed",
    }, "smoke claims differ")
    require(report["privacy"] == {
        "host_specific_absolute_paths_retained": False,
        "parent_environment_retained": False,
        "peer_addresses_retained": False,
        "raw_process_output_retained": False,
    }, "smoke privacy differs")
    observations = strict_object(report["observations"], {
        "baseline_clean_restart", "baseline_public_prefix_acquired",
        "candidate_clean_restart", "candidate_public_prefix_acquired",
        "datadirs_distinct", "ports_distinct", "processes_distinct",
        "roles_equal", "shared_checkpoint",
    }, "smoke observations")
    require(all(observations[key] is True for key in observations
                if key != "shared_checkpoint"), "smoke observation differs")
    checkpoint = strict_object(observations["shared_checkpoint"], {
        "chain", "checkpoint_height", "genesis", "blockhash", "raw_header",
        "chainwork",
    }, "shared checkpoint")
    require(checkpoint["chain"] == "main" and
            checkpoint["checkpoint_height"] == 288 and
            checkpoint["genesis"] == EXPECTED_GENESIS,
            "shared checkpoint identity differs")
    for key, pattern in (("blockhash", HEX64), ("chainwork", HEX64)):
        require(isinstance(checkpoint[key], str) and
                pattern.fullmatch(checkpoint[key]) is not None,
                f"shared checkpoint {key} differs")
    require(isinstance(checkpoint["raw_header"], str) and
            re.fullmatch(r"[0-9a-f]{160}", checkpoint["raw_header"]) is not None,
            "shared checkpoint header differs")
    require(isinstance(report["limitations"], list) and
            len(report["limitations"]) == 5 and
            all(isinstance(item, str) and item for item in report["limitations"]),
            "smoke limitations differ")


def validate_failure_report(report: dict[str, Any],
                            builds: dict[str, dict[str, Any]],
                            exit_code: int) -> tuple[str, str, str]:
    require(isinstance(report, dict), "failure report must be an object")
    result = report.get("result")
    reason_code = report.get("reason_code")
    failure_role = report.get("failure_role")
    require(isinstance(result, str) and result in FAILURE_RESULT_BY_EXIT.values(),
            "failure result differs")
    require(isinstance(reason_code, str) and
            FAILURE_RESULT_BY_REASON.get(reason_code) == result,
            "failure reason differs")
    require(isinstance(failure_role, str) and failure_role in FAILURE_ROLES,
            "failure role differs")
    require((reason_code == "snapshot-mismatch") ==
            (failure_role == "comparison"),
            "failure comparison attribution differs")
    require(FAILURE_RESULT_BY_EXIT.get(exit_code) == result,
            "failure exit differs")
    limitations = report.get("limitations")
    require(isinstance(limitations, list) and len(limitations) == 5 and
            all(isinstance(item, str) and item for item in limitations),
            "failure limitations differ")
    expected = sample_failure_report(
        builds, failure_role, result, reason_code
    )
    expected["limitations"] = limitations
    require(report == expected, "failure report contract differs")
    return failure_role, result, reason_code


def consume_failure_report(report_path: Path, receipt_path: Path,
                           builds: dict[str, dict[str, Any]],
                           exit_code: int) -> str:
    diagnostic = DIAGNOSTIC_UNAVAILABLE
    try:
        require(report_path.is_file() and not report_path.is_symlink() and
                report_path.stat().st_size <= 64 * 1024,
                "failure report is too large")
        report = load_json(report_path)
        role, result, reason_code = validate_failure_report(
            report, builds, exit_code
        )
        diagnostic = (
            f"ERGON_MAINNET_SMOKE_FAILURE role={role} "
            f"disposition={result} reason_code={reason_code}"
        )
    except (OSError, UnicodeError, ReproductionError):
        pass
    remove_outputs((report_path, receipt_path))
    require(not report_path.exists() and not receipt_path.exists(),
            "failure output survived removal")
    require(not any(report_path.parent.iterdir()),
            "failure output directory is not empty")
    return diagnostic


def github_identity() -> dict[str, str]:
    fields = {
        "job": "ERGON_GITHUB_JOB", "repository": "ERGON_GITHUB_REPOSITORY",
        "run_attempt": "ERGON_GITHUB_RUN_ATTEMPT",
        "run_id": "ERGON_GITHUB_RUN_ID", "sha": "ERGON_GITHUB_SHA",
        "workflow_ref": "ERGON_GITHUB_WORKFLOW_REF",
    }
    result = {key: os.environ.get(name, "") for key, name in fields.items()}
    require(result["job"] == "reproduce" and
            result["repository"] == "ErgonSurfer/ergon-lab" and
            result["run_id"].isdigit() and result["run_attempt"].isdigit() and
            HEX40.fullmatch(result["sha"]) is not None and
            result["workflow_ref"] ==
            "ErgonSurfer/ergon-lab/.github/workflows/mainnet-passive-smoke.yml@refs/heads/main",
            "hosted workflow identity differs")
    return result


def direct_package_versions(lock: dict[str, Any], env: dict[str, str]) -> dict[str, str]:
    packages = sorted(lock["apt"]["direct_packages"])
    output = capture(["dpkg-query", "-W", "-f=${Package}\t${Version}\n", *packages], env)
    installed = dict(line.split("\t", 1) for line in output.splitlines())
    require(installed == lock["apt"]["direct_packages"],
            "installed direct packages differ")
    return dict(sorted(installed.items()))


def make_receipt(lock: dict[str, Any], sources: dict[str, Path],
                 build_roots: dict[str, Path], report_path: Path,
                 authentication: dict[str, Any],
                 env: dict[str, str]) -> dict[str, Any]:
    require(platform.machine() == "aarch64", "host architecture differs")
    image_id = os.environ.get("ERGON_CONTAINER_IMAGE_ID", "")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is not None,
            "container image identity differs")
    runner = {
        "architecture": os.environ.get("ERGON_RUNNER_ARCH", ""),
        "image_os": os.environ.get("ERGON_IMAGE_OS", ""),
        "image_version": os.environ.get("ERGON_IMAGE_VERSION", ""),
        "os": os.environ.get("ERGON_RUNNER_IMAGE_OS", ""),
    }
    require(runner["architecture"] == "ARM64" and runner["os"] == "Linux" and
            runner["image_os"] not in ("", "unavailable") and
            runner["image_version"] not in ("", "unavailable"),
            "hosted runner identity differs")
    os_release = {}
    for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            os_release[key] = value.strip('"')
    builds = {role: binary_build(build_roots[role]) for role in ROLES}
    report = load_json(report_path)
    validate_smoke_report(report, builds)
    source_identities = {role: git_identity(sources[role], env) for role in ROLES}
    require(source_identities == {
        "baseline": {"clean": True, "commit": BASELINE_COMMIT, "tree": BASELINE_TREE},
        "candidate": {"clean": True, "commit": CANDIDATE_COMMIT, "tree": CANDIDATE_TREE},
    }, "post-run source identity differs")
    return {
        "schema": "ergon-mainnet-passive-build-provenance/v1",
        "knowledge_status": "Observed",
        "evidence_ceiling": "assembled_runtime",
        "technical_target": {
            "commit": CANDIDATE_COMMIT, "tree": CANDIDATE_TREE,
            "parent_commit": PARENT_COMMIT, "parent_tree": PARENT_TREE,
            "record_sha256": RECORD_SHA256, "runner_sha256": RUNNER_SHA256,
        },
        "freshness": {
            "fresh_github_hosted_vm_required": True,
            "fresh_public_clones": True, "preexisting_builds_used": False,
            "actions_cache_used": False, "datadir_reuse": False,
        },
        "environment": {
            "architecture": platform.machine(),
            "container_image_id": image_id,
            "container_manifest_digest": CONTAINER_MANIFEST,
            "github_runner": runner,
            "kernel_release": platform.release(),
            "os": {"id": os_release.get("ID", ""),
                   "version_id": os_release.get("VERSION_ID", "")},
        },
        "public_ci": github_identity(),
        "dependencies": {
            "locked": True, "lock_sha256": LOCK_SHA256,
            "snapshot": APT_SNAPSHOT,
            "direct_packages": lock["apt"]["direct_packages"],
            "installed_direct_packages": direct_package_versions(lock, env),
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
            "smoke": "exact tests/compatibility/mainnet/run_passive_smoke.py at technical_target.commit",
        },
        "sources": source_identities,
        "source_authentication": authentication,
        "builds": builds,
        "smoke_report_sha256": sha256_file(report_path),
        "claims": {
            "bounded_mainnet_prefix_match": True,
            "independent_public_prefix_acquisition": True,
            "build_reproducibility": "not_claimed",
            "full_historical_replay": "not_claimed",
            "mainnet_coexistence": "not_claimed",
            "operator_binary_parity": "not_claimed",
            "sustained_operation": "not_claimed",
        },
        "privacy": {
            "host_specific_absolute_paths_retained": False,
            "parent_environment_retained": False,
            "peer_addresses_retained": False,
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
            "output directory must be empty")
    require(repository_root not in (work_root, output_dir), "roots must differ")
    lock = validate_lock(repository_root)
    work_root.mkdir(mode=0o700)
    home, temp = work_root / "home", work_root / "tmp"
    home.mkdir(mode=0o700)
    temp.mkdir(mode=0o700)
    env = {
        "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1", "HOME": str(home), "LANG": "C",
        "LC_ALL": "C", "NO_COLOR": "1",
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TERM": "dumb",
        "TMPDIR": str(temp), "TZ": "UTC",
    }
    sources = {role: work_root / f"{role}-source" for role in ROLES}
    builds = {role: work_root / f"{role}-build" for role in ROLES}
    report_path = output_dir / "mainnet-passive-smoke.json"
    receipt_path = output_dir / "build-provenance.json"
    outputs = (report_path, receipt_path)
    failure_diagnostic: str | None = None
    try:
        clone_exact(BASELINE_URL, BASELINE_COMMIT, BASELINE_TREE,
                    sources["baseline"], env)
        clone_exact(CANDIDATE_URL, CANDIDATE_COMMIT, CANDIDATE_TREE,
                    sources["candidate"], env)
        validate_target(sources["candidate"], env)
        authentication = verify_public_history(
            sources["candidate"], work_root / "allowed_signers", env
        )
        for role in ROLES:
            build_role(sources[role], builds[role], lock, env)
        pre_smoke_builds = {
            role: binary_build(builds[role]) for role in ROLES
        }
        smoke_exit = run_smoke([
            "python3", "-B", str(sources["candidate"] / RUNNER_PATH),
            f"--baseline-bitcoind={builds['baseline'] / 'src' / 'bitcoind'}",
            f"--candidate-bitcoind={builds['candidate'] / 'src' / 'bitcoind'}",
            f"--work-root={work_root / 'smoke-work'}",
            f"--report={report_path}",
        ], env)
        post_smoke_builds = {
            role: binary_build(builds[role]) for role in ROLES
        }
        require(post_smoke_builds == pre_smoke_builds,
                "post-smoke binary identity differs")
        if smoke_exit == 0:
            receipt = make_receipt(
                lock, sources, builds, report_path, authentication, env
            )
            write_json_exclusive(receipt_path, receipt)
            require(set(path.name for path in output_dir.iterdir()) == {
                report_path.name, receipt_path.name,
            }, "output artifact set differs")
        else:
            failure_diagnostic = consume_failure_report(
                report_path, receipt_path, post_smoke_builds, smoke_exit
            )
    except BaseException:
        remove_outputs(outputs)
        raise
    finally:
        remove_work_root(work_root, outputs)
    if failure_diagnostic is not None:
        print(failure_diagnostic, file=sys.stderr)
        raise DiagnosedSmokeFailure("bounded smoke did not succeed")


def sample_builds() -> dict[str, dict[str, Any]]:
    return {
        role: {"bitcoind_bytes": index + 1, "bitcoind_sha256": str(index) * 64,
               "config_sha256": str(index + 2) * 64}
        for index, role in enumerate(ROLES)
    }


def sample_report(builds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema": "ergon-mainnet-passive-smoke/v2",
        "scenario_id": SCENARIO_ID, "profile": PROFILE, "result": "success",
        "reason_code": "bounded-mainnet-prefix-matched",
        "knowledge_status": "Observed", "evidence_ceiling": "assembled_runtime",
        "scope": {
            "maximum_accepted_exit_height": 416, "stop_trigger_height": 288,
            "complete_initial_block_download": False,
            "network_source_by_role": {
                "baseline": "public-mainnet",
                "candidate": "public-mainnet",
            },
        },
        "binaries": {role: {
            "bytes": builds[role]["bitcoind_bytes"],
            "sha256": builds[role]["bitcoind_sha256"],
            "binary_to_source_provenance": "external-build-record-required",
        } for role in ROLES},
        "cleanup": {"complete": True, "processes_survived": False,
                    "work_root_survived": False},
        "claims": {
            "bounded_mainnet_prefix_match": True,
            "independent_public_prefix_acquisition": True,
            "current_tip_agreement": "not_claimed",
            "full_historical_replay": "not_claimed",
            "mainnet_coexistence": "not_claimed",
            "operator_binary_parity": "not_claimed",
            "sustained_operation": "not_claimed",
        },
        "privacy": {
            "host_specific_absolute_paths_retained": False,
            "parent_environment_retained": False,
            "peer_addresses_retained": False,
            "raw_process_output_retained": False,
        },
        "limitations": ["bounded"] * 5,
        "observations": {
            "baseline_clean_restart": True,
            "baseline_public_prefix_acquired": True,
            "candidate_clean_restart": True,
            "candidate_public_prefix_acquired": True,
            "datadirs_distinct": True, "ports_distinct": True,
            "processes_distinct": True, "roles_equal": True,
            "shared_checkpoint": {
                "chain": "main", "checkpoint_height": 288,
                "genesis": EXPECTED_GENESIS, "blockhash": "a" * 64,
                "raw_header": "b" * 160, "chainwork": "c" * 64,
            },
        },
    }


def sample_failure_report(builds: dict[str, dict[str, Any]], role: str,
                          result: str, reason_code: str) -> dict[str, Any]:
    report = sample_report(builds)
    report.pop("observations")
    report["result"] = result
    report["reason_code"] = reason_code
    report["failure_role"] = role
    report["knowledge_status"] = "Open Question"
    report["evidence_ceiling"] = "component"
    report["claims"]["bounded_mainnet_prefix_match"] = False
    report["claims"]["independent_public_prefix_acquisition"] = False
    return report


def self_test(repository_root: Path) -> None:
    validate_lock(repository_root)
    require(sha256_file(repository_root / RECORD_PATH) == RECORD_SHA256,
            "self-test record bytes differ")
    builds = sample_builds()
    report = sample_report(builds)
    validate_smoke_report(report, builds)
    mutations = []
    for path, value in (
        (("result",), "inconclusive"),
        (("scope", "stop_trigger_height"), 289),
        (("scope", "network_source_by_role", "candidate"), "baseline-loopback"),
        (("claims", "independent_public_prefix_acquisition"), False),
        (("claims", "mainnet_coexistence"), True),
        (("privacy", "peer_addresses_retained"), True),
        (("cleanup", "complete"), False),
        (("observations", "roles_equal"), False),
        (("observations", "candidate_public_prefix_acquired"), False),
        (("observations", "shared_checkpoint", "checkpoint_height"), 289),
        (("binaries", "candidate", "sha256"), "f" * 64),
    ):
        changed = copy.deepcopy(report)
        cursor: Any = changed
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        mutations.append(changed)
    extra = copy.deepcopy(report)
    extra["unexpected"] = True
    mutations.append(extra)
    for index, changed in enumerate(mutations):
        try:
            validate_smoke_report(changed, builds)
        except ReproductionError:
            continue
        raise ReproductionError(f"self-test accepted forbidden mutation {index}")

    failure_cases = (
        ("baseline", "inconclusive", "timeout", 2),
        ("candidate", "inconclusive", "bounded-prefix-incomplete", 2),
        ("comparison", "contradiction", "snapshot-mismatch", 3),
        ("harness", "harness-error", "unexpected-error", 1),
    )
    for role, result, reason_code, exit_code in failure_cases:
        failure = sample_failure_report(builds, role, result, reason_code)
        require(validate_failure_report(failure, builds, exit_code) ==
                (role, result, reason_code),
                "self-test failure diagnostic differs")

    base_failure = sample_failure_report(
        builds, "candidate", "inconclusive", "timeout"
    )
    failure_mutations: list[tuple[dict[str, Any], int]] = []
    for path, value, exit_code in (
        (("failure_role",), "unknown", 2),
        (("result",), "success", 2),
        (("reason_code",), "snapshot-mismatch", 2),
        (("cleanup", "complete"), False, 2),
    ):
        changed = copy.deepcopy(base_failure)
        cursor = changed
        for key in path[:-1]:
            cursor = cursor[key]
        cursor[path[-1]] = value
        failure_mutations.append((changed, exit_code))
    failure_mutations.append((copy.deepcopy(base_failure), 1))
    wrong_comparison = copy.deepcopy(base_failure)
    wrong_comparison["result"] = "contradiction"
    wrong_comparison["reason_code"] = "snapshot-mismatch"
    failure_mutations.append((wrong_comparison, 3))
    for index, (changed, exit_code) in enumerate(failure_mutations):
        try:
            validate_failure_report(changed, builds, exit_code)
        except ReproductionError:
            continue
        raise ReproductionError(
            f"self-test accepted invalid failure report {index}"
        )

    process = mock.Mock(returncode=2)
    with mock.patch.object(subprocess, "run", return_value=process) as invoked:
        require(run_smoke(["python3", "smoke.py"], {"LANG": "C"}) == 2,
                "self-test smoke exit differs")
    require(invoked.call_count == 1, "self-test smoke invocation differs")
    _, smoke_kwargs = invoked.call_args
    require(smoke_kwargs == {
        "check": False,
        "env": {"LANG": "C"},
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }, "self-test smoke output contract differs")

    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        output = temporary_root / "failure-output"
        output.mkdir()
        failure_path = output / "mainnet-passive-smoke.json"
        receipt_path = output / "build-provenance.json"
        write_json_exclusive(failure_path, base_failure)
        receipt_path.write_text("not-evidence\n", encoding="utf-8")
        diagnostic = consume_failure_report(
            failure_path, receipt_path, builds, 2
        )
        require(diagnostic ==
                "ERGON_MAINNET_SMOKE_FAILURE role=candidate "
                "disposition=inconclusive reason_code=timeout",
                "self-test trusted diagnostic differs")
        require(not any(output.iterdir()),
                "self-test retained trusted failure output")

        failure_path.write_text(
            '{"raw":"/private/tmp/secret peer.example"', encoding="utf-8"
        )
        diagnostic = consume_failure_report(
            failure_path, receipt_path, builds, 2
        )
        require(diagnostic == DIAGNOSTIC_UNAVAILABLE and
                "/private/tmp" not in diagnostic and
                "peer.example" not in diagnostic and not any(output.iterdir()),
                "self-test malformed diagnostic leaked")

        failure_path.write_bytes(b"\xff/private/tmp/secret")
        diagnostic = consume_failure_report(
            failure_path, receipt_path, builds, 2
        )
        require(diagnostic == DIAGNOSTIC_UNAVAILABLE and
                not any(output.iterdir()),
                "self-test invalid UTF-8 diagnostic leaked")

        dirty = copy.deepcopy(base_failure)
        dirty["cleanup"]["complete"] = False
        write_json_exclusive(failure_path, dirty)
        diagnostic = consume_failure_report(
            failure_path, receipt_path, builds, 2
        )
        require(diagnostic == DIAGNOSTIC_UNAVAILABLE and
                not any(output.iterdir()),
                "self-test trusted incomplete cleanup")

        diagnostic = consume_failure_report(
            failure_path, receipt_path, builds, 2
        )
        require(diagnostic == DIAGNOSTIC_UNAVAILABLE and
                not any(output.iterdir()),
                "self-test missing failure report differs")

        root = temporary_root / "late-cleanup-root"
        output = temporary_root / "late-cleanup-output"
        root.mkdir()
        output.mkdir()
        proposed = (output / "mainnet-passive-smoke.json",
                    output / "build-provenance.json")
        for path in proposed:
            path.write_text("{}\n", encoding="utf-8")
        try:
            remove_work_root(root, proposed, lambda *_args, **_kwargs: None)
        except ReproductionError as error:
            require(str(error) == "work root survived cleanup",
                    "late cleanup error differs")
        else:
            raise ReproductionError("late cleanup failure was accepted")
        require(not any(output.iterdir()),
                "late cleanup failure retained output artifacts")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("self-test", allow_abbrev=False)
    check.add_argument("--repository-root")
    run = subparsers.add_parser("run", allow_abbrev=False)
    run.add_argument("--repository-root", required=True)
    run.add_argument("--work-root", required=True)
    run.add_argument("--output-dir", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "self-test":
        root = (Path(args.repository_root).resolve(strict=True)
                if args.repository_root else Path(__file__).resolve().parents[2])
        self_test(root)
    else:
        run_reproduction(Path(args.repository_root), Path(args.work_root),
                         Path(args.output_dir))


if __name__ == "__main__":
    try:
        main()
    except DiagnosedSmokeFailure:
        raise SystemExit(1)
    except ReproductionError as error:
        print(f"mainnet reproduction failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    except (OSError, subprocess.SubprocessError):
        print("mainnet reproduction failed: operating-system error", file=sys.stderr)
        raise SystemExit(1)

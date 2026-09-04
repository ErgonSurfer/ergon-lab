#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Run the public legacy-compatibility matrix and emit sanitized evidence."""

import argparse
import ast
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time


BASE_COMMIT = "2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b"
BASE_TREE = "8a74bb952c2137156214b9fe5888c494bd77aeca"
PUBLIC_ROOT_COMMIT = "5bcdba149119aa9035830e069d1cae1d9bcddfb4"
PUBLIC_ROOT_TREE = BASE_TREE
PUBLIC_MAIN_REF = "refs/remotes/origin/main"
CHANGE_ID = "ERGON-CHANGE-0009"
PUBLIC_RECORD_PATH = "docs/engineering/changes/ergon-change-0009.json"
PUBLIC_SCHEMA_PATH = "docs/engineering/schemas/change-evidence.schema.json"
PUBLIC_VALIDATOR_PATH = "tools/engineering/check_change.py"
PUBLIC_SCHEMA_VERSION = "1.1"
STANDING_PROVENANCE_POLICY = "PUBLICATION_POLICY.md"
SIGNING_PRINCIPAL = "153525861+ErgonSurfer@users.noreply.github.com"
SIGNING_FINGERPRINT = "SHA256:kC/Vx9WJW9ufy4Ttg5tKK6Cw8jEuV9ej2mRCLvZyU3Q"
SIGNING_PUBLIC_KEY = (
    b"ssh-ed25519 "
    b"AAAAC3NzaC1lZDI1NTE5AAAAIFN47Qs8VW9ty+v0tf31kv6pMpyOMxWWLXZ0Pv5MWVCI "
    b"Ergon Lab Git commit signing (ErgonSurfer)\n"
)
SIGNING_PUBLIC_KEY_SHA256 = (
    "0ef0d6055bf86ace992821a029055ed3d3e2da2047619627d3f8405c6cad7512"
)
ALLOWED_SIGNERS = (
    b"153525861+ErgonSurfer@users.noreply.github.com ssh-ed25519 "
    b"AAAAC3NzaC1lZDI1NTE5AAAAIFN47Qs8VW9ty+v0tf31kv6pMpyOMxWWLXZ0Pv5MWVCI\n"
)
ALLOWED_SIGNERS_SHA256 = (
    "4df5711122f5777dbaea2480d2d1fdef81ea294a79d835ab0173ae0065dfa738"
)
SIGNATURE_CONTRACT = {
    "allowed_signers_bytes": 128,
    "allowed_signers_sha256": ALLOWED_SIGNERS_SHA256,
    "candidate_same_key": True,
    "fingerprint": SIGNING_FINGERPRINT,
    "format": "ssh",
    "key_algorithm": "ED25519",
    "principal": SIGNING_PRINCIPAL,
    "public_key": SIGNING_PUBLIC_KEY.decode("ascii").rstrip("\n"),
    "public_key_bytes": 124,
    "public_key_sha256": SIGNING_PUBLIC_KEY_SHA256,
}
TECHNICAL_CHANGE_ENTRIES = (
    ("M", "tests/compatibility/legacy/README.md"),
    ("M", "tests/compatibility/legacy/feature_ergon_legacy_compatibility.py"),
    ("M", "tests/compatibility/legacy/run_matrix.py"),
    ("M", "tests/compatibility/legacy/self_test.py"),
)
GIT_OPERATION_NAMES = {"A": "add", "D": "delete", "M": "modify"}
REVIEWED_FILE_METADATA = {
    "test/functional/mining_basic.py": {
        "production_reachability": "None; test fixture only.",
        "provenance": {
            "code": "BASE+A1",
            "kind": "independent-authorship",
            "license": "MIT",
        },
        "role": "fixture",
        "spdx": "MIT",
        "test_reachability": "Zero-subsidy legacy mining functional fixture.",
    },
    "test/functional/test_framework/test_framework.py": {
        "production_reachability": "None; functional test framework only.",
        "provenance": {
            "code": "BASE+A1",
            "kind": "independent-authorship",
            "license": "MIT",
        },
        "role": "test",
        "spdx": "MIT",
        "test_reachability": "Propagates the explicit hermetic-launch option.",
    },
    "test/functional/test_framework/test_node.py": {
        "production_reachability": "None; functional test framework only.",
        "provenance": {
            "code": "BASE+A1",
            "kind": "independent-authorship",
            "license": "MIT",
        },
        "role": "test",
        "spdx": "MIT",
        "test_reachability": "Closes node child environments and assigns unique TMPDIR paths.",
    },
    "tests/compatibility/legacy/README.md": {
        "production_reachability": "None; documentation only.",
        "provenance": {
            "code": "A1",
            "kind": "independent-authorship",
            "license": "MIT",
        },
        "role": "documentation",
        "spdx": "MIT",
        "test_reachability": (
            "Documents the governed restart, reconstruction, protected-reorg, "
            "and physical-pruning lifecycle contract."
        ),
    },
    "tests/compatibility/legacy/feature_ergon_legacy_compatibility.py": {
        "production_reachability": "None; governed compatibility harness only.",
        "provenance": {
            "code": "A1",
            "kind": "independent-authorship",
            "license": "MIT",
        },
        "role": "harness",
        "spdx": "MIT",
        "test_reachability": (
            "Exercises honest two-role coexistence, clean restart, full reindex, "
            "chainstate reindex, one bounded default-protected valid-chain "
            "reorganization, and manual physical pruning."
        ),
    },
    "tests/compatibility/legacy/run_matrix.py": {
        "production_reachability": "None; governed compatibility harness only.",
        "provenance": {
            "code": "A1",
            "kind": "independent-authorship",
            "license": "MIT",
        },
        "role": "harness",
        "spdx": "MIT",
        "test_reachability": (
            "Runs the identity-bound fail-closed public matrix and requires all "
            "reviewed lifecycle markers."
        ),
    },
    "tests/compatibility/legacy/self_test.py": {
        "production_reachability": "None; governed compatibility tests only.",
        "provenance": {
            "code": "A1",
            "kind": "independent-authorship",
            "license": "MIT",
        },
        "role": "test",
        "spdx": "MIT",
        "test_reachability": (
            "Covers runner contracts, reconstruction, protected-reorg and "
            "physical-pruning canaries, record binding, and fail-closed paths."
        ),
    },
}
PUBLIC_CHANGE_ENTRIES = (
    ("A", PUBLIC_RECORD_PATH),
    *TECHNICAL_CHANGE_ENTRIES,
)
BASELINE_CONTROLLED_PATHS = (
    "CMakeLists.txt",
    "Makefile.am",
    "cmake",
    "configure.ac",
    "src",
)
INTEGRATION_PARENT_PREIMAGES = {
    "tests/compatibility/legacy/README.md":
        "a279e32c9a6c5ffb0a0e4be7ac7b5464dcd8ac15",
    "tests/compatibility/legacy/feature_ergon_legacy_compatibility.py":
        "eeb6ba8c13079fd187bf41c66472bff011232757",
    "tests/compatibility/legacy/run_matrix.py":
        "8c6e21ca8f08cedb8c6de0ed641a783cdbbcdf26",
    "tests/compatibility/legacy/self_test.py":
        "bf62956ebc9b425587fe371a43c412110fa5d91b",
}
BUILD_ROLES = ("baseline", "candidate")
INTEGRATION_PARENT_BINDING = {
    "commit_argument": "--expected-integration-parent-commit",
    "defaults_allowed": False,
    "environment_fallback_allowed": False,
    "input_method": "explicit-reviewed-cli",
    "record_match_required": True,
    "tree_argument": "--expected-integration-parent-tree",
}
PUBLIC_BOUNDARIES = {
    "chronik_activation_authority": False,
    "chronik_chain_selection_authority": False,
    "chronik_compile_time_default": "off",
    "chronik_consensus_authority": False,
    "chronik_enabled_scope": "local-regtest-opt-in-only",
    "chronik_mempool_authority": False,
    "chronik_required_for_correctness": False,
    "chronik_role": "observe-and-index-only",
    "chronik_runtime_default": "off",
    "consensus_authority": "standalone-node",
    "mainnet_parameters_modified": False,
}
CHILD_ENVIRONMENT = {
    "LANG": "C",
    "LC_ALL": "C",
    "NO_COLOR": "1",
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    "TERM": "dumb",
    "TZ": "UTC",
}
MIXED_NODE_SUCCESS_MARKERS = (
    b"ERGON_LEGACY_LIFECYCLE_OK full-reindex",
    b"ERGON_LEGACY_LIFECYCLE_OK chainstate-reindex",
    b"ERGON_LEGACY_LIFECYCLE_OK default-protected-reorg",
    b"ERGON_LEGACY_LIFECYCLE_OK physical-pruning",
)
ALLOWED_ABSOLUTE_RECORD_VALUES = {CHILD_ENVIRONMENT["PATH"]}
POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.:/\\-])/(?!/)[^\s\"'<>]+"
)
WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:[\\/]|\\\\)[^\s\"'<>]*"
)
EXECUTIONS = (
    {
        "id": "mixed-node-coexistence",
        "script": "tests/compatibility/legacy/feature_ergon_legacy_compatibility.py",
        "build": "candidate",
        "legacy": True,
        "hermetic_nodes": True,
        "required_output_markers": MIXED_NODE_SUCCESS_MARKERS,
    },
    {
        "id": "legacy-mining-baseline",
        "script": "test/functional/mining_basic.py",
        "build": "baseline",
        "hermetic_nodes": True,
    },
    {
        "id": "legacy-mining-candidate",
        "script": "test/functional/mining_basic.py",
        "build": "candidate",
        "hermetic_nodes": True,
    },
    {
        "id": "inherited-functional-default-launch",
        "script": "test/functional/feature_abortnode.py",
        "build": "candidate",
        "hermetic_nodes": False,
    },
)


class MatrixError(RuntimeError):
    pass


class StoreOnce(argparse.Action):
    """Reject repeated reviewed identity arguments instead of taking the last."""

    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            raise argparse.ArgumentError(self, "must be supplied exactly once")
        setattr(namespace, self.dest, values)


def reviewed_git_oid(value):
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise argparse.ArgumentTypeError(
            "must be exactly 40 lowercase hex characters"
        )
    return value


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def child_environment(tmpdir):
    environment = dict(CHILD_ENVIRONMENT)
    environment["TMPDIR"] = str(Path(tmpdir).resolve(strict=True))
    return environment


def git_environment(tmpdir):
    environment = child_environment(tmpdir)
    environment.update({
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    })
    return environment


def git_command(source_root, *args, extra_config=()):
    source_root = Path(source_root).resolve(strict=True)
    command = [
        "git",
        "-c",
        f"safe.directory={source_root}",
    ]
    for item in extra_config:
        command.extend(["-c", item])
    command.extend(["-C", str(source_root), *args])
    return command


def git_output(source_root, *args):
    with tempfile.TemporaryDirectory(prefix="ergon-legacy-git-") as tmpdir:
        result = subprocess.run(
            git_command(source_root, *args),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=git_environment(tmpdir),
        )
    return result.stdout.decode("ascii", errors="strict").strip()


def git_returncode(source_root, *args):
    with tempfile.TemporaryDirectory(prefix="ergon-legacy-git-") as tmpdir:
        result = subprocess.run(
            git_command(source_root, *args),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=git_environment(tmpdir),
        )
    return result.returncode


def source_identity(source_root, *, exact_base=False):
    source_root = Path(source_root).resolve(strict=True)
    if git_output(source_root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise MatrixError("source tree must be clean")
    identity = {
        "commit": git_output(source_root, "rev-parse", "HEAD"),
        "tree": git_output(source_root, "rev-parse", "HEAD^{tree}"),
        "clean": True,
    }
    if exact_base and identity != {
        "commit": BASE_COMMIT,
        "tree": BASE_TREE,
        "clean": True,
    }:
        raise MatrixError("baseline source is not exact Bitcoin Static v24.0.5")
    return source_root, identity


def require_complete_git_history(source_root):
    if git_output(source_root, "rev-parse", "--is-shallow-repository") != "false":
        raise MatrixError("source history must be complete and non-shallow")
    if git_output(source_root, "for-each-ref", "--format=%(refname)",
                  "refs/replace"):
        raise MatrixError("source history contains a replace ref")
    git_dir = Path(git_output(source_root, "rev-parse", "--absolute-git-dir"))
    grafts = git_dir / "info" / "grafts"
    if grafts.exists() and grafts.stat().st_size:
        raise MatrixError("source history contains a graft file")


def verify_signing_trust_root():
    if len(SIGNING_PUBLIC_KEY) != 124 \
            or hashlib.sha256(SIGNING_PUBLIC_KEY).hexdigest() \
            != SIGNING_PUBLIC_KEY_SHA256:
        raise MatrixError("reviewed SSH public-key bytes changed")
    if len(ALLOWED_SIGNERS) != 128 \
            or hashlib.sha256(ALLOWED_SIGNERS).hexdigest() \
            != ALLOWED_SIGNERS_SHA256:
        raise MatrixError("reviewed allowed-signers bytes changed")
    with tempfile.TemporaryDirectory(prefix="ergon-legacy-public-key-") as tmpdir:
        public_key = Path(tmpdir) / "ergon-surfer.pub"
        public_key.write_bytes(SIGNING_PUBLIC_KEY)
        os.chmod(public_key, 0o600)
        fingerprint = subprocess.run(
            ["ssh-keygen", "-l", "-E", "sha256", "-f", str(public_key)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_environment(tmpdir),
        ).stdout.decode("ascii", errors="strict").strip().split()
    if len(fingerprint) < 4 or fingerprint[0] != "256" \
            or fingerprint[1] != SIGNING_FINGERPRINT \
            or fingerprint[-1] != "(ED25519)":
        raise MatrixError("reviewed SSH public-key fingerprint changed")


def verify_commit_signature(source_root, commit):
    verify_signing_trust_root()
    with tempfile.TemporaryDirectory(prefix="ergon-legacy-signature-") as tmpdir:
        tmpdir = Path(tmpdir)
        allowed_signers = tmpdir / "allowed_signers"
        revocations = tmpdir / "revocations"
        allowed_signers.write_bytes(ALLOWED_SIGNERS)
        revocations.write_bytes(b"")
        os.chmod(allowed_signers, 0o600)
        os.chmod(revocations, 0o600)
        environment = git_environment(tmpdir)
        signature_config = (
            "gpg.format=ssh",
            f"gpg.ssh.allowedSignersFile={allowed_signers}",
            f"gpg.ssh.revocationFile={revocations}",
            "gpg.ssh.program=/usr/bin/ssh-keygen",
            "gpg.minTrustLevel=fully",
        )
        verify = subprocess.run(
            git_command(
                source_root, "verify-commit", "--raw", commit,
                extra_config=signature_config,
            ),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        )
        if verify.returncode != 0:
            raise MatrixError("reviewed Git commit signature is invalid")
        fields = subprocess.run(
            git_command(
                source_root, "show", "-s", "--format=%G?%x00%GF%x00%GK%x00%GS",
                commit, extra_config=signature_config,
            ),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
        ).stdout.decode("ascii", errors="strict").rstrip("\n").split("\0")
    expected = [
        "G", SIGNING_FINGERPRINT, SIGNING_FINGERPRINT, SIGNING_PRINCIPAL
    ]
    if fields != expected:
        raise MatrixError("reviewed Git signature identity is invalid")
    return {
        "commit": commit,
        "fingerprint": fields[1],
        "format": "ssh",
        "key_algorithm": "ED25519",
        "principal": fields[3],
        "status": fields[0],
        "tree": git_output(source_root, "rev-parse", f"{commit}^{{tree}}"),
        "verification_result": "valid",
    }


def git_version():
    with tempfile.TemporaryDirectory(prefix="ergon-legacy-git-") as tmpdir:
        result = subprocess.run(
            ["git", "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=git_environment(tmpdir),
        )
    version = result.stdout.decode("ascii", errors="strict").strip()
    if not version.startswith("git version "):
        raise MatrixError("Git version output is unavailable")
    return version


def canonical_json_sha256(value):
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii") + b"\n"
    return hashlib.sha256(encoded).hexdigest()


def reject_duplicate_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise MatrixError("public change record contains a duplicate JSON key")
        value[key] = item
    return value


def reject_host_private_state(value):
    if isinstance(value, dict):
        for key, item in value.items():
            reject_host_private_state(key)
            reject_host_private_state(item)
    elif isinstance(value, list):
        for item in value:
            reject_host_private_state(item)
    elif isinstance(value, str) and value not in ALLOWED_ABSOLUTE_RECORD_VALUES:
        if POSIX_ABSOLUTE_PATH_RE.search(value) \
                or WINDOWS_ABSOLUTE_PATH_RE.search(value):
            raise MatrixError("public change record contains a host-specific path")


def git_file_identity(source_root, revision, path):
    entry = git_output(source_root, "ls-tree", revision, "--", path)
    if not entry:
        return None
    mode, object_type, remainder = entry.split(None, 2)
    blob, entry_path = remainder.split("\t", 1)
    if object_type != "blob" or entry_path != path:
        raise MatrixError("reviewed path is not a regular Git blob")
    with tempfile.TemporaryDirectory(prefix="ergon-legacy-git-") as tmpdir:
        result = subprocess.run(
            git_command(source_root, "cat-file", "blob", blob),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=git_environment(tmpdir),
        )
    data = result.stdout
    return {
        "mode": mode,
        "bytes": len(data),
        "git_blob": blob,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def run_public_record_validator(source_root, record_path):
    validator = Path(source_root) / PUBLIC_VALIDATOR_PATH
    if not validator.is_file() or validator.is_symlink():
        raise MatrixError("public change validator is unavailable")
    with tempfile.TemporaryDirectory(prefix="ergon-legacy-validator-") as tmpdir:
        result = subprocess.run(
            [sys.executable, str(validator), "validate", str(record_path)],
            cwd=source_root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment(tmpdir),
        )
    if result.returncode != 0:
        raise MatrixError("public change record failed the public validator")


def load_change_record(source_root, *, expected_sha256,
                       expected_reviewer_identity, expected_decision_date,
                       expected_integration_parent_commit,
                       expected_integration_parent_tree):
    record_path = Path(source_root) / PUBLIC_RECORD_PATH
    if not record_path.is_file() or record_path.is_symlink():
        raise MatrixError("public change record is not a regular file")
    if sha256_file(record_path) != expected_sha256:
        raise MatrixError("public change record does not match the accepted digest")
    run_public_record_validator(source_root, record_path)
    if sha256_file(record_path) != expected_sha256:
        raise MatrixError("public change record changed during validation")
    try:
        record = json.loads(
            record_path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_object,
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MatrixError("public change record is unreadable") from error
    reject_host_private_state(record)
    required = {
        "$comment": "SPDX-License-Identifier: MIT",
        "$schema": "../schemas/change-evidence.schema.json",
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "change_id": CHANGE_ID,
        "record_path": PUBLIC_RECORD_PATH,
        "stage": "legacy-compatibility",
        "status": "accepted",
    }
    for field, expected in required.items():
        if record.get(field) != expected:
            raise MatrixError(f"public change record has invalid {field}")
    if record.get("baseline") != {
        "commit": BASE_COMMIT,
        "license": "MIT",
        "project": "Bitcoin Static",
        "tree": BASE_TREE,
        "version": "24.0.5",
    }:
        raise MatrixError("public change record has the wrong baseline")
    if record.get("public_lineage") != {
        "candidate_parent_law": "direct-child",
        "integration_parent": {
            "commit": expected_integration_parent_commit,
            "tree": expected_integration_parent_tree,
        },
        "public_root": {
            "commit": PUBLIC_ROOT_COMMIT,
            "parentless": True,
            "tree": PUBLIC_ROOT_TREE,
        },
        "remote_ref": PUBLIC_MAIN_REF,
    }:
        raise MatrixError("public change record has the wrong public lineage")
    if record.get("signature_policy") != SIGNATURE_CONTRACT:
        raise MatrixError("public change record has the wrong signature policy")
    if record.get("prerequisites") != []:
        raise MatrixError("legacy compatibility cannot pre-claim prerequisites")
    if record.get("boundaries") != PUBLIC_BOUNDARIES:
        raise MatrixError("public change record exceeds the authority boundary")
    expected_surfaces = {
        "build_release": False,
        "consensus": False,
        "data_research": False,
        "documentation_cockpit": True,
        "indexing_chronik": False,
        "p2p": False,
        "rpc_api": False,
        "storage_datadir": False,
        "tests": True,
        "ui": False,
        "validation_mempool": False,
        "wallet": False,
    }
    if record.get("surfaces") != expected_surfaces:
        raise MatrixError("public change record has the wrong surfaces")
    verification = record.get("verification", {})
    if verification.get("integration_parent_binding") \
            != INTEGRATION_PARENT_BINDING:
        raise MatrixError("public change record has the wrong parent input law")
    if tuple(item.get("role") for item in verification.get("builds", ())) \
            != BUILD_ROLES:
        raise MatrixError("public change record has the wrong build roles")
    if tuple(item.get("id") for item in verification.get("scenarios", ())) \
            != tuple(execution["id"] for execution in EXECUTIONS):
        raise MatrixError("public change record has the wrong scenario IDs")
    if record.get("evidence") != {
        "artifacts": [],
        "ceiling": "record-review-only",
        "delivery_state": "planned",
        "knowledge_status": "Open Question",
    }:
        raise MatrixError("accepted metadata must not pre-claim execution evidence")
    decision = record.get("decision", {})
    if set(decision) != {"date", "reviewer", "status"} \
            or decision.get("status") != "accepted" \
            or decision.get("reviewer") != expected_reviewer_identity \
            or decision.get("date") != expected_decision_date:
        raise MatrixError("public change record lacks an accepted review decision")
    try:
        parsed_date = time.strptime(decision.get("date", ""), "%Y-%m-%d")
    except ValueError as error:
        raise MatrixError("public change record has an invalid review date") from error
    if time.strftime("%Y-%m-%d", parsed_date) != decision["date"]:
        raise MatrixError("public change record has a non-canonical review date")
    provenance_inventory = record.get("provenance_inventory", {})
    if set(provenance_inventory) != {
            "exclusions", "implicit_operational_authorization",
            "inventory_schema", "inventory_sha256", "license",
            "standing_policy",
    } or provenance_inventory.get("inventory_schema") \
            != "ergon-authorship-inventory/v1" \
            or provenance_inventory.get("license") != "MIT" \
            or provenance_inventory.get("standing_policy") \
            != STANDING_PROVENANCE_POLICY \
            or provenance_inventory.get(
                "implicit_operational_authorization"
            ) is not False \
            or not isinstance(provenance_inventory.get("exclusions"), list) \
            or not provenance_inventory["exclusions"] \
            or not all(
                isinstance(item, str) and item.strip()
                for item in provenance_inventory["exclusions"]
            ):
        raise MatrixError(
            "public change record has an invalid provenance inventory"
        )
    return record, sha256_file(record_path)


def validate_recorded_files(source_root, integration_parent_commit,
                            candidate_commit, record):
    reviewed_files = record.get("files")
    if not isinstance(reviewed_files, list) or len(reviewed_files) != len(
        TECHNICAL_CHANGE_ENTRIES
    ):
        raise MatrixError("public change record has an incomplete file set")
    observed_entries = []
    provenance_entries = []
    for expected, file_record in zip(TECHNICAL_CHANGE_ENTRIES, reviewed_files):
        operation, path = expected
        if file_record.get("action") != GIT_OPERATION_NAMES[operation] \
                or file_record.get("path") != path:
            raise MatrixError("public change record file ordering changed")
        if set(file_record) != {
            "action", "after", "before", "path", "production_reachability",
            "provenance", "role", "spdx", "test_reachability",
        }:
            raise MatrixError("public change record file metadata is incomplete")
        metadata = REVIEWED_FILE_METADATA[path]
        if any(file_record[field] != metadata[field] for field in metadata):
            raise MatrixError("public change record file declaration mismatch")
        actual_before = git_file_identity(
            source_root, integration_parent_commit, path
        )
        actual_after = git_file_identity(source_root, candidate_commit, path)
        if file_record["before"] != actual_before \
                or file_record["after"] != actual_after:
            raise MatrixError("public change record file identity mismatch")
        observed_entries.append((operation, path))
        provenance_entries.append({
            "blob": actual_after["git_blob"],
            "bytes": actual_after["bytes"],
            "mode": actual_after["mode"],
            "path": path,
            "provenance": metadata["provenance"]["code"],
            "sha256": actual_after["sha256"],
        })
    if tuple(observed_entries) != TECHNICAL_CHANGE_ENTRIES:
        raise MatrixError("public change record changed the technical file set")
    provenance_inventory = record.get("provenance_inventory", {})
    inventory = {
        "change_id": CHANGE_ID,
        "entries": provenance_entries,
        "schema": "ergon-authorship-inventory/v1",
    }
    if provenance_inventory.get("inventory_sha256") \
            != canonical_json_sha256(inventory):
        raise MatrixError("public change record provenance digest mismatch")


def candidate_source_identity(source_root, *, expected_commit, expected_tree,
                              expected_record_sha256,
                              expected_reviewer_identity,
                              expected_decision_date,
                              expected_integration_parent_commit,
                              expected_integration_parent_tree):
    source_root, identity = source_identity(source_root)
    if identity["commit"] != expected_commit or identity["tree"] != expected_tree:
        raise MatrixError("candidate source does not match the reviewed identity")
    require_complete_git_history(source_root)
    signatures = {
        "candidate": verify_commit_signature(source_root, expected_commit),
        "integration_parent": verify_commit_signature(
            source_root, expected_integration_parent_commit
        ),
        "public_root": verify_commit_signature(source_root, PUBLIC_ROOT_COMMIT),
    }
    if git_output(source_root, "rev-parse", f"{PUBLIC_ROOT_COMMIT}^{{tree}}") \
            != PUBLIC_ROOT_TREE:
        raise MatrixError("public root tree is not the exact baseline tree")
    if git_output(
        source_root, "rev-list", "--parents", "-n", "1", PUBLIC_ROOT_COMMIT
    ).split() != [PUBLIC_ROOT_COMMIT]:
        raise MatrixError("public root is not parentless")
    if git_output(
        source_root, "rev-parse",
        f"{expected_integration_parent_commit}^{{tree}}"
    ) != expected_integration_parent_tree:
        raise MatrixError("integration parent tree does not match review")
    if git_returncode(
        source_root, "merge-base", "--is-ancestor", PUBLIC_ROOT_COMMIT,
        expected_integration_parent_commit
    ) != 0:
        raise MatrixError("integration parent does not descend from public root")
    if git_returncode(
        source_root, "diff", "--quiet", PUBLIC_ROOT_COMMIT,
        expected_integration_parent_commit, "--", *BASELINE_CONTROLLED_PATHS
    ) != 0:
        raise MatrixError("integration parent changed a baseline-controlled path")
    for path, expected_blob in INTEGRATION_PARENT_PREIMAGES.items():
        if git_output(
            source_root, "rev-parse", f"{expected_integration_parent_commit}:{path}"
        ) != expected_blob:
            raise MatrixError("integration parent preimage changed")
    parents = git_output(
        source_root, "rev-list", "--parents", "-n", "1", expected_commit
    ).split()
    if parents != [expected_commit, expected_integration_parent_commit]:
        raise MatrixError("candidate must directly follow the integration parent")
    origin_main = git_output(source_root, "rev-parse", PUBLIC_MAIN_REF)
    if git_returncode(
        source_root, "merge-base", "--is-ancestor",
        expected_integration_parent_commit, PUBLIC_MAIN_REF
    ) != 0:
        raise MatrixError("integration parent is not reachable from fetched origin/main")
    if git_returncode(
        source_root, "merge-base", "--is-ancestor", expected_commit,
        PUBLIC_MAIN_REF
    ) != 0:
        raise MatrixError("candidate is not reachable from fetched origin/main")
    changed = tuple(
        tuple(line.split("\t", 1))
        for line in git_output(
            source_root, "diff", "--name-status", "--no-renames",
            expected_integration_parent_commit, expected_commit
        ).splitlines()
    )
    if len(changed) != len(set(changed)) \
            or set(changed) != set(PUBLIC_CHANGE_ENTRIES):
        raise MatrixError("candidate diff is not the exact public change")
    try:
        git_output(
            source_root, "diff", "--check", expected_integration_parent_commit,
            expected_commit,
        )
    except subprocess.CalledProcessError as error:
        raise MatrixError("candidate diff failed git diff --check") from error
    record, record_sha256 = load_change_record(
        source_root,
        expected_sha256=expected_record_sha256,
        expected_reviewer_identity=expected_reviewer_identity,
        expected_decision_date=expected_decision_date,
        expected_integration_parent_commit=expected_integration_parent_commit,
        expected_integration_parent_tree=expected_integration_parent_tree,
    )
    post_validator_root, post_validator_identity = source_identity(source_root)
    if post_validator_root != source_root or post_validator_identity != identity:
        raise MatrixError("candidate source changed during record validation")
    validate_recorded_files(
        source_root, expected_integration_parent_commit, expected_commit, record
    )
    public_git = {
        "accepted_record": {
            "decision_date": expected_decision_date,
            "reviewer_identity": expected_reviewer_identity,
            "sha256": record_sha256,
        },
        "git_version": git_version(),
        "integration_parent": {
            "candidate_direct_parent": True,
            "commit": expected_integration_parent_commit,
            "controlled_paths_unchanged": True,
            "explicit_reviewed_cli": True,
            "preimages_verified": True,
            "record_match": True,
            "remote_ancestor": True,
            "tree": expected_integration_parent_tree,
        },
        "origin_main_oid": origin_main,
        "signatures": signatures,
        "trust_root": SIGNATURE_CONTRACT,
    }
    return source_root, identity, record_sha256, public_git


def functional_inventory(source_root):
    functional_root = Path(source_root) / "test" / "functional"
    names = sorted(path.name for path in functional_root.glob("*.py"))
    runner_path = functional_root / "test_runner.py"
    tree = ast.parse(runner_path.read_text(encoding="utf-8"))
    literals = {}
    for statement in tree.body:
        if not isinstance(statement, ast.Assign):
            continue
        for target in statement.targets:
            if isinstance(target, ast.Name) and target.id in {
                "NON_SCRIPTS", "TEST_PARAMS"
            }:
                literals[target.id] = ast.literal_eval(statement.value)
    if set(literals) != {"NON_SCRIPTS", "TEST_PARAMS"}:
        raise MatrixError("test_runner.py omitted a governed literal inventory")
    non_scripts = literals["NON_SCRIPTS"]
    selected = sorted(set(names) - set(non_scripts))
    return {
        "python_files": names,
        "non_scripts": list(non_scripts),
        "test_params": literals["TEST_PARAMS"],
        "selected_scripts": selected,
        "test_runner_sha256": sha256_file(runner_path),
        "timing_sha256": sha256_file(functional_root / "timing.json"),
    }


def require_unchanged_functional_selection(baseline_source, candidate_source):
    baseline = functional_inventory(baseline_source)
    candidate = functional_inventory(candidate_source)
    if candidate != baseline:
        raise MatrixError("inherited functional test selection changed")
    return baseline


def build_identity(build_root, expected_source):
    build_root = Path(build_root).resolve(strict=True)
    binary = require_strict_descendant(
        build_root / "src" / "bitcoind", build_root, "daemon file"
    )
    config_path = require_strict_descendant(
        build_root / "test" / "config.ini", build_root, "build configuration"
    )
    config = configparser.ConfigParser()
    with open(config_path, encoding="utf-8") as file:
        config.read_file(file)
    configured_build = Path(config["environment"]["BUILDDIR"]).resolve(strict=True)
    configured_source = Path(config["environment"]["SRCDIR"]).resolve(strict=True)
    if configured_build != build_root:
        raise MatrixError("config.ini does not bind the supplied build root")
    if configured_source != Path(expected_source).resolve(strict=True):
        raise MatrixError("config.ini does not bind the supplied source root")
    return {
        "root": build_root,
        "binary": binary,
        "config": config_path,
        "public": {
            "bitcoind_bytes": binary.stat().st_size,
            "bitcoind_sha256": sha256_file(binary),
            "config_sha256": sha256_file(config_path),
            "config_source_binding_checked": True,
            "binary_to_source_provenance": "external_build_record_required",
        },
    }


def require_distinct(paths, label):
    resolved = [Path(item).resolve(strict=True) for item in paths]
    if len({str(item) for item in resolved}) != len(resolved):
        raise MatrixError(f"{label} paths must be distinct")
    identities = [(item.stat().st_dev, item.stat().st_ino) for item in resolved]
    if len(set(identities)) != len(identities):
        raise MatrixError(f"{label} inodes must be distinct")


def require_strict_descendant(path, root, label):
    path = Path(path).resolve(strict=True)
    root = Path(root).resolve(strict=True)
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise MatrixError(f"{label} must remain inside its declared root") from error
    if not relative.parts:
        raise MatrixError(f"{label} must be below its declared root")
    return path


def require_disjoint_roots(paths, label):
    roots = [Path(item).resolve(strict=True) for item in paths]
    require_distinct(roots, label)
    for index, first in enumerate(roots):
        for second in roots[index + 1:]:
            try:
                first.relative_to(second)
            except ValueError:
                pass
            else:
                raise MatrixError(f"{label} must not contain one another")
            try:
                second.relative_to(first)
            except ValueError:
                pass
            else:
                raise MatrixError(f"{label} must not contain one another")
    return roots


def require_work_root_outside_inputs(work_root, protected_roots):
    work_root = Path(work_root).resolve(strict=False)
    for protected_root in protected_roots:
        try:
            work_root.relative_to(Path(protected_root).resolve(strict=True))
        except ValueError:
            continue
        raise MatrixError("work root must be outside every source and build root")
    return work_root


def require_report_outside_work_root(report_path, work_root):
    report_path = Path(report_path).resolve(strict=False)
    try:
        report_path.relative_to(Path(work_root).resolve(strict=False))
    except ValueError:
        return report_path
    raise MatrixError("report path must be outside the disposable work root")


def require_report_outside_inputs(report_path, protected_roots):
    report_path = Path(report_path).resolve(strict=False)
    for protected_root in protected_roots:
        protected_root = Path(protected_root).resolve(strict=True)
        if report_path == protected_root:
            raise MatrixError("report path must be outside every input root")
        try:
            report_path.relative_to(protected_root)
        except ValueError:
            continue
        raise MatrixError("report path must be outside every input root")
    return report_path


def validate_test_output(returncode, stdout, stderr, required_markers=()):
    combined = stdout + b"\n" + stderr
    if returncode != 0:
        raise MatrixError(f"functional test exited {returncode}")
    if b"Test skipped" in combined or b"Tests skipped" in combined:
        raise MatrixError("functional test reported a skip")
    if b"Tests successful" not in combined:
        raise MatrixError("functional test omitted its success marker")
    for marker in required_markers:
        if marker not in combined:
            raise MatrixError("functional test omitted a required lifecycle marker")


def wait_process_group_gone(process_group, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(0.1)
    return False


def terminate_process_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    if wait_process_group_gone(process.pid, 10):
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        pass
    if process.poll() is None:
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    if not wait_process_group_gone(process.pid, 10):
        raise MatrixError("unable to verify process-group cleanup")


def run_execution(execution, *, candidate_source, builds, work_root,
                  portseed, timeout):
    execution_root = work_root / execution["id"]
    tmpdir = execution_root / "tmp"
    datadir = execution_root / "datadir"
    tmpdir.mkdir(parents=True)
    command = [
        sys.executable,
        str(candidate_source / execution["script"]),
        f"--configfile={builds[execution['build']]['config']}",
        f"--tmpdir={datadir}",
        f"--portseed={portseed}",
    ]
    if execution["hermetic_nodes"]:
        command.append("--hermetic-child-env")
    if execution.get("legacy"):
        command.append(f"--legacy-bitcoind={builds['baseline']['binary']}")

    process = subprocess.Popen(
        command,
        cwd=candidate_source,
        env=child_environment(tmpdir),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise MatrixError(f"functional test timed out: {execution['id']}") from error
    finally:
        terminate_process_group(process)
    validate_test_output(
        process.returncode,
        stdout,
        stderr,
        execution.get("required_output_markers", ()),
    )


def public_report(baseline_identity, candidate_identity, record_sha256,
                  public_git, builds, inventory, completed):
    expected = [execution["id"] for execution in EXECUTIONS]
    if completed != expected:
        raise MatrixError("public report requires every exact execution")
    results = {execution_id: "pass" for execution_id in completed}
    return {
        "schema": "ergon-legacy-compatibility/v1",
        "knowledge_status": "Observed",
        "evidence_ceiling": "assembled_component",
        "raw_output_retained": False,
        "host_specific_absolute_paths_retained": False,
        "parent_environment_retained": False,
        "candidate_source": candidate_identity,
        "public_git": public_git,
        "builds": {
            role: builds[role]["public"] for role in BUILD_ROLES
        },
        "runner_environment": {
            **CHILD_ENVIRONMENT,
            "TMPDIR": "unique_runner_created_directory",
        },
        "node_launch_modes": {
            execution["id"]: (
                "closed-hermetic-environment"
                if execution["hermetic_nodes"]
                else "framework-default-inherits-closed-runner-environment"
            )
            for execution in EXECUTIONS
        },
        "change_identity": {
            "baseline": baseline_identity,
            "public_root": {
                "commit": PUBLIC_ROOT_COMMIT,
                "tree": PUBLIC_ROOT_TREE,
            },
            "integration_parent": public_git["integration_parent"],
            "candidate": candidate_identity,
            "candidate_direct_parent_is_integration_parent": True,
            "record_path": PUBLIC_RECORD_PATH,
            "record_sha256": record_sha256,
            "diff_entries": [
                {"operation": operation, "path": path}
                for operation, path in PUBLIC_CHANGE_ENTRIES
            ],
        },
        "inherited_functional_selection": {
            "baseline": inventory,
            "candidate": inventory,
            "identical": True,
            "selection_delta": [],
            "governed_tests_outside_functional_discovery": [
                "tests/compatibility/legacy/feature_ergon_legacy_compatibility.py"
            ],
        },
        "executions": results,
        "claims": {
            "legacy_consensus_changed": False,
            "optional_indexing_present": False,
            "testnet_activation": "out-of-scope",
            "mainnet_activation": "out-of-scope",
        },
    }


def parse_args():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--baseline-source", required=True)
    parser.add_argument("--candidate-source", required=True)
    parser.add_argument("--expected-candidate-commit", required=True)
    parser.add_argument("--expected-candidate-tree", required=True)
    parser.add_argument(
        "--expected-integration-parent-commit", action=StoreOnce,
        required=True, type=reviewed_git_oid,
    )
    parser.add_argument(
        "--expected-integration-parent-tree", action=StoreOnce,
        required=True, type=reviewed_git_oid,
    )
    parser.add_argument("--expected-accepted-record-sha256", required=True)
    parser.add_argument("--expected-reviewer-identity", required=True)
    parser.add_argument("--expected-decision-date", required=True)
    parser.add_argument("--baseline-build", required=True)
    parser.add_argument("--candidate-build", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--portseed-base", type=int, default=510)
    parser.add_argument("--timeout", type=int, default=1200)
    return parser.parse_args()


def main():
    args = parse_args()
    baseline_source, baseline_identity = source_identity(
        args.baseline_source, exact_base=True
    )
    candidate_source, candidate_identity, record_sha256, public_git = \
        candidate_source_identity(
            args.candidate_source,
            expected_commit=args.expected_candidate_commit,
            expected_tree=args.expected_candidate_tree,
            expected_record_sha256=args.expected_accepted_record_sha256,
            expected_reviewer_identity=args.expected_reviewer_identity,
            expected_decision_date=args.expected_decision_date,
            expected_integration_parent_commit=
                args.expected_integration_parent_commit,
            expected_integration_parent_tree=
                args.expected_integration_parent_tree,
        )
    require_distinct([baseline_source, candidate_source], "source roots")
    inventory = require_unchanged_functional_selection(
        baseline_source, candidate_source
    )
    builds = {
        "baseline": build_identity(args.baseline_build, baseline_source),
        "candidate": build_identity(args.candidate_build, candidate_source),
    }
    require_distinct(
        [builds["baseline"]["root"], builds["candidate"]["root"]],
        "build roots",
    )
    require_disjoint_roots(
        [
            baseline_source,
            candidate_source,
            builds["baseline"]["root"],
            builds["candidate"]["root"],
        ],
        "source and build roots",
    )
    require_distinct(
        [builds["baseline"]["binary"], builds["candidate"]["binary"]],
        "daemon files",
    )
    work_root = require_work_root_outside_inputs(
        args.work_root,
        [
            baseline_source,
            candidate_source,
            builds["baseline"]["root"],
            builds["candidate"]["root"],
        ],
    )
    if work_root.exists():
        raise MatrixError("work root must not exist")
    report_path = require_report_outside_work_root(args.report, work_root)
    report_path = require_report_outside_inputs(
        report_path,
        [
            baseline_source,
            candidate_source,
            builds["baseline"]["root"],
            builds["candidate"]["root"],
        ],
    )
    if report_path.exists():
        raise MatrixError("report path already exists")
    work_root.mkdir(parents=True, mode=0o700)

    completed = []
    try:
        for offset, execution in enumerate(EXECUTIONS):
            run_execution(
                execution,
                candidate_source=candidate_source,
                builds=builds,
                work_root=work_root,
                portseed=args.portseed_base + offset,
                timeout=args.timeout,
            )
            completed.append(execution["id"])
    except BaseException:
        shutil.rmtree(work_root, ignore_errors=True)
        raise

    shutil.rmtree(work_root)
    if work_root.exists():
        raise MatrixError("disposable work root survived cleanup")
    report = public_report(
        baseline_identity, candidate_identity, record_sha256, public_git, builds,
        inventory, completed
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "x", encoding="utf-8", newline="\n") as file:
        json.dump(report, file, indent=2, sort_keys=True)
        file.write("\n")


if __name__ == "__main__":
    try:
        main()
    except MatrixError as error:
        print(f"legacy compatibility matrix failed: {error}", file=sys.stderr)
        raise SystemExit(1)
    except (OSError, subprocess.SubprocessError):
        print("legacy compatibility matrix failed: operating-system error", file=sys.stderr)
        raise SystemExit(1)

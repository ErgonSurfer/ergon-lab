#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Validate the Chronik donor corpus and governed executable workspace."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tomllib


SCHEMA = "ergon-chronik-donor-import/v1"
DONOR_REPOSITORY = "https://github.com/Bitcoin-ABC/bitcoin-abc.git"
DONOR_COMMIT = "784d83de2e19eab726898d77bfe7465410d27a9c"
DONOR_TREE = "763739eb3c255e90a6122a2679c667089f9efad2"
DONOR_ARCHIVE_SHA256 = (
    "310d266efd6a0fa5486746d0fc9dda7db983542f054a149386cb69a8d2e6fcfd"
)
MANIFEST_PATH = Path(
    "docs/engineering/chronik/chronik-port-lot-a-donor-v1.json"
)

DONOR_DIRS = (
    "abc-rust-error",
    "abc-rust-lint",
    "bitcoinsuite-core",
    "bitcoinsuite-slp",
    "chronik-bridge",
    "chronik-db",
    "chronik-http",
    "chronik-indexer",
    "chronik-lib",
    "chronik-plugin",
    "chronik-plugin-common",
    "chronik-plugin-impl",
    "chronik-proto",
    "chronik-util",
)
DONOR_ROOT_FILES = ("COPYING",)
WHITESPACE_ADAPTATIONS = (
    "chronik/chronik-http/Cargo.toml",
    "chronik/chronik-plugin-impl/Cargo.toml",
    "chronik/chronik-proto/proto/chronik.proto",
)

WORKSPACE_MEMBERS = (
    "abc-rust-error",
    "abc-rust-lint",
    "bitcoinsuite-core",
    "bitcoinsuite-slp",
    "chronik-db",
    "chronik-observer",
    "chronik-plugin",
    "chronik-plugin-common",
    "chronik-plugin-impl",
    "chronik-proto",
    "chronik-runtime",
    "chronik-util",
)
DEFAULT_MEMBERS = (
    "bitcoinsuite-core",
    "bitcoinsuite-slp",
    "chronik-db",
    "chronik-proto",
    "chronik-runtime",
)
EXCLUDED_MEMBERS = (
    "chronik-bridge",
    "chronik-http",
    "chronik-indexer",
    "chronik-lib",
)


class CheckError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CheckError(message)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_blob(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    return hashlib.sha1(header + data).hexdigest()


def file_mode(path: Path) -> str:
    return "100755" if path.stat().st_mode & 0o111 else "100644"


def strip_ascii_trailing_spaces(data: bytes) -> bytes:
    return b"\n".join(line.rstrip(b" ") for line in data.split(b"\n"))


def imported_paths(root: Path) -> list[Path]:
    chronik = root / "chronik"
    paths = [chronik / name for name in DONOR_ROOT_FILES]
    for directory in DONOR_DIRS:
        for path in (chronik / directory).rglob("*"):
            require(not path.is_symlink(), f"symlink rejected: {path.relative_to(root)}")
            if path.is_file():
                paths.append(path)
    return sorted(paths, key=lambda path: path.relative_to(root).as_posix())


def donor_tree_map(tree_json: Path) -> dict[str, tuple[str, str]]:
    payload = json.loads(tree_json.read_text(encoding="utf-8"))
    require(payload.get("sha") == DONOR_TREE, "donor tree identity mismatch")
    require(payload.get("truncated") is False, "donor tree response is truncated")
    result: dict[str, tuple[str, str]] = {}
    for entry in payload.get("tree", []):
        if entry.get("type") != "blob":
            continue
        result[entry["path"]] = (entry["mode"], entry["sha"])
    return result


def generate(repository_root: Path, donor_root: Path, tree_json: Path) -> dict:
    tree = donor_tree_map(tree_json)
    entries = []
    adaptations = []
    for local_path in imported_paths(repository_root):
        relative = local_path.relative_to(repository_root).as_posix()
        donor_relative = (
            relative.removeprefix("chronik/")
            if relative in {f"chronik/{name}" for name in DONOR_ROOT_FILES}
            else relative
        )
        donor_path = donor_root / donor_relative
        require(donor_path.is_file(), f"missing donor file: {donor_relative}")
        require(not donor_path.is_symlink(), f"donor symlink rejected: {donor_relative}")
        local_bytes = local_path.read_bytes()
        donor_bytes = donor_path.read_bytes()
        require(donor_relative in tree, f"path absent from donor tree: {donor_relative}")
        tree_mode, tree_blob = tree[donor_relative]
        require(file_mode(local_path) == tree_mode, f"donor mode drift: {relative}")
        require(git_blob(donor_bytes) == tree_blob, f"donor blob drift: {relative}")
        if relative in WHITESPACE_ADAPTATIONS:
            normalized = strip_ascii_trailing_spaces(donor_bytes)
            require(local_bytes == normalized, f"whitespace adaptation drift: {relative}")
            require(local_bytes != donor_bytes, f"declared adaptation is empty: {relative}")
            adaptations.append(
                {
                    "path": relative,
                    "donor_path": donor_relative,
                    "rule": "strip-ascii-trailing-spaces",
                    "removed_bytes": len(donor_bytes) - len(local_bytes),
                    "before": {
                        "mode": tree_mode,
                        "bytes": len(donor_bytes),
                        "git_blob": tree_blob,
                        "sha256": sha256(donor_bytes),
                    },
                    "after": {
                        "mode": tree_mode,
                        "bytes": len(local_bytes),
                        "git_blob": git_blob(local_bytes),
                        "sha256": sha256(local_bytes),
                    },
                }
            )
            continue
        require(local_bytes == donor_bytes, f"donor byte drift: {relative}")
        require(git_blob(local_bytes) == tree_blob, f"donor blob drift: {relative}")
        entries.append(
            {
                "path": relative,
                "donor_path": donor_relative,
                "mode": tree_mode,
                "bytes": len(local_bytes),
                "git_blob": tree_blob,
                "sha256": sha256(local_bytes),
            }
        )

    lock_bytes = (repository_root / "chronik/Cargo.lock").read_bytes()
    return {
        "$comment": "SPDX-License-Identifier: MIT",
        "schema": SCHEMA,
        "donor": {
            "repository": DONOR_REPOSITORY,
            "commit": DONOR_COMMIT,
            "tree": DONOR_TREE,
            "archive_sha256": DONOR_ARCHIVE_SHA256,
            "license": "MIT",
            "license_path": "chronik/COPYING",
        },
        "scope": {
            "exact_donor_files": len(entries),
            "adapted_donor_files": len(adaptations),
            "runtime_status": "dormant-until-lot-b",
            "consensus_authority": False,
            "node_runtime_linked": False,
            "executable_packages": list(DEFAULT_MEMBERS),
            "excluded_runtime_members": list(EXCLUDED_MEMBERS),
        },
        "workspace": {
            "members": list(WORKSPACE_MEMBERS),
            "default_members": list(DEFAULT_MEMBERS),
            "excluded": list(EXCLUDED_MEMBERS),
            "rust_version": "1.85.0",
            "cargo_lock_sha256": sha256(lock_bytes),
        },
        "entries": entries,
        "adaptations": adaptations,
    }


def validate(repository_root: Path) -> dict:
    path = repository_root / MANIFEST_PATH
    payload = json.loads(path.read_text(encoding="utf-8"))
    require(payload.get("schema") == SCHEMA, "manifest schema mismatch")
    donor = payload.get("donor", {})
    require(donor.get("repository") == DONOR_REPOSITORY, "donor repository mismatch")
    require(donor.get("commit") == DONOR_COMMIT, "donor commit mismatch")
    require(donor.get("tree") == DONOR_TREE, "donor tree mismatch")
    require(
        donor.get("archive_sha256") == DONOR_ARCHIVE_SHA256,
        "donor archive digest mismatch",
    )
    require(donor.get("license") == "MIT", "donor license mismatch")

    entries = payload.get("entries")
    require(isinstance(entries, list) and entries, "empty donor inventory")
    adaptations = payload.get("adaptations")
    require(isinstance(adaptations, list), "missing donor adaptations")
    paths = [entry.get("path") for entry in entries]
    adapted_paths = [entry.get("path") for entry in adaptations]
    require(paths == sorted(paths), "donor inventory is not path-sorted")
    require(adapted_paths == sorted(adapted_paths), "adaptation inventory is not path-sorted")
    require(len(paths) == len(set(paths)), "duplicate donor inventory path")
    require(len(adapted_paths) == len(set(adapted_paths)), "duplicate adaptation path")
    require(not set(paths) & set(adapted_paths), "exact/adapted path overlap")
    expected_paths = [
        path.relative_to(repository_root).as_posix()
        for path in imported_paths(repository_root)
    ]
    require(sorted(paths + adapted_paths) == expected_paths, "donor inventory path set drift")
    require(
        payload.get("scope", {}).get("exact_donor_files") == len(entries),
        "donor inventory count mismatch",
    )
    require(
        payload.get("scope", {}).get("adapted_donor_files") == len(adaptations),
        "donor adaptation count mismatch",
    )
    require(tuple(adapted_paths) == WHITESPACE_ADAPTATIONS, "donor adaptation allowlist drift")

    for entry in entries:
        relative = entry["path"]
        expected_donor_path = (
            relative.removeprefix("chronik/")
            if relative in {f"chronik/{name}" for name in DONOR_ROOT_FILES}
            else relative
        )
        require(
            entry.get("donor_path") == expected_donor_path,
            f"donor path mapping drift: {relative}",
        )
        source = repository_root / relative
        require(source.is_file() and not source.is_symlink(), f"bad source type: {relative}")
        data = source.read_bytes()
        require(entry.get("mode") == file_mode(source), f"mode drift: {relative}")
        require(entry.get("bytes") == len(data), f"size drift: {relative}")
        require(entry.get("git_blob") == git_blob(data), f"blob drift: {relative}")
        require(entry.get("sha256") == sha256(data), f"SHA-256 drift: {relative}")

    for entry in adaptations:
        relative = entry["path"]
        source = repository_root / relative
        require(source.is_file() and not source.is_symlink(), f"bad source type: {relative}")
        data = source.read_bytes()
        after = entry.get("after", {})
        require(entry.get("rule") == "strip-ascii-trailing-spaces", f"adaptation rule drift: {relative}")
        require(entry.get("removed_bytes", 0) > 0, f"empty adaptation: {relative}")
        require(after.get("mode") == file_mode(source), f"adapted mode drift: {relative}")
        require(after.get("bytes") == len(data), f"adapted size drift: {relative}")
        require(after.get("git_blob") == git_blob(data), f"adapted blob drift: {relative}")
        require(after.get("sha256") == sha256(data), f"adapted SHA-256 drift: {relative}")

    cargo_toml = tomllib.loads(
        (repository_root / "chronik/Cargo.toml").read_text(encoding="utf-8")
    )
    workspace = cargo_toml["workspace"]
    require(tuple(workspace["members"]) == WORKSPACE_MEMBERS, "workspace members drift")
    require(
        tuple(workspace["default-members"]) == DEFAULT_MEMBERS,
        "workspace default members drift",
    )
    require(tuple(workspace["exclude"]) == EXCLUDED_MEMBERS, "workspace exclusions drift")
    require(
        cargo_toml["workspace"]["package"]["rust-version"] == "1.85.0",
        "Rust version drift",
    )

    lock_path = repository_root / "chronik/Cargo.lock"
    lock_bytes = lock_path.read_bytes()
    require(
        payload.get("workspace", {}).get("cargo_lock_sha256") == sha256(lock_bytes),
        "Cargo.lock digest drift",
    )
    lock_text = lock_bytes.decode("utf-8")
    require("git+" not in lock_text, "Git dependency found in Cargo.lock")

    root_cmake = (repository_root / "CMakeLists.txt").read_text(encoding="utf-8")
    require(
        "option(BUILD_CHRONIK_PORT_CORE" in root_cmake
        and "Build the Chronik token, database, protobuf, and persistent runtime core"
        in root_cmake,
        "Chronik port-core CMake option missing",
    )
    chronik_cmake = (repository_root / "chronik/CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    require(
        "add_custom_target(check-chronik-port-core" in chronik_cmake,
        "Lot A test target missing",
    )
    require("--locked" in chronik_cmake and "--offline" in chronik_cmake,
            "Lot A Cargo target is not locked and offline")
    return payload


def canonical_json(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "generate"))
    parser.add_argument(
        "--repository-root", type=Path, default=Path(__file__).resolve().parents[2]
    )
    parser.add_argument("--donor-root", type=Path)
    parser.add_argument("--donor-tree-json", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repository_root = args.repository_root.resolve()
    try:
        if args.command == "generate":
            require(args.donor_root is not None, "--donor-root is required")
            require(args.donor_tree_json is not None, "--donor-tree-json is required")
            payload = generate(
                repository_root,
                args.donor_root.resolve(),
                args.donor_tree_json.resolve(),
            )
            destination = repository_root / MANIFEST_PATH
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(canonical_json(payload))
            print(f"generated {destination.relative_to(repository_root)}")
        else:
            payload = validate(repository_root)
            print(
                "PASS: "
                f"{payload['scope']['exact_donor_files']} donor files, "
                f"{payload['scope']['adapted_donor_files']} whitespace adaptations, "
                f"{len(payload['workspace']['default_members'])} governed executable packages"
            )
        return 0
    except (CheckError, KeyError, OSError, ValueError, tomllib.TOMLDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

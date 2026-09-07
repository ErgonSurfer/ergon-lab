#!/usr/bin/env -S python3 -I -B
# SPDX-License-Identifier: MIT
"""Run the fail-closed, local optional-indexing evidence boundary."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import shutil
import signal
import subprocess
import sys
import sysconfig
import tempfile
import time
from typing import Any


SCHEMA = "ergon-optional-indexing-closure/v1"
PUBLIC_URL = "https://github.com/ErgonSurfer/ergon-lab.git"
BASELINE_COMMIT = "2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b"
BASELINE_TREE = "8a74bb952c2137156214b9fe5888c494bd77aeca"
PUBLIC_ROOT_COMMIT = "5bcdba149119aa9035830e069d1cae1d9bcddfb4"
INTEGRATION_PARENT_COMMIT = "b91ac10f741de15218e5b033430e19974668c5a6"
INTEGRATION_PARENT_TREE = "c210d4db9c6c78a774518e993cbc83e33dc3ef8d"
SIGNING_PRINCIPAL = "153525861+ErgonSurfer@users.noreply.github.com"
SIGNING_FINGERPRINT = "SHA256:kC/Vx9WJW9ufy4Ttg5tKK6Cw8jEuV9ej2mRCLvZyU3Q"
SIGNING_PUBLIC_KEY = (
    b"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFN47Qs8VW9ty+v0tf31kv6pMpyOMxWWLXZ0Pv5MWVCI "
    b"Ergon Lab Git commit signing (ErgonSurfer)\n"
)
SIGNING_PUBLIC_KEY_SHA256 = "0ef0d6055bf86ace992821a029055ed3d3e2da2047619627d3f8405c6cad7512"
ALLOWED_SIGNERS = (
    SIGNING_PRINCIPAL.encode("ascii") + b" " +
    SIGNING_PUBLIC_KEY.split(b" ", 2)[0] + b" " +
    SIGNING_PUBLIC_KEY.split(b" ", 2)[1] + b"\n"
)
ALLOWED_SIGNERS_SHA256 = "4df5711122f5777dbaea2480d2d1fdef81ea294a79d835ab0173ae0065dfa738"
RECORD_PATH = Path("docs/engineering/changes/ergon-change-0033.json")
HARNESS_PATH = Path("tools/engineering/run_optional_indexing_closure.py")
LOCK_PATH = Path("chronik/Cargo.lock")
EXPECTED_CHANGED_PATHS = tuple(sorted((str(RECORD_PATH), str(HARNESS_PATH))))
SCENARIOS = (
    "index-local-regtest-opt-in",
    "index-restart-288",
    "index-full-reindex-288",
    "index-chainstate-reindex-288",
    "index-pruned-datadir-288",
    "index-deep-reorg-fail-closed",
)
BUILD_ROLES = ("compiled-out", "compiled-in-disabled", "local-regtest-indexing")
REPORT_KEYS = {
    "schema", "result", "reason_code", "knowledge_status", "evidence_ceiling",
    "target", "environment", "builds", "checks", "scenarios", "invariants",
    "claims", "privacy", "limitations",
}
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
CHRONIK_SYMBOL = re.compile(rb"(?:^|\s)_?chronik_observer_create_bounded(?:$|\s)")
CHILD_BASE = {
    "LANG": "C", "LC_ALL": "C", "NO_COLOR": "1", "TERM": "dumb",
    "TZ": "UTC", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
}
MAX_CAPTURE_BYTES = 64 * 1024 * 1024


class ClosureError(RuntimeError):
    """A governed invariant failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ClosureError(message)


def sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with file_path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ClosureError(f"cannot hash {file_path.name}") from error
    return digest.hexdigest()


def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        require(key not in value, "duplicate JSON field")
        value[key] = item
    return value


def load_json(file_path: Path) -> dict[str, Any]:
    try:
        with file_path.open(encoding="utf-8") as stream:
            value = json.load(stream, object_pairs_hook=reject_duplicates)
    except (OSError, json.JSONDecodeError) as error:
        raise ClosureError(f"cannot read {file_path.name}") from error
    require(isinstance(value, dict), f"{file_path.name} must contain an object")
    return value


def strict_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    require(isinstance(value, dict) and set(value) == keys, f"{label} fields differ")
    return value


def canonical_root(raw: str, *, exists: bool, empty: bool = False) -> Path:
    supplied = Path(raw)
    require(supplied.is_absolute(), "all path inputs must be absolute")
    require(not supplied.is_symlink(), "path inputs must not be symlinks")
    if exists:
        require(supplied.exists(), "required path does not exist")
        resolved = supplied.resolve(strict=True)
        require(resolved == supplied, "path input must already be canonical")
        require(resolved.is_dir(), "required root is not a directory")
        if empty:
            require(not any(resolved.iterdir()), "output directory must be empty")
        return resolved
    require(not supplied.exists(), "work root must not exist")
    parent = supplied.parent.resolve(strict=True)
    require(parent / supplied.name == supplied, "work root must be canonical")
    return supplied


def executable(raw: str) -> Path:
    supplied = Path(raw)
    require(supplied.is_absolute() and not supplied.is_symlink(),
            "tool paths must be absolute non-symlinks")
    resolved = supplied.resolve(strict=True)
    require(resolved == supplied and resolved.is_file() and os.access(resolved, os.X_OK),
            "tool path must name a canonical executable file")
    return resolved


def disjoint_roots(roots: list[Path]) -> None:
    identities: set[tuple[int, int]] = set()
    for root in roots:
        identity = (root.stat().st_dev, root.stat().st_ino) if root.exists() else None
        if identity is not None:
            require(identity not in identities, "root aliases another root")
            identities.add(identity)
    for index, first in enumerate(roots):
        for second in roots[index + 1:]:
            require(first not in second.parents and second not in first.parents,
                    "roots must not overlap")


def child_environment(tmpdir: Path, tools: dict[str, Path]) -> dict[str, str]:
    environment = dict(CHILD_BASE)
    environment.update({
        "TMPDIR": str(tmpdir),
        "CARGO": str(tools["cargo"]),
        "RUSTC": str(tools["rustc"]),
        "CARGO_NET_OFFLINE": "true",
        "CARGO_TERM_COLOR": "never",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "CCACHE_DISABLE": "1",
        "CCACHE_CONFIGPATH": "/dev/null",
    })
    return environment


def git_environment(tmpdir: Path) -> dict[str, str]:
    environment = dict(CHILD_BASE)
    environment.update({
        "TMPDIR": str(tmpdir), "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
        "GIT_ATTR_NOSYSTEM": "1",
    })
    return environment


def git_command(source: Path, *args: str, extra_config: tuple[str, ...] = ()) -> list[str]:
    command = ["git", "-c", f"safe.directory={source}"]
    for item in extra_config:
        command.extend(("-c", item))
    return [*command, "-C", str(source), *args]


def capture_bounded(command: list[str], environment: dict[str, str],
                    label: str, timeout: int = 120,
                    max_capture_bytes: int = MAX_CAPTURE_BYTES) -> bytes:
    require(1 <= max_capture_bytes <= MAX_CAPTURE_BYTES,
            "capture byte bound is invalid")

    def bound_output_files() -> None:
        _, hard_limit = resource.getrlimit(resource.RLIMIT_FSIZE)
        child_limit = (max_capture_bytes if hard_limit == resource.RLIM_INFINITY
                       else min(max_capture_bytes, hard_limit))
        resource.setrlimit(resource.RLIMIT_FSIZE, (child_limit, child_limit))

    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                env=environment, start_new_session=True,
                preexec_fn=bound_output_files,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ClosureError(f"{label} failed") from error
        timed_out = False
        try:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
        finally:
            terminate_group(process)
        require(not timed_out and process.returncode == 0, f"{label} failed")
        require(stdout.tell() <= max_capture_bytes and
                stderr.tell() <= max_capture_bytes,
                f"{label} exceeded the capture bound")
        stdout.seek(0)
        return stdout.read()


def git_output(source: Path, *args: str) -> str:
    with tempfile.TemporaryDirectory(prefix="ergon-index-git-") as temporary:
        output = capture_bounded(
            git_command(source, *args), git_environment(Path(temporary)),
            "Git query",
        )
    return output.decode("ascii", errors="strict").strip()


def require_history(source: Path, candidate_commit: str) -> None:
    require(git_output(source, "rev-parse", "--is-shallow-repository") == "false",
            "source history must be complete")
    require(not git_output(source, "for-each-ref", "--format=%(refname)", "refs/replace"),
            "replace refs are forbidden")
    grafts = Path(git_output(
        source, "rev-parse", "--path-format=absolute", "--git-path",
        "info/grafts",
    ))
    require(not grafts.exists() or grafts.stat().st_size == 0, "grafts are forbidden")
    objects = git_output(source, "rev-list", "--objects", "--missing=print",
                         candidate_commit).splitlines()
    require(objects and not any(line.startswith("?") for line in objects),
            "source history contains missing objects")
    git_output(source, "fsck", "--connectivity-only", "--no-dangling",
               candidate_commit)


def fetch_public_main(source: Path, candidate_commit: str, bare: Path,
                      environment: dict[str, str]) -> Path:
    require(not git_output(source, "status", "--porcelain=v1", "--untracked-files=all"),
            "source tree must be clean before public fetch")
    require(git_output(source, "config", "--local", "--get",
                       "remote.origin.url") == PUBLIC_URL,
            "origin URL is not the public repository")
    require(not bare.exists(), "temporary public Git root already exists")
    run_checked(
        ["git", "init", "--bare", "--quiet", str(bare)],
        environment, 60, "temporary public Git initialization",
    )
    run_checked(
        git_command(bare, "fetch", "--no-tags", "--force", "--quiet",
                    PUBLIC_URL, "+refs/heads/main:refs/heads/main"),
        environment, 300, "public Git fetch",
    )
    run_checked(
        git_command(bare, "symbolic-ref", "HEAD", "refs/heads/main"),
        environment, 30, "temporary public Git HEAD binding",
    )
    fetched_commit = git_output(bare, "rev-parse", "refs/heads/main^{commit}")
    require(fetched_commit == candidate_commit,
            "fresh public main is not the candidate")
    return bare.resolve(strict=True)


def verify_trust_root() -> None:
    require(len(SIGNING_PUBLIC_KEY) == 124 and
            hashlib.sha256(SIGNING_PUBLIC_KEY).hexdigest() == SIGNING_PUBLIC_KEY_SHA256,
            "signing key bytes differ")
    require(len(ALLOWED_SIGNERS) == 128 and
            hashlib.sha256(ALLOWED_SIGNERS).hexdigest() == ALLOWED_SIGNERS_SHA256,
            "allowed signers bytes differ")


def verify_signature(source: Path, commit: str) -> dict[str, str]:
    verify_trust_root()
    with tempfile.TemporaryDirectory(prefix="ergon-index-signature-") as temporary:
        temp = Path(temporary)
        allowed = temp / "allowed_signers"
        revoked = temp / "revocations"
        allowed.write_bytes(ALLOWED_SIGNERS)
        revoked.write_bytes(b"")
        os.chmod(allowed, 0o600)
        config = (
            "gpg.format=ssh", f"gpg.ssh.allowedSignersFile={allowed}",
            f"gpg.ssh.revocationFile={revoked}", "gpg.ssh.program=/usr/bin/ssh-keygen",
            "gpg.minTrustLevel=fully",
        )
        environment = git_environment(temp)
        capture_bounded(
            git_command(source, "verify-commit", "--raw", commit, extra_config=config),
            environment, "Git signature verification",
        )
        fields = capture_bounded(
            git_command(source, "show", "-s", "--format=%G?%x00%GF%x00%GK%x00%GS",
                        commit, extra_config=config),
            environment, "Git signature identity",
        ).decode("ascii", errors="strict").rstrip("\n").split("\0")
    require(fields == ["G", SIGNING_FINGERPRINT, SIGNING_FINGERPRINT, SIGNING_PRINCIPAL],
            "commit signer identity differs")
    return {"commit": commit, "fingerprint": fields[1], "principal": fields[3],
            "status": fields[0], "verification_result": "valid"}


def validate_record(source: Path, public_repo: Path) -> dict[str, Any]:
    record = load_json(source / RECORD_PATH)
    require(record.get("change_id") == "ERGON-CHANGE-0033" and
            record.get("stage") == "optional-indexing" and
            record.get("status") == "under-review", "change record identity differs")
    require(record.get("record_path") == str(RECORD_PATH), "record path differs")
    boundaries = record.get("boundaries", {})
    require(boundaries == {
        "consensus_authority": "standalone-node",
        "chronik_role": "observe-and-index-only",
        "chronik_consensus_authority": False,
        "chronik_mempool_authority": False,
        "chronik_activation_authority": False,
        "chronik_chain_selection_authority": False,
        "chronik_required_for_correctness": False,
        "chronik_compile_time_default": "off",
        "chronik_runtime_default": "off",
        "chronik_enabled_scope": "local-regtest-opt-in-only",
        "mainnet_parameters_modified": False,
    }, "authority boundary differs")
    verification = record.get("verification", {})
    require(tuple(item.get("role") for item in verification.get("builds", [])) == BUILD_ROLES,
            "build roles differ")
    require(tuple(item.get("id") for item in verification.get("scenarios", [])) == SCENARIOS,
            "scenario inventory differs")
    evidence = record.get("evidence", {})
    require(evidence.get("delivery_state") == "planned" and
            evidence.get("knowledge_status") == "Open Question" and
            record.get("decision", {}).get("status") == "pending",
            "record must remain planned and pending")
    harness = source / HARNESS_PATH
    require(harness.is_file() and not harness.is_symlink() and
            os.access(harness, os.X_OK) and
            harness.stat().st_mode & 0o777 == 0o755,
            "recorded harness must be a regular non-symlink")
    committed_blob = git_output(public_repo, "rev-parse",
                                f"HEAD:{HARNESS_PATH}")
    require(git_output(public_repo, "ls-tree", "HEAD", "--",
                       str(HARNESS_PATH)) ==
            f"100755 blob {committed_blob}\t{HARNESS_PATH}",
            "public harness mode or tree entry differs")
    expected_identity = {
        "mode": "100755", "bytes": harness.stat().st_size,
        "git_blob": committed_blob,
        "sha256": sha256_file(harness),
    }
    files = record.get("files", [])
    require(len(files) == 1 and files[0].get("path") == str(HARNESS_PATH) and
            files[0].get("action") == "add" and files[0].get("before") is None and
            files[0].get("after") == expected_identity,
            "recorded harness postimage differs")
    for relative in (HARNESS_PATH, RECORD_PATH):
        current_blob = git_output(source, "hash-object", "--no-filters",
                                  str(source / relative))
        committed_blob = git_output(public_repo, "rev-parse",
                                    f"HEAD:{relative}")
        require(current_blob == committed_blob,
                "governed working file differs from the signed tree")
    return record


def validate_source(source: Path, public_repo: Path,
                    candidate_commit: str, candidate_tree: str,
                    parent_commit: str, parent_tree: str) -> dict[str, Any]:
    require(HEX40.fullmatch(candidate_commit) is not None and
            HEX40.fullmatch(candidate_tree) is not None, "candidate identity is malformed")
    require(parent_commit == INTEGRATION_PARENT_COMMIT and parent_tree == INTEGRATION_PARENT_TREE,
            "integration parent input differs")
    require(not git_output(source, "status", "--porcelain=v1", "--untracked-files=all"),
            "source tree must be clean")
    require(all(line.startswith("H ") for line in
                git_output(source, "ls-files", "-v").splitlines()),
            "tracked files use assume-unchanged or skip-worktree")
    require_history(public_repo, candidate_commit)
    actual = {"commit": git_output(public_repo, "rev-parse", "HEAD^{commit}"),
              "tree": git_output(public_repo, "rev-parse", "HEAD^{tree}")}
    require(actual == {"commit": candidate_commit, "tree": candidate_tree},
            "public candidate identity differs")
    require({"commit": git_output(source, "rev-parse", "HEAD^{commit}"),
             "tree": git_output(source, "rev-parse", "HEAD^{tree}")} == actual,
            "checkout identity differs from the public candidate")
    actual_parent = git_output(public_repo, "rev-parse", "HEAD^")
    actual_parent_tree = git_output(public_repo, "rev-parse", "HEAD^^{tree}")
    require((actual_parent, actual_parent_tree) == (parent_commit, parent_tree),
            "candidate is not the direct child of the reviewed parent")
    require_direct_parent(
        git_output(public_repo, "rev-list", "--parents", "-n", "1",
                   candidate_commit),
        candidate_commit, parent_commit,
    )
    require(git_output(public_repo, "rev-parse",
                       f"{PUBLIC_ROOT_COMMIT}^{{tree}}") == BASELINE_TREE and
            git_output(public_repo, "rev-list", "--parents", "-n", "1",
                       PUBLIC_ROOT_COMMIT) ==
            PUBLIC_ROOT_COMMIT, "public root identity differs")
    require(git_output(source, "config", "--local", "--get",
                       "remote.origin.url") == PUBLIC_URL,
            "origin URL is not the public repository")
    require(git_output(source, "rev-parse",
                       "refs/remotes/origin/main^{commit}") == candidate_commit and
            git_output(public_repo, "rev-parse",
                       "refs/heads/main^{commit}") == candidate_commit,
            "public origin/main is not the candidate")
    changed = tuple(sorted(filter(None, git_output(
        public_repo, "diff", "--name-only", parent_commit, candidate_commit,
    ).splitlines())))
    require(changed == EXPECTED_CHANGED_PATHS, "candidate diff contains an unexpected path")
    statuses = git_output(public_repo, "diff", "--name-status",
                          parent_commit, candidate_commit).splitlines()
    require(all(line.startswith("A\t") for line in statuses), "candidate paths must be additions")
    validate_record(source, public_repo)
    signatures = {
        "public_root": verify_signature(public_repo, PUBLIC_ROOT_COMMIT),
        "integration_parent": verify_signature(public_repo, parent_commit),
        "candidate": verify_signature(public_repo, candidate_commit),
    }
    return {**actual, "parent_commit": parent_commit, "parent_tree": parent_tree,
            "signatures": signatures}


def require_direct_parent(revision_line: str, candidate: str, parent: str) -> None:
    require(revision_line.split() == [candidate, parent],
            "candidate must have exactly one reviewed parent")


def validate_cargo_lock(lock_path: Path) -> tuple[tuple[str, str, str], ...]:
    text = lock_path.read_text(encoding="utf-8")
    require('source = "git+' not in text and 'source = "path+' not in text,
            "Cargo.lock contains a non-registry dependency")
    packages = text.split("[[package]]")[1:]
    require(packages, "Cargo.lock contains no package entries")
    registry_packages: list[tuple[str, str, str]] = []
    for package in packages:
        source = re.search(r'^source = "([^"]+)"$', package, re.MULTILINE)
        if source is None:
            continue
        require(source.group(1) ==
                "registry+https://github.com/rust-lang/crates.io-index",
                "Cargo.lock contains an unexpected registry")
        name = re.search(r'^name = "([^"]+)"$', package, re.MULTILINE)
        version = re.search(r'^version = "([^"]+)"$', package, re.MULTILINE)
        checksum = re.search(r'^checksum = "([0-9a-f]{64})"$',
                             package, re.MULTILINE)
        require(name is not None and version is not None and checksum is not None,
                "registry dependency is not fully locked")
        registry_packages.append((name.group(1), version.group(1),
                                  checksum.group(1)))
    require(registry_packages and len(registry_packages) ==
            len(set(registry_packages)), "registry lock inventory is invalid")
    return tuple(sorted(registry_packages))


def cargo_seed_manifest(root: Path) -> str:
    entries = []
    for item in sorted(root.rglob("*")):
        require(not item.is_symlink(), "copied Cargo input contains a symlink")
        if item.is_file():
            entries.append({
                "bytes": item.stat().st_size,
                "path": item.relative_to(root).as_posix(),
                "sha256": sha256_file(item),
            })
    require(entries, "copied Cargo input is empty")
    encoded = (json.dumps(entries, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True) + "\n").encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def source_manifest(root: Path) -> str:
    entries = []
    for item in sorted(root.rglob("*")):
        require(not item.is_symlink(), "exported source contains a symlink")
        if item.is_file():
            permissions = item.stat().st_mode & 0o777
            require(permissions in {0o644, 0o755},
                    "materialized source mode changed")
            data = item.read_bytes()
            entries.append({
                "blob": hashlib.sha1(
                    f"blob {len(data)}\0".encode("ascii") + data
                ).hexdigest(),
                "bytes": len(data),
                "mode": "100755" if permissions == 0o755 else "100644",
                "path": item.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(data).hexdigest(),
            })
    require(entries, "exported source is empty")
    encoded = (json.dumps(entries, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True) + "\n").encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def copy_cargo_seed(seed: Path, destination: Path,
                    packages: tuple[tuple[str, str, str], ...]) -> str:
    require(seed.is_dir() and not seed.is_symlink(), "Cargo cache seed is invalid")
    forbidden = {"credentials", "credentials.toml", "config", "config.toml"}
    require(not forbidden.intersection(item.name for item in seed.iterdir()),
            "Cargo home contains credential or configuration overrides")
    for current, directories, files in os.walk(seed, followlinks=False):
        current_path = Path(current)
        for name in [*directories, *files]:
            require(not (current_path / name).is_symlink(), "Cargo cache contains a symlink")
    registry = seed / "registry"
    index = registry / "index"
    cache = registry / "cache"
    require(index.is_dir() and cache.is_dir() and not index.is_symlink() and
            not cache.is_symlink(), "Cargo registry index or archive cache is absent")
    destination.mkdir(mode=0o700)
    shutil.copytree(index, destination / "registry" / "index", symlinks=False)
    for name, version, checksum in packages:
        archives = [item for item in cache.glob(f"*/{name}-{version}.crate")
                    if item.is_file() and not item.is_symlink()]
        require(len(archives) == 1, "locked Cargo archive is absent or ambiguous")
        archive = archives[0]
        require(sha256_file(archive) == checksum,
                "Cargo archive checksum differs from Cargo.lock")
        target = destination / "registry" / "cache" / archive.parent.name / archive.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, target)
    require(not (destination / "registry" / "src").exists(),
            "pre-extracted Cargo sources are forbidden")
    return cargo_seed_manifest(destination)


def signed_tree_inventory(source: Path, candidate_commit: str,
                          environment: dict[str, str]) -> list[dict[str, str]]:
    output = capture_bounded(
        git_command(source, "ls-tree", "-rz", "--full-tree", candidate_commit),
        environment, "signed tree inventory",
    )
    entries: list[dict[str, str]] = []
    for encoded in output.split(b"\0"):
        if not encoded:
            continue
        try:
            header, raw_path = encoded.split(b"\t", 1)
            mode, object_type, blob = header.decode("ascii").split()
            relative = raw_path.decode("utf-8", errors="strict")
        except (ValueError, UnicodeDecodeError) as error:
            raise ClosureError("signed tree inventory is malformed") from error
        relative_path = Path(relative)
        require(object_type == "blob" and mode in {"100644", "100755"} and
                not relative_path.is_absolute() and
                ".." not in relative_path.parts and
                ".git" not in {part.casefold() for part in relative_path.parts},
                "signed tree contains an unsupported entry")
        entries.append({"blob": blob, "mode": mode, "path": relative})
    require(entries and [item["path"] for item in entries] ==
            sorted(item["path"] for item in entries),
            "signed tree inventory is empty, duplicated, or unordered")
    return entries


def materialize_signed_tree(source: Path, destination: Path,
                            candidate_commit: str,
                            environment: dict[str, str]) -> tuple[Path, str]:
    require(not destination.exists(), "materialization destination already exists")
    inventory = signed_tree_inventory(source, candidate_commit, environment)
    index_path = destination.parent / f"{destination.name}.index"
    require(not index_path.exists(), "materialization index already exists")
    index_environment = {**environment, "GIT_INDEX_FILE": str(index_path)}
    run_checked(
        git_command(source, "read-tree", candidate_commit),
        index_environment, 120, "signed tree index seed",
    )
    destination.mkdir(mode=0o700)
    run_checked(
        ["git", "-c", f"safe.directory={source}", f"--git-dir={source}",
         f"--work-tree={destination}", "checkout-index", "--all", "--force"],
        index_environment, 300, "signed tree materialization",
    )
    expected_paths = [item["path"] for item in inventory]
    actual_paths = sorted(
        item.relative_to(destination).as_posix()
        for item in destination.rglob("*") if item.is_file()
    )
    require(actual_paths == expected_paths,
            "materialized path inventory differs from the signed tree")
    materialized: list[dict[str, Any]] = []
    for entry in inventory:
        file_path = destination / entry["path"]
        require(file_path.is_file() and not file_path.is_symlink(),
                "materialized source entry is not a regular file")
        expected_mode = 0o755 if entry["mode"] == "100755" else 0o644
        os.chmod(file_path, expected_mode)
        data = file_path.read_bytes()
        blob = hashlib.sha1(
            f"blob {len(data)}\0".encode("ascii") + data
        ).hexdigest()
        require(blob == entry["blob"] and
                file_path.stat().st_mode & 0o777 == expected_mode,
                "materialized mode, blob, or raw bytes differ")
        materialized.append({
            "blob": blob, "bytes": len(data), "mode": entry["mode"],
            "path": entry["path"], "sha256": hashlib.sha256(data).hexdigest(),
        })
    encoded = (json.dumps(materialized, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True) + "\n").encode("ascii")
    manifest = hashlib.sha256(encoded).hexdigest()
    for relative in (HARNESS_PATH, RECORD_PATH, LOCK_PATH):
        require((destination / relative).is_file(),
                "materialized source is incomplete")
    return destination.resolve(strict=True), manifest


def run_checked(command: list[str], environment: dict[str, str], timeout: int,
                label: str) -> None:
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=environment, start_new_session=True,
        )
    except OSError as error:
        raise ClosureError(f"{label} failed") from error
    timed_out = False
    try:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
    finally:
        terminate_group(process)
    require(not timed_out and process.returncode == 0, f"{label} failed")


def configure_build(source: Path, build: Path, role: str, tools: dict[str, Path],
                    cargo_home: Path, jobs: int, environment: dict[str, str]) -> None:
    require(not build.exists(), "build root already exists")
    common = [
        str(tools["cmake"]), "-S", str(source), "-B", str(build), "-GNinja",
        "-DCMAKE_BUILD_TYPE=RelWithDebInfo", f"-DCMAKE_MAKE_PROGRAM={tools['ninja']}",
        f"-DCMAKE_C_COMPILER={tools['cc']}", f"-DCMAKE_CXX_COMPILER={tools['cxx']}",
        "-DBUILD_BITCOIN_QT=OFF", "-DBUILD_BITCOIN_WALLET=OFF",
        "-DBUILD_BITCOIN_ZMQ=OFF", "-DENABLE_UPNP=OFF", "-DCCACHE=OFF",
    ]
    options = {
        "compiled-out": ["-DBUILD_CHRONIK_BUILD_ONLY=OFF", "-DBUILD_CHRONIK_OBSERVER=OFF"],
        "build-only": ["-DBUILD_CHRONIK_BUILD_ONLY=ON", "-DBUILD_CHRONIK_OBSERVER=OFF"],
        "observer": ["-DBUILD_CHRONIK_BUILD_ONLY=OFF", "-DBUILD_CHRONIK_OBSERVER=ON"],
    }[role]
    if role != "compiled-out":
        options.extend((f"-DCHRONIK_CARGO_HOME={cargo_home}",
                        f"-DCHRONIK_CARGO_EXECUTABLE={tools['cargo']}",
                        f"-DCHRONIK_RUSTC_EXECUTABLE={tools['rustc']}"))
    run_checked([*common, *options], environment, 900, f"configure {role}")
    cache = (build / "CMakeCache.txt").read_text(encoding="utf-8")
    require(re.search(r"^CCACHE:(?:FILEPATH|UNINITIALIZED)=OFF$", cache,
                      re.MULTILINE) is not None and
            "CMAKE_C_COMPILER_LAUNCHER" not in cache and
            "CMAKE_CXX_COMPILER_LAUNCHER" not in cache,
            "C/C++ compiler cache was not disabled")
    target = "chronik_build_only" if role == "build-only" else "bitcoind"
    run_checked([str(tools["cmake"]), "--build", str(build), "--target", target,
                 "--parallel", str(jobs)], environment, 7200, f"build {role}")


def check_build_config(build: Path, source: Path, expected_observer: bool) -> dict[str, Any]:
    config_path = build / "test" / "config.ini"
    binary = build / "src" / "bitcoind"
    require(config_path.is_file() and binary.is_file() and os.access(binary, os.X_OK),
            "node build output is incomplete")
    config = configparser.ConfigParser()
    with config_path.open(encoding="utf-8") as stream:
        config.read_file(stream)
    require(Path(config["environment"]["BUILDDIR"]).resolve(strict=True) == build and
            Path(config["environment"]["SRCDIR"]).resolve(strict=True) == source,
            "config.ini source/build binding differs")
    cache = (build / "CMakeCache.txt").read_text(encoding="utf-8")
    require(f"BUILD_CHRONIK_OBSERVER:BOOL={'ON' if expected_observer else 'OFF'}" in cache,
            "observer CMake state differs")
    if not expected_observer:
        require("CHRONIK_CARGO_EXECUTABLE" not in cache and
                not (build / "cargo").exists(),
                "compiled-out graph discovered or created a Chronik Cargo edge")
    native_cache_path = build / "native" / "CMakeCache.txt"
    require(native_cache_path.is_file() and not native_cache_path.is_symlink(),
            "native sub-build cache is absent")
    native_cache = native_cache_path.read_text(encoding="utf-8")
    native_ccache = re.search(r"^CCACHE:FILEPATH=(.*)$", native_cache,
                              re.MULTILINE)
    require(native_ccache is not None and native_ccache.group(1) in {
        "CCACHE-NOTFOUND", "/usr/bin/ccache", "/bin/ccache",
    }, "native sub-build resolved an unexpected compiler cache")
    return {"binary": binary, "config": config_path,
            "bitcoind_bytes": binary.stat().st_size,
            "bitcoind_sha256": sha256_file(binary),
            "config_sha256": sha256_file(config_path)}


def check_symbols(binary: Path, nm: Path, expected: bool,
                  environment: dict[str, str]) -> None:
    try:
        result = subprocess.run([str(nm), "-g", str(binary)], check=True,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                env=environment, timeout=120)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ClosureError("binary symbol inspection failed") from error
    present = CHRONIK_SYMBOL.search(result.stdout) is not None
    require(present is expected, "observer binary linkage differs")


def process_group_gone(process_group: int, timeout: float) -> bool:
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


def terminate_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    if not process_group_gone(process.pid, 10):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
    require(process_group_gone(process.pid, 10), "child process group survived cleanup")


def run_functional(identifier: str, script: Path, config: Path, execution_root: Path,
                   portseed: int, environment: dict[str, str], timeout: int) -> None:
    cache = execution_root / "cache"
    temp = execution_root / "tmp"
    datadir = execution_root / "datadir"
    cache.mkdir(parents=True, mode=0o700)
    temp.mkdir(mode=0o700)
    require(not datadir.exists(), "functional datadir must be fresh")
    command = [
        sys.executable, "-s", "-B", str(script), f"--configfile={config}",
        f"--cachedir={cache}", f"--tmpdir={datadir}", f"--portseed={portseed}",
        "--hermetic-child-env",
    ]
    process = subprocess.Popen(command, cwd=script.parents[2], env={**environment, "TMPDIR": str(temp)},
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise ClosureError(f"functional execution timed out: {identifier}") from error
    finally:
        terminate_group(process)
    require(process.returncode not in (77,), f"functional execution skipped: {identifier}")
    require(process.returncode == 0, f"functional execution failed: {identifier}")


def validate_report(report: dict[str, Any]) -> None:
    strict_object(report, REPORT_KEYS, "receipt")
    require(report["schema"] == SCHEMA and report["result"] == "pass" and
            report["reason_code"] == "all-governed-checks-passed" and
            report["knowledge_status"] == "Observed" and
            report["evidence_ceiling"] == "local-assembled-runtime",
            "receipt disposition differs")
    target = strict_object(report["target"], {
        "candidate_commit", "candidate_tree", "integration_parent_commit",
        "integration_parent_tree", "record_sha256", "harness_sha256",
        "cargo_lock_sha256", "cargo_seed_manifest_sha256", "signatures",
    }, "target")
    for field in ("candidate_commit", "candidate_tree", "integration_parent_commit",
                  "integration_parent_tree"):
        require(HEX40.fullmatch(target[field]) is not None, f"{field} is malformed")
    for field in ("record_sha256", "harness_sha256", "cargo_lock_sha256",
                  "cargo_seed_manifest_sha256"):
        require(HEX64.fullmatch(target[field]) is not None, f"{field} is malformed")
    require(target["integration_parent_commit"] == INTEGRATION_PARENT_COMMIT and
            target["integration_parent_tree"] == INTEGRATION_PARENT_TREE,
            "receipt integration parent differs")
    signatures = strict_object(
        target["signatures"], {"public_root", "integration_parent", "candidate"},
        "target signatures",
    )
    for signature in signatures.values():
        strict_object(signature, {
            "commit", "fingerprint", "principal", "status", "verification_result",
        }, "signature")
        require(HEX40.fullmatch(signature["commit"]) is not None and
                signature["fingerprint"] == SIGNING_FINGERPRINT and
                signature["principal"] == SIGNING_PRINCIPAL and
                signature["status"] == "G" and
                signature["verification_result"] == "valid",
                "signature receipt differs")
    require(signatures["public_root"]["commit"] == PUBLIC_ROOT_COMMIT and
            signatures["integration_parent"]["commit"] ==
            target["integration_parent_commit"] and
            signatures["candidate"]["commit"] == target["candidate_commit"],
            "signature receipts do not bind the target")
    require(report["environment"] == {
        "closed_child_environment": True, "network_dependency_fetch": False,
        "cargo_locked": True, "cargo_offline": True, "parallel_jobs_bounded": True,
        "python_main_isolated": True, "python_user_site_disabled": True,
        "tool_versions_recorded": False,
    }, "environment receipt differs")
    require(all(set(item) == {"role", "result", "bitcoind_sha256"} and
                item["result"] == "pass" and
                HEX64.fullmatch(item["bitcoind_sha256"]) is not None
                for item in report["builds"]), "build receipt fields differ")
    require([item["role"] for item in report["builds"]] == list(BUILD_ROLES),
            "receipt build roles differ")
    require(report["builds"][1]["bitcoind_sha256"] ==
            report["builds"][2]["bitcoind_sha256"],
            "disabled and opt-in roles do not share the observer binary")
    require(report["builds"][0]["bitcoind_sha256"] !=
            report["builds"][1]["bitcoind_sha256"],
            "compiled-out and observer binaries unexpectedly alias")
    expected_checks = [
        "compiled-out-inherited-mining", "build-only-rust-workspace",
        "observer-rust-units", "compiled-in-disabled-inherited-mining",
        "asset-diagnostics", "pruned-storage-boundary",
    ]
    require(report["checks"] == [{"id": item, "result": "pass"}
                                  for item in expected_checks],
            "supplemental checks differ")
    require([item["id"] for item in report["scenarios"]] == list(SCENARIOS) and
            all(item == {"id": item["id"], "result": "pass"}
                for item in report["scenarios"]), "receipt scenarios differ")
    claims = report["claims"]
    require(claims == {
        "default_off_correctness_observed": True,
        "local_bounded_observer_behavior_observed": True,
        "deterministic_build": "not_claimed", "public_reproduction": "not_claimed",
        "mainnet_behavior": "not_claimed", "testnet_behavior": "not_claimed",
        "native_asset_validity": "not_claimed", "persistence": "not_claimed",
        "stable_api": "not_claimed",
    }, "receipt claims differ")
    require(report["invariants"] == {
        "compiled_out_has_no_observer_symbol": True,
        "compiled_in_disabled_and_opt_in_same_binary": True,
        "three_fresh_build_graphs": True, "fresh_isolated_test_state": True,
        "signed_tree_materializations_verified": True,
        "source_identity_unchanged": True, "binary_identities_unchanged": True,
        "process_groups_cleaned": True, "build_only_is_supplemental": True,
    }, "receipt invariants differ")
    require(report["privacy"] == {
        "absolute_paths_recorded": False, "environment_values_recorded": False,
        "raw_output_recorded": False, "cache_or_datadir_contents_recorded": False,
        "process_data_recorded": False,
    }, "privacy receipt differs")
    require(report["limitations"] == [
        "This is a local assembled-runtime observation, not an independent public reproduction.",
        "It does not establish deterministic builds, persistence, a stable API, or behavior on public networks.",
        "The candidate commit demonstrates the final assembled optional observer, not each historical intermediate identity.",
    ], "receipt limitations differ")
    def strings(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            return [item for child in value.values() for item in strings(child)]
        if isinstance(value, list):
            return [item for child in value for item in strings(child)]
        return []
    require(all(not item.startswith("/") and "/Users/" not in item and
                "/home/" not in item for item in strings(report)),
            "receipt exposes forbidden host data")


def make_report(identity: dict[str, Any], source: Path,
                build_data: dict[str, dict[str, Any]],
                cargo_seed_manifest_sha256: str) -> dict[str, Any]:
    observer = build_data["observer"]
    report = {
        "schema": SCHEMA, "result": "pass", "reason_code": "all-governed-checks-passed",
        "knowledge_status": "Observed", "evidence_ceiling": "local-assembled-runtime",
        "target": {
            "candidate_commit": identity["commit"], "candidate_tree": identity["tree"],
            "integration_parent_commit": identity["parent_commit"],
            "integration_parent_tree": identity["parent_tree"],
            "record_sha256": sha256_file(source / RECORD_PATH),
            "harness_sha256": sha256_file(source / HARNESS_PATH),
            "cargo_lock_sha256": sha256_file(source / LOCK_PATH),
            "cargo_seed_manifest_sha256": cargo_seed_manifest_sha256,
            "signatures": identity["signatures"],
        },
        "environment": {
            "closed_child_environment": True, "network_dependency_fetch": False,
            "cargo_locked": True, "cargo_offline": True, "parallel_jobs_bounded": True,
            "python_main_isolated": True, "python_user_site_disabled": True,
            "tool_versions_recorded": False,
        },
        "builds": [
            {"role": "compiled-out", "result": "pass",
             "bitcoind_sha256": build_data["compiled-out"]["bitcoind_sha256"]},
            {"role": "compiled-in-disabled", "result": "pass",
             "bitcoind_sha256": observer["bitcoind_sha256"]},
            {"role": "local-regtest-indexing", "result": "pass",
             "bitcoind_sha256": observer["bitcoind_sha256"]},
        ],
        "checks": [
            {"id": "compiled-out-inherited-mining", "result": "pass"},
            {"id": "build-only-rust-workspace", "result": "pass"},
            {"id": "observer-rust-units", "result": "pass"},
            {"id": "compiled-in-disabled-inherited-mining", "result": "pass"},
            {"id": "asset-diagnostics", "result": "pass"},
            {"id": "pruned-storage-boundary", "result": "pass"},
        ],
        "scenarios": [{"id": item, "result": "pass"} for item in SCENARIOS],
        "invariants": {
            "compiled_out_has_no_observer_symbol": True,
            "compiled_in_disabled_and_opt_in_same_binary": True,
            "three_fresh_build_graphs": True, "fresh_isolated_test_state": True,
            "signed_tree_materializations_verified": True,
            "source_identity_unchanged": True, "binary_identities_unchanged": True,
            "process_groups_cleaned": True, "build_only_is_supplemental": True,
        },
        "claims": {
            "default_off_correctness_observed": True,
            "local_bounded_observer_behavior_observed": True,
            "deterministic_build": "not_claimed", "public_reproduction": "not_claimed",
            "mainnet_behavior": "not_claimed", "testnet_behavior": "not_claimed",
            "native_asset_validity": "not_claimed", "persistence": "not_claimed",
            "stable_api": "not_claimed",
        },
        "privacy": {
            "absolute_paths_recorded": False, "environment_values_recorded": False,
            "raw_output_recorded": False, "cache_or_datadir_contents_recorded": False,
            "process_data_recorded": False,
        },
        "limitations": [
            "This is a local assembled-runtime observation, not an independent public reproduction.",
            "It does not establish deterministic builds, persistence, a stable API, or behavior on public networks.",
            "The candidate commit demonstrates the final assembled optional observer, not each historical intermediate identity.",
        ],
    }
    validate_report(report)
    return report


def write_report(output: Path, report: dict[str, Any]) -> None:
    report_path = output / "optional-indexing-closure.json"
    require(not report_path.exists(), "receipt already exists")
    try:
        with report_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except OSError as error:
        report_path.unlink(missing_ok=True)
        raise ClosureError("cannot write receipt") from error


def execute(args: argparse.Namespace) -> None:
    source = canonical_root(args.repository_root, exists=True)
    output = canonical_root(args.output_dir, exists=True, empty=True)
    seed = canonical_root(args.cargo_cache_seed, exists=True)
    work = canonical_root(args.work_root, exists=False)
    tools = {name: executable(getattr(args, name)) for name in
             ("cmake", "ninja", "cc", "cxx", "cargo", "rustc", "nm")}
    require(1 <= args.jobs <= 8, "jobs must be between 1 and 8")
    disjoint_roots([source, output, seed, work])
    work.mkdir(mode=0o700)
    report: dict[str, Any] | None = None
    try:
        temp = work / "tmp"
        temp.mkdir(mode=0o700)
        public_repo = fetch_public_main(
            source, args.expected_candidate_commit, work / "public.git",
            git_environment(temp),
        )
        identity = validate_source(
            source, public_repo, args.expected_candidate_commit,
            args.expected_candidate_tree,
            args.expected_integration_parent_commit,
            args.expected_integration_parent_tree,
        )
        pre_source = {
            "commit": identity["commit"], "tree": identity["tree"],
            "record": sha256_file(source / RECORD_PATH),
            "harness": sha256_file(source / HARNESS_PATH),
            "lock": sha256_file(source / LOCK_PATH),
        }
        materialized = {
            role: materialize_signed_tree(
                public_repo, work / f"source-{role}", identity["commit"],
                git_environment(temp),
            )
            for role in ("compiled-out", "build-only", "observer")
        }
        build_sources = {
            role: result[0] for role, result in materialized.items()
        }
        source_manifests = {
            role: result[1] for role, result in materialized.items()
        }
        require(len(set(source_manifests.values())) == 1,
                "fresh signed tree materializations differ")
        require(all(source_manifest(build_sources[role]) ==
                    source_manifests[role] for role in build_sources),
                "materialized source manifest does not bind the signed tree")
        cargo_packages = validate_cargo_lock(
            build_sources["observer"] / LOCK_PATH,
        )
        environment = child_environment(temp, tools)
        builds = {name: work / f"build-{name}" for name in
                  ("compiled-out", "build-only", "observer")}
        cargo_homes = {
            role: work / f"cargo-home-{role}"
            for role in ("build-only", "observer")
        }
        cargo_manifests = {
            role: copy_cargo_seed(seed, cargo_home, cargo_packages)
            for role, cargo_home in cargo_homes.items()
        }
        require(len(set(cargo_manifests.values())) == 1,
                "isolated Cargo input manifests differ")
        for role in ("compiled-out", "build-only", "observer"):
            cargo_role = "observer" if role == "observer" else "build-only"
            configure_build(
                build_sources[role], builds[role], role, tools,
                cargo_homes[cargo_role], args.jobs, environment,
            )
            require(source_manifest(build_sources[role]) ==
                    source_manifests[role],
                    f"{role} source changed during build")
        run_checked([str(tools["cmake"]), "--build", str(builds["build-only"]),
                     "--target", "check-chronik-build-only", "--parallel", str(args.jobs)],
                    environment, 7200, "build-only Rust tests")
        require(source_manifest(build_sources["build-only"]) ==
                source_manifests["build-only"],
                "build-only source changed during Rust tests")
        run_checked([str(tools["cmake"]), "--build", str(builds["observer"]),
                     "--target", "check-chronik-observer", "--parallel", str(args.jobs)],
                    environment, 7200, "observer Rust tests")
        require(source_manifest(build_sources["observer"]) ==
                source_manifests["observer"],
                "observer source changed during Rust tests")
        build_data = {
            "compiled-out": check_build_config(
                builds["compiled-out"], build_sources["compiled-out"], False,
            ),
            "observer": check_build_config(
                builds["observer"], build_sources["observer"], True,
            ),
        }
        check_symbols(build_data["compiled-out"]["binary"], tools["nm"], False, environment)
        check_symbols(build_data["observer"]["binary"], tools["nm"], True, environment)
        executions = (
            ("compiled-out-mining", "mining_basic.py", "compiled-out", 6101, 1800),
            ("compiled-in-disabled-mining", "mining_basic.py", "observer", 6102, 1800),
            ("block-observer", "feature_chronik_block_observer.py", "observer", 6103, 3600),
            ("asset-observer", "feature_chronik_asset_observer.py", "observer", 6104, 1800),
            ("pruned-observer", "feature_chronik_pruned_observer.py", "observer", 6105, 7200),
        )
        for identifier, script_name, build_name, portseed, timeout in executions:
            source_role = "compiled-out" if build_name == "compiled-out" else "observer"
            run_functional(identifier,
                           build_sources[source_role] / "test" / "functional" /
                           script_name,
                           build_data[build_name]["config"], work / "executions" / identifier,
                           portseed, environment, timeout)
            require(source_manifest(build_sources[source_role]) ==
                    source_manifests[source_role],
                    f"{source_role} source changed during functional tests")
        for item in build_data.values():
            require(sha256_file(item["binary"]) == item["bitcoind_sha256"] and
                    sha256_file(item["config"]) == item["config_sha256"],
                    "build identity changed during execution")
        post_source = {"commit": git_output(source, "rev-parse", "HEAD^{commit}"),
                       "tree": git_output(source, "rev-parse", "HEAD^{tree}"),
                       "record": sha256_file(source / RECORD_PATH),
                       "harness": sha256_file(source / HARNESS_PATH),
                       "lock": sha256_file(source / LOCK_PATH)}
        require(post_source == pre_source and not git_output(
            source, "status", "--porcelain=v1", "--untracked-files=all"),
            "source identity changed during execution")
        report = make_report(identity, source, build_data,
                             cargo_manifests["observer"])
    finally:
        shutil.rmtree(work, ignore_errors=True)
        require(not work.exists(), "work root survived cleanup")
    require(report is not None, "receipt was not produced")
    write_report(output, report)


def sample_report() -> dict[str, Any]:
    def signature(commit: str) -> dict[str, str]:
        return {"commit": commit, "fingerprint": SIGNING_FINGERPRINT,
                "principal": SIGNING_PRINCIPAL, "status": "G",
                "verification_result": "valid"}
    identity = {"commit": "1" * 40, "tree": "2" * 40,
                "parent_commit": INTEGRATION_PARENT_COMMIT,
                "parent_tree": INTEGRATION_PARENT_TREE,
                "signatures": {
                    "public_root": signature(PUBLIC_ROOT_COMMIT),
                    "integration_parent": signature(INTEGRATION_PARENT_COMMIT),
                    "candidate": signature("1" * 40),
                }}
    files = {"compiled-out": {"bitcoind_sha256": "3" * 64},
             "observer": {"bitcoind_sha256": "4" * 64}}
    with tempfile.TemporaryDirectory(prefix="ergon-index-selftest-") as temporary:
        root = Path(temporary)
        (root / RECORD_PATH.parent).mkdir(parents=True)
        (root / HARNESS_PATH.parent).mkdir(parents=True)
        (root / LOCK_PATH.parent).mkdir(parents=True)
        (root / RECORD_PATH).write_text("{}\n", encoding="utf-8")
        (root / HARNESS_PATH).write_text("test\n", encoding="utf-8")
        (root / LOCK_PATH).write_text("test\n", encoding="utf-8")
        return make_report(identity, root, files, "5" * 64)


def self_test(repository_root: str | None) -> None:
    verify_trust_root()
    report = sample_report()
    validate_report(report)
    mutations: list[tuple[str, Any]] = []
    for key in sorted(REPORT_KEYS):
        changed = json.loads(json.dumps(report))
        del changed[key]
        mutations.append((f"missing-{key}", changed))
    changed = json.loads(json.dumps(report)); changed["unexpected"] = True
    mutations.append(("extra-top-level", changed))
    changed = json.loads(json.dumps(report)); changed["knowledge_status"] = "Reproduced"
    mutations.append(("inflated-knowledge", changed))
    changed = json.loads(json.dumps(report)); changed["evidence_ceiling"] = "public"
    mutations.append(("inflated-ceiling", changed))
    changed = json.loads(json.dumps(report)); changed["claims"]["public_reproduction"] = True
    mutations.append(("public-claim", changed))
    changed = json.loads(json.dumps(report)); changed["claims"]["mainnet_behavior"] = True
    mutations.append(("mainnet-claim", changed))
    changed = json.loads(json.dumps(report)); changed["target"]["candidate_commit"] = "bad"
    mutations.append(("bad-commit", changed))
    changed = json.loads(json.dumps(report)); changed["target"]["record_sha256"] = "bad"
    mutations.append(("bad-digest", changed))
    changed = json.loads(json.dumps(report)); changed["builds"].reverse()
    mutations.append(("build-order", changed))
    changed = json.loads(json.dumps(report)); changed["scenarios"].pop()
    mutations.append(("missing-scenario", changed))
    changed = json.loads(json.dumps(report)); changed["scenarios"][0]["result"] = "skip"
    mutations.append(("scenario-skip", changed))
    changed = json.loads(json.dumps(report)); changed["limitations"].append("/Users/private")
    mutations.append(("absolute-path", changed))
    for field in sorted(report["environment"]):
        changed = json.loads(json.dumps(report)); changed["environment"][field] = None
        mutations.append((f"environment-{field}", changed))
    for field in sorted(report["privacy"]):
        changed = json.loads(json.dumps(report)); changed["privacy"][field] = True
        mutations.append((f"privacy-{field}", changed))
    for field in sorted(report["invariants"]):
        changed = json.loads(json.dumps(report)); changed["invariants"][field] = False
        mutations.append((f"invariant-{field}", changed))
    changed = json.loads(json.dumps(report))
    changed["builds"][2]["bitcoind_sha256"] = "5" * 64
    mutations.append(("observer-binary-alias", changed))
    changed = json.loads(json.dumps(report)); changed["checks"][0]["result"] = "fail"
    mutations.append(("check-failure", changed))
    changed = json.loads(json.dumps(report))
    changed["target"]["signatures"]["candidate"]["status"] = "U"
    mutations.append(("signature-status", changed))
    for label, mutation in mutations:
        try:
            validate_report(mutation)
        except ClosureError:
            continue
        raise ClosureError(f"self-test accepted mutation: {label}")
    parser = make_parser()
    try:
        parser.parse_args(["ru"])
    except SystemExit:
        pass
    else:
        raise ClosureError("argument parser permits abbreviations")
    try:
        require_direct_parent(
            f"{'1' * 40} {'2' * 40} {'3' * 40}", "1" * 40, "2" * 40,
        )
    except ClosureError:
        pass
    else:
        raise ClosureError("direct-parent check accepted a merge")
    with tempfile.TemporaryDirectory(prefix="ergon-index-contract-") as temporary:
        contract_root = Path(temporary).resolve(strict=True)
        empty = contract_root / "empty"
        empty.mkdir()
        require(canonical_root(str(empty), exists=True, empty=True) == empty,
                "empty canonical output was rejected")
        (empty / "occupied").write_text("x", encoding="ascii")
        for invalid in ("relative", str(empty)):
            try:
                canonical_root(invalid, exists=True, empty=True)
            except ClosureError:
                pass
            else:
                raise ClosureError("invalid output root was accepted")
        nested = empty / "nested"
        nested.mkdir()
        try:
            disjoint_roots([empty, nested])
        except ClosureError:
            pass
        else:
            raise ClosureError("nested roots were accepted")
        alias = contract_root / "alias"
        alias.symlink_to(empty, target_is_directory=True)
        try:
            canonical_root(str(alias), exists=True)
        except ClosureError:
            pass
        else:
            raise ClosureError("symlink root was accepted")

        archive_bytes = b"reviewed crate archive\n"
        archive_sha = hashlib.sha256(archive_bytes).hexdigest()
        lock = contract_root / "Cargo.lock"
        lock.write_text(
            'version = 3\n\n[[package]]\nname = "demo"\nversion = "1.2.3"\n'
            'source = "registry+https://github.com/rust-lang/crates.io-index"\n'
            f'checksum = "{archive_sha}"\n',
            encoding="utf-8",
        )
        packages = validate_cargo_lock(lock)
        seed = contract_root / "seed"
        (seed / "registry" / "index" / "index-id").mkdir(parents=True)
        (seed / "registry" / "index" / "index-id" / "config.json").write_text(
            "{}\n", encoding="ascii",
        )
        archive = seed / "registry" / "cache" / "index-id" / "demo-1.2.3.crate"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(archive_bytes)
        extracted = seed / "registry" / "src" / "index-id" / "demo-1.2.3"
        extracted.mkdir(parents=True)
        (extracted / "build.rs").write_text("malicious\n", encoding="ascii")
        copied = contract_root / "copied"
        manifest = copy_cargo_seed(seed, copied, packages)
        require(HEX64.fullmatch(manifest) is not None and
                not (copied / "registry" / "src").exists(),
                "Cargo seed copy retained pre-extracted sources")
        archive.write_bytes(b"tampered\n")
        try:
            copy_cargo_seed(seed, contract_root / "rejected", packages)
        except ClosureError:
            pass
        else:
            raise ClosureError("tampered Cargo archive was accepted")

        functional_root = contract_root / "repo" / "test" / "functional"
        functional_root.mkdir(parents=True)
        (functional_root / "helper.py").write_text(
            "VALUE = 7\n", encoding="utf-8",
        )
        hostile_user_base = contract_root / "hostile-user-base"
        user_scheme = sysconfig.get_preferred_scheme("user")
        hostile_user_site = Path(sysconfig.get_path(
            "purelib", scheme=user_scheme,
            vars={"userbase": str(hostile_user_base)},
        ))
        hostile_user_site.mkdir(parents=True)
        customization_marker = contract_root / "customization-ran"
        customization = (
            f"open({str(customization_marker)!r},'w').write('ran')\n"
        )
        (hostile_user_site / "sitecustomize.py").write_text(
            customization, encoding="utf-8",
        )
        (hostile_user_site / "usercustomize.py").write_text(
            customization, encoding="utf-8",
        )
        entry_bin = contract_root / "entry-bin"
        entry_bin.mkdir()
        (entry_bin / "python3").symlink_to(
            Path(sys.executable).resolve(strict=True),
        )
        entry_environment = dict(CHILD_BASE)
        entry_environment.update({
            "PATH": f"{entry_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONPATH": str(hostile_user_site),
            "PYTHONUSERBASE": str(hostile_user_base),
            "TMPDIR": str(contract_root),
        })
        capture_bounded(
            [str(Path(__file__).resolve(strict=True)), "--help"],
            entry_environment, "isolated harness entry", timeout=30,
        )
        require(not customization_marker.exists(),
                "host Python customization reached the harness entry")
        fake = functional_root / "fake.py"
        fake.write_text(
            "import os,sys\n"
            "from helper import VALUE\n"
            "assert VALUE == 7\n"
            "target=next(x.split('=',1)[1] for x in sys.argv if x.startswith('--tmpdir='))\n"
            "assert not os.path.exists(target)\n"
            "os.mkdir(target)\n",
            encoding="utf-8",
        )
        functional_env = dict(CHILD_BASE)
        functional_env.update({
            "TMPDIR": str(contract_root),
            "PYTHONNOUSERSITE": "1",
            "PYTHONUSERBASE": str(hostile_user_base),
        })
        run_functional("selftest-success", fake, contract_root / "config.ini",
                       contract_root / "exec-success", 6901, functional_env, 10)
        require(not customization_marker.exists(),
                "host user-site customization executed")
        for label, code in (("skip", 77), ("nonzero", 1)):
            fake.write_text(f"import sys\nsys.exit({code})\n", encoding="utf-8")
            try:
                run_functional(f"selftest-{label}", fake,
                               contract_root / "config.ini",
                               contract_root / f"exec-{label}",
                               6902 + code, functional_env, 10)
            except ClosureError:
                pass
            else:
                raise ClosureError(f"functional {label} was accepted")
        fake.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        try:
            run_functional("selftest-timeout", fake, contract_root / "config.ini",
                           contract_root / "exec-timeout", 6999,
                           functional_env, 1)
        except ClosureError:
            pass
        else:
            raise ClosureError("functional timeout was accepted")
        command_script = contract_root / "command.py"
        command_script.write_text("pass\n", encoding="utf-8")
        run_checked([sys.executable, str(command_script)], functional_env, 10,
                    "self-test command")
        command_script.write_text("raise SystemExit(1)\n", encoding="utf-8")
        try:
            run_checked([sys.executable, str(command_script)], functional_env,
                        10, "self-test failing command")
        except ClosureError:
            pass
        else:
            raise ClosureError("nonzero build command was accepted")
        process_group_file = contract_root / "process-group"
        command_script.write_text(
            "import os,subprocess,sys,time\n"
            f"open({str(process_group_file)!r},'w').write(str(os.getpgrp()))\n"
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        try:
            run_checked([sys.executable, str(command_script)], functional_env,
                        1, "self-test descendant command")
        except ClosureError:
            pass
        else:
            raise ClosureError("descendant command timeout was accepted")
        process_group = int(process_group_file.read_text(encoding="ascii"))
        if not process_group_gone(process_group, 1):
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise ClosureError("build descendant survived timeout cleanup")
        capture_group_file = contract_root / "capture-process-group"
        command_script.write_text(
            "import os,subprocess,sys,time\n"
            f"open({str(capture_group_file)!r},'w').write(str(os.getpgrp()))\n"
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
            "print('bounded output')\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        try:
            capture_bounded(
                [sys.executable, str(command_script)], functional_env,
                "self-test captured descendant", timeout=1,
            )
        except ClosureError:
            pass
        else:
            raise ClosureError("captured command timeout was accepted")
        capture_group = int(capture_group_file.read_text(encoding="ascii"))
        if not process_group_gone(capture_group, 1):
            try:
                os.killpg(capture_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise ClosureError("captured descendant survived timeout cleanup")
        quota_group_file = contract_root / "quota-process-group"
        command_script.write_text(
            "import os,subprocess,sys,time\n"
            f"open({str(quota_group_file)!r},'w').write(str(os.getpgrp()))\n"
            "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
            "os.write(1,b'x'*4096)\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        try:
            capture_bounded(
                [sys.executable, str(command_script)], functional_env,
                "self-test capture quota", timeout=10,
                max_capture_bytes=1024,
            )
        except ClosureError:
            pass
        else:
            raise ClosureError("captured output quota was not enforced")
        quota_group = int(quota_group_file.read_text(encoding="ascii"))
        if not process_group_gone(quota_group, 1):
            try:
                os.killpg(quota_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            raise ClosureError("quota descendant survived cleanup")
        dummy_tools = {
            name: Path(sys.executable).resolve(strict=True)
            for name in ("cargo", "rustc")
        }
        closed_environment = child_environment(contract_root, dummy_tools)
        require(closed_environment["CCACHE_DISABLE"] == "1" and
                closed_environment["CCACHE_CONFIGPATH"] == "/dev/null" and
                closed_environment["PYTHONNOUSERSITE"] == "1" and
                "HOME" not in closed_environment and
                "HTTP_PROXY" not in closed_environment,
                "closed build environment permits host cache or network drift")

        archive_repo = contract_root / "archive-repo"
        archive_repo.mkdir()
        git_test_environment = git_environment(contract_root)
        run_checked(["git", "init", "--quiet", str(archive_repo)],
                    git_test_environment, 30, "self-test Git init")
        for relative, contents in (
            (HARNESS_PATH, "harness\n"),
            (RECORD_PATH, "{}\n"),
            (LOCK_PATH, "version = 3\n"),
            (Path(".gitattributes"),
             "payload.txt export-ignore\nformatted.txt export-subst\n"),
            (Path("payload.txt"), "signed payload\n"),
            (Path("formatted.txt"), "$Format:%H$\n"),
            (Path("executable.sh"), "#!/bin/sh\nexit 0\n"),
        ):
            target = archive_repo / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(contents, encoding="utf-8")
            if relative == Path("executable.sh"):
                os.chmod(target, 0o755)
        run_checked(git_command(archive_repo, "add", "--all"),
                    git_test_environment, 30, "self-test Git add")
        run_checked(
            git_command(
                archive_repo, "commit", "--quiet", "--no-gpg-sign",
                "-m", "self-test archive",
                extra_config=("user.name=Ergon self-test",
                              "user.email=self-test@example.invalid"),
            ),
            git_test_environment, 30, "self-test Git commit",
        )
        archive_commit = git_output(archive_repo, "rev-parse", "HEAD")
        local_attributes = archive_repo / ".git" / "info" / "attributes"
        local_attributes.write_text("* export-ignore\n", encoding="ascii")
        archive_bare = contract_root / "archive-public.git"
        run_checked(["git", "init", "--bare", "--quiet", str(archive_bare)],
                    git_test_environment, 30, "self-test bare init")
        run_checked(
            git_command(
                archive_bare, "fetch", "--quiet", str(archive_repo),
                "+HEAD:refs/heads/main",
            ),
            git_test_environment, 30, "self-test bare fetch",
        )
        run_checked(
            git_command(archive_bare, "symbolic-ref", "HEAD",
                        "refs/heads/main"),
            git_test_environment, 30, "self-test bare HEAD",
        )
        materialized_source, materialized_manifest = materialize_signed_tree(
            archive_bare, contract_root / "tree-materialization",
            archive_commit, git_test_environment,
        )
        require(
            (materialized_source / "payload.txt").read_text(encoding="utf-8") ==
            "signed payload\n" and
            (materialized_source / "formatted.txt").read_text(
                encoding="utf-8",
            ) == "$Format:%H$\n" and
            (materialized_source / "executable.sh").stat().st_mode & 0o777 ==
            0o755 and
            source_manifest(materialized_source) == materialized_manifest,
            "Git attributes altered or omitted signed-tree material",
        )
        os.chmod(materialized_source / "executable.sh", 0o644)
        try:
            require(source_manifest(materialized_source) == materialized_manifest,
                    "materialized executable mode differs from the signed tree")
        except ClosureError:
            pass
        else:
            raise ClosureError("materialized executable mode mutation was accepted")
        require_history(archive_bare, archive_commit)
        graft = archive_bare / "info" / "grafts"
        graft.write_text("forbidden\n", encoding="ascii")
        try:
            require_history(archive_bare, archive_commit)
        except ClosureError:
            pass
        else:
            raise ClosureError("common-dir graft was accepted")
    if repository_root is not None:
        root = canonical_root(repository_root, exists=True)
        validate_cargo_lock(root / LOCK_PATH)
        source = (root / HARNESS_PATH).read_text(encoding="utf-8")
        for token in ("--locked", "CARGO_NET_OFFLINE", "start_new_session=True",
                      "--hermetic-child-env", "check-chronik-build-only",
                      "check-chronik-observer", "refs/heads/main",
                      "GIT_NO_LAZY_FETCH", "-DCCACHE=OFF",
                      "GIT_ATTR_NOSYSTEM", "CCACHE_DISABLE",
                      "materialize_signed_tree", "checkout-index", "-s",
                      "PYTHONNOUSERSITE", "sys.flags.isolated", "RLIMIT_FSIZE",
                      "ls-tree"):
            require(token in source, f"harness contract token absent: {token}")
    print("optional-indexing closure self-test passed "
          f"({len(mutations) + 17} rejection classes)")


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    self_parser = commands.add_parser("self-test", allow_abbrev=False)
    self_parser.add_argument("--repository-root")
    run_parser = commands.add_parser("run", allow_abbrev=False)
    for option in ("repository-root", "work-root", "output-dir", "cargo-cache-seed",
                   "expected-candidate-commit", "expected-candidate-tree",
                   "expected-integration-parent-commit", "expected-integration-parent-tree",
                   "cmake", "ninja", "cc", "cxx", "cargo", "rustc", "nm"):
        run_parser.add_argument(f"--{option}", required=True)
    run_parser.add_argument("--jobs", required=True, type=int)
    return parser


def main() -> int:
    try:
        require(sys.flags.isolated == 1 and sys.flags.no_user_site == 1 and
                sys.flags.ignore_environment == 1,
                "invoke the harness with Python isolated mode")
        parser = make_parser()
        args = parser.parse_args()
        if args.command == "self-test":
            self_test(args.repository_root)
        else:
            execute(args)
    except ClosureError as error:
        print(f"optional-indexing closure failed: {error}", file=sys.stderr)
        return 1
    except Exception:
        print("optional-indexing closure failed: unexpected-error", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

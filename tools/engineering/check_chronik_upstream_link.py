#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Bind a finite Ergon host delta without replacing historical donor evidence.

This is a source-identity gate, not a runtime or noninterference proof. The
historical donor validator checks original preimages; this gate independently
checks every current technical postimage and rejects additional changed paths.
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tomllib

PARENT = "8ae2e14af4765b0ffa5c8f4bd89d6ba3850ae84a"
PARENT_TREE = "57ecce880d26044d88e26d24ff982072aedf7dc9"
MANIFEST = "docs/engineering/chronik/upstream-host-adaptation-v1.json"
REGISTRY = "docs/engineering/chronik/upstream-registry-additions-v1.json"
DONOR_MANIFEST = "docs/engineering/chronik/chronik-port-lot-a-donor-v1.json"
TECHNICAL = frozenset({
    "CMakeLists.txt", "chronik/Cargo.lock", "chronik/Cargo.toml",
    "chronik/UpstreamLink.cmake", "chronik/chronik-bridge/src/ffi.rs",
    "chronik/chronik-cpp/chronik_bridge.cpp", "chronik/chronik-cpp/chronik_bridge.h",
    "chronik/chronik-cpp/chronik_validationinterface.cpp",
    "chronik/chronik-cpp/chronik_validationinterface.h",
    "chronik/chronik-cpp/ergon_archival_probe.cpp",
    "chronik/chronik-cpp/ergon_archival_probe.h",
    "chronik/chronik-cpp/ergon_archival_worker.cpp",
    "chronik/chronik-cpp/ergon_archival_worker.h",
    "chronik/chronik-cpp/ergon_node_access.h",
    "chronik/chronik-cpp/ergon_position_reader.cpp",
    "chronik/chronik-cpp/ergon_position_reader.h",
    "chronik/chronik-http/src/server.rs", "chronik/chronik-lib/Cargo.toml",
    "chronik/chronik-lib/build.rs", "chronik/chronik-lib/src/bridge.rs",
    "chronik/chronik-lib/src/ergon_archival.rs",
    "chronik/chronik-lib/src/ergon_http_tests.rs",
    "chronik/chronik-lib/src/ergon_runtime.rs",
    "chronik/chronik-lib/src/ergon_service.rs", "chronik/chronik-lib/src/ffi.rs",
    "chronik/chronik-lib/src/lib.rs", "src/CMakeLists.txt", "src/chain.cpp",
    "src/chain.h", "src/test/chronik_native_upstream_bridge_tests.cpp",
    "src/test/chronik_upstream_archival_tests.cpp", "src/txmempool.cpp",
    "src/txmempool.h", "src/util/source_generation.h",
})
POLICY = frozenset({
    MANIFEST, REGISTRY, "docs/engineering/changes/ergon-change-0045.json",
    "tools/engineering/check_chronik_upstream_link.py",
    "tools/engineering/check_chronik_port_core.py",
    ".github/workflows/chronik-port-core.yml", ".github/workflows/chronik-upstream-link.yml",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/self_test.py",
})

class GateError(ValueError):
    pass

def require(ok: bool, message: str) -> None:
    if not ok:
        raise GateError(message)

def public_path(value: str) -> str:
    require(isinstance(value, str) and value and "\\" not in value and ":" not in value
            and not any(ord(c) < 32 for c in value), "invalid relative path")
    path = PurePosixPath(value)
    require(not path.is_absolute() and path.as_posix() == value and
            all(p not in (".", "..", ".git") for p in path.parts), "escaping relative path")
    return value

def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result

def load_json(path: Path):
    require(path.is_file() and not path.is_symlink(), "manifest must be regular")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)

def _run_git(root: Path, *args: str) -> bytes:
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_NO_REPLACE_OBJECTS="1", GIT_TERMINAL_PROMPT="0")
    return subprocess.check_output(["git", "-C", str(root), *args], env=env,
                                   stderr=subprocess.PIPE, timeout=30)


@lru_cache(maxsize=2048)
def _immutable_git(root: Path, args: tuple[str, ...]) -> bytes:
    return _run_git(root, *args)


def git(root: Path, *args: str) -> bytes:
    # Cache only byte reads of full immutable object IDs, never references,
    # signatures, worktree/index state, diffs, ancestry or validation results.
    tree_path = (len(args) == 4 and args[0] == "ls-tree" and
                 re.fullmatch(r"[0-9a-f]{40}", args[1]) is not None and args[2] == "--")
    blob_path = (len(args) == 2 and args[0] == "show" and
                 re.fullmatch(r"[0-9a-f]{40}:[^:\x00]+", args[1]) is not None)
    return _immutable_git(root, args) if tree_path or blob_path else _run_git(root, *args)

def identity(raw: bytes, mode: str) -> dict:
    return {"mode": mode, "bytes": len(raw),
            "git_blob_sha1": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
            "sha256": hashlib.sha256(raw).hexdigest()}

def current_identity(root: Path, relative: str) -> dict:
    public_path(relative)
    path = root / relative
    require(path.is_file() and not path.is_symlink(), "source must be a regular file")
    require(path.resolve().is_relative_to(root.resolve()), "source parent symlink escapes root")
    require(not any(parent.is_symlink() for parent in path.parents if parent != root and root in parent.parents),
            "source directory alias refused")
    return identity(path.read_bytes(), "100755" if stat.S_IMODE(path.stat().st_mode) & 0o111 else "100644")

class HistoricalView:
    def __init__(self, root: Path, rows: dict):
        self.root, self.rows = root, rows
    def bytes(self, relative: str) -> bytes:
        public_path(relative)
        require(relative not in self.rows or self.rows[relative]["before"] is not None,
                "authored addition has no donor preimage")
        return git(self.root, "show", PARENT + ":" + relative)
    def added(self, relative: str) -> bool:
        return relative in self.rows and self.rows[relative]["before"] is None

def expected_bridge_source(original, path):
    result = original.replace('include!("blockindex.h");', 'include!("chain.h");')
    result = result.replace('include!("node/context.h");', 'include!("chronik-cpp/ergon_node_access.h");')
    if 'chronik-bridge/' in path:
        result = result.replace('/// node::NodeContext from node/context.h\n        #[namespace = "node"]\n        type NodeContext;',
                                '/// Ergon host access; its owner must outlive every Chronik worker.\n        type ErgonNodeAccess;')
        result = result.replace('/// ::CBlockIndex from blockindex.h', '/// ::CBlockIndex from chain.h')
        result = result.replace('/// Make the bridge given the NodeContext', '/// Make the bridge given the Ergon host access.')
        result = result.replace('node: &NodeContext', 'node: &ErgonNodeAccess')
        result = result.replace('/// node::NodeContext &) can be moved betweed threads safely.',
                                '/// ErgonNodeAccess &) can be moved between threads. The native bridge must\n/// lock all live chain/mempool accesses and join users before host teardown.')
        result = result.replace('/// node::NodeContext &) can be accessed from different threads safely.',
                                '/// ErgonNodeAccess &) can be shared between threads under the same native\n/// locking and lifetime contract; this does not make CBlockIndex access safe.')
        result = result.replace('fn get_chain_tip(self: &ChronikBridge) -> Result<&CBlockIndex>;',
                                'fn get_chain_tip(self: &ChronikBridge) -> Result<&CBlockIndex>;\n        /// Synchronous nonwrapping native mempool generation; zero is exhausted.\n        fn mempool_revision(self: &ChronikBridge) -> u64;\n        /// Synchronous nonwrapping native active-chain generation.\n        fn chain_generation(self: &ChronikBridge) -> u64;')
        result = result.replace('fn chain_generation(self: &ChronikBridge) -> u64;',
                                'fn chain_generation(self: &ChronikBridge) -> u64;\n'
                                '        /// Nonblocking source check under native chain and mempool locks.\n'
                                '        fn matches_source(\n'
                                '            self: &ChronikBridge,\n'
                                '            hash: [u8; 32],\n'
                                '            height: i32,\n'
                                '            generation: u64,\n'
                                '            revision: u64,\n'
                                '        ) -> bool;')
    else:
        result = result.replace('/// CBlockIndex from blockindex.h', '/// CBlockIndex from chain.h')
        result = result.replace('/// NodeContext from node/context.h\n        #[namespace = "node"]\n        type NodeContext = chronik_bridge::ffi::NodeContext;',
                                '/// Ergon host access from chronik-cpp/ergon_node_access.h.\n        type ErgonNodeAccess = chronik_bridge::ffi::ErgonNodeAccess;')
        result = result.replace('node: &NodeContext', 'node: &ErgonNodeAccess')
        result = result.replace('type ErgonNodeAccess = chronik_bridge::ffi::ErgonNodeAccess;',
                                'type ErgonNodeAccess = chronik_bridge::ffi::ErgonNodeAccess;\n\n        /// Link integration alone does not authorize index/server startup.\n        fn upstream_runtime_permitted(node: &ErgonNodeAccess) -> bool;')
    return result


def expected_native_generation_source(original, path):
    transforms = {
        'src/chain.h': [
            ('#include <uint256.h>\n', '#include <uint256.h>\n#include <util/source_generation.h>\n'),
            ('    std::vector<CBlockIndex *> vChain;\n',
             '    std::vector<CBlockIndex *> vChain;\n'
             '    // Synchronous native observer identity; active-chain access uses cs_main.\n'
             '    SourceGeneration m_source_generation;\n'),
            ('    SourceGeneration m_source_generation;\n\npublic:\n',
             '    SourceGeneration m_source_generation;\n\npublic:\n'
             '    uint64_t GetSourceGeneration() const noexcept {\n'
             '        return m_source_generation.Value();\n    }\n')],
        'src/chain.cpp': [
            ('void CChain::SetTip(CBlockIndex *pindex) {\n',
             'void CChain::SetTip(CBlockIndex *pindex) {\n'
             '    // Count before mutation, including a transient branch that later returns\n'
             '    // to the same tip. No asynchronous callback or wrapping counter involved.\n'
             '    if (Tip() != pindex) {\n'
             '        m_source_generation.Advance();\n    }\n')],
        'src/txmempool.h': [
            ('#include <sync.h>\n', '#include <sync.h>\n#include <util/source_generation.h>\n'),
            ('    unsigned int nTransactionsUpdated;\n',
             '    unsigned int nTransactionsUpdated;\n'
             '    // Separate observer identity; preserve the legacy mining counter/API.\n'
             '    SourceGeneration m_source_generation;\n'),
            ('    unsigned int GetTransactionsUpdated() const;\n',
             '    unsigned int GetTransactionsUpdated() const;\n'
             '    uint64_t GetSourceGeneration() const;\n')],
        'src/txmempool.cpp': [
            ('    nTransactionsUpdated += n;\n}\n',
             '    nTransactionsUpdated += n;\n    m_source_generation.Advance(n);\n}\n'
             '\nuint64_t CTxMemPool::GetSourceGeneration() const {\n'
             '    LOCK(cs);\n    return m_source_generation.Value();\n}\n'),
            ('\n    nTransactionsUpdated++;\n',
             '\n    nTransactionsUpdated++;\n    m_source_generation.Advance();\n'),
            ('\n    ++nTransactionsUpdated;\n',
             '\n    ++nTransactionsUpdated;\n    m_source_generation.Advance();\n'),
            ('\n            ++nTransactionsUpdated;\n',
             '\n            ++nTransactionsUpdated;\n            m_source_generation.Advance();\n')]
    }
    for before, after in transforms[path]:
        expected = 2 if before == '\n    nTransactionsUpdated++;\n' else 1
        if original.count(before) != expected:
            raise ValueError('native generation mutation inventory changed')
        original = original.replace(before, after)
    return original


def validate(root: Path) -> HistoricalView:
    root = root.resolve(strict=True)
    # A separately finite successor binds current bytes to the exact public
    # review parent. Preserve this original adaptation's identities and donor
    # preimages: do not relabel later authored native storage code as donor.
    from check_chronik_chainstate_reads import MANIFEST as READ_MANIFEST
    from check_chronik_chainstate_reads import validate as validate_reads, before_identity as read_parent_identity
    from check_chronik_chainstate_reads import TECHNICAL as READ_TECHNICAL
    from check_chronik_plugin_fixtures import MANIFEST as PLUGIN_MANIFEST, TECHNICAL as PLUGIN_TECHNICAL
    from check_chronik_plugin_fixtures import before_identity as plugin_parent_identity
    plugin_layer = (root / PLUGIN_MANIFEST).exists()
    from check_chronik_regtest_service import MANIFEST as SERVICE_MANIFEST, TECHNICAL as SERVICE_TECHNICAL
    from check_chronik_regtest_service import before_identity as service_parent_identity
    service_layer = (root / SERVICE_MANIFEST).exists()
    from check_chronik_service_startup import MANIFEST as STARTUP_MANIFEST, TECHNICAL as STARTUP_TECHNICAL
    from check_chronik_service_startup import before_identity as startup_parent_identity
    startup_layer = (root / STARTUP_MANIFEST).exists()
    read_layer = validate_reads(root) if (root / READ_MANIFEST).exists() else frozenset()
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == PARENT_TREE,
            "public parent tree mismatch or missing history")
    require(git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"",
            "public parent is not an ancestor")
    payload = load_json(root / MANIFEST)
    require(payload.get("schema") == "ergon-upstream-host-adaptation/v1", "adaptation schema mismatch")
    require(payload.get("public_parent") == {"commit": PARENT, "tree": PARENT_TREE}, "parent binding mismatch")
    rows = payload.get("files", [])
    names = [row["path"] for row in rows]
    require(names == sorted(TECHNICAL), "finite technical allowlist mismatch")
    require(payload.get("license") == "MIT", "MIT declaration missing")
    view = HistoricalView(root, {row["path"]: row for row in rows})
    for row in rows:
        path = public_path(row["path"])
        observed = (read_parent_identity(root, path) if read_layer and path in READ_TECHNICAL else
                    plugin_parent_identity(root, path) if plugin_layer and path in PLUGIN_TECHNICAL else
                    service_parent_identity(root, path) if service_layer and path in SERVICE_TECHNICAL else
                    startup_parent_identity(root, path) if startup_layer and path in STARTUP_TECHNICAL else
                    current_identity(root, path))
        require(observed == row["after"], "technical postimage drift: " + path)
        tree_entry = git(root, "ls-tree", PARENT, "--", path).decode().strip()
        if row["before"] is None:
            require(not tree_entry, "addition already exists in public parent")
        else:
            require(bool(tree_entry), "missing preimage")
            mode = tree_entry.split()[0]
            require(identity(view.bytes(path), mode) == row["before"], "technical preimage drift: " + path)
    for path in ("src/chain.h", "src/chain.cpp", "src/txmempool.h", "src/txmempool.cpp"):
        require((root / path).read_text() == expected_native_generation_source(view.bytes(path).decode(), path),
                "native delta exceeds exact observer-generation hooks")
    require(current_identity(root, "src/util/source_generation.h")["sha256"] ==
            "46970fe0bffdcbc0771d141098850f77140f20c3e1457cdaa6065ac1d15750a6", "source generation law changed")
    for path in ("chronik/chronik-bridge/src/ffi.rs", "chronik/chronik-lib/src/ffi.rs"):
        require((root / path).read_text() == expected_bridge_source(view.bytes(path).decode(), path),
                "FFI delta exceeds exact host/read-only adaptation")
    # A missing manifest must never fall back to an ungoverned adapter profile.
    require(hashlib.sha256((root / DONOR_MANIFEST).read_bytes()).hexdigest() ==
            payload.get("historical_donor_manifest_sha256") ==
            hashlib.sha256(git(root, "show", PARENT + ":" + DONOR_MANIFEST)).hexdigest() ==
            "821041df4043c707a31106b69f2653d08bbbd95eae4c5163b0586e972a3ea1ab",
            "historical donor manifest changed")
    changed = set(p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p)
    changed |= set(p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p)
    require(TECHNICAL <= changed and changed <= TECHNICAL | POLICY | read_layer, "unexpected or missing changed path")
    for relative in changed:
        require(current_identity(root, relative)["mode"] == "100644", "unexpected changed-file executable bit")
    workspace = tomllib.loads((root / "chronik/Cargo.toml").read_text())["workspace"]
    require(workspace.get("members") == payload["workspace"]["members"] and
            workspace.get("default-members") == payload["workspace"]["default_members"] and
            workspace.get("exclude", []) == [] and workspace["package"]["rust-version"] == "1.85.0",
            "current workspace boundary drift")
    base_workspace = tomllib.loads(view.bytes("chronik/Cargo.toml").decode())["workspace"]
    require(workspace["default-members"] == base_workspace["default-members"] and
            set(workspace["members"]) == set(base_workspace["members"]) |
            {"chronik-bridge", "chronik-http", "chronik-indexer", "chronik-lib"},
            "workspace expanded beyond the four upstream members")
    lock = tomllib.loads((root / "chronik/Cargo.lock").read_text())["package"]
    registry = [p for p in lock if "source" in p]
    require(len(lock) == 344 and len(registry) == 328, "dependency closure count drift")
    require(all(p["source"] == "registry+https://github.com/rust-lang/crates.io-index" and
                re.fullmatch(r"[0-9a-f]{64}", p.get("checksum", "")) for p in registry),
            "unbound dependency source/checksum")
    base_registry = [p for p in tomllib.loads(view.bytes("chronik/Cargo.lock").decode())["package"] if "source" in p]
    key = lambda p: (p["name"], p["version"], p["source"], p.get("checksum"))
    require(set(map(key, base_registry)) <= set(map(key, registry)), "published dependency tuple removed or replaced")
    additions = load_json(root / REGISTRY)
    require(additions.get("schema") == "ergon-upstream-registry-additions/v1" and
            additions.get("resolved_lock_sha256") == view.rows["chronik/Cargo.lock"]["after"]["sha256"],
            "registry additions binding mismatch")
    require([(p["name"], p["version"]) for p in additions["packages"]] ==
            [(name, "0.3.34") for name in ("futures", "futures-channel", "futures-executor",
                                         "futures-io", "futures-macro", "futures-sink")] + [("want", "0.3.2")],
            "registry additions exceed the seven reviewed tuples")
    for item in additions["packages"]:
        require(item["source"] == "registry+https://github.com/rust-lang/crates.io-index",
                "registry additions source mismatch")
        require(sum(p["name"] == item["name"] and p["version"] == item["version"] and
                    p["checksum"] == item["checksum"] for p in registry) == 1,
                "additional registry tuple mismatch")
    host = (root / "chronik/chronik-cpp/ergon_node_access.h").read_text()
    require("inline bool upstream_runtime_permitted(const ErgonNodeAccess &) noexcept {\n    return false;\n}" in host,
            "ordinary upstream setup admission is not unconditionally closed")
    cmake = (root / "CMakeLists.txt").read_text()
    for flag in ("BUILD_CHRONIK_UPSTREAM_LINK", "BUILD_CHRONIK_UPSTREAM_ARCHIVAL_TESTS"):
        require(re.search(r"option\(" + flag + r"\s+\"[^\"]+\"\s+OFF\s*\)", cmake), "compile default is not OFF")
    if plugin_layer:
        # Original donor rows stay historical. New exact helpers are a separate
        # reviewed import, not fictitious entries in the older donor corpus.
        for path in sorted(PLUGIN_TECHNICAL):
            if path.startswith("chronik/") and path not in view.rows:
                view.rows[path] = {"before": plugin_parent_identity(root, path)}
    return view

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    try:
        validate(args.repository_root)
        print("PASS: 34 historical host adaptations preserved; current finite successors checked; ordinary upstream setup closed")
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print("FAIL: upstream source-identity gate: " + type(error).__name__, file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    sys.exit(main())

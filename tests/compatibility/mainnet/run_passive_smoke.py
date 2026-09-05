#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Run a bounded, non-economic mainnet public-prefix parity smoke."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import Any


SCENARIO_ID = "mixed-node-coexistence"
PROFILE = "mainnet-passive-independent-prefix-smoke"
SCHEMA = "ergon-mainnet-passive-smoke/v2"
ROLES = ("baseline", "candidate")
FAILURE_ROLES = frozenset((*ROLES, "comparison", "harness"))
STOP_HEIGHT = 288
MAX_OBSERVED_HEIGHT = STOP_HEIGHT + 128
EXPECTED_GENESIS = (
    "000000070e37bfee7e84b94f997f38155a85b22172f5ca25fd4eb3d64c5ad7c5"
)
PHASE_TIMEOUT_SECONDS = 900
SHUTDOWN_TIMEOUT_SECONDS = 60
POLL_SECONDS = 0.1
DISK_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
RPC_METHODS = frozenset({
    "getblockchaininfo",
    "getblockhash",
    "getblockheader",
    "getnetworkinfo",
    "stop",
})
RESULT_EXIT_CODES = {
    "success": 0,
    "harness-error": 1,
    "inconclusive": 2,
    "contradiction": 3,
}
RESULT_BY_REASON = {
    "bounded-mainnet-prefix-matched": "success",
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
REASON_CODES = frozenset(RESULT_BY_REASON)
CHILD_ENVIRONMENT = {
    "LANG": "C",
    "LC_ALL": "C",
    "NO_COLOR": "1",
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    "TERM": "dumb",
    "TZ": "UTC",
}
COMMON_ARGS = (
    "-server=1",
    "-daemon=0",
    "-disablewallet=1",
    "-blocksonly=1",
    "-persistmempool=0",
    "-discover=0",
    "-listenonion=0",
    "-upnp=0",
    "-assumevalid=0",
    "-onlynet=ipv4",
    "-printtoconsole=0",
    "-debug=0",
    "-rpcbind=127.0.0.1",
    "-rpcallowip=127.0.0.1",
)
PUBLIC_ARGS = (
    "-listen=0",
    "-dnsseed=1",
    "-forcednsseed=1",
    "-maxconnections=1",
)
OFFLINE_ARGS = (
    "-connect=0",
    "-listen=0",
    "-dnsseed=0",
    "-forcednsseed=0",
    "-maxconnections=0",
)


class SmokeFailure(Exception):
    """A closed, report-safe scenario disposition."""

    def __init__(self, result: str, reason_code: str,
                 failure_role: str = "harness") -> None:
        if result == "success" or RESULT_BY_REASON.get(reason_code) != result \
                or failure_role not in FAILURE_ROLES:
            raise ValueError("invalid smoke disposition")
        super().__init__(reason_code)
        self.result = result
        self.reason_code = reason_code
        self.failure_role = failure_role


@dataclass(frozen=True)
class BinaryIdentity:
    bytes: int
    sha256: str


@dataclass(frozen=True)
class NodeSpec:
    role: str
    binary: Path
    datadir: Path
    rpc_port: int
    p2p_port: int
    runtime_root: Path


@dataclass
class NodeProcess:
    spec: NodeSpec
    process: subprocess.Popen[bytes]


def fail(result: str, reason_code: str,
         failure_role: str = "harness") -> None:
    raise SmokeFailure(result, reason_code, failure_role)


def run_role_phase(role: str, function: Any, *args: Any) -> Any:
    if role not in ROLES:
        fail("harness-error", "report-contract-invalid")
    try:
        return function(*args)
    except SmokeFailure as error:
        raise SmokeFailure(error.result, error.reason_code, role) from error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def binary_identity(path: Path) -> BinaryIdentity:
    try:
        resolved = path.resolve(strict=True)
        stat = resolved.stat()
        digest = sha256_file(resolved)
    except OSError:
        fail("harness-error", "binary-input-invalid")
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        fail("harness-error", "binary-input-invalid")
    return BinaryIdentity(bytes=stat.st_size, sha256=digest)


def is_below(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_roots(work_root: Path, report: Path) -> tuple[Path, Path]:
    work_root = work_root.resolve(strict=False)
    try:
        report_parent = report.parent.resolve(strict=True)
    except OSError:
        fail("harness-error", "report-contract-invalid")
    report = report_parent / report.name
    if work_root.exists() or report.exists() or is_below(report, work_root):
        fail("harness-error", "report-contract-invalid")
    return work_root, report


def reserve_ports(count: int) -> list[int]:
    sockets: list[socket.socket] = []
    try:
        for _ in range(count):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        ports = [sock.getsockname()[1] for sock in sockets]
    except OSError:
        fail("harness-error", "port-alias")
    finally:
        for sock in sockets:
            sock.close()
    if len(set(ports)) != count:
        fail("harness-error", "port-alias")
    return ports


def child_env(runtime_root: Path) -> dict[str, str]:
    home = runtime_root / "home"
    temp = runtime_root / "tmp"
    home.mkdir(mode=0o700, parents=True)
    temp.mkdir(mode=0o700)
    return {
        **CHILD_ENVIRONMENT,
        "HOME": str(home),
        "TMPDIR": str(temp),
    }


def directory_bytes(root: Path) -> int:
    total = 0
    try:
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
    except OSError:
        fail("harness-error", "unexpected-error")
    return total


def rpc(spec: NodeSpec, method: str, params: list[Any] | None = None) -> Any:
    if method not in RPC_METHODS:
        fail("harness-error", "rpc-contract-invalid")
    cookie_path = spec.datadir / ".cookie"
    try:
        cookie = cookie_path.read_bytes().strip()
        authorization = base64.b64encode(cookie).decode("ascii")
        body = json.dumps({
            "jsonrpc": "1.0",
            "id": "mainnet-passive-smoke",
            "method": method,
            "params": params or [],
        }, separators=(",", ":"))
        connection = http.client.HTTPConnection(
            "127.0.0.1", spec.rpc_port, timeout=30
        )
        connection.request(
            "POST",
            "/",
            body=body,
            headers={
                "Authorization": f"Basic {authorization}",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        value = json.loads(payload)
    except (OSError, ValueError, KeyError, http.client.HTTPException):
        fail("harness-error", "rpc-contract-invalid")
    if response.status != 200 or not isinstance(value, dict) \
            or value.get("error") is not None or "result" not in value:
        fail("harness-error", "rpc-contract-invalid")
    return value["result"]


def node_args(spec: NodeSpec, mode: str) -> list[str]:
    args = [
        str(spec.binary),
        *COMMON_ARGS,
        f"-datadir={spec.datadir}",
        f"-rpcport={spec.rpc_port}",
        f"-port={spec.p2p_port}",
    ]
    if mode == "public":
        args.extend((*PUBLIC_ARGS, f"-stopatheight={STOP_HEIGHT}"))
    elif mode == "offline":
        args.extend(OFFLINE_ARGS)
    else:
        fail("harness-error", "report-contract-invalid")
    return args


def start_node(spec: NodeSpec, mode: str) -> NodeProcess:
    spec.runtime_root.mkdir(mode=0o700, parents=True)
    env = child_env(spec.runtime_root)
    try:
        process = subprocess.Popen(
            node_args(spec, mode),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        fail("harness-error", "binary-input-invalid")
    return NodeProcess(spec=spec, process=process)


def wait_for_rpc(node: NodeProcess, deadline: float) -> None:
    while time.monotonic() < deadline:
        if node.process.poll() is not None:
            fail("inconclusive", "node-exited-before-rpc")
        try:
            rpc(node.spec, "getblockchaininfo")
            return
        except SmokeFailure as error:
            if error.reason_code != "rpc-contract-invalid":
                raise
        time.sleep(POLL_SECONDS)
    fail("inconclusive", "timeout")


def wait_for_bounded_exit(node: NodeProcess, work_root: Path) -> None:
    deadline = time.monotonic() + PHASE_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        returncode = node.process.poll()
        if returncode is not None:
            if returncode != 0:
                fail("inconclusive", "node-exited-nonzero")
            return
        if directory_bytes(work_root) > DISK_LIMIT_BYTES:
            fail("inconclusive", "disk-limit-exceeded")
        try:
            network = rpc(node.spec, "getnetworkinfo")
            if not isinstance(network, dict):
                fail("harness-error", "rpc-contract-invalid")
            if network.get("networkactive") is not True \
                    or network.get("localrelay") is not False \
                    or network.get("localaddresses") != []:
                fail("harness-error", "network-surface-open")
        except SmokeFailure as error:
            if error.reason_code != "rpc-contract-invalid":
                raise
        time.sleep(POLL_SECONDS)
    fail("inconclusive", "timeout")


def stop_node(node: NodeProcess, *, graceful_required: bool) -> bool:
    if node.process.poll() is None:
        try:
            rpc(node.spec, "stop")
        except SmokeFailure:
            if graceful_required:
                return False
        try:
            node.process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(node.process.pid, signal.SIGTERM)
                node.process.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(node.process.pid, signal.SIGKILL)
                    node.process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    return False
            return False
    return node.process.returncode == 0


def snapshot(spec: NodeSpec) -> dict[str, Any]:
    chain = rpc(spec, "getblockchaininfo")
    if not isinstance(chain, dict):
        fail("harness-error", "rpc-contract-invalid")
    if chain.get("chain") != "main":
        fail("contradiction", "mainnet-chain-mismatch")
    if not isinstance(chain.get("blocks"), int) \
            or not STOP_HEIGHT <= chain["blocks"] <= MAX_OBSERVED_HEIGHT:
        fail("inconclusive", "bounded-prefix-incomplete")
    genesis = rpc(spec, "getblockhash", [0])
    if genesis != EXPECTED_GENESIS:
        fail("contradiction", "genesis-mismatch")
    block_hash = rpc(spec, "getblockhash", [STOP_HEIGHT])
    raw_header = rpc(spec, "getblockheader", [block_hash, False])
    header = rpc(spec, "getblockheader", [block_hash, True])
    if not isinstance(block_hash, str) or len(block_hash) != 64 \
            or not all(character in "0123456789abcdef"
                       for character in block_hash) \
            or not isinstance(raw_header, str) or len(raw_header) != 160 \
            or not all(character in "0123456789abcdef"
                       for character in raw_header) \
            or not isinstance(header, dict) \
            or header.get("height") != STOP_HEIGHT \
            or header.get("hash") != block_hash \
            or not isinstance(header.get("chainwork"), str) \
            or len(header["chainwork"]) != 64 \
            or not all(character in "0123456789abcdef"
                       for character in header["chainwork"]):
        fail("harness-error", "rpc-contract-invalid")
    result = {
        "chain": "main",
        "checkpoint_height": STOP_HEIGHT,
        "genesis": genesis,
        "blockhash": block_hash,
        "raw_header": raw_header,
        "chainwork": header["chainwork"],
    }
    return result


class SystemBackend:
    """Small process boundary, replaced by deterministic fakes in self-tests."""

    def __init__(self, work_root: Path) -> None:
        self.work_root = work_root
        self.nodes: list[NodeProcess] = []
        self.pids: set[int] = set()

    def _record(self, node: NodeProcess) -> NodeProcess:
        if node.process.pid in self.pids:
            fail("harness-error", "process-alias")
        self.pids.add(node.process.pid)
        self.nodes.append(node)
        return node

    def fetch_public(self, spec: NodeSpec) -> int:
        node = self._record(start_node(spec, "public"))
        wait_for_bounded_exit(node, self.work_root)
        return node.process.pid

    def inspect(self, spec: NodeSpec) -> tuple[dict[str, Any], int]:
        node = self._record(start_node(spec, "offline"))
        try:
            wait_for_rpc(node, time.monotonic() + SHUTDOWN_TIMEOUT_SECONDS)
            value = snapshot(spec)
            if not stop_node(node, graceful_required=True):
                fail("harness-error", "cleanup-failed")
            return value, node.process.pid
        finally:
            if node.process.poll() is None:
                stop_node(node, graceful_required=False)

    def cleanup(self) -> bool:
        ok = True
        for node in reversed(self.nodes):
            if node.process.poll() is None:
                ok = stop_node(node, graceful_required=False) and ok
        return ok and all(node.process.poll() is not None for node in self.nodes)


def make_spec(role: str, binary: Path, datadir: Path, runtime_root: Path,
              rpc_port: int, p2p_port: int) -> NodeSpec:
    return NodeSpec(
        role=role,
        binary=binary,
        datadir=datadir,
        rpc_port=rpc_port,
        p2p_port=p2p_port,
        runtime_root=runtime_root,
    )


def execute_scenario(work_root: Path, baseline_binary: Path,
                     candidate_binary: Path, backend: Any) -> dict[str, Any]:
    datadirs = work_root / "datadirs"
    baseline_datadir = datadirs / "baseline"
    candidate_datadir = datadirs / "candidate"
    baseline_datadir.mkdir(mode=0o700, parents=True)
    candidate_datadir.mkdir(mode=0o700)
    baseline_inode = baseline_datadir.stat()
    candidate_inode = candidate_datadir.stat()
    if (baseline_inode.st_dev, baseline_inode.st_ino) == \
            (candidate_inode.st_dev, candidate_inode.st_ino):
        fail("harness-error", "datadir-alias")
    ports = reserve_ports(8)
    if len(set(ports)) != len(ports):
        fail("harness-error", "port-alias")
    runtime = work_root / "runtime"
    baseline_public = make_spec(
        "baseline", baseline_binary, baseline_datadir,
        runtime / "baseline-public", ports[0], ports[1]
    )
    baseline_inspect = make_spec(
        "baseline", baseline_binary, baseline_datadir,
        runtime / "baseline-inspect", ports[2], ports[3]
    )
    candidate_public = make_spec(
        "candidate", candidate_binary, candidate_datadir,
        runtime / "candidate-public", ports[4], ports[5]
    )
    candidate_inspect = make_spec(
        "candidate", candidate_binary, candidate_datadir,
        runtime / "candidate-inspect", ports[6], ports[7]
    )

    run_role_phase("baseline", backend.fetch_public, baseline_public)
    baseline_snapshot, _ = run_role_phase(
        "baseline", backend.inspect, baseline_inspect
    )
    run_role_phase("candidate", backend.fetch_public, candidate_public)
    candidate_snapshot, _ = run_role_phase(
        "candidate", backend.inspect, candidate_inspect
    )
    try:
        baseline_after = baseline_datadir.stat()
        candidate_after = candidate_datadir.stat()
    except OSError:
        fail("harness-error", "datadir-identity-changed")
    if (baseline_after.st_dev, baseline_after.st_ino) != \
            (baseline_inode.st_dev, baseline_inode.st_ino) \
            or (candidate_after.st_dev, candidate_after.st_ino) != \
            (candidate_inode.st_dev, candidate_inode.st_ino):
        fail("harness-error", "datadir-identity-changed")
    if baseline_snapshot != candidate_snapshot:
        fail("contradiction", "snapshot-mismatch", "comparison")
    return baseline_snapshot


def report_document(result: str, reason_code: str,
                    identities: dict[str, BinaryIdentity],
                    shared_snapshot: dict[str, Any] | None,
                    cleanup_complete: bool,
                    failure_role: str | None = None) -> dict[str, Any]:
    if RESULT_BY_REASON.get(reason_code) != result:
        fail("harness-error", "report-contract-invalid")
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "scenario_id": SCENARIO_ID,
        "profile": PROFILE,
        "result": result,
        "reason_code": reason_code,
        "knowledge_status": "Observed" if result == "success" else "Open Question",
        "evidence_ceiling": "assembled_runtime" if result == "success" else "component",
        "scope": {
            "maximum_accepted_exit_height": MAX_OBSERVED_HEIGHT,
            "stop_trigger_height": STOP_HEIGHT,
            "complete_initial_block_download": False,
            "network_source_by_role": {
                "baseline": "public-mainnet",
                "candidate": "public-mainnet",
            },
        },
        "binaries": {
            role: {
                "bytes": identities[role].bytes,
                "sha256": identities[role].sha256,
                "binary_to_source_provenance": "external-build-record-required",
            }
            for role in ROLES
        },
        "cleanup": {
            "complete": cleanup_complete,
            "processes_survived": False,
            "work_root_survived": False,
        },
        "claims": {
            "bounded_mainnet_prefix_match": result == "success",
            "independent_public_prefix_acquisition": result == "success",
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
        "limitations": [
            "Public peers and DNS resolvers necessarily observe each role's source IP.",
            "Passive means no authored transaction or block and no public inbound service; P2P protocol traffic still occurs.",
            "The fixed prefix does not cover the current tip, full history, sustained operation, or operator binaries.",
            "Shutdown may complete already in-flight blocks above the fixed checkpoint; only checkpoint 288 is compared.",
            "Distinct public peer sets and simultaneous operation are not established.",
        ],
    }
    if result == "success":
        if shared_snapshot is None or failure_role is not None:
            fail("harness-error", "report-contract-invalid")
        document["observations"] = {
            "baseline_clean_restart": True,
            "baseline_public_prefix_acquired": True,
            "candidate_clean_restart": True,
            "candidate_public_prefix_acquired": True,
            "datadirs_distinct": True,
            "ports_distinct": True,
            "processes_distinct": True,
            "roles_equal": True,
            "shared_checkpoint": shared_snapshot,
        }
    else:
        if shared_snapshot is not None or failure_role not in FAILURE_ROLES:
            fail("harness-error", "report-contract-invalid")
        document["failure_role"] = failure_role
    return document


def write_report(path: Path, value: dict[str, Any]) -> None:
    try:
        encoded = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
        with path.open("xb") as file:
            file.write(encoded)
    except OSError:
        fail("harness-error", "report-contract-invalid")


def run(args: argparse.Namespace, backend_factory: Any = SystemBackend) -> int:
    work_root, report = validate_roots(Path(args.work_root), Path(args.report))
    baseline_binary = Path(args.baseline_bitcoind).resolve(strict=False)
    candidate_binary = Path(args.candidate_bitcoind).resolve(strict=False)
    identities = {
        "baseline": binary_identity(baseline_binary),
        "candidate": binary_identity(candidate_binary),
    }
    try:
        baseline_stat = baseline_binary.stat()
        candidate_stat = candidate_binary.stat()
    except OSError:
        fail("harness-error", "binary-input-invalid")
    if baseline_binary == candidate_binary or (
        baseline_stat.st_dev, baseline_stat.st_ino
    ) == (candidate_stat.st_dev, candidate_stat.st_ino):
        fail("harness-error", "binary-input-invalid")

    result = "harness-error"
    reason_code = "unexpected-error"
    failure_role = "harness"
    shared_snapshot: dict[str, Any] | None = None
    backend: Any | None = None
    cleanup_complete = False
    work_root.mkdir(mode=0o700, parents=True)
    try:
        backend = backend_factory(work_root)
        try:
            shared_snapshot = execute_scenario(
                work_root, baseline_binary, candidate_binary, backend
            )
            result = "success"
            reason_code = "bounded-mainnet-prefix-matched"
        except SmokeFailure as error:
            result, reason_code = error.result, error.reason_code
            failure_role = error.failure_role
        except Exception:
            result, reason_code, failure_role = (
                "harness-error", "unexpected-error", "harness"
            )
    finally:
        try:
            processes_clean = backend is None or backend.cleanup()
        except Exception:
            processes_clean = False
        try:
            shutil.rmtree(work_root)
        except OSError:
            processes_clean = False
        cleanup_complete = processes_clean and not work_root.exists()

    if not cleanup_complete:
        print("mainnet passive smoke: harness-error cleanup-failed", file=sys.stderr)
        return RESULT_EXIT_CODES["harness-error"]
    current_identities = {
        "baseline": binary_identity(baseline_binary),
        "candidate": binary_identity(candidate_binary),
    }
    if current_identities != identities:
        print(
            "mainnet passive smoke: harness-error binary-identity-changed",
            file=sys.stderr,
        )
        return RESULT_EXIT_CODES["harness-error"]
    document = report_document(
        result, reason_code, identities, shared_snapshot, cleanup_complete,
        None if result == "success" else failure_role,
    )
    write_report(report, document)
    print(f"mainnet passive smoke: {result} {reason_code}")
    return RESULT_EXIT_CODES[result]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--baseline-bitcoind", required=True)
    parser.add_argument("--candidate-bitcoind", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--report", required=True)
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(run(parse_args()))
    except SmokeFailure as error:
        print(
            f"mainnet passive smoke: {error.result} {error.reason_code}",
            file=sys.stderr,
        )
        raise SystemExit(RESULT_EXIT_CODES[error.result])
    except Exception:
        print(
            "mainnet passive smoke: harness-error unexpected-error",
            file=sys.stderr,
        )
        raise SystemExit(RESULT_EXIT_CODES["harness-error"])

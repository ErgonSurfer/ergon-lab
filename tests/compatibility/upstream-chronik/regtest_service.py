# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Real bitcoind lifecycle over public zero-value regtest transactions only."""
import argparse
import base64
import fcntl
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import tempfile
import time
import run_repetitions as runner

SHUTDOWN = ("chronik_upstream_regtest_tests/shutdown_cancels_inflight_capture_without_failed_root", 9)
LEASE_CASES = (
    ("chronik_upstream_regtest_tests/service_os_lease_excludes_other_owners_and_preserves_inode", 9),
    ("chronik_upstream_regtest_tests/service_unknown_or_partial_lease_is_not_adopted", 6),
)
STARTUP_CASES = (
    ("chronik_upstream_regtest_tests/service_owned_startup_reconciles_progress_and_reorg", 7),
    ("chronik_upstream_regtest_tests/service_startup_cancellation_preserves_reopenable_root", 5),
    ("chronik_upstream_regtest_tests/asynchronous_owner_does_not_wait_for_native_import", 6),
    ("chronik_upstream_regtest_tests/asynchronous_owner_allows_native_progress_before_open", 9),
    ("chronik_upstream_regtest_tests/asynchronous_owner_stop_joins_pending_startup", 9),
)
SERVICE_LEASE = b"ergon-regtest-service-os-lock/v1\n"


def released_service_lease(index):
    path = index / "probe.lock"
    # A scope refusal can precede lease creation. Otherwise the file persists:
    # removing a lock file would permit competing owners on different inodes.
    if path.exists():
        runner.require(runner.regular(path) == SERVICE_LEASE, "unknown service lease")
        with path.open("r+b") as lease:
            fcntl.flock(lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def fields(raw):
    """Small strict protobuf wire reader for committed response assertions."""
    runner.require(len(raw) <= 8 * 1024 * 1024, "oversized protobuf")
    offset = 0
    def integer():
        nonlocal offset
        value = 0
        for shift in range(0, 70, 7):
            runner.require(offset < len(raw), "truncated varint")
            byte = raw[offset]
            offset += 1
            value |= (byte & 127) << shift
            if byte < 128:
                runner.require(shift < 63 or byte <= 1, "varint overflow")
                return value
        raise runner.Rejected("varint overflow")
    result = {}
    while offset < len(raw):
        key = integer()
        number, wire = key >> 3, key & 7
        runner.require(0 < number < 2 ** 29, "invalid protobuf tag")
        if wire == 0:
            value = integer()
        else:
            runner.require(wire in (1, 2, 5), "unsupported protobuf wire")
            length = integer() if wire == 2 else (8 if wire == 1 else 4)
            runner.require(length <= len(raw) - offset, "truncated protobuf field")
            value = raw[offset:offset + length]
            offset += length
        result.setdefault(number, []).append(value)
    return result


def txids(raw):
    txs = fields(raw).get(1, [])
    ids = [fields(tx).get(1) for tx in txs]
    runner.require(all(isinstance(v, list) and len(v) == 1 and
                       isinstance(v[0], bytes) and len(v[0]) == 32 for v in ids),
                   "malformed transaction response")
    result = [v[0][::-1].hex() for v in ids]
    runner.require(len(result) == len(set(result)), "duplicate indexed transaction")
    return sorted(result)


def p2sh_true_address():
    script_hash = hashlib.new("ripemd160", hashlib.sha256(b"\x51").digest()).digest()
    payload = b"\xc4" + script_hash
    payload += hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    value, encoded = int.from_bytes(payload, "big"), ""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    while value:
        value, digit = divmod(value, 58)
        encoded = alphabet[digit] + encoded
    return encoded


def zero_tx(previous, index, redeem):
    # Public zero-subsidy coin only. OP_RETURN padding satisfies the native
    # minimum transaction size, not a token/economic validity shortcut.
    script = b"\x01\x51" if redeem else b""
    data = (struct.pack("<I", 2) + b"\x01" + bytes.fromhex(previous)[::-1] +
            struct.pack("<I", index) + bytes([len(script)]) + script + b"\xff" * 4 +
            b"\x02" + bytes(8) + b"\x01\x51" + bytes(8) + b"\x43\x6a\x4c\x40" +
            bytes(64) + bytes(4))
    return data.hex()


def wait_for(check, timeout=30):
    deadline = time.monotonic() + timeout
    while True:
        try:
            value = check()
            if value:
                return value
        except (OSError, ValueError, http.client.HTTPException):
            pass
        runner.require(time.monotonic() < deadline, "daemon lifecycle deadline exceeded")
        time.sleep(0.05)


class Daemon:
    def __init__(self, binary, root, data, phase, enabled=True, *, public_plugin=False):
        self.data, self.root = data, root
        self.public_plugin = public_plugin
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.rpc_port = reservation.getsockname()[1]
        self.log = root / (phase + ".log")
        self.output = self.log.open("xb")
        temporary = root / (phase + "-environment")
        temporary.mkdir()
        self.proc = subprocess.Popen([str(binary), "-regtest", "-server=1", "-listen=0",
            "-connect=0", "-dnsseed=0", "-discover=0", "-upnp=0", "-printtoconsole=1",
            "-acceptnonstdtxn=1", "-blockmintxfee=0",
            "-datadir=" + str(data), "-rpcbind=127.0.0.1", "-rpcallowip=127.0.0.1",
            "-rpcport=" + str(self.rpc_port)] + (["-chronikupstreamregtest=1"] if enabled else []) +
            (["-chronikupstreamregtestplugins=1"] if public_plugin else []),
            cwd=root, env=runner.environment(temporary), stdout=self.output,
            stderr=subprocess.STDOUT, start_new_session=True)
        self.port = None

    def rpc(self, method, params=()):
        if self.proc.poll() is not None:
            raise RuntimeError("daemon exited during lifecycle")
        cookie = (self.data / "regtest/.cookie").read_bytes().strip()
        connection = http.client.HTTPConnection("127.0.0.1", self.rpc_port, timeout=2)
        try:
            connection.request("POST", "/", json.dumps({"jsonrpc": "1.0", "id": "regtest",
                "method": method, "params": list(params)}), {"Authorization": "Basic " +
                base64.b64encode(cookie).decode(), "Content-Type": "application/json"})
            response = connection.getresponse()
            body = json.loads(response.read(2 * 1024 * 1024))
            runner.require(response.status == 200 and body["error"] is None, "native RPC refusal: " + method)
            return body["result"]
        finally:
            connection.close()

    def ready(self, available=True, *, startup_timeout=30):
        wait_for(lambda: self.rpc("getblockchaininfo"))
        if available:
            def port():
                text = self.log.read_text()
                matches = re.findall(r"Chronik upstream regtest listening=127\.0\.0\.1:([0-9]+) plugins=" +
                                     str(int(self.public_plugin)) + r"\b", text)
                runner.require(len(matches) <= 1, "ambiguous service listener")
                return int(matches[0]) if matches else None
            self.port = wait_for(port, timeout=startup_timeout)
        else:
            wait_for(lambda: "Chronik upstream regtest unavailable node_continues=1" in self.log.read_text())
            runner.require("Chronik upstream regtest listening=" not in self.log.read_text(), "failed root served")

    def http(self, path, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        try:
            connection.request(method, path)
            response = connection.getresponse()
            raw = response.read(8 * 1024 * 1024 + 1)
            runner.require(len(raw) <= 8 * 1024 * 1024, "HTTP response cap")
            runner.require(response.getheader("Cache-Control") == "no-store", "cacheable response")
            return response.status, raw
        finally:
            connection.close()

    def tip(self, height, block_hash):
        def matches():
            status, body = self.http("/blockchain-info")
            if status == 503:
                return False
            runner.require(status == 200, "unexpected tip response")
            value = fields(body)
            return value.get(1) == [bytes.fromhex(block_hash)[::-1]] and value.get(2, [0]) == [height]
        wait_for(matches)

    def pool(self, expected):
        def matches():
            runner.require(sorted(self.rpc("getrawmempool")) == sorted(expected), "native pool mismatch")
            status, body = self.http("/unconfirmed-txs")
            if status == 503:
                return False
            runner.require(status == 200, "unexpected pool response")
            return txids(body) == sorted(expected)
        wait_for(matches)

    def stop(self):
        self.rpc("stop")
        runner.require(self.proc.wait(timeout=20) == 0, "daemon stop refused")
        runner.stop_group(self.proc)
        self.output.close()
        if self.port is not None:
            runner.require("Chronik upstream regtest stopped joined=1" in self.log.read_text(), "missing joined stop")
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                    raise runner.Rejected("listener survived positive stop")
            except OSError:
                pass
        index = self.data / "regtest/chronik-upstream-regtest"
        released_service_lease(index)

    def crash(self):
        runner.require(self.port is not None and self.proc.poll() is None,
                       "crash witness requires a running ready service")
        self.proc.kill()  # Real SIGKILL, deliberately bypassing graceful Stop.
        runner.require(self.proc.wait(timeout=10) == -9, "missing SIGKILL witness")
        runner.stop_group(self.proc)
        self.output.close()
        runner.require("Chronik upstream regtest stopped joined=1" not in self.log.read_text(),
                       "crash was an ordinary stop")
        released_service_lease(self.data / "regtest/chronik-upstream-regtest")

    def cleanup(self):
        runner.stop_group(self.proc)
        self.output.close()


def observe(binary, work):
    root = Path(tempfile.mkdtemp(prefix="ergon-upstream-", dir=work)).resolve()
    data = root / "data"
    data.mkdir()
    daemons = []
    def start(phase, available=True):
        daemon = Daemon(binary, root, data, phase)
        daemons.append(daemon)
        daemon.ready(available)
        return daemon
    try:
        daemon = start("initial")
        initial = daemon.rpc("getblockchaininfo")
        daemon.tip(0, initial["bestblockhash"])
        hashes = daemon.rpc("generatetoaddress", (101, p2sh_true_address()))
        daemon.tip(101, hashes[-1])
        funding = daemon.rpc("getblock", (hashes[0], 2))["tx"][0]
        runner.require(all(o["value"] == 0 for o in funding["vout"]), "nonzero funding forbidden")
        raw_parent = zero_tx(funding["txid"], 0, True)
        parent_id = hashlib.sha256(hashlib.sha256(bytes.fromhex(raw_parent)).digest()).digest()[::-1].hex()
        runner.require(daemon.rpc("prioritisetransaction", (parent_id, 0, 1000000)) is True, "native parent priority refused")
        parent = daemon.rpc("sendrawtransaction", (raw_parent,))
        runner.require(parent == parent_id, "native parent txid drift")
        raw_child = zero_tx(parent, 0, False)
        child_id = hashlib.sha256(hashlib.sha256(bytes.fromhex(raw_child)).digest()).digest()[::-1].hex()
        runner.require(daemon.rpc("prioritisetransaction", (child_id, 0, 1000000)) is True, "native child priority refused")
        child = daemon.rpc("sendrawtransaction", (raw_child,))
        runner.require(child == child_id, "native child txid drift")
        daemon.pool([parent, child])
        runner.require(daemon.http("/ws")[0] == 501 and
                       daemon.http("/broadcast-tx", "POST")[0] == 501, "unexpected capability")
        index = data / "regtest/chronik-upstream-regtest"
        identity = runner.regular(index / "ergon-identity.json")
        daemon.stop()
        runner.require(not (index / "probe.failed").exists(), "ordinary stop quarantined index")
        daemon = start("reopen-pending")
        daemon.tip(101, hashes[-1])
        daemon.pool([parent, child])
        confirmed = daemon.rpc("generatetoaddress", (1, p2sh_true_address()))[0]
        daemon.tip(102, confirmed)
        daemon.pool([])
        for txid, raw in ((parent, raw_parent), (child, raw_child)):
            status, body = daemon.http("/raw-tx/" + txid)
            runner.require(status == 200 and fields(body).get(1) == [bytes.fromhex(raw)], "raw tx parity drift")
        daemon.rpc("invalidateblock", (confirmed,))
        daemon.tip(101, hashes[-1])
        daemon.pool([parent, child])
        daemon.rpc("reconsiderblock", (confirmed,))
        daemon.tip(102, confirmed)
        daemon.pool([])
        runner.require(daemon.rpc("getconnectioncount") == 0, "unexpected peers")
        daemon.stop()
        runner.require(not (index / "probe.failed").exists() and
                       runner.regular(index / "ergon-identity.json") == identity, "healthy root changed identity")
        daemon = start("reopen-confirmed")
        daemon.tip(102, confirmed)
        daemon.pool([])
        lease = index / "probe.lock"
        lease_inode = (lease.stat().st_dev, lease.stat().st_ino)
        daemon.crash()
        runner.require(runner.regular(index / "ergon-identity.json") == identity and
                       not (index / "probe.failed").exists(), "crash changed root authority")
        daemon = start("recover-after-sigkill")
        daemon.tip(102, confirmed)
        daemon.pool([])
        for txid, raw in ((parent, raw_parent), (child, raw_child)):
            status, body = daemon.http("/raw-tx/" + txid)
            runner.require(status == 200 and fields(body).get(1) == [bytes.fromhex(raw)],
                           "post-crash raw transaction parity drift")
        recovered = daemon.rpc("generatetoaddress", (1, p2sh_true_address()))[0]
        daemon.tip(103, recovered)
        daemon.stop()
        runner.require(lease_inode == (lease.stat().st_dev, lease.stat().st_ino) and
                       runner.regular(index / "ergon-identity.json") == identity,
                       "crash recovery replaced lease or identity")
        # Synthetic failure marker, not an incoming private artifact. Preserve
        # the failed root for this phase; no auto-wipe/rebuild/identity rewrite.
        (index / "probe.failed").write_bytes(b"synthetic public refusal witness\n")
        daemon = start("refuse-failed-root", available=False)
        progressed = daemon.rpc("generatetoaddress", (1, p2sh_true_address()))
        runner.require(len(progressed) == 1 and daemon.rpc("getblockcount") == 104,
                       "index refusal affected native mining")
        daemon.stop()
        runner.require(runner.regular(index / "ergon-identity.json") == identity and
                       runner.regular(index / "probe.failed") == b"synthetic public refusal witness\n",
                       "failed root was silently adopted")
        return {"network": "regtest", "initial_height": 0, "final_height": 104,
            "native_pending_parent_child_reopened": True, "confirmation_disconnect_reconsider": True,
            "raw_transaction_parity": True, "healthy_reopens": 2, "positive_stops": 4,
            "sigkill_processes": 1, "crash_reopens": 1, "lease_inode_preserved": True,
            "post_crash_native_mining_and_indexing": True,
            "failed_index_native_mining_continues": True, "external_connections": 0,
            "synthetic_fee_priority_fixoshis_per_transaction": 1000000,
            "actual_input_output_value_and_fee": 0,
            "websocket_and_broadcast_disabled": True, "plugins": False}
    finally:
        for daemon in daemons:
            daemon.cleanup()
        runner.remove_owned(root, work)


def reject_scope(binary, work, option):
    root = Path(tempfile.mkdtemp(prefix="ergon-upstream-", dir=work)).resolve()
    data = root / "data"
    data.mkdir()
    proc = None
    try:
        with (root / "output").open("xb") as out:
            proc = subprocess.Popen([str(binary), "-regtest", "-chronikupstreamregtest=1",
                "-datadir=" + str(data), "-listen=0", "-connect=0", option], cwd=root,
                env=runner.environment(root), stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            runner.require(proc.wait(timeout=15) != 0, "invalid service scope accepted")
            runner.stop_group(proc)
        runner.require("-chronikupstreamregtest requires unpruned local regtest" in
                       (root / "output").read_text(), "wrong refusal class")
        runner.require(not any("chronik" in p.name.lower() for p in data.rglob("*")),
                       "out-of-scope request created index")
    finally:
        if proc is not None:
            runner.stop_group(proc)
        runner.remove_owned(root, work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "build-root", "work-parent", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    source, build, work = (p.resolve(strict=True) for p in (args.source_root, args.build_root, args.work_parent))
    report = args.report.absolute()
    runner.require(report.parent.resolve(strict=True) == report.parent and not report.exists() and not report.is_symlink(), "new canonical report required")
    runner.require(not any(report.is_relative_to(p) for p in (source, build, work)), "separate report required")
    runner.require(not any(work.is_relative_to(p) or p.is_relative_to(work) for p in (source, build)), "separate disposable parent required")
    runner.require(1 <= args.repetitions <= 10, "repetitions outside bounded range")
    sys.path.insert(0, str(source / "tools/engineering"))
    from check_chronik_regtest_service import validate
    validate(source)
    manifest = source / "docs/engineering/chronik/upstream-host-adaptation-v1.json"
    binding = runner.bind_build(source, build, "regtest", manifest)
    binary = build / "src/bitcoind"
    native_test = build / "src/test/test_bitcoin"
    native_hash = runner.digest(runner.regular(native_test))
    before = runner.digest(runner.regular(binary))
    runner.smoke(binary, work)  # Same compiled-in binary, runtime default OFF.
    reject_scope(binary, work, "-regtest=0")
    reject_scope(binary, work, "-prune=550")
    result = None
    for _ in range(args.repetitions):
        for case, assertions in (SHUTDOWN, *LEASE_CASES, *STARTUP_CASES):
            streams = runner.execute([str(native_test), "--run_test=" + case,
                "--report_level=short", "--log_level=message", "--color_output=no"],
                work, (source, build, binary, native_test))
            runner.parse_streams(streams, (case,), assertions)
        current = observe(binary, work)
        runner.require(result is None or result == current, "lifecycle result instability")
        result = current
    runner.require(before == runner.digest(runner.regular(binary)), "daemon binary drift")
    runner.require(native_hash == runner.digest(runner.regular(native_test)), "native test binary drift")
    runner.require(binding == runner.bind_build(source, build, "regtest", manifest), "source/build binding drift")
    validate(source)
    payload = {"schema": "ergon-upstream-regtest-service-observation/v1", "knowledge_status": "Observed",
        "proof_ceiling": "bounded_regtest_service_observation", "binary_sha256": before, "result": result,
        "binding": binding, "repetitions": args.repetitions, "fresh_daemon_processes": 3 + 5 * args.repetitions,
        "native_shutdown_processes": args.repetitions, "native_shutdown_case": SHUTDOWN[0],
        "native_shutdown_assertions_per_process": SHUTDOWN[1], "native_test_binary_sha256": native_hash,
        "native_lease_processes": len(LEASE_CASES) * args.repetitions,
        "native_lease_cases": LEASE_CASES,
        "native_startup_cases": STARTUP_CASES,
        "native_startup_processes": len(STARTUP_CASES) * args.repetitions,
        "child_environment": dict(runner.ENV, TMPDIR="<runner-created-unique-directory>"),
        "disposable_roots_removed": True, "runtime_default_off_witness": True,
        "wrong_network_and_pruned_refused": True,
        "binary_to_source_provenance": "external-build-record-required"}
    raw = runner.canonical(payload)
    runner.require(not any(str(p).encode() in raw for p in (source, build, work, binary)), "private binding in report")
    runner.write_report(report, payload)
    print("PASS: bounded real-daemon regtest lifecycle; no mainnet or production readiness claim")


if __name__ == "__main__":
    main()

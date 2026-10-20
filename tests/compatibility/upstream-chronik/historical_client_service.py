# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Synthetic zero-value regtest oracle for the historical client profile.

Uses the same explicit network/genesis-bound owner as main, never a main
transaction. Reuses upstream ALP builders and existing native test helpers.
This is a component test, not independent token validation or deployment.
"""
import base64
import hashlib
import http.client
import json
from pathlib import Path
import re
import socket
import struct
import subprocess
import time

import alp_service as alp
import regtest_service as service
import run_repetitions as runner


def reserve_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class HistoricalDaemon(service.Daemon):
    def __init__(self, binary, root, data, phase, genesis, port=0):
        self.data, self.root, self.public_plugin = data, root, False
        self.rpc_port = reserve_port()
        runner.require(not port or port != self.rpc_port, "port role collision")
        self.log = root / (phase + ".log")
        self.output = self.log.open("xb")
        temporary = root / (phase + "-environment")
        temporary.mkdir()
        self.port = None
        self.proc = subprocess.Popen([str(binary), "-regtest", "-server=1", "-listen=0",
            "-connect=0", "-dnsseed=0", "-discover=0", "-upnp=0", "-printtoconsole=1",
            "-acceptnonstdtxn=1", "-blockmintxfee=0", "-datadir=" + str(data),
            "-rpcbind=127.0.0.1", "-rpcallowip=127.0.0.1", "-rpcport=" + str(self.rpc_port),
            "-chronikupstreamservice=1", "-chronikupstreamservicenetwork=regtest",
            "-chronikupstreamservicegenesis=" + genesis,
            "-chronikupstreamserviceport=" + str(port)], cwd=root,
            env=runner.environment(temporary), stdout=self.output,
            stderr=subprocess.STDOUT, start_new_session=True)

    def ready(self):
        service.wait_for(lambda: self.rpc("getblockchaininfo"))
        def port():
            rows = re.findall(r"Chronik upstream service listening=127\.0\.0\.1:([0-9]+) plugins=0\b",
                              self.log.read_text("utf8"))
            runner.require(len(rows) <= 1, "ambiguous historical service listener")
            return int(rows[0]) if rows else None
        self.port = service.wait_for(port)

    def stop(self):
        self.rpc("stop")
        runner.require(self.proc.wait(timeout=30) == 0, "historical native stop refused")
        runner.stop_group(self.proc)
        self.output.close()
        runner.require("Chronik upstream service stopped joined=1" in self.log.read_text("utf8"),
                       "missing joined service stop")
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                raise runner.Rejected("historical listener survived stop")
        except OSError:
            pass
        index = self.data / "regtest/chronik-upstream-service"
        service.released_service_lease(index)
        runner.require(not (index / "probe.failed").exists(), "healthy stop quarantined index")


def post(daemon, path, body, expected):
    connection = http.client.HTTPConnection("127.0.0.1", daemon.port, timeout=10)
    try:
        connection.request("POST", path, body, {"Content-Type": "application/x-protobuf",
            "Origin": "https://127.0.0.1:" + str(daemon.port)})
        reply = connection.getresponse()
        raw = reply.read(8 * 1024 * 1024 + 1)
        runner.require(len(raw) <= 8 * 1024 * 1024 and reply.status in expected,
                       "historical submission refusal/status contradiction: " + str(reply.status))
        return reply.status, raw, reply.getheader("x-ergon-index-state")
    finally:
        connection.close()


def varint(value):
    result = b""
    while value >= 128:
        result += bytes([(value & 127) | 128])
        value >>= 7
    return result + bytes([value])


def message(raw):
    return b"\x0a" + varint(len(raw)) + raw


class WebSocket:
    """Bounded test wire client; the server is the existing upstream handler."""
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.buffer = b""
        self.events = []
        key = base64.b64encode(b"ergon-probe-0084").decode("ascii")
        self.sock.sendall((f"GET /ws HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Origin: https://127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n").encode("ascii"))
        while b"\r\n\r\n" not in self.buffer:
            runner.require(len(self.buffer) < 8192, "WebSocket handshake cap")
            chunk = self.sock.recv(4096)
            runner.require(bool(chunk), "WebSocket handshake closed")
            self.buffer += chunk
        headers, self.buffer = self.buffer.split(b"\r\n\r\n", 1)
        lines = headers.decode("ascii").split("\r\n")
        runner.require(lines[0] == "HTTP/1.1 101 Switching Protocols", "WebSocket upgrade refused")
        values = dict(line.split(":", 1) for line in lines[1:])
        values = {k.lower(): v.strip() for k, v in values.items()}
        accept = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
        runner.require(values.get("sec-websocket-accept") == accept, "WebSocket accept mismatch")
        self.send(b"\x12\x00")  # WsSub.blocks
        self.send(b"\x42\x00")  # WsSub.txs (global)
        # Error acknowledgement is a round-trip barrier AFTER both subscriptions.
        self.send(b"")
        self.wait(lambda e: 1 in e)
        self.events.clear()

    def read(self, count):
        while len(self.buffer) < count:
            chunk = self.sock.recv(max(4096, count - len(self.buffer)))
            runner.require(bool(chunk), "WebSocket closed before expected event")
            self.buffer += chunk
        result, self.buffer = self.buffer[:count], self.buffer[count:]
        return result

    def send(self, raw, opcode=2):
        runner.require(len(raw) < 126, "test WebSocket send cap")
        mask = b"ERGN"
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(raw)]) + mask +
                          bytes(byte ^ mask[i % 4] for i, byte in enumerate(raw)))

    def wait(self, predicate):
        deadline = time.monotonic() + 20
        while True:
            for event in self.events:
                if predicate(event):
                    return event
            runner.require(time.monotonic() < deadline, "WebSocket event deadline")
            self.sock.settimeout(max(0.01, deadline - time.monotonic()))
            a, b = self.read(2)
            runner.require(a & 0x80 and not a & 0x70 and not b & 0x80, "unexpected server WebSocket framing")
            size = b & 127
            if size == 126:
                size = struct.unpack("!H", self.read(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self.read(8))[0]
            runner.require(size <= 1024 * 1024, "WebSocket frame cap")
            raw = self.read(size)
            opcode = a & 15
            if opcode == 9:
                self.send(raw, 10)
                continue
            runner.require(opcode == 2, "unexpected WebSocket opcode or early close")
            self.events.append(service.fields(raw))

    def event(self, field, kind, identifier):
        def matches(event):
            if field not in event or len(event[field]) != 1:
                return False
            item = service.fields(event[field][0])
            return item.get(1, [0]) == [kind] and item.get(2) == [bytes.fromhex(identifier)[::-1]]
        return self.wait(matches)

    def close(self):
        self.sock.close()


def observe(binary, test_binary, root):
    runner.require(root.is_dir() and not any(root.iterdir()), "new empty owned work root required")
    data = root / "data"
    data.mkdir()
    # Discover the public regtest genesis from a disabled native process, not
    # from the database about to be admitted.
    native = service.Daemon(binary, root, data, "native-bootstrap", enabled=False)
    daemons, sockets = [native], []
    steps = []
    def record(name, **values):
        steps.append({"stage": name, **values})
    try:
        service.wait_for(lambda: native.rpc("getblockchaininfo"))
        genesis = native.rpc("getblockhash", (0,))
        hashes = native.rpc("generatetoaddress", (101, service.p2sh_true_address()))
        native.stop()
        environment = root / "template-environment"
        environment.mkdir()
        output = subprocess.run([str(test_binary), "--run_test=" + alp.TEMPLATE_CASE,
            "--report_level=short", "--log_level=message", "--color_output=no"],
            cwd=root, env=runner.environment(environment), capture_output=True, timeout=60, check=True)
        scripts = alp.templates(tuple(raw.decode("utf8", "strict") for raw in (output.stdout, output.stderr)))
        port = reserve_port()
        d = HistoricalDaemon(binary, root, data, "historical-clients", genesis, port)
        daemons.append(d)
        d.ready()
        runner.require(d.port == port, "explicit loopback port ignored")
        rejected = http.client.HTTPConnection("127.0.0.1", d.port, timeout=10)
        try:
            rejected.request("POST", "/broadcast-tx", b"", {
                "Content-Type": "application/x-protobuf", "Origin": "https://foreign.invalid"})
            response = rejected.getresponse()
            runner.require(response.status == 403, "foreign origin was not refused before submission")
            response.read(65536)
        finally:
            rejected.close()
        record("HTTPS-same-host-admitted-and-foreign-origin-refused")
        d.tip(101, hashes[-1])
        ws = WebSocket(d.port)
        sockets.append(ws)
        funding = d.rpc("getblock", (hashes[0], 2))["tx"][0]
        wires, ids = [], []
        for op in range(4):
            inputs = ([(funding["txid"], 0, True)] if op == 0 else
                      [(ids[0], 2, False)] if op == 1 else
                      [(ids[0], 1, False), (ids[1], 1, False)] if op == 2 else [(ids[2], 2, False)])
            wire = alp.transaction(inputs, [alp.bind_script(scripts, op, ids[0] if ids else "00" * 32), b"\x51"] +
                                   ([b"\x52" if op == 2 else b"\x51"] if op < 3 else []))
            txid = hashlib.sha256(hashlib.sha256(bytes.fromhex(wire)).digest()).digest()[::-1].hex()
            runner.require(d.rpc("prioritisetransaction", (txid, 0, 1000000)), "synthetic priority refused")
            if op == 1:
                unsafe = alp.transaction([(ids[0], 1, False)], [b"\x6a\x4c\x40" + bytes(64), b"\x51"])
                code, raw, _ = post(d, "/broadcast-tx", message(bytes.fromhex(unsafe)), {400})
                error = service.fields(raw)
                runner.require(error.get(2) and b"burn" in error[2][0].lower(), "missing unintended-burn reason")
                runner.require(d.rpc("getrawmempool") == ids, "burn refusal changed native mempool")
                record("unintended-token-burn-refused-before-native-submission")
            body = message(bytes.fromhex(wire))
            for suffix in [b"\x10\x01", b"\x18\x01"]:
                post(d, "/broadcast-tx", body + suffix, {400})
            code, raw, readiness = post(d, "/broadcast-tx", body, {200, 202})
            runner.require(service.fields(raw).get(1) == [bytes.fromhex(txid)[::-1]], "broadcast receipt identity")
            runner.require(readiness == ("ready" if code == 200 else "pending"), "receipt readiness contradiction")
            if code == 200:
                runner.require(d.http("/tx/" + txid)[0] == 200, "ready receipt not immediately queryable")
            else:
                alp.response(d, "/tx/" + txid)  # Acceptance is not rebroadcast.
            ids.append(txid)
            wires.append(wire)
            d.pool(ids)
            ws.event(3, 0, txid)
            record("upstream-ALP-broadcast", operation=op, receipt=code)
        times = [d.rpc("getmempoolentry", (txid,))["time"] for txid in ids]
        pending = alp.check_snapshot(d, ids, wires, times, -1, hashes[-1])
        post(d, "/broadcast-txs", message(b"\x00") * 17, {400})
        post(d, "/broadcast-txs", b"", {400})
        post(d, "/broadcast-tx", message(b"\x00"), {400})
        confirmed = d.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        d.tip(102, confirmed)
        d.pool([])
        ws.event(2, 0, confirmed)
        for txid in ids:
            ws.event(3, 2, txid)
        alp.check_snapshot(d, ids, wires, times, 102, confirmed)
        record("confirmed-ALP-and-real-upstream-WebSocket-events", transactions=4)
        ws.events.clear()
        d.rpc("invalidateblock", (confirmed,))
        d.tip(101, hashes[-1])
        d.pool(ids)
        ws.event(2, 1, confirmed)
        for txid in ids:
            ws.event(3, 0, txid)
        runner.require(alp.check_snapshot(d, ids, wires, times, -1, hashes[-1]) == pending,
                       "disconnect token projection drift")
        record("ordered-disconnect-and-native-mempool-reaccept")
        # Mining clears native fee deltas (the unchanged legacy
        # DefaultBatchUpdater::removeForBlock). These zero-value synthetic
        # transactions have no economic fee. Restore the declared fixture
        # priority BEFORE persistence, never refill/rebroadcast after reopen.
        # Otherwise LoadMempool may honestly reject them for min relay fee.
        for txid in ids:
            runner.require(d.rpc("prioritisetransaction", (txid, 0, 1000000)),
                           "restored synthetic priority refused")
            entry = d.rpc("getmempoolentry", (txid,))
            runner.require(entry["fees"]["modified"] == 0.01 and entry["fees"]["base"] == 0,
                           "synthetic persistence fee-priority precondition")
        record("restore-declared-zero-fee-fixture-priority-before-native-persistence",
               priority_fixoshis_per_transaction=1000000, native_fee=0)
        ws.close()
        identity_path = data / "regtest/chronik-upstream-service/ergon-identity.json"
        identity = identity_path.read_bytes()
        runner.require(json.loads(identity)["schema"] == "ergon-historical-client-service/v1", "wrong owner identity")
        d.stop()
        d = HistoricalDaemon(binary, root, data, "historical-clients-reopen", genesis, port)
        daemons.append(d)
        d.ready()
        d.tip(101, hashes[-1])
        d.pool(ids)
        runner.require(identity_path.read_bytes() == identity and
                       alp.check_snapshot(d, ids, wires, times, -1, hashes[-1]) == pending,
                       "same-identity nonempty-mempool reopen drift")
        ws = WebSocket(d.port)
        sockets.append(ws)
        d.rpc("reconsiderblock", (confirmed,))
        d.tip(102, confirmed)
        d.pool([])
        ws.event(2, 0, confirmed)
        for txid in ids:
            ws.event(3, 2, txid)
        ws.close()
        d.stop()
        record("fresh-process-same-port-nonempty-pool-reopen-and-WebSocket-resubscribe")
        runner.require(not (data / "regtest/chronik-upstream-shadow").exists() and
                       not (data / "regtest/chronik-upstream-regtest").exists(), "old profile root adopted")
        return {"status": "PASS_SYNTHETIC_HISTORICAL_CLIENT_COMPONENT", "stages": steps,
                "zero_native_value": True, "mainnet_submission": False,
                "synthetic_fee_priority_fixoshis_per_transaction": 1000000,
                "priority_restored_before_persistence": True,
                "independent_token_validation": False, "public_reproduction": False}
    finally:
        for ws in sockets:
            ws.close()
        for daemon in daemons:
            if daemon.proc.poll() is None:
                daemon.cleanup()

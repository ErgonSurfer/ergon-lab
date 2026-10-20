#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Mine and propagate five real development blocks in disposable loopback nodes.

An isolated component check, never a launcher for a persistent or mainnet node.
The explicit --chronik profile also checks exact native network/genesis admission.
No private output, cookie, path or raw log is emitted as public evidence.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import model

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/compatibility/upstream-chronik"))
sys.path.insert(0, str(ROOT / "test/functional"))
import regtest_service as service
from test_framework.cashaddr import encode, SCRIPT_TYPE

NETWORK = "ergon-testnet-v1"
GENESIS = "00000066119ff801d756659ca11217fe21c0b5f66779eaf9fbc87a8945aa8b4c"
MAIN_GENESIS = "000000070e37bfee7e84b94f997f38155a85b22172f5ca25fd4eb3d64c5ad7c5"


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def port(used):
    for _ in range(50):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            value = sock.getsockname()[1]
        if value not in used and value not in (8331, 8332, 8333, 42136, 42137, 42138):
            used.add(value)
            return value
    raise RuntimeError("no distinct ephemeral loopback port")


class Node(service.Daemon):
    def __init__(self, binary, root, data, phase, used, chronik=False, extra=()):
        self.root, self.data, self.public_plugin = root, data, False
        self.rpc_port, self.p2p_port = port(used), port(used)
        self.port = port(used) if chronik else None
        temporary = root / (phase + "-environment")
        temporary.mkdir(mode=0o700)
        self.log = root / (phase + ".log")
        self.output = self.log.open("xb")
        self.chain = NETWORK
        argv = [str(binary), "-ergontestnet=1", "-server=1", "-listen=1", "-connect=0",
                "-dnsseed=0", "-discover=0", "-upnp=0", "-printtoconsole=1", "-prune=0",
                # Reserve real inbound capacity: the native connection manager
                # allocates outbound/feeler slots before admitting inbound peers.
                "-dbcache=32", "-maxconnections=16", "-bind=127.0.0.1", "-port=" + str(self.p2p_port),
                "-datadir=" + str(data), "-conf=" + str(root / "empty.conf"),
                "-rpcbind=127.0.0.1", "-rpcallowip=127.0.0.1", "-rpcport=" + str(self.rpc_port)]
        if chronik:
            argv += ["-chronikupstreamservice=1", "-chronikupstreamservicenetwork=" + NETWORK,
                     "-chronikupstreamservicegenesis=" + GENESIS,
                     "-chronikupstreamserviceport=" + str(self.port)]
        self.proc = subprocess.Popen(argv + list(extra), cwd=root,
            env=service.runner.environment(temporary), stdout=self.output,
            stderr=subprocess.STDOUT, start_new_session=True)

    def rpc(self, method, params=()):
        # Reuse the inherited RPC implementation with a per-instance network
        # cookie path, without changing its regtest assumptions globally.
        import base64
        import http.client
        require(self.proc.poll() is None, "disposable node exited")
        cookie = (self.data / self.chain / ".cookie").read_bytes().strip()
        connection = http.client.HTTPConnection("127.0.0.1", self.rpc_port, timeout=3)
        try:
            connection.request("POST", "/", json.dumps({"jsonrpc": "1.0", "id": "testnet-smoke",
                "method": method, "params": list(params)}), {"Authorization": "Basic " +
                base64.b64encode(cookie).decode(), "Content-Type": "application/json"})
            response = connection.getresponse()
            raw = response.read(2 * 1024 * 1024 + 1)
            require(len(raw) <= 2 * 1024 * 1024, "RPC response bound")
            body = json.loads(raw)
            if response.status == 500 and isinstance(body.get("error"), dict) and body["error"].get("code") == -28:
                raise ValueError("disposable RPC warming up")
            require(response.status == 200 and body["error"] is None, "disposable RPC refused")
            return body["result"]
        finally:
            connection.close()

    def ready(self, chronik=False):
        info = service.wait_for(lambda: self.rpc("getblockchaininfo"), timeout=60)
        require(info["chain"] == NETWORK and self.rpc("getblockhash", [0]) == GENESIS,
                "disposable node identity mismatch")
        require(info.get("currency") == {"ticker": "tXRG", "decimals": 8},
                "development display identity missing or ambiguous")
        if chronik:
            self.tip(info["blocks"], info["bestblockhash"])

    def stop(self):
        self.rpc("stop")
        require(self.proc.wait(timeout=30) == 0, "disposable positive stop refused")
        self.finish()

    def finish(self):
        service.runner.stop_group(self.proc)
        self.output.close()
        for value in (self.rpc_port, self.p2p_port, self.port):
            if value is None:
                continue
            try:
                with socket.create_connection(("127.0.0.1", value), timeout=.2):
                    raise RuntimeError("disposable listener survived cleanup")
            except OSError:
                pass


def refusal(binary, root, used, phase, flags):
    data = root / (phase + "-data")
    data.mkdir(mode=0o700)
    node = Node(binary, root, data, phase, used, extra=flags)
    try:
        code = node.proc.wait(timeout=30)
        require(code > 0, "invalid profile was admitted or terminated by a signal")
        require(not (data / NETWORK / "chronik-upstream-service").exists(),
                "refusal touched development index root")
        require(not (data / "regtest/chronik-upstream-service").exists(),
                "refusal touched foreign index root")
    finally:
        node.finish()


def peers_ready(a, b, *, pong=False):
    left, right = a.rpc("getpeerinfo"), b.rpc("getpeerinfo")
    if len(left) != 1 or len(right) != 1:
        return False
    for peer, inbound in ((left[0], True), (right[0], False)):
        if peer.get("inbound") is not inbound or peer.get("version", 0) <= 0 or not peer.get("subver"):
            return False
        if pong and "pingtime" not in peer:
            return False
    return True


def run(binary, parent, chronik=False):
    model.load_profile()
    binary = binary.resolve(strict=True)
    require(binary.is_file() and os.access(binary, os.X_OK), "explicit executable required")
    parent = parent.resolve(strict=True)
    require(parent.is_dir(), "explicit disposable parent required")
    used, nodes = set(), []
    # This context deletes only its own newly-created directory, after all
    # child groups and listeners have been checked. A cleanup failure preserves it.
    temporary = tempfile.mkdtemp(prefix="ergon-testnet-smoke-", dir=parent)
    root = Path(temporary)
    success = False
    try:
        (root / "empty.conf").touch(mode=0o600)
        for phase, flags in (
            ("selection-conflict", ["-regtest=1"]),
            ("ema-override", ["-emaactivationtime=1"]),
            ("axion-override", ["-axionactivationtime=1"]),
            ("tachyon-override", ["-tachyonactivationtime=1"]),
        ):
            refusal(binary, root, used, phase, flags)
        if chronik:
            for phase, flags in (
                ("swapped-genesis", ["-chronikupstreamservice=1", "-chronikupstreamservicenetwork=" + NETWORK,
                                     "-chronikupstreamservicegenesis=" + MAIN_GENESIS]),
                ("swapped-network", ["-chronikupstreamservice=1", "-chronikupstreamservicenetwork=main",
                                     "-chronikupstreamservicegenesis=" + GENESIS]),
            ):
                refusal(binary, root, used, phase, flags)
        for name in ("a", "b"):
            data = root / (name + "-data")
            data.mkdir(mode=0o700)
            node = Node(binary, root, data, name, used, chronik=chronik and name == "a")
            nodes.append(node)
            node.ready(chronik=chronik and name == "a")
        a, b = nodes
        b.rpc("addnode", ["127.0.0.1:" + str(a.p2p_port), "onetry"])
        service.wait_for(lambda: peers_ready(a, b))
        a.rpc("ping")
        b.rpc("ping")
        service.wait_for(lambda: peers_ready(a, b, pong=True))
        script_hash = hashlib.new("ripemd160", hashlib.sha256(b"\x51").digest()).digest()
        address = "ergontest:" + encode("ergontest", SCRIPT_TYPE, script_hash)
        # Real SHA-256d work; no mocktime, reduced-rule mining or regtest override.
        # The dedicated deadline applies to this new fixture, not inherited gates.
        # The RPC socket's normal read deadline is short. Perform mining with
        # an explicit long RPC connection instead of weakening readiness checks.
        hashes = mine(a, address)
        require(isinstance(hashes, list) and len(hashes) == 5, "five accepted native blocks required")
        service.wait_for(lambda: b.rpc("getblockcount") == 5, timeout=60)
        require(a.rpc("getblockhash", [5]) == b.rpc("getblockhash", [5]) == hashes[-1],
                "accepted P2P tips differ")
        for block_hash in hashes:
            block = a.rpc("getblock", [block_hash, 2])
            require(all(output["value"] == 0 for output in block["tx"][0]["vout"]),
                    "low-work bootstrap subsidy was artificially funded")
        if chronik:
            a.tip(5, hashes[-1])
            status, raw = a.http("/block/0")
            require(status == 200 and bytes.fromhex(GENESIS)[::-1] in raw,
                    "Chronik did not return the native development genesis")
        for node in reversed(nodes):
            node.stop()
        nodes.clear()
        restarted = Node(binary, root, a.data, "restart", used, chronik=chronik)
        nodes.append(restarted)
        restarted.ready(chronik=chronik)
        require(restarted.rpc("getblockhash", [5]) == hashes[-1], "accepted history changed after restart")
        if chronik:
            restarted.tip(5, hashes[-1])
        restarted.stop()
        nodes.clear()
        success = True
    finally:
        cleanup_ok = True
        for node in reversed(nodes):
            try:
                node.finish()
            except BaseException:
                cleanup_ok = False
        require(cleanup_ok, "disposable process cleanup failed; fixture preserved")
        if success:
            import shutil
            shutil.rmtree(root)
            require(not root.exists(), "disposable directory cleanup failed")
    print("PASS: independent native development genesis, five accepted blocks, loopback P2P and restart" +
          ("; Chronik identity admission and indexed tips" if chronik else "; compiled-out node"))


def mine(node, address):
    import base64
    import http.client
    cookie = (node.data / NETWORK / ".cookie").read_bytes().strip()
    connection = http.client.HTTPConnection("127.0.0.1", node.rpc_port, timeout=600)
    try:
        connection.request("POST", "/", json.dumps({"jsonrpc": "1.0", "id": "testnet-mine",
            "method": "generatetoaddress", "params": [5, address, 2147483647]}),
            {"Authorization": "Basic " + base64.b64encode(cookie).decode(), "Content-Type": "application/json"})
        reply = connection.getresponse()
        raw = reply.read(16385)
        require(len(raw) <= 16384, "mining response bound")
        body = json.loads(raw)
        require(reply.status == 200 and body["error"] is None, "development mining refused")
        return body["result"]
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daemon", type=Path, required=True)
    parser.add_argument("--work-parent", type=Path, required=True)
    parser.add_argument("--chronik", action="store_true")
    args = parser.parse_args()
    try:
        run(args.daemon, args.work_parent, args.chronik)
    except Exception:
        print("FAIL: development-network smoke; private diagnostics remain local", file=sys.stderr)
        raise SystemExit(1)

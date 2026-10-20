# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Synthetic historical shadow: existing upstream ALP/queries, no operator data.

This is not a main-network replay or replacement-readiness claim. The separate
profile reconstructs 5000 node-accepted regtest blocks and an old ALP graph.
All wire builders and query/token oracles are reused from existing fixtures.
"""
from pathlib import Path
import http.client
import re
import socket
import subprocess
import tempfile

import cold_rebuild_service as cold

service, alp, runner = cold.service, cold.alp, cold.runner
HISTORY_HEIGHT = 5000
STARTUP_TIMEOUT_SECONDS = 120


class ShadowDaemon(service.Daemon):
    def __init__(self, binary, root, data, phase, genesis):
        self.data, self.root, self.public_plugin = data, root, False
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.rpc_port = reservation.getsockname()[1]
        self.log = root / (phase + ".log")
        self.output = self.log.open("xb")
        temporary = root / (phase + "-environment")
        temporary.mkdir()
        self.proc = subprocess.Popen([str(binary), "-regtest", "-server=1", "-listen=0",
            "-connect=0", "-dnsseed=0", "-discover=0", "-upnp=0", "-printtoconsole=1",
            "-acceptnonstdtxn=1", "-blockmintxfee=0", "-datadir=" + str(data),
            "-rpcbind=127.0.0.1", "-rpcallowip=127.0.0.1", "-rpcport=" + str(self.rpc_port),
            "-chronikupstreamshadow=1", "-chronikupstreamshadownetwork=regtest",
            "-chronikupstreamshadowgenesis=" + genesis], cwd=root,
            env=runner.environment(temporary), stdout=self.output, stderr=subprocess.STDOUT,
            start_new_session=True)
        self.port = None

    def ready(self):
        service.wait_for(lambda: self.rpc("getblockchaininfo"))
        def port():
            matches = re.findall(r"Chronik upstream shadow listening=127\.0\.0\.1:([0-9]+) plugins=0\b",
                                 self.log.read_text())
            runner.require(len(matches) <= 1, "ambiguous shadow listener")
            return int(matches[0]) if matches else None
        self.port = service.wait_for(port, timeout=STARTUP_TIMEOUT_SECONDS)

    def stop(self):
        self.rpc("stop")
        runner.require(self.proc.wait(timeout=20) == 0, "shadow positive stop refused")
        runner.stop_group(self.proc)
        self.output.close()
        runner.require("Chronik upstream shadow stopped joined=1" in self.log.read_text(),
                       "shadow stop was not joined")
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=1):
                raise runner.Rejected("shadow listener survived stop")
        except OSError:
            pass
        service.released_service_lease(self.data / "regtest/chronik-upstream-shadow")

    def crash(self):
        runner.require(self.port is not None and self.proc.poll() is None,
                       "shadow crash requires a live ready owner")
        self.proc.kill()
        runner.require(self.proc.wait(timeout=10) == -9, "shadow crash was not SIGKILL")
        runner.stop_group(self.proc)
        self.output.close()
        runner.require("Chronik upstream shadow stopped joined=1" not in self.log.read_text(),
                       "shadow crash was a positive stop")
        service.released_service_lease(self.data / "regtest/chronik-upstream-shadow")


def refusal_vectors(genesis, plugins):
    runner.require(type(plugins) is bool, "explicit compiled plugin profile required")
    valid = ["-chronikupstreamshadow=1", "-chronikupstreamshadownetwork=regtest",
             "-chronikupstreamshadowgenesis=" + genesis]
    cases = [
        ["-chronikupstreamshadow=1"],
        valid[:2],
        [valid[0], "-chronikupstreamshadownetwork=main", valid[2]],
        valid[:2] + ["-chronikupstreamshadowgenesis=" + "0" * 64],
        valid[:2] + ["-chronikupstreamshadowgenesis=" + "A" * 64],
        valid + ["-prune=550"],
        valid + ["-chronikupstreamregtest=1"],
        valid + ["-chronikupstreamregtestclients=1"],
        valid + ["-chronikupstreamregtestplugins=1"],
        valid + ["-chronikupstreamregtestpluginbundle=synthetic-not-opened"],
        valid + [valid[1]],
        valid[1:],
    ]
    shape = b"Shadow requires explicit network/genesis, unpruned storage and no regtest/client/plugin profile"
    identity = b"Shadow expected network/genesis does not match native parameters"
    enabled = b"Shadow identity requires exactly one enabled shadow profile"
    reasons = [shape, shape, identity, identity, identity, shape, shape, shape, shape, shape, shape, enabled]
    if not plugins:
        # These options are deliberately not registered in a non-plugin
        # binary. Its exact command-line refusal precedes profile interaction.
        for number in (8, 9):
            key = cases[number][-1].split("=", 1)[0]
            reasons[number] = ("Error parsing command line arguments: Invalid parameter " + key).encode()
    return tuple(zip(cases, reasons, strict=True))


def refusal_cases(binary, work, genesis, *, plugins=True):
    vectors = refusal_vectors(genesis, plugins)
    for number, (flags, expected_reason) in enumerate(vectors):
        root = work / ("shadow-refusal-" + str(number)); root.mkdir()
        data = root / "data"; data.mkdir()
        temp = root / "environment"; temp.mkdir()
        with (root / "refusal.log").open("xb") as log:
            result = subprocess.run([str(binary), "-regtest", "-listen=0", "-connect=0",
                "-dnsseed=0", "-discover=0", "-upnp=0", "-datadir=" + str(data)] + flags,
                env=runner.environment(temp), cwd=root, stdout=log, stderr=log, timeout=20)
        raw = (root / "refusal.log").read_bytes()
        runner.require(len(raw) <= 128 * 1024, "oversized shadow refusal diagnostic")
        raw.decode("utf8", "strict")
        runner.require(result.returncode == 1 and expected_reason in raw and
                       b"Chronik upstream shadow listening=" not in raw and
                       not (data / "regtest/chronik-upstream-shadow").exists() and
                       not (data / "regtest/chronik-upstream-regtest").exists(),
                       "invalid shadow profile did not fail before index creation;case=" + str(number))
    return len(vectors)


def observe(binary, work, scripts, *, plugins=True, on_failure=None):
    root = Path(tempfile.mkdtemp(prefix="ergon-upstream-", dir=work)).resolve()
    data = root / "data"; data.mkdir()
    index = data / "regtest/chronik-upstream-shadow"
    daemons = []
    try:
        native = service.Daemon(binary, root, data, "shadow-native-history", enabled=False)
        daemons.append(native)
        service.wait_for(lambda: native.rpc("getblockchaininfo"))
        genesis = native.rpc("getblockhash", (0,))
        hashes = [native.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0] for _ in range(101)]
        funding = native.rpc("getblock", (hashes[0], 2))["tx"][0]
        runner.require(all(out["value"] == 0 for out in funding["vout"]), "nonzero synthetic funding")
        wires, ids = [], []
        for op in range(4):
            points = ([(funding["txid"], 0, True)] if op == 0 else
                      [(ids[parent], out, False) for parent, out in cold.combined.INPUTS[op]])
            script = alp.bind_script(scripts, op, ids[0] if ids else "00" * 32)
            raw = alp.transaction(points, [script, b"\x51"] +
                                  ([b"\x52" if op == 2 else b"\x51"] if op < 3 else []))
            wires.append(raw); ids.append(alp.submit(native, raw))
        confirmed = native.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        block = native.rpc("getblock", (confirmed, 2))
        runner.require(block["height"] == 102 and cold.bind_ancestry(block, (funding["txid"], 0)) == ids,
                       "synthetic historical ALP ancestry drift")
        for _ in range(HISTORY_HEIGHT - 102):
            tip = native.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        runner.require(native.rpc("getblockcount") == HISTORY_HEIGHT and not index.exists() and
                       not (data / "regtest/chronik-upstream-regtest").exists() and
                       native.rpc("getconnectioncount") == 0, "shadow default-OFF/history precondition drift")
        native.stop()
        rejected = refusal_cases(binary, root, genesis, plugins=plugins)

        def start(phase):
            daemon = ShadowDaemon(binary, root, data, phase, genesis)
            daemons.append(daemon); daemon.ready()
            return daemon
        daemon = start("shadow-cold-index")
        daemon.tip(HISTORY_HEIGHT, tip); daemon.pool([])
        expected = alp.check_snapshot(daemon, ids, wires, [0] * 4, 102, confirmed)
        identity = runner.regular(index / "ergon-identity.json")
        runner.require(b'"schema":"ergon-historical-readonly/v1"' in identity and
                       b'"read_only":true' in identity and b'"plugins":false' in identity,
                       "wrong shadow identity domain")
        lease = index / "probe.lock"; inode = (lease.stat().st_dev, lease.stat().st_ino)
        daemon.rpc("invalidateblock", (tip,)); daemon.tip(HISTORY_HEIGHT - 1, daemon.rpc("getbestblockhash"))
        daemon.rpc("reconsiderblock", (tip,)); daemon.tip(HISTORY_HEIGHT, tip)
        pending_coin = daemon.rpc("getblock", (hashes[1], 2))["tx"][0]["txid"]
        pending = alp.submit(daemon, service.zero_tx(pending_coin, 0, True)); daemon.pool([pending])
        runner.require(daemon.http("/ws")[0] == 501 and daemon.http("/broadcast-tx", "POST")[0] == 501,
                       "shadow transport capability widened")
        daemon.stop()
        daemon = start("shadow-pending-reopen"); daemon.tip(HISTORY_HEIGHT, tip); daemon.pool([pending])
        runner.require(alp.check_snapshot(daemon, ids, wires, [0] * 4, 102, confirmed) == expected,
                       "historical ALP changed after pending process reopen")
        daemon.crash()
        daemon = start("shadow-crash-reopen"); daemon.tip(HISTORY_HEIGHT, tip); daemon.pool([pending])
        next_tip = daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        daemon.tip(HISTORY_HEIGHT + 1, next_tip); daemon.pool([])
        runner.require(alp.check_snapshot(daemon, ids, wires, [0] * 4, 102, confirmed) == expected and
                       runner.regular(index / "ergon-identity.json") == identity and
                       (lease.stat().st_dev, lease.stat().st_ino) == inode and
                       not (index / "probe.failed").exists() and
                       not (data / "regtest/chronik-upstream-regtest").exists() and
                       daemon.rpc("getconnectioncount") == 0, "shadow reconstruction/identity/native drift")
        daemon.stop()
        return {"history_height": HISTORY_HEIGHT, "final_height": HISTORY_HEIGHT + 1,
                "historical_ALP_corpus_height": 102, "historical_first_seen": "explicit-unknown-zero",
                "upstream_ALP_genesis_mint_send_burn_and_query_oracles": True,
                "same_identity_and_lease_reopens": True, "nonempty_pool_positive_reopen": True,
                "SIGKILL_and_reopen": True, "native_reorg_and_mining": True,
                "invalid_profile_refusals": rejected, "daemon_processes": 4,
                "external_connections": 0, "plugins": False, "websockets": False,
                "broadcast": False, "mainnet_history": False, "operator_data": False}
    except BaseException:
        # Optional private diagnostic hook, invoked only before failed-root
        # cleanup. Raw logs must never enter a public observation report.
        if on_failure is not None:
            on_failure(root)
        raise
    finally:
        failed = False
        for daemon in daemons:
            try:
                daemon.cleanup()
            except BaseException:
                failed = True
        if failed:
            raise runner.Rejected("shadow daemon group cleanup failed")
        runner.remove_owned(root, work)

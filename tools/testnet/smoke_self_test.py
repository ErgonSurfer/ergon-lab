#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic contract checks only; the actual daemon smoke remains separate."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock
import run_smoke as smoke


class SmokeTests(unittest.TestCase):
    def test_socket_count_is_not_a_successful_p2p_handshake(self):
        a, b = Mock(), Mock()
        a.rpc.return_value = [{"version": 0, "inbound": True, "subver": ""}]
        b.rpc.return_value = [{"version": 0, "inbound": False, "subver": ""}]
        self.assertFalse(smoke.peers_ready(a, b))
        a.rpc.return_value = [{"version": 70016, "inbound": True, "subver": "synthetic"}]
        b.rpc.return_value = [{"version": 70016, "inbound": False, "subver": "synthetic"}]
        self.assertTrue(smoke.peers_ready(a, b))
        self.assertFalse(smoke.peers_ready(a, b, pong=True))
        a.rpc.return_value[0]["pingtime"] = .1
        b.rpc.return_value[0]["pingtime"] = .1
        self.assertTrue(smoke.peers_ready(a, b, pong=True))

    def test_ready_requires_native_genesis_and_display_identity(self):
        node = object.__new__(smoke.Node)
        for change in ({}, {"chain": "main"}, {"currency": {"ticker": "XRG", "decimals": 8}}):
            info = {"chain": smoke.NETWORK, "currency": {"ticker": "tXRG", "decimals": 8},
                    "blocks": 0, "bestblockhash": smoke.GENESIS}
            info.update(change)
            node.rpc = lambda method, params=(): info if method == "getblockchaininfo" else smoke.GENESIS
            with patch.object(smoke.service, "wait_for", side_effect=lambda f, **kw: f()):
                if change:
                    with self.assertRaises(RuntimeError): node.ready()
                else:
                    node.ready()
        node.rpc = lambda method, params=(): info if method == "getblockchaininfo" else smoke.MAIN_GENESIS
        with patch.object(smoke.service, "wait_for", side_effect=lambda f, **kw: f()), self.assertRaises(RuntimeError):
            node.ready()

    def test_refusal_must_be_nonzero_and_must_join(self):
        for exit_code in (0, -6, 1):
            fake = Mock()
            fake.proc.wait.return_value = exit_code
            with tempfile.TemporaryDirectory() as name, patch.object(smoke, "Node", return_value=fake):
                if exit_code <= 0:
                    with self.assertRaises(RuntimeError):
                        smoke.refusal(Path(sys.executable), Path(name), set(), "refuse", ["-regtest"])
                else:
                    smoke.refusal(Path(sys.executable), Path(name), set(), "refuse", ["-regtest"])
                fake.finish.assert_called_once()

    def test_refusal_rejects_created_index(self):
        fake = Mock()
        fake.proc.wait.return_value = 1
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            def constructor(binary, root, data, *args, **kwargs):
                (data / smoke.NETWORK / "chronik-upstream-service").mkdir(parents=True)
                return fake
            with patch.object(smoke, "Node", side_effect=constructor), self.assertRaises(RuntimeError):
                smoke.refusal(Path(sys.executable), root, set(), "refuse", ["-regtest"])
            fake.finish.assert_called_once()

    def test_listener_survival_is_failure_after_group_cleanup(self):
        node = object.__new__(smoke.Node)
        node.proc, node.output = Mock(), Mock()
        node.rpc_port, node.p2p_port, node.port = 45001, 45002, None
        with patch.object(smoke.service.runner, "stop_group") as stop:
            with patch.object(smoke.socket, "create_connection", return_value=contextlib.nullcontext()), self.assertRaises(RuntimeError):
                node.finish()
            stop.assert_called_once_with(node.proc)
        with patch.object(smoke.service.runner, "stop_group"), patch.object(smoke.socket, "create_connection", side_effect=OSError):
            node.finish()

    def test_complete_synthetic_flow_requires_five_zero_value_blocks(self):
        hashes = [format(i, "064x") for i in range(1, 6)]
        instances = []
        class Fake:
            def __init__(self, binary, root, data, phase, used, chronik=False, extra=()):
                self.data, self.p2p_port, self.stopped = data, 45000 + len(instances), False
                self.inbound = phase == "a"
                self.proc = Mock()
                self.proc.wait.return_value = 1
                instances.append(self)
            def ready(self, **kwargs): pass
            def tip(self, *args): pass
            def stop(self): self.stopped = True
            def finish(self): self.stopped = True
            def rpc(self, method, params=()):
                if method == "getpeerinfo":
                    return [{"version": 70016, "inbound": self.inbound, "subver": "synthetic", "pingtime": .1}]
                return {"getconnectioncount": 1, "getblockcount": 5, "getblockhash": hashes[-1],
                        "getblock": {"tx": [{"vout": [{"value": 0}]}]}}.get(method)
        with tempfile.TemporaryDirectory() as name, patch.object(smoke, "Node", Fake), \
             patch.object(smoke, "mine", return_value=hashes), \
             patch.object(smoke.service, "wait_for", side_effect=lambda f, **kwargs: f()), \
             contextlib.redirect_stdout(io.StringIO()):
            smoke.run(Path(sys.executable), Path(name))
            self.assertEqual(list(Path(name).iterdir()), [])
        self.assertTrue(all(node.stopped for node in instances))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Exercise the opt-in volatile Chronik block-event boundary."""

import configparser
import os
import re
import stat

from test_framework.test_framework import BitcoinTestFramework, SkipTest
from test_framework.test_node import ErrorMatch
from test_framework.util import assert_equal


EVENT_RE = re.compile(
    r"Chronik observer event sequence=(\d+) "
    r"kind=(connected|disconnected) "
    r"hash=([0-9a-f]{64}) height=(-?\d+) fingerprint=(\d+)"
)
FNV_OFFSET_BASIS = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3


def event_fingerprint(kind, block_hash, height):
    fingerprint = FNV_OFFSET_BASIS
    kind_byte = 1 if kind == "connected" else 2
    internal_hash = bytes.fromhex(block_hash)[::-1]
    payload = bytes([kind_byte]) + internal_hash + height.to_bytes(
        4, byteorder="little", signed=True
    )
    for byte in payload:
        fingerprint ^= byte
        fingerprint = (fingerprint * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return fingerprint


class ChronikBlockObserverTest(BitcoinTestFramework):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 1
        self.extra_args = [["-connect=0", "-disablewallet"]]

    def skip_test_if_missing_module(self):
        config = configparser.ConfigParser()
        with open(self.options.configfile, encoding="utf-8") as config_file:
            config.read_file(config_file)
        if not config["components"].getboolean("ENABLE_CHRONIK_OBSERVER"):
            raise SkipTest("bitcoind has not been built with the Chronik observer.")

    def log_path(self):
        node = self.nodes[0]
        return os.path.join(node.datadir, "regtest", "debug.log")

    def read_log(self):
        with open(self.log_path(), encoding="utf-8") as debug_log:
            return debug_log.read()

    def read_events(self):
        return [
            (int(sequence), kind, block_hash, int(height), int(fingerprint))
            for sequence, kind, block_hash, height, fingerprint in EVENT_RE.findall(
                self.read_log()
            )
        ]

    def expected_event(self, sequence, kind, block_hash, height):
        return (
            sequence,
            kind,
            block_hash,
            height,
            event_fingerprint(kind, block_hash, height),
        )

    def assert_no_chronik_paths(self):
        chain_dir = os.path.join(self.nodes[0].datadir, "regtest")
        found_paths = []
        found_sockets = []
        for root, directories, files in os.walk(chain_dir):
            for name in directories + files:
                path = os.path.join(root, name)
                if "chronik" in name.lower():
                    found_paths.append(os.path.relpath(path, chain_dir))
                if stat.S_ISSOCK(os.lstat(path).st_mode):
                    found_sockets.append(os.path.relpath(path, chain_dir))
        assert_equal(found_paths, [])
        assert_equal(found_sockets, [])

    def assert_network_rejected(self, network_args):
        self.nodes[0].assert_start_raises_init_error(
            extra_args=[
                "-connect=0",
                "-disablewallet",
                "-regtest=0",
                *network_args,
                "-chronikobserver",
            ],
            expected_msg=(
                "Error: -chronikobserver is restricted to the local regtest profile"
            ),
            match=ErrorMatch.PARTIAL_REGEX,
        )

    def run_test(self):
        node = self.nodes[0]
        assert "Chronik observer" not in self.read_log()
        assert_equal(node.getnetworkinfo()["connections"], 0)
        self.assert_no_chronik_paths()

        address = node.get_deterministic_priv_key().address
        disabled_blocks = node.generatetoaddress(2, address)
        node.syncwithvalidationinterfacequeue()
        node.invalidateblock(disabled_blocks[0])
        node.syncwithvalidationinterfacequeue()
        node.reconsiderblock(disabled_blocks[0])
        node.syncwithvalidationinterfacequeue()
        assert_equal(node.getbestblockhash(), disabled_blocks[1])
        assert "Chronik observer" not in self.read_log()
        self.stop_node(0)

        self.start_node(
            0,
            extra_args=["-connect=0", "-disablewallet", "-chronikobserver"],
        )
        assert "Chronik observer started mode=in-memory events=blocks" in self.read_log()
        assert_equal(node.getnetworkinfo()["connections"], 0)
        self.assert_no_chronik_paths()

        observed_blocks = node.generatetoaddress(2, address)
        node.syncwithvalidationinterfacequeue()
        node.invalidateblock(observed_blocks[0])
        node.syncwithvalidationinterfacequeue()
        node.reconsiderblock(observed_blocks[0])
        node.syncwithvalidationinterfacequeue()
        assert_equal(node.getbestblockhash(), observed_blocks[1])

        expected = [
            self.expected_event(1, "connected", observed_blocks[0], 3),
            self.expected_event(2, "connected", observed_blocks[1], 4),
            self.expected_event(3, "disconnected", observed_blocks[1], -1),
            self.expected_event(4, "disconnected", observed_blocks[0], -1),
            self.expected_event(5, "connected", observed_blocks[0], 3),
            self.expected_event(6, "connected", observed_blocks[1], 4),
        ]
        assert_equal(self.read_events(), expected)
        self.assert_no_chronik_paths()

        self.stop_node(0)
        observer_lines = [
            line for line in self.read_log().splitlines()
            if "Chronik observer" in line
        ]
        assert observer_lines[-1].endswith(
            "Chronik observer stopped observations=6"
        )

        self.assert_network_rejected([])
        self.assert_network_rejected(["-testnet"])
        self.assert_network_rejected(["-testnet4"])
        self.assert_network_rejected(["-scalenet"])


if __name__ == "__main__":
    ChronikBlockObserverTest().main()

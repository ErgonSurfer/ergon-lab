#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Exercise the bounded, reconstructible Chronik block projection."""

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
CONNECTED_RE = re.compile(
    r"Chronik observer event sequence=(\d+) kind=connected "
    r"hash=([0-9a-f]{64}) height=(-?\d+) fingerprint=(\d+) "
    r"bytes=(\d+) payload_fingerprint=(\d+) transactions=(\d+) "
    r"projection_blocks=(\d+) projection_transactions=(\d+)"
)
DISCONNECTED_RE = re.compile(
    r"Chronik observer event sequence=(\d+) kind=disconnected "
    r"hash=([0-9a-f]{64}) height=-1 fingerprint=(\d+) "
    r"transactions=(\d+) projection_blocks=(\d+) "
    r"projection_transactions=(\d+)"
)
BOOTSTRAP_RE = re.compile(
    r"Chronik observer bootstrap start_height=(\d+) tip_height=(\d+) "
    r"retained_blocks=(\d+) transactions=(\d+)"
)
FNV_OFFSET_BASIS = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3


def fnv_fingerprint(payload):
    fingerprint = FNV_OFFSET_BASIS
    for byte in payload:
        fingerprint ^= byte
        fingerprint = (fingerprint * FNV_PRIME) & 0xFFFFFFFFFFFFFFFF
    return fingerprint


def event_fingerprint(kind, block_hash, height):
    kind_byte = 1 if kind == "connected" else 2
    payload = (
        bytes([kind_byte])
        + bytes.fromhex(block_hash)[::-1]
        + height.to_bytes(4, byteorder="little", signed=True)
    )
    return fnv_fingerprint(payload)


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
        return os.path.join(self.nodes[0].datadir, "regtest", "debug.log")

    def read_log(self, offset=0):
        with open(self.log_path(), encoding="utf-8") as debug_log:
            debug_log.seek(offset)
            return debug_log.read()

    def read_events(self, offset=0):
        return [
            (int(sequence), kind, block_hash, int(height), int(fingerprint))
            for sequence, kind, block_hash, height, fingerprint in EVENT_RE.findall(
                self.read_log(offset)
            )
        ]

    def read_connected(self, offset=0):
        return [
            tuple(int(value) if index != 1 else value for index, value in enumerate(event))
            for event in CONNECTED_RE.findall(self.read_log(offset))
        ]

    def read_disconnected(self, offset=0):
        return [
            tuple(int(value) if index != 1 else value for index, value in enumerate(event))
            for event in DISCONNECTED_RE.findall(self.read_log(offset))
        ]

    def read_bootstrap(self, offset=0):
        return [
            tuple(int(value) for value in event)
            for event in BOOTSTRAP_RE.findall(self.read_log(offset))
        ]

    def expected_event(self, sequence, kind, block_hash, height):
        return (
            sequence,
            kind,
            block_hash,
            height,
            event_fingerprint(kind, block_hash, height),
        )

    def expected_connected(
        self, sequence, block_hash, height, projection_blocks, projection_transactions
    ):
        raw_block = bytes.fromhex(self.nodes[0].getblock(block_hash, 0))
        return (
            sequence,
            block_hash,
            height,
            event_fingerprint("connected", block_hash, height),
            len(raw_block),
            fnv_fingerprint(raw_block),
            1,
            projection_blocks,
            projection_transactions,
        )

    def expected_disconnected(
        self, sequence, block_hash, projection_blocks, projection_transactions
    ):
        return (
            sequence,
            block_hash,
            event_fingerprint("disconnected", block_hash, -1),
            1,
            projection_blocks,
            projection_transactions,
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
        address = node.get_deterministic_priv_key().address
        assert "Chronik observer" not in self.read_log()
        assert_equal(node.getnetworkinfo()["connections"], 0)
        self.assert_no_chronik_paths()

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
        assert_equal(self.read_bootstrap(), [(0, 2, 3, 3)])
        assert_equal(node.getnetworkinfo()["connections"], 0)
        self.assert_no_chronik_paths()

        observed_blocks = node.generatetoaddress(2, address)
        node.syncwithvalidationinterfacequeue()
        node.invalidateblock(observed_blocks[0])
        node.syncwithvalidationinterfacequeue()
        node.reconsiderblock(observed_blocks[0])
        node.syncwithvalidationinterfacequeue()
        assert_equal(node.getbestblockhash(), observed_blocks[1])
        assert_equal(
            self.read_events(),
            [
                self.expected_event(1, "connected", observed_blocks[0], 3),
                self.expected_event(2, "connected", observed_blocks[1], 4),
                self.expected_event(3, "disconnected", observed_blocks[1], -1),
                self.expected_event(4, "disconnected", observed_blocks[0], -1),
                self.expected_event(5, "connected", observed_blocks[0], 3),
                self.expected_event(6, "connected", observed_blocks[1], 4),
            ],
        )
        assert_equal(
            self.read_connected(),
            [
                self.expected_connected(1, observed_blocks[0], 3, 4, 4),
                self.expected_connected(2, observed_blocks[1], 4, 5, 5),
                self.expected_connected(5, observed_blocks[0], 3, 4, 4),
                self.expected_connected(6, observed_blocks[1], 4, 5, 5),
            ],
        )
        assert_equal(
            self.read_disconnected(),
            [
                self.expected_disconnected(3, observed_blocks[1], 4, 4),
                self.expected_disconnected(4, observed_blocks[0], 3, 3),
            ],
        )
        self.stop_node(0)
        assert "Chronik observer stopped observations=6" in self.read_log()

        restart_offset = os.path.getsize(self.log_path())
        self.start_node(
            0,
            extra_args=["-connect=0", "-disablewallet", "-chronikobserver"],
        )
        assert_equal(self.read_bootstrap(restart_offset), [(0, 4, 5, 5)])
        restart_block = node.generatetoaddress(1, address)[0]
        node.syncwithvalidationinterfacequeue()
        assert_equal(
            self.read_connected(restart_offset),
            [self.expected_connected(1, restart_block, 5, 6, 6)],
        )
        node.generatetoaddress(283, address)
        node.syncwithvalidationinterfacequeue()
        assert_equal(node.getblockcount(), 288)
        self.stop_node(0)

        reindex_offset = os.path.getsize(self.log_path())
        self.start_node(
            0,
            extra_args=[
                "-connect=0",
                "-disablewallet",
                "-chronikobserver",
                "-reindex=1",
            ],
        )
        node.syncwithvalidationinterfacequeue()
        active_blocks = [node.getblockhash(height) for height in range(289)]
        assert "Chronik observer bootstrap active_chain=empty retained_blocks=0" in self.read_log(
            reindex_offset
        )
        assert_equal(
            self.read_events(reindex_offset),
            [
                self.expected_event(sequence, "connected", block_hash, height)
                for sequence, (height, block_hash) in enumerate(
                    enumerate(active_blocks), start=1
                )
            ],
        )
        assert_equal(
            self.read_connected(reindex_offset),
            [
                self.expected_connected(
                    sequence,
                    block_hash,
                    height,
                    min(height + 1, 288),
                    min(height + 1, 288),
                )
                for sequence, (height, block_hash) in enumerate(
                    enumerate(active_blocks), start=1
                )
            ],
        )

        retained_anchor = node.getblockhash(1)
        node.invalidateblock(retained_anchor)
        node.syncwithvalidationinterfacequeue()
        assert_equal(node.getblockcount(), 0)
        events_before_reconsider = self.read_events(reindex_offset)
        assert_equal(events_before_reconsider[-1][0], 577)
        assert_equal(
            self.read_log(reindex_offset).count(
                "Chronik observer state=rebuild-required "
                "hash="
            ),
            1,
        )
        assert (
            "reason=retained-anchor-disconnected recovery=restart"
            in self.read_log(reindex_offset)
        )

        node.reconsiderblock(retained_anchor)
        node.syncwithvalidationinterfacequeue()
        assert_equal(node.getblockcount(), 288)
        assert_equal(self.read_events(reindex_offset), events_before_reconsider)
        self.assert_no_chronik_paths()
        self.stop_node(0)
        assert "Chronik observer stopped observations=577" in self.read_log(
            reindex_offset
        )

        recovery_offset = os.path.getsize(self.log_path())
        self.start_node(
            0,
            extra_args=["-connect=0", "-disablewallet", "-chronikobserver"],
        )
        assert_equal(self.read_bootstrap(recovery_offset), [(1, 288, 288, 288)])
        recovery_block = node.generatetoaddress(1, address)[0]
        node.syncwithvalidationinterfacequeue()
        assert_equal(
            self.read_connected(recovery_offset),
            [self.expected_connected(1, recovery_block, 289, 288, 288)],
        )
        self.assert_no_chronik_paths()
        self.stop_node(0)
        assert "Chronik observer stopped observations=1" in self.read_log(
            recovery_offset
        )

        self.assert_network_rejected([])
        self.assert_network_rejected(["-testnet"])
        self.assert_network_rejected(["-testnet4"])
        self.assert_network_rejected(["-scalenet"])


if __name__ == "__main__":
    ChronikBlockObserverTest().main()

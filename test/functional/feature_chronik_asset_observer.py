#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Observe native-asset family markers without assigning token validity."""

import os

from feature_chronik_block_observer import ChronikBlockObserverTest
from test_framework.blocktools import create_block, create_coinbase
from test_framework.messages import ToHex
from test_framework.script import CScript, OP_RESERVED, OP_RETURN
from test_framework.util import assert_equal


class ChronikAssetObserverTest(ChronikBlockObserverTest):
    def set_test_params(self):
        super().set_test_params()

    @staticmethod
    def slp_script():
        return CScript(
            [
                OP_RETURN,
                b"SLP\x00",
                b"\x01",
                b"SEND",
                bytes([0x11]) * 32,
                (1).to_bytes(8, "big"),
            ]
        )

    @staticmethod
    def alp_script():
        section = (
            b"SLP2"
            + b"\x00"
            + b"\x04SEND"
            + bytes([0x22]) * 32
            + b"\x01"
            + (1).to_bytes(6, "little")
        )
        return CScript([OP_RETURN, OP_RESERVED, section])

    @staticmethod
    def alp_color_failure_script():
        amounts = b"".join(value.to_bytes(6, "little") for value in range(1, 9))
        section = (
            b"SLP2"
            + b"\x00"
            + b"\x04SEND"
            + bytes([0x33]) * 32
            + b"\x08"
            + amounts
        )
        return CScript([OP_RETURN, OP_RESERVED, section])

    @staticmethod
    def malformed_slp_script():
        return CScript([OP_RETURN, b"SLP\x00"])

    @staticmethod
    def cash_token_prefix_candidate_script():
        # This is deliberately only a wire-prefix candidate. The legacy node
        # neither parses nor validates CashTokens in this test.
        return CScript(b"\xef")

    def mine_marker_block(self, script):
        node = self.nodes[0]
        height = node.getblockcount() + 1
        previous_hash = node.getbestblockhash()
        previous_time = node.getblockheader(previous_hash)["time"]
        coinbase = create_coinbase(height)
        coinbase.vout[0].nValue = 0
        coinbase.vout[0].scriptPubKey = script
        coinbase.rehash()
        block = create_block(int(previous_hash, 16), coinbase, previous_time + 1)
        block.solve()
        assert_equal(node.submitblock(ToHex(block)), None)
        node.syncwithvalidationinterfacequeue()
        assert_equal(node.getbestblockhash(), block.hash)
        return block.hash

    def run_test(self):
        node = self.nodes[0]
        assert "Chronik observer" not in self.read_log()
        self.stop_node(0)

        self.start_node(
            0,
            extra_args=["-connect=0", "-disablewallet", "-chronikobserver"],
        )
        assert_equal(self.read_bootstrap(), [(0, 0, 1, 1, 0, 0, 0, 0, 0)])
        assert_equal(self.read_events(), [])

        slp_block = self.mine_marker_block(self.slp_script())
        alp_block = self.mine_marker_block(self.alp_script())
        color_failure_block = self.mine_marker_block(self.alp_color_failure_script())
        prefix_block = self.mine_marker_block(
            self.cash_token_prefix_candidate_script()
        )
        malformed_block = self.mine_marker_block(self.malformed_slp_script())

        assert_equal(
            self.read_connected(),
            [
                self.expected_connected(
                    1,
                    slp_block,
                    1,
                    2,
                    2,
                    block_assets=(1, 0, 0, 0, 0),
                    projection_assets=(1, 0, 0, 0, 0),
                ),
                self.expected_connected(
                    2,
                    alp_block,
                    2,
                    3,
                    3,
                    block_assets=(0, 1, 0, 0, 0),
                    projection_assets=(1, 1, 0, 0, 0),
                ),
                self.expected_connected(
                    3,
                    color_failure_block,
                    3,
                    4,
                    4,
                    block_assets=(0, 1, 0, 1, 0),
                    projection_assets=(1, 2, 0, 1, 0),
                ),
                self.expected_connected(
                    4,
                    prefix_block,
                    4,
                    5,
                    5,
                    block_assets=(0, 0, 0, 0, 1),
                    projection_assets=(1, 2, 0, 1, 1),
                ),
                self.expected_connected(
                    5,
                    malformed_block,
                    5,
                    6,
                    6,
                    block_assets=(0, 0, 1, 0, 0),
                    projection_assets=(1, 2, 1, 1, 1),
                ),
            ],
        )

        node.invalidateblock(slp_block)
        node.syncwithvalidationinterfacequeue()
        assert_equal(node.getblockcount(), 0)
        assert_equal(
            self.read_disconnected(),
            [
                self.expected_disconnected(
                    6,
                    malformed_block,
                    5,
                    5,
                    block_assets=(0, 0, 1, 0, 0),
                    projection_assets=(1, 2, 0, 1, 1),
                ),
                self.expected_disconnected(
                    7,
                    prefix_block,
                    4,
                    4,
                    block_assets=(0, 0, 0, 0, 1),
                    projection_assets=(1, 2, 0, 1, 0),
                ),
                self.expected_disconnected(
                    8,
                    color_failure_block,
                    3,
                    3,
                    block_assets=(0, 1, 0, 1, 0),
                    projection_assets=(1, 1, 0, 0, 0),
                ),
                self.expected_disconnected(
                    9,
                    alp_block,
                    2,
                    2,
                    block_assets=(0, 1, 0, 0, 0),
                    projection_assets=(1, 0, 0, 0, 0),
                ),
                self.expected_disconnected(
                    10,
                    slp_block,
                    1,
                    1,
                    block_assets=(1, 0, 0, 0, 0),
                ),
            ],
        )

        node.reconsiderblock(slp_block)
        node.syncwithvalidationinterfacequeue()
        assert_equal(node.getbestblockhash(), malformed_block)
        assert_equal(len(self.read_connected()), 10)
        self.assert_no_chronik_paths()
        self.stop_node(0)

        restart_offset = os.path.getsize(self.log_path())
        self.start_node(
            0,
            extra_args=["-connect=0", "-disablewallet", "-chronikobserver"],
        )
        assert_equal(
            self.read_bootstrap(restart_offset),
            [(0, 5, 6, 6, 1, 2, 1, 1, 1)],
        )
        assert_equal(self.read_events(restart_offset), [])
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
        assert_equal(node.getbestblockhash(), malformed_block)
        assert "Chronik observer bootstrap active_chain=empty retained_blocks=0" in self.read_log(
            reindex_offset
        )
        reindexed = self.read_connected(reindex_offset)
        assert_equal(len(reindexed), 6)
        assert_equal(reindexed[-1][-5:], (1, 2, 1, 1, 1))
        assert "Chronik observer rejected" not in self.read_log(reindex_offset)
        self.assert_no_chronik_paths()


if __name__ == "__main__":
    ChronikAssetObserverTest().main()

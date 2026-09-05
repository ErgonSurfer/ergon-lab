#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Exercise bounded Chronik reconstruction from a truly pruned datadir."""

import os

from feature_chronik_block_observer import ChronikBlockObserverTest
from feature_pruning import mine_large_blocks
from test_framework.util import assert_equal, assert_raises_rpc_error, wait_until


class ChronikPrunedObserverTest(ChronikBlockObserverTest):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 1
        self.rpc_timeout = 300
        self.extra_args = [[
            "-connect=0",
            "-disablewallet",
            "-prune=1",
            "-blockmaxsize=999000",
        ]]

    def run_test(self):
        node = self.nodes[0]
        address = node.get_deterministic_priv_key().address
        assert "Chronik observer" not in self.read_log()

        # Cross the 128 MiB block-file boundary, then use cheap blocks to pass
        # regtest's PruneAfterHeight while keeping the retained suffix small.
        mine_large_blocks(node, 150, coinbase_value=0)
        assert_equal(node.getblockcount(), 150)
        node.generatetoaddress(851, address)
        assert_equal(node.getblockcount(), 1001)

        blocks_dir = os.path.join(node.datadir, "regtest", "blocks")
        block_file_0 = os.path.join(blocks_dir, "blk00000.dat")
        block_file_1 = os.path.join(blocks_dir, "blk00001.dat")
        assert os.path.isfile(block_file_0)
        assert os.path.isfile(block_file_1)

        old_hash = node.getblockhash(1)
        retained_hash = node.getblockhash(714)
        tip_hash = node.getblockhash(1001)
        chain_info = node.getblockchaininfo()
        assert_equal(chain_info["blocks"], 1001)
        assert_equal(chain_info["pruned"], True)
        assert_equal(chain_info["automatic_pruning"], False)

        assert_equal(node.pruneblockchain(713), 713)
        wait_until(lambda: not os.path.exists(block_file_0), timeout=30)
        assert os.path.isfile(block_file_1)

        chain_info = node.getblockchaininfo()
        assert chain_info["pruneheight"] > 0
        assert chain_info["pruneheight"] <= 714
        assert_raises_rpc_error(
            -1, "Block not available (pruned data)", node.getblock, old_hash
        )
        assert_equal(node.getblockheader(old_hash)["height"], 1)
        assert_equal(node.getblock(retained_hash)["height"], 714)
        assert_equal(node.getbestblockhash(), tip_hash)

        self.stop_node(0)
        restart_offset = os.path.getsize(self.log_path())
        self.start_node(
            0,
            extra_args=[
                "-connect=0",
                "-disablewallet",
                "-prune=1",
                "-blockmaxsize=999000",
                "-chronikobserver",
            ],
        )
        assert_equal(
            self.read_bootstrap(restart_offset),
            [(714, 1001, 288, 288, 0, 0, 0, 0, 0)],
        )
        assert_equal(self.read_events(restart_offset), [])
        chain_info = node.getblockchaininfo()
        assert_equal(chain_info["pruned"], True)
        assert_equal(chain_info["automatic_pruning"], False)
        assert_raises_rpc_error(
            -1, "Block not available (pruned data)", node.getblock, old_hash
        )
        assert_equal(node.getblock(retained_hash)["height"], 714)
        assert_equal(node.getbestblockhash(), tip_hash)
        self.assert_no_chronik_paths()

        next_hash = node.generatetoaddress(1, address)[0]
        node.syncwithvalidationinterfacequeue()
        assert_equal(
            self.read_connected(restart_offset),
            [self.expected_connected(1, next_hash, 1002, 288, 288)],
        )
        self.assert_no_chronik_paths()
        self.stop_node(0)
        assert "Chronik observer stopped observations=1" in self.read_log(
            restart_offset
        )


if __name__ == "__main__":
    ChronikPrunedObserverTest().main()

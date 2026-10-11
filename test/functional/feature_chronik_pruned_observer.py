#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Exercise bounded Chronik reconstruction from a truly pruned datadir."""

import os
import threading

from feature_chronik_block_observer import ChronikBlockObserverTest
from feature_pruning import mine_large_blocks
from feature_chronik_token_http import ChronikTokenHttpTest, chronik_port, parse_protobuf
from test_framework.util import assert_equal, assert_raises_rpc_error, wait_until


class ChronikPrunedObserverTest(ChronikBlockObserverTest):
    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 2
        self.rpc_timeout = 300
        self.extra_args = [[
            "-connect=0",
            "-disablewallet",
            "-prune=1",
            "-blockmaxsize=999000",
        ]]
        self.extra_args.append(list(self.extra_args[0]))

    def exercise_indexed_pruning(self):
        """Existing index metadata must not disguise unavailable node bodies."""
        self.chronik_port = chronik_port()
        self.restart_node(1, extra_args=[
            "-connect=0", "-disablewallet", "-prune=1", "-blockmaxsize=999000",
            "-chronikobserver", f"-chronikbind=127.0.0.1:{self.chronik_port}",
        ])
        node = self.nodes[1]
        def request(txid):
            return ChronikTokenHttpTest.request(self, f"/tx/{txid}")

        def wait_for_tip():
            node.syncwithvalidationinterfacequeue()
            txid = node.getblock(node.getbestblockhash())["tx"][0]
            wait_until(lambda: request(txid)[0] == 200, timeout=180)
            return txid

        mine_large_blocks(node, 150, coinbase_value=0)
        wait_for_tip()
        # This is a storage-pruning canary, not an event-envelope overflow
        # stress test. Let the asynchronous index catch up between bounded
        # batches so slower CI machines exercise the intended boundary.
        for offset in range(0, 851, 100):
            node.generatetoaddress(min(100, 851 - offset), node.get_deterministic_priv_key().address)
            tip_txid = wait_for_tip()
        old_hash = node.getblockhash(1)
        old_txid = node.getblock(old_hash)["tx"][0]
        assert_equal(request(old_txid)[0], 200)
        stop = threading.Event()
        ready = threading.Event()
        statuses = []
        errors = []

        def query_during_prune():
            try:
                for _ in range(100):
                    if stop.is_set():
                        break
                    status, _ = request(old_txid)
                    statuses.append(status)
                    assert status in (200, 503)
                    if status == 200:
                        ready.set()
            except Exception as error:
                errors.append(error)
                ready.set()

        reader = threading.Thread(target=query_during_prune)
        reader.start()
        try:
            assert ready.wait(10), "concurrent query did not complete"
            assert_equal(node.pruneblockchain(713), 713)
        finally:
            stop.set()
            reader.join(10)
        assert not reader.is_alive(), "concurrent query did not stop"
        assert not errors, errors
        assert 200 in statuses
        wait_until(lambda: not os.path.exists(os.path.join(node.datadir, "regtest", "blocks", "blk00000.dat")), timeout=30)
        status, body = request(old_txid)
        assert_equal(status, 503)
        assert_equal(parse_protobuf(body)[2][0][1].decode(), "503: Confirmed transaction data unavailable")
        assert_equal(request(tip_txid)[0], 200)
        assert_equal(node.getblockcount(), 1001)
        self.stop_node(1)

    def run_test(self):
        self.exercise_indexed_pruning()
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
        restart_log = self.read_log(restart_offset)
        assert "Chronik runtime rejected kind=bootstrap-read" in restart_log
        assert (
            "Chronik runtime state=disabled reason=bootstrap-failed "
            "node_continues=1 observer_continues=1 recovery=reindex"
            in restart_log
        )
        assert (
            "Chronik observer started mode=persistent-token-runtime "
            "runtime_enabled=0"
            in restart_log
        )
        assert_equal(
            self.read_bootstrap(restart_offset),
            [(714, 1001, 288, 288, 0, 0, 0, 0, 0)],
        )
        retained_blocks = [
            node.getblockhash(height) for height in range(714, 1002)
        ]
        assert_equal(
            self.read_bootstrap_record_fingerprints(restart_offset),
            [
                (
                    714,
                    1001,
                    self.projection_transaction_record_fingerprint(
                        retained_blocks
                    ),
                )
            ],
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
        self.assert_chronik_runtime_path()

        next_hash = node.generatetoaddress(1, address)[0]
        node.syncwithvalidationinterfacequeue()
        assert_equal(
            self.read_connected(restart_offset),
            [self.expected_connected(1, next_hash, 1002, 288, 288)],
        )
        assert_equal(
            self.read_record_fingerprints(restart_offset),
            self.expected_record_fingerprints(
                retained_blocks, self.read_events(restart_offset)
            ),
        )
        self.assert_chronik_runtime_path()
        self.stop_node(0)
        assert "Chronik observer stopped observations=1" in self.read_log(
            restart_offset
        )


if __name__ == "__main__":
    ChronikPrunedObserverTest().main()

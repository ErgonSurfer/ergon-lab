#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Exercise honest regtest coexistence with the exact legacy daemon."""

import os
from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SOURCE_ROOT / "test" / "functional"))

from test_framework.test_framework import BitcoinTestFramework  # noqa: E402
from test_framework.util import assert_equal, connect_nodes  # noqa: E402


NODE_ARGS = ("-connect=0", "-disablewallet")
CHAIN_SNAPSHOT_FIELDS = ("blocks", "headers", "bestblockhash", "chainwork")
UTXO_SNAPSHOT_FIELDS = (
    "height",
    "bestblock",
    "txouts",
    "bogosize",
    "total_amount",
)
UTXO_COMMITMENT_FIELDS = ("hash_serialized_2", "hash_serialized")
REINDEX_LIFECYCLES = (
    (
        "full-reindex",
        "-reindex",
        ("Reindexing block file blk00000.dat...", "Reindexing finished"),
        "ERGON_LEGACY_LIFECYCLE_OK full-reindex",
    ),
    (
        "chainstate-reindex",
        "-reindex-chainstate",
        ("Wiping LevelDB in",),
        "ERGON_LEGACY_LIFECYCLE_OK chainstate-reindex",
    ),
)


class ErgonLegacyCompatibilityTest(BitcoinTestFramework):
    def add_options(self, parser):
        parser.add_argument(
            "--legacy-bitcoind",
            required=True,
            help="Exact Bitcoin Static v24.0.5 baseline daemon",
        )

    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 2

    def setup_nodes(self):
        legacy = os.path.realpath(self.options.legacy_bitcoind)
        candidate = os.path.realpath(self.options.bitcoind)
        if legacy == candidate or os.path.samestat(os.stat(legacy), os.stat(candidate)):
            raise AssertionError("legacy and candidate daemons must be distinct files")
        self.add_nodes(
            2,
            extra_args=[
                list(NODE_ARGS),
                list(NODE_ARGS),
            ],
            binary=[legacy, candidate],
        )
        self.start_nodes()

    def node_snapshot(self, node):
        chain = node.getblockchaininfo()
        utxo = node.gettxoutsetinfo()
        commitments = [
            field
            for field in UTXO_COMMITMENT_FIELDS
            if field in utxo
        ]
        if not commitments:
            raise AssertionError("gettxoutsetinfo omitted a UTXO commitment")
        commitment = commitments[0]
        tip = chain["bestblockhash"]
        return {
            "chain": {field: chain[field] for field in CHAIN_SNAPSHOT_FIELDS},
            "utxo": {field: utxo[field] for field in UTXO_SNAPSHOT_FIELDS},
            "utxo_commitment": {
                "field": commitment,
                "value": utxo[commitment],
            },
            "raw_tip": node.getblock(tip, 0),
        }

    def assert_common_chain(self):
        legacy_snapshot = self.node_snapshot(self.nodes[0])
        assert_equal(self.node_snapshot(self.nodes[1]), legacy_snapshot)
        return legacy_snapshot

    def mine_and_compare(self, miner, blocks, address):
        self.nodes[miner].generatetoaddress(blocks, address)
        self.sync_all()
        self.assert_common_chain()

    def rebuild_and_compare(self, lifecycle, address):
        _name, argument, expected_log_markers, success_marker = lifecycle
        expected_snapshot = self.assert_common_chain()

        for node_index in (0, 1):
            with self.nodes[node_index].assert_debug_log(expected_log_markers):
                self.restart_node(
                    node_index,
                    extra_args=[*NODE_ARGS, argument],
                )

        connect_nodes(self.nodes[0], self.nodes[1])
        self.sync_all()
        assert_equal(self.assert_common_chain(), expected_snapshot)

        self.mine_and_compare(0, 1, address)
        self.mine_and_compare(1, 1, address)
        self.log.info(success_marker)

    def run_test(self):
        connect_nodes(self.nodes[0], self.nodes[1])
        address = self.nodes[0].get_deterministic_priv_key().address

        for miner in (0, 1, 0, 1):
            self.mine_and_compare(miner, 2, address)

        expected_snapshot = self.assert_common_chain()
        self.restart_node(0, extra_args=list(NODE_ARGS))
        self.restart_node(1, extra_args=list(NODE_ARGS))
        connect_nodes(self.nodes[0], self.nodes[1])
        self.sync_all()
        assert_equal(self.assert_common_chain(), expected_snapshot)

        self.mine_and_compare(0, 1, address)
        self.mine_and_compare(1, 1, address)

        for lifecycle in REINDEX_LIFECYCLES:
            self.rebuild_and_compare(lifecycle, address)


if __name__ == "__main__":
    ErgonLegacyCompatibilityTest().main()

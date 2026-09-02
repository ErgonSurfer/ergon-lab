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
                ["-connect=0", "-disablewallet"],
                ["-connect=0", "-disablewallet"],
            ],
            binary=[legacy, candidate],
        )
        self.start_nodes()

    def assert_common_chain(self):
        legacy_info = self.nodes[0].getblockchaininfo()
        candidate_info = self.nodes[1].getblockchaininfo()
        for field in ("blocks", "headers", "bestblockhash", "chainwork"):
            assert_equal(candidate_info[field], legacy_info[field])

        legacy_utxo = self.nodes[0].gettxoutsetinfo()
        candidate_utxo = self.nodes[1].gettxoutsetinfo()
        for field in ("height", "bestblock", "txouts", "bogosize", "total_amount"):
            assert_equal(candidate_utxo[field], legacy_utxo[field])
        commitments = [
            field
            for field in ("hash_serialized_2", "hash_serialized")
            if field in legacy_utxo and field in candidate_utxo
        ]
        if not commitments:
            raise AssertionError("gettxoutsetinfo omitted a shared commitment")
        assert_equal(candidate_utxo[commitments[0]], legacy_utxo[commitments[0]])

        tip = legacy_info["bestblockhash"]
        assert_equal(self.nodes[1].getblock(tip, 0), self.nodes[0].getblock(tip, 0))

    def mine_and_compare(self, miner, blocks, address):
        self.nodes[miner].generatetoaddress(blocks, address)
        self.sync_all()
        self.assert_common_chain()

    def run_test(self):
        connect_nodes(self.nodes[0], self.nodes[1])
        address = self.nodes[0].get_deterministic_priv_key().address

        for miner in (0, 1, 0, 1):
            self.mine_and_compare(miner, 2, address)

        self.restart_node(0, extra_args=["-connect=0", "-disablewallet"])
        self.restart_node(1, extra_args=["-connect=0", "-disablewallet"])
        connect_nodes(self.nodes[0], self.nodes[1])
        self.sync_all()
        self.assert_common_chain()

        self.mine_and_compare(0, 1, address)
        self.mine_and_compare(1, 1, address)


if __name__ == "__main__":
    ErgonLegacyCompatibilityTest().main()

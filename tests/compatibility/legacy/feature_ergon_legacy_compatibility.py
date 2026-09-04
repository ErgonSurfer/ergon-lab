#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Exercise honest regtest coexistence with the exact legacy daemon."""

import os
from pathlib import Path
import stat
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SOURCE_ROOT / "test" / "functional"))

from test_framework.blocktools import create_coinbase  # noqa: E402
from test_framework.messages import CBlock, ToHex  # noqa: E402
from test_framework.mininode import P2PInterface  # noqa: E402
from test_framework.script import CScript, OP_NOP, OP_RETURN  # noqa: E402
from test_framework.test_framework import BitcoinTestFramework  # noqa: E402
from test_framework.util import (  # noqa: E402
    assert_equal,
    assert_raises_rpc_error,
    connect_nodes,
    connect_nodes_bi,
    disconnect_nodes,
)


NODE_ARGS = (
    "-connect=0",
    "-disablewallet",
)
PRUNE_NODE_ARGS = (*NODE_ARGS, "-prune=1")
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
PRUNE_AFTER_HEIGHT = 1000
MIN_BLOCKS_TO_KEEP = 288
LARGE_COINBASE_SCRIPT_NOPS = 950000
LARGE_BLOCK_COUNT = 150
PHYSICAL_PRUNING_SUCCESS_MARKER = (
    "ERGON_LEGACY_LIFECYCLE_OK physical-pruning"
)
REORG_DEPTH = 3
REORG_UNPARK_MARGIN = 2
REORG_REPLACEMENT_BLOCKS = REORG_DEPTH + REORG_UNPARK_MARGIN
REORG_SUCCESS_MARKER = "ERGON_LEGACY_LIFECYCLE_OK default-protected-reorg"


def submit_branch_block(node, record, label):
    result = node.submitblock(record["raw"])
    if result == "inconclusive":
        header = node.getblockheader(record["hash"])
        assert_equal(header["hash"], record["hash"])
        assert_equal(header["confirmations"], -1)
        return
    if result not in (None, ""):
        raise AssertionError(f"{label} block was rejected: {result}")


def branch_tip_status(node, block_hash, label):
    statuses = [
        item["status"]
        for item in node.getchaintips()
        if item.get("hash") == block_hash
    ]
    if len(statuses) != 1:
        raise AssertionError(f"{label} branch tip is missing or duplicated")
    return statuses[0]


def mine_large_blocks(nodes, count, script_pub_key):
    """Submit identical template-bound near-megabyte blocks to both roles."""
    baseline, candidate = nodes
    for _ in range(count):
        template = baseline.getblocktemplate()
        previous_height = baseline.getblockcount()
        previous_hash = baseline.getbestblockhash()
        assert_equal(candidate.getblockcount(), previous_height)
        assert_equal(candidate.getbestblockhash(), previous_hash)
        assert_equal(template["height"], previous_height + 1)
        assert_equal(template["previousblockhash"], previous_hash)
        assert_equal(template["coinbasevalue"], 0)

        coinbase = create_coinbase(template["height"])
        coinbase.vout[0].nValue = int(template["coinbasevalue"])
        coinbase.vout[0].scriptPubKey = script_pub_key
        coinbase.vin[0].nSequence = 0xFFFFFFFF
        coinbase.rehash()

        block = CBlock()
        block.nVersion = template["version"]
        block.hashPrevBlock = int(template["previousblockhash"], 16)
        block.nTime = template["curtime"]
        block.nBits = int(template["bits"], 16)
        block.nNonce = 0
        block.vtx = [coinbase]
        block.hashMerkleRoot = block.calc_merkle_root()
        block.solve()
        raw_block = ToHex(block)

        proposal = {"data": raw_block, "mode": "proposal"}
        assert_equal(baseline.getblocktemplate(proposal), None)
        assert_equal(candidate.getblocktemplate(proposal), None)
        assert_equal(baseline.submitblock(raw_block), None)
        assert_equal(candidate.submitblock(raw_block), None)
        assert_equal(baseline.getbestblockhash(), candidate.getbestblockhash())


def block_file_pair(node, file_number):
    block_dir = Path(node.datadir) / "regtest" / "blocks"
    return (
        block_dir / f"blk{file_number:05d}.dat",
        block_dir / f"rev{file_number:05d}.dat",
    )


def regular_file_identity(path):
    if path.is_symlink():
        raise AssertionError(f"physical block path is a symlink: {path.name}")
    try:
        identity = path.stat()
    except FileNotFoundError as error:
        raise AssertionError(
            f"physical block path is missing: {path.name}"
        ) from error
    if not stat.S_ISREG(identity.st_mode) or identity.st_size <= 0:
        raise AssertionError(f"physical block path is not a nonempty file: {path.name}")
    return (identity.st_dev, identity.st_ino, identity.st_size)


def directory_identity(path):
    if path.is_symlink():
        raise AssertionError(f"datadir path is a symlink: {path}")
    identity = path.stat()
    if not stat.S_ISDIR(identity.st_mode):
        raise AssertionError(f"datadir path is not a directory: {path}")
    return (identity.st_dev, identity.st_ino)


def assert_file_pair_absent(paths):
    for path in paths:
        if path.exists() or path.is_symlink():
            raise AssertionError(f"pruned physical path survived: {path.name}")


class ErgonLegacyCompatibilityTest(BitcoinTestFramework):
    def add_options(self, parser):
        parser.add_argument(
            "--legacy-bitcoind",
            required=True,
            help="Exact Bitcoin Static v24.0.5 baseline daemon",
        )

    def set_test_params(self):
        self.setup_clean_chain = True
        self.num_nodes = 3

    def setup_nodes(self):
        legacy = os.path.realpath(self.options.legacy_bitcoind)
        candidate = os.path.realpath(self.options.bitcoind)
        if legacy == candidate or os.path.samestat(os.stat(legacy), os.stat(candidate)):
            raise AssertionError("legacy and candidate daemons must be distinct files")
        self.add_nodes(
            3,
            extra_args=[
                list(NODE_ARGS),
                list(NODE_ARGS),
                list(NODE_ARGS),
            ],
            binary=[legacy, candidate, legacy],
        )
        self.start_nodes()

    def setup_network(self):
        self.setup_nodes()
        connect_nodes_bi(self.nodes[0], self.nodes[1])
        self.sync_all([self.nodes[:2]])

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
        self.sync_all([self.nodes[:2]])
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

        connect_nodes_bi(self.nodes[0], self.nodes[1])
        self.sync_all([self.nodes[:2]])
        assert_equal(self.assert_common_chain(), expected_snapshot)

        self.mine_and_compare(0, 1, address)
        self.mine_and_compare(1, 1, address)
        self.log.info(success_marker)

    def advance_to_prune_boundary(self, address):
        script_pub_key = CScript(
            [OP_RETURN] + [OP_NOP] * LARGE_COINBASE_SCRIPT_NOPS
        )
        disconnect_nodes(self.nodes[0], self.nodes[1])
        self.nodes[0].add_p2p_connection(P2PInterface())
        try:
            mine_large_blocks(
                self.nodes[:2], LARGE_BLOCK_COUNT, script_pub_key
            )
        finally:
            self.nodes[0].disconnect_p2ps()
        connect_nodes_bi(self.nodes[0], self.nodes[1])
        self.assert_common_chain()

        remaining = PRUNE_AFTER_HEIGHT + 1 - self.nodes[0].getblockcount()
        if remaining <= 1:
            raise AssertionError("large-block phase left no cross-mining budget")
        baseline_blocks = (remaining + 1) // 2
        self.mine_and_compare(0, baseline_blocks, address)
        self.mine_and_compare(1, remaining - baseline_blocks, address)
        assert_equal(self.nodes[0].getblockcount(), PRUNE_AFTER_HEIGHT + 1)

    def enable_pruning(self):
        expected_snapshot = self.assert_common_chain()
        datadir_identities = [
            directory_identity(Path(node.datadir)) for node in self.nodes[:2]
        ]
        self.restart_node(0, extra_args=list(PRUNE_NODE_ARGS))
        self.restart_node(1, extra_args=list(PRUNE_NODE_ARGS))
        connect_nodes_bi(self.nodes[0], self.nodes[1])
        self.sync_all([self.nodes[:2]])
        assert_equal(self.assert_common_chain(), expected_snapshot)

        for node_index, node in enumerate(self.nodes[:2]):
            assert_equal(
                directory_identity(Path(node.datadir)),
                datadir_identities[node_index],
            )
            chain = node.getblockchaininfo()
            assert_equal(chain["pruned"], True)
            assert_equal(chain["automatic_pruning"], False)
            assert_equal(chain["pruneheight"], 0)

    def physical_prune_and_compare(self, address):
        expected_snapshot = self.assert_common_chain()
        tip_height = expected_snapshot["chain"]["blocks"]
        assert_equal(tip_height, PRUNE_AFTER_HEIGHT + 1)
        expected_prune_height = tip_height - MIN_BLOCKS_TO_KEEP

        old_hash = self.nodes[0].getblockhash(1)
        old_raw_block = self.nodes[0].getblock(old_hash, 0)
        old_header = self.nodes[0].getblockheader(old_hash)
        physical_pairs = []
        pre_prune_usage = []
        old_pair_identities = []
        datadir_identities = [
            directory_identity(Path(node.datadir)) for node in self.nodes[:2]
        ]

        for node in self.nodes[:2]:
            assert_equal(node.getblockhash(1), old_hash)
            assert_equal(node.getblock(old_hash, 0), old_raw_block)
            assert_equal(node.getblockheader(old_hash), old_header)
            chain = node.getblockchaininfo()
            assert_equal(chain["pruned"], True)
            assert_equal(chain["automatic_pruning"], False)
            assert_equal(chain["pruneheight"], 0)
            pre_prune_usage.append(chain["size_on_disk"])

            old_pair = block_file_pair(node, 0)
            retained_pair = block_file_pair(node, 1)
            old_pair_identities.append(
                tuple(regular_file_identity(path) for path in old_pair)
            )
            for path in retained_pair:
                regular_file_identity(path)
            physical_pairs.append((old_pair, retained_pair))

        for left, right in zip(old_pair_identities[0], old_pair_identities[1]):
            if left[:2] == right[:2]:
                raise AssertionError("legacy and candidate block files share an inode")

        log_markers = (
            "Prune: UnlinkPrunedFiles deleted blk/rev (00000)",
            (
                "Prune (Manual): prune_height="
                f"{expected_prune_height} removed "
            ),
        )
        prune_heights = []
        first_available_blocks = []
        for node_index, node in enumerate(self.nodes[:2]):
            with node.assert_debug_log(log_markers):
                assert_equal(
                    node.pruneblockchain(tip_height), expected_prune_height
                )
            old_pair, retained_pair = physical_pairs[node_index]
            assert_file_pair_absent(old_pair)
            for path in retained_pair:
                regular_file_identity(path)

            chain = node.getblockchaininfo()
            assert_equal(chain["pruned"], True)
            assert_equal(chain["automatic_pruning"], False)
            prune_height = chain["pruneheight"]
            if not 1 < prune_height <= expected_prune_height:
                raise AssertionError("pruneheight escaped the retained range")
            prune_heights.append(prune_height)
            first_available_blocks.append(
                node.getblock(node.getblockhash(prune_height), 0)
            )
            if chain["size_on_disk"] >= pre_prune_usage[node_index]:
                raise AssertionError("reported block storage did not shrink")
            assert_equal(node.getblockheader(old_hash), old_header)
            assert_raises_rpc_error(
                -1,
                "Block not available (pruned data)",
                node.getblock,
                old_hash,
                0,
            )

        assert_equal(prune_heights[1], prune_heights[0])
        assert_equal(first_available_blocks[1], first_available_blocks[0])
        durable_prune_height = prune_heights[0]

        assert_equal(self.assert_common_chain(), expected_snapshot)

        self.restart_node(0, extra_args=list(PRUNE_NODE_ARGS))
        self.restart_node(1, extra_args=list(PRUNE_NODE_ARGS))
        connect_nodes_bi(self.nodes[0], self.nodes[1])
        self.sync_all([self.nodes[:2]])
        assert_equal(self.assert_common_chain(), expected_snapshot)
        for node_index, node in enumerate(self.nodes[:2]):
            assert_equal(
                directory_identity(Path(node.datadir)),
                datadir_identities[node_index],
            )
            old_pair, retained_pair = physical_pairs[node_index]
            assert_file_pair_absent(old_pair)
            for path in retained_pair:
                regular_file_identity(path)
            chain = node.getblockchaininfo()
            assert_equal(chain["pruned"], True)
            assert_equal(chain["automatic_pruning"], False)
            assert_equal(chain["pruneheight"], durable_prune_height)
            assert_equal(
                node.getblock(node.getblockhash(durable_prune_height), 0),
                first_available_blocks[node_index],
            )
            assert_equal(node.getblockheader(old_hash), old_header)
            assert_raises_rpc_error(
                -1,
                "Block not available (pruned data)",
                node.getblock,
                old_hash,
                0,
            )

        self.mine_and_compare(0, 1, address)
        self.mine_and_compare(1, 1, address)
        self.log.info(PHYSICAL_PRUNING_SUCCESS_MARKER)

    def generate_reorg_bundle(self, incumbent_address, replacement_address):
        common_snapshot = self.assert_common_chain()
        common_height = common_snapshot["chain"]["blocks"]
        common_hash = common_snapshot["chain"]["bestblockhash"]
        generator = self.nodes[2]
        connect_nodes(self.nodes[0], generator)
        self.sync_all([[self.nodes[0], generator]])
        assert_equal(self.node_snapshot(generator), common_snapshot)
        disconnect_nodes(self.nodes[0], generator)

        incumbent_hashes = generator.generatetoaddress(
            REORG_DEPTH, incumbent_address
        )
        incumbent = [
            {"hash": block_hash, "raw": generator.getblock(block_hash, 0)}
            for block_hash in incumbent_hashes
        ]
        incumbent_chainwork = generator.getblockheader(
            incumbent[-1]["hash"]
        )["chainwork"]
        assert_equal(generator.invalidateblock(incumbent[0]["hash"]), None)
        assert_equal(generator.getblockcount(), common_height)
        assert_equal(generator.getbestblockhash(), common_hash)

        replacement_hashes = generator.generatetoaddress(
            REORG_REPLACEMENT_BLOCKS, replacement_address
        )
        replacement = [
            {"hash": block_hash, "raw": generator.getblock(block_hash, 0)}
            for block_hash in replacement_hashes
        ]
        if incumbent[-1]["hash"] == replacement[-1]["hash"]:
            raise AssertionError("competing branches have the same tip")
        if int(
            generator.getblockheader(replacement[-1]["hash"])["chainwork"], 16
        ) <= int(incumbent_chainwork, 16):
            raise AssertionError("generated replacement does not have more work")
        self.stop_node(2)
        return incumbent, replacement

    def default_protected_reorg_and_compare(self, address):
        common_snapshot = self.assert_common_chain()
        common_height = common_snapshot["chain"]["blocks"]
        replacement_address = self.nodes[1].get_deterministic_priv_key().address
        if replacement_address == address:
            raise AssertionError("competing branches require distinct addresses")
        incumbent, replacement = self.generate_reorg_bundle(
            address, replacement_address
        )

        disconnect_nodes(self.nodes[0], self.nodes[1])
        for offset, record in enumerate(incumbent, start=1):
            proposal = {"data": record["raw"], "mode": "proposal"}
            for node in self.nodes[:2]:
                assert_equal(node.getblocktemplate(proposal), None)
                submit_branch_block(node, record, "incumbent")
                assert_equal(node.getblockcount(), common_height + offset)
                assert_equal(node.getbestblockhash(), record["hash"])
        incumbent_tip = incumbent[-1]["hash"]
        incumbent_chainwork = [
            node.getblockheader(incumbent_tip)["chainwork"]
            for node in self.nodes[:2]
        ]
        assert_equal(incumbent_chainwork[1], incumbent_chainwork[0])
        self.assert_common_chain()

        for offset, record in enumerate(replacement, start=1):
            for node in self.nodes[:2]:
                submit_branch_block(node, record, "replacement")
            final = offset == REORG_REPLACEMENT_BLOCKS
            expected_height = (
                common_height + REORG_REPLACEMENT_BLOCKS
                if final else common_height + REORG_DEPTH
            )
            expected_tip = record["hash"] if final else incumbent_tip
            for node in self.nodes[:2]:
                assert_equal(node.getblockcount(), expected_height)
                assert_equal(node.getbestblockhash(), expected_tip)

            if offset == REORG_DEPTH:
                for node_index, node in enumerate(self.nodes[:2]):
                    assert_equal(
                        node.getblockheader(record["hash"])["chainwork"],
                        incumbent_chainwork[node_index],
                    )
                    assert_equal(
                        branch_tip_status(node, record["hash"], "equal-work"),
                        "parked",
                    )
            elif offset == REORG_DEPTH + 1:
                for node_index, node in enumerate(self.nodes[:2]):
                    if int(
                        node.getblockheader(record["hash"])["chainwork"], 16
                    ) <= int(incumbent_chainwork[node_index], 16):
                        raise AssertionError(
                            "replacement did not lead incumbent chainwork"
                        )
                    assert_equal(
                        branch_tip_status(node, record["hash"], "one-block-lead"),
                        "parked",
                    )

        for node in self.nodes[:2]:
            assert_equal(
                branch_tip_status(node, incumbent_tip, "incumbent"),
                "valid-fork",
            )
        assert_equal(
            self.assert_common_chain()["chain"]["blocks"],
            common_height + REORG_REPLACEMENT_BLOCKS,
        )

        connect_nodes_bi(self.nodes[0], self.nodes[1])
        self.sync_all([self.nodes[:2]])
        self.assert_common_chain()
        self.mine_and_compare(0, 1, address)
        self.mine_and_compare(1, 1, address)
        self.log.info(REORG_SUCCESS_MARKER)

    def run_test(self):
        connect_nodes_bi(self.nodes[0], self.nodes[1])
        address = self.nodes[0].get_deterministic_priv_key().address

        for miner in (0, 1, 0, 1):
            self.mine_and_compare(miner, 2, address)

        expected_snapshot = self.assert_common_chain()
        self.restart_node(0, extra_args=list(NODE_ARGS))
        self.restart_node(1, extra_args=list(NODE_ARGS))
        connect_nodes_bi(self.nodes[0], self.nodes[1])
        self.sync_all([self.nodes[:2]])
        assert_equal(self.assert_common_chain(), expected_snapshot)

        self.mine_and_compare(0, 1, address)
        self.mine_and_compare(1, 1, address)

        for lifecycle in REINDEX_LIFECYCLES:
            self.rebuild_and_compare(lifecycle, address)

        self.default_protected_reorg_and_compare(address)
        self.enable_pruning()
        self.advance_to_prune_boundary(address)
        self.physical_prune_and_compare(address)


if __name__ == "__main__":
    ErgonLegacyCompatibilityTest().main()

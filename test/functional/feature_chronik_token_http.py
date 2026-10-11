#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Serve confirmed ALP metadata and canonical transactions on local regtest."""

from http.client import HTTPConnection

from feature_chronik_block_observer import ChronikBlockObserverTest
from test_framework.blocktools import create_block, create_coinbase
from test_framework.messages import COutPoint, CTransaction, CTxIn, CTxOut, ToHex
from test_framework.script import CScript, OP_RESERVED, OP_RETURN, OP_TRUE
from test_framework.txtools import pad_tx
from test_framework.util import PORT_MIN, PORT_RANGE, PortSeed, assert_equal, wait_until


PROTOBUF_CONTENT_TYPE = "application/x-protobuf"


def chronik_port():
    return PORT_MIN + 2 * PORT_RANGE + 2000 + (PortSeed.n % 2000)


def read_varint(payload, offset):
    value = 0
    shift = 0
    while offset < len(payload):
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
        if shift >= 70:
            break
    raise AssertionError("invalid protobuf varint")


def parse_protobuf(payload):
    fields = {}
    offset = 0
    while offset < len(payload):
        tag, offset = read_varint(payload, offset)
        field = tag >> 3
        wire_type = tag & 7
        if field == 0:
            raise AssertionError("invalid protobuf field zero")
        if wire_type == 0:
            value, offset = read_varint(payload, offset)
        elif wire_type == 2:
            size, offset = read_varint(payload, offset)
            end = offset + size
            if end > len(payload):
                raise AssertionError("truncated protobuf field")
            value = payload[offset:end]
            offset = end
        else:
            raise AssertionError(f"unexpected protobuf wire type {wire_type}")
        fields.setdefault(field, []).append((wire_type, value))
    return fields


class ChronikTokenHttpTest(ChronikBlockObserverTest):
    def set_test_params(self):
        super().set_test_params()

    @staticmethod
    def alp_genesis_script():
        ticker = b"LAB"
        name = b"Ergon Lab Token"
        url = b"https://ergon.network"
        data = b"confirmed-metadata"
        section = (
            b"SLP2"
            + b"\x00"
            + b"\x07GENESIS"
            + bytes([len(ticker)])
            + ticker
            + bytes([len(name)])
            + name
            + bytes([len(url)])
            + url
            + bytes([len(data)])
            + data
            + b"\x00"  # no auth pubkey
            + b"\x02"  # decimals
            + b"\x00"  # no minted amounts
            + b"\x00"  # no mint batons
        )
        return CScript([OP_RETURN, OP_RESERVED, section])

    def request(self, path):
        connection = HTTPConnection("127.0.0.1", self.chronik_port, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        body = response.read()
        content_type = response.getheader("Content-Type")
        status = response.status
        connection.close()
        assert_equal(content_type, PROTOBUF_CONTENT_TYPE)
        return status, body

    def mine_genesis(self):
        node = self.nodes[0]
        height = node.getblockcount() + 1
        previous_hash = node.getbestblockhash()
        previous_time = node.getblockheader(previous_hash)["time"]
        coinbase = create_coinbase(height)
        coinbase.vout[0].nValue = 0
        coinbase.vout[0].scriptPubKey = self.alp_genesis_script()
        self.genesis_coinbase = coinbase
        token_id = coinbase.rehash()
        block = create_block(int(previous_hash, 16), coinbase, previous_time + 1)
        block.solve()
        assert_equal(node.submitblock(ToHex(block)), None)
        node.syncwithvalidationinterfacequeue()
        return block.hash, token_id, block.nTime

    def assert_token_info(self, token_id, expected_timestamp):
        status, body = self.request(f"/token/{token_id}")
        assert_equal(status, 200)
        token_info = parse_protobuf(body)
        assert_equal(token_info[1][0][1].decode(), token_id)

        genesis_info = parse_protobuf(token_info[3][0][1])
        assert_equal(genesis_info[1][0][1], b"LAB")
        assert_equal(genesis_info[2][0][1], b"Ergon Lab Token")
        assert_equal(genesis_info[3][0][1], b"https://ergon.network")
        assert_equal(genesis_info[6][0][1], b"confirmed-metadata")
        assert_equal(genesis_info[8][0][1], 2)

        block = parse_protobuf(token_info[4][0][1])
        assert_equal(block[1][0][1], 1)
        assert_equal(block[3][0][1], expected_timestamp)

    def assert_token_missing(self, token_id):
        status, body = self.request(f"/token/{token_id}")
        assert_equal(status, 404)
        error = parse_protobuf(body)[2][0][1].decode()
        assert_equal(error, f"404: Token {token_id} not found in the index")

    def assert_confirmed_tx(self, txid, block_hash, timestamp):
        result = None

        def query_ready():
            nonlocal result
            result = self.request(f"/tx/{txid}")
            assert result[0] in (200, 503)
            return result[0] == 200

        wait_until(query_ready)
        tx = parse_protobuf(result[1])
        assert_equal(tx[1][0][1], bytes.fromhex(txid)[::-1])
        assert_equal(tx[11][0][1], len(bytes.fromhex(ToHex(self.genesis_coinbase))))
        assert_equal(tx[12][0][1], 1)
        block = parse_protobuf(tx[8][0][1])
        assert_equal(block[1][0][1], 1)
        assert_equal(block[2][0][1], bytes.fromhex(block_hash)[::-1])
        assert_equal(block[3][0][1], timestamp)
        output = parse_protobuf(tx[4][0][1])
        assert_equal(output[2][0][1], bytes(self.alp_genesis_script()))
        entry = parse_protobuf(tx[13][0][1])
        assert_equal(entry[1][0][1].decode(), txid)
        # Confirmed-only route does not pretend to expose spend/finality state.
        assert 4 not in output  # spent_by
        assert 16 not in tx  # is_final defaults false
        return result[1]

    def assert_tx_missing(self, txid):
        status, body = self.request(f"/tx/{txid}")
        assert_equal(status, 404)
        assert_equal(parse_protobuf(body)[2][0][1].decode(),
                     f"404: Transaction {txid} not found in the confirmed index")

    def exercise_spent_coin_read(self):
        node = self.nodes[0]
        height = node.getblockcount() + 1
        previous = node.getbestblockhash()
        funding = create_coinbase(height)
        funding.vout[0].nValue = 0
        funding.vout[0].scriptPubKey = CScript([OP_TRUE])
        funding.rehash()
        block = create_block(int(previous, 16), funding, node.getblockheader(previous)["time"] + 1)
        block.solve()
        assert_equal(node.submitblock(ToHex(block)), None)
        node.generatetoaddress(100, node.get_deterministic_priv_key().address)

        spend = CTransaction()
        spend.vin = [CTxIn(COutPoint(funding.sha256, 0))]
        spend.vout = [CTxOut(0, CScript([OP_TRUE]))]
        pad_tx(spend)
        spend.rehash()
        node.syncwithvalidationinterfacequeue()
        wait_until(lambda: self.request(f"/tx/{funding.hash}")[0] == 200)
        self.assert_tx_missing(spend.hash)

        previous = node.getbestblockhash()
        coinbase = create_coinbase(node.getblockcount() + 1)
        coinbase.vout[0].nValue = 0
        coinbase.rehash()
        block = create_block(int(previous, 16), coinbase, node.getblockheader(previous)["time"] + 1)
        block.vtx.append(spend)
        block.hashMerkleRoot = block.calc_merkle_root()
        block.solve()
        assert_equal(node.submitblock(ToHex(block)), None)
        node.syncwithvalidationinterfacequeue()
        wait_until(lambda: self.request(f"/tx/{spend.hash}")[0] == 200)
        status, body = self.request(f"/tx/{spend.hash}")
        assert_equal(status, 200)
        tx = parse_protobuf(body)
        assert_equal(tx[1][0][1], bytes.fromhex(spend.hash)[::-1])
        assert 12 not in tx  # non-coinbase
        source = parse_protobuf(tx[3][0][1])
        assert_equal(parse_protobuf(source[1][0][1])[1][0][1], bytes.fromhex(funding.hash)[::-1])
        assert_equal(source[3][0][1], bytes(CScript([OP_TRUE])))
        assert 4 not in source  # zero previous amount, protobuf default
        assert_equal(parse_protobuf(tx[4][0][1])[2][0][1], bytes(CScript([OP_TRUE])))
        node.invalidateblock(block.hash)
        node.syncwithvalidationinterfacequeue()
        wait_until(lambda: self.request(f"/tx/{spend.hash}")[0] == 404)
        self.assert_tx_missing(spend.hash)

    def run_test(self):
        self.chronik_port = chronik_port()
        node = self.nodes[0]
        self.stop_node(0)

        node.assert_start_raises_init_error(
            [f"-chronikbind=127.0.0.1:{self.chronik_port}"],
            "Error: -chronikbind requires -chronikobserver on local regtest",
        )

        args = [
            "-connect=0",
            "-disablewallet",
            "-chronikobserver",
            f"-chronikbind=127.0.0.1:{self.chronik_port}",
        ]
        self.start_node(0, extra_args=args)
        wait_until(
            lambda: "Chronik token service started" in self.read_log(),
        )

        block_hash, token_id, timestamp = self.mine_genesis()
        self.assert_token_info(token_id, timestamp)
        canonical_tx = self.assert_confirmed_tx(token_id, block_hash, timestamp)

        missing = "99" * 32
        self.assert_token_missing(missing)
        self.assert_tx_missing(missing)
        status, body = self.request("/tx/not-a-txid")
        assert_equal(status, 400)
        assert_equal(parse_protobuf(body)[2][0][1].decode(), "400: Not a txid: not-a-txid")
        status, body = self.request("/token/not-a-txid")
        assert_equal(status, 400)
        assert_equal(
            parse_protobuf(body)[2][0][1].decode(),
            "400: Not a txid: not-a-txid",
        )

        node.invalidateblock(block_hash)
        node.syncwithvalidationinterfacequeue()
        self.assert_token_missing(token_id)
        wait_until(lambda: self.request(f"/tx/{token_id}")[0] == 404)
        self.assert_tx_missing(token_id)

        node.reconsiderblock(block_hash)
        node.syncwithvalidationinterfacequeue()
        self.assert_token_info(token_id, timestamp)
        assert_equal(self.assert_confirmed_tx(token_id, block_hash, timestamp), canonical_tx)

        self.restart_node(0, extra_args=args)
        self.assert_token_info(token_id, timestamp)
        assert_equal(self.assert_confirmed_tx(token_id, block_hash, timestamp), canonical_tx)

        self.restart_node(0, extra_args=args + ["-reindex=1"])
        node.syncwithvalidationinterfacequeue()
        assert_equal(self.assert_confirmed_tx(token_id, block_hash, timestamp), canonical_tx)

        self.restart_node(0, extra_args=args + ["-reindex-chainstate=1"])
        node.syncwithvalidationinterfacequeue()
        assert_equal(self.assert_confirmed_tx(token_id, block_hash, timestamp), canonical_tx)
        self.exercise_spent_coin_read()


if __name__ == "__main__":
    ChronikTokenHttpTest().main()

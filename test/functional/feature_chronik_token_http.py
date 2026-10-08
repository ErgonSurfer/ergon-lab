#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Serve confirmed ALP metadata through the upstream Chronik token route."""

from http.client import HTTPConnection

from feature_chronik_block_observer import ChronikBlockObserverTest
from test_framework.blocktools import create_block, create_coinbase
from test_framework.messages import ToHex
from test_framework.script import CScript, OP_RESERVED, OP_RETURN
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

        missing = "99" * 32
        self.assert_token_missing(missing)
        status, body = self.request("/token/not-a-txid")
        assert_equal(status, 400)
        assert_equal(
            parse_protobuf(body)[2][0][1].decode(),
            "400: Not a txid: not-a-txid",
        )

        node.invalidateblock(block_hash)
        node.syncwithvalidationinterfacequeue()
        self.assert_token_missing(token_id)

        node.reconsiderblock(block_hash)
        node.syncwithvalidationinterfacequeue()
        self.assert_token_info(token_id, timestamp)

        self.restart_node(0, extra_args=args)
        self.assert_token_info(token_id, timestamp)


if __name__ == "__main__":
    ChronikTokenHttpTest().main()

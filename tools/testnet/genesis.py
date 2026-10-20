#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Offline deterministic development-genesis construction; never contacts a node.

This is a genesis utility, not a mining benchmark or a network launcher.
"""
import argparse
import hashlib
import json
import struct

TAG = b"Ergon development testnet v1 - 2026-10-20 - no mainnet value"
TIMESTAMP = 1792454400
BITS = 0x1E00FFFF


def hash256(data):
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def push(data):
    if len(data) >= 76:
        raise ValueError("genesis fixture only supports short pushes")
    return bytes([len(data)]) + data


def coinbase():
    # CScript << 486604799 << CScriptNum(4) << timestamp.
    script = push(bytes.fromhex("ffff001d")) + push(b"\x04") + push(TAG)
    output = b"\x6a" + push(b"ergon-testnet-v1")
    return (struct.pack("<i", 1) + b"\x01" + bytes(32) + b"\xff" * 4
            + bytes([len(script)]) + script + b"\xff" * 4 + b"\x01"
            + struct.pack("<q", 0) + bytes([len(output)]) + output + bytes(4))


def target(bits):
    size = bits >> 24
    word = bits & 0x007FFFFF
    if bits & 0x00800000 or word == 0:
        raise ValueError("invalid target")
    value = word >> (8 * (3 - size)) if size <= 3 else word << (8 * (size - 3))
    if not 0 < value < 2**256:
        raise ValueError("target overflow")
    return value


def header(nonce):
    return (struct.pack("<i", 1) + bytes(32) + hash256(coinbase())
            + struct.pack("<III", TIMESTAMP, BITS, nonce))


def describe(nonce):
    raw = header(nonce)
    digest = hash256(raw)
    return {
        "network": "ergon-testnet-v1", "timestamp": TIMESTAMP,
        "bits": f"{BITS:08x}", "nonce": nonce,
        "hash": digest[::-1].hex(), "merkle_root": hash256(coinbase())[::-1].hex(),
        "header_hex": raw.hex(), "coinbase_hex": coinbase().hex(),
        "valid_pow": int.from_bytes(digest, "little") <= target(BITS),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nonce", type=int)
    parser.add_argument("--search", action="store_true")
    args = parser.parse_args()
    if args.search == (args.nonce is not None):
        parser.error("choose --search or --nonce")
    nonce = args.nonce
    if args.search:
        bound = target(BITS)
        prefix = header(0)[:76]
        nonce = next(n for n in range(2**32)
                     if int.from_bytes(hash256(prefix + struct.pack("<I", n)), "little") <= bound)
    if not 0 <= nonce < 2**32:
        parser.error("nonce out of range")
    print(json.dumps(describe(nonce), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

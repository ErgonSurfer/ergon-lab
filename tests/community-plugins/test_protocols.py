# SPDX-License-Identifier: MIT
"""Synthetic plugin projections using the actual upstream Python data API.

Not embedded-CPython dispatch, token validation, database, HTTP or node evidence.
No wallet, operator configuration, real address or chain input is used here.
"""
import argparse
import importlib.util
from pathlib import Path
import sys
import types
import unittest


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def configure(upstream, plugins):
    global script, api, token_api, tx_api, token_plugin, nft, market, claims
    package = types.ModuleType("chronik_plugin")
    package.__path__ = []
    sys.modules["chronik_plugin"] = package
    source = upstream / "chronik/chronik-plugin-impl"
    script = load("chronik_plugin.script", source / "python/upstream/script.py")
    api = load("chronik_plugin.plugin", source / "src/plugin.py")
    token_api = load("chronik_plugin.etoken", source / "src/etoken.py")
    tx_api = load("chronik_plugin.tx", source / "src/tx.py")
    instances = []
    for name, klass in (
        ("moom_alp_token_index", "MoomAlpTokenIndexPlugin"),
        ("moom_nft_index", "MoomNftIndexPlugin"),
        ("moom_alp_exch_market", "MoomAlpExchMarketPlugin"),
        ("moom_reward_claim_index", "MoomRewardClaimIndexPlugin"),
    ):
        module = load("reviewed_" + name, plugins / (name + ".py"))
        instances.append(getattr(module, klass)({}))
    token_plugin, nft, market, claims = instances


TOKEN_ID = "11" * 32
COLLECTION_ID = "22" * 32


def genesis(url=b"https://example.invalid/synthetic", decimals=2):
    return token_api.GenesisInfo(b"TEST", b"Synthetic token", None, url,
                                 None, None, None, decimals)


def entry(protocol="ALP", token_type=0, **kwargs):
    return token_api.TokenTxEntry(TOKEN_ID, protocol, token_type, **kwargs)


def token(protocol="ALP", token_type=0, atoms=1, baton=False, entry_idx=0):
    return token_api.Token(TOKEN_ID, protocol, token_type, entry_idx, atoms, baton)


def output(ops=(), value=None):
    return tx_api.TxOutput(script.CScript(ops), 0, value)


def transaction(outputs=(), entries=()):
    return tx_api.Tx(bytes(32), 2, [], list(outputs), 0, list(entries), [])


def sell():
    return [script.OP_RETURN, b"EXCH", b"\x01", b"SELL", bytes([0x7c]) * 32,
            bytes(4), (7).to_bytes(8, "little"), (9).to_bytes(8, "little"),
            bytes([3]) * 20, bytes([4]) * 20]


def bid():
    return [script.OP_RETURN, b"EXCH", b"\x01", b"PBID", bytes([1]) * 32,
            bytes([2]) * 32, bytes([3]) * 8, bytes([4]) * 8, bytes([5]) * 32,
            bytes([2]) * 33, bytes([3]) * 33, bytes([6]) * 4]


def accept():
    widths = [32, 32, 4, 32, 4, 32, 8, 8, 32, 33, 33, 33, 4]
    return [script.OP_RETURN, b"EXCH", b"\x01", b"PACC",
            *(bytes([i + 1]) * width for i, width in enumerate(widths))]


def carrier(ciphertext=1, kind=1):
    return b"MCLM" + bytes([0, kind]) + bytes(16 + 16 + 12) + bytes([ciphertext]) \
        + bytes([0x7c]) * ciphertext + bytes(16)


class Protocols(unittest.TestCase):
    def test_01_identifiers_and_versions(self):
        self.assertEqual(token_plugin.lokad_id(), b"SLP2")
        self.assertEqual(nft.lokad_id(), [b"SLP\x00", b"SLP2"])
        self.assertEqual(market.lokad_id(), b"EXCH")
        self.assertEqual(claims.lokad_id(), b"MCLM")
        self.assertEqual([p.version() for p in (token_plugin, nft, market, claims)],
                         ["0.1.0", "0.2.0", "0.2.1", "0.1.1"])

    def test_02_empty_transactions(self):
        for plugin in (token_plugin, nft, market, claims):
            self.assertEqual(plugin.run(transaction()), [])

    def test_03_alp_exact_output(self):
        tx = transaction([output([script.OP_TRUE], token(atoms=123))])
        self.assertEqual(token_plugin.run(tx), [api.PluginOutput(0,
            [b"ALPT" + bytes.fromhex(TOKEN_ID),
             b"ALPH" + bytes.fromhex(TOKEN_ID) + b"\x51"],
            [b"atoms=123", b"type=0"])])

    def test_04_alp_genesis_metadata(self):
        tx = transaction([output(value=token())], [entry(genesis_info=genesis())])
        result = token_plugin.run(tx)[0]
        self.assertEqual(result.groups[-1], b"ALPG" + bytes.fromhex(TOKEN_ID))
        self.assertEqual(result.data[2:], [b"ticker=TEST", b"name=Synthetic token",
            b"url=https://example.invalid/synthetic", b"decimals=2"])

    def test_05_alp_ignores_other_protocol(self):
        self.assertEqual(token_plugin.run(transaction([output(value=token("SLP", 1))])), [])

    def test_06_alp_ignores_zero_and_baton(self):
        for value in (token(atoms=0), token(baton=True)):
            self.assertEqual(token_plugin.run(transaction([output(value=value)])), [])

    def test_07_alp_invalid_genesis_not_projected_as_metadata(self):
        tx = transaction([output(value=token())], [entry(genesis_info=genesis(), is_invalid=True)])
        self.assertEqual(len(token_plugin.run(tx)[0].groups), 2)

    def test_08_holder_groups_are_scripts_not_people(self):
        tx = transaction([output([script.OP_TRUE], token()), output([script.OP_FALSE], token())])
        groups = [row.groups[1] for row in token_plugin.run(tx)]
        self.assertNotEqual(groups[0], groups[1])
        self.assertEqual([row.idx for row in token_plugin.run(tx)], [0, 1])

    def test_09_nft_alp_candidate_is_not_supply_proof(self):
        tx = transaction([output(value=token())], [entry()])
        self.assertEqual(nft.run(tx)[0].data, [b"protocol=ALP", b"type=0", b"profile_asset=0"])

    def test_10_nft_preserves_profile_marker(self):
        tx = transaction([output(value=token())],
                         [entry(genesis_info=genesis(b"moom://asset-profile/synthetic"))])
        self.assertEqual(nft.run(tx)[0].data[2], b"profile_asset=1")

    def test_11_nft_slp_child_and_collection(self):
        tx = transaction([output(value=token("SLP", 0x41))],
                         [entry("SLP", 0x41, group_token_id=COLLECTION_ID)])
        result = nft.run(tx)[0]
        self.assertEqual(result.groups[-1], b"NFTC" + bytes.fromhex(COLLECTION_ID))
        self.assertIn(b"collection=" + COLLECTION_ID.encode(), result.data)

    def test_12_nft_slp_group_preserved(self):
        tx = transaction([output(value=token("SLP", 0x81))], [entry("SLP", 0x81)])
        self.assertEqual(nft.run(tx)[0].data[:2], [b"protocol=SLP", b"type=129"])

    def test_13_nft_slp_fungible_excluded(self):
        for kind in (1, 2):
            tx = transaction([output(value=token("SLP", kind))], [entry("SLP", kind)])
            self.assertEqual(nft.run(tx), [])

    def test_14_nft_bad_entry_excluded(self):
        for entries in ([], [entry(is_invalid=True)]):
            self.assertEqual(nft.run(transaction([output(value=token())], entries)), [])

    def test_15_nft_amount_and_baton_excluded(self):
        for value in (token(atoms=0), token(atoms=2), token(baton=True)):
            self.assertEqual(nft.run(transaction([output(value=value)], [entry()])), [])

    def test_16_market_sell_exact_binary_projection(self):
        fields = sell()
        result = market.run(transaction([output(fields), output()]))
        self.assertEqual(result, [api.PluginOutput(1, [b"EXCH\x01"], [b"|".join(fields[4:])])])
        self.assertIn(b"|" * 32, result[0].data[0])

    def test_17_market_bid_exact_projection(self):
        fields = bid()
        self.assertEqual(market.run(transaction([output(fields), output()])),
            [api.PluginOutput(1, [b"EXCH\x01PBID", b"EXCT" + fields[5]],
             [b"|".join([b"PBID", *fields[4:9], fields[11]])])])

    def test_18_market_accept_exact_projection(self):
        fields = accept()
        self.assertEqual(market.run(transaction([output(fields), output()])),
            [api.PluginOutput(1, [b"EXCH\x01PACC", b"EXCT" + fields[9]],
             [b"|".join([b"PACC", *fields[4:13], fields[16]])])])

    def test_19_market_wrong_widths(self):
        for factory in (sell, bid, accept):
            for i in range(4, len(factory())):
                parts = factory()
                parts[i] = parts[i][:-1]
                self.assertEqual(market.run(transaction([output(parts), output()])), [])

    def test_20_market_nonpush_fields_are_refused(self):
        for factory in (sell, bid, accept):
            for i in range(4, len(factory())):
                parts = factory()
                parts[i] = script.OP_TRUE
                self.assertEqual(market.run(transaction([output(parts), output()])), [])

    def test_21_market_truncated_scripts_are_refused(self):
        for raw in (b"\x6a\x04EXCH\x4c", bytes(output(sell()).script) + b"\x4d"):
            self.assertEqual(market.run(transaction([tx_api.TxOutput(script.CScript(raw), 0, None)])), [])

    def test_22_market_missing_fields(self):
        for factory in (sell, bid, accept):
            parts = factory()
            self.assertEqual(market.run(transaction([output(parts[:-1]), output()])), [])

    def test_23_market_header_refusals(self):
        for idx, value in ((0, script.OP_TRUE), (1, b"OTHER"), (2, b"\x02"), (3, b"OTHER")):
            parts = sell()
            parts[idx] = value
            self.assertEqual(market.run(transaction([output(parts), output()])), [])

    def test_24_market_only_first_output_is_carrier(self):
        self.assertEqual(market.run(transaction([output(), output(sell()), output()])), [])

    def test_25_market_sell_requires_output_one(self):
        self.assertEqual(market.run(transaction([output(sell())])), [])

    def test_26_market_bid_single_output_legacy_behavior(self):
        for factory in (bid, accept):
            self.assertEqual(market.run(transaction([output(factory())]))[0].idx, 0)

    def test_27_market_trailing_fields_contract(self):
        self.assertEqual(len(market.run(transaction([output(sell() + [b"extension"]), output()]))), 1)
        for factory in (bid, accept):
            self.assertEqual(market.run(transaction([output(factory() + [b"extension"]), output()])), [])

    def test_28_market_does_not_validate_prices_or_covenants(self):
        parts = sell()
        parts[6], parts[7] = bytes(8), bytes(8)
        self.assertEqual(len(market.run(transaction([output(parts), output()]))), 1)

    def test_29_claim_exact_metadata(self):
        self.assertEqual(claims.run(transaction([output([script.OP_RETURN, b"MCLM", carrier()])])),
            [api.PluginOutput(0, [b"MCLM\x00"], [b"version=0", b"kind=1"])])

    def test_30_claim_single_push(self):
        self.assertEqual(len(claims.run(transaction([output([script.OP_RETURN, carrier()])]))), 1)

    def test_31_claim_pushdata1(self):
        payload = carrier(80)
        raw = b"\x6a\x4c" + bytes([len(payload)]) + payload
        self.assertEqual(len(claims.run(transaction([output(script.CScript(raw))]))), 1)

    def test_32_claim_pushdata2_legacy_behavior(self):
        payload = carrier()
        raw = b"\x6a\x4d" + len(payload).to_bytes(2, "little") + payload
        self.assertEqual(len(claims.run(transaction([output(script.CScript(raw))]))), 1)

    def test_33_claim_ciphertext_bounds(self):
        for count in (1, 80):
            self.assertTrue(claims._valid_carrier(carrier(count)))
        for count in (0, 81):
            self.assertFalse(claims._valid_carrier(carrier(count)))

    def test_34_claim_magic_and_version(self):
        data = carrier()
        for bad in (b"NOPE" + data[4:], data[:4] + b"\x01" + data[5:]):
            self.assertFalse(claims._valid_carrier(bad))

    def test_35_claim_exact_length(self):
        data = carrier(7)
        self.assertFalse(claims._valid_carrier(data[:-1]))
        self.assertFalse(claims._valid_carrier(data + b"\x00"))

    def test_36_claim_nonpush_payloads_are_refused(self):
        for parts in ([script.OP_RETURN, script.OP_TRUE],
                      [script.OP_RETURN, b"MCLM", script.OP_TRUE]):
            self.assertEqual(claims.run(transaction([output(parts)])), [])

    def test_37_claim_truncated_scripts_are_refused(self):
        for raw in (b"\x6a\x4c", b"\x6a\x04MCLM\x4d"):
            self.assertEqual(claims.run(transaction([tx_api.TxOutput(script.CScript(raw), 0, None)])), [])

    def test_38_claim_all_outputs_scanned(self):
        result = claims.run(transaction([output(), output([script.OP_RETURN, carrier()])]))
        self.assertEqual([row.idx for row in result], [1])

    def test_39_claim_kind_is_not_eligibility(self):
        self.assertTrue(claims._valid_carrier(carrier(kind=255)))

    def test_40_claim_extra_marker_push_legacy_behavior(self):
        result = claims.run(transaction([output([script.OP_RETURN, b"MCLM", carrier(), b"extra"])]))
        self.assertEqual(len(result), 1)

    def test_41_all_opcode_bytes_and_truncated_pushes_do_not_raise(self):
        prefixes = (b"\x6a", b"\x6a\x04EXCH\x01\x01\x04SELL",
                    b"\x6a\x04MCLM")
        for prefix in prefixes:
            for opcode in range(256):
                for suffix in (b"", b"\xff\x00\x01"):
                    tx = transaction([tx_api.TxOutput(
                        script.CScript(prefix + bytes([opcode]) + suffix), 0, None), output()])
                    self.assertIsInstance(market.run(tx), list)
                    self.assertIsInstance(claims.run(tx), list)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-source", required=True, type=Path)
    parser.add_argument("--plugins", type=Path, default=Path(__file__).resolve().parents[2] / "plugins")
    args = parser.parse_args()
    configure(args.upstream_source.resolve(strict=True), args.plugins.resolve(strict=True))
    unittest.main(argv=[sys.argv[0]])

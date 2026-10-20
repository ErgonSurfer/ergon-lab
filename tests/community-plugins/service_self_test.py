# SPDX-License-Identifier: MIT
"""Synthetic rejection tests for the service oracle; not daemon evidence."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import service as community
from alp_service_self_test import encode

IDS = {name: bytes([n + 1]).hex() * 32 for n, name in enumerate(
    ("ALP_GENESIS", "ALP_SEND", "SLP_GENESIS", "SLP_SEND", "SELL", "CLAIM"))}
FIELDS = [b"|" * 32, bytes(4), bytes(8), bytes(8), b"a" * 20, b"b" * 20]
HEIGHT, BLOCK = 106, "ab" * 32


def entries(value):
    return [encode({1: [name], 2: [encode(body)]}) for name, body in value.items()]


def responses(projection):
    values = {}
    for txid, (index, plugins) in projection.items():
        parent = next((IDS[f + "_GENESIS"] for f in ("ALP", "SLP") if txid == IDS[f + "_SEND"]), None)
        value = {1: [bytes.fromhex(txid)[::-1]],
                 3: [encode({9: entries(projection[parent][1])}) if parent else b""],
                 4: [encode({6: entries(plugins)}) if i == index else b"" for i in range(2)],
                 8: [encode({1: [HEIGHT], 2: [bytes.fromhex(BLOCK)[::-1]]})]}
        values["/tx/" + txid] = encode(value)
    live = {}
    for role in ("ALP_SEND", "SLP_SEND", "SELL", "CLAIM"):
        txid = IDS[role]; index, plugins = projection[txid]
        for name, entry in plugins.items():
            for group in entry[1]:
                live.setdefault(name, {})[group] = (txid, index)
    for name, members in live.items():
        groups = sorted(members); prefix = "/plugin/" + name.decode() + "/"
        page = lambda gs, cursor=b"": encode({1: [encode({1: [g]}) for g in gs], **({2: [cursor]} if cursor else {})})
        values[prefix + "groups?page_size=50"] = page(groups)
        values[prefix + "groups?prefix=&page_size=1"] = page(groups[:1], groups[1] if len(groups) > 1 else b"")
        if len(groups) > 1:
            values[prefix + "groups?prefix=&start=" + groups[1].hex() + "&page_size=50"] = page(groups[1:])
        short = groups[0][:4]
        values[prefix + "groups?prefix=" + short.hex() + "&page_size=50"] = page([g for g in groups if g.startswith(short)])
        for group, (txid, index) in members.items():
            route = prefix + group.hex() + "/"
            related = sorted(t for t, (_, p) in projection.items() if name in p and group in p[name][1])
            for endpoint in ("history", "confirmed-txs"):
                values[route + endpoint] = encode({1: [values["/tx/" + t] for t in related]})
            values[route + "unconfirmed-txs"] = b""
            values[route + "utxos"] = encode({1: [encode({
                1: [encode({1: [bytes.fromhex(txid)[::-1]], 2: [index]})],
                2: [HEIGHT], 5: [community.pushed([b"MCLM", community.CLAIM_ENVELOPE])
                                if txid == IDS["CLAIM"] else b"\x51"],
                8: entries(projection[txid][1])})]})
    return values


class FakeHttp:
    def http(self, path):
        return (501 if path in ("/plugin/not_loaded/groups", "/plugin/moom_nft_index/AA/history") else 400), b""


class Contract(unittest.TestCase):
    def check(self, values, http=None):
        with patch.object(community.alp, "response", side_effect=lambda daemon, path: values[path]):
            community.check(http or FakeHttp(), IDS, community.expected(IDS, FIELDS), HEIGHT, BLOCK)

    def test_exact_six_transaction_projection_and_map_order(self):
        projection = community.expected(IDS, FIELDS)
        self.check(responses(projection))
        value = {6: entries(projection[IDS["ALP_GENESIS"]][1])}
        self.assertEqual(community.plugin_map(value, 6), community.plugin_map({6: value[6][::-1]}, 6))
        self.assertEqual(set(projection[IDS["SLP_SEND"]][1]), {community.NFT})
        self.assertNotIn(b"ALPG" + bytes.fromhex(IDS["ALP_GENESIS"]),
                         projection[IDS["ALP_SEND"]][1][community.REGISTRY][1])

    def test_upstream_template_byte_order_and_placeholder_rejections(self):
        token = "0123456789abcdef" * 4
        scripts = {family + "_SEND": b"prefix" + community.PLACEHOLDER + b"suffix" for family in ("ALP", "SLP")}
        self.assertEqual(community.send_template(scripts, "ALP", token), b"prefix" + bytes.fromhex(token)[::-1] + b"suffix")
        self.assertEqual(community.send_template(scripts, "SLP", token), b"prefix" + bytes.fromhex(token) + b"suffix")
        for family, value in (("other", token), ("ALP", "00"), ("SLP", token.upper())):
            with self.assertRaises(community.runner.Rejected): community.send_template(scripts, family, value)
        for raw in (b"", community.PLACEHOLDER * 2):
            with self.assertRaises(community.runner.Rejected):
                community.send_template({"ALP_SEND": raw}, "ALP", token)

    def test_carrier_pushes_remain_byte_fields_and_bounded(self):
        self.assertEqual(community.pushed([b"\x01", b"x" * 76]), b"\x6a\x01\x01\x4c\x4c" + b"x" * 76)
        for parts in ([1], [b"x" * 256]):
            with self.assertRaises(community.runner.Rejected): community.pushed(parts)
        market = community.expected(IDS, FIELDS)[IDS["SELL"]][1][community.MARKET][2][0]
        self.assertEqual(market[:32], b"|" * 32)
        self.assertEqual(len(market), sum(map(len, FIELDS)) + 5)

    def test_missing_duplicate_unknown_or_malformed_map_entries_refused(self):
        entry = entries({community.NFT: {1: [b"NFT"], 2: [b"data"]}})[0]
        for raw in ([entry, entry], [encode({1: [b"other"], 2: [b""]})],
                    [encode({1: [community.NFT]})],
                    [encode({1: [community.NFT, community.NFT], 2: [b""]})],
                    [encode({1: [community.NFT], 2: [encode({3: [b"unexpected"]})]})]):
            with self.assertRaises(community.runner.Rejected): community.plugin_map({6: raw}, 6)

    def test_output_spent_input_and_block_contradictions_fail(self):
        original = responses(community.expected(IDS, FIELDS))
        path = "/tx/" + IDS["ALP_SEND"]
        tx = community.service.fields(original[path])
        for field, value in ((1, [bytes(32)]), (3, [b""]), (4, []),
                             (4, [b"", encode({6: entries({community.NFT: {1: [b"wrong"]}})})]),
                             (8, [encode({1: [HEIGHT - 1], 2: [bytes.fromhex(BLOCK)[::-1]]})])):
            bad = copy.deepcopy(original); item = copy.deepcopy(tx); item[field] = value; bad[path] = encode(item)
            with self.assertRaises(community.runner.Rejected): self.check(bad)

    def test_group_cursor_history_and_utxo_ambiguity_fail(self):
        original = responses(community.expected(IDS, FIELDS))
        for path in (p for p in original if p.startswith("/plugin/") and not p.endswith("unconfirmed-txs")):
            bad = dict(original); bad[path] = b""
            with self.assertRaises(community.runner.Rejected, msg=path): self.check(bad)
        for path in (p for p in original if p.endswith(("/history", "/utxos"))):
            bad = dict(original); bad[path] += bad[path]
            with self.assertRaises(community.runner.Rejected): self.check(bad)
        path = next(p for p in original if p.endswith("/unconfirmed-txs"))
        bad = dict(original); bad[path] = original[path.replace("unconfirmed-txs", "history")]
        with self.assertRaises(community.runner.Rejected): self.check(bad)

    def test_group_page_rejects_duplicate_unordered_and_nonbyte_cursor(self):
        for raw in (encode({1: [encode({1: [b"a"]}), encode({1: [b"a"]})]}),
                    encode({1: [encode({1: [b"b"]}), encode({1: [b"a"]})]}),
                    encode({2: [1]}), encode({2: [b"x" * 513]})):
            with patch.object(community.alp, "response", return_value=raw), self.assertRaises(community.runner.Rejected):
                community.groups_page(None, community.NFT, "page_size=50")

    def test_utxo_identity_height_value_script_and_metadata_cannot_drift(self):
        original = responses(community.expected(IDS, FIELDS))
        path = next(p for p in original if p.endswith("/utxos"))
        item = community.service.fields(community.service.fields(original[path])[1][0])
        for field, values in ((1, [encode({1: [bytes(32)], 2: [1]})]),
                              (2, [HEIGHT - 1]), (3, [1]), (4, [1]), (5, [b"\x52"]), (8, [])):
            bad = dict(original); row = copy.deepcopy(item); row[field] = values
            bad[path] = encode({1: [encode(row)]})
            with self.assertRaises(community.runner.Rejected): self.check(bad)

    def test_http_refusals_cannot_be_counted_as_success(self):
        class Wrong:
            def http(self, path): return 200, b""
        with self.assertRaises(community.runner.Rejected):
            self.check(responses(community.expected(IDS, FIELDS)), Wrong())

    def test_bundle_contains_only_reviewed_modules_and_synthetic_config(self):
        with tempfile.TemporaryDirectory(prefix="community-bundle-self-") as name:
            root = Path(name).resolve(); source = root / "source"; source.mkdir()
            (source / "plugins").mkdir(); (source / "tests/community-plugins").mkdir(parents=True)
            for module in community.MODULES: (source / "plugins" / (module + ".py")).write_bytes(b"# synthetic\n")
            (source / "tests/community-plugins/plugins.toml").write_bytes(b"# synthetic\n")
            bundle, digest = community.fixture_bundle(source, root, {"version": "3.12.9", "embedding_sha256": "01" * 32})
            self.assertEqual(len(digest), 64)
            self.assertEqual(sorted(p.name for p in bundle.iterdir()), sorted([m + ".py" for m in community.MODULES] + ["plugins.toml"]))
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in bundle.iterdir()))
            self.assertFalse((bundle / "__pycache__").exists())

    def test_launch_failure_cleans_owned_root_without_report(self):
        with tempfile.TemporaryDirectory(prefix="community-cleanup-self-") as name:
            work = Path(name).resolve(); cleaned = []
            class Refused:
                def __init__(self, *args): pass
                def ready(self, available): raise ValueError("synthetic refusal")
                def cleanup(self): cleaned.append(True)
            with patch.object(community, "fixture_bundle", side_effect=lambda source, root, python: (root, "00" * 32)), \
                    patch.object(community, "Daemon", Refused), self.assertRaises(ValueError):
                community.observe(work, work / "not-an-executable", work, {}, {})
            self.assertEqual(cleaned, [True]); self.assertEqual(list(work.iterdir()), [])

    def test_launcher_retains_isolation_and_explicit_bundle_binding(self):
        with tempfile.TemporaryDirectory(prefix="community-launch-self-") as name:
            root = Path(name).resolve(); data = root / "data"; data.mkdir()
            child = object()
            with patch.object(community.subprocess, "Popen", return_value=child) as launch, \
                    patch.object(community.socket, "socket") as socket:
                socket.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 32123)
                daemon = community.Daemon(root / "binary", root, data, "synthetic", root / "bundle", "ab" * 32)
            args, kwargs = launch.call_args
            self.assertIn("-chronikupstreamregtestpluginbinding=" + "ab" * 32, args[0])
            self.assertIn("-chronikupstreamregtestpluginbundle=" + str(root / "bundle"), args[0])
            self.assertNotIn("-chronikupstreamregtestplugins=1", args[0])
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(set(kwargs["env"]), set(community.runner.ENV) | {"TMPDIR"})
            self.assertEqual(kwargs["cwd"], root)
            self.assertEqual(daemon.public_plugin, 4)
            daemon.output.close()


if __name__ == "__main__":
    unittest.main()

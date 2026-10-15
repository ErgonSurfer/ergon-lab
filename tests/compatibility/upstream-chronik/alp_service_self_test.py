# SPDX-License-Identifier: MIT
"""Synthetic ALP client/oracle rejection tests; not daemon or token evidence."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import alp_service as alp


def varint(value):
    raw = b""
    while value >= 128:
        raw += bytes([(value & 127) | 128])
        value >>= 7
    return raw + bytes([value])


def encode(value):
    raw = b""
    for field, values in value.items():
        for item in values:
            if isinstance(item, bytes):
                raw += varint(field << 3 | 2) + varint(len(item)) + item
            else:
                raw += varint(field << 3) + varint(item)
    return raw


TOKEN = "0123456789abcdef" * 4
TXID = "123456789abcdef0" * 4
WIRE = "00" * 100
TYPE = encode({2: [0]})


def colored(expected, entry=0):
    return encode({1: [TOKEN.encode()], 2: [TYPE], 3: [entry], 4: [expected[0]], 5: [int(expected[1])]})


def tx(op=0):
    inputs, outputs = alp.COLORS[op]
    entry = {1: [TOKEN.encode()], 2: [TYPE], 3: [alp.KINDS[op]],
             8: [b"100" if op == 3 else b"0"], 9: [100 if op == 3 else 0]}
    return {1: [bytes.fromhex(TXID)[::-1]],
            3: [encode({8: [colored(c)]} if c else {}) for c in inputs],
            4: [encode({5: [colored(c)]} if c else {}) for c in outputs],
            9: [123], 11: [100], 13: [encode(entry)], 15: [1]}


class Contract(unittest.TestCase):
    def check(self, value, op=0, height=-1):
        return alp.check_tx(encode(value), TXID, TOKEN, op, 123, height, "01" * 32, WIRE)

    def test_upstream_template_transport_and_exact_assertions(self):
        lines = ["Ergon ALP template " + str(i) + " " +
                 (b"\x6a" + (alp.PLACEHOLDER if i else b"synthetic")).hex() for i in range(4)]
        streams = ("Running 1 test case...\n" + "\n".join(lines) + "\n",
            "Fixture start: " + alp.TEMPLATE_CASE + "\nFixture done: " + alp.TEMPLATE_CASE +
            "\n  4 assertions out of 4 passed\n")
        scripts = alp.templates(streams)
        self.assertEqual(alp.bind_script(scripts, 1, TOKEN), b"\x6a" + bytes.fromhex(TOKEN)[::-1])
        for bad in ((streams[0] + lines[1] + "\n", streams[1]),
                    (streams[0].replace(lines[0], ""), streams[1]),
                    (streams[0].replace(lines[1], lines[2]), streams[1]),
                    (streams[0].replace(lines[1], lines[1] + "0"), streams[1]),
                    (streams[0], streams[1].replace("4 assertions out of 4", "3 assertions out of 3")),
                    (streams[0].replace(alp.PLACEHOLDER.hex(), "00" * 32), streams[1])):
            with self.assertRaises(alp.runner.Rejected): alp.templates(bad)

    def test_native_wire_is_zero_value_and_little_endian(self):
        wire = bytes.fromhex(alp.transaction([(TXID, 0, True)], [bytes(65), b"\x51", b"\x51"]))
        self.assertEqual(wire[5:37], bytes.fromhex(TXID)[::-1])
        self.assertEqual(wire[-4:], bytes(4))
        self.assertGreaterEqual(len(wire), 100)
        for inputs, outputs in (([], [b"a", b"b"]), ([("nothex", 0, False)], [b"a", b"b"]),
                                ([(TXID, 3, False)], [b"a", b"b"]), ([(TXID, 0, False)], [b"a"])):
            with self.assertRaises(alp.runner.Rejected): alp.transaction(inputs, outputs)
        for size in (-1, 65536):
            with self.assertRaises(alp.runner.Rejected): alp.compact_size(size)
        self.assertEqual(alp.compact_size(253), b"\xfd\xfd\x00")

    def test_empty_successful_protobuf_is_not_warmup(self):
        class Daemon:
            def http(self, path): return 200, b""
        self.assertEqual(alp.response(Daemon(), "/synthetic"), b"")

    def test_duplicate_scalar_wrong_family_and_coloring_fail_closed(self):
        with self.assertRaises(alp.runner.Rejected): alp.scalar({1: [1, 1]}, 1)
        valid = {5: [colored((1000, False))]}
        alp.color(valid, 5, TOKEN, (1000, False))
        for expected in (None, (999, False), (1000, True)):
            with self.assertRaises(alp.runner.Rejected): alp.color(valid, 5, TOKEN, expected)
        bad = alp.service.fields(valid[5][0])
        bad[2] = [encode({1: [1]})]
        with self.assertRaises(alp.runner.Rejected): alp.color({5: [encode(bad)]}, 5, TOKEN, (1000, False))
        alp.color({7: [colored((0, True), 2 ** 64 - 1)]}, 7, TOKEN, (0, True), 2 ** 64 - 1)

    def test_all_four_exact_operation_colors(self):
        for op in range(4): self.check(tx(op), op)

    def test_first_seen_wire_block_and_status_contradictions(self):
        original = tx()
        for field, values in ((1, [bytes(32)]), (9, [122]), (11, [101]), (12, [1]),
                              (16, [1]), (15, [2]), (14, [b"failed"]),
                              (8, [encode({1: [102], 2: [bytes(32)]})]), (13, [])):
            bad = copy.deepcopy(original)
            bad[field] = values
            with self.assertRaises(alp.runner.Rejected): self.check(bad)
        confirmed = copy.deepcopy(original)
        confirmed[8] = [encode({1: [102], 2: [bytes.fromhex("01" * 32)[::-1]]})]
        self.check(confirmed, height=102)
        with self.assertRaises(alp.runner.Rejected): self.check(confirmed, height=103)

    def test_burn_mint_baton_and_input_output_color_contradictions(self):
        for op in range(4):
            original = tx(op)
            for field, replacement in ((3, []), (4, [])):
                bad = copy.deepcopy(original)
                bad[field] = replacement
                with self.assertRaises(alp.runner.Rejected): self.check(bad, op)
            for field, replacement in ((3, [encode({8: [colored((123, False))]})]),
                                        (4, [encode({5: [colored((123, False))]})])):
                bad = copy.deepcopy(original)
                bad[field][0] = replacement[0]
                with self.assertRaises(alp.runner.Rejected): self.check(bad, op)
            for field, value in ((8, b"101"), (9, 101), (10, 1), (3, 1), (5, 1), (7, b"bad")):
                bad = copy.deepcopy(original)
                entry = alp.service.fields(bad[13][0])
                entry[field] = [value]
                bad[13] = [encode(entry)]
                with self.assertRaises(alp.runner.Rejected): self.check(bad, op)

    def test_invalid_mint_is_diagnostic_not_fake_normal(self):
        bad = tx(1)
        bad[3] = [encode({8: [colored((1400, False))]})]
        bad[4] = [b"", b"", b""]
        entry = alp.service.fields(bad[13][0])
        entry[5], entry[8] = [1], [b"1400"]
        bad[13], bad[15] = [encode(entry)], [2]
        alp.check_tx(encode(bad), TXID, TOKEN, 1, 123, -1, "01" * 32, WIRE,
                     ([(1400, False)], [None, None, None]), True, 1400)
        with self.assertRaises(alp.runner.Rejected): self.check(bad, 1)

    def test_malformed_requires_exact_native_binding_and_upstream_error(self):
        value = {1: [bytes.fromhex(TXID)[::-1]], 9: [123], 11: [100],
                 3: [b""], 4: [b"", b""], 15: [2], 14: [encode({3: [b"synthetic upstream parsing error"]})]}
        def check(v): alp.check_malformed(encode(v), TXID, 123, -1, "01" * 32, WIRE)
        check(value)
        for field, replacement in ((1, [bytes(32)]), (9, [122]), (14, []),
                                    (14, [b""]), (15, [1]), (13, [b""]),
                                    (4, [b""]), (3, [encode({8: [colored((1, False))]})])):
            bad = copy.deepcopy(value)
            bad[field] = replacement
            with self.assertRaises(alp.runner.Rejected): check(bad)

    def test_cleanup_runs_on_native_refusal(self):
        class Daemon:
            def __init__(self, *args): self.cleaned = False; created.append(self)
            def ready(self): raise RuntimeError("synthetic native refusal")
            def cleanup(self): self.cleaned = True
        import tempfile
        created = []
        with tempfile.TemporaryDirectory(prefix="synthetic-alp-") as directory:
            work = Path(directory).resolve()
            with patch.object(alp.service, "Daemon", Daemon), self.assertRaises(RuntimeError):
                alp.observe(Path("synthetic-binary"), work, ())
            self.assertTrue(created[0].cleaned)
            self.assertEqual(list(work.iterdir()), [])


    def test_decoded_native_prevout_sequence_script_and_shape_binding(self):
        native = {"txid": TXID, "version": 2, "locktime": 0,
                  "vin": [{"txid": TOKEN, "vout": 1, "scriptSig": {"hex": ""}, "sequence": 2 ** 32 - 1}],
                  "vout": [{"value": 0, "scriptPubKey": {"hex": "51"}}]}
        value = {1: [bytes.fromhex(TXID)[::-1]], 2: [2],
                 3: [encode({1: [encode({1: [bytes.fromhex(TOKEN)[::-1]], 2: [1]})], 5: [2 ** 32 - 1]})],
                 4: [encode({2: [b"\x51"]})]}
        alp.check_native_view(value, native)
        for mutate in (lambda n: n.update(version=3), lambda n: n.update(locktime=1),
                       lambda n: n["vin"][0].update(txid="00" * 32),
                       lambda n: n["vin"][0].update(vout=0),
                       lambda n: n["vin"][0].update(sequence=0),
                       lambda n: n["vin"][0]["scriptSig"].update(hex="51"),
                       lambda n: n["vout"][0].update(value=1),
                       lambda n: n["vout"][0]["scriptPubKey"].update(hex="52"),
                       lambda n: n["vin"].pop(), lambda n: n["vout"].pop()):
            bad = copy.deepcopy(native)
            mutate(bad)
            with self.assertRaises(alp.runner.Rejected): alp.check_native_view(value, bad)


if __name__ == "__main__":
    unittest.main()

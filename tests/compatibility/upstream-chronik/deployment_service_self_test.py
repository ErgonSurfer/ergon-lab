# SPDX-License-Identifier: MIT
import struct
import tempfile
import unittest
from pathlib import Path
import deployment_service as driver
import historical_client_service as clients

ROOT = Path(__file__).resolve().parents[3]

def elf(flags=6, count=1):
    raw = bytearray(64 + 56 * count)
    raw[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<H", raw, 18, 62)
    struct.pack_into("<Q", raw, 32, 64)
    struct.pack_into("<HH", raw, 54, 56, count)
    for i in range(count): struct.pack_into("<II", raw, 64 + 56 * i, 0x6474e551, flags)
    return bytes(raw)

class Socket:
    def __init__(self, raw=b""): self.raw, self.sent = raw, []
    def recv(self, count):
        result, self.raw = self.raw[:min(count, 3)], self.raw[min(count, 3):]
        return result
    def sendall(self, raw): self.sent.append(raw)
    def settimeout(self, seconds): pass

def websocket(raw=b""):
    value = object.__new__(clients.WebSocket)
    value.sock, value.buffer, value.events = Socket(raw), b"", []
    return value

class Contract(unittest.TestCase):
    def test_shadow_refusals_bind_the_actual_compiled_plugin_profile(self):
        full = driver.shadow.refusal_vectors("01" * 32, True)
        plain = driver.shadow.refusal_vectors("01" * 32, False)
        self.assertEqual(len(full), 12)
        self.assertEqual(len(plain), 12)
        for number, ((flags, expected), (plain_flags, plain_expected)) in enumerate(zip(full, plain, strict=True)):
            self.assertEqual(flags, plain_flags)
            if number in (8, 9):
                key = flags[-1].split("=", 1)[0]
                self.assertEqual(plain_expected, ("Error parsing command line arguments: Invalid parameter " + key).encode())
                self.assertIn(b"no regtest/client/plugin profile", expected)
            else:
                self.assertEqual(expected, plain_expected)
        for invalid in (None, 0, 1, "regtest"):
            with self.assertRaises(ValueError): driver.shadow.refusal_vectors("01" * 32, invalid)

    def test_stack_requires_one_RW_non_executable_header(self):
        driver.elf_stack(elf())
        for raw in (elf(7), elf(4), elf(count=2), elf(count=0), b"", elf()[:70]):
            with self.assertRaises(ValueError): driver.elf_stack(raw)

    def test_stack_rejects_foreign_machine_or_encoding(self):
        for offset, value in ((4, 1), (5, 2), (18, 183)):
            raw = bytearray(elf()); raw[offset] = value
            with self.assertRaises(ValueError): driver.elf_stack(bytes(raw))

    def test_assertion_summary_is_required_unique_positive_and_equal(self):
        self.assertEqual(driver.assertion_count(("", "  31 assertions out of 31 passed\n")), 31)
        for raw in ("", "  0 assertions out of 0 passed", "  2 assertions out of 3 passed",
                    "  01 assertions out of 01 passed", "  2 assertions out of 2 passed\n  2 assertions out of 2 passed"):
            with self.assertRaises(ValueError): driver.assertion_count((raw, ""))
        with self.assertRaises(ValueError): driver.assertion_count(("  1 assertions out of 1 passed", "  1 assertions out of 1 passed"))

    def test_complete_native_inventory_and_compiled_out_controls(self):
        out = driver.native_groups(ROOT, "compiled-out")
        self.assertEqual(tuple(name for name, _ in out), driver.JOURNAL)
        self.assertEqual(sum(len(cases) for _, cases in out), 9)
        plain, plugins = driver.native_groups(ROOT, "regtest"), driver.native_groups(ROOT, "regtest-plugins")
        self.assertEqual(len(plain), 9); self.assertEqual(len(plugins), 9)
        self.assertEqual(sum(len(cases) for _, cases in plugins) - sum(len(cases) for _, cases in plain), 2)

    def test_unknown_or_unbalanced_preprocessor_is_refused(self):
        for raw in ("#ifdef UNKNOWN\n", "#endif\n", "#ifdef ENABLE_CHRONIK_UPSTREAM_SHADOW\n"):
            with self.assertRaises(ValueError): driver.case_groups(raw, False, False)

    def test_exact_protobuf_raw_request(self):
        for size in (0, 1, 127, 128, 1000, 65536):
            raw = b"x" * size
            self.assertEqual(clients.service.fields(clients.message(raw)), {1: [raw]})

    def test_masked_binary_client_and_fragmented_reads(self):
        value = websocket(b"abcdefgh")
        self.assertEqual(value.read(4), b"abcd"); self.assertEqual(value.read(4), b"efgh")
        value.send(b"\x42\x00")
        raw = value.sock.sent[0]
        self.assertEqual(raw[:2], b"\x82\x82")
        self.assertEqual(bytes(b ^ raw[2 + i % 4] for i, b in enumerate(raw[6:])), b"\x42\x00")

    def test_invalid_server_frames_cannot_forge_notification(self):
        for raw in (b"\x82\x80", b"\x81\x00", b"\x02\x00", b"\x88\x00",
                    b"\x82\x7f" + (1024 * 1024 + 1).to_bytes(8, "big")):
            with self.assertRaises(Exception): websocket(raw).wait(lambda _: False)

    def test_ping_pong_is_not_notification(self):
        value = websocket(b"\x89\x01x\x82\x02\x0a\x00")
        self.assertEqual(value.wait(lambda event: 1 in event), {1: [b""]})
        self.assertEqual(value.sock.sent[0][0], 0x8a)

    def test_native_timestamp_rebase_is_exact_unique_and_positive(self):
        raw = b"\x0a\x01x\x48" + clients.varint(2000) + b"\x58\x7b"
        expected = b"\x0a\x01x\x48" + clients.varint(1000) + b"\x58\x7b"
        self.assertEqual(clients.rebase_timestamp(raw, 9, 2000, 1000), expected)
        for broken, current, reference in ((b"\x0a\x01x", 2000, 1000), (raw, 1999, 1000),
                (raw + b"\x48" + clients.varint(2000), 2000, 1000),
                (b"\x4a\x01x", 2000, 1000), (raw, 2000, 0), (raw, 0, 1000)):
            with self.assertRaises(ValueError): clients.rebase_timestamp(broken, 9, current, reference)

    def test_timestamp_view_changes_only_three_declared_protobuf_locations(self):
        ids = [bytes([i]).hex() * 32 for i in range(1, 5)]
        now, old = clients.MOCK_TIME + 120, clients.MOCK_TIME
        def tx(identity, timestamp):
            return clients.message(bytes.fromhex(identity)[::-1]) + b"\x48" + clients.varint(timestamp) + b"\x58\x7b\xc2\x02\x03abc"
        current = {"/tx/" + identity: tx(identity, now) for identity in ids}
        expected = {"/tx/" + identity: tx(identity, old) for identity in ids}
        token = "/token/" + ids[0]
        current[token] = clients.message(ids[0].encode()) + b"\x28" + clients.varint(now)
        expected[token] = clients.message(ids[0].encode()) + b"\x28" + clients.varint(old)
        for prefix in ("/token-id/" + ids[0], "/lokad-id/534c5032"):
            for suffix in ("/history", "/confirmed-txs", "/unconfirmed-txs"):
                current[prefix + suffix] = b"".join(clients.message(tx(identity, now)) for identity in ids) + b"\x10\x01\x18\x04"
                expected[prefix + suffix] = b"".join(clients.message(tx(identity, old)) for identity in ids) + b"\x10\x01\x18\x04"
        current["/raw-tx/" + ids[0]] = b"uninterpreted-wire"
        expected["/raw-tx/" + ids[0]] = b"uninterpreted-wire"
        class Daemon:
            def http(self, path, method="GET"): return 200, current[path]
        view = clients.NativeTimeComparison(Daemon(), ids, [now] * 4, [old] * 4)
        for path in current: self.assertEqual(view.http(path), (200, expected[path]))
        # Unknown bytes and repeated order are never scrubbed or sorted away.
        current["/tx/" + ids[0]] = current["/tx/" + ids[0]].replace(b"abc", b"bad")
        self.assertNotEqual(view.http("/tx/" + ids[0])[1], expected["/tx/" + ids[0]])
        current["/tx/" + ids[0]] = tx("ff" * 32, now)
        with self.assertRaises(ValueError): view.http("/tx/" + ids[0])
        current["/tx/" + ids[0]] = tx(ids[0], now + 1)
        with self.assertRaises(ValueError): view.http("/tx/" + ids[0])

    def test_wire_rebase_preserves_fixed_and_opaque_fields(self):
        raw = b"\x09" + bytes(range(8)) + b"\x15" + bytes(range(4)) + b"\x1a\x03abc\x48\x7b"
        self.assertEqual(clients.rebase_timestamp(raw, 9, 123, 124), raw[:-1] + b"\x7c")
        for raw in (b"\x09x", b"\x15x", b"\x1a\x02x", b"\x48" + b"\x80" * 10):
            with self.assertRaises(ValueError): list(clients.wire_fields(raw))

    def test_client_root_is_owned_by_unchanged_cleanup_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary).resolve()
            root = Path(tempfile.mkdtemp(prefix="ergon-upstream-continuous-client-", dir=work)).resolve()
            driver.runner.remove_owned(root, work)
            self.assertFalse(root.exists())
            root = Path(tempfile.mkdtemp(prefix="continuous-client-", dir=work)).resolve()
            with self.assertRaises(ValueError): driver.runner.remove_owned(root, work)
            self.assertTrue(root.exists())

if __name__ == "__main__":
    unittest.main()

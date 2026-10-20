# SPDX-License-Identifier: MIT
import struct
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

if __name__ == "__main__":
    unittest.main()

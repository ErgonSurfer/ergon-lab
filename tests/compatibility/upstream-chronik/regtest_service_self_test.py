# SPDX-License-Identifier: MIT
"""Synthetic strict response and lifecycle failure-path checks; no daemon."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import regtest_service as service
import run_repetitions as runner


class Contract(unittest.TestCase):
    def test_exact_owned_startup_cases_and_assertions(self):
        self.assertEqual(len(service.STARTUP_CASES), 5)
        self.assertEqual(tuple(count for _, count in service.STARTUP_CASES), (7, 5, 6, 9, 9))
        self.assertEqual(len({case for case, _ in service.STARTUP_CASES}), 5)
        for case, count in service.STARTUP_CASES:
            for wrong in (count - 1, count + 1):
                with self.assertRaises(runner.Rejected):
                    runner.parse_streams(("Running 1 test case...\n",
                        "Fixture start: " + case + "\nFixture done: " + case +
                        f"\n  {wrong} assertions out of {wrong} passed\n"), (case,), count)

    def test_refusal_diagnostics_do_not_serialize_exception_paths_or_output(self):
        import subprocess
        private = "/synthetic-private/path/never-publish"
        errors = ((subprocess.TimeoutExpired([private], 60, output=private), "process-timeout"),
                  (subprocess.CalledProcessError(1, [private], output=private), "child-process-refusal"),
                  (OSError(private), "operating-system-refusal"),
                  (KeyError(private), "binding-field-missing"),
                  (runner.Rejected(private), "contract-refusal"))
        for error, expected in errors:
            self.assertEqual(runner.refusal_class(error), expected)
            self.assertNotIn(private, runner.refusal_class(error))

    def test_persistent_lease_rejects_unknown_bytes_and_competing_owner(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-service-lease-") as name:
            root = Path(name)
            lease = root / "probe.lock"
            lease.write_bytes(b"fixture-exclusive; stale lock requires external review\n")
            with self.assertRaises(runner.Rejected):
                service.released_service_lease(root)
            lease.write_bytes(service.SERVICE_LEASE)
            original = lease.stat().st_ino
            with lease.open("r+b") as held:
                service.fcntl.flock(held.fileno(), service.fcntl.LOCK_EX | service.fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    service.released_service_lease(root)
            service.released_service_lease(root)
            self.assertEqual(lease.stat().st_ino, original)

    def test_exact_shutdown_case_and_assertions(self):
        self.assertEqual(service.SHUTDOWN, (
            "chronik_upstream_regtest_tests/shutdown_cancels_inflight_capture_without_failed_root", 9))
        case, assertions = service.SHUTDOWN
        out = "Running 1 test case...\n"
        progress = "Fixture start: " + case + "\nFixture done: " + case + "\n"
        for count in (8, 10):
            with self.assertRaises(runner.Rejected):
                runner.parse_streams((out, progress + f"  {count} assertions out of {count} passed\n"), (case,), assertions)

    def test_wire_bounds(self):
        self.assertEqual(service.fields(b"\x0a\x02ab\x10\x03"), {1: [b"ab"], 2: [3]})
        for raw in (b"\x00", b"\x0a\x04a", b"\x08\x80", b"\x0b", b"\x08" + b"\xff" * 10,
                    b"\x0a" + b"\xff" * 10, b"\x09ab", bytes(8 * 1024 * 1024 + 1)):
            with self.subTest(raw_bytes=len(raw)), self.assertRaises(runner.Rejected):
                service.fields(raw)

    def test_exact_pool_ids_no_duplicates(self):
        tx = b"\x0a\x20" + bytes(range(32))
        page = b"\x0a" + bytes([len(tx)]) + tx
        self.assertEqual(service.txids(page), [bytes(range(32))[::-1].hex()])
        for malformed in (page + page, b"\x0a\x00", b"\x0a\x03\x0a\x01a"):
            with self.assertRaises(runner.Rejected):
                service.txids(malformed)

    def test_zero_value_native_wire(self):
        previous = "12" * 32
        first = bytes.fromhex(service.zero_tx(previous, 0, True))
        child = bytes.fromhex(service.zero_tx(previous, 1, False))
        self.assertGreaterEqual(len(first), 100)
        self.assertGreaterEqual(len(child), 100)
        self.assertNotEqual(first, child)
        self.assertEqual(first[5:37], bytes.fromhex(previous)[::-1])
        self.assertEqual(first[-4:], bytes(4))
        self.assertEqual(service.p2sh_true_address(), service.p2sh_true_address())

    def test_failed_root_cannot_also_listen(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-service-") as name:
            path = Path(name) / "log"
            path.write_text("Chronik upstream regtest unavailable node_continues=1\n"
                            "Chronik upstream regtest listening=127.0.0.1:12345 plugins=0\n")
            daemon = object.__new__(service.Daemon)
            daemon.log = path
            daemon.rpc = lambda *args: {"chain": "regtest"}
            with self.assertRaises(runner.Rejected):
                daemon.ready(available=False)

    def test_failed_tip_and_pool_cannot_pass(self):
        daemon = object.__new__(service.Daemon)
        daemon.http = lambda _: (200, b"\x0a\x20" + bytes(32))
        def once(check, timeout=30):
            runner.require(check(), "synthetic mismatch")
        with patch.object(service, "wait_for", once):
            with self.assertRaises(runner.Rejected):
                daemon.tip(1, "01" * 32)
            daemon.http = lambda _: (200, b"")
            daemon.rpc = lambda *args: ["01" * 32]
            with self.assertRaises(runner.Rejected):
                daemon.pool(["01" * 32])

    def test_deadline_fails_closed(self):
        with patch.object(service.time, "monotonic", side_effect=[0, 31]):
            with self.assertRaises(runner.Rejected):
                service.wait_for(lambda: False)

    def test_exited_daemon_is_not_misclassified_as_warmup(self):
        daemon = object.__new__(service.Daemon)
        class Exited:
            def poll(self):
                return 1
        daemon.proc = Exited()
        with self.assertRaisesRegex(RuntimeError, "daemon exited"):
            service.wait_for(lambda: daemon.rpc("getblockchaininfo"))

    def test_exclusive_report_and_no_operational_fields(self):
        payload = {"schema": "synthetic", "binary_sha256": hashlib.sha256(b"public").hexdigest()}
        with tempfile.TemporaryDirectory(prefix="synthetic-service-report-") as name:
            report = Path(name) / "new.json"
            runner.write_report(report, payload)
            with self.assertRaises(FileExistsError):
                runner.write_report(report, payload)
            self.assertNotIn(name.encode(), report.read_bytes())


if __name__ == "__main__":
    unittest.main()

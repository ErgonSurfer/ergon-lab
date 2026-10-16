# SPDX-License-Identifier: MIT
"""Synthetic scheduling/refusal tests only, never native execution evidence."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import catchup_service as client


class Contract(unittest.TestCase):
    def run_observation(self, *, refuse=None, drift=None, cleanup_refuse=None):
        created, snapshots, submitted, pools, calls = [], [], [], [], []
        height = 0
        identities = {"ergon-identity.json": b'{"plugins":true}',
                      "ergon_public_probe.py": b"synthetic", "plugins.toml": b"synthetic"}
        class Daemon:
            def __init__(self, binary, root, data, phase, public_plugin=False):
                self.number = len(created); self.cleaned = False; self.stopped = False
                self.plugin = public_plugin; created.append(self)
            def ready(self):
                if refuse == ("ready", self.number): raise client.runner.Rejected("synthetic startup")
            def rpc(self, method, args=()):
                nonlocal height
                calls.append((self.number, method, args))
                if refuse == ("rpc", self.number): raise client.runner.Rejected("synthetic RPC")
                if method == "generatetoaddress":
                    self_test.assertEqual(args[0], 1)
                    height += 1
                    return [f"{height:064x}"]
                if method == "getblock": return {"tx": [{"txid": "01" * 32, "vout": [{"value": int(drift == "funding")}]}]}
                if method == "getmempoolentry": return {"time": 0 if drift == "time" else 123}
                if method == "getconnectioncount": return int(drift == "peer")
                raise AssertionError("unexpected RPC")
            def tip(self, number, block_hash):
                self_test.assertEqual(height, number)
                self_test.assertEqual(f"{number:064x}", block_hash)
            def pool(self, ids): pools.append(tuple(ids))
            def http(self, path, method="GET"): return (200 if drift == "capability" else 501), b""
            def stop(self):
                if refuse == ("stop", self.number): raise client.runner.Rejected("synthetic joined stop")
                self.stopped = True
            def cleanup(self):
                self.cleaned = True
                if self.number == cleanup_refuse:
                    raise OSError("synthetic cleanup refusal; never serialize this text")
        def snapshot(daemon, ids, wires, times, number, block_hash):
            snapshots.append((daemon.number, number, len(ids), len(wires), times))
            if refuse == ("snapshot", len(snapshots)): raise client.runner.Rejected("synthetic oracle")
            return "changed" if drift == "snapshot" and len(snapshots) == 3 else (number, block_hash)
        def submit(daemon, wire):
            submitted.append(wire)
            return f"{len(submitted):064x}"
        def regular(path):
            result = identities[path.name]
            return result + b"drift" if drift == "identity" and len(created) == 2 else result
        self_test = self
        with tempfile.TemporaryDirectory(prefix="synthetic-catchup-") as directory:
            work = Path(directory).resolve()
            with ExitStack() as stack:
                for module, name, value in ((client.service, "Daemon", Daemon),
                    (client.combined, "snapshot", snapshot), (client.alp, "submit", submit),
                    (client.alp, "bind_script", lambda *args: b"synthetic-upstream-template"),
                    (client.alp, "transaction", lambda *args: "00"), (client.runner, "regular", regular)):
                    stack.enter_context(patch.object(module, name, value))
                try:
                    result = client.observe(Path("synthetic-binary"), work, (b"synthetic",) * 4)
                finally:
                    self.assertTrue(all(d.cleaned for d in created))
                    if cleanup_refuse is None:
                        self.assertEqual(list(work.iterdir()), [])
                    else:
                        self.assertEqual(len(list(work.iterdir())), 1)
        return result, created, snapshots, calls

    def test_schedule_existing_oracles_300_blocks_and_two_joined_stops(self):
        result, daemons, snapshots, calls = self.run_observation()
        self.assertEqual(result["final_height"], 402)
        self.assertEqual(result["rapid_native_blocks"], 300)
        self.assertEqual(len(daemons), 2)
        self.assertTrue(all(d.plugin and d.stopped for d in daemons))
        self.assertEqual([s[:2] for s in snapshots], [(0, -1), (0, 102), (0, 102), (1, 102)])
        self.assertEqual(sum(method == "generatetoaddress" for _, method, _ in calls), 402)

    def test_refusal_at_every_stage_cleans_up_and_never_becomes_success(self):
        for refuse in (("ready", 0), ("ready", 1), ("rpc", 0), ("rpc", 1),
                       ("stop", 0), ("stop", 1), *(('snapshot', i) for i in range(1, 5))):
            with self.subTest(refuse=refuse), self.assertRaises(client.runner.Rejected):
                self.run_observation(refuse=refuse)

    def test_native_value_time_snapshot_identity_peer_and_capability_refused(self):
        for drift in ("funding", "time", "snapshot", "identity", "peer", "capability"):
            with self.subTest(drift=drift), self.assertRaises(client.runner.Rejected):
                self.run_observation(drift=drift)

    def test_no_automatic_retry_after_first_series_failure(self):
        with patch.object(client, "observe", side_effect=client.runner.Rejected("synthetic")) as observe:
            with self.assertRaises(client.runner.Rejected): client.series(None, None, (), 3)
            self.assertEqual(observe.call_count, 1)
        with patch.object(client, "observe", side_effect=[{"v": 1}, {"v": 2}, {"v": 1}]) as observe:
            with self.assertRaises(client.runner.Rejected): client.series(None, None, (), 3)
            self.assertEqual(observe.call_count, 2)

    def test_finite_repetitions_and_stability(self):
        for value in (0, 11, True, -1):
            with self.assertRaises(client.runner.Rejected): client.series(None, None, (), value)
        with patch.object(client, "observe", return_value={"v": 1}) as observe:
            self.assertEqual(client.series(None, None, (), 3), {"v": 1})
            self.assertEqual(observe.call_count, 3)

    def test_cleanup_refusal_still_attempts_every_owner_and_retains_unverified_root(self):
        for owner in (0, 1):
            with self.subTest(owner=owner), self.assertRaises(client.runner.Rejected):
                self.run_observation(cleanup_refuse=owner)

    def test_exact_native_leaf_assertions_missing_duplicate_and_timeout_refused(self):
        def streams(command, work, forbidden):
            name = command[1].split("=", 1)[1].split("/", 1)[1]
            count = dict(client.NATIVE_CASES)[name]
            case = client.runner.PREFIX + name
            return (f"Running 1 test case...\nFixture start: {case}\nFixture done: {case}\n",
                    f"  {count} assertions out of {count} passed\n")
        with patch.object(client.runner, "execute", side_effect=streams):
            result = client.native_controls(Path("synthetic"), None, ())
            self.assertEqual(len(result), 5)
            self.assertEqual(sum(count for _, count in client.NATIVE_CASES), 72)
        for bad in (("Running 1 test case...\n", ""),
                    ("Running 1 test case...\n", "  0 assertions out of 0 passed\n")):
            with patch.object(client.runner, "execute", return_value=bad), self.assertRaises(client.runner.Rejected):
                client.native_controls(Path("synthetic"), None, ())
        with patch.object(client.runner, "execute", side_effect=client.subprocess.TimeoutExpired("synthetic", 60)) as execute:
            with self.assertRaises(client.subprocess.TimeoutExpired): client.native_controls(Path("synthetic"), None, ())
            self.assertEqual(execute.call_count, 1)

    def test_no_paths_or_exception_text_in_public_report(self):
        forbidden = (Path("/synthetic/source"), Path("/synthetic/build"), Path("/synthetic/work"))
        for path in forbidden:
            with self.assertRaises(client.runner.Rejected): client.public_report({"field": str(path)}, forbidden)
        self.assertEqual(client.public_report({"result": True}, forbidden), {"result": True})
        self.assertNotIn("private", client.runner.refusal_class(OSError("private synthetic data")))

    def test_report_creation_is_exclusive(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-catchup-report-") as directory:
            path = Path(directory) / "report.json"
            client.runner.write_report(path, {"synthetic": True})
            original = path.read_bytes()
            with self.assertRaises(FileExistsError): client.runner.write_report(path, {"synthetic": False})
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

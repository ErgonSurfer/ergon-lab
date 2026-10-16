# SPDX-License-Identifier: MIT
"""Synthetic contract tests; not real daemon or reconstruction evidence."""
from contextlib import ExitStack
import itertools
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch
import cold_rebuild_service as client


def block_fixture():
    ids = [f"{op + 10:064x}" for op in range(4)]
    funding = ("01" * 32, 0)
    corpus = [{"txid": txid, "vin": [{"txid": parent, "vout": out} for parent, out in
              ([funding] if op == 0 else [(ids[p], n) for p, n in client.combined.INPUTS[op]])]}
              for op, txid in enumerate(ids)]
    coinbase = {"txid": "02" * 32, "vin": [{"coinbase": "00"}]}
    return ids, funding, coinbase, corpus


class Contract(unittest.TestCase):
    def test_ancestry_independent_of_all_block_order_permutations(self):
        ids, funding, coinbase, corpus = block_fixture()
        for ordering in itertools.permutations(corpus):
            self.assertEqual(client.bind_ancestry({"tx": [coinbase, *ordering]}, funding), ids)

    def test_wrong_ambiguous_or_duplicate_native_ancestry_refused(self):
        ids, funding, coinbase, corpus = block_fixture()
        altered = dict(corpus[1], vin=corpus[0]["vin"])
        for rows in ([coinbase, *corpus[:3]], [coinbase, corpus[0], corpus[0], *corpus[2:]],
                     [coinbase, corpus[0], altered, *corpus[2:]], [*corpus, coinbase]):
            with self.subTest(rows=rows), self.assertRaises((client.runner.Rejected, KeyError)):
                client.bind_ancestry({"tx": rows}, funding)

    def observation(self, drift=None, refusal=None, cleanup_refusal=None):
        ids, funding, coinbase, corpus = block_fixture()
        created, snapshots, submitted = [], [], []
        height = 0
        test = self
        class Daemon:
            def __init__(self, binary, root, data, phase, enabled=True, *, public_plugin=False):
                self.number = len(created)
                self.data, self.enabled, self.plugin = data, enabled, public_plugin
                self.cleaned = False
                created.append(self)
                test.assertEqual(enabled, public_plugin)
            def ready(self):
                if refusal == ("ready", self.number): raise client.runner.Rejected("synthetic ready")
                index = self.data / "regtest/chronik-upstream-regtest"
                inputs = self.data / "regtest/chronik-upstream-regtest-plugin-inputs"
                index.mkdir(parents=True, exist_ok=True)
                inputs.mkdir(parents=True, exist_ok=True)
                for path, raw in ((index / "ergon-identity.json", b'{"plugins":true}'),
                                  (index / "probe.lock", b"synthetic lock"),
                                  (inputs / "ergon_public_probe.py", b"synthetic"),
                                  (inputs / "plugins.toml", b"synthetic")):
                    if not path.exists(): path.write_bytes(raw)
                if self.number == 2 and drift == "identity":
                    (index / "ergon-identity.json").write_bytes(b"changed")
                if self.number == 2 and drift == "lease":
                    (index / "probe.lock").rename(index / "synthetic-prior-lock")
                    (index / "probe.lock").write_bytes(b"synthetic lock")
            def rpc(self, method, args=()):
                nonlocal height
                if refusal == ("rpc", self.number): raise client.runner.Rejected("synthetic RPC")
                if method == "getblockchaininfo": return {"blocks": height}
                if method == "generatetoaddress":
                    test.assertEqual(args[0], 1)
                    height += 1
                    return [f"{height:064x}"]
                if method == "getblock":
                    if args[0] == f"{1:064x}":
                        return {"tx": [{"txid": funding[0], "vout": [{"value": int(drift == "funding")}]}]}
                    return {"height": 102, "tx": [coinbase, *reversed(corpus)]}
                if method == "getrawmempool": return submitted[:] if height < 102 else []
                if method == "getmempoolentry": return {"time": 0 if drift == "time" else 123}
                if method == "getrawtransaction": return "bad" if drift == "wire" else "00"
                if method == "getblockcount": return height
                if method == "getbestblockhash": return f"{height:064x}"
                if method == "getconnectioncount": return int(drift == "peer")
                raise AssertionError("unexpected RPC")
            def tip(self, h, block_hash):
                test.assertEqual(height, h)
                test.assertEqual(block_hash, f"{height:064x}")
            def pool(self, pool): test.assertEqual(pool, [])
            def stop(self):
                if refusal == ("stop", self.number): raise client.runner.Rejected("synthetic stop")
            def http(self, path, method="GET"): return (200 if drift == "capability" else 501, b"")
            def cleanup(self):
                self.cleaned = True
                if cleanup_refusal == self.number: raise OSError("synthetic cleanup refusal")
        def submit(daemon, raw):
            txid = ids[len(submitted)]
            submitted.append(txid)
            return txid
        def snapshot(daemon, actual_ids, wires, times, h, block_hash):
            test.assertEqual(actual_ids, ids)
            test.assertEqual(wires, ["00"] * 4)
            test.assertEqual(times, [0] * 4)
            test.assertEqual(h, 102)
            test.assertEqual(block_hash, f"{102:064x}")
            snapshots.append(daemon.number)
            if refusal == ("snapshot", len(snapshots)): raise client.runner.Rejected("synthetic snapshot")
            return "changed" if drift == "snapshot" and len(snapshots) == 2 else "same"
        with tempfile.TemporaryDirectory(prefix="synthetic-cold-contract-") as directory, ExitStack() as stack:
            work = Path(directory).resolve()
            for target, name, value in ((client.service, "Daemon", Daemon),
                    (client.service, "wait_for", lambda fn: fn()), (client.alp, "submit", submit),
                    (client.alp, "transaction", lambda *args: "00"),
                    (client.alp, "bind_script", lambda *args: b"\x6a"),
                    (client.combined, "snapshot", snapshot)):
                stack.enter_context(patch.object(target, name, value))
            try:
                result = client.observe(Path("synthetic"), work, ())
                self.assertEqual(list(work.iterdir()), [])
                return result, created, snapshots
            finally:
                self.assertTrue(all(d.cleaned for d in created))
                if cleanup_refusal is not None:
                    self.assertEqual(len(list(work.iterdir())), 1)

    def test_cold_rebuild_and_reopen_use_three_distinct_owners_and_unknown_times(self):
        result, owners, snapshots = self.observation()
        self.assertEqual([d.plugin for d in owners], [False, True, True])
        self.assertEqual(snapshots, [1, 2, 2])
        self.assertEqual(result["daemon_processes"], 3)
        self.assertEqual(result["historical_first_seen"], "explicit-unknown-zero-not-original-mempool-time")

    def test_native_values_time_wire_snapshot_identity_lease_peers_capabilities_refused(self):
        for drift in ("funding", "time", "wire", "snapshot", "identity", "lease", "peer", "capability"):
            with self.subTest(drift=drift), self.assertRaises(client.runner.Rejected):
                self.observation(drift=drift)

    def test_stage_failures_attempt_all_owned_cleanup(self):
        for refusal in (("rpc", 0), ("ready", 1), ("ready", 2),
                        ("stop", 0), ("stop", 1), ("stop", 2),
                        ("snapshot", 1), ("snapshot", 2), ("snapshot", 3)):
            with self.subTest(refusal=refusal), self.assertRaises(client.runner.Rejected):
                self.observation(refusal=refusal)

    def test_cleanup_failure_at_any_owner_retains_unverified_root(self):
        for owner in range(3):
            with self.subTest(owner=owner), self.assertRaises(client.runner.Rejected):
                self.observation(cleanup_refusal=owner)

    def test_existing_or_dangling_index_is_never_adopted_or_deleted(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-cold-absence-") as directory:
            root = Path(directory)
            index, inputs = root / "index", root / "inputs"
            client.require_absent_index(index, inputs)
            index.symlink_to(root / "missing-target")
            with self.assertRaises(client.runner.Rejected): client.require_absent_index(index, inputs)
            self.assertTrue(index.is_symlink())
            index.unlink()
            inputs.mkdir()
            with self.assertRaises(client.runner.Rejected): client.require_absent_index(index, inputs)
            self.assertTrue(inputs.is_dir())

    def test_finite_stable_series_without_automatic_retry(self):
        for repetitions in (0, 11, True, -1):
            with self.assertRaises(client.runner.Rejected): client.series(None, None, (), repetitions)
        with patch.object(client, "observe", return_value={"result": True}) as observe:
            self.assertEqual(client.series(None, None, (), 3), {"result": True})
            self.assertEqual(observe.call_count, 3)
        with patch.object(client, "observe", side_effect=client.runner.Rejected("synthetic")) as observe:
            with self.assertRaises(client.runner.Rejected): client.series(None, None, (), 3)
            self.assertEqual(observe.call_count, 1)
        with patch.object(client, "observe", side_effect=[{"v": 1}, {"v": 2}, {"v": 1}]) as observe:
            with self.assertRaises(client.runner.Rejected): client.series(None, None, (), 3)
            self.assertEqual(observe.call_count, 2)

    def test_report_roots_reject_overlap_existing_files_and_symlinks(self):
        with tempfile.TemporaryDirectory(prefix="synthetic-cold-paths-") as directory:
            root = Path(directory).resolve()
            source, build, work = (root / name for name in ("source", "build", "work"))
            for p in (source, build, work): p.mkdir()
            report = root / "report.json"
            self.assertEqual(client.report_paths(source, build, work, report), (source, build, work, report))
            for paths in ((source, source, work, report), (source, build, source, report),
                          (source, build, root, report), (source, build, work, work / "report.json"),
                          (source, build, work, source / "report.json")):
                with self.subTest(paths=paths), self.assertRaises(client.runner.Rejected): client.report_paths(*paths)
            report.symlink_to(root / "missing")
            with self.assertRaises(client.runner.Rejected): client.report_paths(source, build, work, report)
            report.unlink()
            report.write_bytes(b"synthetic report")
            with self.assertRaises(client.runner.Rejected): client.report_paths(source, build, work, report)
            self.assertEqual(report.read_bytes(), b"synthetic report")

    def test_public_report_privacy_and_exclusive_creation(self):
        forbidden = (Path("/synthetic/source"), Path("/synthetic/build"), Path("/synthetic/work"))
        for path in forbidden:
            with self.assertRaises(client.runner.Rejected): client.public_report({"field": str(path)}, forbidden)
        self.assertEqual(client.public_report({"result": True}, forbidden), {"result": True})
        self.assertNotIn("synthetic private path", client.runner.refusal_class(OSError("synthetic private path")))
        with tempfile.TemporaryDirectory(prefix="synthetic-cold-exclusive-") as directory:
            report = Path(directory) / "report.json"
            client.runner.write_report(report, {"v": 1})
            before = report.read_bytes()
            with self.assertRaises(FileExistsError): client.runner.write_report(report, {"v": 2})
            self.assertEqual(report.read_bytes(), before)

    def test_cli_rechecks_inputs_and_never_reports_success_after_drift_or_failure(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools/engineering"))
        import check_chronik_cold_rebuild as gate
        for drift in (None, "binary", "source", "build", "gate", "runtime"):
            with self.subTest(drift=drift), tempfile.TemporaryDirectory(prefix="synthetic-cold-cli-") as directory, ExitStack() as stack:
                root = Path(directory).resolve()
                source, build, work = (root / p for p in ("source", "build", "work"))
                for p in (source, build, work): p.mkdir()
                report = root / "report.json"
                argv = ["cold_rebuild_service.py", "--source-root", str(source), "--build-root", str(build),
                        "--work-parent", str(work), "--report", str(report), "--repetitions", "3"]
                stack.enter_context(patch.object(sys, "argv", argv))
                stack.enter_context(patch.object(gate, "validate", side_effect=[None, client.runner.Rejected("synthetic gate")] if drift == "gate" else None))
                stack.enter_context(patch.object(gate, "input_binding", side_effect=["bound", "drift"] if drift == "source" else ["bound", "bound"]))
                stack.enter_context(patch.object(client.runner, "bind_build", side_effect=["bound", "drift"] if drift == "build" else ["bound", "bound"]))
                stack.enter_context(patch.object(client.runner, "regular", side_effect=[b"daemon", b"native", b"changed" if drift == "binary" else b"daemon", b"native"]))
                stack.enter_context(patch.object(client.runner, "execute", return_value=("synthetic", "")))
                stack.enter_context(patch.object(client.alp, "templates", return_value=(b"synthetic",) * 4))
                series = stack.enter_context(patch.object(client, "series", return_value={"synthetic": True},
                    side_effect=client.runner.Rejected("synthetic runtime") if drift == "runtime" else None))
                write = stack.enter_context(patch.object(client.runner, "write_report"))
                if drift is None:
                    client.main()
                    self.assertEqual(write.call_count, 1)
                    payload = write.call_args.args[1]
                    self.assertEqual(payload["fresh_daemon_processes"], 9)
                    self.assertEqual(payload["template_assertions"], 4)
                    self.assertEqual(payload["knowledge_status"], "Observed")
                else:
                    with self.assertRaises(client.runner.Rejected): client.main()
                    self.assertEqual(write.call_count, 0)
                self.assertEqual(series.call_count, 1)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Self-test the bounded mainnet passive smoke without using the network."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).with_name("run_passive_smoke.py")
SPEC = importlib.util.spec_from_file_location("mainnet_passive_smoke", MODULE_PATH)
SMOKE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SMOKE
SPEC.loader.exec_module(SMOKE)


def valid_snapshot() -> dict:
    return {
        "chain": "main",
        "checkpoint_height": SMOKE.STOP_HEIGHT,
        "genesis": SMOKE.EXPECTED_GENESIS,
        "blockhash": SMOKE.EXPECTED_CHECKPOINT_HASH,
        "raw_header": "2" * 160,
        "chainwork": "3" * 64,
        "median_time": SMOKE.EXPECTED_MEDIAN_TIME,
    }


class FakeBackend:
    def __init__(self, work_root: Path, snapshots: list[dict] | None = None,
                 failure: SMOKE.SmokeFailure | None = None,
                 failure_role: str | None = None) -> None:
        self.work_root = work_root
        self.snapshots = list(snapshots or [valid_snapshot(), valid_snapshot()])
        self.failure = failure
        self.failure_role = failure_role
        self.calls: list[str] = []

    def fetch_public_pair(self, specs):
        self.calls.extend((
            f"start-public-{specs[0].role}",
            f"start-public-{specs[1].role}",
            "joint-public-ready",
        ))
        if self.failure:
            raise SMOKE.SmokeFailure(
                self.failure.result, self.failure.reason_code,
                self.failure_role or "harness",
            )
        self.calls.extend((
            f"exit-public-{specs[0].role}",
            f"exit-public-{specs[1].role}",
        ))
        return 101, 102

    def inspect(self, spec):
        self.calls.append(f"inspect-{spec.role}")
        return self.snapshots.pop(0), 102 + len(self.calls)

    def cleanup(self):
        self.calls.append("cleanup")
        return True


class SmokeContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.baseline = self.root / "baseline-bitcoind"
        self.candidate = self.root / "candidate-bitcoind"
        self.baseline.write_bytes(b"baseline")
        self.candidate.write_bytes(b"candidate")
        self.baseline.chmod(0o755)
        self.candidate.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def args(self):
        return type("Args", (), {
            "baseline_bitcoind": str(self.baseline),
            "candidate_bitcoind": str(self.candidate),
            "work_root": str(self.root / "work"),
            "report": str(self.root / "report.json"),
        })()

    def test_exact_scope_and_closed_rpc_allowlist(self):
        self.assertEqual(SMOKE.SCENARIO_ID, "mixed-node-coexistence")
        self.assertEqual(
            SMOKE.PROFILE, "mainnet-passive-joint-ready-current-era-smoke"
        )
        self.assertEqual(SMOKE.SCHEMA, "ergon-mainnet-passive-smoke/v3")
        self.assertEqual(SMOKE.ROLES, ("baseline", "candidate"))
        self.assertEqual(
            SMOKE.FAILURE_ROLES,
            {"baseline", "candidate", "comparison", "harness"},
        )
        self.assertEqual(SMOKE.STOP_HEIGHT, 250000)
        self.assertEqual(
            SMOKE.EXPECTED_CHECKPOINT_HASH,
            "00000000000000403f540557916c604251d03e9816da37a605036c6a6a0acc9a",
        )
        self.assertEqual(SMOKE.EXPECTED_MEDIAN_TIME, 1763660224)
        self.assertEqual(SMOKE.LAST_LEGACY_ACTIVATION_EMA_TIME, 1659182400)
        self.assertGreater(
            SMOKE.EXPECTED_MEDIAN_TIME,
            SMOKE.LAST_LEGACY_ACTIVATION_EMA_TIME,
        )
        self.assertEqual(SMOKE.PHASE_TIMEOUT_SECONDS, 1800)
        self.assertEqual(SMOKE.POLL_SECONDS, 1.0)
        self.assertEqual(SMOKE.DISK_LIMIT_BYTES, 2 * 1024 * 1024 * 1024)
        self.assertEqual(
            SMOKE.RPC_METHODS,
            {
                "getblockchaininfo",
                "getblockhash",
                "getblockheader",
                "getnetworkinfo",
                "stop",
            },
        )
        forbidden = (
            "addnode", "generatetoaddress", "invalidateblock", "sendrawtransaction",
            "submitblock", "walletpassphrase",
        )
        source = MODULE_PATH.read_text(encoding="utf-8")
        for method in forbidden:
            self.assertNotIn(f'"{method}"', source)

    def test_node_profiles_close_economic_and_network_surfaces(self):
        common = set(SMOKE.COMMON_ARGS)
        self.assertTrue({
            "-disablewallet=1", "-blocksonly=1",
            "-persistmempool=0", "-discover=0", "-listenonion=0",
            "-upnp=0", "-assumevalid=0", "-rpcbind=127.0.0.1",
            "-rpcallowip=127.0.0.1",
        }.issubset(common))
        self.assertNotIn("-main", common)
        self.assertIn("-listen=0", SMOKE.PUBLIC_ARGS)
        self.assertIn("-connect=0", SMOKE.OFFLINE_ARGS)
        for role, binary in (
            ("baseline", self.baseline), ("candidate", self.candidate)
        ):
            spec = SMOKE.NodeSpec(
                role, binary, self.root / role, 20001, 20002,
                self.root / f"runtime-{role}"
            )
            public = SMOKE.node_args(spec, "public")
            self.assertIn("-listen=0", public)
            self.assertIn("-dnsseed=1", public)
            self.assertIn("-forcednsseed=1", public)
            self.assertIn(f"-stopatheight={SMOKE.STOP_HEIGHT}", public)
            offline = SMOKE.node_args(spec, "offline")
            self.assertIn("-connect=0", offline)
            self.assertIn("-dnsseed=0", offline)
        source = MODULE_PATH.read_text(encoding="utf-8")
        for removed in (
            '"legacy-server"', '"candidate-client"', "-minimumchainwork",
            "-maxtipage", "baseline-role-loopback-only",
        ):
            self.assertNotIn(removed, source)

    def test_success_uses_exact_lifecycle_and_sanitized_report(self):
        holder = {}

        def factory(root):
            holder["backend"] = FakeBackend(root)
            return holder["backend"]

        with mock.patch.object(SMOKE, "reserve_ports", return_value=list(range(20000, 20008))):
            code = SMOKE.run(self.args(), backend_factory=factory)
        self.assertEqual(code, 0)
        self.assertEqual(
            holder["backend"].calls,
            [
                "start-public-baseline",
                "start-public-candidate",
                "joint-public-ready",
                "exit-public-baseline",
                "exit-public-candidate",
                "inspect-baseline",
                "inspect-candidate",
                "cleanup",
            ],
        )
        report = json.loads((self.root / "report.json").read_text())
        self.assertEqual(report["result"], "success")
        self.assertNotIn("failure_role", report)
        self.assertEqual(report["scenario_id"], "mixed-node-coexistence")
        self.assertEqual(
            report["profile"], "mainnet-passive-joint-ready-current-era-smoke"
        )
        self.assertEqual(report["knowledge_status"], "Observed")
        self.assertEqual(report["evidence_ceiling"], "assembled_runtime")
        self.assertEqual(report["claims"], {
            "complete_validation_through_fixed_checkpoint": True,
            "current_tip_agreement": "not_claimed",
            "deterministic_build": "not_claimed",
            "full_historical_replay": "not_claimed",
            "independent_public_prefix_acquisition": True,
            "mainnet_coexistence": "not_claimed",
            "operator_binary_parity": "not_claimed",
            "peer_diversity": "not_claimed",
            "simultaneous_public_readiness_observed": True,
            "sustained_operation": "not_claimed",
        })
        self.assertEqual(
            report["scope"]["network_source_by_role"],
            {"baseline": "public-mainnet", "candidate": "public-mainnet"},
        )
        self.assertTrue(report["observations"]["candidate_public_prefix_acquired"])
        self.assertTrue(
            report["observations"]["simultaneous_public_readiness_observed"]
        )
        self.assertEqual(
            report["observations"]["shared_checkpoint"], valid_snapshot()
        )
        self.assertIn(
            "Concurrent peer connections, concurrent public traffic, and any minimum overlap duration are not established.",
            report["limitations"],
        )
        encoded = json.dumps(report)
        for forbidden in (str(self.root), "127.0.0.1", "20000", "baseline-bitcoind"):
            self.assertNotIn(forbidden, encoded)
        self.assertFalse((self.root / "work").exists())

    def test_snapshot_difference_is_a_contradiction(self):
        changed = valid_snapshot()
        changed["chainwork"] = "9" * 64

        def factory(root):
            return FakeBackend(root, [valid_snapshot(), changed])

        with mock.patch.object(SMOKE, "reserve_ports", return_value=list(range(21000, 21008))):
            code = SMOKE.run(self.args(), backend_factory=factory)
        self.assertEqual(code, 3)
        report = json.loads((self.root / "report.json").read_text())
        self.assertEqual(report["result"], "contradiction")
        self.assertEqual(report["reason_code"], "snapshot-mismatch")
        self.assertEqual(report["failure_role"], "comparison")
        self.assertNotIn("observations", report)

    def test_network_failure_is_inconclusive(self):
        failure = SMOKE.SmokeFailure("inconclusive", "timeout")

        holder = {}

        def factory(root):
            holder["backend"] = FakeBackend(
                root, failure=failure, failure_role="candidate"
            )
            return holder["backend"]

        with mock.patch.object(SMOKE, "reserve_ports", return_value=list(range(22000, 22008))):
            code = SMOKE.run(self.args(), backend_factory=factory)
        self.assertEqual(code, 2)
        self.assertEqual(
            holder["backend"].calls,
            [
                "start-public-baseline", "start-public-candidate",
                "joint-public-ready", "cleanup",
            ],
        )
        report = json.loads((self.root / "report.json").read_text())
        self.assertEqual(report["result"], "inconclusive")
        self.assertEqual(report["knowledge_status"], "Open Question")
        self.assertEqual(report["failure_role"], "candidate")

        args = self.args()
        args.work_root = str(self.root / "work-baseline")
        args.report = str(self.root / "report-baseline.json")

        def baseline_factory(root):
            return FakeBackend(root, failure=failure, failure_role="baseline")

        with mock.patch.object(SMOKE, "reserve_ports", return_value=list(range(22100, 22108))):
            code = SMOKE.run(args, backend_factory=baseline_factory)
        report = json.loads(Path(args.report).read_text())
        self.assertEqual(code, 2)
        self.assertEqual(report["failure_role"], "baseline")

        with self.assertRaises(ValueError):
            SMOKE.SmokeFailure("inconclusive", "timeout", "unknown")
        with self.assertRaises(ValueError):
            SMOKE.SmokeFailure("contradiction", "timeout", "comparison")

    def test_cleanup_failure_emits_no_report(self):
        class DirtyBackend(FakeBackend):
            def cleanup(self):
                return False

        with mock.patch.object(SMOKE, "reserve_ports", return_value=list(range(23000, 23008))):
            code = SMOKE.run(self.args(), backend_factory=DirtyBackend)
        self.assertEqual(code, 1)
        self.assertFalse((self.root / "report.json").exists())

    def test_binary_and_report_aliases_fail_before_execution(self):
        args = self.args()
        args.candidate_bitcoind = str(self.baseline)
        with self.assertRaises(SMOKE.SmokeFailure) as raised:
            SMOKE.run(args, backend_factory=FakeBackend)
        self.assertEqual(raised.exception.reason_code, "binary-input-invalid")
        self.assertEqual(raised.exception.failure_role, "harness")
        args = self.args()
        args.report = str(self.root / "work" / "report.json")
        with self.assertRaises(SMOKE.SmokeFailure) as raised:
            SMOKE.run(args, backend_factory=FakeBackend)
        self.assertEqual(raised.exception.reason_code, "report-contract-invalid")
        self.assertEqual(raised.exception.failure_role, "harness")

    def test_port_and_process_aliases_fail_closed(self):
        work = self.root / "alias-work"
        work.mkdir()
        with mock.patch.object(SMOKE, "reserve_ports", return_value=[24000] * 8):
            with self.assertRaises(SMOKE.SmokeFailure) as raised:
                SMOKE.execute_scenario(
                    work, self.baseline, self.candidate, FakeBackend(work)
                )
        self.assertEqual(raised.exception.reason_code, "port-alias")

        backend = SMOKE.SystemBackend(work)
        process = mock.Mock(pid=12345)
        node = SMOKE.NodeProcess(
            SMOKE.NodeSpec(
                "baseline", self.baseline, work / "datadir",
                1, 2, work / "runtime"
            ),
            process,
        )
        backend._record(node)
        with self.assertRaises(SMOKE.SmokeFailure) as raised:
            backend._record(node)
        self.assertEqual(raised.exception.reason_code, "process-alias")

    def test_rpc_rejects_unknown_method_before_io(self):
        spec = SMOKE.NodeSpec(
            role="baseline",
            binary=self.baseline,
            datadir=self.root / "datadir",
            rpc_port=1,
            p2p_port=2,
            runtime_root=self.root / "runtime",
        )
        with self.assertRaises(SMOKE.SmokeFailure) as raised:
            SMOKE.rpc(spec, "sendrawtransaction", ["00"])
        self.assertEqual(raised.exception.reason_code, "rpc-contract-invalid")

    def test_joint_ready_and_early_exit_are_fail_closed(self):
        nodes = tuple(
            SMOKE.NodeProcess(
                SMOKE.NodeSpec(
                    role, binary, self.root / role, 1, 2,
                    self.root / f"runtime-{role}",
                ),
                mock.Mock(pid=100 + index),
            )
            for index, (role, binary) in enumerate((
                ("baseline", self.baseline), ("candidate", self.candidate)
            ))
        )
        for node in nodes:
            node.process.poll.return_value = None
        network = {
            "networkactive": True,
            "localrelay": False,
            "localaddresses": [],
        }
        backend = SMOKE.SystemBackend(self.root)
        with mock.patch.object(SMOKE, "start_node", side_effect=nodes) as start, \
                mock.patch.object(SMOKE, "wait_for_joint_ready") as joint, \
                mock.patch.object(SMOKE, "wait_for_bounded_exits") as bounded, \
                mock.patch.object(SMOKE.time, "monotonic", return_value=10):
            joint.side_effect = lambda started, _root, _deadline: self.assertEqual(
                (start.call_count, started), (2, nodes)
            )
            self.assertEqual(
                backend.fetch_public_pair(tuple(node.spec for node in nodes)),
                (100, 101),
            )
        self.assertEqual(joint.call_args.args[:2], (nodes, self.root))
        self.assertEqual(joint.call_args.args[2], bounded.call_args.args[2])
        self.assertEqual(joint.call_args.args[2], 10 + SMOKE.PHASE_TIMEOUT_SECONDS)

        deadline = SMOKE.time.monotonic() + SMOKE.PHASE_TIMEOUT_SECONDS
        with mock.patch.object(SMOKE, "directory_bytes", return_value=0), \
                mock.patch.object(SMOKE, "rpc", return_value=network):
            SMOKE.wait_for_joint_ready(nodes, self.root, deadline)

        nodes[0].process.poll.return_value = 0
        with mock.patch.object(SMOKE, "directory_bytes", return_value=0), \
                mock.patch.object(SMOKE, "rpc") as rpc_call:
            with self.assertRaises(SMOKE.SmokeFailure) as raised:
                SMOKE.wait_for_joint_ready(nodes, self.root, deadline)
        self.assertEqual(raised.exception.reason_code, "node-exited-before-rpc")
        self.assertEqual(raised.exception.failure_role, "baseline")
        rpc_call.assert_not_called()

        nodes[0].process.poll.return_value = None
        leaking = dict(network, localrelay=True)
        with mock.patch.object(SMOKE, "directory_bytes", return_value=0), \
                mock.patch.object(SMOKE, "rpc", return_value=leaking):
            with self.assertRaises(SMOKE.SmokeFailure) as raised:
                SMOKE.wait_for_joint_ready(nodes, self.root, deadline)
        self.assertEqual(raised.exception.reason_code, "network-surface-open")

        with mock.patch.object(
            SMOKE, "directory_bytes", return_value=SMOKE.DISK_LIMIT_BYTES + 1
        ):
            with self.assertRaises(SMOKE.SmokeFailure) as raised:
                SMOKE.wait_for_joint_ready(nodes, self.root, deadline)
        self.assertEqual(raised.exception.reason_code, "disk-limit-exceeded")

        for node in nodes:
            node.process.poll.side_effect = [None, 0]
        with mock.patch.object(SMOKE, "directory_bytes", return_value=0), \
                mock.patch.object(SMOKE, "rpc", return_value=network), \
                mock.patch.object(SMOKE.time, "sleep"):
            SMOKE.wait_for_bounded_exits(nodes, self.root, deadline)

    def test_offline_inspection_process_is_tracked_before_failure(self):
        work = self.root / "inspection-work"
        work.mkdir()
        spec = SMOKE.NodeSpec(
            "baseline", self.baseline, self.root / "datadir",
            1, 2, self.root / "runtime",
        )
        process = mock.Mock(pid=45678)
        process.poll.return_value = None
        node = SMOKE.NodeProcess(spec, process)
        backend = SMOKE.SystemBackend(work)
        failure = SMOKE.SmokeFailure("inconclusive", "timeout")
        with mock.patch.object(SMOKE, "start_node", return_value=node), \
                mock.patch.object(SMOKE, "wait_for_rpc", side_effect=failure), \
                mock.patch.object(SMOKE, "stop_node", return_value=False):
            with self.assertRaises(SMOKE.SmokeFailure):
                backend.inspect(spec)
            self.assertIn(node, backend.nodes)
            self.assertFalse(backend.cleanup())

    def test_snapshot_fail_closed_oracles(self):
        base_chain = {
            "chain": "main",
            "blocks": SMOKE.STOP_HEIGHT,
            "headers": SMOKE.STOP_HEIGHT + 1,
            "initialblockdownload": True,
        }
        def side_effect(_spec, method, params=None):
            values = {
                "getblockchaininfo": base_chain,
                "getblockhash": (
                    SMOKE.EXPECTED_GENESIS if params == [0]
                    else SMOKE.EXPECTED_CHECKPOINT_HASH
                ),
                "getblockheader": "2" * 160 if params and params[-1] is False else {
                    "height": SMOKE.STOP_HEIGHT,
                    "hash": SMOKE.EXPECTED_CHECKPOINT_HASH,
                    "mediantime": SMOKE.EXPECTED_MEDIAN_TIME,
                    "chainwork": "3" * 64,
                },
            }
            return values[method]

        spec = SMOKE.NodeSpec("baseline", self.baseline, self.root, 1, 2, self.root)
        with mock.patch.object(SMOKE, "rpc", side_effect=side_effect):
            self.assertEqual(SMOKE.snapshot(spec), valid_snapshot())
        for field, value, reason in (
            ("chain", "test", "mainnet-chain-mismatch"),
            ("blocks", 287, "bounded-prefix-incomplete"),
            ("blocks", SMOKE.MAX_OBSERVED_HEIGHT + 1,
             "bounded-prefix-incomplete"),
        ):
            changed = dict(base_chain)
            changed[field] = value

            def changed_rpc(_spec, method, params=None, changed=changed):
                if method == "getblockchaininfo":
                    return changed
                return side_effect(_spec, method, params)

            with mock.patch.object(SMOKE, "rpc", side_effect=changed_rpc):
                with self.assertRaises(SMOKE.SmokeFailure) as raised:
                    SMOKE.snapshot(spec)
            self.assertEqual(raised.exception.reason_code, reason)
            if field == "blocks":
                self.assertEqual(raised.exception.result, "inconclusive")

        def wrong_genesis(_spec, method, params=None):
            if method == "getblockhash" and params == [0]:
                return "f" * 64
            return side_effect(_spec, method, params)

        with mock.patch.object(SMOKE, "rpc", side_effect=wrong_genesis):
            with self.assertRaises(SMOKE.SmokeFailure) as raised:
                SMOKE.snapshot(spec)
        self.assertEqual(raised.exception.reason_code, "genesis-mismatch")

        def wrong_checkpoint(_spec, method, params=None):
            if method == "getblockhash" and params == [SMOKE.STOP_HEIGHT]:
                return "f" * 64
            return side_effect(_spec, method, params)

        with mock.patch.object(SMOKE, "rpc", side_effect=wrong_checkpoint):
            with self.assertRaises(SMOKE.SmokeFailure) as raised:
                SMOKE.snapshot(spec)
        self.assertEqual(raised.exception.reason_code, "checkpoint-hash-mismatch")

        def wrong_median_time(_spec, method, params=None):
            value = side_effect(_spec, method, params)
            if method == "getblockheader" and params and params[-1] is True:
                value = dict(value, mediantime=SMOKE.EXPECTED_MEDIAN_TIME - 1)
            return value

        with mock.patch.object(SMOKE, "rpc", side_effect=wrong_median_time):
            with self.assertRaises(SMOKE.SmokeFailure) as raised:
                SMOKE.snapshot(spec)
        self.assertEqual(
            raised.exception.reason_code, "checkpoint-median-time-mismatch"
        )

        def leaking_header(_spec, method, params=None):
            if method == "getblockheader" and params and params[-1] is False:
                return "/private/tmp/peer-output"
            return side_effect(_spec, method, params)

        with mock.patch.object(SMOKE, "rpc", side_effect=leaking_header):
            with self.assertRaises(SMOKE.SmokeFailure) as raised:
                SMOKE.snapshot(spec)
        self.assertEqual(raised.exception.reason_code, "rpc-contract-invalid")

    def test_child_environment_is_closed(self):
        runtime = self.root / "runtime-env"
        with mock.patch.dict("os.environ", {"SECRET_TOKEN": "secret"}, clear=False):
            env = SMOKE.child_env(runtime)
        self.assertEqual(
            set(env),
            {"HOME", "LANG", "LC_ALL", "NO_COLOR", "PATH", "TERM", "TMPDIR", "TZ"},
        )
        self.assertNotIn("SECRET_TOKEN", env)
        self.assertNotEqual(env["HOME"], env["TMPDIR"])


if __name__ == "__main__":
    unittest.main()

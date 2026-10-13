# SPDX-License-Identifier: MIT
"""Synthetic runner failure tests; not native restart or plugin evidence."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import plugin_restart as restart


class Contract(unittest.TestCase):
    def run_observation(self, mutate=None, repetitions=2):
        with tempfile.TemporaryDirectory(prefix="synthetic-plugin-restart-") as name:
            work = Path(name).resolve()
            phases = tuple((role, case, 3) for role, case, _ in restart.PHASES)
            calls = []
            def execute(command, parent, forbidden):
                root = Path(command[-1].split("=", 1)[1])
                self.assertIn(root, forbidden)
                role, case, n = phases[len(calls) % len(phases)]
                if role == "seed-v1":
                    (root / "corpus.bin").write_bytes(b"synthetic native corpus")
                    (root / "index-v1").mkdir()
                    (root / "index-v1/ergon-identity.json").write_bytes(b"synthetic bound identity")
                calls.append(role)
                out = "Running 1 test case...\n"
                if role in restart.POSITIVE:
                    out += "Restart view: " + "a" * 64 + "\nRestart source: " + "b" * 64 + "\n"
                err = f"Fixture start: {restart.PREFIX}{case}\nFixture done: {restart.PREFIX}{case}\n  {n} assertions out of {n} passed\n"
                return mutate(role, root, out, err) if mutate else (out, err)
            try:
                with patch.object(restart, "PHASES", phases), patch.object(restart.runner, "execute", side_effect=execute):
                    result = restart.observe(Path("synthetic-binary"), work, (), repetitions)
                return result, calls
            finally:
                self.assertEqual(list(work.iterdir()), [], "cleanup must precede success or failure")

    def test_exact_phase_order_and_stability(self):
        result, calls = self.run_observation()
        self.assertEqual(calls, [role for role, _, _ in restart.PHASES] * 2)
        self.assertEqual(result["fresh_processes"], 16)
        self.assertEqual(result["per_process_timeout_seconds"], 60)
        self.assertEqual(len(result["phases"]), 8)
        self.assertNotIn("synthetic-plugin-restart-", restart.runner.canonical(result).decode())

    def test_view_and_native_source_drift_fail(self):
        for label in ("Restart view: ", "Restart source: "):
            def mutate(role, root, out, err):
                if role == "reopen-v1":
                    out = out.replace(label + ("a" if "view" in label else "b") * 64, label + "c" * 64)
                return out, err
            with self.assertRaises(restart.runner.Rejected):
                self.run_observation(mutate)

    def test_missing_duplicate_malformed_and_negative_commitments(self):
        for change in (lambda s: s.replace("Restart view: " + "a" * 64 + "\n", ""),
                       lambda s: s + "Restart view: " + "a" * 64 + "\n",
                       lambda s: s.replace("a" * 64, "A" * 64)):
            with self.assertRaises(restart.runner.Rejected):
                self.run_observation(lambda role, root, out, err: (change(out), err))
        with self.assertRaises(restart.runner.Rejected):
            self.run_observation(lambda role, root, out, err: (
                out + "Restart view: " + "a" * 64 + "\n" if role == "refuse-v2" else out, err))

    def test_corpus_and_identity_rewrite_fail(self):
        for path in ("corpus.bin", "index-v1/ergon-identity.json"):
            def mutate(role, root, out, err):
                if role == "refuse-v2":
                    (root / path).write_bytes(b"synthetic drift")
                return out, err
            with self.assertRaises(restart.runner.Rejected):
                self.run_observation(mutate)

    def test_missing_join_and_original_index_failure_fail(self):
        for path in ("index-v1/probe.lock", "index-v1/probe.failed"):
            def mutate(role, root, out, err):
                if role == "refuse-v2":
                    (root / path).write_bytes(b"synthetic")
                return out, err
            with self.assertRaises(restart.runner.Rejected):
                self.run_observation(mutate)

    def test_wrong_case_and_assertion_count_fail(self):
        for change in (lambda e: e.replace("3 assertions out of 3", "2 assertions out of 2"),
                       lambda e: e.replace("seed_v1", "unknown")):
            with self.assertRaises(restart.runner.Rejected):
                self.run_observation(lambda role, root, out, err: (out, change(err)))

    def test_failure_does_not_continue_and_cleans(self):
        def mutate(role, root, out, err):
            if role == "refuse-v2":
                raise TimeoutError("synthetic timeout")
            return out, err
        with self.assertRaises(TimeoutError):
            self.run_observation(mutate)

    def test_bounded_repetitions(self):
        for n in (0, 21):
            with self.assertRaises(restart.runner.Rejected):
                self.run_observation(repetitions=n)


if __name__ == "__main__":
    unittest.main()

# SPDX-License-Identifier: MIT
"""Synthetic scheduling/refusal tests, never native execution evidence."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import startup_metadata as client


class Contract(unittest.TestCase):
    def streams(self, command, work, forbidden):
        case = command[1].split("=", 1)[1]
        name = case.rsplit("/", 1)[1]
        count = dict(client.CASES)[name]
        return (f"Running 1 test case...\nFixture start: {case}\nFixture done: {case}\n",
                f"  {count} assertions out of {count} passed\n")

    def test_exact_cases_counts_three_repetitions_and_stability(self):
        with patch.object(client.runner, "execute", side_effect=self.streams) as execute:
            rows = client.controls(Path("synthetic"), Path("synthetic-work"), (), 3)
        self.assertEqual(len(rows), 9)
        self.assertEqual(sum(row["passing_assertions"] for row in rows), 60)
        self.assertEqual(execute.call_count, 9)

    def test_missing_wrong_duplicate_and_cross_run_instability_refused(self):
        bad = (("Running 1 test case...\n", ""),
               ("Running 1 test case...\n", "  8 assertions out of 8 passed\n"),
               ("Running 1 test case...\n", "  9 assertions out of 9 passed\n  9 assertions out of 9 passed\n"))
        for streams in bad:
            with self.subTest(streams=streams), patch.object(client.runner, "execute", return_value=streams), self.assertRaises(client.runner.Rejected):
                client.controls(Path("synthetic"), None, (), 1)
        calls = 0
        def unstable(*args):
            nonlocal calls
            calls += 1
            out, err = self.streams(*args)
            return (("prefix\n" if calls > 3 else "") + out, err)
        with patch.object(client.runner, "execute", side_effect=unstable), self.assertRaises(client.runner.Rejected):
            client.controls(Path("synthetic"), None, (), 2)

    def test_first_failure_has_no_automatic_retry_and_timeout_is_retained(self):
        with patch.object(client.runner, "execute", side_effect=client.runner.Rejected("synthetic")) as execute, self.assertRaises(client.runner.Rejected):
            client.controls(Path("synthetic"), None, (), 3)
        self.assertEqual(execute.call_count, 1)
        with patch.object(client.runner, "execute", side_effect=client.subprocess.TimeoutExpired("synthetic", 60)) as execute, self.assertRaises(client.subprocess.TimeoutExpired):
            client.controls(Path("synthetic"), None, (), 3)
        self.assertEqual(execute.call_count, 1)

    def test_finite_repetitions(self):
        for value in (0, 11, True, -1):
            with self.subTest(value=value), self.assertRaises(client.runner.Rejected):
                client.controls(None, None, (), value)

    def test_report_paths_and_privacy(self):
        with tempfile.TemporaryDirectory(prefix="startup-metadata-self-test-") as directory:
            base = Path(directory).resolve()
            source, build, work, report_parent = (base / name for name in ("source", "build", "work", "report"))
            for path in (source, build, work, report_parent): path.mkdir()
            report = report_parent / "result.json"
            self.assertEqual(client.report_paths(source, build, work, report), (source, build, work, report))
            for path in (source, build, work):
                with self.assertRaises(client.runner.Rejected):
                    client.public_report({"field": str(path)}, (source, build, work))
            client.runner.write_report(report, {"synthetic": True})
            original = report.read_bytes()
            with self.assertRaises((client.runner.Rejected, FileExistsError)):
                client.report_paths(source, build, work, report)
            self.assertEqual(report.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic standard-library failure-path checks; no trusted node required."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import run_repetitions as runner

class Contract(unittest.TestCase):
    def streams(self, cases=runner.STARTUP, assertions=593):
        out = "Running " + str(len(cases)) + " test cases...\n"
        err = "".join("Fixture start: " + case + "\nFixture done: " + case + "\n" for case in cases)
        err += "  " + str(assertions) + " assertions out of " + str(assertions) + " passed\n"
        return out, err

    def rejected(self, streams):
        with self.assertRaises(runner.Rejected):
            runner.parse_streams(streams, runner.STARTUP, 593)

    def test_exact_names_counts_and_disjoint_groups(self):
        for _, cases, assertions in runner.GROUPS:
            result = runner.parse_streams(self.streams(cases, assertions), cases, assertions)
            self.assertEqual(result["cases"], list(cases))
        self.assertEqual(sum(len(c) for _, c, _ in runner.GROUPS), 37)
        self.assertEqual(len({n for _, c, _ in runner.GROUPS for n in c}), 37)
        self.assertEqual(sum(a for _, _, a in runner.GROUPS), 2129)
        self.assertEqual(runner.filter_for(runner.STARTUP).count(runner.PREFIX), 1)

    def test_summary_fail_closed(self):
        out, err = self.streams()
        line = "  593 assertions out of 593 passed\n"
        for substitute in ("", line * 2, "  592 assertions out of 592 passed\n",
                           "  593 assertions out of 594 passed\n", "  0593 assertions out of 0593 passed\n",
                           "  NaN assertions out of NaN passed\n", "  593 assertions out of 593 passed extra\n"):
            with self.subTest(summary=substitute):
                self.rejected((out, err.replace(line, substitute)))
        self.rejected((out + line, err))
        self.rejected((out + "  593 assertions out of ", err.replace(line, "593 passed\n")))
        self.rejected((out.replace("3", "2"), err))
        self.rejected((out * 2, err))
        self.rejected((out, err + "  593 assertions out of NaN passed\n"))

    def test_progress_identity_and_failure_rejection(self):
        out, err = self.streams()
        self.rejected((out, err.replace(runner.STARTUP[0], "unexpected", 1)))
        self.rejected((out, err.replace("Fixture done: " + runner.STARTUP[0] + "\n", "")))
        self.rejected((out, err + "Fixture done: " + runner.STARTUP[0] + "\n"))
        self.rejected((out, err + "1 test case failed\n"))

    def test_exclusive_report_and_privacy(self):
        with tempfile.TemporaryDirectory(prefix="ergon-runner-self-") as name:
            report = Path(name) / "record.json"
            result = runner.parse_streams(self.streams(), runner.STARTUP, 593)
            self.assertNotIn(name, runner.canonical(result).decode())
            self.assertNotIn("Fixture start", runner.canonical(result).decode())
            runner.write_report(report, result)
            before = report.read_bytes()
            with self.assertRaises(FileExistsError):
                runner.write_report(report, {"changed": True})
            self.assertEqual(report.read_bytes(), before)
            link = Path(name) / "alias.json"
            link.symlink_to(report)
            with self.assertRaises(FileExistsError):
                runner.write_report(link, {})

    def test_child_environment_cleanup_paths_utf8_exit_and_timeout(self):
        with tempfile.TemporaryDirectory(prefix="ergon-runner-self-") as name:
            work = Path(name).resolve()
            marker = "SYNTHETIC_PARENT_ONLY_0045"
            previous = os.environ.get(marker)
            os.environ[marker] = "must-not-propagate"
            try:
                command = [sys.executable, "-B", "-c",
                           "import os; assert '" + marker + "' not in os.environ; print('ok')"]
                self.assertEqual(runner.execute(command, work, (), timeout=3), ("ok\n", ""))
                for program in ("import os; print(os.environ['TMPDIR'])",
                                "import os, sys; print(os.environ['TMPDIR'], file=sys.stderr)",
                                "print(" + repr(str(work)) + ")",
                                "import sys; sys.stdout.buffer.write(b'\\xff')",
                                "raise SystemExit(7)"):
                    with self.subTest(program=program), self.assertRaises((runner.Rejected, UnicodeError)):
                        runner.execute([sys.executable, "-B", "-c", program], work, (work,), timeout=3)
                    self.assertEqual(list(work.iterdir()), [])
                with self.assertRaises(Exception) as caught:
                    runner.execute([sys.executable, "-B", "-c", "import time; time.sleep(30)"], work, (), timeout=0.1)
                self.assertEqual(type(caught.exception).__name__, "TimeoutExpired")
                self.assertEqual(list(work.iterdir()), [])
            finally:
                if previous is None:
                    del os.environ[marker]
                else:
                    os.environ[marker] = previous

    def test_projection_drift_mode_and_symlink(self):
        with tempfile.TemporaryDirectory(prefix="ergon-runner-self-") as name:
            root = Path(name).resolve()
            source = root / "source"
            source.mkdir()
            entries = []
            for i in range(34):
                path = source / (str(i).zfill(2) + ".txt")
                path.write_bytes(b"synthetic\n")
                entries.append({"path": path.name, "after": {"mode": "100644", "bytes": 10,
                               "sha256": runner.digest(path.read_bytes())}})
            manifest = root / "manifest.json"
            manifest.write_bytes(runner.canonical({"files": entries}))
            runner.source_projection(source, manifest)
            target = source / "00.txt"
            target.write_bytes(b"drift\n")
            with self.assertRaises(runner.Rejected):
                runner.source_projection(source, manifest)
            target.write_bytes(b"synthetic\n")
            target.chmod(0o755)
            with self.assertRaises(runner.Rejected):
                runner.source_projection(source, manifest)
            target.unlink()
            target.symlink_to(source / "01.txt")
            with self.assertRaises(runner.Rejected):
                runner.source_projection(source, manifest)

    def test_synthetic_unfunded_address(self):
        self.assertEqual(runner.mining_address(), "mfWxJ45yp2SFn7UciZyNpvDKrzbhyfKrY8")

    def test_build_binding_mismatch(self):
        with tempfile.TemporaryDirectory(prefix="ergon-runner-self-") as name:
            root = Path(name).resolve()
            source, build = root / "source", root / "build"
            source.mkdir()
            build.mkdir()
            cache = build / "CMakeCache.txt"
            def content(home, observer, link):
                return "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(home) + "\nBUILD_CHRONIK_OBSERVER:BOOL=" + observer + "\nBUILD_CHRONIK_UPSTREAM_LINK:BOOL=" + link + "\n"
            with patch.object(runner, "source_projection", return_value="synthetic"):
                cache.write_text(content(source, "OFF", "OFF"))
                runner.bind_build(source, build, "compiled-out", root / "unused")
                for text in (content(root, "OFF", "OFF"), content(source, "ON", "OFF"), content(source, "OFF", "ON")):
                    cache.write_text(text)
                    with self.assertRaises(runner.Rejected):
                        runner.bind_build(source, build, "compiled-out", root / "unused")
                cache.write_text(content(source, "OFF", "OFF") * 2)
                with self.assertRaises(runner.Rejected):
                    runner.bind_build(source, build, "compiled-out", root / "unused")

    def test_repeat_stability_and_bounds(self):
        first = self.streams(runner.BRIDGE, 166)
        changed = (first[0] + "unexpected change\n", first[1])
        with patch.object(runner, "execute", side_effect=[first, changed]):
            with self.assertRaises(runner.Rejected):
                runner.repeat(Path("synthetic"), Path("unused"), (), 2)
        for repetitions in (0, 21):
            with self.assertRaises(runner.Rejected):
                runner.repeat(Path("synthetic"), Path("unused"), (), repetitions)

if __name__ == "__main__":
    unittest.main()

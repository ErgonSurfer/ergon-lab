#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthetic standard-library failure-path checks; no trusted node required."""
import copy
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
        self.assertEqual(sum(len(c) for _, c, _ in runner.GROUPS), 44)
        self.assertEqual(len({n for _, c, _ in runner.GROUPS for n in c}), 44)
        self.assertEqual(sum(a for _, _, a in runner.GROUPS), 2285)
        self.assertEqual(len(runner.GROUPS), 6)
        self.assertEqual(runner.filter_for(runner.STARTUP).count(runner.PREFIX), 1)

    def test_plugin_profiles_exact_counts_and_single_context_processes(self):
        self.assertEqual(sum(a for _, _, a in runner.PLUGIN_GROUPS), 100)
        for profile, groups, count, assertions, processes in (
                ("plugins", runner.GROUPS + runner.PLUGIN_GROUPS, 48, 2385, 100),
                ("archival", runner.GROUPS + (runner.DISABLED_GROUP,), 45, 2291, 70)):
            streams = [self.streams(cases, n) for _, cases, n in groups for _ in range(10)]
            with patch.object(runner, "execute", side_effect=streams):
                result = runner.repeat(Path("synthetic"), Path("unused"), (), 10, profile)
            self.assertEqual((result["selected_cases"], result["assertions_per_repetition"],
                              result["fresh_processes"]), (count, assertions, processes))
        self.assertTrue(all(len(cases) == 1 for _, cases, _ in runner.PLUGIN_GROUPS))
        out, err = self.streams(runner.PLUGIN_GROUPS[0][1], 55)
        for n in (0, 54, 56):
            with self.assertRaises(runner.Rejected):
                runner.parse_streams((out, err.replace("55", str(n))), runner.PLUGIN_GROUPS[0][1], 55)

    def test_disabled_profile_rejects_enabled_engine_cache(self):
        with tempfile.TemporaryDirectory(prefix="ergon-runner-self-") as name:
            root = Path(name).resolve()
            source, build = root / "source", root / "build"
            source.mkdir()
            build.mkdir()
            (build / "CMakeCache.txt").write_text(
                "CMAKE_HOME_DIRECTORY:INTERNAL=" + str(source) + "\n"
                "BUILD_CHRONIK_OBSERVER:BOOL=OFF\nBUILD_CHRONIK_UPSTREAM_LINK:BOOL=OFF\n"
                "BUILD_CHRONIK_UPSTREAM_PLUGIN_TESTS:BOOL=ON\n")
            with patch.object(runner, "source_projection", return_value="synthetic"), self.assertRaises(runner.Rejected):
                runner.bind_build(source, build, "compiled-out", root / "unused")

    def test_plugin_projection_rejects_inventory_parent_and_preimage_drift(self):
        source_root = Path(__file__).resolve().parents[3]
        names = [r["path"] for r in json.loads((source_root /
            "docs/engineering/chronik/upstream-host-adaptation-v1.json").read_text())["files"]]
        raw = b"synthetic\n"
        identity = {"mode": "100644", "bytes": len(raw), "sha256": runner.digest(raw)}
        host_rows = [dict(path=p, after=identity) for p in names]
        read = {"schema": "ergon-owned-chainstate-reads/v1",
                "public_parent": {"commit": "8fc7aec5a3935533fe7d4e0ccbfdf14b276abc37",
                                   "tree": "0a97f74862c1f7ff1842ce60aa7969837b31a887"},
                "files": [dict(path=p, before=identity if p in names else None, after=identity)
                          for p in sorted(runner.READ_INPUTS)]}
        old_names = set(names) | runner.READ_INPUTS
        plugin = {"schema": "ergon-upstream-plugin-fixtures/v1",
                  "public_parent": {"commit": "eaa73b03ed060af5623eb30ff4a08502d2a73c7a",
                                     "tree": "85ca3e2bd4a0d5017cbad506aa26539f99e38d2b"},
                  "files": [dict(path=p, before=identity if p in old_names else None, after=identity)
                            for p in sorted(runner.plugin_inputs())]}
        with tempfile.TemporaryDirectory(prefix="ergon-projection-self-") as name:
            root = Path(name).resolve()
            source = root / "source"
            source.mkdir()
            for p in old_names | runner.plugin_inputs():
                target = source / p
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            host_path, read_path, plugin_path = (root / p for p in ("host.json", "read.json", "plugin.json"))
            host_path.write_bytes(runner.canonical({"files": host_rows}))
            read_path.write_bytes(runner.canonical(read))
            def run(record):
                plugin_path.write_bytes(runner.canonical(record))
                return runner.source_projection(source, host_path, read_path, plugin_path)
            self.assertEqual(len(run(plugin)), 64)
            for mutate in (lambda r: r["files"].pop(), lambda r: r["files"].reverse(),
                           lambda r: r["public_parent"].update(tree="0" * 40),
                           lambda r: r["files"][0].update(before=None)):
                invalid = copy.deepcopy(plugin)
                mutate(invalid)
                with self.assertRaises(runner.Rejected): run(invalid)
            target = source / "chronik/chronik-lib/src/ergon_plugin_fixture.rs"
            target.write_bytes(b"synthetic drift\n")
            with self.assertRaises(runner.Rejected): run(plugin)

    def test_python_embedding_and_adapter_binding_rejections(self):
        with tempfile.TemporaryDirectory(prefix="ergon-python-bind-self-") as name:
            root = Path(name).resolve()
            source, build = root / "source", root / "build"
            source.mkdir()
            build.mkdir()
            for p in runner.ADAPTER_INPUTS + runner.PLUGIN_ADAPTER_INPUTS + runner.PLUGIN_ENABLED_INPUTS:
                target = source / p
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"synthetic\n")
            executable, library = root / "python", root / "library"
            executable.write_bytes(b"synthetic executable")
            library.write_bytes(b"synthetic library")
            version = "3.12.9"
            embedding = runner.digest((version + "\n" + runner.digest(executable.read_bytes()) + "\n" +
                runner.digest(library.read_bytes()) + "\n").encode())
            adapter = "ergon-archival-adapter/v1\n" + "".join(p + ":" + runner.digest((source / p).read_bytes()) + "\n"
                for p in runner.ADAPTER_INPUTS + runner.PLUGIN_ADAPTER_INPUTS + runner.PLUGIN_ENABLED_INPUTS)
            adapter += "python-profile:" + embedding + "\n"
            ninja = ("CHRONIK_ERGON_PYTHON_VERSION=" + version + " CHRONIK_ERGON_PYTHON_ID=" + embedding +
                     " CHRONIK_ERGON_ADAPTER_ID=" + runner.digest(adapter.encode()) + " --features plugins\n")
            cache = ("CMAKE_HOME_DIRECTORY:INTERNAL=" + str(source) + "\nBUILD_CHRONIK_OBSERVER:BOOL=OFF\n"
                     "BUILD_CHRONIK_UPSTREAM_LINK:BOOL=ON\nBUILD_CHRONIK_UPSTREAM_ARCHIVAL_TESTS:BOOL=ON\n"
                     "BUILD_CHRONIK_UPSTREAM_PLUGIN_TESTS:BOOL=ON\n_Python3_EXECUTABLE:INTERNAL=" + str(executable) +
                     "\n_Python3_LIBRARY_RELEASE:INTERNAL=" + str(library) + "\n")
            (build / "CMakeCache.txt").write_text(cache)
            (build / "build.ninja").write_text(ninja)
            with patch.object(runner, "source_projection", return_value="synthetic"):
                binding = runner.bind_build(source, build, "plugins", root / "unused")
                self.assertEqual(binding["python_embedding"]["embedding_sha256"], embedding)
                self.assertNotIn(str(root), runner.canonical(binding).decode())
                for invalid in (ninja.replace("3.12.9", "3.13.9"), ninja + ninja,
                                ninja.replace(embedding, "0" * 64), ninja.replace("--features plugins", ""),
                                ninja.replace(runner.digest(adapter.encode()), "0" * 64)):
                    (build / "build.ninja").write_text(invalid)
                    with self.assertRaises(runner.Rejected): runner.bind_build(source, build, "plugins", root / "unused")
                (build / "build.ninja").write_text(ninja)
                library.write_bytes(b"synthetic library drift")
                with self.assertRaises(runner.Rejected): runner.bind_build(source, build, "plugins", root / "unused")

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

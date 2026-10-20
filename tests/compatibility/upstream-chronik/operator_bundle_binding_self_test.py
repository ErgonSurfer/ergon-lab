# SPDX-License-Identifier: MIT
"""Offline commitment contract only; no node or Python-plugin execution."""
import os
from pathlib import Path
import tempfile
import unittest
import operator_bundle_binding as bundle


class Contract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="operator-public-contract-")
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / "public_probe.py"
        self.source.write_bytes(b"raise RuntimeError('must never execute during inspection')\n")
        (self.root / "plugins.toml").write_bytes(b"[regtest.plugin.public_probe]\n")

    def tearDown(self):
        self.temp.cleanup()

    def digest(self):
        return bundle.inspect(self.root, "3.12.3", "0" * 64)

    def test_stable_without_execution(self):
        self.assertEqual(self.digest(), self.digest())

    def test_sources_config_and_interpreter_bound(self):
        before = self.digest()
        self.source.write_bytes(b"# changed\n")
        self.assertNotEqual(before, self.digest())
        before = self.digest()
        (self.root / "plugins.toml").write_bytes(b"[regtest.plugin.public_probe]\nclass='Other'\n")
        self.assertNotEqual(before, self.digest())
        self.assertNotEqual(self.digest(), bundle.inspect(self.root, "3.12.4", "0" * 64))
        self.assertNotEqual(self.digest(), bundle.inspect(self.root, "3.12.3", "1" * 64))

    def test_unexpected_input_refused(self):
        for name in ("cache.pyc", "bad-name.py", "3bad.py", "a.b.py"):
            path = self.root / name
            path.write_bytes(b"# synthetic\n")
            with self.assertRaises(ValueError):
                self.digest()
            path.unlink()

    def test_missing_config_refused(self):
        (self.root / "plugins.toml").unlink()
        with self.assertRaises(ValueError):
            self.digest()

    def test_no_modules_refused(self):
        self.source.unlink()
        with self.assertRaises(ValueError):
            self.digest()

    def test_directory_and_root_alias_refused(self):
        path = self.root / "extra.py"
        path.mkdir()
        with self.assertRaises(ValueError):
            self.digest()
        path.rmdir()
        path.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            bundle.inspect(path, "3.12.3", "0" * 64)

    def test_links_and_permissions_refused(self):
        path = self.root / "other.py"
        path.symlink_to(self.source)
        with self.assertRaises(ValueError):
            self.digest()
        path.unlink()
        os.link(self.source, path)
        with self.assertRaises(ValueError):
            self.digest()
        path.unlink()
        self.source.chmod(0o666)
        with self.assertRaises(ValueError):
            self.digest()

    def test_non_text_refused(self):
        for data in (b"\xff", b"a\x00b"):
            self.source.write_bytes(data)
            with self.assertRaises(ValueError):
                self.digest()

    def test_source_and_config_limits(self):
        self.source.write_bytes(b"x" * (bundle.MAX_SOURCE + 1))
        with self.assertRaises(ValueError):
            self.digest()
        self.source.write_bytes(b"# synthetic\n")
        (self.root / "plugins.toml").write_bytes(b"x" * (bundle.MAX_CONFIG + 1))
        with self.assertRaises(ValueError):
            self.digest()

    def test_input_count_limit(self):
        for i in range(bundle.MAX_MODULES):
            (self.root / f"extra_{i}.py").write_bytes(b"# synthetic\n")
        with self.assertRaises(ValueError):
            self.digest()

    def test_total_bytes_limit(self):
        for i in range(4):
            (self.root / f"extra_{i}.py").write_bytes(b"x" * bundle.MAX_SOURCE)
        with self.assertRaises(ValueError):
            self.digest()


if __name__ == "__main__":
    unittest.main()

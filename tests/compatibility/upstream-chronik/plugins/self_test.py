# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Standard-library helper closure/fixture checks, not native engine evidence."""
import hashlib
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / "chronik/chronik-plugin-impl"
HELPERS = {
    "script.py": "89d41426d7466222a5eeded22e5caf980366f198a4909ea0b47e7f0bea24eafd",
    "slp.py": "0452a00bfec362c15d1ae663fcedd38adfd63d73c9eb559f115feebd5b1ad5c3",
}


def load():
    package = types.ModuleType("chronik_plugin")
    package.__path__ = []
    sys.modules[package.__name__] = package
    modules = {}
    for name, path in [("script", BASE / "python/upstream/script.py"),
                       ("slp", BASE / "python/upstream/slp.py"),
                       ("plugin", BASE / "src/plugin.py")]:
        raw = path.read_bytes()
        if name in ("script", "slp"):
            assert hashlib.sha256(raw).hexdigest() == HELPERS[path.name]
        module = types.ModuleType("chronik_plugin." + name)
        sys.modules[module.__name__] = module
        # Same narrow upstream import namespace transform, no replacement
        # script/parser implementation and no native baseline file changes.
        exec(compile(raw.replace(b"test_framework", b"chronik_plugin"),
                     str(path), "exec"), module.__dict__)
        modules[name] = module
    fixture = types.ModuleType("ergon_public_probe")
    exec(compile((ROOT / "tests/compatibility/upstream-chronik/plugins/ergon_public_probe.py").read_bytes(),
                 "public-synthetic-plugin", "exec"), fixture.__dict__)
    modules["fixture"] = fixture
    return modules


MODULES = load()


class Fixture(unittest.TestCase):
    def test_exact_identity_and_metadata(self):
        plugin = MODULES["fixture"].PublicProbePlugin({})
        self.assertEqual(plugin.lokad_id(), b"ERGN")
        self.assertEqual(plugin.version(), "1.0.0")
        tx = types.SimpleNamespace(
            inputs=[types.SimpleNamespace(plugin={"ergon_public_probe": {}})],
            outputs=[types.SimpleNamespace(script=b"\x51", sats=0,
                                          token=types.SimpleNamespace(atoms=1000)),
                     types.SimpleNamespace(script=b"\x6a", sats=0, token=None)])
        output, = plugin.run(tx)
        self.assertEqual(output.idx, 0)
        self.assertEqual(output.groups, b"public")
        self.assertEqual(output.data, [b"synthetic-v1", b"0", b"1", b"1000"])

    def test_failure_variants_are_explicit(self):
        tx = types.SimpleNamespace(inputs=[], outputs=[])
        with self.assertRaisesRegex(ValueError, "public synthetic plugin exception"):
            MODULES["fixture"].ThrowProbePlugin({}).run(tx)
        self.assertEqual(MODULES["fixture"].BadOutputProbePlugin({}).run(tx)[0].idx, 0)
        self.assertEqual(MODULES["fixture"].VersionTwoProbePlugin({}).version(), "2.0.0")


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Fixture)
    for name in ("script", "slp"):
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(MODULES[name]))
    raise SystemExit(not unittest.TextTestRunner().run(suite).wasSuccessful())

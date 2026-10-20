# SPDX-License-Identifier: MIT
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


class Contract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import component
        cls.component = component

    def valid(self):
        return ("\n".join("test " + name + " ... ok" for name in sorted(self.component.UNIT_NAMES)) +
                "\ntest result: ok. 28 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n", "")

    def test_all_units_required(self):
        self.component.unit_result(self.valid())
        raw = self.valid()[0]
        for bad in (raw + raw, raw.replace("28 passed", "27 passed"),
                    raw.replace(sorted(self.component.UNIT_NAMES)[0], "unexpected_case"),
                    raw.replace("0 ignored", "1 ignored"), raw.split("test result:")[0]):
            with self.assertRaises(ValueError): self.component.unit_result((bad, ""))

    def test_strict_marker_and_no_unchecked_setup_output(self):
        self.component.probe_result((self.component.MARKER + "\n", ""))
        for bad in ((self.component.MARKER, ""), ("extra\n" + self.component.MARKER + "\n", ""),
                    (self.component.MARKER + "\n", "warning"), ("", "")):
            with self.assertRaises(ValueError): self.component.probe_result(bad)

    def test_template_shape_order_placeholders_and_stderr(self):
        raw = "\n".join("COMMUNITY_TEMPLATE " + name + " 6a" +
                         ("42" * 32 if name.endswith("_SEND") else "00")
                         for name in self.component.TEMPLATES) + "\n"
        self.assertEqual(tuple(self.component.templates((raw, ""))), self.component.TEMPLATES)
        for bad in ((raw + raw, ""), (raw.replace("42" * 32, "43" * 32), ""),
                    (raw.replace("ALP_GENESIS", "UNKNOWN"), ""), (raw, "warning"), ("", "")):
            with self.assertRaises(ValueError): self.component.templates(bad)

    def test_artifact_name_profile_kind_path_and_uniqueness(self):
        row = {"reason": "compiler-artifact", "executable": "/public/target/unit",
               "target": {"name": "ergon-community-plugins", "kind": ["example"]},
               "profile": {"test": True}}
        raw = (json.dumps(row) + "\n").encode()
        with patch.object(self.component.Path, "resolve", lambda p, **kwargs: p), \
             patch.object(self.component.Path, "is_symlink", return_value=False), \
             patch.object(self.component.runner, "regular", return_value=b"synthetic"):
            self.component.artifact(raw, Path("/public/target"))
            for bad in (raw + raw, b"", raw.replace(b"/public/target/unit", b"/other/unit"),
                        raw.replace(b'"example"', b'"bin"'), raw.replace(b'"test": true', b'"test": false')):
                with self.assertRaises(ValueError): self.component.artifact(bad, Path("/public/target"))


if __name__ == "__main__":
    parser = __import__("argparse").ArgumentParser()
    parser.add_argument("--upstream-source", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    sys.path.insert(0, str(args.upstream_source.resolve(strict=True) / "tests/compatibility/upstream-chronik"))
    unittest.main(argv=[sys.argv[0]])

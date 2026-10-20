# SPDX-License-Identifier: MIT
import unittest
from unittest.mock import patch
from pathlib import Path
import json
import multi_lokad_component as witness


class Contract(unittest.TestCase):
    def valid(self):
        return ("\n".join("test " + witness.UNIT_PREFIX + name + " ... ok" for name in sorted(witness.UNIT_NAMES)) +
                "\ntest result: ok. 11 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n", "")

    def test_exact_cases_and_summary(self):
        witness.unit_result(self.valid())

    def test_missing_duplicate_wrong_case_or_summary_rejected(self):
        valid = self.valid()[0]
        name = sorted(witness.UNIT_NAMES)[0]
        for bad in (valid.replace(name, "wrong_case"), valid.replace("11 passed", "10 passed"),
                    valid.replace("0 failed", "1 failed"), valid + valid,
                    valid.replace(" ... ok", " ... FAILED", 1), valid.split("test result:")[0]):
            with self.assertRaises(ValueError): witness.unit_result((bad, ""))

    def test_probe_requires_exact_marker_and_no_stderr(self):
        witness.probe_result((witness.MARKER + "\n", ""))
        for bad in (("", ""), (witness.MARKER + "\n" + witness.MARKER, ""),
                    (witness.MARKER, "warning"), (witness.MARKER.replace("combined_once=1", "combined_once=2"), "")):
            with self.assertRaises(ValueError): witness.probe_result(bad)

    def test_artifact_identity_and_ambiguity(self):
        row = {"reason": "compiler-artifact", "executable": "/public/target/unit",
               "target": {"name": "chronik_plugin_impl"}, "profile": {"test": True}}
        raw = (json.dumps(row) + "\n").encode()
        with patch.object(witness.Path, "resolve", lambda p: p), \
             patch.object(witness.Path, "is_symlink", return_value=False), \
             patch.object(witness.runner, "regular", return_value=b"synthetic"):
            self.assertEqual(witness.artifact(raw, Path("/public/target")), Path("/public/target/unit"))
            for bad in (raw + raw, b"", raw.replace(b"/public/target/unit", b"/other/unit")):
                with self.assertRaises(ValueError): witness.artifact(bad, Path("/public/target"))


if __name__ == "__main__":
    unittest.main()

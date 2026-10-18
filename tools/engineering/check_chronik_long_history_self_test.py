# SPDX-License-Identifier: MIT
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_long_history as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_failure_trace_cannot_change_normal_protocol(self):
        path = "tests/compatibility/upstream-chronik/run_repetitions.py"
        before = gate.git(self.root, "show", gate.PARENT + ":" + path).decode()
        after = (self.root / path).read_text()
        gate.validate_runner_protocol(before, after)
        for old, new in (("timeout=60", "timeout=120"),
                         ("                stop_group(proc)", "                pass"),
                         ('raise\n    finally:', 'return ()\n    finally:'),
                         ("os.O_NOFOLLOW | os.O_NONBLOCK", "0"),
                         ('code == 0', 'code >= 0')):
            bad = after.replace(old, new)
            self.assertNotEqual(after, bad)
            with self.assertRaises(ValueError):
                gate.validate_runner_protocol(before, bad)

    def test_exact_layer(self):
        self.assertEqual(gate.validate(self.root), gate.CHANGED)

    def test_scope_budget_and_proof_rejected(self):
        original = gate.load_json
        manifest = original(self.root / gate.MANIFEST)
        for field, value in (("network", "mainnet"), ("mainnet_admission", True),
                             ("fixture_maximum_height", 5000), ("maximum_mempool", 100000),
                             ("retained_blocks", 5000), ("native_process_timeout_seconds", 120),
                             ("inherited_startup_timeout_seconds", 120), ("knowledge_status", "Reproduced")):
            bad = copy.deepcopy(manifest); bad["contract"][field] = value
            with patch.object(gate, "load_json", side_effect=lambda p:
                    bad if p == self.root / gate.MANIFEST else original(p)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_unknown_path_and_wrong_preimage(self):
        original = gate.git
        with patch.object(gate, "git", side_effect=lambda root, *args:
                original(root, *args) + b"extra.py\0" if args[0] == "ls-files" else original(root, *args)), \
                self.assertRaises(ValueError):
            gate.validate(self.root)
        path = "chronik/chronik-cpp/ergon_node_access.h"
        before = gate.before_identity(self.root, path)
        self.assertEqual(gate.successor_identity(self.root, path, before), gate.current_identity(self.root, path))
        with self.assertRaises(ValueError):
            gate.successor_identity(self.root, path, dict(before, sha256="0" * 64))


if __name__ == "__main__":
    unittest.main()

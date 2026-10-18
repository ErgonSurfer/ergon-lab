# SPDX-License-Identifier: MIT
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import check_chronik_position_catalogue as gate


class Contract(unittest.TestCase):
    root = Path(__file__).resolve().parents[2]

    def test_exact_layer(self):
        self.assertEqual(gate.validate(self.root), gate.CHANGED)

    def test_runner_cannot_change_old_process_contract(self):
        before = gate.git(self.root, "show", gate.PARENT + ":" + gate.RUNNER).decode()
        after = (self.root / gate.RUNNER).read_text()
        gate.validate_runner_protocol(before, after)
        for old, new in (("timeout=60", "timeout=120"),
                         ("                stop_group(proc)", "                pass"),
                         ('code == 0', 'code >= 0'),
                         ('== 58, "non-disjoint', '== 64, "non-disjoint'),
                         ('(\"capture-and-query-controls\", CAPTURE, 1063)',
                          '(\"capture-and-query-controls\", CAPTURE, 1)'),
                         ('"LANG": "C"', '"LANG": "en_US"'),
                         ('position_catalogue_staging_rejects_native_aba", 8',
                          'position_catalogue_staging_rejects_native_aba", 7')):
            bad = after.replace(old, new)
            self.assertNotEqual(bad, after)
            with self.assertRaises(ValueError): gate.validate_runner_protocol(before, bad)
        with self.assertRaises(ValueError):
            gate.validate_runner_protocol(before, after + "\ndef unrelated():\n    pass\n")

    def test_native_reader_cannot_weaken_authentication_or_fences(self):
        before = gate.git(self.root, "show", gate.PARENT + ":" + gate.READER).decode()
        after = (self.root / gate.READER).read_text()
        gate.validate_reader_source(before, after)
        for old, new in (("CATALOGUE_BATCH = 64", "CATALOGUE_BATCH = 65"),
                         ("LAYOUT_LIMIT = 8", "LAYOUT_LIMIT = 80"),
                         ("AssertLockNotHeld(cs_main);", ""),
                         ("SameSource(current, tip);", "Available();"),
                         ("checksum != verifier.GetHash()", "false"),
                         ("index->GetUndoPos() != ref.undo", "false"),
                         ("working->directory.clear();", ""),
                         ("batch[i] = {pos.nFile, pos.nPos};", "working->heights.push_back({pos.nFile, pos.nPos});")):
            bad = after.replace(old, new)
            self.assertNotEqual(bad, after)
            with self.assertRaises(ValueError): gate.validate_reader_source(before, bad)

    def test_scope_and_proof_do_not_widen(self):
        original = gate.load_json
        manifest = original(self.root / gate.MANIFEST)
        for key, value in (("catalogue_batch_rows", 10000), ("cached_layouts", 100),
                           ("native_process_timeout_seconds", 120), ("ci_repetitions", 1),
                           ("mainnet_admission", True), ("knowledge_status", "Reproduced"),
                           ("upstream_engine_change", True), ("automatic_retry", True)):
            bad = copy.deepcopy(manifest); bad["contract"][key] = value
            with patch.object(gate, "load_json", side_effect=lambda p:
                    bad if p == self.root / gate.MANIFEST else original(p)), self.assertRaises(ValueError):
                gate.validate(self.root)

    def test_extra_path_and_wrong_successor_preimage_fail(self):
        original = gate.git
        with patch.object(gate, "git", side_effect=lambda root, *args:
                original(root, *args) + b"extra.py\0" if args[0] == "ls-files" else original(root, *args)), \
                self.assertRaises(ValueError):
            gate.validate(self.root)
        before = gate.before_identity(self.root, gate.READER)
        self.assertEqual(gate.successor_identity(self.root, gate.READER, before), gate.current_identity(self.root, gate.READER))
        with self.assertRaises(ValueError):
            gate.successor_identity(self.root, gate.READER, dict(before, sha256="0" * 64))


if __name__ == "__main__":
    unittest.main()

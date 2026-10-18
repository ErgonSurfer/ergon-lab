# SPDX-License-Identifier: MIT
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
import long_history_service as long


class Contract(unittest.TestCase):
    def result(self):
        return {"initial_tip": 5000, "final_tip": 5001, "corpus_height": 102,
            "retained_capture_window": 288, "daemon_processes": 3, "external_connections": 0,
            "upstream_cold_reconstruction_and_same_index_reopen": True,
            "ALP_and_public_plugin_oracles": True,
            "historical_first_seen": "explicit-unknown-zero-not-original-mempool-time"}

    def test_upstream_driver_reused_with_declared_budget(self):
        expected = self.result()
        with patch.object(long.cold, "observe", return_value=expected) as observe:
            self.assertEqual(long.series(Path("node"), Path("work"), (), 1), expected)
        observe.assert_called_once_with(Path("node"), Path("work"), (),
                                         final_height=5000, startup_timeout=120)

    def test_wrong_history_scope_or_claim_rejected(self):
        for field, value in (("initial_tip", 402), ("final_tip", 5000),
                             ("corpus_height", 4900), ("retained_capture_window", 5000),
                             ("external_connections", 1), ("daemon_processes", 2),
                             ("ALP_and_public_plugin_oracles", False),
                             ("historical_first_seen", "invented")):
            bad = self.result(); bad[field] = value
            with patch.object(long.cold, "observe", return_value=bad), self.assertRaises(ValueError):
                long.series(Path("node"), Path("work"), (), 1)

    def test_instability_and_repetition_bounds(self):
        second = copy.deepcopy(self.result()); second["unexpected"] = True
        with patch.object(long.cold, "observe", side_effect=[self.result(), second]), self.assertRaises(ValueError):
            long.series(Path("node"), Path("work"), (), 2)
        for count in (0, 4, True, 1.5):
            with self.assertRaises(ValueError):
                long.series(Path("node"), Path("work"), (), count)

    def test_failure_is_not_retried(self):
        with patch.object(long.cold, "observe", side_effect=ValueError("synthetic")) as observe, \
                self.assertRaises(ValueError):
            long.series(Path("node"), Path("work"), (), 3)
        self.assertEqual(observe.call_count, 1)


if __name__ == "__main__":
    unittest.main()

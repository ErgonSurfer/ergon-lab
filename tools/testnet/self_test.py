#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import copy
import json
from pathlib import Path
import tempfile
import unittest
import genesis
import model
import thresholds


class ProfileTests(unittest.TestCase):
    def test_exact_compact_zero_positive_boundaries(self):
        for decays, required, zero_bits, positive_bits, actual in (
            (0, 7100000000000, 0x1b27a4f5, 0x1b27a4f4, 7100002645635),
            (1, 7105826777958, 0x1b279ca3, 0x1b279ca2, 7105828144088),
            (365, 9578467056439, 0x1b1d62e0, 0x1b1d62df, 9578469860768),
        ):
            self.assertEqual(thresholds.required_work(decays), required)
            self.assertEqual(thresholds.bits_for_work(required), positive_bits)
            self.assertEqual(model.work(positive_bits), actual)
            self.assertEqual(model.subsidy(model.work(zero_bits), thresholds.CALIBRATION, decays), 0)
            self.assertEqual(model.subsidy(actual, thresholds.CALIBRATION, decays), 1)
            self.assertEqual(model.subsidy(required - 1, thresholds.CALIBRATION, decays), 0)
            self.assertEqual(model.subsidy(required, thresholds.CALIBRATION, decays), 1)

    def test_quantized_tiers_are_not_ideal_work_or_miner_eligibility(self):
        table = thresholds.tables()
        rows = {r["illustrative_total_network_rate"]: r for r in table["tiers"]}
        self.assertEqual(rows["100 PH/s"]["height_one_subsidy_units"], 8450705)
        self.assertEqual(rows["100 PH/s"]["height_52560_subsidy_units"], 6264050)
        self.assertEqual(rows["12 GH/s"]["height_one_subsidy_units"], 1)
        self.assertEqual(rows["12 GH/s"]["height_52560_subsidy_units"], 0)
        self.assertEqual(rows["1 TH/s"]["height_one_subsidy_txrg"], "0.00000084")
        self.assertEqual(table["asset"], {"name": "Ergon Testnet", "ticker": "tXRG", "decimals": 8})
        self.assertEqual(table, thresholds.tables())
        for row in table["tiers"]:
            bits = int(row["canonical_bits"], 16)
            self.assertEqual(thresholds.compact(genesis.target(bits)), bits)

    def test_fixed_genesis_and_no_premine(self):
        p = model.load_profile()
        g = genesis.describe(p["genesis"]["nonce"])
        self.assertTrue(g["valid_pow"])
        self.assertEqual(p["genesis"]["reward_base_units"], 0)
        self.assertEqual(len(bytes.fromhex(g["header_hex"])), 80)
        self.assertNotEqual(g["hash"], "000000070e37bfee7e84b94f997f38155a85b22172f5ca25fd4eb3d64c5ad7c5")

    def test_target_and_reward_vectors(self):
        self.assertEqual(model.work(0x1e00ffff), 16777472)
        calibration = model.load_profile()["consensus"]["value_calibration"]
        self.assertEqual(calibration, 7100000000000)
        for w, c, expected in ((calibration - 1, calibration, 0), (calibration, calibration, 1),
                               (2 * calibration - 1, calibration, 1),
                               (16777472, calibration, 0), (268435456, calibration, 0)):
            self.assertEqual(model.subsidy(w, c), expected)
        self.assertEqual(model.subsidy(calibration, calibration, 1), 0)
        self.assertEqual(model.subsidy(calibration * 100000, calibration, 1), 99918)

    def test_zero_to_positive_at_multiple_assumed_equilibrium_rates(self):
        result = model.scenarios()
        rows = {r["id"]: r for r in result["scenarios"]}
        self.assertEqual(result["initial_reward_base_units"], 0)
        for name, expected in (("low-work-steady", 0), ("ten-gigahash-steady", 0),
                               ("twelve-gigahash-steady", 1), ("one-terahash-steady", 84)):
            self.assertEqual(rows[name]["assumed_new_equilibrium_reward_base_units"], expected)
        self.assertFalse(result["stress_cases_included"])
        self.assertEqual(rows["twofold-arrival"]["first_block_expected_seconds"], 300)
        self.assertEqual(rows["fourfold-departure"]["first_block_expected_seconds"], 2400)

    def test_extreme_work_is_not_silently_truncated(self):
        with self.assertRaises(ValueError): model.subsidy(2**200, 1)
        for c in (0, -1, True):
            with self.assertRaises(ValueError): model.subsidy(4096, c)
        with self.assertRaises(ValueError): genesis.target(0x1e80ffff)

    def test_optional_stress_does_not_change_profile_or_claim_recovery(self):
        profile = model.load_profile()
        original = copy.deepcopy(profile)
        result = model.scenarios(profile, stress=True)
        rows = {r["id"]: r for r in result["scenarios"]}
        self.assertEqual(result["knowledge_status"], "Simulation")
        self.assertTrue(all(r["nominal_reward_in_money_range"] for r in rows.values()))
        self.assertGreater(rows["hundred-petahash-departure-to-low-work"]["probability_no_block_in_one_hour"], .999)
        self.assertLess(rows["hundred-petahash-arrival"]["first_block_expected_seconds"], .000001)
        self.assertEqual(profile, original)
        self.assertEqual(model.scenarios(stress=True), result)

    def test_wrong_profile_never_defaults_to_mainnet(self):
        original = model.load_profile()
        with tempfile.TemporaryDirectory(prefix="ergon-testnet-profile-") as temporary:
            path = Path(temporary) / "profile.json"
            for change in (lambda p: p.update(network="main"),
                           lambda p: p["genesis"].update(hash="00" * 32),
                           lambda p: p["consensus"].update(value_calibration=0),
                           lambda p: p["consensus"].update(value_calibration=4096),
                           lambda p: p["consensus"].update(target_spacing_seconds=60),
                           lambda p: p["consensus"].update(pow="randomx")):
                p = copy.deepcopy(original); change(p)
                path.write_text(json.dumps(p))
                with self.assertRaises(ValueError): model.load_profile(path)
            path.write_text('{"network":"main","network":"ergon-testnet-v1"}')
            with self.assertRaises(ValueError): model.load_profile(path)


if __name__ == "__main__":
    unittest.main()

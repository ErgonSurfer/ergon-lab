#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Deterministic arithmetic scenarios, not mining benchmarks or live evidence."""
import argparse
import json
import math
from pathlib import Path
import genesis

ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "docs/engineering/testnet/ergon-testnet-v1.json"
MAX_MONEY = 21000000 * 100000000


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate profile field")
        result[key] = value
    return result


def load_profile(path=PROFILE):
    raw = path.read_bytes()
    if len(raw) > 16384:
        raise ValueError("profile oversized")
    p = json.loads(raw, object_pairs_hook=unique)
    if p["network"] != "ergon-testnet-v1" or p["schema"] != "ergon-development-network/v1":
        raise ValueError("wrong network profile")
    g = genesis.describe(p["genesis"]["nonce"])
    if not g["valid_pow"] or any(p["genesis"][k] != g[k] for k in
                                 ("hash", "merkle_root", "timestamp", "nonce", "bits")):
        raise ValueError("genesis mismatch")
    c = p["consensus"]
    if c["pow"] != "sha256d" or c["daa"] != "existing-ergon-ema":
        raise ValueError("unexpected consensus engine")
    for key in ("target_spacing_seconds", "decay_interval_blocks", "value_calibration"):
        if type(c[key]) is not int or c[key] <= 0:
            raise ValueError("invalid monetary parameter")
    if (c["target_spacing_seconds"], c["decay_interval_blocks"], c["value_calibration"]) != (600, 144, 7100000000000):
        raise ValueError("development profile must preserve mainnet monetary parameters")
    if int(c["pow_limit_hex"], 16) != genesis.target(int(c["bootstrap_bits"], 16)):
        raise ValueError("bootstrap target mismatch")
    return p


def work(bits):
    return 2**256 // (genesis.target(bits) + 1)


def subsidy(expected_work, calibration, intervals=0):
    if type(expected_work) is not int or not 0 < expected_work < 2**256:
        raise ValueError("work out of range")
    if type(calibration) is not int or calibration <= 0 or not 0 <= intervals <= 20000:
        raise ValueError("invalid calibration or model horizon")
    # Native arith_uint256 multiplication wraps; preserve that behavior here.
    value = expected_work
    for _ in range(intervals):
        value = (value * 99918 % 2**256) // 100000
    quotient = value // calibration
    # Report inherited low64/signed conversion risk instead of silently masking it.
    if quotient >= 2**63:
        raise ValueError("native signed amount conversion would exceed range")
    return quotient


def scenarios(profile=None, *, stress=False):
    p = profile or load_profile()
    c = p["consensus"]
    initial = work(int(c["bootstrap_bits"], 16))
    result = {
        "knowledge_status": "Simulation",
        "scope": "integer-reward-and-expected-first-block-arithmetic-not-native-chain-execution",
        "initial_work_expected_hashes": initial,
        "initial_reward_base_units": subsidy(initial, c["value_calibration"]),
        "initial_seconds_at_illustrative_1MHps": initial / 1000000,
        "height_one_first_positive_reward_expected_work": c["value_calibration"],
        "height_one_assumed_equilibrium_threshold_hashes_per_second": c["value_calibration"] / c["target_spacing_seconds"],
        "stress_cases_included": stress,
        "maturity_blocks": c["coinbase_maturity_blocks"],
        "scenarios": [],
        "limits": ["Hash rates are illustrative, not observed hardware performance.",
                   "Equilibria are assumed; this model does not simulate DAA convergence or miner economics.",
                   "An expected waiting time is not a deadline; block discovery is stochastic.",
                   "Native DAA arrival/withdrawal tests are separate; no automatic stall recovery is claimed."]}
    cases = [
        ("low-work-steady", 1000000, 1000000),
        ("ten-gigahash-steady", 10000000000, 10000000000),
        ("twelve-gigahash-steady", 12000000000, 12000000000),
        ("one-terahash-steady", 1000000000000, 1000000000000),
        ("twofold-arrival", 1000000000000, 2000000000000),
        ("fourfold-departure", 1000000000000, 250000000000),
    ]
    if stress:
        cases += [("hundred-petahash-arrival", 1000000, 100000000000000000),
                  ("hundred-petahash-departure-to-low-work", 100000000000000000, 1000000)]
    for label, previous, current in cases:
        # Each row assumes target-spacing equilibrium before the change.
        # The separately reported bootstrap is not an equilibrium assumption.
        before = max(initial, previous * c["target_spacing_seconds"])
        equilibrium = max(initial, current * c["target_spacing_seconds"])
        reward = subsidy(equilibrium, c["value_calibration"])
        result["scenarios"].append({
            "id": label, "previous_hashes_per_second": previous, "hashes_per_second": current,
            "first_block_expected_seconds": before / current,
            "probability_no_block_in_one_hour": math.exp(-current * 3600 / before),
            "assumed_new_equilibrium_work": equilibrium,
            "assumed_new_equilibrium_reward_base_units": reward,
            "nominal_reward_in_money_range": 0 <= reward <= MAX_MONEY,
        })
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stress", action="store_true", help="Include optional extreme arithmetic cases; never changes consensus.")
    args = parser.parse_args()
    print(json.dumps(scenarios(stress=args.stress), indent=2, sort_keys=True, allow_nan=False))

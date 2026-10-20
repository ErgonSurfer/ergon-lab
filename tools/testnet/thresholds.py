#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Exact bounded compact-target subsidy tables, not hardware or income forecasts."""
import json
from decimal import Decimal, localcontext
import model
import genesis

CALIBRATION = 7100000000000
SPACING = 600
TIERS = (("1 MH/s", 10**6), ("10 MH/s", 10**7), ("100 MH/s", 10**8),
         ("1 GH/s", 10**9), ("10 GH/s", 10**10), ("12 GH/s", 12 * 10**9),
         ("25 GH/s", 25 * 10**9), ("50 GH/s", 50 * 10**9), ("100 GH/s", 10**11),
         ("1 TH/s", 10**12), ("10 TH/s", 10**13), ("100 TH/s", 10**14),
         ("1 PH/s", 10**15), ("10 PH/s", 10**16), ("100 PH/s", 10**17))


def compact(target):
    if type(target) is not int or not 0 < target < 2**256:
        raise ValueError("target out of range")
    size = (target.bit_length() + 7) // 8
    word = target << (8 * (3 - size)) if size <= 3 else target >> (8 * (size - 3))
    if word & 0x800000:
        word >>= 8
        size += 1
    return size << 24 | word


def required_work(intervals):
    if type(intervals) is not int or not 0 <= intervals <= 365:
        raise ValueError("threshold table horizon out of range")
    value = CALIBRATION
    for _ in range(intervals):
        value = (value * 100000 + 99918 - 1) // 99918
    return value


def bits_for_work(work):
    if type(work) is not int or not 1 < work < 2**256:
        raise ValueError("required work out of range")
    return compact(2**256 // work - 1)


def coins(units):
    # Fixed decimal text, never binary floating point or a monetary forecast.
    return f"{units // 100000000}.{units % 100000000:08d}"


def tables():
    profile = model.load_profile()
    assert profile["consensus"]["value_calibration"] == CALIBRATION
    thresholds, tiers = [], []
    for intervals in (0, 1, 365):
        required = required_work(intervals)
        bits = bits_for_work(required)
        actual = model.work(bits)
        with localcontext() as context:
            context.prec = 40
            rate = str(Decimal(actual) / SPACING)
        thresholds.append({"decays": intervals, "first_height": max(1, intervals * 144),
            "minimum_integer_work": required, "weakest_positive_bits": f"{bits:08x}",
            "actual_work": actual, "subsidy_units": model.subsidy(actual, CALIBRATION, intervals),
            "easier_neighbor_bits": f"{bits + 1:08x}",
            "easier_neighbor_work": model.work(bits + 1),
            "easier_neighbor_subsidy_units": model.subsidy(model.work(bits + 1), CALIBRATION, intervals),
            "assumed_equilibrium_hashes_per_second_decimal": rate})
    for label, rate in TIERS:
        bits = bits_for_work(rate * SPACING)
        work = model.work(bits)
        subsidy = model.subsidy(work, CALIBRATION)
        tiers.append({"illustrative_total_network_rate": label,
            "requested_hashes_per_second": rate, "canonical_bits": f"{bits:08x}",
            "actual_integer_work": work, "height_one_subsidy_units": subsidy,
            "height_one_subsidy_txrg": coins(subsidy),
            "nominal_fixed_subsidy_144_blocks_txrg": coins(subsidy * 144),
            "height_144_subsidy_units": model.subsidy(work, CALIBRATION, 1),
            "height_52560_subsidy_units": model.subsidy(work, CALIBRATION, 365)})
    return {"knowledge_status": "Simulation", "asset": profile["asset"], "thresholds": thresholds, "tiers": tiers,
        "limits": ["Rate is total-network assumed equilibrium, not individual-miner eligibility.",
            "Targets are compact-quantized; requested rate is only approximate.",
            "No DAA convergence, wall-clock recovery or hardware performance is simulated.",
            "Subsidy excludes transaction fees; coinbase maturity is 100 blocks.",
            "The 144-block and 365-decay horizons follow height, not calendar time.",
            "EMA-from-origin development age differs from historical mainnet pre-EMA decay.",
            "The 144-block daily projection holds subsidy constant and is not actual daily issuance.",
            "Bounded non-overflow arithmetic, not equivalence at arbitrary uint256 work."]}


if __name__ == "__main__":
    print(json.dumps(tables(), indent=2, sort_keys=True, allow_nan=False))

<!-- SPDX-License-Identifier: MIT -->
# When does proportional subsidy become non-zero?

Knowledge status: **Simulation**. These are bounded calculations from the
existing native rules, not measured mining, profitability or DAA-convergence
results. The development asset is `tXRG`; one base unit is 0.00000001 tXRG.

The threshold belongs to **a block's required work and height**, not to an
individual miner. At an assumed 600-second equilibrium, the initial threshold
corresponds to approximately **11.83 GH/s of total network hash rate**. A
smaller miner can find a positive-subsidy block at that difficulty; its power
affects its discovery probability, not the subsidy attached to the target.
Connecting more power does not instantly change the next block's subsidy.

## Exact zero-to-one boundaries

Canonical compact target encoding slightly quantizes the integer-work boundary.
The adjacent targets below exercise the actual native subsidy function in
`ergon_testnet_tests` as well as the offline model.

| Height | Decays | Minimum ideal integer work | Weakest positive `nBits` | Encoded integer work | Base units |
| --- | ---: | ---: | --- | ---: | ---: |
| 1–143 | 0 | 7,100,000,000,000 | `1b27a4f4` | 7,100,002,645,635 | 1 |
| 144–287 | 1 | 7,105,826,777,958 | `1b279ca2` | 7,105,828,144,088 | 1 |
| 52,560–52,703 | 365 | 9,578,467,056,439 | `1b1d62df` | 9,578,469,860,768 | 1 |

The immediately easier neighbors `1b27a4f5`, `1b279ca3` and `1b1d62e0`
respectively pay **zero** at those horizons. The initial encoded positive
boundary corresponds to approximately 11.833337743 GH/s at equilibrium.
Bootstrap `1e00ffff` instead has integer work 16,777,472 and pays zero.

## Illustrative network-power tiers

Each row chooses a canonical compact target approximating the requested
**whole-network** rate at equilibrium. Integer subsidies are exact for that
chosen target. The daily column simply multiplies the initial displayed
subsidy by 144; it holds subsidy constant and is **not exact daily issuance**.

| Approximate network rate | Units/block, height 1 | tXRG/block | Nominal tXRG/144 blocks | Units at height 144 | Units after 365 decays |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 MH/s | 0 | 0.00000000 | 0.00000000 | 0 | 0 |
| 10 MH/s | 0 | 0.00000000 | 0.00000000 | 0 | 0 |
| 100 MH/s | 0 | 0.00000000 | 0.00000000 | 0 | 0 |
| 1 GH/s | 0 | 0.00000000 | 0.00000000 | 0 | 0 |
| 10 GH/s | 0 | 0.00000000 | 0.00000000 | 0 | 0 |
| 12 GH/s | 1 | 0.00000001 | 0.00000144 | 1 | 0 |
| 25 GH/s | 2 | 0.00000002 | 0.00000288 | 2 | 1 |
| 50 GH/s | 4 | 0.00000004 | 0.00000576 | 4 | 3 |
| 100 GH/s | 8 | 0.00000008 | 0.00001152 | 8 | 6 |
| 1 TH/s | 84 | 0.00000084 | 0.00012096 | 84 | 62 |
| 10 TH/s | 845 | 0.00000845 | 0.00121680 | 844 | 626 |
| 100 TH/s | 8,450 | 0.00008450 | 0.01216800 | 8,443 | 6,264 |
| 1 PH/s | 84,507 | 0.00084507 | 0.12169008 | 84,437 | 62,640 |
| 10 PH/s | 845,073 | 0.00845073 | 1.21690512 | 844,380 | 626,407 |
| 100 PH/s | 8,450,705 | 0.08450705 | 12.16901520 | 8,443,775 | 6,264,050 |

No miner eligibility or distribution promise follows from this table. It
excludes transaction fees; coinbase maturity remains 100 blocks. Height
52,560 is about one year only if the chain actually averages 144 blocks/day.

## Method and reproduction

Sources are [native subsidy](../../../src/validation.cpp),
[integer block work](../../../src/chain.cpp),
[compact target encoding](../../../src/arith_uint256.cpp),
[network parameters](../../../src/chainparams.cpp) and
[the candidate profile](ergon-testnet-v1.json).

For valid decoded target `T`, set `M = 2^256`, `W = floor(M/(T+1))`, and
`d = floor(height/144)`. Starting at `v = W`, repeat
`v = floor(((v × 99918) mod M)/100000)` exactly `d` times. The subsidy is
`floor(v/7100000000000)` base units. The bounded examples do not overflow.
The exact inverse work threshold starts at 7,100,000,000,000 and repeats
`required = ceil(required × 100000/99918)` for each decay.

`tools/testnet/thresholds.py` selects the largest canonical compact target
not exceeding `floor(M/required_work)-1`. It emits exact work, compact bits,
integer rewards, fixed-decimal amounts and adjacent zero/one vectors:

```sh
python3 -B tools/testnet/thresholds.py
python3 -B tools/testnet/self_test.py
```

The simpler `model.py` scenarios use *ideal* `rate × 600` work, not compact
quantization. At 100 PH/s, this gives 8,450,704 rather than 8,450,705 units;
near the initial threshold, quantization can change zero to one. Do not mix
the two models without naming this difference.

## Limits and counterevidence

- Discovery is stochastic. An expected delay is not a deadline or guarantee.
- The native DAA adjusts through accepted blocks; these tables do not model
  convergence or recovery after hash-rate withdrawal.
- Current EMA rules start at the new chain's origin. Historical mainnet also
  has a pre-EMA decay factor of `99826/100000`; its monetary age is not reset
  when the development chain starts. Equal rules do not imply equal rewards
  at the same calendar date.
- The easier development bootstrap and proof-of-work limit remain explicit
  differences. No reward floor or DAA redesign is introduced to fit a tier.
- The model rejects signed-range overflow rather than claiming equivalence
  to inherited low64 conversion at arbitrary extreme work.
- No price, economic value, mining-hardware performance or return is inferred.

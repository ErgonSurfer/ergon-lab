<!-- SPDX-License-Identifier: MIT -->
# Ergon development testnet

Status: **candidate, not deployed**. Knowledge: **Open Question** for native
operation; the offline arithmetic is **Simulation**. This is not a consensus
upgrade proposal for mainnet.

## Faithful rules first

The initial network preserves Ergon mainnet's SHA-256d proof of work, existing
EMA difficulty algorithm, 600-second target spacing, 144-block reward-decay
interval, 7,100,000,000,000 value calibration and integer reward rounding.
It introduces no reward floor, emission cap, automatic minimum-difficulty
exception, CPU-only rule or CashTokens activation.

Its explicit differences are a new genesis, network identifiers, address
prefixes, ports, storage namespaces and an easier bootstrap/proof-of-work
limit. Current rules apply from the new chain's origin rather than replaying
mainnet's historical upgrade schedule. Inherited peers, historical checkpoints,
assumed-valid blocks and minimum chain work are not reused. Therefore it is
not a replay of mainnet history, and behaviour at the easier proof-of-work
limit must not be described as identical to mainnet.

The canonical candidate parameters are in [the profile](ergon-testnet-v1.json).
Display the development asset as **Ergon Testnet (`tXRG`)**, with eight decimals
and `ergontest:` CashAddr addresses. The development-only `currency` object in
`getblockchaininfo` supplies this display identity; all numeric amount encoding
is unchanged. This is not a global replacement of legacy `XRG` log/help strings
or a completed GUI/wallet integration. Clients must bind network and genesis,
not infer authority from a ticker or strip the CashAddr prefix.
The mainnet profile is unchanged. Chronik remains optional, and any admitted
instance must bind this network's exact native genesis. Neither a different
address prefix nor a separate genesis supplies universal transaction-signature
replay protection; applications must also enforce explicit network selection
and keep keys and wallets separate. No bridge or mainnet fallback is provided.

## Zero is an intentional result

At bootstrap, the expected work is 16,777,472 hashes. Dividing by the unchanged
mainnet calibration gives **zero base units**. Raising hash rate alone does
not directly raise subsidy: accepted blocks and the DAA must first change
the required work. A first block at a new rate still uses its required target.

At height one, before decay, the arithmetic first becomes nonzero at expected
work 7,100,000,000,000. An *assumed* 600-second equilibrium puts this threshold
near 11.83 GH/s. These examples use ideal work, not compact-target rounding:

| Assumed equilibrium hash rate | Expected work per block | Subsidy, base units |
| --- | ---: | ---: |
| 1 MH/s | 600,000,000 | 0 |
| 10 GH/s | 6,000,000,000,000 | 0 |
| 12 GH/s | 7,200,000,000,000 | 1 |
| 1 TH/s | 600,000,000,000,000 | 84 |

Later decay and target quantization change the boundary. None of these rows
proves a real miner's performance, DAA convergence, viable distribution,
economic value or a recovery deadline. Low-work mining can advance the chain
without producing spendable new value. Native tests compare the reward rules
against mainnet across both zero and positive cases.

## Experiments do not define this network

Start with modest arrivals and departures. Extreme PH/s-to-low-work cases are
optional stress experiments, not a launch requirement or a reason to alter
the monetary parameters. A future DAA proposal needs separately labelled
experiments, methods, limitations and counterevidence; this profile implements
no such proposal.

Run the standard-library arithmetic checks without starting any node:

```sh
python3 -B tools/testnet/self_test.py
python3 -B tools/testnet/model.py
python3 -B tools/testnet/model.py --stress
python3 -B tools/testnet/thresholds.py
```

The model assumes equilibria and calculates first-block expectations. It does
not simulate the native DAA's transient convergence. The native
`ergon_testnet_tests` suite is a separate component check, not proof of a
deployed network. Startup, accepted-block propagation, Chronik service tests
and public CI remain required before operation.

Historical mainnet events are useful independent comparisons. Explorer
screenshots alone cannot establish exact work, duration or recovery: bind a
relevant block range and public headers before claiming reproduction. An
estimated hash rate is not a measurement of individual miners or their intent.

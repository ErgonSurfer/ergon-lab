# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Reconstruct an absent index through upstream Chronik, not a second indexer.

The native daemon first creates a public ALP graph while Chronik is disabled.
The corpus is older than the retained capture window before the first index
opens. A fresh index cannot reconstruct historical mempool first-seen times:
the existing HTTP oracle must see upstream's explicit unknown value zero.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import plugin_alp_service as combined

alp, service, runner = combined.alp, combined.service, combined.runner
CORPUS_HEIGHT = 102
FINAL_HEIGHT = 402
RETAINED_WINDOW = 288


def bind_ancestry(block, funding):
    """Bind operation names by native outpoints, never canonical block order."""
    rows = block["tx"]
    runner.require(len(rows) == 5 and "coinbase" in rows[0]["vin"][0],
                   "exact native coinbase and four-operation corpus required")
    corpus = rows[1:]
    keys = [tx["txid"] for tx in corpus]
    runner.require(len(set(keys)) == 4, "duplicate native corpus transaction")
    remaining, ids = dict(zip(keys, corpus)), []
    for op in range(4):
        expected = ([funding] if op == 0 else
                    [(ids[parent], out) for parent, out in combined.INPUTS[op]])
        matches = [txid for txid, tx in remaining.items()
                   if [(vin["txid"], vin["vout"]) for vin in tx["vin"]] == expected]
        runner.require(len(matches) == 1, "ambiguous or missing native ancestry")
        ids.append(matches[0])
        del remaining[matches[0]]
    runner.require(not remaining, "unbound native corpus transaction")
    return ids


def require_absent_index(index, inputs):
    runner.require(not any(p.exists() or p.is_symlink() for p in (index, inputs)),
                   "cold reconstruction requires absent index and plugin inputs")


def observe(binary, work, scripts):
    root = Path(tempfile.mkdtemp(prefix="ergon-upstream-", dir=work)).resolve()
    data = root / "data"
    data.mkdir()
    index = data / "regtest/chronik-upstream-regtest"
    inputs_root = data / "regtest/chronik-upstream-regtest-plugin-inputs"
    daemons = []
    def start(phase, enabled):
        daemon = service.Daemon(binary, root, data, phase,
                                enabled=enabled, public_plugin=enabled)
        daemons.append(daemon)
        if enabled:
            daemon.ready()
        else:
            service.wait_for(lambda: daemon.rpc("getblockchaininfo"))
        return daemon
    try:
        daemon = start("cold-native-only", False)
        require_absent_index(index, inputs_root)
        hashes = [daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
                  for _ in range(101)]
        funding = daemon.rpc("getblock", (hashes[0], 2))["tx"][0]
        runner.require(all(out["value"] == 0 for out in funding["vout"]),
                       "nonzero cold reconstruction funding refused")
        wires, ids = [], []
        for op in range(4):
            points = ([(funding["txid"], 0, True)] if op == 0 else
                      [(ids[parent], out, False) for parent, out in combined.INPUTS[op]])
            script = alp.bind_script(scripts, op, ids[0] if ids else "00" * 32) + b"\x04ERGN"
            raw = alp.transaction(points, [script, b"\x51"] +
                                  ([b"\x52" if op == 2 else b"\x51"] if op < 3 else []))
            wires.append(raw)
            ids.append(alp.submit(daemon, raw))
            runner.require(sorted(daemon.rpc("getrawmempool")) == sorted(ids),
                           "native-only cold corpus pool drift")
        times = [daemon.rpc("getmempoolentry", (txid,))["time"] for txid in ids]
        runner.require(all(type(t) is int and t > 0 for t in times),
                       "native-only corpus lacks observed first-seen times")
        confirmed = daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        block = daemon.rpc("getblock", (confirmed, 2))
        runner.require(block["height"] == CORPUS_HEIGHT and
                       bind_ancestry(block, (funding["txid"], 0)) == ids and
                       [daemon.rpc("getrawtransaction", (txid, False, confirmed)) for txid in ids] == wires,
                       "native corpus identity, ancestry or wire drift")
        for _ in range(FINAL_HEIGHT - CORPUS_HEIGHT):
            tip = daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        runner.require(daemon.rpc("getblockcount") == FINAL_HEIGHT and
                       daemon.rpc("getbestblockhash") == tip and
                       FINAL_HEIGHT - CORPUS_HEIGHT >= RETAINED_WINDOW and
                       daemon.rpc("getrawmempool") == [] and
                       daemon.rpc("getconnectioncount") == 0,
                       "native-only cold reconstruction precondition drift")
        daemon.stop()
        require_absent_index(index, inputs_root)

        daemon = start("cold-first-index", True)
        daemon.tip(FINAL_HEIGHT, tip)
        daemon.pool([])
        # No index observed the original mempool. Never invent continuity of
        # first-seen timestamps from native block/header or filesystem times.
        expected = combined.snapshot(daemon, ids, wires, [0] * 4, CORPUS_HEIGHT, confirmed)
        identity = runner.regular(index / "ergon-identity.json")
        runner.require(b'"plugins":true' in identity, "cold plugin identity missing")
        inputs = tuple(runner.regular(inputs_root / p) for p in ("ergon_public_probe.py", "plugins.toml"))
        lease = index / "probe.lock"
        lease_identity = (lease.stat().st_dev, lease.stat().st_ino)
        daemon.stop()

        daemon = start("cold-index-reopen", True)
        daemon.tip(FINAL_HEIGHT, tip)
        daemon.pool([])
        runner.require(combined.snapshot(daemon, ids, wires, [0] * 4, CORPUS_HEIGHT, confirmed) == expected,
                       "rebuilt ALP/plugin process-reopen snapshot drift")
        next_tip = daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        daemon.tip(FINAL_HEIGHT + 1, next_tip)
        runner.require(combined.snapshot(daemon, ids, wires, [0] * 4, CORPUS_HEIGHT, confirmed) == expected,
                       "rebuilt ALP/plugin snapshot drift after native progress")
        runner.require(runner.regular(index / "ergon-identity.json") == identity and
                       tuple(runner.regular(inputs_root / p) for p in ("ergon_public_probe.py", "plugins.toml")) == inputs and
                       (lease.stat().st_dev, lease.stat().st_ino) == lease_identity and
                       not (inputs_root / "__pycache__").exists() and
                       not (index / "probe.failed").exists() and
                       daemon.rpc("getconnectioncount") == 0,
                       "cold reconstruction identity, lease, input or peer drift")
        runner.require(daemon.http("/ws")[0] == 501 and daemon.http("/broadcast-tx", "POST")[0] == 501,
                       "cold reconstruction service capability widened")
        daemon.stop()
        return {"corpus_height": CORPUS_HEIGHT, "initial_tip": FINAL_HEIGHT,
            "final_tip": FINAL_HEIGHT + 1, "retained_capture_window": RETAINED_WINDOW,
            "index_absent_during_native_history": True,
            "upstream_cold_reconstruction_and_same_index_reopen": True,
            "ALP_and_public_plugin_oracles": True,
            "historical_first_seen": "explicit-unknown-zero-not-original-mempool-time",
            "native_input_output_value_and_fee": 0, "daemon_processes": 3,
            "positive_stops": 3, "positive_joined_service_stops": 2,
            "external_connections": 0, "websocket_and_broadcast_disabled": True}
    finally:
        failed = False
        for daemon in daemons:
            try:
                daemon.cleanup()
            except Exception:
                failed = True
        runner.require(not failed, "cold reconstruction cleanup incomplete")
        runner.remove_owned(root, work)


def series(binary, work, scripts, repetitions):
    runner.require(type(repetitions) is int and 1 <= repetitions <= 10,
                   "finite cold reconstruction repetitions required")
    result = None
    for _ in range(repetitions):
        current = observe(binary, work, scripts)
        runner.require(result is None or current == result, "cold reconstruction instability")
        result = current
    return result


def report_paths(source, build, work, report):
    source, build, work = (p.resolve(strict=True) for p in (source, build, work))
    report = report.absolute()
    runner.require(report.parent.resolve(strict=True) == report.parent and
                   not report.exists() and not report.is_symlink(), "new canonical cold report required")
    runner.require(not any(report.is_relative_to(p) for p in (source, build, work)) and
                   not any(work.is_relative_to(p) or p.is_relative_to(work) for p in (source, build)) and
                   not source.is_relative_to(build) and not build.is_relative_to(source),
                   "separate cold source/build/work/report roots required")
    return source, build, work, report


def public_report(payload, forbidden):
    raw = runner.canonical(payload)
    runner.require(not any(str(p).encode() in raw for p in forbidden), "private cold report binding")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "build-root", "work-parent", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    runner.require(type(args.repetitions) is int and 1 <= args.repetitions <= 10,
                   "finite cold reconstruction repetitions required")
    source, build, work, report = report_paths(args.source_root, args.build_root, args.work_parent, args.report)
    sys.path.insert(0, str(source / "tools/engineering"))
    from check_chronik_cold_rebuild import validate, input_binding
    validate(source)
    test_binding = input_binding(source)
    manifest = source / "docs/engineering/chronik/upstream-host-adaptation-v1.json"
    binding = runner.bind_build(source, build, "regtest-plugins", manifest)
    binary, native = build / "src/bitcoind", build / "src/test/test_bitcoin"
    hashes = tuple(runner.digest(runner.regular(p)) for p in (binary, native))
    forbidden = (source, build, work, binary, native)
    scripts = alp.templates(runner.execute([str(native), "--run_test=" + alp.TEMPLATE_CASE,
        "--report_level=short", "--log_level=message", "--color_output=no"], work, forbidden))
    result = series(binary, work, scripts, args.repetitions)
    runner.require(hashes == tuple(runner.digest(runner.regular(p)) for p in (binary, native)) and
                   binding == runner.bind_build(source, build, "regtest-plugins", manifest) and
                   test_binding == input_binding(source), "cold source/build/binary drift")
    validate(source)
    payload = public_report({"schema": "ergon-upstream-cold-rebuild-observation/v1",
        "knowledge_status": "Observed", "proof_ceiling": "bounded_regtest_service_observation",
        "binding": binding, "test_inputs_sha256": test_binding, "binary_sha256": hashes[0],
        "native_test_binary_sha256": hashes[1], "template_case": alp.TEMPLATE_CASE,
        "template_assertions": alp.TEMPLATE_ASSERTIONS,
        "upstream_template_sha256": [runner.digest(raw) for raw in scripts],
        "repetitions": args.repetitions, "fresh_daemon_processes": 3 * args.repetitions,
        "result": result, "child_environment": dict(runner.ENV, TMPDIR="<runner-created-unique-directory>"),
        "disposable_roots_removed": True, "binary_to_source_provenance": "external-build-record-required",
        "historical_timeouts": "retained-not-replaced-or-promoted-by-this-observation"}, forbidden)
    runner.write_report(report, payload)
    print("PASS: upstream cold ALP/public-plugin reconstruction, same-index process reopen and progress")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print("REFUSED: cold reconstruction; class=" + runner.refusal_class(error), file=sys.stderr)
        sys.exit(1)

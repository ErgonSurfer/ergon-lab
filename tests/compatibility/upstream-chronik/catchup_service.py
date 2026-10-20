# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Bounded real-daemon catchup using existing upstream ALP/public-plugin oracles.

This schedules native transactions and blocks, not an alternative indexer,
token implementation or plugin engine. Any failure ends the series.
"""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import plugin_alp_service as combined

alp, service, runner = combined.alp, combined.service, combined.runner
NATIVE_CASES = (
    ("callback_capacity_reason_survives_drain", 10),
    ("callback_notification_reason_is_not_relabelled", 8),
    ("service_frontier_coalesces_ordered_invalidations", 25),
    ("service_frontier_stop_discards_unpublished_range", 21),
    ("service_frontier_notification_failure_is_terminal", 8),
)
RAPID_BLOCKS = 300
FINAL_HEIGHT = 402


def native_controls(binary, work, blocked):
    results = []
    for name, assertions in NATIVE_CASES:
        case = runner.PREFIX + name
        streams = runner.execute([str(binary), "--run_test=" + case,
            "--report_level=short", "--log_level=message", "--color_output=no"], work, blocked)
        results.append(dict(name=name, **runner.parse_streams(streams, (case,), assertions)))
    return results


def observe(binary, work, scripts):
    root = Path(tempfile.mkdtemp(prefix="ergon-upstream-", dir=work)).resolve()
    data = root / "data"
    data.mkdir()
    daemons = []
    def start(phase):
        daemon = service.Daemon(binary, root, data, phase, public_plugin=True)
        daemons.append(daemon)
        daemon.ready()  # Existing native/listener waits, no extra retry or deadline.
        return daemon
    try:
        daemon = start("catchup-initial")
        # One RPC per block: allow native callbacks to advance independently of
        # the HTTP projection, without imposing Chronik catchup between blocks.
        hashes = [daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
                  for _ in range(101)]
        daemon.tip(101, hashes[-1])
        funding = daemon.rpc("getblock", (hashes[0], 2))["tx"][0]
        runner.require(all(o["value"] == 0 for o in funding["vout"]), "nonzero catchup funding refused")
        wires, ids = [], []
        for op in range(4):
            inputs = ([(funding["txid"], 0, True)] if op == 0 else
                      [(ids[parent], out, False) for parent, out in combined.INPUTS[op]])
            script = alp.bind_script(scripts, op, ids[0] if ids else "00" * 32) + b"\x04ERGN"
            wire = alp.transaction(inputs, [script, b"\x51"] +
                                   ([b"\x52" if op == 2 else b"\x51"] if op < 3 else []))
            wires.append(wire)
            ids.append(alp.submit(daemon, wire))
            daemon.pool(ids)
        times = [daemon.rpc("getmempoolentry", (txid,))["time"] for txid in ids]
        runner.require(all(type(t) is int and t > 0 for t in times), "missing catchup native first-seen")
        combined.snapshot(daemon, ids, wires, times, -1, None)
        confirmed = daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        daemon.tip(102, confirmed)
        daemon.pool([])
        expected = combined.snapshot(daemon, ids, wires, times, 102, confirmed)
        index = data / "regtest/chronik-upstream-regtest"
        inputs = data / "regtest/chronik-upstream-regtest-plugin-inputs"
        identity = runner.regular(index / "ergon-identity.json")
        runner.require(b'"plugins":true' in identity, "catchup plugin identity missing")
        original_inputs = tuple(runner.regular(inputs / p) for p in ("ergon_public_probe.py", "plugins.toml"))
        for _ in range(RAPID_BLOCKS):
            tip = daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        daemon.tip(FINAL_HEIGHT, tip)
        daemon.pool([])
        runner.require(combined.snapshot(daemon, ids, wires, times, 102, confirmed) == expected,
                       "ALP/plugin snapshot drift after native advance")
        daemon.stop()
        daemon = start("catchup-reopen")
        daemon.tip(FINAL_HEIGHT, tip)
        daemon.pool([])
        runner.require(combined.snapshot(daemon, ids, wires, times, 102, confirmed) == expected,
                       "ALP/plugin snapshot drift after same-index process reopen")
        runner.require(daemon.rpc("getconnectioncount") == 0 and
                       runner.regular(index / "ergon-identity.json") == identity and
                       tuple(runner.regular(inputs / p) for p in ("ergon_public_probe.py", "plugins.toml")) == original_inputs and
                       not (inputs / "__pycache__").exists() and not (index / "probe.failed").exists(),
                       "catchup index/input/peer/failure-marker drift")
        runner.require(daemon.http("/ws")[0] == 501 and daemon.http("/broadcast-tx", "POST")[0] == 501,
                       "catchup service capability widened")
        daemon.stop()
        return {"rapid_native_blocks": RAPID_BLOCKS, "final_height": FINAL_HEIGHT,
            "genesis_mint_send_burn_and_public_plugin": True, "same_index_process_reopen": True,
            "native_first_seen_and_ALP_plugin_snapshot_preserved": True,
            "native_input_output_value_and_fee": 0, "daemon_processes": 2,
            "positive_joined_stops": 2, "external_connections": 0,
            "websocket_and_broadcast_disabled": True}
    finally:
        cleanup_failed = False
        for daemon in daemons:
            try:
                daemon.cleanup()
            except Exception:
                # Attempt every owned process group even if an earlier cleanup
                # fails. Retain the unverified root and emit no passing report.
                cleanup_failed = True
        runner.require(not cleanup_failed, "catchup daemon cleanup incomplete")
        runner.remove_owned(root, work)


def series(binary, work, scripts, repetitions):
    runner.require(type(repetitions) is int and 1 <= repetitions <= 10, "finite catchup repetitions required")
    result = None
    for _ in range(repetitions):
        current = observe(binary, work, scripts)
        runner.require(result is None or current == result, "catchup lifecycle instability")
        result = current
    return result


def public_report(payload, forbidden):
    raw = runner.canonical(payload)
    runner.require(not any(str(p).encode() in raw for p in forbidden), "private catchup report binding")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "build-root", "work-parent", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    source, build, work = (p.resolve(strict=True) for p in (args.source_root, args.build_root, args.work_parent))
    runner.require(type(args.repetitions) is int and 1 <= args.repetitions <= 10, "finite catchup repetitions required")
    report = args.report.absolute()
    runner.require(report.parent.resolve(strict=True) == report.parent and not report.exists() and
                   not report.is_symlink(), "new canonical catchup report required")
    runner.require(not any(report.is_relative_to(p) for p in (source, build, work)) and
                   not any(work.is_relative_to(p) or p.is_relative_to(work) for p in (source, build)),
                   "separate catchup report and disposable parent required")
    sys.path.insert(0, str(source / "tools/engineering"))
    from check_ergon_testnet import validate
    from check_chronik_service_catchup import input_binding
    validate(source)
    test_binding = input_binding(source)
    manifest = source / "docs/engineering/chronik/upstream-host-adaptation-v1.json"
    binding = runner.bind_build(source, build, "regtest-plugins", manifest)
    binary, native = build / "src/bitcoind", build / "src/test/test_bitcoin"
    hashes = tuple(runner.digest(runner.regular(p)) for p in (binary, native))
    blocked = (source, build, binary, native)
    controls = native_controls(native, work, blocked)
    scripts = alp.templates(runner.execute([str(native), "--run_test=" + alp.TEMPLATE_CASE,
        "--report_level=short", "--log_level=message", "--color_output=no"], work, blocked))
    result = series(binary, work, scripts, args.repetitions)
    runner.require(hashes == tuple(runner.digest(runner.regular(p)) for p in (binary, native)) and
                   binding == runner.bind_build(source, build, "regtest-plugins", manifest) and
                   test_binding == input_binding(source), "catchup source/build/binary drift")
    validate(source)
    payload = public_report({"schema": "ergon-upstream-regtest-service-catchup-observation/v1",
        "knowledge_status": "Observed", "proof_ceiling": "bounded_regtest_service_observation",
        "binding": binding, "test_inputs_sha256": test_binding, "binary_sha256": hashes[0],
        "native_test_binary_sha256": hashes[1], "native_controls": controls,
        "native_control_assertions": 72, "per_native_process_timeout_seconds": 60,
        "template_case": alp.TEMPLATE_CASE, "template_assertions": alp.TEMPLATE_ASSERTIONS,
        "upstream_template_sha256": [runner.digest(raw) for raw in scripts],
        "repetitions": args.repetitions, "fresh_daemon_processes": 2 * args.repetitions,
        "result": result, "child_environment": dict(runner.ENV, TMPDIR="<runner-created-unique-directory>"),
        "disposable_roots_removed": True, "binary_to_source_provenance": "external-build-record-required",
        "historical_timeouts": "retained-not-replaced-or-promoted-by-this-observation"}, (*blocked, work))
    runner.write_report(report, payload)
    print("PASS: compact frontier controls and bounded ALP/public-plugin native catchup with process reopen")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print("REFUSED: catchup; class=" + runner.refusal_class(error), file=sys.stderr)
        sys.exit(1)

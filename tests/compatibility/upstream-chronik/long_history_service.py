# SPDX-License-Identifier: MIT
"""Reconstruct a real 5000-block regtest history using upstream Chronik."""
import argparse
from pathlib import Path
import subprocess
import sys
import cold_rebuild_service as cold

runner = cold.runner
HISTORY_HEIGHT = 5000
STARTUP_TIMEOUT_SECONDS = 120


def series(binary, work, scripts, repetitions):
    runner.require(type(repetitions) is int and 1 <= repetitions <= 3,
                   "finite long-history repetitions required")
    first = None
    for _ in range(repetitions):
        result = cold.observe(binary, work, scripts, final_height=HISTORY_HEIGHT,
                              startup_timeout=STARTUP_TIMEOUT_SECONDS)
        runner.require(result["initial_tip"] == HISTORY_HEIGHT and
                       result["final_tip"] == HISTORY_HEIGHT + 1 and
                       result["corpus_height"] == 102 and
                       result["retained_capture_window"] == 288 and
                       result["daemon_processes"] == 3 and
                       result["external_connections"] == 0 and
                       result["upstream_cold_reconstruction_and_same_index_reopen"] and
                       result["ALP_and_public_plugin_oracles"] and
                       result["historical_first_seen"] == "explicit-unknown-zero-not-original-mempool-time",
                       "long-history scenario or claim drift")
        runner.require(first is None or result == first, "long-history result instability")
        first = result
    return first


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "build-root", "work-parent", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    runner.require(type(args.repetitions) is int and 1 <= args.repetitions <= 3,
                   "finite long-history repetitions required")
    source, build, work, report = cold.report_paths(args.source_root, args.build_root,
                                                 args.work_parent, args.report)
    sys.path.insert(0, str(source / "tools/engineering"))
    from check_chronik_long_history import validate
    validate(source)
    manifest = source / "docs/engineering/chronik/upstream-host-adaptation-v1.json"
    binding = runner.bind_build(source, build, "regtest-plugins", manifest)
    binary, native = build / "src/bitcoind", build / "src/test/test_bitcoin"
    hashes = tuple(runner.digest(runner.regular(p)) for p in (binary, native))
    forbidden = (source, build, work, binary, native)
    scripts = cold.alp.templates(runner.execute([str(native), "--run_test=" + cold.alp.TEMPLATE_CASE,
        "--report_level=short", "--log_level=message", "--color_output=no"], work, forbidden))
    result = series(binary, work, scripts, args.repetitions)
    runner.require(hashes == tuple(runner.digest(runner.regular(p)) for p in (binary, native)) and
                   binding == runner.bind_build(source, build, "regtest-plugins", manifest),
                   "long-history source/build/binary drift")
    validate(source)
    payload = cold.public_report({"schema": "ergon-upstream-long-history-observation/v1",
        "knowledge_status": "Observed", "proof_ceiling": "bounded_regtest_service_observation",
        "binding": binding, "binary_sha256": hashes[0], "native_test_binary_sha256": hashes[1],
        "history_height": HISTORY_HEIGHT, "startup_timeout_seconds": STARTUP_TIMEOUT_SECONDS,
        "inherited_startup_timeout_seconds": 30, "native_process_timeout_seconds": 60,
        "repetitions": args.repetitions, "fresh_daemon_processes": 3 * args.repetitions,
        "result": result, "disposable_roots_removed": True,
        "child_environment": dict(runner.ENV, TMPDIR="<runner-created-unique-directory>"),
        "binary_to_source_provenance": "external-build-record-required",
        "mainnet_admission": False, "historical_timeouts": "retained-not-replaced-or-promoted"}, forbidden)
    runner.write_report(report, payload)
    print("PASS: upstream reconstruction of 5000 native regtest blocks, historical ALP/plugins and process reopen")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print("REFUSED: long history; class=" + runner.refusal_class(error), file=sys.stderr)
        sys.exit(1)

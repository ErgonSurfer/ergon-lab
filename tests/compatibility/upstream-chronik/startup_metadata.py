# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Verify lock-free startup graph materialization through exact native leaves."""
import argparse
from pathlib import Path
import subprocess
import sys
import run_repetitions as runner

CASES = (
    ("startup_materialization_allows_native_progress_without_borrowing", 9),
    ("startup_materialization_storage_drift_is_not_copied_or_accepted", 5),
    ("startup_materialization_exception_keeps_graph_unpublished", 6),
)


def controls(binary, work, forbidden, repetitions):
    runner.require(type(repetitions) is int and 1 <= repetitions <= 10,
                   "finite startup metadata repetitions required")
    first = {}
    results = []
    for repetition in range(repetitions):
        for name, assertions in CASES:
            case = runner.PREFIX + name
            streams = runner.execute([str(binary), "--run_test=" + case,
                "--report_level=short", "--log_level=message", "--color_output=no"],
                work, forbidden)
            observation = runner.parse_streams(streams, (case,), assertions)
            runner.require(name not in first or observation == first[name],
                           "startup metadata fresh-process instability")
            first.setdefault(name, observation)
            results.append({"repetition": repetition + 1, "name": name, **observation})
    return results


def report_paths(source, build, work, report):
    source, build, work = (p.resolve(strict=True) for p in (source, build, work))
    report = report.absolute()
    runner.require(report.parent.resolve(strict=True) == report.parent and
                   not report.exists() and not report.is_symlink(),
                   "new canonical startup metadata report required")
    runner.require(not any(report.is_relative_to(p) for p in (source, build, work)) and
                   not any(work.is_relative_to(p) or p.is_relative_to(work)
                           for p in (source, build)) and
                   not source.is_relative_to(build) and not build.is_relative_to(source),
                   "separate startup metadata source/build/work/report roots required")
    return source, build, work, report


def public_report(payload, forbidden):
    raw = runner.canonical(payload)
    runner.require(not any(str(path).encode() in raw for path in forbidden),
                   "private startup metadata report binding")
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "build-root", "work-parent", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    source, build, work, report = report_paths(
        args.source_root, args.build_root, args.work_parent, args.report)
    sys.path.insert(0, str(source / "tools/engineering"))
    from check_chronik_startup_metadata import validate, input_binding
    validate(source)
    test_binding = input_binding(source)
    manifest = source / "docs/engineering/chronik/upstream-host-adaptation-v1.json"
    binding = runner.bind_build(source, build, "regtest-plugins", manifest)
    binary = build / "src/test/test_bitcoin"
    binary_hash = runner.digest(runner.regular(binary))
    forbidden = (source, build, work, binary)
    results = controls(binary, work, forbidden, args.repetitions)
    runner.require(binary_hash == runner.digest(runner.regular(binary)) and
                   binding == runner.bind_build(source, build, "regtest-plugins", manifest) and
                   test_binding == input_binding(source) and not list(work.iterdir()),
                   "startup metadata source/build/binary or cleanup drift")
    validate(source)
    payload = public_report({
        "schema": "ergon-upstream-startup-metadata-observation/v1",
        "knowledge_status": "Observed",
        "proof_ceiling": "bounded_regtest_component_observation",
        "binding": binding,
        "test_inputs_sha256": test_binding,
        "native_test_binary_sha256": binary_hash,
        "cases": [{"name": name, "assertions": assertions} for name, assertions in CASES],
        "repetitions": args.repetitions,
        "fresh_native_processes": len(CASES) * args.repetitions,
        "passing_assertions": sum(assertions for _, assertions in CASES) * args.repetitions,
        "results": results,
        "per_process_timeout_seconds": 60,
        "automatic_retry": False,
        "child_environment": dict(runner.ENV, TMPDIR="<runner-created-unique-directory>"),
        "disposable_roots_removed": True,
        "binary_to_source_provenance": "external-build-record-required",
        "historical_timeouts": "retained-not-replaced-or-promoted-by-this-observation",
    }, forbidden)
    runner.write_report(report, payload)
    print("PASS: startup graph materialization outside native locks; exact failure fences retained")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        print("REFUSED: startup metadata; class=" + runner.refusal_class(error), file=sys.stderr)
        sys.exit(1)

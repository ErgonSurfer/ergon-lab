# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Public native plugin restart/rebuild witnesses, never daemon activation."""
import argparse
import json
from pathlib import Path
import re
import sys
import tempfile
import run_repetitions as runner

PREFIX = "chronik_upstream_plugin_restart_tests/"
# Exact reviewed native selections, never inferred from observed text.
PHASES = (
    ("seed-v1", "seed_v1", 239),
    ("reopen-v1", "reopen_v1", 237),
    ("refuse-v2", "refuse_v2", 222),
    ("reopen-v1-after-refusal", "reopen_v1", 237),
    ("interrupted-rebuild", "interrupted_rebuild", 222),
    ("failed-reopen-refused", "failed_reopen_refused", 218),
    ("rebuild-v2", "rebuild_v2", 236),
    ("reopen-v2", "reopen_v2", 237),
)
POSITIVE = {"seed-v1", "reopen-v1", "reopen-v1-after-refusal", "rebuild-v2", "reopen-v2"}


def commitment(streams, label, required):
    lines = [line for stream in streams for line in stream.splitlines() if line.startswith(label)]
    runner.require(len(lines) == int(required), "missing or ambiguous restart commitment")
    if not required:
        return None
    match = re.fullmatch(re.escape(label) + r"([0-9a-f]{64})", lines[0])
    runner.require(match is not None, "malformed restart commitment")
    return match[1]


def observe(binary, work, forbidden, repetitions):
    runner.require(1 <= repetitions <= 20, "repetitions outside bounded range")
    runner.require(all(n > 0 for _, _, n in PHASES), "native assertion selections not governed")
    previous = None
    for _ in range(repetitions):
        root = Path(tempfile.mkdtemp(prefix="ergon-upstream-", dir=work)).resolve()
        try:
            rows = []
            baseline = None
            corpus_digest = None
            original_identity = None
            for role, case, assertions in PHASES:
                if corpus_digest is not None:
                    runner.require(runner.digest(runner.regular(root / "corpus.bin")) == corpus_digest,
                                   "native corpus changed between phases")
                streams = runner.execute([str(binary), "--run_test=" + PREFIX + case,
                    "--report_level=short", "--log_level=message", "--color_output=no",
                    "--", "-ergonpluginrestartroot=" + str(root)], work, (*forbidden, root))
                row = runner.parse_streams(streams, (PREFIX + case,), assertions)
                good = role in POSITIVE
                view = commitment(streams, "Restart view: ", good)
                source = commitment(streams, "Restart source: ", good)
                if good:
                    if baseline is None:
                        baseline = (view, source)
                    runner.require((view, source) == baseline, "restart/rebuild view or native source drift")
                new_corpus = runner.digest(runner.regular(root / "corpus.bin"))
                runner.require(corpus_digest is None or new_corpus == corpus_digest, "native corpus rewritten")
                corpus_digest = new_corpus
                identity = runner.regular(root / "index-v1/ergon-identity.json")
                runner.require(original_identity is None or identity == original_identity,
                               "original plugin identity rewritten")
                original_identity = identity
                runner.require(not any(root.rglob("probe.lock")), "native owner not positively closed")
                runner.require(not (root / "index-v1/probe.failed").exists(), "original plugin index quarantined")
                rows.append(dict(role=role, view_sha256=view, native_tip=source, **row))
            observation = {"phases": rows, "native_corpus_sha256": corpus_digest,
                           "original_identity_sha256": runner.digest(original_identity)}
            runner.require(previous is None or observation == previous, "fresh-process restart signature instability")
            previous = observation
        finally:
            runner.remove_owned(root, work)
    return {**previous, "repetitions": repetitions, "fresh_processes": repetitions * len(PHASES),
            "per_process_timeout_seconds": 60, "result": "pass",
            "claims": "synthetic component restart and new-root upstream rebuild only"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "build-root", "work-parent", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=10)
    args = parser.parse_args()
    try:
        source, build, work = (p.resolve(strict=True) for p in (args.source_root, args.build_root, args.work_parent))
        report = args.report.absolute()
        runner.require(report.parent.resolve(strict=True) == report.parent and not report.exists() and not report.is_symlink(),
                       "canonical new report required")
        runner.require(not any(report.is_relative_to(p) for p in (source, build, work)),
                       "report must be outside source/build/disposable parent")
        runner.require(not any(work.is_relative_to(p) or p.is_relative_to(work) for p in (source, build)),
                       "separate disposable work parent required")
        sys.path.insert(0, str(source / "tools/engineering"))
        from check_ergon_testnet import validate
        validate(source)
        manifest = source / "docs/engineering/chronik/upstream-host-adaptation-v1.json"
        binding = runner.bind_build(source, build, "plugins", manifest)
        binary = build / "src/test/test_bitcoin"
        binary_hash = runner.digest(runner.regular(binary))
        result = observe(binary, work, (source, build, binary), args.repetitions)
        runner.require(binary_hash == runner.digest(runner.regular(binary)) and
                       binding == runner.bind_build(source, build, "plugins", manifest), "build/source changed during restart")
        validate(source)
        payload = {"schema": "ergon-plugin-restart-observation/v1", "knowledge_status": "Observed",
            "proof_ceiling": "component_observation", "binary_sha256": binary_hash,
            "binding": binding, "result": result}
        raw = runner.canonical(payload)
        runner.require(not any(str(p).encode() in raw for p in (source, build, work, binary)), "private path in report")
        with report.open("xb") as out:
            out.write(raw)
        print("PASS: eight phases per repetition; native source/view parity and cleanup; report sha256=" + runner.digest(raw))
    except Exception as error:
        # Never serialize exception messages: they may contain operational paths.
        print("FAIL-CLOSED: " + type(error).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: MIT
"""Public synthetic deployment-profile gates, never a deployment or cutover.

Inherited groups keep their prebound assertion counts and ten repetitions.
Additional groups require exact source-declared cases, one strict positive
passing-assertion summary and three byte-stable fresh-process observations.
The report records these newly observed counts; it is not independent proof.
"""
import argparse
import json
from pathlib import Path
import re
import struct
import sys
import tempfile

import run_repetitions as runner
import historical_client_service as clients
import historical_shadow_service as shadow
import alp_service as alp

SUITES = (
    "chronik_upstream_regtest_tests", "chronik_upstream_historical_client_tests",
    "chronik_upstream_historical_admission_tests", "chronik_upstream_journal_replay_tests",
    "chronik_upstream_journal_shutdown_tests", "chronik_upstream_continuous_journal_tests",
    "chronik_upstream_shadow_owner_tests")
JOURNAL = ("validation_event_journal_tests", "validation_event_journal_native_tests")

def elf_stack(raw):
    runner.require(len(raw) >= 64 and raw[:6] == b"\x7fELF\x02\x01", "ELF64 little-endian required")
    runner.require(struct.unpack_from("<H", raw, 18)[0] == 62, "Linux x86_64 ELF required")
    offset = struct.unpack_from("<Q", raw, 32)[0]
    size, count = struct.unpack_from("<HH", raw, 54)
    runner.require(size >= 56 and 0 < count <= 1024 and offset + size * count <= len(raw), "ELF table bound")
    flags = [struct.unpack_from("<I", raw, offset + size * i + 4)[0] for i in range(count)
             if struct.unpack_from("<I", raw, offset + size * i)[0] == 0x6474e551]
    runner.require(flags == [6], "exactly one RW non-executable GNU_STACK required")

def case_groups(text, plugins, shadow_enabled):
    """Small closed preprocessor subset used by the governed native test file."""
    active, condition, suite, groups = True, [], None, {}
    flags = {"ENABLE_CHRONIK_UPSTREAM_REGTEST_PLUGINS": plugins,
             "ENABLE_CHRONIK_UPSTREAM_SHADOW": shadow_enabled}
    for line in text.splitlines():
        if line.startswith("#ifdef "):
            name = line.split()[1]
            runner.require(name in flags, "unknown native test conditional")
            condition.append(active); active = active and flags[name]; continue
        if line == "#endif":
            runner.require(bool(condition), "unbalanced native conditional")
            active = condition.pop(); continue
        match = re.match(r"BOOST_FIXTURE_TEST_SUITE\((\w+),", line)
        if match:
            suite = match[1]
            if active:
                runner.require(suite not in groups, "duplicate native suite")
                groups[suite] = []
        match = re.match(r"BOOST_AUTO_TEST_CASE\((\w+)\)", line)
        if active and match:
            runner.require(suite in groups, "case without native suite")
            groups[suite].append(suite + "/" + match[1])
    runner.require(not condition, "unfinished native conditional")
    return groups

def assertion_count(streams):
    matches = [re.fullmatch(r"  ([1-9][0-9]*) assertions out of ([1-9][0-9]*) passed", line)
               for text in streams for line in text.splitlines() if " assertions out of " in line]
    runner.require(len(matches) == 1 and matches[0] is not None and matches[0][1] == matches[0][2],
                   "missing, malformed, duplicate or failing assertion summary")
    return int(matches[0][1])

def native_groups(source, profile):
    groups = case_groups((source / "src/test/validation_tests.cpp").read_text(), False, False)
    result = [(name, tuple(groups[name])) for name in JOURNAL]
    if profile != "compiled-out":
        groups = case_groups((source / "src/test/chronik_upstream_regtest_tests.cpp").read_text(),
                             profile == "regtest-plugins", True)
        runner.require(set(groups) == set(SUITES), "native deployment suite inventory drift")
        result += [(name, tuple(groups[name])) for name in SUITES]
    runner.require(all(cases for _, cases in result), "empty native group")
    return result

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "build-root", "work-parent", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--profile", choices=("regtest", "regtest-plugins", "compiled-out"), required=True)
    args = parser.parse_args()
    source, build, work = (p.resolve(strict=True) for p in (args.source_root, args.build_root, args.work_parent))
    runner.require(not source.is_relative_to(build) and not build.is_relative_to(source)
                   and not work.is_relative_to(source) and not source.is_relative_to(work)
                   and not work.is_relative_to(build) and not build.is_relative_to(work), "separate work roles required")
    sys.path.insert(0, str(source / "tools/engineering"))
    from check_ergon_testnet import validate
    validate(source)
    manifest = source / "docs/engineering/chronik/upstream-host-adaptation-v1.json"
    binding = runner.bind_build(source, build, args.profile, manifest)
    binary = build / "src/bitcoind"
    tests = build / "src/test/test_bitcoin"
    before = [runner.digest(runner.regular(p)) for p in (binary, tests)]
    for path in (binary, tests):
        elf_stack(runner.regular(path))
    observations = []
    for name, cases in native_groups(source, args.profile):
        first = None
        for _ in range(3):
            streams = runner.execute([str(tests), "--run_test=" + name, "--report_level=short",
                "--log_level=message", "--color_output=no"], work, (source, build, binary, tests))
            row = runner.parse_streams(streams, cases, assertion_count(streams))
            runner.require(first is None or first == row, "new native group count/output instability")
            first = row
        observations.append({"group": name, "repetitions": 3, **first})
        print("PASS continuous native: " + name + "; cases=" + str(len(cases)), flush=True)
    client_result = shadow_result = None
    if args.profile != "compiled-out":
        root = Path(tempfile.mkdtemp(prefix="ergon-upstream-continuous-client-", dir=work)).resolve()
        try:
            client_result = clients.observe(binary, tests, root)
        finally:
            runner.remove_owned(root, work)
        streams = runner.execute([str(tests), "--run_test=" + alp.TEMPLATE_CASE,
            "--report_level=short", "--log_level=message", "--color_output=no"], work, (source, build))
        scripts = alp.templates(streams)
        shadow_result = shadow.observe(binary, work, scripts, plugins=args.profile == "regtest-plugins")
    runner.require(before == [runner.digest(runner.regular(p)) for p in (binary, tests)], "binary drift")
    runner.require(binding == runner.bind_build(source, build, args.profile, manifest), "source/build drift")
    validate(source)
    payload = {"schema": "ergon-continuous-service-observation/v1", "knowledge_status": "Observed",
        "proof_ceiling": "synthetic-regtest-component", "profile": args.profile,
        "binary_sha256": before[0], "test_binary_sha256": before[1], "binding": binding,
        "ELF_GNU_STACK": "RW-non-executable", "native_groups": observations,
        "historical_client": client_result, "historical_shadow": shadow_result,
        "mainnet_submission": False, "deployment": False, "cutover": False,
        "disposable_roots_removed": True, "inherited_test_budgets": "unchanged"}
    raw = runner.canonical(payload)
    runner.require(not any(str(p).encode() in raw for p in (source, build, work, binary, tests)), "host binding in report")
    runner.write_report(args.report, payload)
    print("PASS: continuous service synthetic component; no deployment or readiness promotion")

if __name__ == "__main__":
    main()

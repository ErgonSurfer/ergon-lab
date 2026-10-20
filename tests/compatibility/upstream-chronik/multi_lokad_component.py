#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Public synthetic metadata/dispatch witness; no daemon or token-validity claim."""
import argparse
import json
from pathlib import Path
import re
import sys
import run_repetitions as runner

UNIT_PREFIX = "plugin::ergon_multi_lokad_tests::"
UNIT_NAMES = frozenset({"legacy_bytes_unchanged", "both_identifiers_preserve_order",
    "single_entry_list", "exact_list_limit", "empty_list_refused",
    "oversized_list_refused_before_entries", "duplicate_refused",
    "legacy_wrong_width_refused", "list_wrong_width_refused", "list_nonbytes_refused",
    "string_and_tuple_refused"})
MARKER = "PASS_PUBLIC_MULTI_LOKAD legacy=1 multi=2 combined_once=1 unmatched=1 no_extra_call=1"


def unit_result(streams):
    text = "\n".join(streams)
    names = re.findall(r"^test " + re.escape(UNIT_PREFIX) + r"(\w+) \.\.\. ok$", text, re.M)
    runner.require(len(names) == 11 and set(names) == UNIT_NAMES,
                   "missing, duplicate or unexpected multi-identifier case")
    summaries = re.findall(r"^test result: ok\. 11 passed; 0 failed; 0 ignored; 0 measured; "
                           r"[0-9]+ filtered out; finished in [0-9]+\.[0-9]+s$", text, re.M)
    runner.require(len(summaries) == 1 and "... FAILED" not in text,
                   "multi-identifier suite count/failure mismatch")


def probe_result(streams):
    runner.require(streams[0].splitlines() == [MARKER] and not streams[1],
                   "upstream multi-identifier dispatch witness mismatch")


def artifact(raw, target):
    runner.require(len(raw) <= 4 * 1024 * 1024, "Cargo artifact record exceeds bound")
    executables = []
    for line in raw.decode("utf8", "strict").splitlines():
        row = json.loads(line)
        if (row.get("reason") == "compiler-artifact" and row.get("executable")
                and row["target"]["name"] == "chronik_plugin_impl"
                and row["profile"]["test"] is True):
            path = Path(row["executable"])
            runner.require(path.is_absolute() and path.resolve().is_relative_to(target)
                           and not path.is_symlink(), "redirected Cargo unit executable")
            runner.regular(path)
            executables.append(path)
    runner.require(len(executables) == 1, "Cargo unit executable absent or ambiguous")
    return executables[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--target-dir", required=True, type=Path)
    parser.add_argument("--cargo-artifacts", required=True, type=Path)
    parser.add_argument("--work-parent", required=True, type=Path)
    args = parser.parse_args()
    source, target, work = (p.resolve(strict=True) for p in
                            (args.source_root, args.target_dir, args.work_parent))
    runner.require(source != target and not target.is_relative_to(source) and
                   source != work and not work.is_relative_to(source), "generated work inside source")
    sys.path.insert(0, str(source / "tools/engineering"))
    from check_chronik_multi_lokad import validate
    validate(source)
    unit = artifact(runner.regular(args.cargo_artifacts), target)
    probe = target / "debug/examples/ergon-public-multi-lokad"
    runner.regular(probe)
    unit_result(runner.execute([str(unit), "plugin::ergon_multi_lokad_tests", "--test-threads=1"],
                               work, (source, target)))
    plugins = source / "tests/compatibility/upstream-chronik/plugins"
    probe_result(runner.execute([str(probe), str(plugins), str(plugins / "multi_lokad.toml")],
                                work, (source, target)))
    print("PASS:11 metadata cases;6 upstream synthetic transactions;legacy bytes preserved;combined match dispatches once")


if __name__ == "__main__":
    main()

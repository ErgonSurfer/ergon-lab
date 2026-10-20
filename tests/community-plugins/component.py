# SPDX-License-Identifier: MIT
"""Bounded real-upstream component; no daemon, DB or deployment claim."""
import argparse
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compatibility/upstream-chronik"))
import run_repetitions as runner

UNIT_NAMES = frozenset(
    "ergon_operator_bundle::tests::" + name for name in (
        "native_network_separates_plugin_binding_without_changing_regtest",
        "path_independent_and_explicit_admission", "changed_source_refused", "changed_config_refused",
        "wrong_and_malformed_binding_refused", "interpreter_identity_bound", "missing_inputs_refused",
        "extra_cache_or_binary_refused", "directory_refused", "non_text_refused",
        "oversize_source_and_config_refused", "too_many_inputs_refused", "total_size_bound",
        "framed_fields_are_unambiguous", "aliases_and_external_write_permissions_refused")) | frozenset(
    "ergon_plugin_access::tests::" + name for name in (
        "disabled_admits_no_plugin", "synthetic_scope_unchanged", "operator_only_loaded_names",
        "invalid_groups_fail_closed", "group_boundary", "immutable_shared_capability")) | frozenset(
    "ergon_plugin_access::query_tests::" + name for name in (
        "operator_prefix_cursor_and_empty_prefix", "optional_cursor_does_not_leak_to_other_scopes",
        "duplicate_and_unknown_query_keys_refused", "noncanonical_hex_and_encoded_delimiters_refused",
        "group_limits_equal_existing_upstream_maximum", "legacy_page_contract_preserved",
        "individual_and_total_cursor_bounds", "cursor_prefix_relation_remains_upstream_semantics"))
MARKER = "PASS_COMMUNITY_PLUGINS loaded=4 token_families=2 carriers=2 invalid_refusals=2"
TEMPLATES = ("ALP_GENESIS", "ALP_SEND", "SLP_GENESIS", "SLP_SEND")


def unit_result(streams):
    text = "\n".join(streams)
    names = re.findall(r"^test ([a-zA-Z0-9_:]+) \.\.\. ok$", text, re.M)
    runner.require(len(names) == len(UNIT_NAMES) == 29 and set(names) == UNIT_NAMES,
                   "missing, duplicate or unexpected operator contract test")
    summaries = re.findall(r"^test result: ok\. 29 passed; 0 failed; 0 ignored; 0 measured; "
                           r"0 filtered out; finished in [0-9]+\.[0-9]+s$", text, re.M)
    runner.require(len(summaries) == 1 and "... FAILED" not in text, "operator contract result mismatch")


def probe_result(streams):
    runner.require(streams == (MARKER + "\n", ""), "community upstream dispatch signature mismatch")


def templates(streams):
    runner.require(not streams[1], "template stderr is not empty")
    rows = []
    for line in streams[0].splitlines():
        match = re.fullmatch(r"COMMUNITY_TEMPLATE ([A-Z_]+) ([0-9a-f]+)", line)
        runner.require(match is not None and len(match[2]) % 2 == 0, "malformed upstream template")
        raw = bytes.fromhex(match[2])
        runner.require(0 < len(raw) <= 256, "oversized upstream template")
        runner.require(raw.count(bytes([0x42]) * 32) == int(match[1].endswith("_SEND")),
                       "upstream template placeholder mismatch")
        rows.append((match[1], raw))
    runner.require(tuple(name for name, _ in rows) == TEMPLATES, "missing or reordered upstream template")
    return dict(rows)


def artifact(raw, target):
    runner.require(len(raw) <= 4 * 1024 * 1024, "Cargo record exceeds bound")
    executables = []
    for line in raw.decode("utf8", "strict").splitlines():
        row = json.loads(line)
        if (row.get("reason") == "compiler-artifact" and row.get("executable")
                and row["target"]["name"] == "ergon-community-plugins"
                and row["target"]["kind"] == ["example"] and row["profile"]["test"] is True):
            path = Path(row["executable"])
            runner.require(path.is_absolute() and not path.is_symlink()
                           and path.resolve(strict=True).is_relative_to(target), "redirected unit executable")
            runner.regular(path)
            executables.append(path)
    runner.require(len(executables) == 1, "missing or ambiguous unit executable")
    return executables[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "target-dir", "cargo-artifacts", "work-parent"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    source, target, work = (p.resolve(strict=True) for p in
                            (args.source_root, args.target_dir, args.work_parent))
    runner.require(not any(p == source or p.is_relative_to(source) or source.is_relative_to(p)
                           for p in (target, work)), "generated work overlaps source")
    sys.path.insert(0, str(source / "tools/engineering"))
    from check_ergon_testnet import validate
    validate(source)
    unit = artifact(runner.regular(args.cargo_artifacts), target)
    example = target / "debug/examples/ergon-community-plugins"
    runner.regular(example)
    unit_result(runner.execute([str(unit), "--test-threads=1"], work, (source, target)))
    probe_result(runner.execute([str(example), str(source / "plugins"),
        str(source / "tests/community-plugins/plugins.toml")], work, (source, target)))
    templates(runner.execute([str(example), "--templates"], work, (source, target)))
    print("PASS:29 exact input/query cases;4 real-upstream plugins;ALP+SLP;4 upstream-built templates")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Finite metadata-loader extension over the signed public catalogue parent."""
import argparse
from pathlib import Path
import re
import tomllib
from check_chronik_upstream_link import current_identity, git, load_json, require
from check_chronik_service_catchup import revision_identity

PARENT = "871987b5ed0f06d2d668e8d55c9422de26ba961a"
TREE = "691b60b8054b889f4d860474ba3fe98961b830fd"
MANIFEST = "docs/engineering/chronik/upstream-multi-lokad-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0064.json"
LOADER = "chronik/chronik-plugin-impl/src/plugin.rs"
API = "chronik/chronik-plugin-impl/src/plugin.py"
CARGO = "chronik/chronik-plugin-impl/Cargo.toml"
SOURCES = frozenset({LOADER, API, CARGO, "chronik/UpstreamLink.cmake",
    "tests/compatibility/upstream-chronik/multi_lokad_probe.rs",
    "tests/compatibility/upstream-chronik/plugins/multi_lokad_public.py",
    "tests/compatibility/upstream-chronik/plugins/single_lokad_public.py",
    "tests/compatibility/upstream-chronik/plugins/multi_lokad.toml"})
POLICY = frozenset({".github/workflows/chronik-upstream-link.yml",
    "docs/engineering/chronik/upstream-multi-lokad.md",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/multi_lokad_component.py",
    "tests/compatibility/upstream-chronik/multi_lokad_component_self_test.py",
    "tools/engineering/check_chronik_multi_lokad.py",
    "tools/engineering/check_chronik_multi_lokad_self_test.py",
    *("tools/engineering/check_chronik_" + name + ".py" for name in (
        "upstream_link", "position_catalogue", "position_catalogue_self_test",
        "long_history", "native_startup", "owned_skip_links", "startup_batches",
        "startup_release", "startup_metadata"))})
MANIFEST_FILES = SOURCES | POLICY
CHANGED = MANIFEST_FILES | {MANIFEST, RECORD}
CASES = frozenset({"legacy_bytes_unchanged", "both_identifiers_preserve_order",
    "single_entry_list", "exact_list_limit", "empty_list_refused",
    "oversized_list_refused_before_entries", "duplicate_refused",
    "legacy_wrong_width_refused", "list_wrong_width_refused", "list_nonbytes_refused",
    "string_and_tuple_refused"})
CONTRACT = {"legacy_bytes": "one-four-byte-identifier-unchanged", "list_minimum": 1,
    "list_maximum": 32, "identifier_bytes": 4, "duplicates": "reject",
    "list_order": "preserved", "tuple_or_iterable": "reject",
    "dispatch": "existing-upstream-once-per-matching-plugin",
    "CPython": "configured-exact3.12", "unit_cases": sorted(CASES),
    "dispatch_transactions": 6, "compile_default": False, "runtime_default": False,
    "daemon_admission_change": False, "consensus_change": False,
    "registry_tuple_change": False, "incoming_evidence": [],
    "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}

PROJECTION_BLOCK = '''    multi_lokad_manifest = source / "docs/engineering/chronik/upstream-multi-lokad-v1.json"
    if multi_lokad_manifest.exists():
        from check_chronik_multi_lokad import PARENT, TREE, MANIFEST_FILES
        layer = json.loads(regular(multi_lokad_manifest))
        require(layer["schema"] == "ergon-upstream-multi-lokad/v1" and
                layer["public_parent"] == {"commit": PARENT, "tree": TREE} and
                [r["path"] for r in layer["files"]] == sorted(MANIFEST_FILES), "multi-lokad projection drift")
        merged = {r["path"]: r for r in rows}
        for row in layer["files"]:
            if row["path"] in merged:
                require(row["before"] == merged[row["path"]]["after"], "multi-lokad projection preimage drift")
            merged[row["path"]] = row
        rows = [merged[p] for p in sorted(merged)]
'''

PLUGIN_IDENTITY_BLOCK = '''    "chronik/chronik-plugin-impl/src/module.rs",
    "chronik/chronik-plugin-impl/src/plugin.rs",
    "chronik/chronik-plugin-impl/src/plugin.py",
'''


def normalize_runner(text):
    from check_chronik_external_plugins import normalize_runner as normalize_external
    text = normalize_external(text)
    if 'multi_lokad_manifest = source / ' not in text:
        return text
    require(text.count(PROJECTION_BLOCK) == 1, "multi-lokad projection protocol drift")
    require(text.count(PLUGIN_IDENTITY_BLOCK) == 1, "multi-lokad runner adapter inputs drift")
    return text.replace(PROJECTION_BLOCK, "", 1).replace(PLUGIN_IDENTITY_BLOCK,
        '    "chronik/chronik-plugin-impl/src/module.rs",\n', 1)


def before_identity(root, path):
    return revision_identity(root, PARENT, path)


def additional_paths(root):
    from check_chronik_external_plugins import additional_paths as external_paths
    return (CHANGED if (root / MANIFEST).exists() else frozenset()) | external_paths(root)


def successor_identity(root, path, previous):
    result = previous
    if (root / MANIFEST).exists():
        rows = [r for r in load_json(root / MANIFEST)["files"] if r["path"] == path]
        if rows:
            require(path in MANIFEST_FILES and len(rows) == 1 and
                    rows[0]["before"] == before_identity(root, path) and
                    previous in (rows[0]["before"], rows[0]["after"]), "multi-lokad successor preimage drift")
            result = rows[0]["after"]
    from check_chronik_external_plugins import successor_identity as external_successor
    return external_successor(root, path, result)


def validate_loader(before, after):
    old = '''    let lokad_id = plugin_instance.getattr("lokad_id")?.call0()?;
    let lokad_id = lokad_id
        .extract::<&[u8]>()
        .map_err(|err| InvalidLokadIdType(err.to_string()))?;
    let lokad_id: LokadId = lokad_id
        .try_into()
        .map_err(|_| InvalidLokadIdLen(lokad_id.len()))?;'''
    new = '    let lokad_ids = parse_lokad_ids(&plugin_instance.getattr("lokad_id")?.call0()?)?;'
    require(before.count(old) == 1 and before.count("lokad_ids: vec![lokad_id]") == 1,
            "multi-lokad public loader preimage drift")
    expected = before.replace(old, new).replace("lokad_ids: vec![lokad_id]", "lokad_ids")
    start = "pub(crate) fn load_plugin"
    require(after[after.index(start):after.index("// Ergon compatibility extension:")].strip()
            == expected[expected.index(start):].strip(), "loader changed imports/version/class/construction semantics")
    for token in ('downcast::<PyBytes>()', 'is_instance_of::<PyBytes>()',
                  'downcast::<PyList>()', '!(1..=32).contains(&list.len())',
                  'Vec::with_capacity(list.len())', 'if ids.contains(&id)',
                  'return Err(DuplicateLokadId.into())', 'ids.push(id)', 'raw.try_into()'):
        require(token in after, "loader datatype/width/count/duplicate law drift")
    require(set(re.findall(r"#\[test\]\s+fn (\w+)\(", after)) == CASES,
            "multi-lokad case selection drift")


def validate(root):
    root = root.resolve(strict=True)
    from check_chronik_external_plugins import additional_paths as external_paths, validate as validate_external, normalize_cargo, normalize_cmake
    extra = external_paths(root)
    if extra:
        validate_external(root)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "multi-lokad parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and manifest["license"] == "MIT" and
            manifest["schema"] == "ergon-upstream-multi-lokad/v1" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT and
            [r["path"] for r in manifest["files"]] == sorted(MANIFEST_FILES), "multi-lokad manifest drift")
    for row in manifest["files"]:
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["before"] == before_identity(root, row["path"]) and
                successor_identity(root, row["path"], row["after"]) == current_identity(root, row["path"]) and row["after"] != row["before"] and
                row["after"]["mode"] == "100644" and
                row["provenance"] == ("A1/MIT" if row["before"] is None else "PUBLIC+A1/MIT") and
                row["reachability"] == ("optional-plugin-build-or-synthetic-test" if row["path"] in SOURCES else "test-or-policy-only"),
                "multi-lokad pre/postimage or provenance drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == CHANGED | extra, "unexpected multi-lokad path")
    validate_loader(git(root, "show", PARENT + ":" + LOADER).decode(), (root / LOADER).read_text())
    runner_path = "tests/compatibility/upstream-chronik/run_repetitions.py"
    runner = (root / runner_path).read_text()
    require(runner.count(PROJECTION_BLOCK) == 1 and normalize_runner(runner) ==
            git(root, "show", PARENT + ":" + runner_path).decode(),
            "multi-lokad changed inherited runner selection/deadline/environment/failure/cleanup law")
    cargo_before = tomllib.loads(git(root, "show", PARENT + ":" + CARGO).decode())
    cargo_after = tomllib.loads((root / CARGO).read_text())
    if extra:
        cargo_after = normalize_cargo(cargo_after)
    require(cargo_after.pop("example") == [{"name": "ergon-public-multi-lokad",
        "path": "../../tests/compatibility/upstream-chronik/multi_lokad_probe.rs"}] and
        cargo_after == cargo_before, "multi-lokad changed crate/dependency boundary")
    for path in ("chronik/Cargo.lock", "chronik/Cargo.toml", "CMakeLists.txt", "src/init.cpp",
        "src/validation.cpp", "src/txmempool.cpp", "src/chainparams.cpp",
        "chronik/chronik-plugin-impl/src/context.rs", "chronik/chronik-plugin-impl/src/etoken.rs",
        "chronik/chronik-plugin-impl/src/etoken.py", "chronik/chronik-indexer/src/indexer.rs",
        "chronik/chronik-cpp/ergon_node_access.h", "chronik/chronik-lib/src/ergon_plugin_fixture.rs"):
        require(current_identity(root, path) == successor_identity(root, path, before_identity(root, path)), "multi-lokad changed frozen engine/native input")
    for path in (LOADER, API, CARGO):
        require(b"Copyright (c) 2024 The Bitcoin developers" in (root / path).read_bytes()[:250], "upstream notice lost")
    cmake = (root / "chronik/UpstreamLink.cmake").read_text()
    if extra:
        cmake = normalize_cmake(cmake)
    before_cmake = git(root, "show", PARENT + ":chronik/UpstreamLink.cmake").decode()
    anchor = '        list(APPEND _identity_inputs chronik/chronik-plugin-impl/src/module.rs\n'
    require(cmake == before_cmake.replace(anchor, anchor +
        '            chronik/chronik-plugin-impl/src/plugin.rs\n            chronik/chronik-plugin-impl/src/plugin.py\n'),
        "multi-lokad adapter binding exceeds metadata source identities")
    from check_change import validate as validate_change
    record = load_json(root / RECORD); validate_change(record, RECORD)
    require(record["change_id"] == "ERGON-CHANGE-0064" and
        record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
        record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question", "ceiling": "record-review-only", "artifacts": []} and
        [r["path"] for r in record["files"]] == sorted(CHANGED - {RECORD}), "multi-lokad record drift")
    for row in record["files"]:
        normalize = lambda v: None if v is None else {("git_blob_sha1" if k == "git_blob" else k): x for k, x in v.items()}
        require(normalize(row["before"]) == before_identity(root, row["path"]) and
                successor_identity(root, row["path"], normalize(row["after"])) == current_identity(root, row["path"]), "multi-lokad record identity drift")
    return CHANGED | extra


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: bounded multi-identifier metadata; existing upstream dispatch and node authority unchanged")

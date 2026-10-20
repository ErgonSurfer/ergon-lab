#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Finite trusted plugin contribution; unchanged upstream engine/node authority."""
import argparse
import copy
from pathlib import Path
import tomllib
from check_chronik_upstream_link import current_identity, git, load_json, require
from check_chronik_service_catchup import revision_identity

PARENT = "385aba7f8939b2327fa3083bc8fa7a2c26f4e152"
TREE = "6d93d8ba3899121a4db71ce3dd56b7379b97817c"
MANIFEST = "docs/engineering/chronik/upstream-external-plugins-v1.json"
RECORD = "docs/engineering/changes/ergon-change-0065.json"
GENERIC = frozenset({
    "chronik/chronik-cpp/ergon_regtest_service.cpp", "chronik/chronik-cpp/ergon_regtest_service.h",
    "chronik/chronik-lib/src/ergon_archival.rs", "chronik/chronik-lib/src/ergon_operator_bundle.rs",
    "chronik/chronik-lib/src/ergon_plugin_access.rs", "chronik/chronik-lib/src/ergon_plugin_fixture.rs",
    "chronik/chronik-lib/src/ergon_service.rs", "chronik/chronik-lib/src/lib.rs", "src/init.cpp",
    "tests/compatibility/upstream-chronik/OPERATOR_BUNDLE.md",
    "tests/compatibility/upstream-chronik/operator_bundle_binding.py",
    "tests/compatibility/upstream-chronik/operator_bundle_binding_self_test.py"})
COMMUNITY = frozenset({
    "plugins/moom_alp_token_index.py", "plugins/moom_nft_index.py",
    "plugins/moom_alp_exch_market.py", "plugins/moom_reward_claim_index.py",
    "plugins/README.md", "plugins/PROTOCOLS.md", "tests/community-plugins/test_protocols.py",
    "tests/community-plugins/upstream_probe.rs", "tests/community-plugins/plugins.toml",
    "tests/community-plugins/component.py", "tests/community-plugins/component_self_test.py"})
SOURCES = GENERIC | COMMUNITY | {"chronik/UpstreamLink.cmake", "chronik/chronik-plugin-impl/Cargo.toml"}
POLICY = frozenset({
    "PUBLICATION_POLICY.md",
    "tools/engineering/check_chronik_external_plugins.py",
    "tools/engineering/check_chronik_external_plugins_self_test.py",
    "tools/engineering/check_chronik_multi_lokad.py",
    "tools/engineering/check_chronik_multi_lokad_self_test.py",
    "tools/engineering/check_chronik_upstream_link.py",
    ".github/workflows/chronik-upstream-link.yml",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/community-plugins/service.py", "tests/community-plugins/service_self_test.py",
    "docs/engineering/chronik/upstream-external-plugins.md"})
MANIFEST_FILES = SOURCES | POLICY
CHANGED = MANIFEST_FILES | {MANIFEST, RECORD}
EXAMPLE = {"name": "ergon-community-plugins", "path": "../../tests/community-plugins/upstream_probe.rs"}
CONTRACT = {
    "compile_default": False, "runtime_default": False, "network": "unpruned-local-regtest",
    "trust": "explicit-approved-Python-not-a-sandbox", "maximum_modules": 32,
    "maximum_source_bytes": 1048576, "maximum_config_bytes": 65536, "maximum_bundle_bytes": 4194304,
    "binding": "path-independent-length-framed-source-config-interpreter-SHA256",
    "drift": "pre-import-and-post-import-checks-not-an-adversarial-filesystem-snapshot",
    "index_adoption_or_wipe": False, "interpreter": "process-once-shared-owner-no-finalization",
    "plugin_names": "retain-existing-client-ABI", "token_families": ["ALP", "SLP"],
    "plugin_authority": "discovery-and-projection-only", "strict_group_hex_bytes": 512,
    "query_bytes": 4096, "group_page_size": 50, "other_page_size": 200, "maximum_page": 1000,
    "component_unit_cases": 28, "Python_API_tests": 41, "offline_binding_tests": 11,
    "upstream_templates": 4, "execution_deadline_seconds": 60,
    "daemon_repetitions_CI": 1, "inherited_cases_bodies_and_budgets": "unchanged",
    "consensus_change": False, "token_engine_change": False, "dependency_tuple_change": False,
    "incoming_evidence": [], "knowledge_status": "Open Question", "evidence_ceiling": "record-review-only"}

CMAKE_BLOCK = '''    list(APPEND _identity_inputs chronik/chronik-lib/src/ergon_operator_bundle.rs
        chronik/chronik-lib/src/ergon_plugin_access.rs)
'''
RUNNER_IDENTITY_BLOCK = '''    "src/test/main.cpp",
    "chronik/chronik-lib/src/ergon_operator_bundle.rs",
    "chronik/chronik-lib/src/ergon_plugin_access.rs",
'''
PROJECTION_BLOCK = '''    external_manifest = source / "docs/engineering/chronik/upstream-external-plugins-v1.json"
    if external_manifest.exists():
        from check_chronik_external_plugins import PARENT, TREE, MANIFEST_FILES
        layer = json.loads(regular(external_manifest))
        require(layer["schema"] == "ergon-upstream-external-plugins/v1" and
                layer["public_parent"] == {"commit": PARENT, "tree": TREE} and
                [r["path"] for r in layer["files"]] == sorted(MANIFEST_FILES), "external plugin projection drift")
        merged = {r["path"]: r for r in rows}
        for row in layer["files"]:
            if row["path"] in merged:
                require(row["before"] == merged[row["path"]]["after"], "external plugin projection preimage drift")
            merged[row["path"]] = row
        rows = [merged[p] for p in sorted(merged)]
'''
CONTRIBUTION_BLOCK = '''### Finite maintainer-contributed plugin release

The maintainer's 2026-10-20 publication instruction permits only the four
standalone protocol plugins `plugins/moom_alp_token_index.py`,
`plugins/moom_nft_index.py`, `plugins/moom_alp_exch_market.py` and
`plugins/moom_reward_claim_index.py`, with exact reviewed postimages bound by
`docs/engineering/chronik/upstream-external-plugins-v1.json` and
`ERGON-CHANGE-0065`. They are explicitly contributed under MIT, not relabeled
as donor bytes. Original module/class and wire identifiers remain for client
compatibility and provenance. Their protocol documentation and synthetic public
tests are reviewed with that finite contribution. This permits no other host
product source, architecture, configuration, endpoint, operator data, private
history, corpus or report, and creates no automatic future export exception.
Unclear third-party rights remain blocked. The exact-SHA, signature, review,
evidence and activation rules below remain unchanged.

'''


def normalize_runner(text):
    if 'external_manifest = source / ' not in text:
        return text
    require(text.count(PROJECTION_BLOCK) == 1, "external plugin projection protocol drift")
    require(text.count(RUNNER_IDENTITY_BLOCK) == 1, "external plugin runner identity drift")
    return text.replace(PROJECTION_BLOCK, "", 1).replace(RUNNER_IDENTITY_BLOCK,
        '    "src/test/main.cpp",\n', 1)


def normalize_cargo(value):
    value = copy.deepcopy(value)
    if EXAMPLE not in value.get("example", []):
        return value
    require(value["example"].count(EXAMPLE) == 1, "duplicate external plugin example")
    value["example"].remove(EXAMPLE)
    return value


def normalize_cmake(text):
    require(text.count(CMAKE_BLOCK) == 1, "external plugin adapter identity binding drift")
    return text.replace(CMAKE_BLOCK, "", 1)


def before_identity(root, path):
    return revision_identity(root, PARENT, path)


def additional_paths(root):
    return CHANGED if (root / MANIFEST).exists() else frozenset()


def successor_identity(root, path, previous):
    if not (root / MANIFEST).exists():
        return previous
    rows = [r for r in load_json(root / MANIFEST)["files"] if r["path"] == path]
    if not rows:
        return previous
    require(path in MANIFEST_FILES and len(rows) == 1 and
            rows[0]["before"] == before_identity(root, path) and
            previous in (rows[0]["before"], rows[0]["after"]), "external plugin successor preimage drift")
    return rows[0]["after"]


def validate(root):
    root = root.resolve(strict=True)
    require(git(root, "rev-parse", PARENT + "^{tree}").decode().strip() == TREE and
            git(root, "merge-base", "--is-ancestor", PARENT, "HEAD") == b"", "external plugin public parent drift")
    manifest = load_json(root / MANIFEST)
    require(set(manifest) == {"$comment", "schema", "license", "public_parent", "contract", "files"} and
            manifest["$comment"] == "SPDX-License-Identifier: MIT" and manifest["license"] == "MIT" and
            manifest["schema"] == "ergon-upstream-external-plugins/v1" and
            manifest["public_parent"] == {"commit": PARENT, "tree": TREE} and
            manifest["contract"] == CONTRACT and
            [r["path"] for r in manifest["files"]] == sorted(MANIFEST_FILES), "external plugin manifest drift")
    for row in manifest["files"]:
        require(set(row) == {"path", "before", "after", "provenance", "reachability"} and
                row["before"] == before_identity(root, row["path"]) and
                row["after"] == current_identity(root, row["path"]) and row["after"] != row["before"] and
                row["after"]["mode"] == "100644" and
                row["provenance"] == ("MAINTAINER-CONTRIBUTION/MIT" if row["path"] in COMMUNITY and row["path"].startswith("plugins/") and row["path"].endswith(".py")
                    else "A1/MIT" if row["before"] is None else "PUBLIC+A1/MIT") and
                row["reachability"] == ("optional-plugin-or-service-build" if row["path"] in SOURCES and row["path"].startswith(("chronik/", "src/"))
                    else "external-optional-plugin" if row["path"].startswith("plugins/") and row["path"].endswith(".py")
                    else "test-or-policy-only"), "external plugin source/provenance identity drift")
    changed = {p.decode() for p in git(root, "diff", "--name-only", "-z", PARENT).split(b"\0") if p}
    changed |= {p.decode() for p in git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0") if p}
    require(changed == CHANGED, "unexpected external plugin path")
    for path in ("chronik/Cargo.lock", "chronik/Cargo.toml", "CMakeLists.txt",
                 "src/validation.cpp", "src/txmempool.cpp", "src/chainparams.cpp",
                 "chronik/chronik-plugin-impl/src/plugin.rs", "chronik/chronik-plugin-impl/src/plugin.py",
                 "chronik/chronik-plugin-impl/src/context.rs", "chronik/chronik-plugin-impl/src/etoken.rs",
                 "chronik/chronik-indexer/src/indexer.rs", "chronik/chronik-db/src/plugins/mod.rs",
                 "chronik/chronik-db/src/plugins/io.rs",
                 "chronik/chronik-cpp/ergon_node_access.h", "src/test/chronik_upstream_regtest_tests.cpp",
                 "tests/compatibility/upstream-chronik/self_test.py"):
        require(current_identity(root, path) == before_identity(root, path), "external plugin changed frozen engine/native test input")
    cargo = "chronik/chronik-plugin-impl/Cargo.toml"
    after_cargo = tomllib.loads((root / cargo).read_text())
    require(after_cargo.get("example", []).count(EXAMPLE) == 1 and normalize_cargo(after_cargo) ==
            tomllib.loads(git(root, "show", PARENT + ":" + cargo).decode()), "external plugin changed dependency boundary")
    require(normalize_cmake((root / "chronik/UpstreamLink.cmake").read_text()) ==
            git(root, "show", PARENT + ":chronik/UpstreamLink.cmake").decode(), "external plugin changed CMake beyond two identity inputs")
    runner_path = "tests/compatibility/upstream-chronik/run_repetitions.py"
    require(normalize_runner((root / runner_path).read_text()) ==
            git(root, "show", PARENT + ":" + runner_path).decode(), "external plugin changed inherited runner law")
    policy = (root / "PUBLICATION_POLICY.md").read_text()
    require(policy.count(CONTRIBUTION_BLOCK) == 1 and policy.replace(CONTRIBUTION_BLOCK, "", 1) ==
            git(root, "show", PARENT + ":PUBLICATION_POLICY.md").decode(), "plugin contribution changed another publication protection")
    for path in ("chronik/chronik-lib/src/ergon_operator_bundle.rs",
                 "chronik/chronik-lib/src/ergon_plugin_access.rs"):
        require((root / path).read_text().startswith("// SPDX-License-Identifier: MIT"), "external plugin SPDX lost")
    text = (root / "chronik/chronik-lib/src/ergon_operator_bundle.rs").read_text()
    for token in ("const MAX_MODULES: usize = 32;", "const MAX_SOURCE: u64 = 1024 * 1024;",
                  "const MAX_CONFIG: u64 = 64 * 1024;", "const MAX_TOTAL: usize = 4 * 1024 * 1024;",
                  "meta.nlink() != 1", "meta.mode() & 0o022 != 0", "bundle.binding != expected",
                  "fs::canonicalize(root)? != root", "bytes.contains(&0)"):
        require(token in text, "operator trust/bounds contract drift")
    from check_change import validate as validate_change
    record = load_json(root / RECORD); validate_change(record, RECORD)
    require(record["change_id"] == "ERGON-CHANGE-0065" and
            record["public_lineage"]["integration_parent"] == {"commit": PARENT, "tree": TREE} and
            record["evidence"] == {"delivery_state": "active", "knowledge_status": "Open Question", "ceiling": "record-review-only", "artifacts": []} and
            [r["path"] for r in record["files"]] == sorted(CHANGED - {RECORD}), "external plugin record drift")
    for row in record["files"]:
        normalize = lambda v: None if v is None else {("git_blob_sha1" if k == "git_blob" else k): x for k, x in v.items()}
        require(normalize(row["before"]) == before_identity(root, row["path"]) and
                normalize(row["after"]) == current_identity(root, row["path"]), "external plugin record identity drift")
    return CHANGED


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check"])
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    validate(parser.parse_args().repository_root)
    print("PASS: finite trusted plugin inputs; original upstream engine and Ergon authority retained")

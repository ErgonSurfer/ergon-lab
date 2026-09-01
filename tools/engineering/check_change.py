#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Validate public engineering change evidence for Ergon Lab."""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any


VERSION = "1.1.0"
RECORDS_DIR = Path("docs/engineering/changes")
SCHEMA_PATH = Path("docs/engineering/schemas/change-evidence.schema.json")
BASELINE = {
    "project": "Bitcoin Static",
    "version": "24.0.5",
    "commit": "2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b",
    "tree": "8a74bb952c2137156214b9fe5888c494bd77aeca",
    "license": "MIT",
}
PUBLIC_ROOT = {
    "commit": "5bcdba149119aa9035830e069d1cae1d9bcddfb4",
    "tree": "8a74bb952c2137156214b9fe5888c494bd77aeca",
    "parentless": True,
}
SIGNATURE_POLICY = {
    "format": "ssh",
    "key_algorithm": "ED25519",
    "principal": "153525861+ErgonSurfer@users.noreply.github.com",
    "fingerprint": "SHA256:kC/Vx9WJW9ufy4Ttg5tKK6Cw8jEuV9ej2mRCLvZyU3Q",
    "public_key": (
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFN47Qs8VW9ty+v0tf31kv6pMpyOMxWWLXZ0Pv5MWVCI "
        "Ergon Lab Git commit signing (ErgonSurfer)"
    ),
    "public_key_bytes": 124,
    "public_key_sha256": "0ef0d6055bf86ace992821a029055ed3d3e2da2047619627d3f8405c6cad7512",
    "allowed_signers_bytes": 128,
    "allowed_signers_sha256": "4df5711122f5777dbaea2480d2d1fdef81ea294a79d835ab0173ae0065dfa738",
    "candidate_same_key": True,
}
STAGES = {
    "legacy-compatibility",
    "optional-indexing",
    "testnet-activation",
    "mainnet-readiness",
    "research",
}
DELIVERY_STATES = {"verified", "active", "blocked", "planned", "research"}
KNOWLEDGE_STATUSES = {
    "Explainer",
    "Hypothesis",
    "Simulation",
    "Observed",
    "Reproduced",
    "Open Question",
}
SURFACES = {
    "consensus",
    "validation_mempool",
    "p2p",
    "storage_datadir",
    "rpc_api",
    "wallet",
    "indexing_chronik",
    "ui",
    "build_release",
    "tests",
    "documentation_cockpit",
    "data_research",
}
FILE_ROLES = {
    "production",
    "build",
    "test",
    "harness",
    "fixture",
    "documentation",
    "provenance",
    "data",
    "research",
}
BUILD_ROLES = {
    "legacy-compatibility": ("baseline", "candidate"),
    "optional-indexing": (
        "compiled-out",
        "compiled-in-disabled",
        "local-regtest-indexing",
    ),
    "testnet-activation": (
        "testnet-activation-disabled",
        "testnet-activation-enabled",
        "mainnet-control",
    ),
    "mainnet-readiness": (),
    "research": (),
}
REQUIRED_SCENARIOS = {
    "legacy-compatibility": (
        "mixed-node-coexistence",
        "legacy-mining-baseline",
        "legacy-mining-candidate",
        "inherited-functional-default-launch",
    ),
    "optional-indexing": (
        "index-local-regtest-opt-in",
        "index-restart-288",
        "index-full-reindex-288",
        "index-chainstate-reindex-288",
        "index-pruned-datadir-288",
        "index-deep-reorg-fail-closed",
    ),
    "testnet-activation": (
        "activation-dormant-by-default",
        "activation-governance-binding",
        "activation-boundary",
        "activation-reorg",
        "mixed-peer-testnet",
        "mainnet-parameters-unchanged",
    ),
    "mainnet-readiness": (),
    "research": (),
}
SAFE_ENVIRONMENT = {
    "LANG": "C",
    "LC_ALL": "C",
    "NO_COLOR": "1",
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    "TERM": "dumb",
    "TZ": "UTC",
    "TMPDIR": "<runner-created-unique-directory>",
}
GIT_POLICY = {
    "system_config": False,
    "global_config": False,
    "replace_objects": False,
    "complete_history_required": True,
    "verified_commits": ["public-root", "integration-parent", "candidate"],
    "required_signature_status": "G",
    "candidate_direct_child": True,
    "fresh_origin_main_fetch": True,
    "candidate_reachable_from_remote_ref": True,
}
BOUNDARIES = {
    "consensus_authority": "standalone-node",
    "chronik_role": "observe-and-index-only",
    "chronik_consensus_authority": False,
    "chronik_mempool_authority": False,
    "chronik_activation_authority": False,
    "chronik_chain_selection_authority": False,
    "chronik_required_for_correctness": False,
    "chronik_compile_time_default": "off",
    "chronik_runtime_default": "off",
    "chronik_enabled_scope": "local-regtest-opt-in-only",
    "mainnet_parameters_modified": False,
}
TOP_LEVEL_KEYS = {
    "$comment",
    "$schema",
    "schema_version",
    "change_id",
    "stage",
    "status",
    "record_path",
    "purpose",
    "baseline",
    "public_lineage",
    "signature_policy",
    "provenance_inventory",
    "prerequisites",
    "files",
    "surfaces",
    "boundaries",
    "verification",
    "evidence",
    "limitations",
    "counterevidence",
    "decision",
}

RELATIVE_PATH_SCHEMA_PATTERN = (
    r"^(?!/)(?!.*//)(?!.*/$)(?!.*(?:^|/)\.\.?(?:/|$))"
    r"(?!.*(?:^|/)\.[gG][iI][tT](?:/|$))[^\\:\u0000-\u001F]+$"
)
INTEGRATION_PARENT_BINDING = {
    "input_method": "explicit-reviewed-cli",
    "commit_argument": "--expected-integration-parent-commit",
    "tree_argument": "--expected-integration-parent-tree",
    "defaults_allowed": False,
    "environment_fallback_allowed": False,
    "record_match_required": True,
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_RE = re.compile(r"^[0-9a-f]{40}$")
ID_RE = re.compile(r"^ERGON-(?:CHANGE|RESEARCH)-[0-9]{4}$")
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
MODE_RE = re.compile(r"^100(?:644|755)$")


class ValidationError(Exception):
    """Raised when public change evidence is incomplete or contradictory."""


def fail(path: str, message: str) -> None:
    raise ValidationError(f"{path}: {message}")


def require(condition: bool, path: str, message: str) -> None:
    if not condition:
        fail(path, message)


def obj(value: Any, path: str) -> dict[str, Any]:
    require(isinstance(value, dict), path, "must be an object")
    return value


def arr(value: Any, path: str, *, nonempty: bool = False) -> list[Any]:
    require(isinstance(value, list), path, "must be an array")
    if nonempty:
        require(bool(value), path, "must not be empty")
    return value


def text(value: Any, path: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), path, "must be a non-empty string")
    return value


def keys(value: dict[str, Any], path: str, required: set[str], optional: set[str] | None = None) -> None:
    optional = optional or set()
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    require(not missing, path, f"missing keys: {', '.join(sorted(missing))}")
    require(not unknown, path, f"unknown keys: {', '.join(sorted(unknown))}")


def strings(value: Any, path: str, *, nonempty: bool = False) -> list[str]:
    items = arr(value, path, nonempty=nonempty)
    for index, item in enumerate(items):
        text(item, f"{path}[{index}]")
    return items


def digest(value: Any, path: str, pattern: re.Pattern[str] = SHA256_RE) -> str:
    result = text(value, path)
    require(bool(pattern.fullmatch(result)), path, "has an invalid digest")
    return result


def relative_path(value: Any, path: str) -> str:
    result = text(value, path)
    require("\\" not in result and ":" not in result, path, "must be a portable POSIX path")
    require(not any(ord(character) < 32 for character in result), path, "contains a control character")
    pure = PurePosixPath(result)
    require(not pure.is_absolute() and pure.as_posix() == result, path, "must be normalized and relative")
    require(all(part not in ("", ".", "..") for part in pure.parts), path, "contains traversal")
    require(not any(part.lower() == ".git" for part in pure.parts), path, "cannot address Git metadata")
    return result


def identity(value: Any, path: str) -> None:
    record = obj(value, path)
    keys(record, path, {"mode", "bytes", "git_blob", "sha256"})
    require(bool(MODE_RE.fullmatch(text(record["mode"], path + ".mode"))), path + ".mode", "must be 100644 or 100755")
    require(type(record["bytes"]) is int and record["bytes"] >= 0, path + ".bytes", "must be non-negative")
    digest(record["git_blob"], path + ".git_blob", GIT_RE)
    digest(record["sha256"], path + ".sha256")


def provenance(value: Any, path: str) -> None:
    record = obj(value, path)
    keys(record, path, {"kind", "code", "license"}, {"source"})
    require(record["kind"] in ("public-source", "independent-authorship"), path + ".kind", "invalid kind")
    text(record["code"], path + ".code")
    require(record["license"] == "MIT", path + ".license", "must be MIT")
    if record["kind"] == "public-source":
        require("source" in record, path, "requires source metadata")
        source = obj(record["source"], path + ".source")
        keys(source, path + ".source", {"url", "commit", "path", "license"})
        require(text(source["url"], path + ".source.url").startswith("https://"), path + ".source.url", "must use HTTPS")
        digest(source["commit"], path + ".source.commit", GIT_RE)
        relative_path(source["path"], path + ".source.path")
        require(source["license"] == "MIT", path + ".source.license", "must be MIT")
    else:
        require("source" not in record, path, "independent authorship cannot claim donor source metadata")


def validate_public_lineage(value: Any, path: str) -> None:
    record = obj(value, path)
    keys(record, path, {"public_root", "integration_parent", "candidate_parent_law", "remote_ref"})
    require(record["public_root"] == PUBLIC_ROOT, path + ".public_root", "must bind the signed parentless public root")
    parent = obj(record["integration_parent"], path + ".integration_parent")
    keys(parent, path + ".integration_parent", {"commit", "tree"})
    commit = digest(parent["commit"], path + ".integration_parent.commit", GIT_RE)
    digest(parent["tree"], path + ".integration_parent.tree", GIT_RE)
    require(commit != PUBLIC_ROOT["commit"], path + ".integration_parent.commit", "must be a post-launch public parent")
    require(record["candidate_parent_law"] == "direct-child", path + ".candidate_parent_law", "must require one direct parent")
    require(record["remote_ref"] == "refs/remotes/origin/main", path + ".remote_ref", "must bind public origin/main")


def validate_signature_policy(value: Any, path: str) -> None:
    require(value == SIGNATURE_POLICY, path, "must match the reviewed ErgonSurfer SSH trust root")
    public_key = (SIGNATURE_POLICY["public_key"] + "\n").encode("ascii")
    encoded_key = SIGNATURE_POLICY["public_key"].split(" ", 2)[1]
    key_blob = base64.b64decode(encoded_key, validate=True)
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key_blob).digest()).decode("ascii").rstrip("=")
    allowed_signers = (
        SIGNATURE_POLICY["principal"]
        + " "
        + " ".join(SIGNATURE_POLICY["public_key"].split(" ", 2)[:2])
        + "\n"
    ).encode("ascii")
    require(len(public_key) == SIGNATURE_POLICY["public_key_bytes"], path, "public-key byte count mismatch")
    require(hashlib.sha256(public_key).hexdigest() == SIGNATURE_POLICY["public_key_sha256"], path, "public-key digest mismatch")
    require(fingerprint == SIGNATURE_POLICY["fingerprint"], path, "SSH fingerprint mismatch")
    require(len(allowed_signers) == SIGNATURE_POLICY["allowed_signers_bytes"], path, "allowed-signers byte count mismatch")
    require(hashlib.sha256(allowed_signers).hexdigest() == SIGNATURE_POLICY["allowed_signers_sha256"], path, "allowed-signers digest mismatch")


def provenance_inventory_bytes(change_id: str, files: list[dict[str, Any]]) -> bytes:
    entries: list[dict[str, Any]] = []
    for record in files:
        provenance_record = record["provenance"]
        if provenance_record["kind"] != "independent-authorship":
            continue
        require(record["after"] is not None, "files", "independent-authorship inventory requires a postimage")
        after = record["after"]
        entries.append(
            {
                "blob": after["git_blob"],
                "bytes": after["bytes"],
                "mode": after["mode"],
                "path": record["path"],
                "provenance": provenance_record["code"],
                "sha256": after["sha256"],
            }
        )
    entries.sort(key=lambda item: item["path"])
    payload = {
        "change_id": change_id,
        "entries": entries,
        "schema": "ergon-authorship-inventory/v1",
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8") + b"\n"


def validate_provenance_inventory(value: Any, path: str, change_id: str, files: list[dict[str, Any]]) -> None:
    record = obj(value, path)
    keys(
        record,
        path,
        {
            "inventory_schema",
            "inventory_sha256",
            "license",
            "standing_policy",
            "exclusions",
            "implicit_operational_authorization",
        },
    )
    require(record["inventory_schema"] == "ergon-authorship-inventory/v1", path + ".inventory_schema", "unexpected schema")
    require(any(item["provenance"]["kind"] == "independent-authorship" for item in files), path, "requires at least one independently authored postimage")
    canonical = provenance_inventory_bytes(change_id, files)
    inventory_digest = digest(record["inventory_sha256"], path + ".inventory_sha256")
    require(inventory_digest == hashlib.sha256(canonical).hexdigest(), path + ".inventory_sha256", "does not bind the canonical independent-authorship postimages")
    require(record["license"] == "MIT", path + ".license", "must be MIT")
    require(record["standing_policy"] == "PUBLICATION_POLICY.md", path + ".standing_policy", "must use the repository standing policy")
    strings(record["exclusions"], path + ".exclusions", nonempty=True)
    require(record["implicit_operational_authorization"] is False, path + ".implicit_operational_authorization", "must be false")


def validate_files(value: Any, path: str, stage: str) -> list[dict[str, Any]]:
    records = arr(value, path, nonempty=True)
    seen: set[str] = set()
    ordered_paths: list[str] = []
    for index, item in enumerate(records):
        item_path = f"{path}[{index}]"
        record = obj(item, item_path)
        keys(record, item_path, {"path", "action", "role", "before", "after", "provenance", "spdx", "production_reachability", "test_reachability"})
        file_path = relative_path(record["path"], item_path + ".path")
        require(file_path not in seen, item_path + ".path", "duplicate path")
        seen.add(file_path)
        ordered_paths.append(file_path)
        require(record["action"] in ("add", "modify", "delete"), item_path + ".action", "invalid action")
        require(record["role"] in FILE_ROLES, item_path + ".role", "invalid role")
        if stage == "research":
            require(record["role"] not in ("production", "build"), item_path + ".role", "research cannot change node production or build files")
        if record["action"] == "add":
            require(record["before"] is None, item_path + ".before", "must be null for an added file")
            identity(record["after"], item_path + ".after")
        elif record["action"] == "delete":
            identity(record["before"], item_path + ".before")
            require(record["after"] is None, item_path + ".after", "must be null for a deleted file")
        else:
            identity(record["before"], item_path + ".before")
            identity(record["after"], item_path + ".after")
        provenance(record["provenance"], item_path + ".provenance")
        require(record["spdx"] == "MIT", item_path + ".spdx", "must be MIT")
        text(record["production_reachability"], item_path + ".production_reachability")
        text(record["test_reachability"], item_path + ".test_reachability")
    require(ordered_paths == sorted(ordered_paths), path, "must be sorted by public relative path")
    return records


def validate_prerequisites(value: Any, path: str, stage: str) -> None:
    records = arr(value, path)
    seen_stages: set[str] = set()
    for index, item in enumerate(records):
        item_path = f"{path}[{index}]"
        record = obj(item, item_path)
        keys(record, item_path, {"change_id", "stage", "public_commit", "evidence_sha256", "delivery_state", "knowledge_status"})
        require(bool(ID_RE.fullmatch(text(record["change_id"], item_path + ".change_id"))), item_path + ".change_id", "invalid change id")
        require(record["stage"] in STAGES, item_path + ".stage", "invalid stage")
        require(record["stage"] not in seen_stages, item_path + ".stage", "duplicate prerequisite stage")
        seen_stages.add(record["stage"])
        digest(record["public_commit"], item_path + ".public_commit", GIT_RE)
        digest(record["evidence_sha256"], item_path + ".evidence_sha256")
        require(record["delivery_state"] == "verified", item_path + ".delivery_state", "must be verified")
        require(record["knowledge_status"] == "Reproduced", item_path + ".knowledge_status", "must be Reproduced")
    if stage == "optional-indexing":
        require(seen_stages == {"legacy-compatibility"}, path, "optional indexing requires reproduced legacy compatibility evidence")
    elif stage == "testnet-activation":
        require(seen_stages == {"legacy-compatibility", "optional-indexing"}, path, "testnet activation requires reproduced legacy and indexing evidence")
    elif stage == "mainnet-readiness":
        require(seen_stages == {"testnet-activation"}, path, "mainnet readiness requires reproduced testnet activation evidence")
    elif stage in ("legacy-compatibility", "research"):
        require(not records, path, "this stage has no implementation prerequisite")


def validate_verification(value: Any, path: str, stage: str) -> None:
    record = obj(value, path)
    keys(record, path, {"builds", "scenarios", "child_environment", "dependencies", "integration_parent_binding", "git_policy", "report_policy"})
    build_roles: list[str] = []
    for index, item in enumerate(arr(record["builds"], path + ".builds")):
        item_path = f"{path}.builds[{index}]"
        build = obj(item, item_path)
        keys(build, item_path, {"role", "commands", "expected_results"})
        role = text(build["role"], item_path + ".role")
        require(role not in build_roles, item_path + ".role", "duplicate build role")
        build_roles.append(role)
        strings(build["commands"], item_path + ".commands", nonempty=True)
        strings(build["expected_results"], item_path + ".expected_results", nonempty=True)
    require(tuple(build_roles) == BUILD_ROLES[stage], path + ".builds", "does not match the ordered stage build roles")

    scenario_ids: list[str] = []
    for index, item in enumerate(arr(record["scenarios"], path + ".scenarios")):
        item_path = f"{path}.scenarios[{index}]"
        scenario = obj(item, item_path)
        keys(scenario, item_path, {"id", "command", "expected_results", "failure_conditions", "fresh_process", "isolated_datadir"})
        identifier = text(scenario["id"], item_path + ".id")
        require(bool(NAME_RE.fullmatch(identifier)), item_path + ".id", "must be lowercase kebab-case")
        require(identifier not in scenario_ids, item_path + ".id", "duplicate scenario")
        scenario_ids.append(identifier)
        text(scenario["command"], item_path + ".command")
        strings(scenario["expected_results"], item_path + ".expected_results", nonempty=True)
        strings(scenario["failure_conditions"], item_path + ".failure_conditions", nonempty=True)
        require(scenario["fresh_process"] is True, item_path + ".fresh_process", "must be true")
        require(scenario["isolated_datadir"] is True, item_path + ".isolated_datadir", "must be true")
    require(tuple(scenario_ids) == REQUIRED_SCENARIOS[stage], path + ".scenarios", "does not match the ordered stage scenarios")

    require(record["child_environment"] == SAFE_ENVIRONMENT, path + ".child_environment", "must match the closed environment")
    dependencies = obj(record["dependencies"], path + ".dependencies")
    keys(dependencies, path + ".dependencies", {"locked", "platforms"}, {"lock_reference"})
    require(type(dependencies["locked"]) is bool, path + ".dependencies.locked", "must be boolean")
    strings(dependencies["platforms"], path + ".dependencies.platforms", nonempty=True)
    if dependencies["locked"]:
        require("lock_reference" in dependencies, path + ".dependencies", "locked dependencies require a reference")
        text(dependencies["lock_reference"], path + ".dependencies.lock_reference")
    else:
        require("lock_reference" not in dependencies, path + ".dependencies", "unlocked dependencies cannot claim a lock")
    require(record["integration_parent_binding"] == INTEGRATION_PARENT_BINDING, path + ".integration_parent_binding", "must require explicit reviewed parent inputs")
    require(record["git_policy"] == GIT_POLICY, path + ".git_policy", "must match the fail-closed Git identity policy")
    reports = obj(record["report_policy"], path + ".report_policy")
    require(
        reports == {
            "source": "public-tree-only",
            "paths": "repository-relative-only",
            "raw_process_output": False,
            "parent_environment": False,
        },
        path + ".report_policy",
        "must preserve portable public evidence",
    )


def validate_evidence(value: Any, path: str, status: str) -> None:
    record = obj(value, path)
    keys(record, path, {"delivery_state", "knowledge_status", "ceiling", "artifacts"})
    require(record["delivery_state"] in DELIVERY_STATES, path + ".delivery_state", "invalid delivery state")
    require(record["knowledge_status"] in KNOWLEDGE_STATUSES, path + ".knowledge_status", "invalid knowledge status")
    text(record["ceiling"], path + ".ceiling")
    artifacts = arr(record["artifacts"], path + ".artifacts")
    for index, item in enumerate(artifacts):
        item_path = f"{path}.artifacts[{index}]"
        artifact = obj(item, item_path)
        keys(artifact, item_path, {"path", "sha256"})
        relative_path(artifact["path"], item_path + ".path")
        digest(artifact["sha256"], item_path + ".sha256")
    if record["knowledge_status"] == "Reproduced":
        require(bool(artifacts), path + ".artifacts", "Reproduced requires public evidence artifacts")
    if status in ("draft", "under-review"):
        require(record["knowledge_status"] != "Reproduced", path + ".knowledge_status", "review work cannot pre-claim reproduction")
    if status == "under-review":
        require(record["knowledge_status"] == "Open Question", path + ".knowledge_status", "under-review work must remain an Open Question")
        require(record["ceiling"] == "record-review-only", path + ".ceiling", "under-review work must use the record-review-only ceiling")
        require(not artifacts, path + ".artifacts", "under-review work cannot attach evidence")
    if record["delivery_state"] == "verified":
        require(record["knowledge_status"] == "Reproduced", path, "verified delivery requires Reproduced evidence")


def validate_decision(value: Any, path: str, status: str) -> None:
    record = obj(value, path)
    keys(record, path, {"status"}, {"reviewer", "date", "public_commit"})
    require(record["status"] in ("pending", "accepted", "narrowed", "rejected"), path + ".status", "invalid decision")
    if status in ("draft", "under-review"):
        require(record == {"status": "pending"}, path, "unfinished work cannot pre-claim a decision")
    else:
        require(record["status"] in ("accepted", "narrowed"), path + ".status", "accepted or landed work needs an approving decision")
        require({"reviewer", "date"} <= record.keys(), path, "approval metadata is incomplete")
        text(record["reviewer"], path + ".reviewer")
        require(bool(DATE_RE.fullmatch(text(record["date"], path + ".date"))), path + ".date", "must be YYYY-MM-DD")
        if status == "landed":
            require("public_commit" in record, path, "landed work requires its public commit")
            digest(record["public_commit"], path + ".public_commit", GIT_RE)
        else:
            require("public_commit" not in record, path, "accepted work cannot pre-claim a public commit")


def validate(value: Any, source: str = "change") -> dict[str, Any]:
    record = obj(value, source)
    keys(record, source, TOP_LEVEL_KEYS)
    require(record["$comment"] == "SPDX-License-Identifier: MIT", source + ".$comment", "must declare MIT")
    require(record["$schema"] == "../schemas/change-evidence.schema.json", source + ".$schema", "unexpected schema")
    require(record["schema_version"] == "1.1", source + ".schema_version", "must be 1.1")
    change_id = text(record["change_id"], source + ".change_id")
    require(bool(ID_RE.fullmatch(change_id)), source + ".change_id", "invalid change id")
    expected_record_path = f"docs/engineering/changes/{change_id.lower()}.json"
    require(record["record_path"] == expected_record_path, source + ".record_path", "must match the change ID")
    stage = record["stage"]
    require(stage in STAGES, source + ".stage", "invalid stage")
    require((stage == "research") == change_id.startswith("ERGON-RESEARCH-"), source + ".change_id", "research stage and ID family must agree")
    status = record["status"]
    require(status in ("draft", "under-review", "accepted", "landed"), source + ".status", "invalid status")

    purpose = obj(record["purpose"], source + ".purpose")
    keys(purpose, source + ".purpose", {"summary", "scope", "exclusions"})
    text(purpose["summary"], source + ".purpose.summary")
    strings(purpose["scope"], source + ".purpose.scope", nonempty=True)
    strings(purpose["exclusions"], source + ".purpose.exclusions", nonempty=True)
    require(record["baseline"] == BASELINE, source + ".baseline", "must bind the exact public baseline")
    validate_public_lineage(record["public_lineage"], source + ".public_lineage")
    validate_signature_policy(record["signature_policy"], source + ".signature_policy")
    validate_prerequisites(record["prerequisites"], source + ".prerequisites", stage)
    files = validate_files(record["files"], source + ".files", stage)
    require(expected_record_path not in {item["path"] for item in files}, source + ".files", "must not self-bind the governance record")
    validate_provenance_inventory(record["provenance_inventory"], source + ".provenance_inventory", change_id, files)

    surfaces = obj(record["surfaces"], source + ".surfaces")
    keys(surfaces, source + ".surfaces", SURFACES)
    for name, enabled in surfaces.items():
        require(type(enabled) is bool, source + ".surfaces." + name, "must be boolean")
    if stage in ("legacy-compatibility", "optional-indexing", "mainnet-readiness", "research"):
        require(surfaces["consensus"] is False, source + ".surfaces.consensus", "this stage cannot change consensus")
    else:
        require(surfaces["consensus"] is True, source + ".surfaces.consensus", "activation must declare consensus reachability")
    require(record["boundaries"] == BOUNDARIES, source + ".boundaries", "must match the standalone authority boundary")
    validate_verification(record["verification"], source + ".verification", stage)
    validate_evidence(record["evidence"], source + ".evidence", status)
    strings(record["limitations"], source + ".limitations", nonempty=True)
    strings(record["counterevidence"], source + ".counterevidence", nonempty=True)
    validate_decision(record["decision"], source + ".decision", status)
    return record


def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "JSON", f"duplicate key {key!r}")
        result[key] = value
    return result


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_pairs)
    except FileNotFoundError as exc:
        raise ValidationError(f"{path}: required file is missing") from exc
    except UnicodeDecodeError as exc:
        raise ValidationError(f"{path}: must be strict UTF-8") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path}:{exc.lineno}:{exc.colno}: invalid JSON: {exc.msg}") from exc


def validate_file(path: Path) -> None:
    record = validate(load(path), str(path))
    expected = record["change_id"].lower() + ".json"
    require(path.name == expected, str(path), f"filename must be {expected}")


def check(root: Path) -> None:
    schema_path = root / SCHEMA_PATH
    require(schema_path.is_file() and not schema_path.is_symlink(), str(SCHEMA_PATH), "must be a regular file")
    schema = obj(load(schema_path), str(SCHEMA_PATH))
    require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", str(SCHEMA_PATH), "must declare JSON Schema 2020-12")
    require(set(arr(schema.get("required"), str(SCHEMA_PATH) + ".required")) == TOP_LEVEL_KEYS, str(SCHEMA_PATH) + ".required", "must match the validator top-level contract")
    require(set(obj(schema.get("properties"), str(SCHEMA_PATH) + ".properties")) == TOP_LEVEL_KEYS, str(SCHEMA_PATH) + ".properties", "must match the validator top-level contract")
    require(schema.get("additionalProperties") is False, str(SCHEMA_PATH) + ".additionalProperties", "must fail closed on unknown keys")
    properties = schema["properties"]
    require(properties["schema_version"].get("const") == "1.1", str(SCHEMA_PATH) + ".properties.schema_version", "must match validator version 1.1")
    require(properties["change_id"].get("type") == "string", str(SCHEMA_PATH) + ".properties.change_id", "must type change IDs as strings")
    require("allOf" not in schema, str(SCHEMA_PATH) + ".allOf", "must not pin a per-change inventory digest")
    definitions = obj(schema.get("$defs"), str(SCHEMA_PATH) + ".$defs")
    require(
        obj(definitions.get("relativePath"), str(SCHEMA_PATH) + ".$defs.relativePath").get("pattern") == RELATIVE_PATH_SCHEMA_PATTERN,
        str(SCHEMA_PATH) + ".$defs.relativePath.pattern",
        "must match the portable path contract",
    )
    prerequisite_properties = obj(
        obj(definitions.get("prerequisite"), str(SCHEMA_PATH) + ".$defs.prerequisite").get("properties"),
        str(SCHEMA_PATH) + ".$defs.prerequisite.properties",
    )
    require(prerequisite_properties["change_id"].get("type") == "string", str(SCHEMA_PATH) + ".$defs.prerequisite.properties.change_id", "must type prerequisite IDs as strings")
    verification_properties = obj(
        obj(definitions.get("verification"), str(SCHEMA_PATH) + ".$defs.verification").get("properties"),
        str(SCHEMA_PATH) + ".$defs.verification.properties",
    )
    require(
        verification_properties.get("integration_parent_binding") == {"const": INTEGRATION_PARENT_BINDING},
        str(SCHEMA_PATH) + ".$defs.verification.properties.integration_parent_binding",
        "must match the explicit reviewed parent-input law",
    )
    records_dir = root / RECORDS_DIR
    records: list[Path] = []
    if records_dir.exists():
        require(records_dir.is_dir() and not records_dir.is_symlink(), str(RECORDS_DIR), "must be a directory")
        for child in sorted(records_dir.iterdir()):
            if child.name == "README.md":
                require(child.is_file() and not child.is_symlink(), str(child), "must be a regular file")
            else:
                require(child.suffix == ".json" and child.is_file() and not child.is_symlink(), str(child), "unexpected change-record entry")
                records.append(child)
    for path in records:
        validate_file(path)
    print(f"Engineering change evidence passed ({len(records)} records).")


def sample(stage: str = "legacy-compatibility", status: str = "draft") -> dict[str, Any]:
    require(stage in STAGES, "sample.stage", "invalid stage")
    require(status in ("draft", "under-review", "accepted", "landed"), "sample.status", "invalid status")
    before = {"mode": "100644", "bytes": 10, "git_blob": "0" * 40, "sha256": "0" * 64}
    after = {"mode": "100644", "bytes": 11, "git_blob": "1" * 40, "sha256": "1" * 64}
    builds = [
        {"role": role, "commands": [f"build {role}"], "expected_results": ["exit zero"]}
        for role in BUILD_ROLES[stage]
    ]
    scenarios = [
        {
            "id": identifier,
            "command": f"run {identifier}",
            "expected_results": ["exit zero"],
            "failure_conditions": ["any role divergence"],
            "fresh_process": True,
            "isolated_datadir": True,
        }
        for identifier in REQUIRED_SCENARIOS[stage]
    ]
    change_id = "ERGON-RESEARCH-9999" if stage == "research" else "ERGON-CHANGE-9999"
    prerequisites_by_stage = {
        "legacy-compatibility": (),
        "optional-indexing": ("legacy-compatibility",),
        "testnet-activation": ("legacy-compatibility", "optional-indexing"),
        "mainnet-readiness": ("testnet-activation",),
        "research": (),
    }
    prerequisites = [
        {
            "change_id": f"ERGON-CHANGE-{index + 1:04d}",
            "stage": prerequisite_stage,
            "public_commit": str(index + 4) * 40,
            "evidence_sha256": str(index + 5) * 64,
            "delivery_state": "verified",
            "knowledge_status": "Reproduced",
        }
        for index, prerequisite_stage in enumerate(prerequisites_by_stage[stage])
    ]
    decision: dict[str, Any] = {"status": "pending"}
    if status in ("accepted", "landed"):
        decision = {"status": "accepted", "reviewer": "Example Reviewer", "date": "2099-01-02"}
        if status == "landed":
            decision["public_commit"] = "9" * 40
    result = {
        "$comment": "SPDX-License-Identifier: MIT",
        "$schema": "../schemas/change-evidence.schema.json",
        "schema_version": "1.1",
        "change_id": change_id,
        "stage": stage,
        "status": status,
        "record_path": f"docs/engineering/changes/{change_id.lower()}.json",
        "purpose": {"summary": "Validator self-test.", "scope": ["Exercise public change evidence."], "exclusions": ["No activation."]},
        "baseline": copy.deepcopy(BASELINE),
        "public_lineage": {
            "public_root": copy.deepcopy(PUBLIC_ROOT),
            "integration_parent": {"commit": "2" * 40, "tree": "3" * 40},
            "candidate_parent_law": "direct-child",
            "remote_ref": "refs/remotes/origin/main",
        },
        "signature_policy": copy.deepcopy(SIGNATURE_POLICY),
        "provenance_inventory": {
            "inventory_schema": "ergon-authorship-inventory/v1",
            "inventory_sha256": "0" * 64,
            "license": "MIT",
            "standing_policy": "PUBLICATION_POLICY.md",
            "exclusions": ["No private material."],
            "implicit_operational_authorization": False,
        },
        "prerequisites": prerequisites,
        "files": [
            {
                "path": "tests/compatibility/example.py",
                "action": "modify",
                "role": "harness",
                "before": before,
                "after": after,
                "provenance": {
                    "kind": "independent-authorship",
                    "code": "BASE+A1",
                    "license": "MIT",
                },
                "spdx": "MIT",
                "production_reachability": "None.",
                "test_reachability": "Compatibility harness.",
            }
        ],
        "surfaces": {
            name: name == "tests" or (name == "consensus" and stage == "testnet-activation")
            for name in sorted(SURFACES)
        },
        "boundaries": copy.deepcopy(BOUNDARIES),
        "verification": {
            "builds": builds,
            "scenarios": scenarios,
            "child_environment": copy.deepcopy(SAFE_ENVIRONMENT),
            "dependencies": {"locked": False, "platforms": ["ubuntu-24.04"]},
            "integration_parent_binding": copy.deepcopy(INTEGRATION_PARENT_BINDING),
            "git_policy": copy.deepcopy(GIT_POLICY),
            "report_policy": {
                "source": "public-tree-only",
                "paths": "repository-relative-only",
                "raw_process_output": False,
                "parent_environment": False,
            },
        },
        "evidence": {
            "delivery_state": "blocked" if status in ("draft", "under-review") else "active",
            "knowledge_status": "Open Question" if status in ("draft", "under-review") else "Observed",
            "ceiling": "record-review-only" if status in ("draft", "under-review") else "component-observation",
            "artifacts": [],
        },
        "limitations": ["No public run exists."],
        "counterevidence": ["Missing scenarios must fail validation."],
        "decision": decision,
    }
    result["provenance_inventory"]["inventory_sha256"] = hashlib.sha256(
        provenance_inventory_bytes(result["change_id"], result["files"])
    ).hexdigest()
    return result


def self_test() -> None:
    for stage in sorted(STAGES):
        validate(sample(stage), "self-test.stage." + stage)
    for status in ("under-review", "accepted", "landed"):
        validate(sample(status=status), "self-test.status." + status)

    generic_change = sample(status="under-review")
    generic_change["change_id"] = "ERGON-CHANGE-0001"
    generic_change["record_path"] = "docs/engineering/changes/ergon-change-0001.json"
    generic_change["provenance_inventory"]["inventory_sha256"] = hashlib.sha256(
        provenance_inventory_bytes(generic_change["change_id"], generic_change["files"])
    ).hexdigest()
    validate(generic_change, "self-test.generic-change-0001")

    valid = sample()
    validate(valid, "self-test.valid")
    mutations = [
        ("baseline drift", lambda item: item["baseline"].update({"commit": "f" * 40})),
        ("public root drift", lambda item: item["public_lineage"]["public_root"].update({"commit": "f" * 40})),
        ("root reused as parent", lambda item: item["public_lineage"]["integration_parent"].update({"commit": PUBLIC_ROOT["commit"]})),
        ("merge parent law", lambda item: item["public_lineage"].update({"candidate_parent_law": "merge-allowed"})),
        ("remote ref drift", lambda item: item["public_lineage"].update({"remote_ref": "refs/heads/main"})),
        ("signer drift", lambda item: item["signature_policy"].update({"principal": "someone@example.invalid"})),
        ("declarative signature bypass", lambda item: item["verification"]["git_policy"].update({"required_signature_status": "verified"})),
        ("replace object", lambda item: item["verification"]["git_policy"].update({"replace_objects": True})),
        ("provenance digest drift", lambda item: item["provenance_inventory"].update({"inventory_sha256": "f" * 64})),
        ("standing policy drift", lambda item: item["provenance_inventory"].update({"standing_policy": "OTHER.md"})),
        ("implicit publication authority", lambda item: item["provenance_inventory"].update({"implicit_operational_authorization": True})),
        ("parent binding missing", lambda item: item["verification"].pop("integration_parent_binding")),
        ("parent binding default", lambda item: item["verification"]["integration_parent_binding"].update({"defaults_allowed": True})),
        ("parent binding environment", lambda item: item["verification"]["integration_parent_binding"].update({"environment_fallback_allowed": True})),
        ("parent binding argument drift", lambda item: item["verification"]["integration_parent_binding"].update({"commit_argument": "--parent"})),
        ("parent binding record bypass", lambda item: item["verification"]["integration_parent_binding"].update({"record_match_required": False})),
        ("record path drift", lambda item: item.update({"record_path": "docs/engineering/changes/other.json"})),
        ("consensus smuggling", lambda item: item["surfaces"].update({"consensus": True})),
        ("missing scenario", lambda item: item["verification"]["scenarios"].pop()),
        ("scenario reorder", lambda item: item["verification"]["scenarios"].reverse()),
        ("build reorder", lambda item: item["verification"]["builds"].reverse()),
        ("unexpected build", lambda item: item["verification"]["builds"].append({"role": "extra", "commands": ["x"], "expected_results": ["x"]})),
        ("path traversal", lambda item: item["files"][0].update({"path": "../escape.py"})),
        ("path empty segment", lambda item: item["files"][0].update({"path": "tests//escape.py"})),
        ("case-folded git metadata", lambda item: item["files"][0].update({"path": ".GIT/config"})),
        ("non-MIT file", lambda item: item["files"][0].update({"spdx": "unknown"})),
        ("index authority", lambda item: item["boundaries"].update({"chronik_chain_selection_authority": True})),
        ("mainnet mutation", lambda item: item["boundaries"].update({"mainnet_parameters_modified": True})),
        ("raw output", lambda item: item["verification"]["report_policy"].update({"raw_process_output": True})),
        ("parent environment", lambda item: item["verification"]["child_environment"].update({"PARENT_ONLY": "forbidden"})),
        ("preclaimed reproduction", lambda item: item["evidence"].update({"knowledge_status": "Reproduced"})),
        ("empty counterevidence", lambda item: item.update({"counterevidence": []})),
        ("preclaimed decision", lambda item: item.update({"decision": {"status": "accepted", "reviewer": "x", "date": "2099-01-01"}})),
        ("unknown key", lambda item: item.update({"unexpected": True})),
    ]
    for name, mutate in mutations:
        candidate = copy.deepcopy(valid)
        mutate(candidate)
        try:
            validate(candidate, "self-test." + name.replace(" ", "-"))
        except ValidationError:
            continue
        fail("self-test", f"forbidden mutation accepted: {name}")
    try:
        json.loads('{"duplicate": 1, "duplicate": 2}', object_pairs_hook=unique_pairs)
    except ValidationError:
        pass
    else:
        fail("self-test", "duplicate JSON keys were accepted")

    stage_mutations = [
        (
            "optional prerequisite missing",
            sample("optional-indexing"),
            lambda item: item.update({"prerequisites": []}),
        ),
        (
            "testnet consensus hidden",
            sample("testnet-activation"),
            lambda item: item["surfaces"].update({"consensus": False}),
        ),
        (
            "research production reachability",
            sample("research"),
            lambda item: item["files"][0].update({"role": "production"}),
        ),
        (
            "landed commit missing",
            sample(status="landed"),
            lambda item: item["decision"].pop("public_commit"),
        ),
        (
            "accepted commit preclaimed",
            sample(status="accepted"),
            lambda item: item["decision"].update({"public_commit": "9" * 40}),
        ),
    ]
    for name, candidate, mutate in stage_mutations:
        mutate(candidate)
        try:
            validate(candidate, "self-test." + name.replace(" ", "-"))
        except ValidationError:
            continue
        fail("self-test", f"forbidden mutation accepted: {name}")
    rejection_classes = len(mutations) + len(stage_mutations) + 1
    print(f"Engineering change self-tests passed ({rejection_classes} rejection classes).")


def root() -> Path:
    return Path(__file__).resolve().parents[2]


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "validate", "self-test"))
    parser.add_argument("record", nargs="?", type=Path)
    parser.add_argument("--root", type=Path, default=root())
    result = parser.parse_args()
    if result.command == "validate" and result.record is None:
        parser.error("validate requires a change-record path")
    if result.command != "validate" and result.record is not None:
        parser.error("a path is accepted only by validate")
    return result


def main() -> int:
    args = arguments()
    try:
        if args.command == "check":
            check(args.root.resolve())
        elif args.command == "validate":
            validate_file(args.record.resolve())
            print(f"Engineering change evidence passed: {args.record}")
        else:
            self_test()
    except ValidationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

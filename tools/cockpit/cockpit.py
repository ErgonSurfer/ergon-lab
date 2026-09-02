#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Validate the public cockpit and generate its projection-derived roadmap.

The .yaml sources are deliberately written as JSON, which is a YAML 1.2 subset.
This keeps the publication gate auditable and dependency-free.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any


VERSION = "1.0.0"
COCKPIT_PATH = Path("cockpit/cockpit.yaml")
EVENTS_PATH = Path("cockpit/events/roadmap-events.yaml")
ROADMAP_PATH = Path("docs/roadmap.md")
MANIFEST_PATH = Path("cockpit/generated/roadmap-manifest.json")
SCHEMA_PATHS = (
    Path("cockpit/schemas/cockpit.schema.json"),
    Path("cockpit/schemas/entry.schema.json"),
    Path("cockpit/schemas/reproduction.schema.json"),
)

DELIVERY_STATES = ("verified", "active", "blocked", "planned", "research")
KNOWLEDGE_STATUSES = (
    "Explainer",
    "Hypothesis",
    "Simulation",
    "Observed",
    "Reproduced",
    "Open Question",
)
WORKSTREAMS = {
    "build-operate": "Build / Operate",
    "learn": "Learn",
    "research": "Research",
    "observatory": "Observatory",
}
LANES = tuple(WORKSTREAMS.values())
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
EVENT_ID_PATTERN = re.compile(r"^ROADMAP-\d{4}$")


class ValidationError(Exception):
    """Raised when a public cockpit gate must fail closed."""


def fail(path: str, message: str) -> None:
    raise ValidationError(f"{path}: {message}")


def require(condition: bool, path: str, message: str) -> None:
    if not condition:
        fail(path, message)


def require_object(value: Any, path: str) -> dict[str, Any]:
    require(isinstance(value, dict), path, "must be an object")
    return value


def require_list(value: Any, path: str) -> list[Any]:
    require(isinstance(value, list), path, "must be an array")
    return value


def require_string(value: Any, path: str) -> str:
    require(isinstance(value, str) and bool(value.strip()), path, "must be a non-empty string")
    return value


def require_exact_keys(
    value: dict[str, Any], path: str, required: set[str], optional: set[str] | None = None
) -> None:
    optional = optional or set()
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    require(not missing, path, f"missing required keys: {', '.join(sorted(missing))}")
    require(not unknown, path, f"unknown keys: {', '.join(sorted(unknown))}")


def load_json_document(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"{path}: required file is missing") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(
            f"{path}:{exc.lineno}:{exc.colno}: must be JSON-compatible YAML: {exc.msg}"
        ) from exc


def validate_string_array(value: Any, path: str, *, nonempty: bool = False) -> list[str]:
    items = require_list(value, path)
    if nonempty:
        require(bool(items), path, "must not be empty")
    for index, item in enumerate(items):
        require_string(item, f"{path}[{index}]")
    return items


def validate_reproduction_pack(value: Any, path: str) -> None:
    pack = require_object(value, path)
    required = {"availability", "blockers"}
    optional = {"location", "sha256", "commands", "expected_results", "environment"}
    require_exact_keys(pack, path, required, optional)
    availability = pack["availability"]
    require(availability in ("not-yet-published", "published"), path + ".availability", "invalid value")
    blockers = validate_string_array(pack["blockers"], path + ".blockers")
    if availability == "not-yet-published":
        require(bool(blockers), path + ".blockers", "must explain why the pack is unavailable")
        forbidden = {"location", "sha256", "commands", "expected_results", "environment"} & pack.keys()
        require(not forbidden, path, "an unpublished pack must not carry publication metadata")
        return

    require(not blockers, path + ".blockers", "must be empty for a published pack")
    for key in ("location", "sha256", "commands", "expected_results", "environment"):
        require(key in pack, path, f"published pack is missing {key}")
    require_string(pack["location"], path + ".location")
    require(bool(SHA256_PATTERN.fullmatch(require_string(pack["sha256"], path + ".sha256"))), path + ".sha256", "must be lowercase SHA-256")
    validate_string_array(pack["commands"], path + ".commands", nonempty=True)
    validate_string_array(pack["expected_results"], path + ".expected_results", nonempty=True)
    environment = require_object(pack["environment"], path + ".environment")
    require_exact_keys(environment, path + ".environment", {"dependencies_locked", "platforms"}, {"lock_reference"})
    require(type(environment["dependencies_locked"]) is bool, path + ".environment.dependencies_locked", "must be a boolean")
    validate_string_array(environment["platforms"], path + ".environment.platforms", nonempty=True)
    if environment["dependencies_locked"]:
        require("lock_reference" in environment, path + ".environment", "locked dependencies require lock_reference")
        require_string(environment["lock_reference"], path + ".environment.lock_reference")


def validate_research_evidence(value: Any, path: str, knowledge_status: str, delivery_state: str) -> None:
    evidence = require_object(value, path)
    required = {
        "question",
        "sources",
        "methods",
        "data",
        "units",
        "uncertainty",
        "limitations",
        "counter_evidence",
        "reproduction_pack",
    }
    require_exact_keys(evidence, path, required)
    require_string(evidence["question"], path + ".question")

    sources = require_object(evidence["sources"], path + ".sources")
    require_exact_keys(sources, path + ".sources", {"status", "records"})
    require(sources["status"] in ("missing", "preliminary", "reviewed"), path + ".sources.status", "invalid value")
    records = require_list(sources["records"], path + ".sources.records")
    for index, record_value in enumerate(records):
        record_path = f"{path}.sources.records[{index}]"
        record = require_object(record_value, record_path)
        require_exact_keys(record, record_path, {"title", "url"})
        require_string(record["title"], record_path + ".title")
        url = require_string(record["url"], record_path + ".url")
        require(url.startswith("https://"), record_path + ".url", "must use HTTPS")
    require(sources["status"] != "missing" or not records, path + ".sources", "missing status requires an empty record list")
    require(sources["status"] == "missing" or bool(records), path + ".sources", "preliminary/reviewed status requires records")

    methods = require_object(evidence["methods"], path + ".methods")
    require_exact_keys(methods, path + ".methods", {"status", "description"})
    require(methods["status"] in ("not-started", "draft", "reviewed"), path + ".methods.status", "invalid value")
    require_string(methods["description"], path + ".methods.description")

    data = require_object(evidence["data"], path + ".data")
    require_exact_keys(data, path + ".data", {"policy", "status", "references"})
    require(data["policy"] == "public-only", path + ".data.policy", "must be public-only")
    require(data["status"] in ("not-collected", "partial", "reviewed"), path + ".data.status", "invalid value")
    references = validate_string_array(data["references"], path + ".data.references")
    require(data["status"] == "not-collected" or bool(references), path + ".data", "partial/reviewed data requires references")

    units = require_object(evidence["units"], path + ".units")
    require_exact_keys(units, path + ".units", {"status", "definitions"})
    require(units["status"] in ("not-defined", "draft", "reviewed"), path + ".units.status", "invalid value")
    definitions = require_list(units["definitions"], path + ".units.definitions")
    for index, definition_value in enumerate(definitions):
        definition_path = f"{path}.units.definitions[{index}]"
        definition = require_object(definition_value, definition_path)
        require_exact_keys(definition, definition_path, {"quantity", "unit"})
        require_string(definition["quantity"], definition_path + ".quantity")
        require_string(definition["unit"], definition_path + ".unit")
    require(units["status"] != "not-defined" or not definitions, path + ".units", "not-defined status requires an empty definition list")
    require(units["status"] == "not-defined" or bool(definitions), path + ".units", "draft/reviewed units require definitions")

    validate_string_array(evidence["uncertainty"], path + ".uncertainty", nonempty=True)
    validate_string_array(evidence["limitations"], path + ".limitations", nonempty=True)

    counter = require_object(evidence["counter_evidence"], path + ".counter_evidence")
    require_exact_keys(counter, path + ".counter_evidence", {"status", "records"})
    require(counter["status"] in ("not-collected", "partial", "reviewed"), path + ".counter_evidence.status", "invalid value")
    counter_records = validate_string_array(counter["records"], path + ".counter_evidence.records")
    require(counter["status"] == "not-collected" or bool(counter_records), path + ".counter_evidence", "partial/reviewed status requires records")

    validate_reproduction_pack(evidence["reproduction_pack"], path + ".reproduction_pack")
    availability = evidence["reproduction_pack"]["availability"]
    if knowledge_status == "Reproduced":
        require(availability == "published", path + ".reproduction_pack", "Reproduced status requires a published pack")
        require(methods["status"] == "reviewed", path + ".methods.status", "Reproduced status requires a reviewed method")
        require(data["status"] == "reviewed", path + ".data.status", "Reproduced status requires reviewed data")
    if knowledge_status == "Observed":
        require(data["status"] in ("partial", "reviewed"), path + ".data.status", "Observed status requires referenced data")
    if knowledge_status == "Simulation":
        require(methods["status"] in ("draft", "reviewed"), path + ".methods.status", "Simulation status requires a method")
    if knowledge_status == "Hypothesis":
        require(sources["status"] in ("preliminary", "reviewed"), path + ".sources.status", "Hypothesis status requires at least preliminary sources")
    if delivery_state == "verified":
        require(knowledge_status == "Reproduced", path, "verified research must have Reproduced knowledge status")


def validate_entry(value: Any, path: str, *, expected_kind: str) -> None:
    entry = require_object(value, path)
    required = {"id", "kind", "workstream", "title", "summary", "delivery_state", "knowledge_status"}
    optional = {"blockers", "verification_evidence", "roadmap", "research_evidence"}
    require_exact_keys(entry, path, required, optional)
    entry_id = require_string(entry["id"], path + ".id")
    require(bool(ID_PATTERN.fullmatch(entry_id)), path + ".id", "must be lowercase kebab-case")
    require(entry["kind"] == expected_kind, path + ".kind", f"must be {expected_kind}")
    workstream = entry["workstream"]
    require(workstream in WORKSTREAMS, path + ".workstream", "invalid workstream")
    require_string(entry["title"], path + ".title")
    require_string(entry["summary"], path + ".summary")
    delivery_state = entry["delivery_state"]
    knowledge_status = entry["knowledge_status"]
    require(delivery_state in DELIVERY_STATES, path + ".delivery_state", "invalid delivery state")
    require(knowledge_status in KNOWLEDGE_STATUSES, path + ".knowledge_status", "invalid knowledge status")

    if "blockers" in entry:
        validate_string_array(entry["blockers"], path + ".blockers", nonempty=True)
    if delivery_state == "blocked":
        require("blockers" in entry, path, "blocked state requires blockers")
    if "verification_evidence" in entry:
        validate_string_array(entry["verification_evidence"], path + ".verification_evidence", nonempty=True)
    if delivery_state == "verified":
        require("verification_evidence" in entry, path, "verified state requires verification_evidence")

    if "roadmap" in entry:
        roadmap = require_object(entry["roadmap"], path + ".roadmap")
        require_exact_keys(roadmap, path + ".roadmap", {"lane", "order", "label"})
        require(roadmap["lane"] in LANES, path + ".roadmap.lane", "invalid lane")
        require(roadmap["lane"] == WORKSTREAMS[workstream], path + ".roadmap.lane", "must match the entry workstream")
        require(type(roadmap["order"]) is int and roadmap["order"] >= 0, path + ".roadmap.order", "must be a non-negative integer")
        require_string(roadmap["label"], path + ".roadmap.label")
    if expected_kind == "gate":
        require("roadmap" in entry, path, "every delivery gate must have a roadmap projection")

    research_required = workstream == "research" or delivery_state == "research" or expected_kind == "research-topic"
    if research_required:
        require("research_evidence" in entry, path, "research entries require the complete research_evidence envelope")
    if "research_evidence" in entry:
        validate_research_evidence(entry["research_evidence"], path + ".research_evidence", knowledge_status, delivery_state)


def validate_cockpit(cockpit: Any) -> dict[str, Any]:
    value = require_object(cockpit, "cockpit")
    required = {"$comment", "schema_version", "project", "enums", "workstreams", "boundaries", "gates", "research_topics", "roadmap_generation"}
    require_exact_keys(value, "cockpit", required, {"$schema"})
    require(value["$comment"] == "SPDX-License-Identifier: MIT", "cockpit.$comment", "must declare MIT SPDX licensing")
    require(value["schema_version"] == "1.0", "cockpit.schema_version", "must be 1.0")

    project = require_object(value["project"], "cockpit.project")
    require_exact_keys(project, "cockpit.project", {"id", "language", "baseline"})
    require(project["id"] == "ergon-lab", "cockpit.project.id", "must be ergon-lab")
    require(project["language"] == "en", "cockpit.project.language", "must be en")
    baseline = require_object(project["baseline"], "cockpit.project.baseline")
    require_exact_keys(baseline, "cockpit.project.baseline", {"project", "version", "commit"})
    require(baseline == {"project": "Bitcoin Static", "version": "24.0.5", "commit": "2e8d5f7635c899cc99e71f06dedbe72b3ff7f07b"}, "cockpit.project.baseline", "must bind the exact public baseline")

    enums = require_object(value["enums"], "cockpit.enums")
    require_exact_keys(enums, "cockpit.enums", {"delivery_state", "knowledge_status"})
    require(enums["delivery_state"] == list(DELIVERY_STATES), "cockpit.enums.delivery_state", "must match the governed delivery-state order")
    require(enums["knowledge_status"] == list(KNOWLEDGE_STATUSES), "cockpit.enums.knowledge_status", "must match the governed knowledge-status order")

    workstreams = require_list(value["workstreams"], "cockpit.workstreams")
    expected_workstreams = [{"id": key, "title": title} for key, title in WORKSTREAMS.items()]
    require(workstreams == expected_workstreams, "cockpit.workstreams", "must contain the four governed workstreams in canonical order")

    boundaries = require_object(value["boundaries"], "cockpit.boundaries")
    require_exact_keys(boundaries, "cockpit.boundaries", {"consensus_authority", "chronik", "price_scope", "publication_policy"})
    require(boundaries["consensus_authority"] == "standalone-node", "cockpit.boundaries.consensus_authority", "must remain standalone-node")
    chronik = require_object(boundaries["chronik"], "cockpit.boundaries.chronik")
    expected_chronik = {
        "role": "observe-and-index-only",
        "consensus_authority": False,
        "mempool_authority": False,
        "chain_selection_authority": False,
        "required_for_correctness": False,
    }
    require(chronik == expected_chronik, "cockpit.boundaries.chronik", "must remain observe/index only and non-authoritative")
    require(boundaries["price_scope"] == "historical-context-no-financial-forecasting", "cockpit.boundaries.price_scope", "must prohibit financial forecasting")
    require(boundaries["publication_policy"] == "public-source-provenance-only", "cockpit.boundaries.publication_policy", "must remain public-source provenance only")

    gates = require_list(value["gates"], "cockpit.gates")
    topics = require_list(value["research_topics"], "cockpit.research_topics")
    require(bool(gates), "cockpit.gates", "must not be empty")
    require(len(topics) == 10, "cockpit.research_topics", "must contain the ten governed research topics")
    identifiers: set[str] = set()
    lane_orders: set[tuple[str, int]] = set()
    for collection_name, collection, expected_kind in (
        ("gates", gates, "gate"),
        ("research_topics", topics, "research-topic"),
    ):
        for index, entry in enumerate(collection):
            path = f"cockpit.{collection_name}[{index}]"
            validate_entry(entry, path, expected_kind=expected_kind)
            require(entry["id"] not in identifiers, path + ".id", "duplicate entry id")
            identifiers.add(entry["id"])
            if "roadmap" in entry:
                lane_order = (entry["roadmap"]["lane"], entry["roadmap"]["order"])
                require(lane_order not in lane_orders, path + ".roadmap.order", "duplicate order within lane")
                lane_orders.add(lane_order)

    required_topics = {
        "protocol-native-assets",
        "proportional-rewards",
        "cyphercash",
        "supply-emission",
        "descriptive-price-context",
        "hashrate-responsiveness-elasticity",
        "difficulty-adjustment",
        "block-size-propagation",
        "security",
        "fee-markets",
    }
    require({topic["id"] for topic in topics} == required_topics, "cockpit.research_topics", "topic identifiers do not match the governed scope")

    generation = require_object(value["roadmap_generation"], "cockpit.roadmap_generation")
    require_exact_keys(generation, "cockpit.roadmap_generation", {"projection", "event_log", "document", "manifest"})
    require(generation["projection"] == ["boundaries", "gates.*.id", "gates.*.delivery_state", "gates.*.roadmap"], "cockpit.roadmap_generation.projection", "must exclude narrative and evidence fields")
    require(generation["event_log"] == str(EVENTS_PATH), "cockpit.roadmap_generation.event_log", "unexpected path")
    require(generation["document"] == str(ROADMAP_PATH), "cockpit.roadmap_generation.document", "unexpected path")
    require(generation["manifest"] == str(MANIFEST_PATH), "cockpit.roadmap_generation.manifest", "unexpected path")
    return value


def projection(cockpit: dict[str, Any]) -> dict[str, Any]:
    """Return the only fields allowed to trigger roadmap regeneration."""
    return {
        "boundaries": copy.deepcopy(cockpit["boundaries"]),
        "gates": [
            {
                "id": gate["id"],
                "delivery_state": gate["delivery_state"],
                "roadmap": copy.deepcopy(gate["roadmap"]),
            }
            for gate in sorted(cockpit["gates"], key=lambda item: item["id"])
        ],
    }


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def projection_digest(cockpit: dict[str, Any]) -> str:
    return sha256_bytes(canonical_bytes(projection(cockpit)))


def validate_events(events_document: Any, expected_digest: str) -> dict[str, Any]:
    document = require_object(events_document, "events")
    require_exact_keys(document, "events", {"$comment", "schema_version", "events"})
    require(document["$comment"] == "SPDX-License-Identifier: MIT", "events.$comment", "must declare MIT SPDX licensing")
    require(document["schema_version"] == "1.0", "events.schema_version", "must be 1.0")
    events = require_list(document["events"], "events.events")
    require(bool(events), "events.events", "must contain an initial projection event")
    previous_digest: str | None = None
    for index, event_value in enumerate(events):
        path = f"events.events[{index}]"
        event = require_object(event_value, path)
        require_exact_keys(event, path, {"id", "sequence", "effective_date", "previous_projection_sha256", "projection_sha256", "reason", "changes"})
        event_id = require_string(event["id"], path + ".id")
        require(bool(EVENT_ID_PATTERN.fullmatch(event_id)), path + ".id", "must match ROADMAP-NNNN")
        require(event["sequence"] == index + 1, path + ".sequence", "must be contiguous and start at 1")
        require(event_id == f"ROADMAP-{index + 1:04d}", path + ".id", "must match sequence")
        date = require_string(event["effective_date"], path + ".effective_date")
        require(bool(DATE_PATTERN.fullmatch(date)), path + ".effective_date", "must be YYYY-MM-DD")
        current = require_string(event["projection_sha256"], path + ".projection_sha256")
        require(bool(SHA256_PATTERN.fullmatch(current)), path + ".projection_sha256", "must be lowercase SHA-256")
        require(event["previous_projection_sha256"] == previous_digest, path + ".previous_projection_sha256", "must chain to the preceding event")
        if previous_digest is not None:
            require(current != previous_digest, path + ".projection_sha256", "an event is forbidden when the projection did not change")
        require_string(event["reason"], path + ".reason")
        changes = validate_string_array(event["changes"], path + ".changes", nonempty=True)
        if index == 0:
            require(changes == ["initial-projection"], path + ".changes", "the first event must declare only initial-projection")
        else:
            for change_index, change in enumerate(changes):
                allowed = (
                    change.startswith("boundaries.")
                    or bool(re.fullmatch(r"gates\.[a-z0-9]+(?:-[a-z0-9]+)*\.(?:id|delivery_state|roadmap(?:\.(?:lane|order|label))?)", change))
                )
                require(allowed, f"{path}.changes[{change_index}]", "must identify a governed boundary or gate projection field")
        previous_digest = current
    require(previous_digest == expected_digest, "events.events[-1].projection_sha256", f"does not match current projection {expected_digest}; append a reviewed event before regenerating")
    return document


def render_roadmap(cockpit: dict[str, Any], digest: str, event: dict[str, Any]) -> str:
    status_labels = {
        "verified": "Verified",
        "active": "Active",
        "blocked": "Blocked",
        "planned": "Planned",
        "research": "Research",
    }
    lines = [
        "<!-- SPDX-License-Identifier: MIT -->",
        "",
        "# Public roadmap",
        "",
        "<!-- Generated by tools/cockpit/cockpit.py; do not edit by hand. -->",
        "",
        f"Projection: `{digest}`",
        "",
        f"Roadmap event: `{event['id']}`",
        "",
        "This roadmap reports delivery gates. Knowledge labels and full research evidence live in `cockpit/cockpit.yaml`; a delivery state never upgrades a knowledge claim.",
        "",
        "## Non-negotiable boundaries",
        "",
        "- The standalone node is the only consensus authority.",
        "- Chronik may observe and index. It is never a consensus, mempool, activation, or chain-selection authority and is not required for correctness.",
        "- Price material is limited to historical context; financial forecasting is out of scope.",
        "- Publication uses reviewed public-source provenance only.",
        "",
        "## Delivery-state legend",
        "",
        "| State | Meaning |",
        "| --- | --- |",
        "| Verified | The declared gate has portable evidence and has passed its checks. |",
        "| Active | Public work is in progress; completion is not claimed. |",
        "| Blocked | A named prerequisite prevents progress or publication. |",
        "| Planned | The work is in scope but has not started. |",
        "| Research | Evidence gathering is in progress; no settled result is implied. |",
        "",
    ]
    gates = sorted(
        cockpit["gates"],
        key=lambda gate: (LANES.index(gate["roadmap"]["lane"]), gate["roadmap"]["order"], gate["id"]),
    )
    for lane in LANES:
        lines.extend([f"## {lane}", "", "| Gate | Delivery state | ID |", "| --- | --- | --- |"])
        lane_gates = [gate for gate in gates if gate["roadmap"]["lane"] == lane]
        for gate in lane_gates:
            label = gate["roadmap"]["label"].replace("|", "\\|")
            lines.append(f"| {label} | {status_labels[gate['delivery_state']]} | `{gate['id']}` |")
        if not lane_gates:
            lines.append("| No projected gate | Planned | — |")
        lines.append("")
    lines.extend([
        "## Regeneration rule",
        "",
        "The file changes only when the governed projection changes: a boundary, gate identifier, delivery state, or roadmap placement/label. The matching projection event must be reviewed and appended first. Narrative, knowledge, method, data, and evidence edits do not regenerate this file.",
        "",
    ])
    return "\n".join(lines)


def render_manifest(digest: str, event: dict[str, Any], roadmap_text: str) -> str:
    manifest = {
        "$comment": "SPDX-License-Identifier: MIT",
        "schema_version": "1.0",
        "generator": {"path": "tools/cockpit/cockpit.py", "version": VERSION},
        "projection": {"algorithm": "sha256", "digest": digest},
        "event": {"id": event["id"]},
        "inputs": [str(COCKPIT_PATH), str(EVENTS_PATH)],
        "output": {"path": str(ROADMAP_PATH), "sha256": sha256_bytes(roadmap_text.encode("utf-8"))},
    }
    return json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def load_and_validate(root: Path, *, require_event_match: bool = True) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
    for schema_path in SCHEMA_PATHS:
        schema = load_json_document(root / schema_path)
        require(isinstance(schema, dict) and schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", str(schema_path), "must declare JSON Schema 2020-12")
        require(schema.get("$comment") == "SPDX-License-Identifier: MIT", str(schema_path), "must declare MIT SPDX licensing")
    cockpit = validate_cockpit(load_json_document(root / COCKPIT_PATH))
    digest = projection_digest(cockpit)
    events = None
    if require_event_match:
        events = validate_events(load_json_document(root / EVENTS_PATH), digest)
    return cockpit, events, digest


def expected_outputs(root: Path) -> tuple[str, str]:
    cockpit, events, digest = load_and_validate(root)
    assert events is not None
    event = events["events"][-1]
    roadmap = render_roadmap(cockpit, digest, event)
    manifest = render_manifest(digest, event, roadmap)
    return roadmap, manifest


def check(root: Path) -> None:
    roadmap, manifest = expected_outputs(root)
    actual_roadmap = (root / ROADMAP_PATH).read_text(encoding="utf-8")
    actual_manifest = (root / MANIFEST_PATH).read_text(encoding="utf-8")
    require(actual_roadmap == roadmap, str(ROADMAP_PATH), "is stale or hand-edited; append an event if required, then regenerate")
    require(actual_manifest == manifest, str(MANIFEST_PATH), "is stale or hand-edited; regenerate deterministically")


def generate(root: Path) -> None:
    roadmap, manifest = expected_outputs(root)
    (root / ROADMAP_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_PATH).parent.mkdir(parents=True, exist_ok=True)
    (root / ROADMAP_PATH).write_text(roadmap, encoding="utf-8")
    (root / MANIFEST_PATH).write_text(manifest, encoding="utf-8")


def self_test(root: Path) -> None:
    cockpit, events, digest = load_and_validate(root)
    assert events is not None

    # Rendering must be byte-deterministic.
    event = events["events"][-1]
    first = render_roadmap(cockpit, digest, event)
    second = render_roadmap(copy.deepcopy(cockpit), projection_digest(copy.deepcopy(cockpit)), copy.deepcopy(event))
    require(first == second, "self-test.determinism", "identical inputs rendered differently")

    # Event review metadata is not part of the generated projection.
    metadata_change = copy.deepcopy(event)
    metadata_change["effective_date"] = "2099-12-31"
    metadata_change["reason"] += " Metadata-only test."
    require(render_roadmap(cockpit, digest, metadata_change) == first, "self-test.events", "event metadata altered roadmap output")

    # Narrative/evidence changes are outside the roadmap projection.
    narrative_change = copy.deepcopy(cockpit)
    narrative_change["gates"][0]["summary"] += " Narrative-only test."
    require(projection_digest(narrative_change) == digest, "self-test.projection", "narrative change altered projection")

    # Boundary and gate-state changes are inside the projection.
    state_change = copy.deepcopy(cockpit)
    state_change["gates"][0]["delivery_state"] = "active"
    require(projection_digest(state_change) != digest, "self-test.projection", "gate-state change did not alter projection")
    boundary_change = copy.deepcopy(cockpit)
    boundary_change["boundaries"]["chronik"]["required_for_correctness"] = True
    require(projection_digest(boundary_change) != digest, "self-test.projection", "boundary change did not alter projection")

    # Research validation must fail closed if any mandatory field disappears.
    incomplete = copy.deepcopy(cockpit)
    del incomplete["research_topics"][0]["research_evidence"]["counter_evidence"]
    try:
        validate_cockpit(incomplete)
    except ValidationError:
        pass
    else:
        fail("self-test.research", "missing counter_evidence was accepted")

    # Reproduced claims must point to a complete published pack.
    overclaim = copy.deepcopy(cockpit)
    overclaim["research_topics"][0]["knowledge_status"] = "Reproduced"
    try:
        validate_cockpit(overclaim)
    except ValidationError:
        pass
    else:
        fail("self-test.reproduction", "Reproduced status without a published pack was accepted")

    # A projection change without a matching reviewed event must fail closed.
    try:
        validate_events(events, projection_digest(state_change))
    except ValidationError:
        pass
    else:
        fail("self-test.events", "unlogged projection change was accepted")


def repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "generate", "projection-digest", "render-roadmap", "render-manifest", "self-test"))
    parser.add_argument("--root", type=Path, default=repository_root(), help="repository root (defaults to the script's repository)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    try:
        if args.command == "projection-digest":
            cockpit, _, digest = load_and_validate(root, require_event_match=False)
            del cockpit
            print(digest)
        elif args.command == "render-roadmap":
            cockpit, events, digest = load_and_validate(root)
            assert events is not None
            sys.stdout.write(render_roadmap(cockpit, digest, events["events"][-1]))
        elif args.command == "render-manifest":
            _, manifest = expected_outputs(root)
            sys.stdout.write(manifest)
        elif args.command == "generate":
            generate(root)
            print("Generated roadmap and manifest.")
        elif args.command == "self-test":
            self_test(root)
            print("Cockpit self-tests passed.")
        else:
            check(root)
            print("Cockpit validation and generated-file checks passed.")
    except (ValidationError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

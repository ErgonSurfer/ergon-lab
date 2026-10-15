#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fresh-process component observations, never automatic proof promotion.

Six disjoint native groups have a fixed 60-second process deadline. Reports
exclude process text and binding paths. The source projection and CMake cache
are checked, but are not a substitute for recorded binary construction.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time

ENV = {"LANG": "C", "LC_ALL": "C", "NO_COLOR": "1",
       "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "TERM": "dumb", "TZ": "UTC"}
PREFIX = "chronik_upstream_archival_tests/"
BRIDGE = (
    "chronik_native_upstream_bridge_tests/transaction_wire_units_and_spent_coins",
    "chronik_native_upstream_bridge_tests/script_roundtrip_and_malformed_rejection",
    "chronik_native_upstream_bridge_tests/native_fee_parity_and_overflow_rejection",
    "chronik_native_upstream_bridge_tests/runtime_admission_and_failure_isolation",
    "chronik_native_upstream_disk_tests/native_block_undo_and_positioned_coinbase_reads",
    "chronik_native_upstream_disk_tests/position_catalogue_incremental_and_bounded_cache",
    "chronik_native_upstream_disk_tests/position_corruption_and_storage_drift_fail_closed",
    "chronik_native_upstream_disk_tests/position_native_aba_and_suffix_reorg_recovery",
)
STARTUP = tuple(PREFIX + name for name in (
    "startup_owned_graph_and_source_storage_fences",
    "startup_drift_quarantines_database_without_stopping_node",
    "startup_nonempty_mempool_parent_child_and_reopen",
))
CAPTURE = tuple(PREFIX + name for name in (
    "actual_http_complete_read_profile_and_alp_lifecycle",
    "actual_alp_invalid_mint_and_malformed_metadata_do_not_change_consensus",
    "native_source_generation_counter_laws",
    "native_source_generation_delayed_callback_aba",
    "actual_http_worker_admission_and_generation_rejection",
    "actual_http_fault_admission_does_not_stop_native_node",
    "captured_gap_retries_and_busy_source_stays_unavailable",
    "bounded_capture_window_and_deep_reorg_fail_closed",
    "mempool_capture_and_writer_deltas_preserve_dependencies",
    "incremental_capture_reuses_prefix_and_reads_only_suffix",
    "incremental_capture_prefix_owner_and_bounds_fail_closed",
    "unlocked_disk_capture_source_retry_before_db",
    "unlocked_disk_capture_stable_fault_is_index_only",
))
STOP = tuple(PREFIX + name for name in (
    "unlocked_disk_capture_stop_allows_native_progress",
    "unlocked_capture_stop_allows_native_progress",
    "actual_http_ws_runtime_stop_joins_native_users",
    "ordered_callback_worker_reorg_and_reopen",
    "callback_stop_joins_inflight_and_discards_pending",
    "callback_overflow_quarantines_only_index",
    "callback_stop_drains_subscriber_inflight",
    "callback_mempool_parent_child_remove_mine_and_reorg",
    "real_replay_reopen_queries_and_offline_reorg",
    "identity_paths_and_unbound_database_fail_closed",
    "source_drift_refuses_stale_view_then_reconciles",
    "native_barrier_parent_child_mining_reorg_and_conflict",
    "native_barrier_owner_mismatch_isolates_failure",
))
LIVENESS = tuple(PREFIX + name for name in (
    "silent_mempool_generation_recovers_without_callback",
    "control_handles_do_not_wait_behind_native_capture",
))
CHAINSTATE = tuple(PREFIX + name for name in (
    "owned_native_db_snapshot_outlives_wrapper",
    "native_coin_snapshot_overlay_and_owner_laws",
    "cold_chainstate_capture_retries_owner_and_aba",
    "cold_chainstate_worker_stop_allows_native_progress",
    "spent_cache_tombstone_quarantines_only_index",
))
GROUPS = (
    ("native-bridge-and-disk", BRIDGE, 166),
    ("owned-startup", STARTUP, 593),
    ("capture-and-query-controls", CAPTURE, 1063),
    ("runtime-stop-and-reopen-controls", STOP, 307),
    ("silent-generation-and-control-locks", LIVENESS, 50),
    ("owned-cold-chainstate-reads", CHAINSTATE, 106),
)
READ_MANIFEST = "docs/engineering/chronik/owned-chainstate-reads-v1.json"
PLUGIN_MANIFEST = "docs/engineering/chronik/upstream-plugin-fixtures-v1.json"
RESTART_MANIFEST = "docs/engineering/chronik/upstream-plugin-restart-v1.json"
PLUGIN_GROUPS = tuple((name, (PREFIX + name,), count) for name, count in (
    ("upstream_public_plugin_lifecycle", 55),
    ("upstream_public_plugin_exception_is_index_only", 16),
    ("upstream_public_plugin_invalid_output_is_index_only", 16),
    ("upstream_public_plugin_source_and_config_drift_refused", 13),
))
DISABLED_GROUP = ("upstream-public-plugin-disabled",
                  (PREFIX + "upstream_public_plugin_disabled_refuses_setup",), 6)
READ_INPUTS = frozenset({
    "chronik/UpstreamLink.cmake", "chronik/chronik-cpp/ergon_archival_probe.cpp",
    "chronik/chronik-cpp/ergon_archival_probe.h", "chronik/chronik-lib/src/ergon_archival.rs",
    "src/coins.h", "src/coins.cpp", "src/dbwrapper.h", "src/dbwrapper.cpp",
    "src/txdb.h", "src/txdb.cpp", "src/test/chronik_upstream_archival_tests.cpp",
    "tests/compatibility/upstream-chronik/run_repetitions.py",
    "tests/compatibility/upstream-chronik/self_test.py",
})
ADAPTER_INPUTS = (
    "chronik/Cargo.lock", "chronik/chronik-bridge/src/ffi.rs",
    "chronik/chronik-cpp/chronik_bridge.cpp", "chronik/chronik-cpp/chronik_bridge.h",
    "chronik/chronik-cpp/ergon_position_reader.cpp", "chronik/chronik-cpp/ergon_position_reader.h",
    "chronik/chronik-cpp/ergon_archival_probe.cpp", "chronik/chronik-cpp/ergon_archival_probe.h",
    "chronik/chronik-cpp/ergon_archival_worker.cpp", "chronik/chronik-cpp/ergon_archival_worker.h",
    "chronik/chronik-cpp/ergon_node_access.h", "chronik/chronik-lib/Cargo.toml",
    "chronik/chronik-lib/build.rs", "chronik/chronik-lib/src/ergon_archival.rs",
    "chronik/chronik-lib/src/ergon_runtime.rs", "chronik/chronik-lib/src/bridge.rs",
    "chronik/chronik-lib/src/lib.rs", "chronik/chronik-lib/src/ergon_service.rs",
    "chronik/chronik-http/src/server.rs", "src/chain.h", "src/chain.cpp",
    "src/txmempool.h", "src/txmempool.cpp", "src/util/source_generation.h",
    "chronik/chronik-lib/src/ergon_http_tests.rs",
    "src/coins.h", "src/coins.cpp", "src/dbwrapper.h", "src/dbwrapper.cpp",
    "src/txdb.h", "src/txdb.cpp",
)
PLUGIN_ADAPTER_INPUTS = (
    "chronik/chronik-lib/src/ergon_plugin_fixture.rs",
    "chronik/chronik-db/src/mem/mempool.rs",
    "tests/compatibility/upstream-chronik/plugins/ergon_public_probe.py",
    "src/test/main.cpp",
)
PLUGIN_ENABLED_INPUTS = (
    "chronik/chronik-plugin-impl/src/module.rs",
    "chronik/chronik-plugin-impl/python/upstream/script.py",
    "chronik/chronik-plugin-impl/python/upstream/slp.py",
)
SERVICE_ADAPTER_INPUTS = ("CMakeLists.txt", "src/init.cpp",
    "chronik/chronik-cpp/ergon_regtest_service.h", "chronik/chronik-cpp/ergon_regtest_service.cpp")

def plugin_inputs():
    # The public runner and source gate share one exact finite inventory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools/engineering"))
    from check_chronik_plugin_fixtures import TECHNICAL
    return TECHNICAL

class Rejected(ValueError):
    pass

def require(ok, message):
    if not ok:
        raise Rejected(message)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode()

def regular(path):
    require(path.is_file() and not path.is_symlink(), "regular input required")
    return path.read_bytes()

def source_projection(source, manifest, read_manifest=None, plugin_manifest=None, restart_manifest=None):
    rows = json.loads(regular(manifest))["files"]
    names = [r["path"] for r in rows]
    require(len(rows) == 34 and names == sorted(set(names)), "finite source projection required")
    if read_manifest is not None:
        layer = json.loads(regular(read_manifest))
        require(layer["schema"] == "ergon-owned-chainstate-reads/v1" and
                layer["public_parent"] == {"commit": "8fc7aec5a3935533fe7d4e0ccbfdf14b276abc37",
                                           "tree": "0a97f74862c1f7ff1842ce60aa7969837b31a887"},
                "read-session projection parent/schema drift")
        require([r["path"] for r in layer["files"]] == sorted(READ_INPUTS), "finite read-session projection required")
        merged = {r["path"]: r for r in rows}
        for row in layer["files"]:
            if row["path"] in merged:
                require(row["before"] == merged[row["path"]]["after"], "read-session projection preimage drift")
            merged[row["path"]] = row
        require(len(merged) == 42, "combined source projection cardinality drift")
        rows = [merged[p] for p in sorted(merged)]
    if plugin_manifest is not None:
        layer = json.loads(regular(plugin_manifest))
        require(layer["schema"] == "ergon-upstream-plugin-fixtures/v1" and
                layer["public_parent"] == {"commit": "eaa73b03ed060af5623eb30ff4a08502d2a73c7a",
                                           "tree": "85ca3e2bd4a0d5017cbad506aa26539f99e38d2b"},
                "plugin projection parent/schema drift")
        require([r["path"] for r in layer["files"]] == sorted(plugin_inputs()),
                "finite plugin projection required")
        merged = {r["path"]: r for r in rows}
        for row in layer["files"]:
            if row["path"] in merged:
                require(row["before"] == merged[row["path"]]["after"], "plugin projection preimage drift")
            merged[row["path"]] = row
        rows = [merged[p] for p in sorted(merged)]
    if restart_manifest is not None:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools/engineering"))
        from check_chronik_plugin_restart import PARENT, TREE, TECHNICAL
        layer = json.loads(regular(restart_manifest))
        require(layer["schema"] == "ergon-upstream-plugin-restart/v1" and
                layer["public_parent"] == {"commit": PARENT, "tree": TREE} and
                [r["path"] for r in layer["files"]] == sorted(TECHNICAL), "restart source projection drift")
        merged = {r["path"]: r for r in rows}
        for row in layer["files"]:
            if row["path"] in merged:
                require(row["before"] == merged[row["path"]]["after"], "restart projection preimage drift")
            merged[row["path"]] = row
        rows = [merged[p] for p in sorted(merged)]
    service_manifest = source / "docs/engineering/chronik/upstream-regtest-service-v1.json"
    if service_manifest.exists():
        from check_chronik_regtest_service import PARENT, TREE, TECHNICAL
        layer = json.loads(regular(service_manifest))
        require(layer["schema"] == "ergon-upstream-regtest-service/v1" and
                layer["public_parent"] == {"commit": PARENT, "tree": TREE} and
                [r["path"] for r in layer["files"]] == sorted(TECHNICAL), "regtest service projection drift")
        merged = {r["path"]: r for r in rows}
        for row in layer["files"]:
            if row["path"] in merged:
                require(row["before"] == merged[row["path"]]["after"], "service projection preimage drift")
            merged[row["path"]] = row
        rows = [merged[p] for p in sorted(merged)]
    recovery_manifest = source / "docs/engineering/chronik/upstream-service-recovery-v1.json"
    if recovery_manifest.exists():
        from check_chronik_service_recovery import PARENT, TREE, TECHNICAL
        layer = json.loads(regular(recovery_manifest))
        require(layer["schema"] == "ergon-upstream-service-recovery/v1" and
                layer["public_parent"] == {"commit": PARENT, "tree": TREE} and
                [r["path"] for r in layer["files"]] == sorted(TECHNICAL), "service recovery projection drift")
        merged = {r["path"]: r for r in rows}
        for row in layer["files"]:
            if row["path"] in merged:
                require(row["before"] == merged[row["path"]]["after"], "recovery projection preimage drift")
            merged[row["path"]] = row
        rows = [merged[p] for p in sorted(merged)]
    for row in rows:
        relative = Path(row["path"])
        require(not relative.is_absolute() and ".." not in relative.parts, "escaping source projection")
        path = source / relative
        require(path.resolve().is_relative_to(source), "source parent alias escapes root")
        raw = regular(path)
        expected = row["after"]
        mode = "100755" if stat.S_IMODE(path.stat().st_mode) & 0o111 else "100644"
        require(len(raw) == expected["bytes"] and digest(raw) == expected["sha256"] and mode == expected["mode"],
                "source projection drift")
    return digest(canonical(rows))

def bind_build(source, build, profile, manifest):
    require(profile in ("archival", "plugins", "compiled-out", "upstream-linked-dormant", "upstream-plugins-dormant", "regtest"),
            "unknown build profile")
    projection = source_projection(source, manifest, source / READ_MANIFEST, source / PLUGIN_MANIFEST, source / RESTART_MANIFEST)
    require(not source.is_relative_to(build) and not build.is_relative_to(source), "separate build root required")
    cache = {}
    for line in regular(build / "CMakeCache.txt").decode("utf-8").splitlines():
        match = re.fullmatch(r"([^:#=]+):[^=]+=(.*)", line)
        if match:
            require(match[1] not in cache, "ambiguous build cache")
            cache[match[1]] = match[2]
    require(Path(cache["CMAKE_HOME_DIRECTORY"]).resolve() == source, "build source mismatch")
    require(cache.get("BUILD_CHRONIK_OBSERVER") == "OFF", "observer profiles must remain separate")
    require(cache.get("BUILD_CHRONIK_UPSTREAM_REGTEST", "OFF") == ("ON" if profile == "regtest" else "OFF"),
            "regtest service profile mismatch")
    if profile == "regtest":
        require(cache.get("BUILD_BITCOIN_QT") == "OFF", "regtest service excludes Qt")
    expected_link = "OFF" if profile == "compiled-out" else "ON"
    require(cache.get("BUILD_CHRONIK_UPSTREAM_LINK", "OFF") == expected_link, "link profile mismatch")
    plugins = profile in ("plugins", "upstream-plugins-dormant")
    require(cache.get("BUILD_CHRONIK_UPSTREAM_PLUGIN_TESTS", "OFF") == ("ON" if plugins else "OFF"),
            "plugin profile mismatch")
    python = None
    if profile in ("archival", "plugins", "upstream-linked-dormant", "upstream-plugins-dormant", "regtest"):
        require(cache.get("BUILD_CHRONIK_UPSTREAM_ARCHIVAL_TESTS") == "ON", "archival fixture missing")
        ninja = regular(build / "build.ninja").decode("utf-8")
        inputs = ADAPTER_INPUTS + PLUGIN_ADAPTER_INPUTS + SERVICE_ADAPTER_INPUTS
        suffix = ""
        if plugins:
            versions = re.findall(r"CHRONIK_ERGON_PYTHON_VERSION=(3\.12\.[0-9]+)(?:\s|$)", ninja)
            require(len(versions) == 1, "missing/ambiguous configured Python version")
            executable = Path(cache["_Python3_EXECUTABLE"]).resolve(strict=True)
            library = Path(cache["_Python3_LIBRARY_RELEASE"]).resolve(strict=True)
            python = {"version": versions[0], "executable_sha256": digest(regular(executable)),
                      "library_sha256": digest(regular(library))}
            python["embedding_sha256"] = digest((python["version"] + "\n" +
                python["executable_sha256"] + "\n" + python["library_sha256"] + "\n").encode())
            require("CHRONIK_ERGON_PYTHON_ID=" + python["embedding_sha256"] in ninja and
                    "--features plugins" in ninja, "configured Python embedding/features mismatch")
            inputs += PLUGIN_ENABLED_INPUTS
            suffix = "python-profile:" + python["embedding_sha256"] + "\n"
        else:
            require("--features plugins" not in ninja and "CHRONIK_ERGON_PYTHON_ID=" not in ninja,
                    "disabled profile linked plugin feature")
        adapter = "ergon-archival-adapter/v1\n" + "".join(
            path + ":" + digest(regular(source / path)) + "\n" for path in inputs) + suffix
        adapter_id = digest(adapter.encode())
        require("CHRONIK_ERGON_ADAPTER_ID=" + adapter_id in regular(build / "build.ninja").decode(),
                "configured adapter identity mismatch")
    return {"technical_projection_sha256": projection,
            "cmake_cache_sha256": digest(regular(build / "CMakeCache.txt")),
            "profile": profile, "plugin_engine_enabled": plugins, "python_embedding": python,
            "binary_to_source_provenance": "external-build-record-required"}

def environment(run):
    child_tmp = run / "tmp"
    child_tmp.mkdir()
    return dict(ENV, TMPDIR=str(child_tmp))

def group_exists(pid):
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False

def stop_group(proc):
    # This PID is the leader of a session created by this runner, never borrowed.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if group_exists(proc.pid):
            os.killpg(proc.pid, sig)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            continue
        deadline = time.monotonic() + 2
        while group_exists(proc.pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        if not group_exists(proc.pid):
            return
    require(not group_exists(proc.pid), "process group cleanup incomplete")

def remove_owned(run, work):
    require(run.parent == work and run.name.startswith("ergon-upstream-") and not run.is_symlink(),
            "refuse unowned cleanup")
    shutil.rmtree(run)
    require(not run.exists(), "disposable root cleanup incomplete")

def execute(command, work, forbidden, timeout=60):
    run = Path(tempfile.mkdtemp(prefix="ergon-upstream-", dir=work)).resolve()
    proc = None
    try:
        env = environment(run)
        with (run / "stdout").open("xb") as out, (run / "stderr").open("xb") as err:
            proc = subprocess.Popen(command, cwd=run, env=env, stdout=out, stderr=err,
                                    start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            finally:
                stop_group(proc)
        require(code == 0, "nonzero fixture exit")
        streams = []
        for path in (run / "stdout", run / "stderr"):
            require(path.stat().st_size <= 2 * 1024 * 1024, "oversized fixture output")
            raw = path.read_bytes()
            text = raw.decode("utf-8", errors="strict")
            require(not any(str(p) in text for p in (*forbidden, run, env["TMPDIR"])), "binding path in output")
            streams.append(text)
        return tuple(streams)
    finally:
        if proc is not None:
            stop_group(proc)
        remove_owned(run, work)

def parse_streams(streams, cases, assertions):
    lines = [line for stream in streams for line in stream.splitlines()]
    running = [re.fullmatch(r"Running (0|[1-9][0-9]*) test cases?\.\.\.", line) for line in lines]
    running = [int(m[1]) for m in running if m]
    summaries = [re.fullmatch(r"  (0|[1-9][0-9]*) assertions out of (0|[1-9][0-9]*) passed", line) for line in lines]
    summaries = [(int(m[1]), int(m[2])) for m in summaries if m]
    require(sum(line.startswith("Running ") for line in lines) == 1 and
            sum(" assertions out of " in line for line in lines) == 1,
            "ambiguous or malformed summary")
    require(running == [len(cases)] and summaries == [(assertions, assertions)], "case/assertion summary mismatch")
    for marker in ("Fixture start: ", "Fixture done: "):
        names = [line[len(marker):] for line in lines if line.startswith(marker)]
        require(names == list(cases), "exact case identities/order mismatch")
    require(not any(re.search(r"\b(?:failed|aborted|timed out)\b", line) for line in lines), "failure summary present")
    return {"cases": list(cases), "passing_assertions": assertions,
            "stdout_public_text_sha256": digest(streams[0].encode()),
            "stderr_public_text_sha256": digest(streams[1].encode())}

def filter_for(cases):
    if cases == BRIDGE:
        return "chronik_native_upstream_bridge_tests,chronik_native_upstream_disk_tests"
    require(all(case.startswith(PREFIX) for case in cases), "unexpected suite")
    return PREFIX + ",".join(case[len(PREFIX):] for case in cases)

def refusal_class(error):
    # Never serialize str(error): subprocess and OS exceptions contain paths,
    # commands and operational output. These are fixed public categories only.
    if isinstance(error, subprocess.TimeoutExpired):
        return "process-timeout"
    if isinstance(error, subprocess.SubprocessError):
        return "child-process-refusal"
    if isinstance(error, OSError):
        return "operating-system-refusal"
    if isinstance(error, KeyError):
        return "binding-field-missing"
    return "contract-refusal"


def repeat(binary, work, forbidden, repetitions, profile=None):
    require(1 <= repetitions <= 20, "repetitions outside bounded range")
    require(profile in (None, "archival", "plugins", "regtest"), "unknown repetition profile")
    require(len({case for _, cases, _ in GROUPS for case in cases}) == 44, "non-disjoint test groups")
    groups = GROUPS + (PLUGIN_GROUPS if profile == "plugins" else (DISABLED_GROUP,) if profile in ("archival", "regtest") else ())
    require(len({case for _, cases, _ in groups for case in cases}) == sum(len(c) for _, c, _ in groups),
            "plugin cases overlap inherited groups")
    result = []
    for role, cases, assertions in groups:
        first = None
        for iteration in range(repetitions):
            try:
                streams = execute([str(binary), "--run_test=" + filter_for(cases),
                                   "--report_level=short", "--log_level=message", "--color_output=no"],
                                  work, forbidden)
                observation = parse_streams(streams, cases, assertions)
                require(first is None or observation == first, "fresh-process output instability")
                first = observation
            except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
                print("REFUSED: " + role + "; repetition=" + str(iteration + 1) +
                      "; class=" + refusal_class(error), file=sys.stderr, flush=True)
                raise
        result.append(dict(role=role, repetitions=repetitions, **first))
        print("PASS: " + role + "; " + str(len(cases)) + " cases; " + str(assertions) + " assertions", flush=True)
    return {"groups": result, "selected_cases": sum(len(c) for _, c, _ in groups),
            "assertions_per_repetition": sum(a for _, _, a in groups),
            "fresh_processes": repetitions * len(groups), "per_process_timeout_seconds": 60,
            "stream_contract": "strict UTF-8, exact bytes within each group, binding paths rejected; no text retained"}

def mining_address():
    # Synthetic all-zero hash; no wallet, key or operator material.
    payload = bytes([111]) + bytes(20)
    payload += hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]
    value, encoded = int.from_bytes(payload, "big"), ""
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    while value:
        value, remainder = divmod(value, 58)
        encoded = alphabet[remainder] + encoded
    return encoded

def smoke(binary, work):
    run = Path(tempfile.mkdtemp(prefix="ergon-upstream-", dir=work)).resolve()
    data = run / "data"
    data.mkdir()
    proc = None
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    def rpc(method, params=()):
        credential = (data / "regtest/.cookie").read_bytes().strip()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            connection.request("POST", "/", json.dumps({"jsonrpc": "1.0", "id": "smoke", "method": method, "params": list(params)}),
                               {"Authorization": "Basic " + base64.b64encode(credential).decode(), "Content-Type": "application/json"})
            response = connection.getresponse()
            require(response.status == 200, "RPC transport failure")
            decoded = json.loads(response.read(2 * 1024 * 1024))
            require(decoded["error"] is None, "RPC error")
            return decoded["result"]
        finally:
            connection.close()
    try:
        with (run / "output").open("xb") as output:
            proc = subprocess.Popen([str(binary), "-regtest", "-server=1", "-listen=0", "-connect=0",
                                     "-dnsseed=0", "-discover=0", "-upnp=0", "-printtoconsole=0",
                                     "-datadir=" + str(data), "-rpcbind=127.0.0.1", "-rpcallowip=127.0.0.1", "-rpcport=" + str(port)],
                                    cwd=run, env=environment(run), stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 30
            while True:
                require(proc.poll() is None, "daemon exited during startup")
                try:
                    before = rpc("getblockchaininfo")
                    break
                except (OSError, ValueError):
                    require(time.monotonic() < deadline, "startup deadline exceeded")
                    time.sleep(0.1)
            require(before["chain"] == "regtest" and before["blocks"] == 0, "unexpected initial chain")
            hashes = rpc("generatetoaddress", (3, mining_address()))
            after = rpc("getblockchaininfo")
            require(len(hashes) == 3 and after["blocks"] == 3 and after["bestblockhash"] == hashes[-1], "mining mismatch")
            require(rpc("getconnectioncount") == 0, "unexpected peer connection")
            for block_hash in hashes:
                block = rpc("getblock", (block_hash, 2))
                require(sum(v["value"] for v in block["tx"][0]["vout"]) == 0, "nonzero coinbase subsidy")
            require(not any("chronik" in p.name.lower() for p in data.rglob("*")), "dormant profile created Chronik state")
            rpc("stop")
            require(proc.wait(timeout=15) == 0, "daemon stop failed")
            stop_group(proc)
        return {"network": "regtest", "initial_height": 0, "final_height": 3,
                "zero_subsidy_coinbases": 3, "external_connections": 0,
                "chronik_state_created": False, "daemon_exit": 0}
    finally:
        if proc is not None:
            stop_group(proc)
        remove_owned(run, work)

def write_report(path, payload):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as out:
        out.write(canonical(payload))
        out.flush()
        os.fsync(out.fileno())

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("repeat", "smoke"))
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--build-root", type=Path, required=True)
    parser.add_argument("--adaptation-manifest", type=Path, required=True)
    parser.add_argument("--work-parent", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--profile", choices=("archival", "plugins", "compiled-out", "upstream-linked-dormant", "upstream-plugins-dormant", "regtest"), required=True)
    parser.add_argument("--repetitions", type=int, default=10)
    args = parser.parse_args()
    try:
        source, build, work = (p.resolve(strict=True) for p in (args.source_root, args.build_root, args.work_parent))
        report = args.report.parent.resolve(strict=True) / args.report.name
        require(not report.exists() and not report.is_symlink(), "report overwrite refused")
        require(not any(report.is_relative_to(p) for p in (source, build)), "report must remain outside source/build")
        require(not work.is_relative_to(source) and not work.is_relative_to(build), "separate disposable work parent required")
        require(args.profile == "regtest" or (args.command == "repeat") == (args.profile in ("archival", "plugins")), "command/profile mismatch")
        binding = bind_build(source, build, args.profile, args.adaptation_manifest)
        binary = build / ("src/test/test_bitcoin" if args.command == "repeat" else "src/bitcoind")
        raw = regular(binary)
        before = digest(raw)
        result = repeat(binary, work, (source, build, binary), args.repetitions, args.profile) if args.command == "repeat" else smoke(binary, work)
        require(digest(regular(binary)) == before, "binary changed during observation")
        require(binding == bind_build(source, build, args.profile, args.adaptation_manifest), "source/build binding changed during observation")
        payload = {"schema": "ergon-upstream-component-observation/v1", "result": "pass",
                   "knowledge_status": "Observed", "proof_ceiling": "component_observation",
                   "binding": binding, "binary_sha256": before, "binary_bytes": len(raw),
                   "child_environment": dict(ENV, TMPDIR="<runner-created-unique-directory>"),
                   "disposable_roots_removed": True, "observation": result}
        serialized = canonical(payload).decode()
        require(not any(str(p) in serialized for p in (source, build, work, binary)), "binding path in report")
        write_report(report, payload)
        print("PASS: component observation; cleanup verified; exclusive path-free report")
        return 0
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print("FAIL: component observation rejected; class=" + refusal_class(error) +
              "; no passing report", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())

# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Combine existing upstream ALP and plugin oracles on the real regtest daemon.

No token parser, validator, plugin implementation or daemon rule is added.
"""
import argparse
from pathlib import Path
import sys
import tempfile
import alp_service as alp
import plugin_service as plugin
import regtest_service as service
import run_repetitions as runner

PARENTS = (0, 1, 2, 1)
# Native outpoint ancestry, not token validation logic.
INPUTS = ((None,), ((0, 2),), ((0, 1), (1, 1)), ((2, 2),))
UNSPENT = ((1, 2), (2, 1), (3, 1))


def output_metadata(value, field, op, color):
    plugin.plugin_output(value, field, PARENTS[op], color[0] if color else 0)


def check_plugin_snapshot(daemon, ids, height, block_hash):
    runner.require(len(ids) == 4 and len(set(ids)) == 4, "four distinct ALP operations required")
    snapshots = {}
    def get(path):
        raw = alp.response(daemon, path)
        snapshots[path] = runner.digest(raw)
        return raw
    for op, txid in enumerate(ids):
        tx = service.fields(get("/tx/" + txid))
        runner.require(alp.scalar(tx, 1) == bytes.fromhex(txid)[::-1], "plugin ALP transaction identity")
        alp.block(tx, 8, height, block_hash)
        inputs, outputs = tx.get(3, []), tx.get(4, [])
        runner.require(len(inputs) == len(INPUTS[op]) and len(outputs) == len(alp.COLORS[op][1]),
                       "plugin ALP cardinality drift")
        for raw, point in zip(inputs, INPUTS[op]):
            value = service.fields(raw)
            if point is None:
                runner.require(9 not in value, "coinbase unexpectedly carries plugin metadata")
            else:
                parent, out = point
                prevout = alp.message(value, 1)
                runner.require(alp.scalar(prevout, 1) == bytes.fromhex(ids[parent])[::-1] and
                               alp.scalar(prevout, 2, 0) == out, "plugin input ancestry drift")
                output_metadata(value, 9, parent, alp.COLORS[parent][1][out])
        for index, (raw, color) in enumerate(zip(outputs, alp.COLORS[op][1])):
            value = service.fields(raw)
            if index == 0:
                runner.require(6 not in value, "OP_RETURN unexpectedly has plugin metadata")
            else:
                output_metadata(value, 6, op, color)
    groups = service.fields(get("/plugin/ergon_public_probe/groups?page_size=10"))
    runner.require(groups == {1: [b"\x0a\x06public"]}, "combined plugin groups drift")
    for suffix, wanted in (("history", ids), ("confirmed-txs", ids if height >= 0 else []),
                           ("unconfirmed-txs", ids if height < 0 else [])):
        raw = get(plugin.PREFIX + suffix)
        page = service.fields(raw)
        runner.require(service.txids(raw) == sorted(wanted) and
                       alp.scalar(page, 3, 0) == len(wanted) and
                       alp.scalar(page, 2, 0) == int(bool(wanted)), "combined plugin history drift")
    wanted = {(ids[op], index): (op, alp.COLORS[op][1][index]) for op, index in UNSPENT}
    actual = set()
    for raw in service.fields(get(plugin.PREFIX + "utxos")).get(1, []):
        value = service.fields(raw)
        point = alp.message(value, 1)
        txid = alp.scalar(point, 1)
        runner.require(isinstance(txid, bytes) and len(txid) == 32, "plugin ALP UTXO txid")
        key = (txid[::-1].hex(), alp.scalar(point, 2, 0))
        runner.require(key in wanted and key not in actual, "unexpected or duplicate plugin ALP UTXO")
        op, color = wanted[key]
        runner.require(alp.scalar(value, 2, 0) == (height if height >= 0 else 2 ** 64 - 1) and
                       alp.scalar(value, 3, 0) == 0 and alp.scalar(value, 4, 0) == 0 and
                       alp.scalar(value, 5) == b"\x51" and alp.scalar(value, 6, 0) == 0,
                       "plugin ALP UTXO native metadata drift")
        if color is None:
            runner.require(7 not in value, "non-token output unexpectedly colored")
        else:
            alp.color(value, 7, ids[0], color, entry=2 ** 64 - 1)
        output_metadata(value, 8, op, color)
        actual.add(key)
    runner.require(actual == set(wanted), "missing plugin ALP UTXO")
    return runner.digest(runner.canonical(snapshots))


def snapshot(daemon, ids, wires, times, height, block_hash):
    # Both independent public HTTP oracles must pass for the same native graph.
    return runner.digest(runner.canonical({
        "ALP": alp.check_snapshot(daemon, ids, wires, times, height, block_hash),
        "plugin": check_plugin_snapshot(daemon, ids, height, block_hash)}))


def observe(binary, work, scripts):
    root = Path(tempfile.mkdtemp(prefix="ergon-upstream-", dir=work)).resolve()
    data = root / "data"
    data.mkdir()
    daemons = []
    def start(phase):
        daemon = service.Daemon(binary, root, data, phase, public_plugin=True)
        daemons.append(daemon)
        daemon.ready()
        return daemon
    def native_times(daemon, ids):
        values = [daemon.rpc("getmempoolentry", (txid,))["time"] for txid in ids]
        runner.require(all(type(t) is int and t > 0 for t in values), "missing combined native first-seen")
        return values
    try:
        daemon = start("plugin-alp-initial")
        hashes = daemon.rpc("generatetoaddress", (101, service.p2sh_true_address()))
        daemon.tip(101, hashes[-1])
        funding = daemon.rpc("getblock", (hashes[0], 2))["tx"][0]
        runner.require(all(o["value"] == 0 for o in funding["vout"]), "nonzero combined funding refused")
        wires, ids = [], []
        for op in range(4):
            inputs = ([(funding["txid"], 0, True)] if op == 0 else
                      [(ids[parent], out, False) for parent, out in INPUTS[op]])
            # Append only the existing fixture's eMPP LOKAD push. ALP operation
            # bytes/quantities come unchanged from the upstream template export.
            script = alp.bind_script(scripts, op, ids[0] if ids else "00" * 32) + b"\x04ERGN"
            wire = alp.transaction(inputs, [script, b"\x51"] +
                                   ([b"\x52" if op == 2 else b"\x51"] if op < 3 else []))
            wires.append(wire)
            ids.append(alp.submit(daemon, wire))
            daemon.pool(ids)
        times = native_times(daemon, ids)
        pending = snapshot(daemon, ids, wires, times, -1, None)
        index = data / "regtest/chronik-upstream-regtest"
        inputs = data / "regtest/chronik-upstream-regtest-plugin-inputs"
        identity = runner.regular(index / "ergon-identity.json")
        runner.require(b'"plugins":true' in identity, "combined plugin identity missing")
        original_inputs = tuple(runner.regular(inputs / p) for p in ("ergon_public_probe.py", "plugins.toml"))
        daemon.stop()
        daemon = start("plugin-alp-pending-reopen")
        daemon.tip(101, hashes[-1])
        daemon.pool(ids)
        runner.require(native_times(daemon, ids) == times and
                       snapshot(daemon, ids, wires, times, -1, None) == pending,
                       "combined pending process-reopen drift")
        confirmed = daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        daemon.tip(102, confirmed)
        daemon.pool([])
        snapshot(daemon, ids, wires, times, 102, confirmed)
        daemon.rpc("invalidateblock", (confirmed,))
        daemon.tip(101, hashes[-1])
        daemon.pool(ids)
        restored_times = native_times(daemon, ids)
        snapshot(daemon, ids, wires, restored_times, -1, None)
        daemon.rpc("reconsiderblock", (confirmed,))
        daemon.tip(102, confirmed)
        daemon.pool([])
        restored = snapshot(daemon, ids, wires, restored_times, 102, confirmed)
        daemon.stop()
        daemon = start("plugin-alp-confirmed-reopen")
        daemon.tip(102, confirmed)
        daemon.pool([])
        runner.require(snapshot(daemon, ids, wires, restored_times, 102, confirmed) == restored,
                       "combined confirmed process-reopen drift")
        runner.require(daemon.rpc("getconnectioncount") == 0 and
                       runner.regular(index / "ergon-identity.json") == identity and
                       tuple(runner.regular(inputs / p) for p in ("ergon_public_probe.py", "plugins.toml")) == original_inputs and
                       not (inputs / "__pycache__").exists() and not (index / "probe.failed").exists(),
                       "combined source/index identity or peers drift")
        runner.require(daemon.http("/ws")[0] == 501 and daemon.http("/broadcast-tx", "POST")[0] == 501,
                       "combined profile widened service capability")
        daemon.stop()
        return {"plugins": True, "genesis_mint_send_burn": True, "daemon_processes": 3,
            "pending_and_confirmed_process_reopen": True, "confirmation_disconnect_reconsider": True,
            "native_first_seen_wire_colors_spenders": True, "exact_plugin_inputs_outputs": True,
            "group_history_utxos": True, "token_utxos_atoms": 1400, "token_utxos_batons": 1,
            "intentional_burn_atoms": 100, "native_input_output_value_and_fee": 0,
            "synthetic_fee_priority_fixoshis_per_transaction": 1000000,
            "positive_joined_stops": 3, "external_connections": 0, "final_height": 102,
            "websocket_and_broadcast_disabled": True}
    finally:
        for daemon in daemons:
            daemon.cleanup()
        runner.remove_owned(root, work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "build-root", "work-parent", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    source, build, work = (p.resolve(strict=True) for p in (args.source_root, args.build_root, args.work_parent))
    runner.require(1 <= args.repetitions <= 10, "finite combined repetitions required")
    report = args.report.absolute()
    runner.require(report.parent.resolve(strict=True) == report.parent and not report.exists() and
                   not report.is_symlink(), "new canonical combined report required")
    runner.require(not any(report.is_relative_to(p) for p in (source, build, work)) and
                   not any(work.is_relative_to(p) or p.is_relative_to(work) for p in (source, build)),
                   "separate combined report and disposable parent required")
    sys.path.insert(0, str(source / "tools/engineering"))
    from check_chronik_plugin_alp import validate, input_binding
    validate(source)
    test_binding = input_binding(source)
    manifest = source / "docs/engineering/chronik/upstream-host-adaptation-v1.json"
    binding = runner.bind_build(source, build, "regtest-plugins", manifest)
    binary, native = build / "src/bitcoind", build / "src/test/test_bitcoin"
    hashes = tuple(runner.digest(runner.regular(p)) for p in (binary, native))
    blocked = (source, build, binary, native)
    scripts = alp.templates(runner.execute([str(native), "--run_test=" + alp.TEMPLATE_CASE,
        "--report_level=short", "--log_level=message", "--color_output=no"], work, blocked))
    result = None
    for _ in range(args.repetitions):
        current = observe(binary, work, scripts)
        runner.require(result is None or result == current, "combined lifecycle instability")
        result = current
    runner.require(hashes == tuple(runner.digest(runner.regular(p)) for p in (binary, native)) and
                   binding == runner.bind_build(source, build, "regtest-plugins", manifest) and
                   test_binding == input_binding(source), "combined source/build/binary drift")
    validate(source)
    payload = {"schema": "ergon-upstream-regtest-plugin-alp-observation/v1", "knowledge_status": "Observed",
        "proof_ceiling": "bounded_regtest_service_observation", "binding": binding,
        "test_inputs_sha256": test_binding, "binary_sha256": hashes[0], "native_test_binary_sha256": hashes[1],
        "template_case": alp.TEMPLATE_CASE, "template_assertions": alp.TEMPLATE_ASSERTIONS,
        "upstream_template_sha256": [runner.digest(raw) for raw in scripts],
        "repetitions": args.repetitions, "fresh_daemon_processes": 3 * args.repetitions,
        "result": result, "child_environment": dict(runner.ENV, TMPDIR="<runner-created-unique-directory>"),
        "disposable_roots_removed": True, "binary_to_source_provenance": "external-build-record-required"}
    raw = runner.canonical(payload)
    runner.require(not any(str(p).encode() in raw for p in (*blocked, work)), "private combined report binding")
    runner.write_report(report, payload)
    print("PASS: existing upstream ALP and public plugin on the same native daemon graph")


if __name__ == "__main__":
    main()

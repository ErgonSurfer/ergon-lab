# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Actual-daemon zero-value ALP observations using upstream-generated scripts.

This is a synthetic client/oracle, not another token engine or native rule.
It reuses the existing bounded service, native RPC and upstream HTTP router.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import tempfile
import regtest_service as service
import run_repetitions as runner

TEMPLATE_CASE = "chronik_upstream_regtest_tests/export_upstream_alp_templates"
TEMPLATE_ASSERTIONS = 4
PLACEHOLDER = bytes([0x42]) * 32
COLORS = (
    ([None], [None, (1000, False), (0, True)]),
    ([(0, True)], [None, (500, False), (0, True)]),
    ([(1000, False), (500, False)], [None, (1400, False), (100, False)]),
    ([(100, False)], [None, None]),
)
KINDS = (2, 4, 3, 5)


def scalar(value, field, default=None):
    found = value.get(field, [])
    runner.require(len(found) <= 1, "duplicate protobuf scalar")
    return found[0] if found else default


def message(value, field):
    raw = scalar(value, field)
    runner.require(isinstance(raw, bytes), "missing protobuf message")
    return service.fields(raw)


def alp_type(value):
    runner.require(message(value, 2) == {2: [0]}, "not upstream standard ALP")


def templates(streams):
    runner.parse_streams(streams, (TEMPLATE_CASE,), TEMPLATE_ASSERTIONS)
    rows = []
    for stream in streams:
        for line in stream.splitlines():
            if line.startswith("Ergon ALP template"):
                match = re.fullmatch(r"Ergon ALP template ([0-3]) ([0-9a-f]+)", line)
                runner.require(match is not None and len(match[2]) % 2 == 0,
                               "malformed upstream template")
                rows.append((int(match[1]), bytes.fromhex(match[2])))
    runner.require([op for op, _ in rows] == [0, 1, 2, 3], "ambiguous upstream templates")
    scripts = tuple(raw for _, raw in rows)
    for op, raw in enumerate(scripts):
        runner.require(0 < len(raw) <= 256 and raw.count(PLACEHOLDER) == (op != 0),
                       "template placeholder binding drift")
    return scripts


def bind_script(scripts, op, token):
    runner.require(0 <= op < 4 and re.fullmatch(r"[0-9a-f]{64}", token) is not None,
                   "invalid synthetic template binding")
    # ALP upstream serializes TokenId::txid().as_le_bytes(); only replace the
    # exact placeholder in output already constructed by that implementation.
    return scripts[op] if op == 0 else scripts[op].replace(PLACEHOLDER, bytes.fromhex(token)[::-1])


def compact_size(value):
    runner.require(0 <= value <= 65535, "synthetic wire length cap")
    return bytes([value]) if value < 253 else b"\xfd" + struct.pack("<H", value)


def transaction(inputs, scripts):
    # Native Bitcoin wire serialization only. All actual prevout values,
    # outputs and fees are zero; ALP quantities are not native currency.
    runner.require(1 <= len(inputs) <= 2 and 2 <= len(scripts) <= 3,
                   "synthetic transaction shape")
    wire = struct.pack("<I", 2) + compact_size(len(inputs))
    for txid, index, redeem in inputs:
        runner.require(re.fullmatch(r"[0-9a-f]{64}", txid) is not None and 0 <= index <= 2,
                       "synthetic prevout binding")
        sig = b"\x01\x51" if redeem else b""
        wire += bytes.fromhex(txid)[::-1] + struct.pack("<I", index) + compact_size(len(sig)) + sig + b"\xff" * 4
    wire += compact_size(len(scripts))
    for script in scripts:
        wire += bytes(8) + compact_size(len(script)) + script
    wire += bytes(4)
    runner.require(100 <= len(wire) <= 1024, "synthetic native size bounds")
    return wire.hex()


def submit(daemon, raw):
    txid = hashlib.sha256(hashlib.sha256(bytes.fromhex(raw)).digest()).digest()[::-1].hex()
    runner.require(daemon.rpc("prioritisetransaction", (txid, 0, 1000000)) is True,
                   "synthetic native priority refused")
    runner.require(daemon.rpc("sendrawtransaction", (raw,)) == txid, "native txid drift")
    return txid


def response(daemon, path):
    def ready():
        status, body = daemon.http(path)
        if status == 503:
            return None
        runner.require(status == 200, "ALP endpoint refused: " + path.split("/")[1])
        return (body,)  # Empty protobuf is a valid empty page, not not-ready.
    return service.wait_for(ready)[0]


def color(value, field, token, expected, entry=0):
    if expected is None:
        runner.require(field not in value, "unexpected token coloring")
        return
    actual = message(value, field)
    alp_type(actual)
    runner.require(scalar(actual, 1) == token.encode() and
                   scalar(actual, 3, 0) == entry and
                   scalar(actual, 4, 0) == expected[0] and
                   scalar(actual, 5, 0) == int(expected[1]), "ALP coloring drift")


def block(value, field, height, block_hash):
    if height < 0:
        runner.require(field not in value, "pending item falsely confirmed")
    else:
        actual = message(value, field)
        runner.require(scalar(actual, 1, 0) == height and
                       scalar(actual, 2) == bytes.fromhex(block_hash)[::-1] and
                       scalar(actual, 4, 0) == 0, "ALP block binding drift")


def check_tx(raw, txid, token, op, timestamp, height, block_hash, wire,
             expected_colors=None, invalid=False, actual_burn=None):
    value = service.fields(raw)
    runner.require(scalar(value, 1) == bytes.fromhex(txid)[::-1] and
                   scalar(value, 9, 0) == timestamp and
                   scalar(value, 11) == len(bytes.fromhex(wire)) and
                   scalar(value, 12, 0) == 0 and scalar(value, 16, 0) == 0 and
                   not value.get(14) and scalar(value, 15, 0) == (2 if invalid else 1),
                   "ALP transaction metadata drift")
    block(value, 8, height, block_hash)
    runner.require(len(value.get(13, [])) == 1, "ambiguous ALP entry")
    item = message(value, 13)
    alp_type(item)
    burn = (100 if op == 3 else 0) if actual_burn is None else actual_burn
    runner.require(scalar(item, 1) == token.encode() and scalar(item, 3) == KINDS[op] and
                   scalar(item, 5, 0) == int(invalid) and scalar(item, 8) == str(burn).encode() and
                   scalar(item, 9, 0) == (100 if op == 3 else 0) and
                   scalar(item, 10, 0) == 0, "ALP operation or burn drift")
    if not invalid:
        runner.require(not item.get(7), "unexpected failed coloring")
    inputs, outputs = COLORS[op] if expected_colors is None else expected_colors
    runner.require(len(value.get(3, [])) == len(inputs) and len(value.get(4, [])) == len(outputs),
                   "ALP input/output cardinality drift")
    for raw_input, expected in zip(value.get(3, []), inputs):
        inp = service.fields(raw_input)
        runner.require(scalar(inp, 4, 0) == 0, "nonzero native input")
        color(inp, 8, token, expected)
    for raw_output, expected in zip(value.get(4, []), outputs):
        out = service.fields(raw_output)
        runner.require(scalar(out, 1, 0) == 0, "nonzero native output")
        color(out, 5, token, expected)
    return value


def check_native_view(value, decoded):
    runner.require(scalar(value, 1) == bytes.fromhex(decoded["txid"])[::-1] and
                   scalar(value, 2, 0) == decoded["version"] and
                   scalar(value, 5, 0) == decoded["locktime"] and
                   len(value.get(3, [])) == len(decoded["vin"]) and
                   len(value.get(4, [])) == len(decoded["vout"]), "native decoded transaction shape drift")
    for raw, native in zip(value.get(3, []), decoded["vin"]):
        inp = service.fields(raw)
        point = message(inp, 1)
        runner.require(scalar(point, 1) == bytes.fromhex(native["txid"])[::-1] and
                       scalar(point, 2, 0) == native["vout"] and
                       scalar(inp, 2, b"") == bytes.fromhex(native["scriptSig"]["hex"]) and
                       scalar(inp, 5, 0) == native["sequence"], "native decoded input drift")
    for raw, native in zip(value.get(4, []), decoded["vout"]):
        out = service.fields(raw)
        runner.require(native["value"] == 0 and scalar(out, 1, 0) == 0 and
                       scalar(out, 2, b"") == bytes.fromhex(native["scriptPubKey"]["hex"]),
                       "native decoded output drift")


def check_snapshot(daemon, ids, wires, times, height, block_hash):
    token = ids[0]
    snapshots = {}
    def get(path):
        raw = response(daemon, path)
        snapshots[path] = runner.digest(raw)
        return raw
    txs = [check_tx(get("/tx/" + txid), txid, token, op, times[op], height, block_hash, wires[op])
           for op, txid in enumerate(ids)]
    for value, wire in zip(txs, wires):
        check_native_view(value, daemon.rpc("decoderawtransaction", (wire,)))
    for txid, wire in zip(ids, wires):
        runner.require(scalar(service.fields(get("/raw-tx/" + txid)), 1) == bytes.fromhex(wire),
                       "ALP raw/native wire mismatch")
    for op, out, spender, inp in ((0, 1, 2, 0), (0, 2, 1, 0), (1, 1, 2, 1), (2, 2, 3, 0)):
        spent = message(service.fields(txs[op][4][out]), 4)
        runner.require(scalar(spent, 1) == bytes.fromhex(ids[spender])[::-1] and
                       scalar(spent, 2, 0) == inp, "ALP spend ancestry drift")
    for op, out in ((1, 2), (2, 1), (3, 1)):
        runner.require(4 not in service.fields(txs[op][4][out]), "unspent ALP output has a spender")
    info = service.fields(get("/token/" + token))
    alp_type(info)
    block(info, 4, height, block_hash)
    genesis = message(info, 3)
    runner.require(scalar(info, 1) == token.encode() and scalar(info, 5, 0) == times[0] and
                   scalar(genesis, 1) == b"ELTEST" and
                   scalar(genesis, 2) == b"Ergon Lab synthetic fixture" and
                   scalar(genesis, 8, 0) == 0, "ALP genesis information drift")
    for prefix in ("/token-id/" + token, "/lokad-id/534c5032"):
        for suffix, wanted in (("/history", ids), ("/confirmed-txs", ids if height >= 0 else []),
                               ("/unconfirmed-txs", ids if height < 0 else [])):
            raw = get(prefix + suffix)
            page = service.fields(raw)
            runner.require(service.txids(raw) == sorted(wanted) and
                           scalar(page, 3, 0) == len(wanted) and
                           scalar(page, 2, 0) == int(bool(wanted)), "ALP history drift")
    raw_utxos = service.fields(get("/token-id/" + token + "/utxos")).get(1, [])
    expected = {(ids[2], 1): (1400, False), (ids[1], 2): (0, True)}
    actual = {}
    for raw in raw_utxos:
        value = service.fields(raw)
        point = message(value, 1)
        raw_id = scalar(point, 1)
        runner.require(isinstance(raw_id, bytes) and len(raw_id) == 32, "malformed token outpoint")
        key = (raw_id[::-1].hex(), scalar(point, 2, 0))
        runner.require(key in expected and key not in actual, "unexpected or duplicate token UTXO")
        color(value, 7, token, expected[key], entry=(2 ** 64 - 1))
        runner.require(scalar(value, 2, 0) == (height if height >= 0 else 2 ** 64 - 1) and
                       scalar(value, 3, 0) == 0 and scalar(value, 4, 0) == 0 and
                       scalar(value, 5) == b"\x51" and scalar(value, 6, 0) == 0,
                       "token UTXO native metadata drift")
        actual[key] = expected[key]
    runner.require(actual == expected, "missing token UTXO or mint baton")
    return runner.digest(runner.canonical(snapshots))


def check_malformed(raw, txid, timestamp, height, block_hash, wire):
    value = service.fields(raw)
    runner.require(scalar(value, 1) == bytes.fromhex(txid)[::-1] and
                   scalar(value, 9, 0) == timestamp and scalar(value, 11) == len(bytes.fromhex(wire)) and
                   scalar(value, 12, 0) == 0 and scalar(value, 16, 0) == 0 and
                   scalar(value, 15, 0) == 2 and len(value.get(14, [])) == 1 and
                   not value.get(13), "malformed metadata was not diagnosed")
    block(value, 8, height, block_hash)
    runner.require(scalar(message(value, 14), 3, b"") != b"", "missing upstream parsing diagnostic")
    runner.require(len(value.get(3, [])) == 1 and len(value.get(4, [])) == 2, "malformed fixture shape drift")
    for raw_input in value[3]:
        inp = service.fields(raw_input)
        runner.require(scalar(inp, 4, 0) == 0 and 8 not in inp, "malformed fixture input drift")
    for raw_output in value[4]:
        out = service.fields(raw_output)
        runner.require(scalar(out, 1, 0) == 0 and 5 not in out, "malformed fixture output drift")


def observe(binary, work, scripts):
    root = Path(tempfile.mkdtemp(prefix="ergon-upstream-", dir=work)).resolve()
    data = root / "data"
    data.mkdir()
    daemons = []
    def start(phase):
        daemon = service.Daemon(binary, root, data, phase)
        daemons.append(daemon)
        daemon.ready()
        return daemon
    def native_times(daemon, ids):
        values = [daemon.rpc("getmempoolentry", (txid,))["time"] for txid in ids]
        runner.require(all(type(t) is int and t > 0 for t in values), "missing native first-seen time")
        return values
    try:
        daemon = start("alp-initial")
        hashes = daemon.rpc("generatetoaddress", (101, service.p2sh_true_address()))
        daemon.tip(101, hashes[-1])
        funding = daemon.rpc("getblock", (hashes[0], 2))["tx"][0]
        runner.require(all(o["value"] == 0 for o in funding["vout"]), "nonzero ALP funding refused")
        wires, ids = [], []
        for op in range(4):
            inputs = ([(funding["txid"], 0, True)] if op == 0 else
                      [(ids[0], 2, False)] if op == 1 else
                      [(ids[0], 1, False), (ids[1], 1, False)] if op == 2 else
                      [(ids[2], 2, False)])
            script = bind_script(scripts, op, ids[0] if ids else "00" * 32)
            wire = transaction(inputs, [script, b"\x51"] + ([b"\x52" if op == 2 else b"\x51"] if op < 3 else []))
            wires.append(wire)
            ids.append(submit(daemon, wire))
            daemon.pool(ids)
        times = native_times(daemon, ids)
        pending = check_snapshot(daemon, ids, wires, times, -1, hashes[-1])
        index = data / "regtest/chronik-upstream-regtest"
        identity = runner.regular(index / "ergon-identity.json")
        daemon.stop()
        daemon = start("alp-reopen-pending")
        daemon.tip(101, hashes[-1])
        daemon.pool(ids)
        runner.require(native_times(daemon, ids) == times and
                       check_snapshot(daemon, ids, wires, times, -1, hashes[-1]) == pending,
                       "pending ALP reopen projection drift")
        confirmed = daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        daemon.tip(102, confirmed)
        daemon.pool([])
        check_snapshot(daemon, ids, wires, times, 102, confirmed)
        daemon.rpc("invalidateblock", (confirmed,))
        daemon.tip(101, hashes[-1])
        daemon.pool(ids)
        restored_times = native_times(daemon, ids)
        check_snapshot(daemon, ids, wires, restored_times, -1, hashes[-1])
        daemon.rpc("reconsiderblock", (confirmed,))
        daemon.tip(102, confirmed)
        daemon.pool([])
        restored = check_snapshot(daemon, ids, wires, restored_times, 102, confirmed)
        daemon.stop()
        daemon = start("alp-reopen-confirmed")
        daemon.tip(102, confirmed)
        daemon.pool([])
        runner.require(check_snapshot(daemon, ids, wires, restored_times, 102, confirmed) == restored,
                       "confirmed ALP reopen projection drift")
        # Accepted by the legacy node, invalid only for the upstream token
        # interpretation: mint from atoms without the required mint baton.
        invalid_wire = transaction([(ids[2], 1, False)],
            [bind_script(scripts, 1, ids[0]), b"\x51", b"\x51"])
        invalid = submit(daemon, invalid_wire)
        # Deliberately truncated upstream builder output: a parsing diagnostic,
        # never a second hand-authored ALP parser or consensus rule.
        malformed_wire = transaction([(ids[3], 1, False)], [scripts[0][:-1], b"\x51"])
        malformed = submit(daemon, malformed_wire)
        daemon.pool([invalid, malformed])
        invalid_time = native_times(daemon, [invalid])[0]
        check_tx(response(daemon, "/tx/" + invalid), invalid, ids[0], 1, invalid_time,
                 -1, confirmed, invalid_wire, ([(1400, False)], [None, None, None]), True, 1400)
        for txid, wire in ((invalid, invalid_wire), (malformed, malformed_wire)):
            check_native_view(service.fields(response(daemon, "/tx/" + txid)),
                              daemon.rpc("decoderawtransaction", (wire,)))
            runner.require(scalar(service.fields(response(daemon, "/raw-tx/" + txid)), 1) == bytes.fromhex(wire),
                           "negative ALP raw/native wire mismatch")
        malformed_time = native_times(daemon, [malformed])[0]
        check_malformed(response(daemon, "/tx/" + malformed), malformed, malformed_time,
                        -1, confirmed, malformed_wire)
        next_hash = daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        daemon.tip(103, next_hash)
        daemon.pool([])
        check_tx(response(daemon, "/tx/" + invalid), invalid, ids[0], 1, invalid_time,
                 103, next_hash, invalid_wire, ([(1400, False)], [None, None, None]), True, 1400)
        check_malformed(response(daemon, "/tx/" + malformed), malformed, malformed_time,
                        103, next_hash, malformed_wire)
        runner.require(daemon.rpc("getconnectioncount") == 0 and
                       runner.regular(index / "ergon-identity.json") == identity and
                       not (index / "probe.failed").exists(), "ALP diagnostic affected node or index authority")
        runner.require(daemon.http("/ws")[0] == 501 and
                       daemon.http("/broadcast-tx", "POST")[0] == 501, "unexpected ALP service capability")
        daemon.stop()
        return {"genesis_mint_send_burn": True, "pending_reopen": True,
            "confirmed_reopen": True, "confirmation_disconnect_reconsider": True,
            "exact_input_output_colors_and_spenders": True, "token_utxos_atoms": 1400,
            "token_utxos_batons": 1, "intentional_burn_atoms": 100,
            "native_first_seen_bound": True, "upstream_history_and_raw_parity": True,
            "invalid_mint_native_accepted_index_diagnosed": True,
            "malformed_metadata_native_accepted_index_diagnosed": True,
            "native_mining_continues": True, "actual_input_output_value_and_fee": 0,
            "synthetic_fee_priority_fixoshis_per_transaction": 1000000,
            "plugins": False, "external_connections": 0, "final_height": 103,
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
    report = args.report.absolute()
    runner.require(report.parent.resolve(strict=True) == report.parent and not report.exists() and not report.is_symlink(), "new canonical report required")
    runner.require(not any(report.is_relative_to(p) for p in (source, build, work)), "separate ALP report required")
    runner.require(not any(work.is_relative_to(p) or p.is_relative_to(work) for p in (source, build)), "separate disposable parent required")
    runner.require(1 <= args.repetitions <= 10, "ALP repetitions outside bounded range")
    sys.path.insert(0, str(source / "tools/engineering"))
    from check_chronik_continuous_service import validate
    validate(source)
    manifest = source / "docs/engineering/chronik/upstream-host-adaptation-v1.json"
    binding = runner.bind_build(source, build, "regtest", manifest)
    binary, native_test = build / "src/bitcoind", build / "src/test/test_bitcoin"
    hashes = tuple(runner.digest(runner.regular(p)) for p in (binary, native_test))
    streams = runner.execute([str(native_test), "--run_test=" + TEMPLATE_CASE,
        "--report_level=short", "--log_level=message", "--color_output=no"],
        work, (source, build, binary, native_test))
    scripts = templates(streams)
    result = None
    for _ in range(args.repetitions):
        current = observe(binary, work, scripts)
        runner.require(result is None or result == current, "ALP lifecycle instability")
        result = current
    runner.require(hashes == tuple(runner.digest(runner.regular(p)) for p in (binary, native_test)) and
                   binding == runner.bind_build(source, build, "regtest", manifest), "ALP input drift")
    validate(source)
    payload = {"schema": "ergon-upstream-regtest-alp-observation/v1", "knowledge_status": "Observed",
        "proof_ceiling": "bounded_regtest_service_observation", "binding": binding,
        "binary_sha256": hashes[0], "native_test_binary_sha256": hashes[1],
        "template_case": TEMPLATE_CASE, "template_assertions": TEMPLATE_ASSERTIONS,
        "upstream_template_sha256": [runner.digest(raw) for raw in scripts],
        "repetitions": args.repetitions, "fresh_daemon_processes": 3 * args.repetitions,
        "result": result, "child_environment": dict(runner.ENV, TMPDIR="<runner-created-unique-directory>"),
        "disposable_roots_removed": True, "binary_to_source_provenance": "external-build-record-required"}
    raw = runner.canonical(payload)
    runner.require(not any(str(p).encode() in raw for p in (source, build, work, binary, native_test)), "private ALP report binding")
    runner.write_report(report, payload)
    print("PASS: bounded real-daemon upstream ALP lifecycle; no consensus or production-readiness claim")


if __name__ == "__main__":
    main()

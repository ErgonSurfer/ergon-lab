# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers
"""Public synthetic plugin through the real bounded regtest daemon service.

Reuses upstream-generated ALP scripts, the existing plugin engine and HTTP
router. This client is an observation oracle, not a token implementation.
"""
from pathlib import Path
import argparse
import sys
import tempfile
import alp_service as alp
import regtest_service as service
import run_repetitions as runner

NAME = b"ergon_public_probe"
GROUP = b"public"
PREFIX = "/plugin/ergon_public_probe/7075626c6963/"
OWNER_CASE = "chronik_upstream_regtest_tests/asynchronous_owner_loads_public_plugin_and_joins"
OWNER_ASSERTIONS = 12


def plugin_entry(value, field):
    entries = value.get(field, [])
    runner.require(len(entries) == 1, "missing or ambiguous public plugin")
    entry = service.fields(entries[0])
    runner.require(entry.get(1) == [NAME] and len(entry.get(2, [])) == 1,
                   "wrong public plugin name or entry")
    return service.fields(entry[2][0])


def plugin_output(value, field, parents, atoms):
    runner.require(plugin_entry(value, field) == {
        1: [GROUP], 2: [b"synthetic-v1", b"0", str(parents).encode(), str(atoms).encode()]},
        "public plugin metadata drift")


def check(daemon, parent, child, phase, height, block_hash):
    for txid, parents, atoms in ((parent, 0, 1000), (child, 1, 0)):
        raw = alp.response(daemon, "/tx/" + txid)
        tx = service.fields(raw)
        runner.require(tx.get(1) == [bytes.fromhex(txid)[::-1]], "plugin transaction identity")
        alp.block(tx, 8, height, block_hash)
        outputs = tx.get(4, [])
        runner.require(len(outputs) == (3 if txid == parent else 2), "plugin output count")
        plugin_output(service.fields(outputs[1]), 6, parents, atoms)
        if txid == child:
            inputs = tx.get(3, [])
            runner.require(len(inputs) == 1, "plugin input count")
            plugin_output(service.fields(inputs[0]), 9, 0, 1000)
    groups = service.fields(alp.response(daemon, "/plugin/ergon_public_probe/groups?page_size=10"))
    runner.require(groups == {1: [b"\x0a\x06public"]}, "plugin groups drift")
    expected = sorted([parent, child])
    runner.require(service.txids(alp.response(daemon, PREFIX + "history")) == expected,
                   "plugin history drift")
    runner.require(service.txids(alp.response(daemon, PREFIX + phase)) == expected,
                   "plugin confirmation page drift")
    other = "confirmed-txs" if phase == "unconfirmed-txs" else "unconfirmed-txs"
    runner.require(service.txids(alp.response(daemon, PREFIX + other)) == [],
                   "plugin wrong confirmation page")
    utxos = service.fields(alp.response(daemon, PREFIX + "utxos")).get(1, [])
    actual = []
    for raw in utxos:
        utxo = service.fields(raw)
        runner.require(alp.scalar(utxo, 2, 0) == (height if height >= 0 else 2 ** 64 - 1) and
                       alp.scalar(utxo, 3, 0) == 0 and alp.scalar(utxo, 4, 0) == 0 and
                       alp.scalar(utxo, 5) == b"\x51", "plugin UTXO height/value/script drift")
        point = alp.message(utxo, 1)
        txid = alp.scalar(point, 1)
        index = alp.scalar(point, 2, 0)
        runner.require(isinstance(txid, bytes) and len(txid) == 32, "plugin UTXO txid")
        pair = (txid[::-1].hex(), index)
        runner.require(pair in ((parent, 2), (child, 1)), "unexpected plugin UTXO")
        plugin_output(utxo, 8, 0 if pair[0] == parent else 1, 0)
        actual.append(pair)
    runner.require(sorted(actual) == sorted([(parent, 2), (child, 1)]), "plugin UTXO set drift")


def observe(binary, work, scripts):
    root = Path(tempfile.mkdtemp(prefix="ergon-upstream-", dir=work)).resolve()
    data = root / "data"
    data.mkdir()
    daemons = []
    def start(phase, plugin=True, available=True):
        daemon = service.Daemon(binary, root, data, phase, public_plugin=plugin)
        daemons.append(daemon)
        daemon.ready(available)
        return daemon
    try:
        # Same plugin-capable executable, runtime plugin opt-in absent.
        daemon = start("plugin-off", plugin=False)
        initial = daemon.rpc("getblockchaininfo")
        daemon.tip(0, initial["bestblockhash"])
        index = data / "regtest/chronik-upstream-regtest"
        runner.require(b'"plugins":false' in runner.regular(index / "ergon-identity.json"),
                       "disabled plugin index identity")
        runner.require(not (data / "regtest/chronik-upstream-regtest-plugin-inputs").exists(),
                       "disabled profile created plugin state")
        daemon.stop()
        plain_identity = runner.regular(index / "ergon-identity.json")
        daemon = start("refuse-non-plugin-index", available=False)
        previous_height = daemon.rpc("getblockcount")
        runner.require(len(daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))) == 1 and
                       daemon.rpc("getblockcount") == previous_height + 1,
                       "index identity refusal vetoed native mining")
        daemon.stop()
        runner.require(runner.regular(index / "ergon-identity.json") == plain_identity,
                       "non-plugin index identity was silently rewritten")
        # Never adopt/rewrite the non-plugin index. Use a separate fresh datadir.
        data = root / "plugin-data"
        data.mkdir()
        daemon = start("initial")
        runner.require(daemon.http("/ws")[0] == 501 and
                       daemon.http("/broadcast-tx", "POST")[0] == 501,
                       "plugin profile widened service capabilities")
        hashes = daemon.rpc("generatetoaddress", (101, service.p2sh_true_address()))
        daemon.tip(101, hashes[-1])
        funding = daemon.rpc("getblock", (hashes[0], 2))["tx"][0]
        runner.require(all(o["value"] == 0 for o in funding["vout"]), "nonzero plugin funding")
        # Append a synthetic eMPP LOKAD push to the existing upstream GENESIS
        # output. Do not author or change any ALP quantity/parser/validator.
        parent_raw = alp.transaction([(funding["txid"], 0, True)],
            [scripts[0] + b"\x04ERGN", b"\x51", b"\x51"])
        parent = alp.submit(daemon, parent_raw)
        child_raw = alp.transaction([(parent, 1, False)],
            [b"\x6a\x04ERGN\x4c\x40" + bytes(64), b"\x51"])
        child = alp.submit(daemon, child_raw)
        daemon.pool([parent, child])
        check(daemon, parent, child, "unconfirmed-txs", -1, None)
        index = data / "regtest/chronik-upstream-regtest"
        inputs = data / "regtest/chronik-upstream-regtest-plugin-inputs"
        identity = runner.regular(index / "ergon-identity.json")
        runner.require(b'"plugins":true' in identity, "unbound public plugin index")
        source = runner.regular(inputs / "ergon_public_probe.py")
        config = runner.regular(inputs / "plugins.toml")
        daemon.stop()
        daemon = start("pending-reopen")
        daemon.tip(101, hashes[-1])
        daemon.pool([parent, child])
        check(daemon, parent, child, "unconfirmed-txs", -1, None)
        confirmed = daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        daemon.tip(102, confirmed)
        daemon.pool([])
        check(daemon, parent, child, "confirmed-txs", 102, confirmed)
        daemon.rpc("invalidateblock", (confirmed,))
        daemon.tip(101, hashes[-1])
        daemon.pool([parent, child])
        check(daemon, parent, child, "unconfirmed-txs", -1, None)
        daemon.rpc("reconsiderblock", (confirmed,))
        daemon.tip(102, confirmed)
        daemon.pool([])
        check(daemon, parent, child, "confirmed-txs", 102, confirmed)
        daemon.stop()
        daemon = start("confirmed-reopen")
        daemon.tip(102, confirmed)
        check(daemon, parent, child, "confirmed-txs", 102, confirmed)
        runner.require(daemon.rpc("getconnectioncount") == 0, "plugin fixture has peers")
        daemon.stop()
        runner.require(runner.regular(index / "ergon-identity.json") == identity and
                       runner.regular(inputs / "ergon_public_probe.py") == source and
                       runner.regular(inputs / "plugins.toml") == config and
                       not (inputs / "__pycache__").exists() and
                       not (index / "probe.failed").exists(), "healthy plugin inputs or index drift")
        # Deliberately corrupt only the disposable test input. No rewrite or
        # repair is permitted on refusal. Native mining must continue.
        drift = config + b"\n# synthetic drift\n"
        (inputs / "plugins.toml").write_bytes(drift)
        daemon = start("refuse-drift", available=False)
        previous_height = daemon.rpc("getblockcount")
        runner.require(len(daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))) == 1 and
                       daemon.rpc("getblockcount") == previous_height + 1,
                       "plugin refusal vetoed native mining")
        daemon.stop()
        runner.require(runner.regular(inputs / "plugins.toml") == drift and
                       runner.regular(index / "ergon-identity.json") == identity,
                       "incompatible plugin inputs silently adopted")
        return {"network": "regtest", "public_plugin": True, "daemon_processes": 6,
            "runtime_plugin_off_no_inputs": True, "pending_parent_child_process_reopen": True,
            "upstream_alp_atoms_in_plugin_output": 1000, "native_input_output_value_and_fee": 0,
            "group_history_utxos": True, "confirmation_disconnect_reconsider": True,
            "confirmed_process_reopen": True, "positive_joined_stops": 4,
            "unavailable_service_native_stops": 2,
            "non_plugin_index_refused_without_rewrite": True,
            "websocket_and_broadcast_disabled": True,
            "config_drift_refused_without_rewrite": True, "native_mining_after_refusal": True,
            "external_connections": 0}
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
    runner.require(1 <= args.repetitions <= 10, "finite plugin service repetitions required")
    report = args.report.absolute()
    runner.require(report.parent.resolve(strict=True) == report.parent and not report.exists() and
                   not report.is_symlink(), "new canonical plugin service report required")
    runner.require(not any(report.is_relative_to(p) for p in (source, build, work)), "separate report required")
    runner.require(not any(work.is_relative_to(p) or p.is_relative_to(work) for p in (source, build)),
                   "separate disposable parent required")
    sys.path.insert(0, str(source / "tools/engineering"))
    from check_chronik_service_plugins import validate
    validate(source)
    manifest = source / "docs/engineering/chronik/upstream-host-adaptation-v1.json"
    binding = runner.bind_build(source, build, "regtest-plugins", manifest)
    binary, native = build / "src/bitcoind", build / "src/test/test_bitcoin"
    hashes = tuple(runner.digest(runner.regular(p)) for p in (binary, native))
    blocked = (source, build, binary, native)
    templates = alp.templates(runner.execute([str(native), "--run_test=" + alp.TEMPLATE_CASE,
        "--report_level=short", "--log_level=message", "--color_output=no"], work, blocked))
    result = None
    owner_signature = None
    for _ in range(args.repetitions):
        streams = runner.execute([str(native), "--run_test=" + OWNER_CASE,
            "--report_level=short", "--log_level=message", "--color_output=no"], work, blocked)
        runner.parse_streams(streams, (OWNER_CASE,), OWNER_ASSERTIONS)
        runner.require(owner_signature is None or streams == owner_signature,
                       "native plugin owner output instability")
        owner_signature = streams
        current = observe(binary, work, templates)
        runner.require(result is None or result == current, "plugin service lifecycle instability")
        result = current
    runner.require(hashes == tuple(runner.digest(runner.regular(p)) for p in (binary, native)) and
                   binding == runner.bind_build(source, build, "regtest-plugins", manifest), "plugin service input drift")
    validate(source)
    payload = {"schema": "ergon-upstream-regtest-plugin-observation/v1", "knowledge_status": "Observed",
        "proof_ceiling": "bounded_regtest_service_observation", "binding": binding,
        "binary_sha256": hashes[0], "native_test_binary_sha256": hashes[1],
        "owner_case": OWNER_CASE, "owner_assertions": OWNER_ASSERTIONS,
        "owner_output_comparisons": args.repetitions - 1,
        "repetitions": args.repetitions, "fresh_daemon_processes": 6 * args.repetitions,
        "result": result, "child_environment": dict(runner.ENV, TMPDIR="<runner-created-unique-directory>"),
        "disposable_roots_removed": True, "binary_to_source_provenance": "external-build-record-required"}
    raw = runner.canonical(payload)
    runner.require(not any(str(p).encode() in raw for p in (*blocked, work)), "private plugin service report binding")
    runner.write_report(report, payload)
    print("PASS: bounded actual-daemon public synthetic plugin; no production or sandbox claim")


if __name__ == "__main__":
    main()

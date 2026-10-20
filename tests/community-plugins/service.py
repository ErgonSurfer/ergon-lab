# SPDX-License-Identifier: MIT
"""Four reviewed plugins through native regtest and the unchanged upstream DB.

Synthetic zero-value transactions only. No operator configuration, mainnet
submission, independent token implementation or deployment qualification.
"""
import argparse
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compatibility/upstream-chronik"))
import alp_service as alp
import operator_bundle_binding as bundle_binding
import regtest_service as service
import run_repetitions as runner
import component

MODULES = ("moom_alp_token_index", "moom_nft_index", "moom_alp_exch_market", "moom_reward_claim_index")
REGISTRY, NFT, MARKET, CLAIMS = (name.encode() for name in MODULES)
PLACEHOLDER = bytes([0x42]) * 32
CLAIM_ENVELOPE = b"MCLM\0\x01" + bytes(44) + b"\x01" + bytes(17)


class Daemon(service.Daemon):
    """Use the inherited lifecycle/HTTP/RPC checks, with explicit bundle args."""
    def __init__(self, binary, root, data, phase, bundle, binding):
        self.data, self.root, self.public_plugin = data, root, 4
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.rpc_port = reservation.getsockname()[1]
        self.log = root / (phase + ".log")
        self.output = self.log.open("xb")
        temporary = root / (phase + "-environment")
        temporary.mkdir()
        self.port = None
        try:
            self.proc = subprocess.Popen([str(binary), "-regtest", "-server=1", "-listen=0",
                "-connect=0", "-dnsseed=0", "-discover=0", "-upnp=0", "-printtoconsole=1",
                "-acceptnonstdtxn=1", "-blockmintxfee=0", "-datadir=" + str(data),
                "-rpcbind=127.0.0.1", "-rpcallowip=127.0.0.1", "-rpcport=" + str(self.rpc_port),
                "-chronikupstreamregtest=1", "-chronikupstreamregtestpluginbundle=" + str(bundle),
                "-chronikupstreamregtestpluginbinding=" + binding], cwd=root,
                env=runner.environment(temporary), stdout=self.output,
                stderr=subprocess.STDOUT, start_new_session=True)
        except BaseException:
            self.output.close()
            raise


def pushed(parts):
    """Native carrier push encoding only; token scripts come from upstream."""
    runner.require(all(isinstance(part, bytes) and len(part) <= 255 for part in parts),
                   "synthetic carrier push bound")
    return b"\x6a" + b"".join((bytes([len(part)]) if len(part) < 76 else b"\x4c" + bytes([len(part)]))
                              + part for part in parts)


def send_template(scripts, family, token):
    runner.require(family in ("ALP", "SLP") and re.fullmatch(r"[0-9a-f]{64}", token) is not None,
                   "synthetic upstream token binding")
    script = scripts[family + "_SEND"]
    runner.require(script.count(PLACEHOLDER) == 1, "upstream send placeholder drift")
    # The actual public builders use ALP LE and SLP BE token IDs respectively.
    value = bytes.fromhex(token)
    return script.replace(PLACEHOLDER, value[::-1] if family == "ALP" else value)


def plugin_map(value, field):
    result = {}
    for raw in value.get(field, []):
        entry = service.fields(raw)
        runner.require(set(entry) == {1, 2} and len(entry[1]) == len(entry[2]) == 1,
                       "ambiguous plugin map entry")
        name = entry[1][0]
        runner.require(name in (REGISTRY, NFT, MARKET, CLAIMS) and name not in result,
                       "unknown or duplicate plugin map name")
        body = service.fields(entry[2][0])
        runner.require(set(body) <= {1, 2}, "unknown plugin entry field")
        result[name] = body
    return result


def expected(ids, sell_fields):
    info = [b"ticker=TEST", b"name=Synthetic community token", b"url="]
    output = {}
    for family in ("ALP", "SLP"):
        token = bytes.fromhex(ids[family + "_GENESIS"])
        for genesis in (True, False):
            txid = ids[family + ("_GENESIS" if genesis else "_SEND")]
            nft = {1: [b"NFT" + token, b"NFTO" + token + b"\x51"],
                   2: [b"protocol=" + family.encode(), b"type=" + (b"0" if family == "ALP" else b"129"),
                       b"profile_asset=0"] + (info if genesis else [])}
            plugins = {NFT: nft}
            if family == "ALP":
                plugins[REGISTRY] = {1: [b"ALPT" + token, b"ALPH" + token + b"\x51"] +
                    ([b"ALPG" + token] if genesis else []),
                    2: [b"atoms=1", b"type=0"] + (info + [b"decimals=0"] if genesis else [])}
            output[txid] = (1, plugins)
    output[ids["SELL"]] = (1, {MARKET: {1: [b"EXCH\x01"], 2: [b"|".join(sell_fields)]}})
    output[ids["CLAIM"]] = (0, {CLAIMS: {1: [b"MCLM\0"], 2: [b"version=0", b"kind=1"]}})
    return output


def groups_page(daemon, name, query):
    value = service.fields(alp.response(daemon, "/plugin/" + name.decode() + "/groups?" + query))
    runner.require(set(value) <= {1, 2} and len(value.get(2, [])) <= 1,
                   "plugin group envelope drift")
    groups = []
    for raw in value.get(1, []):
        entry = service.fields(raw)
        runner.require(set(entry) == {1} and len(entry[1]) == 1, "ambiguous group")
        groups.append(entry[1][0])
    runner.require(groups == sorted(set(groups)), "duplicate or unordered groups")
    cursor = alp.scalar(value, 2, b"")
    runner.require(isinstance(cursor, bytes) and len(cursor) <= 512, "plugin cursor type/bound drift")
    return groups, cursor


def check(daemon, ids, projection, height, block_hash):
    """Exact maps, spent inputs, derived groups/history/UTXOs; no map sorting scrub."""
    for txid, (index, plugins) in projection.items():
        tx = service.fields(alp.response(daemon, "/tx/" + txid))
        runner.require(tx.get(1) == [bytes.fromhex(txid)[::-1]] and len(tx.get(4, [])) == 2,
                       "community transaction identity/shape")
        alp.block(tx, 8, height, block_hash)
        for n, raw in enumerate(tx[4]):
            runner.require(plugin_map(service.fields(raw), 6) == (plugins if n == index else {}),
                           "community output projection drift")
        inputs = tx.get(3, [])
        runner.require(len(inputs) == 1, "community input count")
        parent = next((ids[family + "_GENESIS"] for family in ("ALP", "SLP")
                       if txid == ids[family + "_SEND"]), None)
        runner.require(plugin_map(service.fields(inputs[0]), 9) == (projection[parent][1] if parent else {}),
                       "community spent-input metadata drift")
    live = {}
    for role in ("ALP_SEND", "SLP_SEND", "SELL", "CLAIM"):
        txid = ids[role]
        index, plugins = projection[txid]
        for name, entry in plugins.items():
            for group in entry[1]:
                live.setdefault(name, {})[group] = (txid, index, entry)
    for name, members in live.items():
        wanted = sorted(members)
        actual, cursor = groups_page(daemon, name, "page_size=50")
        runner.require(actual == wanted and cursor == b"", "community live group set drift")
        first, cursor = groups_page(daemon, name, "prefix=&page_size=1")
        runner.require(first == wanted[:1] and bool(cursor) == (len(wanted) > 1), "group first page drift")
        if cursor:
            rest, final = groups_page(daemon, name, "prefix=&start=" + cursor.hex() + "&page_size=50")
            runner.require(first + rest == wanted and final == b"", "group cursor continuity drift")
        prefix = wanted[0][:4]
        filtered, tail = groups_page(daemon, name, "prefix=" + prefix.hex() + "&page_size=50")
        runner.require(filtered == [g for g in wanted if g.startswith(prefix)] and tail == b"",
                       "group prefix drift")
        for group, (txid, index, entry) in members.items():
            route = "/plugin/" + name.decode() + "/" + group.hex() + "/"
            related = sorted(t for t, (_, outputs) in projection.items()
                             if name in outputs and group in outputs[name][1])
            for endpoint in ("history", "confirmed-txs"):
                runner.require(service.txids(alp.response(daemon, route + endpoint)) == related,
                               "community confirmed history drift")
            runner.require(service.txids(alp.response(daemon, route + "unconfirmed-txs")) == [],
                           "confirmed community transaction remains pending")
            items = service.fields(alp.response(daemon, route + "utxos")).get(1, [])
            runner.require(len(items) == 1, "community derived UTXO count")
            item = service.fields(items[0]); point = alp.message(item, 1)
            runner.require(alp.scalar(point, 1) == bytes.fromhex(txid)[::-1] and
                           alp.scalar(point, 2, 0) == index and alp.scalar(item, 2, 0) == height and
                           alp.scalar(item, 3, 0) == alp.scalar(item, 4, 0) == 0 and
                           alp.scalar(item, 5) == (pushed([b"MCLM", CLAIM_ENVELOPE]) if txid == ids["CLAIM"] else b"\x51") and
                           plugin_map(item, 8) == projection[txid][1], "community derived UTXO binding drift")
    for path, status in (("/plugin/not_loaded/groups", 501),
                         ("/plugin/moom_nft_index/AA/history", 501),
                         ("/plugin/moom_nft_index/groups?page_size=51", 400),
                         ("/plugin/moom_nft_index/groups?prefix=00&prefix=00", 400),
                         ("/plugin/moom_nft_index/groups?unknown=1", 400)):
        runner.require(daemon.http(path)[0] == status, "community route/query refusal drift")


def fixture_bundle(source, root, python):
    bundle = root / "reviewed-public-bundle"; bundle.mkdir(mode=0o700)
    for name in MODULES:
        path = bundle / (name + ".py")
        path.write_bytes(runner.regular(source / "plugins" / path.name)); path.chmod(0o600)
    config = bundle / "plugins.toml"
    config.write_bytes(runner.regular(source / "tests/community-plugins/plugins.toml")); config.chmod(0o600)
    # This test computes only a reviewed, exact public synthetic fixture's
    # commitment. It is never approval of arbitrary operator inputs.
    binding = bundle_binding.inspect(bundle, python["version"], python["embedding_sha256"])
    return bundle, binding


def observe(source, binary, work, scripts, python):
    root = Path(tempfile.mkdtemp(prefix="ergon-upstream-", dir=work)).resolve()
    daemons = []
    data = root / "data"; data.mkdir()
    try:
        bundle, binding = fixture_bundle(source, root, python)
        def start(phase, expected_binding=binding, available=True):
            daemon = Daemon(binary, root, data, phase, bundle, expected_binding)
            daemons.append(daemon); daemon.ready(available)
            return daemon
        daemon = start("initial")
        runner.require(daemon.http("/ws")[0] == daemon.http("/broadcast-tx", "POST")[0] == 501,
                       "community fixture widened service capabilities")
        hashes = daemon.rpc("generatetoaddress", (105, service.p2sh_true_address()))
        daemon.tip(105, hashes[-1])
        funding = [daemon.rpc("getblock", (h, 2))["tx"][0] for h in hashes[:4]]
        runner.require(all(all(v["value"] == 0 for v in tx["vout"]) for tx in funding), "nonzero funding")
        ids = {}
        for n, family in enumerate(("ALP", "SLP")):
            genesis = alp.transaction([(funding[n]["txid"], 0, True)], [scripts[family + "_GENESIS"], b"\x51"])
            ids[family + "_GENESIS"] = alp.submit(daemon, genesis)
            send = alp.transaction([(ids[family + "_GENESIS"], 1, False)],
                                   [send_template(scripts, family, ids[family + "_GENESIS"]), b"\x51"])
            ids[family + "_SEND"] = alp.submit(daemon, send)
        fields = [b"|" * 32, bytes(4), (1).to_bytes(8, "little"), (2).to_bytes(8, "little"), b"a" * 20, b"b" * 20]
        ids["SELL"] = alp.submit(daemon, alp.transaction([(funding[2]["txid"], 0, True)],
            [pushed([b"EXCH", b"\x01", b"SELL", *fields]), b"\x51"]))
        runner.require(len(CLAIM_ENVELOPE) == 68, "synthetic claim envelope drift")
        ids["CLAIM"] = alp.submit(daemon, alp.transaction([(funding[3]["txid"], 0, True)],
            [pushed([b"MCLM", CLAIM_ENVELOPE]), b"\x51"]))
        daemon.pool(list(ids.values()))
        confirmed = daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))[0]
        daemon.tip(106, confirmed); daemon.pool([])
        projection = expected(ids, fields)
        check(daemon, ids, projection, 106, confirmed)
        runner.require(daemon.rpc("getconnectioncount") == 0, "community fixture has peers")
        index = data / "regtest/chronik-upstream-regtest"
        identity = runner.regular(index / "ergon-identity.json")
        daemon.stop()
        daemon = start("confirmed-reopen"); daemon.tip(106, confirmed)
        check(daemon, ids, projection, 106, confirmed); daemon.stop()
        # Retain the stopped original index intact, then let upstream reconstruct
        # a new index from accepted native history. No adoption or deletion.
        retained = root / "retained-first-index"
        original_inode = index.stat().st_ino
        index.rename(retained)
        daemon = start("independent-index"); daemon.tip(106, confirmed)
        runner.require(index.stat().st_ino != original_inode, "new index was not independent")
        check(daemon, ids, projection, 106, confirmed); daemon.stop()
        runner.require(runner.regular(index / "ergon-identity.json") == identity and
                       runner.regular(retained / "ergon-identity.json") == identity, "index identity drift")
        config = bundle / "plugins.toml"; original_config = runner.regular(config)
        config.write_bytes(original_config + b"\n# synthetic drift\n")
        daemon = start("unapproved-drift", available=False)
        before = daemon.rpc("getblockcount")
        runner.require(len(daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))) == 1 and
                       daemon.rpc("getblockcount") == before + 1, "bundle refusal vetoed native mining")
        daemon.stop()
        runner.require(runner.regular(config) == original_config + b"\n# synthetic drift\n" and
                       runner.regular(index / "ergon-identity.json") == identity, "drift silently repaired/adopted")
        config.write_bytes(original_config)
        nft = bundle / "moom_nft_index.py"; old_source = runner.regular(nft)
        runner.require(old_source.count(b'return "0.2.0"') == 1, "synthetic version preimage drift")
        nft.write_bytes(old_source.replace(b'return "0.2.0"', b'return "0.3.0"'))
        changed = bundle_binding.inspect(bundle, python["version"], python["embedding_sha256"])
        runner.require(changed != binding, "version did not change bundle identity")
        daemon = start("incompatible-existing-index", changed, available=False)
        before = daemon.rpc("getblockcount")
        runner.require(len(daemon.rpc("generatetoaddress", (1, service.p2sh_true_address()))) == 1 and
                       daemon.rpc("getblockcount") == before + 1, "index refusal vetoed native mining")
        daemon.stop()
        runner.require(runner.regular(index / "ergon-identity.json") == identity and
                       runner.regular(retained / "ergon-identity.json") == identity,
                       "incompatible version silently changed an index")
        return {"network": "regtest", "loaded_plugins": 4, "token_families": ["ALP", "SLP"],
            "genesis_and_send_transactions": 4, "carrier_transactions": 2, "zero_native_values_and_fees": True,
            "actual_upstream_group_history_utxos": True, "group_prefix_cursor_and_refusals": True,
            "confirmed_process_reopen": True, "independent_upstream_index_reconstruction": True,
            "retained_original_index_unchanged": True, "unapproved_source_drift_refused": True,
            "changed_version_binding_refuses_existing_index": True, "native_mining_after_both_refusals": True,
            "fresh_daemon_processes": 5, "positive_joined_stops": 3, "unavailable_native_stops": 2,
            "external_connections": 0, "websocket_and_broadcast_disabled": True}
    finally:
        for daemon in daemons:
            daemon.cleanup()
        runner.remove_owned(root, work)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-root", "build-root", "work-parent", "example", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    try:
        source, build, work, example = (p.resolve(strict=True) for p in
            (args.source_root, args.build_root, args.work_parent, args.example))
        runner.require(1 <= args.repetitions <= 3, "finite community service repetitions required")
        report = args.report.absolute()
        runner.require(report.parent.resolve(strict=True) == report.parent and not report.exists() and
                       not report.is_symlink(), "exclusive canonical community report required")
        runner.require(not any(report.is_relative_to(p) for p in (source, build, work)) and
                       not any(p == source or p.is_relative_to(source) or source.is_relative_to(p)
                               for p in (build, work, example)), "separate public build/work/report required")
        runner.require(not work.is_relative_to(build) and not build.is_relative_to(work), "overlapping work/build")
        sys.path.insert(0, str(source / "tools/engineering"))
        from check_chronik_external_plugins import validate
        validate(source)
        manifest = source / "docs/engineering/chronik/upstream-host-adaptation-v1.json"
        binding = runner.bind_build(source, build, "regtest-plugins", manifest)
        binary = build / "src/bitcoind"
        hashes = tuple(runner.digest(runner.regular(p)) for p in (binary, example))
        scripts = component.templates(runner.execute([str(example), "--templates"], work, (source, build, example)))
        result = None
        for _ in range(args.repetitions):
            current = observe(source, binary, work, scripts, binding["python_embedding"])
            runner.require(result is None or result == current, "community lifecycle instability")
            result = current
        runner.require(hashes == tuple(runner.digest(runner.regular(p)) for p in (binary, example)) and
                       binding == runner.bind_build(source, build, "regtest-plugins", manifest), "community build drift")
        validate(source)
        payload = {"schema": "ergon-community-regtest-observation/v1", "knowledge_status": "Observed",
            "proof_ceiling": "bounded_regtest_service_observation", "binding": binding,
            "daemon_sha256": hashes[0], "upstream_template_example_sha256": hashes[1],
            "repetitions": args.repetitions, "result": result,
            "child_environment": dict(runner.ENV, TMPDIR="<runner-created-unique-directory>"),
            "disposable_roots_removed": True, "binary_to_source_provenance": "external-build-record-required"}
        raw = runner.canonical(payload)
        runner.require(not any(str(p).encode() in raw for p in (source, build, work, binary, example)),
                       "community report contains binding path")
        runner.write_report(report, payload)
        print("PASS:4 public plugins;ALP+SLP;native regtest;reopen;fresh upstream index;binding refusals;cleanup")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
        print("FAIL: community observation refused;class=" + runner.refusal_class(error) + ";no passing report",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

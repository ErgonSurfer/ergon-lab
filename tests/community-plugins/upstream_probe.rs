// SPDX-License-Identifier: MIT
// Synthetic actual-upstream dispatch witness. No node, DB or deployment claim.
#[allow(dead_code)]
#[path = "../../chronik/chronik-lib/src/ergon_operator_bundle.rs"]
mod ergon_operator_bundle;
#[allow(dead_code)]
#[path = "../../chronik/chronik-lib/src/ergon_plugin_access.rs"]
mod ergon_plugin_access;
use std::{collections::BTreeMap, path::PathBuf, sync::Mutex};
use bitcoinsuite_core::{
    net::Net,
    script::{opcode::OP_RETURN, Script, ScriptMut},
    tx::{Tx, TxId, TxMut, TxOutput},
};
use bitcoinsuite_slp::{
    alp::{genesis_section, sections_opreturn, send_section},
    color::ColoredTx,
    parsed::ParsedMintData,
    slp::{genesis_opreturn, send_opreturn},
    structs::GenesisInfo,
    token_type::{AlpTokenType, SlpTokenType},
    token_id::TokenId,
    verify::VerifyContext,
};
use chronik_plugin_common::{data::PluginNameMap, params::PluginParams};
use chronik_plugin_impl::context::PluginContext;

static SETUP_LOGS: Mutex<Vec<String>> = Mutex::new(Vec::new());
fn setup_log(_: &str, _: &str, _: u32, message: &str) {
    let mut logs = SETUP_LOGS.lock().unwrap();
    assert!(logs.len() < 5 && message.len() <= 512);
    logs.push(message.to_owned());
}

fn pushed(parts: &[&[u8]]) -> Script {
    let mut script = ScriptMut::default();
    script.put_opcodes([OP_RETURN]);
    for part in parts {
        script.put_pushdata(part);
    }
    script.freeze()
}

fn transaction(script: Script, id: u8) -> Tx {
    Tx::with_txid(TxId::from([id; 32]), TxMut {
        version: 2, inputs: vec![],
        outputs: vec![TxOutput { sats: 0, script }, TxOutput::default()],
        locktime: 0,
    })
}

fn main() {
    let args: Vec<_> = std::env::args().collect();
    if args.len() == 2 && args[1] == "--templates" {
        let info = GenesisInfo { token_ticker: b"TEST".as_slice().into(),
            token_name: b"Synthetic community token".as_slice().into(), ..Default::default() };
        let id = TokenId::new(TxId::from([0x42; 32]));
        let templates = [
            ("ALP_GENESIS", sections_opreturn(vec![genesis_section(AlpTokenType::Standard,
                &info, &ParsedMintData { atoms_vec: vec![1], num_batons: 0 })])),
            ("ALP_SEND", sections_opreturn(vec![send_section(&id, AlpTokenType::Standard, [1])])),
            ("SLP_GENESIS", genesis_opreturn(&info, SlpTokenType::Nft1Group, None, 1)),
            ("SLP_SEND", send_opreturn(&id, SlpTokenType::Nft1Group, &[1])),
        ];
        for (name, script) in templates {
            let hex: String = script.as_ref().iter().map(|byte| format!("{byte:02x}")).collect();
            println!("COMMUNITY_TEMPLATE {name} {hex}");
        }
        return;
    }
    assert_eq!(args.len(), 3, "reviewed module root and synthetic config required");
    assert_eq!(unsafe { pyo3::ffi::Py_IsInitialized() }, 0);
    chronik_util::mount_loggers(chronik_util::Loggers { log: setup_log, log_chronik: setup_log });
    unsafe {
        pyo3::ffi::Py_DontWriteBytecodeFlag = 1;
        pyo3::ffi::Py_IgnoreEnvironmentFlag = 1;
        pyo3::ffi::Py_NoUserSiteDirectory = 1;
        pyo3::ffi::Py_NoSiteFlag = 1;
        pyo3::ffi::Py_IsolatedFlag = 1;
    }
    let context = PluginContext::setup(PluginParams {
        net: Net::Regtest,
        plugins_dir: PathBuf::from(&args[1]),
        plugins_conf: PathBuf::from(&args[2]),
    }).expect("real upstream plugin setup");
    assert_eq!(context.plugins().len(), 4);
    {
        let logs = SETUP_LOGS.lock().unwrap();
        assert_eq!(logs.len(), 5);
        let version = logs[0].strip_prefix("Plugin context initialized Python 3.12.")
            .and_then(|s| s.strip_suffix('\n')).expect("configured CPython setup message");
        assert!(!version.is_empty() && version.bytes().all(|b| b.is_ascii_digit()));
        for (message, plugin) in logs[1..].iter().zip(context.plugins()) {
            let ids = plugin.lokad_ids.iter().map(|id| bytes::Bytes::copy_from_slice(id)).collect::<Vec<_>>();
            assert_eq!(message, &format!("Loaded plugin {}.{} (version {}) with LOKAD IDs {:?}\n",
                plugin.module_name, plugin.class_name, plugin.version, ids));
        }
    }
    let names = PluginNameMap::new(context.plugins().iter().enumerate()
        .map(|(i, p)| (i as u32, p.module_name.clone())));
    let registry = names.idx_by_name("moom_alp_token_index").unwrap();
    let nft = names.idx_by_name("moom_nft_index").unwrap();
    let market = names.idx_by_name("moom_alp_exch_market").unwrap();
    let claims = names.idx_by_name("moom_reward_claim_index").unwrap();
    for plugin in context.plugins() {
        let expected = match plugin.module_name.as_str() {
            "moom_alp_token_index" => "0.1.0",
            "moom_nft_index" => "0.2.0",
            "moom_alp_exch_market" => "0.2.1",
            "moom_reward_claim_index" => "0.1.1",
            _ => panic!("unexpected loaded plugin"),
        };
        assert_eq!(plugin.version.to_string(), expected);
    }
    let info = GenesisInfo { token_ticker: b"TEST".as_slice().into(),
        token_name: b"Synthetic community token".as_slice().into(), ..Default::default() };
    let alp = sections_opreturn(vec![genesis_section(AlpTokenType::Standard, &info,
        &ParsedMintData { atoms_vec: vec![1], num_batons: 0 })]);
    let slp = genesis_opreturn(&info, SlpTokenType::Nft1Group, None, 1);
    for (id, script, is_alp) in [(1, alp, true), (2, slp, false)] {
        let tx = transaction(script, id);
        let colored = ColoredTx::color_tx(&tx).expect("actual upstream token parsing");
        let token_tx = VerifyContext { spent_tokens: &[], spent_scripts: None,
            genesis_info: None, override_has_mint_vault: None }.verify(colored);
        assert_eq!(token_tx.entries.len(), 1);
        assert!(!token_tx.entries[0].is_invalid);
        let result = context.with_py(|py| context.run_on_tx(py, &tx,
            Some((&token_tx, &[])), &BTreeMap::new(), &names)).expect("upstream ALP/SLP dispatch");
        assert_eq!(result.outputs.len(), 1);
        let plugins = &result.outputs[&1].plugins;
        assert_eq!(plugins.len(), if is_alp { 2 } else { 1 });
        assert!(plugins[&nft].groups.iter().any(|group| group.starts_with(b"NFT")));
        assert!(plugins[&nft].data.contains(&if is_alp { b"protocol=ALP".to_vec() }
                                           else { b"protocol=SLP".to_vec() }));
        if is_alp {
            assert!(plugins[&registry].groups.iter().any(|group| group.starts_with(b"ALPT")));
            assert!(plugins[&registry].data.contains(&b"atoms=1".to_vec()));
        }
    }
    let fields: Vec<Vec<u8>> = vec![vec![0x7c; 32], vec![0; 4], vec![1; 8],
        vec![2; 8], vec![3; 20], vec![4; 20]];
    let mut parts: Vec<&[u8]> = vec![b"EXCH", &[1], b"SELL"];
    parts.extend(fields.iter().map(Vec::as_slice));
    let sell = transaction(pushed(&parts), 3);
    let result = context.with_py(|py| context.run_on_tx(py, &sell, None,
        &BTreeMap::new(), &names)).expect("upstream exchange dispatch");
    assert_eq!(result.outputs.len(), 1);
    assert_eq!(result.outputs[&1].plugins.len(), 1);
    assert_eq!(result.outputs[&1].plugins[&market].groups, vec![b"EXCH\x01".to_vec()]);
    let mut expected = Vec::new();
    for (i, field) in fields.iter().enumerate() {
        if i != 0 { expected.push(0x7c); }
        expected.extend_from_slice(field);
    }
    assert_eq!(result.outputs[&1].plugins[&market].data, vec![expected]);
    let mut envelope = vec![0; 68];
    envelope[..4].copy_from_slice(b"MCLM");
    envelope[5] = 1;
    envelope[50] = 1;
    let claim = transaction(pushed(&[b"MCLM", &envelope]), 4);
    let result = context.with_py(|py| context.run_on_tx(py, &claim, None,
        &BTreeMap::new(), &names)).expect("upstream claim dispatch");
    assert_eq!(result.outputs.len(), 1);
    assert_eq!(result.outputs[&0].plugins.len(), 1);
    assert_eq!(result.outputs[&0].plugins[&claims].groups, vec![b"MCLM\0".to_vec()]);
    assert_eq!(result.outputs[&0].plugins[&claims].data,
        vec![b"version=0".to_vec(), b"kind=1".to_vec()]);
    // Valid script opcodes in required data positions used to throw TypeError.
    // The first pushed identifier still routes through upstream dispatch.
    let mut bad_market = pushed(&[b"EXCH", &[1], b"SELL"]).as_ref().to_vec();
    bad_market.push(0x51);
    for field in &fields[1..] {
        let mut push = ScriptMut::default();
        push.put_pushdata(field);
        bad_market.extend_from_slice(push.freeze().as_ref());
    }
    let mut bad_claim = pushed(&[b"MCLM"]).as_ref().to_vec();
    bad_claim.push(0x51);
    for (id, raw) in [(5, bad_market), (6, bad_claim)] {
        let tx = transaction(Script::new(raw.into()), id);
        let result = context.with_py(|py| context.run_on_tx(py, &tx, None,
            &BTreeMap::new(), &names)).expect("malformed carrier must not poison plugin execution");
        assert!(result.outputs.is_empty());
    }
    println!("PASS_COMMUNITY_PLUGINS loaded=4 token_families=2 carriers=2 invalid_refusals=2");
}

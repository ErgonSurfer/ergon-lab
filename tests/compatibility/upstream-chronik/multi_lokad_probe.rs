// SPDX-License-Identifier: MIT
// Public synthetic component witness; no native-node or deployment claim.
use std::{collections::BTreeMap, path::PathBuf};
use bitcoinsuite_core::{net::Net,script::{opcode::{OP_RETURN,OP_RESERVED},ScriptMut},tx::{Tx,TxId,TxMut,TxOutput}};
use chronik_plugin_impl::context::PluginContext;
use chronik_plugin_common::{params::PluginParams,data::PluginNameMap};
fn main() {
    let args=std::env::args().collect::<Vec<_>>();
    assert_eq!(args.len(),3);
    unsafe {
        pyo3::ffi::Py_DontWriteBytecodeFlag=1;
        pyo3::ffi::Py_IgnoreEnvironmentFlag=1;
        pyo3::ffi::Py_NoUserSiteDirectory=1;
        pyo3::ffi::Py_NoSiteFlag=1;
        pyo3::ffi::Py_IsolatedFlag=1;
    }
    let ctx=PluginContext::setup(PluginParams {net:Net::Regtest,
        plugins_dir:PathBuf::from(&args[1]),plugins_conf:PathBuf::from(&args[2])}).expect("upstream setup");
    assert_eq!(ctx.plugins().len(),2);
    let names=PluginNameMap::new(ctx.plugins().iter().enumerate().map(|(i,p)|(i as u32,p.module_name.clone())));
    let multi=names.idx_by_name("multi_lokad_public").unwrap();
    let legacy=names.idx_by_name("single_lokad_public").unwrap();
    for (number,ids,count,legacy_match) in [
        (1,vec![*b"AAAA"],Some(1),false),
        (2,vec![*b"BBBB"],Some(2),false),
        (3,vec![*b"AAAA",*b"BBBB"],Some(3),false),
        (4,vec![*b"DDDD"],None,false),
        (5,vec![*b"CCCC"],None,true),
        (6,vec![*b"AAAA"],Some(4),false),
    ] {
        let mut script=ScriptMut::default();
        script.put_opcodes([OP_RETURN,OP_RESERVED]);
        for id in ids {script.put_pushdata(&id);}
        let tx=Tx::with_txid(TxId::from([number;32]),TxMut {version:2,inputs:vec![],
            outputs:vec![TxOutput {sats:0,script:script.freeze()},TxOutput::default()],locktime:0});
        let result=ctx.with_py(|py|ctx.run_on_tx(py,&tx,None,&BTreeMap::new(),&names)).expect("upstream dispatch");
        if let Some(expected)=count {
            assert_eq!(result.outputs.len(),1);
            let plugins=&result.outputs[&1].plugins;
            assert_eq!(plugins.len(),1);
            assert_eq!(plugins[&multi].groups,vec![b"public".to_vec()]);
            assert_eq!(plugins[&multi].data,vec![expected.to_string().into_bytes()]);
        } else if legacy_match {
            assert_eq!(result.outputs.len(),1);
            let plugins=&result.outputs[&1].plugins;
            assert_eq!(plugins.len(),1);
            assert_eq!(plugins[&legacy].data,vec![b"unchanged".to_vec()]);
        } else {assert!(result.outputs.is_empty());}
    }
    println!("PASS_PUBLIC_MULTI_LOKAD legacy=1 multi=2 combined_once=1 unmatched=1 no_extra_call=1");
}

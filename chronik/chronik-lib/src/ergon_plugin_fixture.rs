// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
//! Owned public synthetic plugin fixture over the unchanged upstream engine.
//! One interpreter context per fresh process, shared across DB reopen/worker.
use crate::ergon_operator_bundle::OperatorBundle;
use abc_rust_error::{bail, Result};
use bitcoinsuite_core::hash::{Hashed, Sha256};
use chronik_plugin::context::PluginContext;
use std::{
    fs,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};

const SOURCE: &str =
    include_str!("../../../tests/compatibility/upstream-chronik/plugins/ergon_public_probe.py");
#[derive(Clone)]
pub struct PluginFixture {
    pub(crate) context: Arc<PluginContext>,
    root: PathBuf,
    config: Vec<u8>,
    pub(crate) binding: String,
    operator_bundle: Option<OperatorBundle>,
}
impl PluginFixture {
    pub(crate) fn check(&self) -> Result<()> {
        #[cfg(feature = "plugins")]
        if let Some(bundle) = &self.operator_bundle {
            return bundle.check(
                env!("CHRONIK_ERGON_PYTHON_VERSION"),
                env!("CHRONIK_ERGON_PYTHON_ID"),
            );
        }
        if fs::canonicalize(&self.root)? != self.root {
            bail!("Plugin fixture root alias changed");
        }
        for path in [
            &self.root.join("ergon_public_probe.py"),
            &self.root.join("plugins.toml"),
        ] {
            if !fs::symlink_metadata(path)?.file_type().is_file() {
                bail!("Plugin fixture input is not regular");
            }
        }
        if fs::read_dir(&self.root)?.count() != 2
            || fs::read(self.root.join("ergon_public_probe.py"))? != SOURCE.as_bytes()
            || fs::read(self.root.join("plugins.toml"))? != self.config
        {
            bail!("Public plugin fixture input drift");
        }
        Ok(())
    }
    pub(crate) fn is_operator(&self) -> bool {
        self.operator_bundle.is_some()
    }
    pub fn loaded(&self) -> u32 {
        self.context.plugins().len() as u32
    }
}

pub fn make_plugin_fixture(root: &str, mode: u8) -> Result<Box<PluginFixture>> {
    plugin_fixture(root, mode, false)
}

pub fn load_plugin_fixture(root: &str, mode: u8) -> Result<Box<PluginFixture>> {
    plugin_fixture(root, mode, true)
}

pub(crate) fn binding_for_mode(mode: u8) -> Result<String> {
    #[cfg(not(feature = "plugins"))]
    {
        let _ = mode;
        bail!("Upstream plugin implementation is not enabled");
    }
    #[cfg(feature = "plugins")]
    {
        let config = config_for_mode(mode)?;
        let mut identity = b"ergon-public-plugin-fixture/v1\n".to_vec();
        identity.extend_from_slice(SOURCE.as_bytes());
        identity.extend_from_slice(&config);
        identity.extend_from_slice(env!("CHRONIK_ERGON_PYTHON_VERSION").as_bytes());
        identity.extend_from_slice(env!("CHRONIK_ERGON_PYTHON_ID").as_bytes());
        Ok(Sha256::digest(identity).hex_le())
    }
}

#[cfg(feature = "plugins")]
fn config_for_mode(mode: u8) -> Result<Vec<u8>> {
    let class = match mode {
        0 => "PublicProbePlugin",
        1 => "ThrowProbePlugin",
        2 => "BadOutputProbePlugin",
        3 => "VersionTwoProbePlugin",
        _ => bail!("Unknown public plugin fixture mode"),
    };
    Ok(format!("[regtest.plugin.ergon_public_probe]\nclass = \"{class}\"\n").into_bytes())
}

fn plugin_fixture(root: &str, mode: u8, reopen: bool) -> Result<Box<PluginFixture>> {
    #[cfg(not(feature = "plugins"))]
    {
        let _ = (root, mode, reopen);
        bail!("Upstream plugin implementation is not enabled");
    }
    #[cfg(feature = "plugins")]
    {
        use std::io::Write;
        let root = PathBuf::from(root);
        if !root.is_absolute()
            || fs::canonicalize(&root)? != root
            || (!reopen && fs::read_dir(&root)?.next().is_some())
        {
            bail!("Plugin fixture requires an empty canonical root");
        }
        let config = config_for_mode(mode)?;
        let binding = binding_for_mode(mode)?;
        let provisional = PluginFixture {
            context: Arc::new(PluginContext::default()),
            root: root.clone(),
            config: config.clone(),
            binding: binding.clone(),
            operator_bundle: None,
        };
        // Validate existing bytes BEFORE interpreter initialization/import.
        if reopen {
            provisional.check()?;
        }
        claim_interpreter()?;
        for (name, bytes) in if reopen {
            Vec::new()
        } else {
            vec![
                ("ergon_public_probe.py", SOURCE.as_bytes()),
                ("plugins.toml", config.as_slice()),
            ]
        } {
            let mut file = fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(root.join(name))?;
            file.write_all(bytes)?;
            file.sync_all()?;
        }
        let context = setup_context(&root)?;
        if context.plugins().len() != 1 || context.plugins()[0].module_name != "ergon_public_probe"
        {
            bail!("Actual upstream plugin was not loaded");
        }
        let fixture = Box::new(PluginFixture {
            context,
            root,
            config,
            binding,
            operator_bundle: None,
        });
        fixture.check()?;
        Ok(fixture)
    }
}

// Shared by both entry points; failed interpreter ownership is not retried.
#[cfg(feature = "plugins")]
fn claim_interpreter() -> Result<()> {
    static INITIALIZED: AtomicBool = AtomicBool::new(false);
    if INITIALIZED.swap(true, Ordering::SeqCst) {
        bail!("Reuse the owned plugin fixture; interpreter setup is process-once");
    }
    if unsafe { pyo3::ffi::Py_IsInitialized() } != 0 {
        bail!("Plugin fixture requires ownership of initial interpreter setup");
    }
    Ok(())
}

#[cfg(feature = "plugins")]
fn setup_context(root: &std::path::Path) -> Result<Arc<PluginContext>> {
    setup_context_for_network(root, bitcoinsuite_core::net::Net::Regtest)
}
#[cfg(feature = "plugins")]
fn setup_context_for_network(root: &std::path::Path, net: bitcoinsuite_core::net::Net) -> Result<Arc<PluginContext>> {
    use chronik_plugin::params::PluginParams;
    use pyo3::{
        types::{PyAnyMethods, PyModule},
        Python,
    };
    // No Python code/import precedes upstream registration. Suppress
    // generated caches at native CPython configuration, before first init.
    unsafe {
        pyo3::ffi::Py_DontWriteBytecodeFlag = 1;
        pyo3::ffi::Py_IgnoreEnvironmentFlag = 1;
        pyo3::ffi::Py_NoUserSiteDirectory = 1;
        pyo3::ffi::Py_NoSiteFlag = 1;
        pyo3::ffi::Py_IsolatedFlag = 1;
    }
    let context = Arc::new(PluginContext::setup(PluginParams {
        net,
        plugins_dir: root.to_path_buf(),
        plugins_conf: root.join("plugins.toml"),
    })?);
    let python: String = Python::with_gil(|py| -> pyo3::PyResult<String> {
        PyModule::import(py, "platform")?
            .getattr("python_version")?
            .call0()?
            .extract()
    })?;
    if python != env!("CHRONIK_ERGON_PYTHON_VERSION") {
        bail!("Plugin interpreter differs from configured profile");
    }
    Ok(context)
}

/// Offline inspection only; caller must explicitly approve this commitment.
pub fn operator_bundle_binding(root: &str) -> Result<String> {
    #[cfg(not(feature = "plugins"))]
    {
        let _ = root;
        bail!("Upstream plugin implementation is not enabled");
    }
    #[cfg(feature = "plugins")]
    Ok(OperatorBundle::inspect(
        std::path::Path::new(root),
        env!("CHRONIK_ERGON_PYTHON_VERSION"),
        env!("CHRONIK_ERGON_PYTHON_ID"),
    )?
    .binding)
}

/// Trusted Python code, never a sandbox. Regtest only, no index adoption/wipe.
/// The legacy CXX type name is retained to preserve the synthetic fixture ABI.
pub fn load_operator_plugins(root: &str, expected: &str) -> Result<Box<PluginFixture>> {
    load_operator_plugins_for_network(root, expected, "regtest")
}
/// Separate historical admission; immutable network is checked before import.
pub fn load_historical_operator_plugins(root: &str, expected: &str, network: &str) -> Result<Box<PluginFixture>> {
    load_operator_plugins_for_network(root, expected, network)
}
fn load_operator_plugins_for_network(root: &str, expected: &str, network: &str) -> Result<Box<PluginFixture>> {
    #[cfg(not(feature = "plugins"))]
    {
        let _ = (root, expected, network);
        bail!("Upstream plugin implementation is not enabled");
    }
    #[cfg(feature = "plugins")]
    {
        use pyo3::{
            types::{PyAnyMethods, PyModule},
            Python,
        };
        let net = match network {
            "main" => bitcoinsuite_core::net::Net::Mainnet,
            "regtest" => bitcoinsuite_core::net::Net::Regtest,
            // Upstream's [test] section is a configuration selector only.
            // Native admission, index roots and commitments remain bound to
            // ergon-testnet-v1 and its exact genesis, never to a BCH chain.
            "ergon-testnet-v1" => bitcoinsuite_core::net::Net::Testnet,
            _ => bail!("Unsupported historical plugin network"),
        };
        let bundle = OperatorBundle::admit_network(
            std::path::Path::new(root),
            expected,
            env!("CHRONIK_ERGON_PYTHON_VERSION"),
            env!("CHRONIK_ERGON_PYTHON_ID"),
            network,
        )?;
        claim_interpreter()?;
        let context = setup_context_for_network(bundle.root(), net)?;
        let names: Vec<_> = context
            .plugins()
            .iter()
            .map(|p| p.module_name.clone())
            .collect();
        if names != bundle.modules() {
            bail!("Configured plugins differ from complete operator source inventory");
        }
        // Do not silently accept a cached/standard-library module in place of
        // an inventoried source. All helpers must be embedded upstream or stdlib.
        Python::with_gil(|py| -> Result<()> {
            for name in &names {
                let file: String = PyModule::import(py, name.as_str())?
                    .getattr("__file__")?
                    .extract()?;
                if std::path::Path::new(&file) != bundle.root().join(format!("{name}.py")) {
                    bail!("Loaded operator module origin mismatch");
                }
            }
            Ok(())
        })?;
        bundle.check(
            env!("CHRONIK_ERGON_PYTHON_VERSION"),
            env!("CHRONIK_ERGON_PYTHON_ID"),
        )?;
        Ok(Box::new(PluginFixture {
            context,
            root: bundle.root().to_path_buf(),
            config: Vec::new(),
            binding: bundle.binding.clone(),
            operator_bundle: Some(bundle),
        }))
    }
}

// Decode the REAL upstream response; no mock handler or parallel plugin logic.
pub fn http_plugin_tx_matches(body: &[u8], output: u32, parents: u32, atoms: u64) -> Result<bool> {
    use prost::Message;
    let tx = chronik_proto::proto::Tx::decode(body)?;
    let Some(out) = tx.outputs.get(output as usize) else {
        return Ok(false);
    };
    let Some(plugin) = out.plugins.get("ergon_public_probe") else {
        return Ok(false);
    };
    Ok(plugin.groups == [b"public".to_vec()]
        && plugin.data
            == [
                b"synthetic-v1".to_vec(),
                out.sats.to_string().into_bytes(),
                parents.to_string().into_bytes(),
                atoms.to_string().into_bytes(),
            ])
}

pub fn http_plugin_group_matches(body: &[u8], kind: u8, points: &[u8]) -> Result<bool> {
    use prost::Message;
    match kind {
        0 => {
            let groups = chronik_proto::proto::PluginGroups::decode(body)?;
            Ok(groups.groups.len() == 1
                && groups.groups[0].group == b"public"
                && groups.next_start.is_empty())
        }
        1 => {
            let utxos = chronik_proto::proto::Utxos::decode(body)?.utxos;
            let mut actual = Vec::new();
            for output in &utxos {
                let Some(point) = &output.outpoint else {
                    return Ok(false);
                };
                let Some(plugin) = output.plugins.get("ergon_public_probe") else {
                    return Ok(false);
                };
                if plugin.groups != [b"public".to_vec()] || output.sats != 0 || output.is_coinbase {
                    return Ok(false);
                }
                let mut bytes = point.txid.clone();
                bytes.extend_from_slice(&point.out_idx.to_le_bytes());
                actual.push(bytes);
            }
            if points.len() % 36 != 0 {
                return Ok(false);
            }
            let mut expected: Vec<_> = points.chunks_exact(36).map(|p| p.to_vec()).collect();
            actual.sort();
            expected.sort();
            Ok(actual == expected)
        }
        _ => bail!("Unknown public plugin group oracle"),
    }
}

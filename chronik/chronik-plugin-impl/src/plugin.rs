// Copyright (c) 2024 The Bitcoin developers
// Distributed under the MIT software license, see the accompanying
// file COPYING or http://www.opensource.org/licenses/mit-license.php.

//! Module for [`Plugin`].

use abc_rust_error::Result;
use bitcoinsuite_slp::lokad_id::LokadId;
use chronik_plugin_common::plugin::Plugin;
use convert_case::{Case, Casing};
use pyo3::{
    types::{PyAnyMethods, PyBytes, PyBytesMethods, PyDict, PyList, PyListMethods, PyModule},
    Bound, PyAny, PyObject, Python,
};
use thiserror::Error;
use versions::SemVer;

use crate::plugin::PluginError::*;

/// Errors for PluginContext.
#[derive(Debug, Eq, Error, PartialEq)]
pub(crate) enum PluginError {
    /// Failed importing plugin module
    #[error("Failed importing plugin module: {0}")]
    FailedImportingModule(String),

    /// Failed importing plugin module
    #[error(
        "Could not find class {0:?} in the plugin module. Make sure it is \
         spelled correctly."
    )]
    ClassNotFound(String),

    /// Class must derive from Plugin
    #[error("Class {0:?} must derive from chronik_plugin.plugin.Plugin")]
    ClassMustDerivePlugin(String),

    /// Invalid LOKAD ID, must be bytes
    #[error("Invalid lokad_id, must be bytes: {0}")]
    InvalidLokadIdType(String),

    /// LOKAD ID must be 4 bytes long
    #[error("Invalid lokad_id length, expected 4 bytes, but got {0} bytes")]
    InvalidLokadIdLen(usize),

    /// Lists of identifiers must be nonempty and bounded.
    #[error("lokad_id list must contain between 1 and 32 identifiers, got {0}")]
    InvalidLokadIdsCount(usize),

    /// A plugin is dispatched once even if several identifiers match.
    #[error("lokad_id list contains a duplicate identifier")]
    DuplicateLokadId,

    /// Invalid version, must be str
    #[error("Invalid version, must be str: {0}")]
    InvalidVersionType(String),

    /// Invalid version, must follow semver https://semver.org/
    #[error(
        "Invalid version {0:?}, must follow semantic versioning (see \
         https://semver.org/)"
    )]
    InvalidVersion(String),
}

pub(crate) fn load_plugin<'py>(
    py: Python<'py>,
    module_name: String,
    class_name: Option<String>,
    plugin_cls: &Bound<'py, PyAny>,
) -> Result<(Plugin, PyObject)> {
    let module = PyModule::import(py, module_name.as_str())
        .map_err(|err| FailedImportingModule(err.to_string()))?;

    // Class name is either NamePascalCasePlugin, or manually specified
    let class_name = match class_name {
        Some(class_name) => class_name,
        None => format!("{}Plugin", module_name.to_case(Case::Pascal)),
    };

    let class = module
        .getattr(class_name.as_str())
        .map_err(|_| ClassNotFound(class_name.clone()))?;

    // Empty config object for now
    let config = PyDict::new(py);
    let plugin_instance = class.call1((config,))?;

    // Must be a Plugin instance
    if !plugin_instance.is_instance(plugin_cls)? {
        return Err(ClassMustDerivePlugin(class_name.clone()).into());
    }

    let lokad_ids = parse_lokad_ids(&plugin_instance.getattr("lokad_id")?.call0()?)?;
    let version = plugin_instance.getattr("version")?.call0()?;
    let version = version
        .extract::<&str>()
        .map_err(|err| InvalidVersionType(err.to_string()))?;
    let version = SemVer::new(version)
        .ok_or_else(|| InvalidVersion(version.to_string()))?;

    Ok((
        Plugin {
            module_name,
            class_name,
            version,
            lokad_ids,
        },
        plugin_instance.into(),
    ))
}

// Ergon compatibility extension: metadata admission only. The existing context
// already routes a plugin once when any of its registered identifiers matches.
// Legacy bytes returns remain unchanged. No index, token or consensus logic.
fn parse_one_lokad_id(value: &Bound<'_, PyAny>) -> Result<LokadId> {
    let bytes = value.downcast::<PyBytes>()
        .map_err(|_| InvalidLokadIdType("expected bytes".to_string()))?;
    let raw = bytes.as_bytes();
    raw.try_into().map_err(|_| InvalidLokadIdLen(raw.len()).into())
}

fn parse_lokad_ids(value: &Bound<'_, PyAny>) -> Result<Vec<LokadId>> {
    if value.is_instance_of::<PyBytes>() {
        return Ok(vec![parse_one_lokad_id(value)?]);
    }
    let list = value.downcast::<PyList>()
        .map_err(|_| InvalidLokadIdType("expected bytes or list[bytes]".to_string()))?;
    if !(1..=32).contains(&list.len()) {
        return Err(InvalidLokadIdsCount(list.len()).into());
    }
    let mut ids = Vec::with_capacity(list.len());
    for value in list.iter() {
        let id = parse_one_lokad_id(&value)?;
        if ids.contains(&id) {
            return Err(DuplicateLokadId.into());
        }
        ids.push(id);
    }
    Ok(ids)
}

#[cfg(test)]
mod ergon_multi_lokad_tests {
    use super::*;
    use pyo3::types::{PyString, PyTuple};

    #[test]
    fn legacy_bytes_unchanged() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            assert_eq!(parse_lokad_ids(PyBytes::new(py, b"SLP\0").as_any()).unwrap(), vec![*b"SLP\0"]);
        });
    }
    #[test]
    fn both_identifiers_preserve_order() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            let list = PyList::new(py, [PyBytes::new(py,b"SLP\0"),PyBytes::new(py,b"SLP2")]).unwrap();
            assert_eq!(parse_lokad_ids(list.as_any()).unwrap(),vec![*b"SLP\0",*b"SLP2"]);
        });
    }
    #[test]
    fn single_entry_list() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            let list = PyList::new(py,[PyBytes::new(py,b"AAAA")]).unwrap();
            assert_eq!(parse_lokad_ids(list.as_any()).unwrap(),vec![*b"AAAA"]);
        });
    }
    #[test]
    fn exact_list_limit() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            let list=PyList::new(py,(0u32..32).map(|n|PyBytes::new(py,&n.to_be_bytes()))).unwrap();
            assert_eq!(parse_lokad_ids(list.as_any()).unwrap().len(),32);
        });
    }
    #[test]
    fn empty_list_refused() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            assert!(matches!(parse_lokad_ids(PyList::empty(py).as_any()).unwrap_err().downcast_ref::<PluginError>(),Some(InvalidLokadIdsCount(0))));
        });
    }
    #[test]
    fn oversized_list_refused_before_entries() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            let list=PyList::new(py,(0..33).map(|_|py.None())).unwrap();
            assert!(matches!(parse_lokad_ids(list.as_any()).unwrap_err().downcast_ref::<PluginError>(),Some(InvalidLokadIdsCount(33))));
        });
    }
    #[test]
    fn duplicate_refused() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            let list=PyList::new(py,[PyBytes::new(py,b"AAAA"),PyBytes::new(py,b"AAAA")]).unwrap();
            assert!(matches!(parse_lokad_ids(list.as_any()).unwrap_err().downcast_ref::<PluginError>(),Some(DuplicateLokadId)));
        });
    }
    #[test]
    fn legacy_wrong_width_refused() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            for n in [0,3,5] {
                assert!(matches!(parse_lokad_ids(PyBytes::new(py,&vec![0;n]).as_any()).unwrap_err().downcast_ref::<PluginError>(),Some(InvalidLokadIdLen(m)) if *m==n));
            }
        });
    }
    #[test]
    fn list_wrong_width_refused() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            let list=PyList::new(py,[PyBytes::new(py,b"AAAA"),PyBytes::new(py,b"BAD")]).unwrap();
            assert!(matches!(parse_lokad_ids(list.as_any()).unwrap_err().downcast_ref::<PluginError>(),Some(InvalidLokadIdLen(3))));
        });
    }
    #[test]
    fn list_nonbytes_refused() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            let list=PyList::new(py,[1_i32]).unwrap();
            assert!(matches!(parse_lokad_ids(list.as_any()).unwrap_err().downcast_ref::<PluginError>(),Some(InvalidLokadIdType(_))));
        });
    }
    #[test]
    fn string_and_tuple_refused() {
        pyo3::prepare_freethreaded_python();
        Python::with_gil(|py| {
            assert!(parse_lokad_ids(PyString::new(py,"AAAA").as_any()).is_err());
            let tuple=PyTuple::new(py,[PyBytes::new(py,b"AAAA")]).unwrap();
            assert!(parse_lokad_ids(tuple.as_any()).is_err());
        });
    }
}

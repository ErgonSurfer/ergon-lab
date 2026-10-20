// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
//! Content-bound inputs for explicitly trusted, local operator plugins.
//!
//! This is admission/integrity checking, NOT a Python sandbox. The operator
//! must approve the complete bundle and keep it stable and access-controlled.
//! An adversarial concurrent filesystem writer is outside this contract.
use abc_rust_error::{bail, Result};
use bitcoinsuite_core::hash::{Hashed, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::Read,
    path::{Path, PathBuf},
};

const MAX_MODULES: usize = 32;
const MAX_SOURCE: u64 = 1024 * 1024;
const MAX_CONFIG: u64 = 64 * 1024;
const MAX_TOTAL: usize = 4 * 1024 * 1024;
const DOMAIN: &[u8] = b"ergon-trusted-operator-plugin-bundle/regtest/v1\n";

#[derive(Clone)]
pub(crate) struct OperatorBundle {
    root: PathBuf,
    files: BTreeMap<String, Vec<u8>>,
    pub(crate) binding: String,
}

impl OperatorBundle {
    /// Pure file inspection. Never initializes Python or executes a plugin.
    pub(crate) fn inspect(root: &Path, python_version: &str, python_id: &str) -> Result<Self> {
        if !root.is_absolute()
            || fs::canonicalize(root)? != root
            || !fs::symlink_metadata(root)?.file_type().is_dir()
        {
            bail!("Operator plugin bundle requires a canonical directory");
        }
        let mut files = BTreeMap::new();
        let mut total = 0;
        for entry in fs::read_dir(root)? {
            if files.len() >= MAX_MODULES + 1 {
                bail!("Too many operator bundle inputs");
            }
            let entry = entry?;
            let name = entry
                .file_name()
                .into_string()
                .map_err(|_| abc_rust_error::Report::msg("Non-UTF-8 operator bundle name"))?;
            let limit = if name == "plugins.toml" {
                MAX_CONFIG
            } else {
                let Some(module) = name.strip_suffix(".py") else {
                    bail!("Unexpected operator bundle input");
                };
                if !module_name(module) {
                    bail!("Invalid operator module name");
                }
                MAX_SOURCE
            };
            let path = entry.path();
            let before = fs::symlink_metadata(&path)?;
            check_metadata(&before, limit)?;
            let file = fs::File::open(&path)?;
            let opened = file.metadata()?;
            check_metadata(&opened, limit)?;
            if !same_file(&before, &opened) {
                bail!("Operator bundle input changed during open");
            }
            let mut bytes = Vec::new();
            file.take(limit + 1).read_to_end(&mut bytes)?;
            let after = fs::symlink_metadata(&path)?;
            check_metadata(&after, limit)?;
            if !same_file(&opened, &after)
                || bytes.len() as u64 != after.len()
                || bytes.len() as u64 > limit
            {
                bail!("Operator bundle input changed during read");
            }
            if std::str::from_utf8(&bytes).is_err() || bytes.contains(&0) {
                bail!("Operator plugin inputs require UTF-8 text without NUL");
            }
            total += bytes.len();
            if total > MAX_TOTAL {
                bail!("Operator bundle exceeds total input limit");
            }
            files.insert(name, bytes);
        }
        if !files.contains_key("plugins.toml") || files.len() < 2 {
            bail!("Operator bundle requires config and at least one module");
        }
        let binding = commitment(&files, python_version, python_id);
        Ok(Self {
            root: root.to_path_buf(),
            files,
            binding,
        })
    }

    pub(crate) fn admit(
        root: &Path,
        expected: &str,
        python_version: &str,
        python_id: &str,
    ) -> Result<Self> {
        if expected.len() != 64
            || !expected
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            bail!("Expected operator binding must be lowercase SHA-256");
        }
        let bundle = Self::inspect(root, python_version, python_id)?;
        if bundle.binding != expected {
            bail!("Operator plugin bundle binding mismatch; no import");
        }
        Ok(bundle)
    }

    pub(crate) fn root(&self) -> &Path {
        &self.root
    }
    pub(crate) fn modules(&self) -> Vec<String> {
        self.files
            .keys()
            .filter_map(|n| n.strip_suffix(".py").map(str::to_owned))
            .collect()
    }
    pub(crate) fn check(&self, python_version: &str, python_id: &str) -> Result<()> {
        let now = Self::inspect(&self.root, python_version, python_id)?;
        if now.files != self.files || now.binding != self.binding {
            bail!("Operator plugin bundle input drift");
        }
        Ok(())
    }
}

fn module_name(name: &str) -> bool {
    let mut chars = name.bytes();
    matches!(chars.next(), Some(b) if b.is_ascii_alphabetic() || b == b'_')
        && chars.all(|b| b.is_ascii_alphanumeric() || b == b'_')
}

fn check_metadata(meta: &fs::Metadata, limit: u64) -> Result<()> {
    if !meta.file_type().is_file() || meta.len() > limit {
        bail!("Operator bundle input must be a bounded regular file");
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if meta.nlink() != 1 || meta.mode() & 0o022 != 0 {
            bail!("Operator bundle input is hard-linked or externally writable");
        }
    }
    Ok(())
}

fn same_file(a: &fs::Metadata, b: &fs::Metadata) -> bool {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        return a.dev() == b.dev()
            && a.ino() == b.ino()
            && a.len() == b.len()
            && a.mtime() == b.mtime()
            && a.mtime_nsec() == b.mtime_nsec()
            && a.ctime() == b.ctime()
            && a.ctime_nsec() == b.ctime_nsec();
    }
    #[cfg(not(unix))]
    {
        a.len() == b.len() && a.modified().ok() == b.modified().ok()
    }
}

fn commitment(files: &BTreeMap<String, Vec<u8>>, python_version: &str, python_id: &str) -> String {
    // Length framing avoids ambiguity; names sorted by BTreeMap. No local path
    // enters the identity. Loader behavior is part of the versioned domain.
    fn field(out: &mut Vec<u8>, bytes: &[u8]) {
        out.extend_from_slice(&(bytes.len() as u64).to_le_bytes());
        out.extend_from_slice(bytes);
    }
    let mut identity = DOMAIN.to_vec();
    field(&mut identity, python_version.as_bytes());
    field(&mut identity, python_id.as_bytes());
    identity.extend_from_slice(&(files.len() as u64).to_le_bytes());
    for (name, bytes) in files {
        field(&mut identity, name.as_bytes());
        field(&mut identity, bytes);
    }
    Sha256::digest(identity).hex_le()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    struct Root(PathBuf);
    impl Root {
        fn new() -> Self {
            static NEXT: AtomicU64 = AtomicU64::new(0);
            let path = std::env::temp_dir().join(format!(
                "operator-bundle-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::SeqCst)
            ));
            fs::create_dir(&path).unwrap();
            Self(fs::canonicalize(path).unwrap())
        }
        fn put(&self, name: &str, bytes: &[u8]) {
            fs::write(self.0.join(name), bytes).unwrap();
        }
        fn good() -> Self {
            let root = Self::new();
            root.put("plugins.toml", b"[regtest.plugin.public_probe]\n");
            root.put("public_probe.py", b"# synthetic public source\n");
            root
        }
        fn inspect(&self) -> Result<OperatorBundle> {
            OperatorBundle::inspect(&self.0, "3.12.0", "public-test-interpreter")
        }
    }
    impl Drop for Root {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).unwrap();
        }
    }
    #[test]
    fn path_independent_and_explicit_admission() {
        let a = Root::good();
        let b = Root::good();
        let binding = a.inspect().unwrap().binding;
        assert_eq!(binding, b.inspect().unwrap().binding);
        assert!(OperatorBundle::admit(&a.0, &binding, "3.12.0", "public-test-interpreter").is_ok());
        assert_eq!(a.inspect().unwrap().modules(), ["public_probe"]);
    }
    #[test]
    fn changed_source_refused() {
        let r = Root::good();
        let sealed = r.inspect().unwrap();
        r.put("public_probe.py", b"# changed\n");
        assert!(sealed.check("3.12.0", "public-test-interpreter").is_err());
    }
    #[test]
    fn changed_config_refused() {
        let r = Root::good();
        let sealed = r.inspect().unwrap();
        r.put(
            "plugins.toml",
            b"[regtest.plugin.public_probe]\nclass='Other'\n",
        );
        assert!(sealed.check("3.12.0", "public-test-interpreter").is_err());
    }
    #[test]
    fn wrong_and_malformed_binding_refused() {
        let r = Root::good();
        for b in ["", "../../../elsewhere", &"f".repeat(64), &"A".repeat(64)] {
            assert!(OperatorBundle::admit(&r.0, b, "3.12.0", "public-test-interpreter").is_err());
        }
    }
    #[test]
    fn interpreter_identity_bound() {
        let r = Root::good();
        let old = r.inspect().unwrap();
        assert!(old.check("3.12.1", "public-test-interpreter").is_err());
        assert!(old.check("3.12.0", "different-interpreter").is_err());
    }
    #[test]
    fn missing_inputs_refused() {
        let r = Root::new();
        assert!(r.inspect().is_err());
        r.put("plugins.toml", b"");
        assert!(r.inspect().is_err());
    }
    #[test]
    fn extra_cache_or_binary_refused() {
        for name in [
            "__pycache__",
            "extra.bin",
            "escape-name.py",
            "3invalid.py",
            "p.q.py",
        ] {
            let r = Root::good();
            r.put(name, b"synthetic");
            assert!(r.inspect().is_err());
        }
    }
    #[test]
    fn directory_refused() {
        let r = Root::good();
        fs::create_dir(r.0.join("extra.py")).unwrap();
        assert!(r.inspect().is_err());
    }
    #[test]
    fn non_text_refused() {
        for bytes in [b"bad\0source".as_slice(), &[255u8]] {
            let r = Root::good();
            r.put("public_probe.py", bytes);
            assert!(r.inspect().is_err());
        }
    }
    #[test]
    fn oversize_source_and_config_refused() {
        let r = Root::good();
        r.put("public_probe.py", &vec![b'x'; MAX_SOURCE as usize + 1]);
        assert!(r.inspect().is_err());
        let r = Root::good();
        r.put("plugins.toml", &vec![b'x'; MAX_CONFIG as usize + 1]);
        assert!(r.inspect().is_err());
    }
    #[test]
    fn too_many_inputs_refused() {
        let r = Root::good();
        for n in 0..MAX_MODULES {
            r.put(&format!("public_{n}.py"), b"# synthetic\n");
        }
        assert!(r.inspect().is_err());
    }
    #[test]
    fn total_size_bound() {
        let r = Root::good();
        for n in 0..4 {
            r.put(&format!("public_{n}.py"), &vec![b'x'; MAX_SOURCE as usize]);
        }
        assert!(r.inspect().is_err());
    }
    #[test]
    fn framed_fields_are_unambiguous() {
        let a = BTreeMap::from([("a.py".into(), b"bc".to_vec())]);
        let b = BTreeMap::from([("ab.py".into(), b"c".to_vec())]);
        assert_ne!(commitment(&a, "py", "id"), commitment(&b, "py", "id"));
    }
    #[cfg(unix)]
    #[test]
    fn aliases_and_external_write_permissions_refused() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let r = Root::good();
        let alias = Root::new();
        let path = alias.0.join("alias");
        symlink(&r.0, &path).unwrap();
        assert!(OperatorBundle::inspect(&path, "py", "id").is_err());
        symlink(r.0.join("public_probe.py"), r.0.join("linked.py")).unwrap();
        assert!(r.inspect().is_err());
        fs::remove_file(r.0.join("linked.py")).unwrap();
        fs::hard_link(r.0.join("public_probe.py"), alias.0.join("copy.py")).unwrap();
        assert!(r.inspect().is_err());
        fs::remove_file(alias.0.join("copy.py")).unwrap();
        fs::set_permissions(
            r.0.join("public_probe.py"),
            fs::Permissions::from_mode(0o666),
        )
        .unwrap();
        assert!(r.inspect().is_err());
    }
}

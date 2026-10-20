// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
//! Immutable HTTP capability derived from the owned, loaded plugin inventory.
use std::{collections::BTreeSet, sync::Arc};

#[derive(Clone)]
pub(crate) enum PluginAccess {
    Disabled,
    Synthetic,
    Operator(Arc<BTreeSet<String>>),
}
impl PluginAccess {
    pub(crate) fn allows(&self, name: &str, group: Option<&str>) -> bool {
        match self {
            Self::Disabled => false,
            Self::Synthetic => {
                name == "ergon_public_probe" && group.is_none_or(|group| group == "7075626c6963")
            }
            Self::Operator(names) => {
                names.contains(name)
                    && group.is_none_or(|group| {
                        !group.is_empty()
                            && group.len() <= 1024
                            && group.len() % 2 == 0
                            && group
                                .bytes()
                                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                    })
            }
        }
    }
    /// Preserve existing page bounds; only approved operator group enumeration
    /// additionally admits the existing upstream prefix/start cursor inputs.
    pub(crate) fn allows_query(&self, path: &str, query: Option<&str>) -> bool {
        let Some(query) = query else {
            return true;
        };
        if query.len() > 4096 {
            return false;
        }
        let segments: Vec<_> = path.trim_start_matches('/').split('/').collect();
        let operator_groups = match segments.as_slice() {
            ["plugin", name, "groups"] => {
                matches!(self, Self::Operator(names) if names.contains(*name))
            }
            _ => false,
        };
        let any_groups = matches!(segments.as_slice(), ["plugin", _, "groups"]);
        let mut names = BTreeSet::new();
        for pair in query.split('&') {
            let Some((key, value)) = pair.split_once('=') else {
                return false;
            };
            if !names.insert(key) {
                return false;
            }
            match key {
                "prefix" | "start" if operator_groups => {
                    if value.len() > 1024
                        || value.len() % 2 != 0
                        || !value
                            .bytes()
                            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
                    {
                        return false;
                    }
                }
                "page" | "page_size" => {
                    if value.is_empty() || !value.bytes().all(|c| c.is_ascii_digit()) {
                        return false;
                    }
                    let Ok(number) = value.parse::<u32>() else {
                        return false;
                    };
                    let max_size = if any_groups { 50 } else { 200 };
                    if (key == "page" && number > 1000)
                        || (key == "page_size" && (number == 0 || number > max_size))
                    {
                        return false;
                    }
                }
                _ => return false,
            }
        }
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn operator() -> PluginAccess {
        PluginAccess::Operator(Arc::new(BTreeSet::from([
            "public_alpha".into(),
            "public_beta".into(),
        ])))
    }
    #[test]
    fn disabled_admits_no_plugin() {
        for name in ["ergon_public_probe", "public_alpha", ""] {
            for group in [None, Some("7075626c6963"), Some("00")] {
                assert!(!PluginAccess::Disabled.allows(name, group));
            }
        }
    }
    #[test]
    fn synthetic_scope_unchanged() {
        assert!(PluginAccess::Synthetic.allows("ergon_public_probe", None));
        assert!(PluginAccess::Synthetic.allows("ergon_public_probe", Some("7075626c6963")));
        for (name, group) in [
            ("public_alpha", None),
            ("public_alpha", Some("7075626c6963")),
            ("ergon_public_probe", Some("00")),
            ("ergon_public_probe", Some("")),
        ] {
            assert!(!PluginAccess::Synthetic.allows(name, group));
        }
    }
    #[test]
    fn operator_only_loaded_names() {
        for name in ["public_alpha", "public_beta"] {
            assert!(operator().allows(name, None));
            assert!(operator().allows(name, Some("00aaff")));
        }
        for name in [
            "unloaded_public",
            "ergon_public_probe",
            "public_alpha/other",
            "Public_alpha",
            "",
        ] {
            assert!(!operator().allows(name, None));
            assert!(!operator().allows(name, Some("00")));
        }
    }
    #[test]
    fn invalid_groups_fail_closed() {
        for group in [
            "", "0", "abc", "ff/00", "%00", "AA", " 00", "é", "gg", "\0\0",
        ] {
            assert!(!operator().allows("public_alpha", Some(group)));
        }
    }
    #[test]
    fn group_boundary() {
        assert!(operator().allows("public_alpha", Some(&"ff".repeat(512))));
        assert!(!operator().allows("public_alpha", Some(&"ff".repeat(513))));
    }
    #[test]
    fn immutable_shared_capability() {
        let names = Arc::new(BTreeSet::from(["public_alpha".into()]));
        let capability = PluginAccess::Operator(names.clone());
        let clone = capability.clone();
        drop(capability);
        drop(names);
        assert!(clone.allows("public_alpha", Some("00")));
        assert!(!clone.allows("public_beta", Some("00")));
    }
}

#[cfg(test)]
mod query_tests {
    use super::*;
    const GROUPS: &str = "/plugin/public_alpha/groups";
    fn operator() -> PluginAccess {
        PluginAccess::Operator(Arc::new(BTreeSet::from(["public_alpha".into()])))
    }
    #[test]
    fn operator_prefix_cursor_and_empty_prefix() {
        for query in [
            "prefix=aabb&page_size=25",
            "prefix=aabb&start=aabb00&page_size=1",
            "prefix=&start=&page_size=50",
        ] {
            assert!(operator().allows_query(GROUPS, Some(query)));
        }
    }
    #[test]
    fn optional_cursor_does_not_leak_to_other_scopes() {
        for scope in [PluginAccess::Disabled, PluginAccess::Synthetic, operator()] {
            for path in [
                "/plugin/unloaded_public/groups",
                "/tx/00",
                "/script/other/51/history",
                "/plugin/public_alpha/00/history",
            ] {
                assert!(!scope.allows_query(path, Some("prefix=aa")));
            }
        }
        assert!(!PluginAccess::Synthetic
            .allows_query("/plugin/ergon_public_probe/groups", Some("prefix=aa")));
    }
    #[test]
    fn duplicate_and_unknown_query_keys_refused() {
        for query in [
            "prefix=aa&prefix=bb",
            "start=aa&start=bb",
            "page_size=1&page_size=2",
            "other=1",
            "prefix",
            "=",
            "",
        ] {
            assert!(!operator().allows_query(GROUPS, Some(query)));
        }
    }
    #[test]
    fn noncanonical_hex_and_encoded_delimiters_refused() {
        for query in [
            "prefix=a",
            "prefix=AA",
            "start=%00",
            "prefix=aa%26start=bb",
            "prefix=gg",
            "prefix=é",
            "prefix=aa=bb",
        ] {
            assert!(!operator().allows_query(GROUPS, Some(query)));
        }
    }
    #[test]
    fn group_limits_equal_existing_upstream_maximum() {
        for query in [
            "page_size=0",
            "page_size=51",
            "page_size=-1",
            "page_size=1.0",
            "page=1001",
            "page=4294967296",
        ] {
            assert!(!operator().allows_query(GROUPS, Some(query)));
        }
        assert!(operator().allows_query(GROUPS, Some("page_size=50&page=1000")));
    }
    #[test]
    fn legacy_page_contract_preserved() {
        for scope in [PluginAccess::Disabled, PluginAccess::Synthetic, operator()] {
            assert!(scope.allows_query("/tx/00", None));
            assert!(scope.allows_query("/script/other/51/history", Some("page=0&page_size=200")));
            assert!(!scope.allows_query("/script/other/51/history", Some("page_size=201")));
        }
    }
    #[test]
    fn individual_and_total_cursor_bounds() {
        assert!(operator().allows_query(
            GROUPS,
            Some(&format!(
                "prefix={}&start={}",
                "aa".repeat(512),
                "aa".repeat(512)
            ))
        ));
        assert!(!operator().allows_query(GROUPS, Some(&format!("prefix={}", "aa".repeat(513)))));
        assert!(!operator().allows_query(GROUPS, Some(&"x".repeat(4097))));
    }
    #[test]
    fn cursor_prefix_relation_remains_upstream_semantics() {
        // Upstream intentionally returns an empty result for a nonmatching
        // start. Admission must not reinterpret/rewrite the request.
        assert!(operator().allows_query(GROUPS, Some("prefix=aa&start=bb&page_size=1")));
    }
}

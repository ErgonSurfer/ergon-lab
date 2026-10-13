// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
//! Owned shutdown of the actual upstream service runtime, never timeout/detach.
use std::sync::Mutex;
use tokio::runtime::{Handle, Runtime};

pub(crate) struct JoinedRuntime {
    runtime: Mutex<Option<Runtime>>,
    // Taking Option alone is not a join barrier: a second interrupt/stop must
    // wait while the first caller is still dropping the runtime.
    shutdown: Mutex<()>,
}
impl JoinedRuntime {
    pub(crate) fn new(runtime: Runtime) -> Self {
        Self {
            runtime: Mutex::new(Some(runtime)),
            shutdown: Mutex::new(()),
        }
    }
    pub(crate) fn handle(&self) -> Option<Handle> {
        self.runtime
            .lock()
            .unwrap()
            .as_ref()
            .map(|r| r.handle().clone())
    }
    // Must run on an owner/control thread, not inside this Tokio runtime.
    // Runtime::drop waits for async task destruction AND all spawn_blocking
    // work. A stalled task retains ownership rather than allowing teardown.
    pub(crate) fn stop(&self) {
        let _serial = self.shutdown.lock().unwrap();
        let runtime = self.runtime.lock().unwrap().take();
        // Do not hold the handle mutex while joining: callbacks can observe
        // that no runtime remains without deadlocking behind shutdown.
        drop(runtime);
    }
}
impl Drop for JoinedRuntime {
    fn drop(&mut self) {
        self.stop();
    }
}

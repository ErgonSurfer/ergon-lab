// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#include <chronik-cpp/ergon_archival_worker.h>
#include <chronik-cpp/ergon_regtest_service.h>
#include <chronik_lib/src/ergon_archival.rs.h>
#include <config.h>
#include <fs.h>
#include <logging.h>
#include <memory>
#include <stdexcept>
#include <txmempool.h>
#include <util/system.h>
#include <validation.h>

namespace ergon_regtest {
namespace {
// Declaration order is intentional: the worker/HTTP owner dies before access.
std::unique_ptr<chronik_bridge::ErgonNodeAccess> access;
std::unique_ptr<ergon_archival::ArchivalWorker> worker;
}
void Start(const Config &config) noexcept {
    if (!gArgs.GetBoolArg("-chronikupstreamregtest", false))
        return;
    try {
        // No arbitrary index path or operator configuration is accepted.
        // The Rust adapter rejects redirected, unbound and failed roots.
        const auto root = GetDataDir() / "chronik-upstream-regtest";
        fs::create_directory(root);
        if (fs::canonical(root) != root || fs::is_symlink(root))
            throw std::runtime_error("Redirected regtest service root");
        access = std::make_unique<chronik_bridge::ErgonNodeAccess>(config, g_mempool);
        worker = std::make_unique<ergon_archival::ArchivalWorker>(
            fs::canonical(root).string(), *access);
        const auto port = worker->StartHttpFixture();
        LogPrintf("Chronik upstream regtest listening=127.0.0.1:%u plugins=0\n", port);
    } catch (...) {
        // A failure is index-only; no AbortNode, shutdown or validation veto.
        Stop();
        LogPrintf("Chronik upstream regtest unavailable node_continues=1\n");
    }
}
void Stop() {
    if (worker) {
        // Stop closes admission, drains registered callbacks and joins Rust,
        // HTTP/Tokio and native users before releasing their native resources.
        worker->Stop();
        worker.reset();
        LogPrintf("Chronik upstream regtest stopped joined=1\n");
    }
    access.reset();
}
} // namespace ergon_regtest

// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#include <chronik-cpp/ergon_archival_worker.h>
#include <chronik-cpp/ergon_regtest_service.h>
#include <chronik_lib/src/ergon_archival.rs.h>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <config.h>
#include <fs.h>
#include <logging.h>
#include <memory>
#include <mutex>
#include <shutdown.h>
#include <stdexcept>
#include <thread>
#include <txmempool.h>
#include <util/system.h>
#include <validation.h>

namespace ergon_regtest {
struct OwnedService::Impl {
    const Config &config;
    const std::function<void()> before_open;
    const bool public_plugin;
    std::atomic<bool> cancel{false}, failed{false}, joined{false};
    std::atomic<uint16_t> port{0};
    std::mutex mutex, stop_mutex;
    std::condition_variable cv;
    std::thread thread;
    Impl(const Config &value, std::function<void()> hook, bool plugin)
        : config(value), before_open(std::move(hook)), public_plugin(plugin),
          thread([this] { Run(); }) {}
    bool Cancelled() const { return cancel.load() || ShutdownRequested(); }
    void Pause() {
        std::unique_lock<std::mutex> lock(mutex);
        cv.wait_for(lock, std::chrono::milliseconds(50), [&] { return Cancelled(); });
    }
    void Run() noexcept {
        try {
            // ThreadImport restores the real pool. No import/DB/native lock
            // wait is allowed to hold AppInitMain or peer/RPC initialization.
            while (!g_mempool.IsLoaded() && !Cancelled())
                Pause();
            if (Cancelled()) return;
            if (before_open) before_open();
            if (Cancelled()) return;
            const auto root = GetDataDir() / "chronik-upstream-regtest";
            fs::create_directory(root);
            if (fs::canonical(root) != root || fs::is_symlink(root))
                throw std::runtime_error("Redirected regtest service root");
            // Exactly one upstream context, on this startup thread. The fixed
            // two-file fixture is checked BEFORE import on reopen. Never load
            // arbitrary plugin paths, modes, or adopt incompatible index roots.
            std::unique_ptr<rust::Box<ergon_archival::PluginFixture>> plugins;
            if (public_plugin) {
#ifdef ENABLE_CHRONIK_UPSTREAM_REGTEST_PLUGINS
                const auto inputs = GetDataDir() / "chronik-upstream-regtest-plugin-inputs";
                const bool created = fs::create_directory(inputs);
                if (fs::canonical(inputs) != inputs || fs::is_symlink(inputs))
                    throw std::runtime_error("Redirected public plugin inputs");
                plugins = std::make_unique<rust::Box<ergon_archival::PluginFixture>>(
                    created ? ergon_archival::make_plugin_fixture(inputs.string(), 0)
                            : ergon_archival::load_plugin_fixture(inputs.string(), 0));
#else
                throw std::runtime_error("Service plugin profile is not enabled");
#endif
            }
            // Worker/HTTP/DB users die before access AND plugin owner, and
            // before this thread is joined. No Python finalization/reinit.
            chronik_bridge::ErgonNodeAccess access(config, g_mempool);
            ergon_archival::ArchivalWorker worker(fs::canonical(root).string(), access,
                128, {}, {}, {}, {}, plugins ? &**plugins : nullptr, true);
            uint16_t listening = 0;
            while (!Cancelled() && !listening) {
                listening = worker.TryStartRegtestHttp();
                if (!listening) Pause();
            }
            if (listening && !Cancelled()) {
                port.store(listening);
                LogPrintf("Chronik upstream regtest listening=127.0.0.1:%u plugins=%u\n",
                          listening, public_plugin ? 1 : 0);
                while (!Cancelled() && !worker.Status().failed)
                    Pause();
            }
            const auto status = worker.Status();
            failed.store(status.failed);
            if (status.failed)
                LogPrintf("Chronik upstream regtest failure reason=%s accepted=%u completed=%u pending_at_failure=%u node_continues=1\n",
                          ergon_archival::WorkerFailureName(status.failure),
                          status.accepted, status.completed, status.failure_pending);
            port.store(0);
            worker.Stop();
            LogPrintf("Chronik upstream regtest stopped joined=1\n");
        } catch (...) {
            // Scope unwind joined worker/HTTP users before access destruction.
            // Index failure has no validation veto or node shutdown authority.
            if (!Cancelled()) {
                failed.store(true);
                LogPrintf("Chronik upstream regtest unavailable node_continues=1\n");
            }
        }
    }
    void Stop() {
        AssertLockNotHeld(cs_main);
        AssertLockNotHeld(g_mempool.cs);
        std::lock_guard<std::mutex> serial(stop_mutex);
        if (joined.load()) return;
        cancel.store(true);
        cv.notify_all();
        thread.join(); // No detach, timeout or destruction of borrowed owners.
        joined.store(true);
    }
};
OwnedService::OwnedService(const Config &config, std::function<void()> hook, bool plugin)
    : impl(std::make_unique<Impl>(config, std::move(hook), plugin)) {}
OwnedService::~OwnedService() { Stop(); }
ServiceStatus OwnedService::Status() const {
    return {impl->failed.load(), impl->cancel.load(), impl->joined.load(), impl->port.load()};
}
void OwnedService::Stop() { impl->Stop(); }
namespace { std::unique_ptr<OwnedService> service; }
void Start(const Config &config) noexcept {
    if (!gArgs.GetBoolArg("-chronikupstreamregtest", false)) return;
    try {
        bool plugin = false;
#ifdef ENABLE_CHRONIK_UPSTREAM_REGTEST_PLUGINS
        plugin = gArgs.GetBoolArg("-chronikupstreamregtestplugins", false);
#endif
        if (!service) service = std::make_unique<OwnedService>(config, std::function<void()>{}, plugin);
    } catch (...) {
        LogPrintf("Chronik upstream regtest unavailable node_continues=1\n");
    }
}
void Stop() { if (service) { service->Stop(); service.reset(); } }
} // namespace ergon_regtest

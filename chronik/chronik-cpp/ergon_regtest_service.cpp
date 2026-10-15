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
    std::atomic<bool> cancel{false}, failed{false}, joined{false};
    std::atomic<uint16_t> port{0};
    std::mutex mutex, stop_mutex;
    std::condition_variable cv;
    std::thread thread;
    Impl(const Config &value, std::function<void()> hook)
        : config(value), before_open(std::move(hook)), thread([this] { Run(); }) {}
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
            // All worker/HTTP/DB users die before access, and before this
            // owner can be joined. No arbitrary path or plugin configuration.
            chronik_bridge::ErgonNodeAccess access(config, g_mempool);
            ergon_archival::ArchivalWorker worker(fs::canonical(root).string(), access,
                128, {}, {}, {}, {}, nullptr, true);
            uint16_t listening = 0;
            while (!Cancelled() && !listening) {
                listening = worker.TryStartRegtestHttp();
                if (!listening) Pause();
            }
            if (listening && !Cancelled()) {
                port.store(listening);
                LogPrintf("Chronik upstream regtest listening=127.0.0.1:%u plugins=0\n", listening);
                while (!Cancelled() && !worker.Status().failed)
                    Pause();
            }
            failed.store(worker.Status().failed);
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
OwnedService::OwnedService(const Config &config, std::function<void()> hook)
    : impl(std::make_unique<Impl>(config, std::move(hook))) {}
OwnedService::~OwnedService() { Stop(); }
ServiceStatus OwnedService::Status() const {
    return {impl->failed.load(), impl->cancel.load(), impl->joined.load(), impl->port.load()};
}
void OwnedService::Stop() { impl->Stop(); }
namespace { std::unique_ptr<OwnedService> service; }
void Start(const Config &config) noexcept {
    if (!gArgs.GetBoolArg("-chronikupstreamregtest", false)) return;
    try {
        if (!service) service = std::make_unique<OwnedService>(config);
    } catch (...) {
        LogPrintf("Chronik upstream regtest unavailable node_continues=1\n");
    }
}
void Stop() { if (service) { service->Stop(); service.reset(); } }
} // namespace ergon_regtest

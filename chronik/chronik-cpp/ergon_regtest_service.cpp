// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#include <chronik-cpp/ergon_archival_worker.h>
#include <chronik-cpp/ergon_archival_probe.h>
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
#include <optional>
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
    const std::string operator_root, operator_binding;
    const std::optional<chronik_bridge::HistoricalReadBinding> historical;
    const bool historical_clients;
    const uint16_t requested_port;
    std::atomic<bool> cancel{false}, failed{false}, joined{false};
    std::atomic<uint16_t> port{0};
    std::mutex mutex, stop_mutex;
    std::condition_variable cv;
    std::thread thread;
    Impl(const Config &value, std::function<void()> hook, bool plugin,
         std::string root, std::string binding,
         std::optional<chronik_bridge::HistoricalReadBinding> historical_binding = {},
         bool clients = false, uint16_t listen_port = 0)
        : config(value), before_open(std::move(hook)), public_plugin(plugin),
          operator_root(std::move(root)), operator_binding(std::move(binding)),
          historical(std::move(historical_binding)),
          historical_clients(clients), requested_port(listen_port),
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
            std::unique_ptr<chronik_bridge::ErgonNodeAccess> access;
            if(historical) {
                access=historical_clients
                    ? std::make_unique<chronik_bridge::ErgonNodeAccess>(config,g_mempool,*historical,
                        chronik_bridge::HistoricalClientAdmission::Explicit)
                    : std::make_unique<chronik_bridge::ErgonNodeAccess>(config,g_mempool,*historical);
                // Network, active genesis, pruning and native resources are
                // checked before this profile creates any index directory.
                ergon_archival::archival_fixture_guard(*access);
            } else {
                access=std::make_unique<chronik_bridge::ErgonNodeAccess>(config,g_mempool,
                    chronik_bridge::NativeReadScope::ArchivalRegtestService,
                    gArgs.GetBoolArg("-chronikupstreamregtestclients",false));
            }
            const bool historical_plugins = historical && !operator_root.empty();
            const auto root = GetDataDir() / (historical_clients ? "chronik-upstream-service" :
                                             historical_plugins ? "chronik-upstream-shadow-plugins" :
                                             historical ? "chronik-upstream-shadow" : "chronik-upstream-regtest");
            fs::create_directory(root);
            if (fs::canonical(root) != root || fs::is_symlink(root))
                throw std::runtime_error("Redirected regtest service root");
            // One upstream context, owned until worker/HTTP join. Synthetic
            // fixture and explicitly bound trusted bundle are mutually exclusive.
            // No mode silently adopts or rebuilds an incompatible index.
            std::unique_ptr<rust::Box<ergon_archival::PluginFixture>> plugins;
            if (public_plugin && !operator_root.empty())
                throw std::runtime_error("Conflicting plugin input modes");
            if (!operator_root.empty()) {
#ifdef ENABLE_CHRONIK_UPSTREAM_REGTEST_PLUGINS
                plugins = std::make_unique<rust::Box<ergon_archival::PluginFixture>>(
                    historical ? ergon_archival::load_historical_operator_plugins(
                        operator_root, operator_binding, historical->Network())
                               : ergon_archival::load_operator_plugins(operator_root, operator_binding));
#else
                throw std::runtime_error("Service plugin profile is not enabled");
#endif
            } else if (public_plugin) {
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
            ergon_archival::ArchivalWorker worker(fs::canonical(root).string(), *access,
                128, {}, {}, {}, {}, plugins ? &**plugins : nullptr, true,
                historical_clients ? ergon_archival::WorkerProfile::HistoricalClientService :
                historical_plugins ? ergon_archival::WorkerProfile::HistoricalPluginsReadOnly :
                historical ? ergon_archival::WorkerProfile::HistoricalReadOnly
                           : ergon_archival::WorkerProfile::Inherited);
            uint16_t listening = 0;
            while (!Cancelled() && !listening) {
                listening = historical_clients ? worker.TryStartHistoricalClientsHttp(requested_port) :
                            historical ? worker.TryStartHistoricalHttp() : worker.TryStartRegtestHttp();
                if (!listening) Pause();
            }
            if (listening && !Cancelled()) {
                port.store(listening);
                LogPrintf("Chronik upstream %s listening=127.0.0.1:%u plugins=%u\n",
                          historical_clients ? "service" : historical ? "shadow" : "regtest",listening, plugins ? (**plugins).loaded() : 0);
                while (!Cancelled() && !worker.Status().failed)
                    Pause();
            }
            const auto status = worker.Status();
            failed.store(status.failed);
            if (status.failed)
                LogPrintf("Chronik upstream %s failure reason=%s accepted=%u completed=%u pending_at_failure=%u node_continues=1\n",
                          historical_clients ? "service" : historical ? "shadow" : "regtest",
                          ergon_archival::WorkerFailureName(status.failure),
                          status.accepted, status.completed, status.failure_pending);
            port.store(0);
            worker.Stop();
            LogPrintf("Chronik upstream %s stopped joined=1\n",historical_clients ? "service" : historical ? "shadow" : "regtest");
        } catch (...) {
            // Scope unwind joined worker/HTTP users before access destruction.
            // Index failure has no validation veto or node shutdown authority.
            if (!Cancelled()) {
                failed.store(true);
                LogPrintf("Chronik upstream %s unavailable node_continues=1\n",historical_clients ? "service" : historical ? "shadow" : "regtest");
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
OwnedService::OwnedService(const Config &config, std::function<void()> hook, bool plugin,
                           std::string root, std::string binding)
    : impl(std::make_unique<Impl>(config, std::move(hook), plugin, std::move(root), std::move(binding))) {}
OwnedService::~OwnedService() { Stop(); }
OwnedService::OwnedService(const Config &config, chronik_bridge::HistoricalReadBinding binding,
                           std::function<void()> hook, std::string root, std::string commitment)
    :impl(std::make_unique<Impl>(config,std::move(hook),false,std::move(root),
                                std::move(commitment),std::move(binding))) {}
ServiceStatus OwnedService::Status() const {
    return {impl->failed.load(), impl->cancel.load(), impl->joined.load(), impl->port.load()};
}
OwnedService::OwnedService(const Config &config, chronik_bridge::HistoricalReadBinding binding,
                           chronik_bridge::HistoricalClientAdmission admission, uint16_t port,
                           std::function<void()> hook, std::string root, std::string commitment) {
    if(admission!=chronik_bridge::HistoricalClientAdmission::Explicit || !binding.Matches(config))
        throw std::runtime_error("Invalid historical client service admission");
    impl=std::make_unique<Impl>(config,std::move(hook),false,std::move(root),
                               std::move(commitment),std::move(binding),true,port);
}
void OwnedService::Stop() { impl->Stop(); }
namespace { std::unique_ptr<OwnedService> service; }
void Start(const Config &config) noexcept {
    if (!gArgs.GetBoolArg("-chronikupstreamregtest", false)) return;
    try {
        bool plugin = false;
        std::string root, binding;
#ifdef ENABLE_CHRONIK_UPSTREAM_REGTEST_PLUGINS
        plugin = gArgs.GetBoolArg("-chronikupstreamregtestplugins", false);
        root = gArgs.GetArg("-chronikupstreamregtestpluginbundle", "");
        binding = gArgs.GetArg("-chronikupstreamregtestpluginbinding", "");
#endif
        if (!service) service = std::make_unique<OwnedService>(config, std::function<void()>{}, plugin, root, binding);
    } catch (...) {
        LogPrintf("Chronik upstream regtest unavailable node_continues=1\n");
    }
}
void Stop() { if (service) { service->Stop(); service.reset(); } }
} // namespace ergon_regtest
#ifdef ENABLE_CHRONIK_UPSTREAM_SERVICE
namespace ergon_continuous {
namespace { std::unique_ptr<ergon_regtest::OwnedService> service; }
void Start(const Config &config) noexcept {
    if(!gArgs.GetBoolArg("-chronikupstreamservice",false)) return;
    try {
        chronik_bridge::HistoricalReadBinding binding(config,
            gArgs.GetArg("-chronikupstreamservicenetwork",""),
            gArgs.GetArg("-chronikupstreamservicegenesis",""));
        std::string root, commitment;
#ifdef ENABLE_CHRONIK_UPSTREAM_REGTEST_PLUGINS
        root=gArgs.GetArg("-chronikupstreamservicepluginbundle","");
        commitment=gArgs.GetArg("-chronikupstreamservicepluginbinding","");
#endif
        if(!service) service=std::make_unique<ergon_regtest::OwnedService>(config,
            std::move(binding),chronik_bridge::HistoricalClientAdmission::Explicit,
            static_cast<uint16_t>(gArgs.GetArg("-chronikupstreamserviceport",int64_t{0})),
            std::function<void()>{},std::move(root),std::move(commitment));
    } catch(...) {
        LogPrintf("Chronik upstream service unavailable node_continues=1\n");
    }
}
void Stop() { if(service) { service->Stop();service.reset(); } }
}
#endif
#ifdef ENABLE_CHRONIK_UPSTREAM_SHADOW
namespace ergon_shadow {
namespace { std::unique_ptr<ergon_regtest::OwnedService> service; }
void Start(const Config &config) noexcept {
    if(!gArgs.GetBoolArg("-chronikupstreamshadow",false)) return;
    try {
        chronik_bridge::HistoricalReadBinding binding(config,
            gArgs.GetArg("-chronikupstreamshadownetwork",""),
            gArgs.GetArg("-chronikupstreamshadowgenesis",""));
        std::string root, commitment;
#ifdef ENABLE_CHRONIK_UPSTREAM_REGTEST_PLUGINS
        root = gArgs.GetArg("-chronikupstreamshadowpluginbundle", "");
        commitment = gArgs.GetArg("-chronikupstreamshadowpluginbinding", "");
#endif
        if(!service) service=std::make_unique<ergon_regtest::OwnedService>(
            config,std::move(binding),std::function<void()>{},std::move(root),std::move(commitment));
    } catch(...) {
        LogPrintf("Chronik upstream shadow unavailable node_continues=1\n");
    }
}
void Stop() { if(service) { service->Stop();service.reset(); } }
}
#endif

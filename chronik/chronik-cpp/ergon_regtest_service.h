// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#ifndef ERGON_REGTEST_SERVICE_H
#define ERGON_REGTEST_SERVICE_H
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
class Config;
namespace chronik_bridge { class HistoricalReadBinding; enum class HistoricalClientAdmission; }
namespace ergon_regtest {
struct ServiceStatus {
    bool failed, stopping, joined;
    uint16_t port;
};
// Config/native owners must outlive joined Stop. Construction schedules work
// only: no import wait, DB, disk or native lock on the calling thread.
class OwnedService {
    struct Impl;
    std::unique_ptr<Impl> impl;
  public:
    // Deterministic native fixture latch, never a daemon option.
    explicit OwnedService(const Config &config, std::function<void()> before_open = {},
                          bool public_plugin = false,
                          std::string operator_root = {}, std::string operator_binding = {});
    // Library-level explicit binding; the daemon entry remains separately
    // compile-gated. Keep this class definition identical in every host TU.
    OwnedService(const Config &config, chronik_bridge::HistoricalReadBinding binding,
                 std::function<void()> before_open = {},
                 std::string operator_root = {}, std::string operator_binding = {});
    OwnedService(const Config &config, chronik_bridge::HistoricalReadBinding binding,
                 chronik_bridge::HistoricalClientAdmission admission, uint16_t port,
                 std::function<void()> before_open = {},
                 std::string operator_root = {}, std::string operator_binding = {});
    ~OwnedService();
    OwnedService(const OwnedService &) = delete;
    OwnedService &operator=(const OwnedService &) = delete;
    ServiceStatus Status() const;
    void Stop();
};
// Main control thread only. Stop precedes scheduler/native-owner teardown.
void Start(const Config &config) noexcept;
void Stop();
} // namespace ergon_regtest
#ifdef ENABLE_CHRONIK_UPSTREAM_SERVICE
namespace ergon_continuous {
void Start(const Config &config) noexcept;
void Stop();
}
#endif
#ifdef ENABLE_CHRONIK_UPSTREAM_SHADOW
namespace ergon_shadow {
// Uses the same joined backend, but owns a distinct explicit profile and root.
void Start(const Config &config) noexcept;
void Stop();
}
#endif
#endif

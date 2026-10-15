// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#ifndef ERGON_REGTEST_SERVICE_H
#define ERGON_REGTEST_SERVICE_H
#include <cstdint>
#include <functional>
#include <memory>
class Config;
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
                          bool public_plugin = false);
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
#endif

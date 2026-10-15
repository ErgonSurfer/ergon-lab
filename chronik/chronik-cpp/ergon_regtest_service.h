// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers
#ifndef ERGON_REGTEST_SERVICE_H
#define ERGON_REGTEST_SERVICE_H
class Config;
namespace ergon_regtest {
// Main init/control thread only. No runtime capability in the ordinary build.
// Stop must precede scheduler teardown and destruction of native chain/coins.
void Start(const Config &config) noexcept;
void Stop();
} // namespace ergon_regtest
#endif

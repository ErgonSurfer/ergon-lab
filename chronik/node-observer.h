// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#ifndef BITCOIN_CHRONIK_NODE_OBSERVER_H
#define BITCOIN_CHRONIK_NODE_OBSERVER_H

#include <string>

namespace chronik {

/** Register the opt-in observer, persistent runtime and optional HTTP route. */
bool StartNodeObserver(const std::string &runtime_path, bool reset_runtime,
                       const std::string &token_service_address);

/** Unregister and destroy the observer after its callback queue is drained. */
void StopNodeObserver();

} // namespace chronik

#endif // BITCOIN_CHRONIK_NODE_OBSERVER_H

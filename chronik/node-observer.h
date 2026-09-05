// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#ifndef BITCOIN_CHRONIK_NODE_OBSERVER_H
#define BITCOIN_CHRONIK_NODE_OBSERVER_H

namespace chronik {

/** Register the opt-in in-memory block observer. */
bool StartNodeObserver();

/** Unregister and destroy the observer after its callback queue is drained. */
void StopNodeObserver();

} // namespace chronik

#endif // BITCOIN_CHRONIK_NODE_OBSERVER_H

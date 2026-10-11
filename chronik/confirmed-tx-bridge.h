// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#ifndef BITCOIN_CHRONIK_CONFIRMED_TX_BRIDGE_H
#define BITCOIN_CHRONIK_CONFIRMED_TX_BRIDGE_H

#include <array>
#include <chronik/confirmed-tx-reader.h>

namespace chronik {

/** Fixed C ABI layout shared with chronik-observer/src/tx_bridge.rs. */
struct RuntimeTxLocation {
  uint64_t success;
  uint64_t error_code;
  uint64_t present;
  int64_t height;
  int64_t timestamp;
  uint64_t is_coinbase;
  std::array<uint8_t, 32> hash;
};
struct RuntimeSpentCoin {
  int64_t sats;
  int32_t height;
  uint32_t is_coinbase;
  const uint8_t *script;
  size_t script_len;
};
struct RuntimeTxQuery {
  uint64_t success;
  uint64_t error_code;
  uint64_t present;
  uint64_t payload_len;
};
static_assert(sizeof(RuntimeTxLocation) == 80);
static_assert(sizeof(RuntimeSpentCoin) == 32);
static_assert(sizeof(RuntimeTxQuery) == 32);

extern "C" {
RuntimeTxLocation chronik_runtime_tx_location(const void *runtime,
                                              const uint8_t *txid);
RuntimeTxQuery chronik_runtime_confirmed_tx(
    const void *runtime, const uint8_t *raw_tx, size_t raw_tx_len,
    const RuntimeSpentCoin *coins, size_t coin_count,
    const RuntimeTxLocation *location, uint8_t *output, size_t output_capacity);
}

/** Resolve a confirmed tx's indexed location without reading node storage. */
std::optional<RuntimeTxLocation> LookupConfirmedTxLocation(const void *runtime,
                                                           const TxId &txid,
                                                           std::string &error);

/** Encode one canonical node-storage result using the accepted token index.
 * The caller must keep the runtime alive and serialize against mutations for
 * the complete lookup/read/encode operation. No HTTP route calls this yet.
 */
std::optional<std::vector<uint8_t>>
EncodeConfirmedTxProto(const void *runtime, const ConfirmedTx &confirmed,
                       std::string &error);

} // namespace chronik
#endif // BITCOIN_CHRONIK_CONFIRMED_TX_BRIDGE_H

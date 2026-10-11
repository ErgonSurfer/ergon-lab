// SPDX-License-Identifier: MIT
// Copyright (c) 2026 The Ergon developers

#include <algorithm>
#include <chronik/confirmed-tx-bridge.h>
#include <limits>
#include <streams.h>
#include <version.h>

namespace chronik {
namespace {
constexpr size_t MAX_QUERY_BYTES = 64 * 1024 * 1024;
constexpr size_t MAX_QUERY_COINS = 1000000;
} // namespace

std::optional<RuntimeTxLocation> LookupConfirmedTxLocation(const void *runtime,
                                                           const TxId &txid,
                                                           std::string &error) {
  const auto location = chronik_runtime_tx_location(runtime, txid.begin());
  if (location.success != 1 || location.error_code != 0) {
    error = "confirmed transaction location query failed";
    return std::nullopt;
  }
  if (location.present != 1) {
    error = "transaction is absent from the accepted index";
    return std::nullopt;
  }
  if (location.height < 0 ||
      location.height > std::numeric_limits<int32_t>::max() ||
      location.is_coinbase > 1) {
    error = "invalid indexed transaction location";
    return std::nullopt;
  }
  error.clear();
  return location;
}

std::optional<std::vector<uint8_t>>
EncodeConfirmedTxProto(const void *runtime, const ConfirmedTx &confirmed,
                       std::string &error) {
  if (!confirmed.tx || confirmed.block_height < 0 ||
      confirmed.tx->IsCoinBase() != (confirmed.tx_index == 0) ||
      (confirmed.tx->IsCoinBase() && !confirmed.spent_coins.empty()) ||
      (!confirmed.tx->IsCoinBase() &&
       confirmed.spent_coins.size() != confirmed.tx->vin.size()) ||
      confirmed.spent_coins.size() > MAX_QUERY_COINS) {
    error = "invalid canonical confirmed transaction result";
    return std::nullopt;
  }
  CDataStream raw_tx(SER_NETWORK, PROTOCOL_VERSION);
  raw_tx << *confirmed.tx;
  if (raw_tx.size() > MAX_QUERY_BYTES) {
    error = "confirmed transaction exceeds bridge byte limit";
    return std::nullopt;
  }
  RuntimeTxLocation location{};
  location.success = location.present = 1;
  location.height = confirmed.block_height;
  location.timestamp = confirmed.block_timestamp;
  location.is_coinbase = confirmed.tx->IsCoinBase();
  std::copy(confirmed.block_hash.begin(), confirmed.block_hash.end(),
            location.hash.begin());
  std::vector<RuntimeSpentCoin> coins;
  coins.reserve(confirmed.spent_coins.size());
  size_t script_bytes = 0;
  for (const Coin &coin : confirmed.spent_coins) {
    if (coin.IsSpent()) {
      error = "confirmed transaction has an unavailable spent coin";
      return std::nullopt;
    }
    const auto &output = coin.GetTxOut();
    const auto &script = output.scriptPubKey;
    if (script.size() > MAX_QUERY_BYTES - script_bytes) {
      error = "spent coin scripts exceed bridge byte limit";
      return std::nullopt;
    }
    script_bytes += script.size();
    coins.push_back({output.nValue / FIXOSHI, int32_t(coin.GetHeight()),
                     uint32_t(coin.IsCoinBase()),
                     script.empty() ? nullptr : script.data(), script.size()});
  }
  const auto *coin_data = coins.empty() ? nullptr : coins.data();
  const auto *raw_data = reinterpret_cast<const uint8_t *>(raw_tx.data());
  const auto probe =
      chronik_runtime_confirmed_tx(runtime, raw_data, raw_tx.size(), coin_data,
                                   coins.size(), &location, nullptr, 0);
  if (probe.success != 1 || probe.error_code != 0 || probe.present != 1 ||
      probe.payload_len == 0 || probe.payload_len > MAX_QUERY_BYTES) {
    error = "confirmed transaction protobuf query failed";
    return std::nullopt;
  }
  std::vector<uint8_t> payload(probe.payload_len);
  const auto copied = chronik_runtime_confirmed_tx(
      runtime, raw_data, raw_tx.size(), coin_data, coins.size(), &location,
      payload.data(), payload.size());
  if (copied.success != 1 || copied.error_code != 0 || copied.present != 1 ||
      copied.payload_len != probe.payload_len) {
    error = "confirmed transaction protobuf changed during copy";
    return std::nullopt;
  }
  error.clear();
  return payload;
}
} // namespace chronik

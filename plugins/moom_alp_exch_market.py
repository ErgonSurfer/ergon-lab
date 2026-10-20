# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers

from chronik_plugin.plugin import Plugin, PluginOutput
from chronik_plugin.script import CScriptInvalidError, OP_RETURN


class MoomAlpExchMarketPlugin(Plugin):
    """Indexes MOOM EXCH market candidates.

    This native Chronik plugin is intentionally candidate-based. MOOM still
    re-validates referenced locks, ALP token id/amounts, covenant scripts, and
    spend state before projecting a market row, fill, claim, refund, or take.
    """

    GROUP_EXCH_V1 = b"EXCH\x01"
    GROUP_EXCH_PBID = b"EXCH\x01PBID"
    GROUP_EXCH_PACC = b"EXCH\x01PACC"
    TOKEN_PREFIX = b"EXCT"

    def lokad_id(self):
        return b"EXCH"

    def version(self):
        return "0.2.1"

    def run(self, tx):
        if not tx.outputs:
            return []
        try:
            ops = list(tx.outputs[0].script)
        except CScriptInvalidError:
            return []
        if len(ops) < 4:
            return []
        if ops[0] != OP_RETURN:
            return []
        if ops[1] != b"EXCH" or ops[2] != b"\x01":
            return []
        if ops[3] == b"SELL":
            return self._index_sell(tx, ops)
        if ops[3] == b"PBID":
            return self._index_protected_bid_intent(tx, ops)
        if ops[3] == b"PACC":
            return self._index_protected_bid_accept(tx, ops)
        return []

    def _output_index(self, tx):
        return 1 if len(tx.outputs) > 1 else 0

    def _index_sell(self, tx, ops):
        if len(ops) < 10:
            return []
        if not all(isinstance(value, bytes) for value in ops[4:10]):
            return []
        lock_txid = ops[4]
        lock_vout = ops[5]
        sell_atoms = ops[6]
        buy_sats = ops[7]
        receive_pkh = ops[8]
        cancel_pkh = ops[9] if len(ops) > 9 else b""
        if (
            len(lock_txid) != 32
            or len(lock_vout) != 4
            or len(sell_atoms) != 8
            or len(buy_sats) != 8
            or len(receive_pkh) != 20
            or len(cancel_pkh) != 20
        ):
            return []
        if len(tx.outputs) < 2:
            return []
        data = b"|".join(
            [
                lock_txid,
                lock_vout,
                sell_atoms,
                buy_sats,
                receive_pkh,
                cancel_pkh,
            ]
        )
        return [
            PluginOutput(
                idx=self._output_index(tx),
                groups=[self.GROUP_EXCH_V1],
                data=[data],
            )
        ]

    def _index_protected_bid_intent(self, tx, ops):
        if len(ops) != 12:
            return []
        if not all(isinstance(value, bytes) for value in ops[4:12]):
            return []
        bid_id = ops[4]
        token_id = ops[5]
        buy_atoms = ops[6]
        pay_sats = ops[7]
        secret_hash = ops[8]
        buyer_claim_pubkey = ops[9]
        buyer_refund_pubkey = ops[10]
        buyer_refund_locktime = ops[11]
        if (
            len(bid_id) != 32
            or len(token_id) != 32
            or len(buy_atoms) != 8
            or len(pay_sats) != 8
            or len(secret_hash) != 32
            or len(buyer_claim_pubkey) != 33
            or len(buyer_refund_pubkey) != 33
            or len(buyer_refund_locktime) != 4
        ):
            return []
        data = b"|".join(
            [
                b"PBID",
                bid_id,
                token_id,
                buy_atoms,
                pay_sats,
                secret_hash,
                buyer_refund_locktime,
            ]
        )
        return [
            PluginOutput(
                idx=self._output_index(tx),
                groups=[
                    self.GROUP_EXCH_PBID,
                    self.TOKEN_PREFIX + token_id,
                ],
                data=[data],
            )
        ]

    def _index_protected_bid_accept(self, tx, ops):
        if len(ops) != 17:
            return []
        if not all(isinstance(value, bytes) for value in ops[4:17]):
            return []
        bid_id = ops[4]
        escrow_txid = ops[5]
        escrow_vout = ops[6]
        token_lock_txid = ops[7]
        token_lock_vout = ops[8]
        token_id = ops[9]
        sell_atoms = ops[10]
        pay_sats = ops[11]
        secret_hash = ops[12]
        buyer_claim_pubkey = ops[13]
        seller_refund_pubkey = ops[14]
        seller_claim_pubkey = ops[15]
        seller_refund_locktime = ops[16]
        if (
            len(bid_id) != 32
            or len(escrow_txid) != 32
            or len(escrow_vout) != 4
            or len(token_lock_txid) != 32
            or len(token_lock_vout) != 4
            or len(token_id) != 32
            or len(sell_atoms) != 8
            or len(pay_sats) != 8
            or len(secret_hash) != 32
            or len(buyer_claim_pubkey) != 33
            or len(seller_refund_pubkey) != 33
            or len(seller_claim_pubkey) != 33
            or len(seller_refund_locktime) != 4
        ):
            return []
        data = b"|".join(
            [
                b"PACC",
                bid_id,
                escrow_txid,
                escrow_vout,
                token_lock_txid,
                token_lock_vout,
                token_id,
                sell_atoms,
                pay_sats,
                secret_hash,
                seller_refund_locktime,
            ]
        )
        return [
            PluginOutput(
                idx=self._output_index(tx),
                groups=[
                    self.GROUP_EXCH_PACC,
                    self.TOKEN_PREFIX + token_id,
                ],
                data=[data],
            )
        ]

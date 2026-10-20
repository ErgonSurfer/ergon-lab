# SPDX-License-Identifier: MIT
# Copyright (c) 2026 The Ergon developers

from chronik_plugin.plugin import Plugin, PluginOutput
from chronik_plugin.script import CScriptInvalidError, OP_RETURN


class MoomRewardClaimIndexPlugin(Plugin):
    """Indexes MOOM reward claim carrier candidates.

    This plugin is intentionally read-model only. It indexes structurally valid
    MCLM v0 OP_RETURN carriers so MOOM can refetch the raw transaction and
    validate claim policy, provider proof, idempotency, caps, signing, payout,
    and confirmation in the local reward validator ecology.
    """

    GROUP_CLAIM_V0 = b"MCLM\x00"
    MAX_STANDARD_OP_RETURN_BYTES = 223
    MIN_CARRIER_BYTES = 68
    MAX_CIPHERTEXT_BYTES = 80
    PROTOCOL_MAGIC = b"MCLM"

    def lokad_id(self):
        return b"MCLM"

    def version(self):
        return "0.1.1"

    def run(self, tx):
        outputs = []
        for idx, output in enumerate(tx.outputs):
            carrier = self._op_return_payload(output.script)
            if carrier is None:
                continue
            if not self._valid_carrier(carrier):
                continue
            data = [
                b"version=" + str(carrier[4]).encode("ascii"),
                b"kind=" + str(carrier[5]).encode("ascii"),
            ]
            outputs.append(
                PluginOutput(
                    idx=idx,
                    groups=[self.GROUP_CLAIM_V0],
                    data=data,
                )
            )
        return outputs

    def _op_return_payload(self, script):
        try:
            ops = list(script)
        except CScriptInvalidError:
            return None
        if len(ops) == 2 and ops[0] == OP_RETURN:
            return ops[1]
        if len(ops) >= 3 and ops[0] == OP_RETURN and ops[1] == self.PROTOCOL_MAGIC:
            return ops[2]

        raw = bytes(script)
        if len(raw) < 2 or raw[0] != OP_RETURN:
            return None
        payloads = self._pushes(raw[1:])
        if len(payloads) == 1:
            return payloads[0]
        if len(payloads) >= 2 and payloads[0] == self.PROTOCOL_MAGIC:
            return payloads[1]
        return None

    def _pushes(self, raw):
        pushes = []
        cursor = 0
        while cursor < len(raw):
            op = raw[cursor]
            cursor += 1
            if op <= 75:
                payload_len = op
            elif op == 0x4C:
                if cursor >= len(raw):
                    return []
                payload_len = raw[cursor]
                cursor += 1
            else:
                return []
            end = cursor + payload_len
            if end > len(raw):
                return []
            pushes.append(raw[cursor:end])
            cursor = end
        return pushes

    def _valid_carrier(self, carrier):
        if not isinstance(carrier, bytes):
            return False
        if len(carrier) < self.MIN_CARRIER_BYTES:
            return False
        if len(carrier) > self.MAX_STANDARD_OP_RETURN_BYTES:
            return False
        if carrier[0:4] != b"MCLM":
            return False
        if carrier[4] != 0:
            return False
        cursor = 6
        cursor += 16  # campaign hash
        cursor += 16  # claimant commitment
        cursor += 12  # nonce
        if len(carrier) <= cursor:
            return False
        ciphertext_len = carrier[cursor]
        cursor += 1
        if ciphertext_len <= 0:
            return False
        if ciphertext_len > self.MAX_CIPHERTEXT_BYTES:
            return False
        expected_len = cursor + ciphertext_len + 16
        return len(carrier) == expected_len

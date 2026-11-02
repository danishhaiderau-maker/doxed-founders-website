"""Laptop Bitfinex/live policy gate — HM AMBER replacement for bitfinex-production-gate."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import local_bitfinex_live_policy_gate as gate
import local_fresh_runtime as lfr


class TestLocalBitfinexLivePolicyGate(unittest.TestCase):
    def _base(self) -> dict:
        return {
            "LOCAL_FRESH_START": "1",
            "LOCAL_FRESH_START_CONFIRM": lfr.LOCAL_FRESH_CONFIRMATION,
            "FORCE_PAPER_MODE": "1",
            "BITFINEX_LIVE_ENABLED": "0",
        }

    def test_pass_when_paper_and_no_keys(self):
        v = gate.evaluate_gate(self._base())
        self.assertTrue(v["ok"])
        self.assertEqual(v["reasons"], [])
        self.assertEqual(v["replaces_workflow"], gate.REPLACES_WORKFLOW)
        self.assertIn("HM AMBER", v["hm_amber_cite"])

    def test_fail_closed_on_trade_key_name(self):
        env = self._base()
        env["BITFINEX_API_SECRET"] = "MUST_NOT_APPEAR_IN_OUTPUT"
        v = gate.evaluate_gate(env)
        self.assertFalse(v["ok"])
        blob = json.dumps(v)
        self.assertIn("FORBIDDEN_TRADE_KEYS_PRESENT:BITFINEX_API_SECRET", blob)
        self.assertNotIn("MUST_NOT_APPEAR_IN_OUTPUT", blob)

    def test_fail_closed_on_live_arm_bit(self):
        env = self._base()
        env["LIVE_COPY_ARMED"] = "true"
        v = gate.evaluate_gate(env)
        self.assertFalse(v["ok"])
        self.assertTrue(any(r.startswith("LIVE_ARM_BIT_TRUE:LIVE_COPY_ARMED") for r in v["reasons"]))

    def test_fail_without_force_paper(self):
        env = self._base()
        env["FORCE_PAPER_MODE"] = "0"
        v = gate.evaluate_gate(env)
        self.assertFalse(v["ok"])
        self.assertIn("FORCE_PAPER_MODE_REQUIRED", v["reasons"])

    def test_receipt_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gate.json"
            v = gate.run_gate(
                self._base(),
                receipt_path=path,
                require_local_fresh_confirm=True,
            )
            self.assertTrue(v["ok"])
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(body["gate_id"], gate.GATE_ID)
            self.assertTrue(body["ok"])


if __name__ == "__main__":
    unittest.main()

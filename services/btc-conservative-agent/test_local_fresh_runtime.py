"""Day-1 local fresh-start runtime gates (paper / no trade keys / health JSON)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import local_fresh_runtime as lfr


class TestLocalFreshRuntime(unittest.TestCase):
    def test_confirmation_required(self):
        env = {"LOCAL_FRESH_START": "1"}
        self.assertFalse(lfr.is_local_fresh_start(env))
        env["LOCAL_FRESH_START_CONFIRM"] = lfr.LOCAL_FRESH_CONFIRMATION
        self.assertTrue(lfr.is_local_fresh_start(env))

    def test_validate_refuses_trade_keys_by_name_only(self):
        env = {
            "LOCAL_FRESH_START": "1",
            "LOCAL_FRESH_START_CONFIRM": lfr.LOCAL_FRESH_CONFIRMATION,
            "FORCE_PAPER_MODE": "1",
            "BITFINEX_LIVE_ENABLED": "0",
            "BITFINEX_API_KEY": "REDACTED_SHOULD_NOT_PRINT",
        }
        ok, reasons = lfr.validate_paper_safe_env(env)
        self.assertFalse(ok)
        joined = ";".join(reasons)
        self.assertIn("FORBIDDEN_TRADE_KEYS_PRESENT:BITFINEX_API_KEY", joined)
        self.assertNotIn("REDACTED_SHOULD_NOT_PRINT", joined)

    def test_validate_refuses_live_and_fly_app(self):
        env = {
            "LOCAL_FRESH_START": "1",
            "LOCAL_FRESH_START_CONFIRM": lfr.LOCAL_FRESH_CONFIRMATION,
            "FORCE_PAPER_MODE": "1",
            "BITFINEX_LIVE_ENABLED": "1",
            "FLY_APP_NAME": "doxed-btc-bot",
        }
        ok, reasons = lfr.validate_paper_safe_env(env)
        self.assertFalse(ok)
        self.assertIn("BITFINEX_LIVE_MUST_BE_OFF", reasons)
        self.assertIn("FLY_APP_NAME_MUST_BE_UNSET", reasons)

    def test_apply_forces_paper_and_clears_fly(self):
        env = {
            "LOCAL_FRESH_START": "1",
            "LOCAL_FRESH_START_CONFIRM": lfr.LOCAL_FRESH_CONFIRMATION,
            "FLY_APP_NAME": "doxed-btc-bot",
            "FLY_MACHINE_ID": "x",
            "LIVE_COPY_ARMED": "1",
            "BITFINEX_LIVE_ENABLED": "1",
        }
        out = lfr.apply_local_fresh_environ(env)
        self.assertEqual(out["FORCE_PAPER_MODE"], "1")
        self.assertEqual(out["BITFINEX_LIVE_ENABLED"], "0")
        self.assertEqual(out["HOME_BOT_LOCAL"], "1")
        self.assertNotIn("FLY_APP_NAME", out)
        self.assertNotIn("LIVE_COPY_ARMED", out)
        self.assertTrue(out["DASHBOARD_PUBLIC_URL"].startswith("http://127.0.0.1:"))

    def test_lanes_default_ctrl_gs01_only(self):
        self.assertEqual(
            lfr.allowed_lanes({}),
            (
                "FAMILY_RANDOM_CONTROL_TAKER_90",
                "FAMILY_GS01_XV_PREMIUM_ATR_TP",
            ),
        )
        self.assertTrue(lfr.lane_allowed("FAMILY_GS01_XV_PREMIUM_ATR_TP"))
        self.assertFalse(lfr.lane_allowed("FAMILY_GSB1_CVD_DIV_REGIME"))

    def test_write_health_json_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "logs" / "local_fresh_health.json"
            payload = lfr.build_health_payload(
                ok=True,
                data_dir=str(Path(tmp) / "data"),
                gap_log_path=str(Path(tmp) / "data" / "gap_log.jsonl"),
                now=1_700_000_000.0,
            )
            written = lfr.write_health_json(path, payload)
            self.assertEqual(written, str(path.resolve()))
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(body["schema"], lfr.HEALTH_SCHEMA)
            self.assertTrue(body["ok"])
            self.assertTrue(body["force_paper_mode"])
            self.assertFalse(body["bitfinex_live_enabled"])
            self.assertTrue(body["disarmed"])
            self.assertEqual(body["lane_labels"], ["CTRL", "GS-01"])


if __name__ == "__main__":
    unittest.main()

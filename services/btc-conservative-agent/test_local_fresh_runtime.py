"""Day-1 local fresh-start runtime gates (paper / no trade keys / health JSON)."""
from __future__ import annotations

import json
import os
import sys
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

    def test_health_reads_real_env_and_exposes_tick_disk_rss(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "logs" / "local_fresh_health.json"
            data_dir = Path(tmp) / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            env = {
                "FORCE_PAPER_MODE": "1",
                "BITFINEX_LIVE_ENABLED": "0",
                "LOCAL_FRESH_MIN_DISK_FREE_BYTES": "1",
            }
            payload = lfr.build_health_payload(
                ok=True,
                data_dir=str(data_dir),
                gap_log_path=str(data_dir / "gap_log.jsonl"),
                now=1_700_000_100.0,
                last_tick_ts=1_700_000_000.0,
                environ=env,
            )
            written = lfr.write_health_json(path, payload)
            self.assertEqual(written, str(path.resolve()))
            body = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(body["schema"], lfr.HEALTH_SCHEMA)
            self.assertTrue(body["ok"])
            self.assertTrue(body["force_paper_mode"])
            self.assertTrue(body["paper_forced"])
            self.assertFalse(body["bitfinex_live_enabled"])
            self.assertTrue(body["disarmed"])
            self.assertEqual(body["last_tick_age_s"], 100.0)
            self.assertIn("disk_free_bytes", body)
            self.assertIn("rss_bytes", body)
            self.assertTrue(body["gaps_never_filled"])
            self.assertEqual(body["lane_labels"], ["CTRL", "GS-01"])

    def test_health_disarmed_false_when_live_arm_env_set(self):
        env = {
            "FORCE_PAPER_MODE": "1",
            "BITFINEX_LIVE_ENABLED": "0",
            "LIVE_COPY_ARMED": "1",
        }
        payload = lfr.build_health_payload(ok=True, environ=env, now=1.0)
        self.assertFalse(payload["disarmed"])
        self.assertTrue(payload["live_copy_armed"])
        self.assertFalse(payload["ok"])

    def test_series_break_created_once_and_refuses_unmarked_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = lfr.ensure_series_break(root, now=1_700_000_000.0, source_git_rev="abc123def456")
            self.assertEqual(first["schema"], lfr.SERIES_BREAK_SCHEMA)
            self.assertEqual(first["fly_era_data"], "CLOSED")
            # Strategist P5: series-start stamp = first local epoch id + timestamp.
            self.assertEqual(first["series_start_ts"], 1_700_000_000.0)
            self.assertEqual(first["series_start_utc"], first["series_break_utc"])
            self.assertEqual(
                first["local_epoch_id"],
                "local-fresh-20231114T221320Z-abc123def456",
            )
            second = lfr.ensure_series_break(root, now=1_800_000_000.0)
            self.assertEqual(second["series_break_ts"], first["series_break_ts"])
            self.assertEqual(second["local_epoch_id"], first["local_epoch_id"])
            # Unmarked Fly-era sqlite in a fresh dir without series_break → refuse.
            other = Path(tmp) / "fly_era"
            other.mkdir()
            (other / "local_market.sqlite3").write_bytes(b"not-empty")
            with self.assertRaises(RuntimeError) as ctx:
                lfr.ensure_series_break(other)
            self.assertIn("SERIES_BREAK_REQUIRED", str(ctx.exception))

    def test_windows_rss_helper_returns_int_or_none(self):
        # On Linux CI this returns None (no WinDLL); shape must stay Optional[int].
        value = lfr._windows_rss_bytes()
        self.assertTrue(value is None or (isinstance(value, int) and value > 0))

    def test_process_rss_bytes_prefers_linux_statm(self):
        rss = lfr.process_rss_bytes()
        # Linux agents always have /proc; Windows path covered by _windows_rss_bytes.
        if sys.platform.startswith("linux"):
            self.assertIsInstance(rss, int)
            self.assertGreater(rss, 0)


class TestStartLocalPaperAutostartPreflight(unittest.TestCase):
    def test_skip_host_preflight_removed_entirely(self):
        # RK 09:12: host-preflight skip switch is a violation — param + flag gone.
        root = Path(__file__).resolve().parents[2]
        script = root / "scripts" / "start-local-paper-bot.ps1"
        text = script.read_text(encoding="utf-8")
        self.assertNotIn("[switch]$SkipHostPreflight", text)
        self.assertNotIn("-$SkipHostPreflight", text)
        self.assertNotRegex(text, r"(?m)^\s*\[switch\]\$SkipHostPreflight\b")
        # Autostart must invoke boot with -NoWait only (preflight always runs).
        start = text.index("function Install-LocalPaperAutostart")
        end = text.index("function Uninstall-LocalPaperAutostart")
        block = text[start:end]
        self.assertIn("-NoWait", block)
        self.assertNotIn("SkipHostPreflight", block)
        # Param block must not declare the removed switch.
        param_end = text.index(")", text.index("param("))
        param_block = text[text.index("param(") : param_end + 1]
        self.assertNotIn("SkipHostPreflight", param_block)

    def test_disk_headroom_fails_closed_when_unmeasurable(self):
        ok, free, minimum = lfr.ensure_disk_headroom("/nonexistent/path/that/cannot/stat")
        # shutil.disk_usage may still resolve a parent on some OSes; force None path:
        # monkey via missing measure: call with a path that raises — covered by
        # wrapping disk_free_bytes return.
        self.assertIsInstance(minimum, int)
        # Direct fail-closed contract when free is None:
        original = lfr.disk_free_bytes
        try:
            lfr.disk_free_bytes = lambda path: None  # type: ignore[assignment]
            ok2, free2, _ = lfr.ensure_disk_headroom(".")
            self.assertFalse(ok2)
            self.assertIsNone(free2)
        finally:
            lfr.disk_free_bytes = original  # type: ignore[assignment]

    def test_uninstall_autostart_disables_only_never_unregisters(self):
        # HM AMBER: -UninstallAutostart must Disable-ScheduledTask only; record previous state.
        root = Path(__file__).resolve().parents[2]
        script = root / "scripts" / "start-local-paper-bot.ps1"
        text = script.read_text(encoding="utf-8")
        start = text.index("function Uninstall-LocalPaperAutostart")
        # Function ends at next top-level if/function after its body — take until Install dispatch.
        end = text.index("\nif ($UninstallAutostart)", start)
        block = text[start:end]
        self.assertIn("Disable-ScheduledTask", block)
        self.assertIn("previous_state", block)
        self.assertIn("local_paper_autostart_disable_receipt.json", block)
        self.assertNotIn("Unregister-ScheduledTask", block)
        self.assertNotRegex(block, r"(?m)^\s*Unregister-ScheduledTask\b")
        self.assertIn("deleted = $false", block)


if __name__ == "__main__":
    unittest.main()

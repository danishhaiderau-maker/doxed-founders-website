"""Local-fresh :9011 checks — no Fly probes; alert policy (RK 19:23)."""
from __future__ import annotations

import unittest

import local_fresh_health9011 as h


class TestLocalFreshHealth9011(unittest.TestCase):
    def test_features_declare_no_fly_and_alert_policy(self):
        self.assertIn("no_fly_probes", h.FEATURES)
        self.assertIn("alert_engine_or_live_safety_only", h.FEATURES)

    def test_fly_probes_check_is_skip(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            data = Path(tmp) / "data"
            data.mkdir()
            report = h.build_report(
                data_dir=data,
                logs_dir=logs,
                environ={
                    "FORCE_PAPER_MODE": "1",
                    "BITFINEX_LIVE_ENABLED": "0",
                },
                engine_url="http://127.0.0.1:9/health",  # fail closed → engine RED
                railway_url="http://127.0.0.1:9/api/health",
                vercel_url="http://127.0.0.1:9/",
            )
        ids = [c["id"] for c in report["checks"]]
        self.assertIn("fly.probes", ids)
        fly = next(c for c in report["checks"] if c["id"] == "fly.probes")
        self.assertEqual(fly["status"], h.SKIP)
        self.assertNotIn("fly.dev", str(report).lower()[:50] + "x")  # no probe URL required
        # Engine down → alert
        kinds = {a["kind"] for a in report["alerts"]}
        self.assertIn("ENGINE_DOWN", kinds)
        # Railway/Vercel failure must not alone create alerts
        self.assertTrue(all(a["kind"] in ("ENGINE_DOWN", "LIVE_SAFETY", "LIVE_SAFETY_CHANGED") for a in report["alerts"]))

    def test_live_safety_red_alerts(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            data = Path(tmp) / "data"
            data.mkdir()
            report = h.build_report(
                data_dir=data,
                logs_dir=logs,
                environ={
                    "FORCE_PAPER_MODE": "0",
                    "BITFINEX_LIVE_ENABLED": "1",
                    "LIVE_COPY_ARMED": "1",
                },
                engine_url="http://127.0.0.1:9/health",
                railway_url="http://127.0.0.1:9/x",
                vercel_url="http://127.0.0.1:9/",
            )
        kinds = {a["kind"] for a in report["alerts"]}
        self.assertIn("LIVE_SAFETY", kinds)

    def test_fly_pull_candidate_ready_is_red_status_not_always_alert(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            data = Path(tmp) / "data"
            data.mkdir()
            report = h.build_report(
                data_dir=data,
                logs_dir=logs,
                environ={"FORCE_PAPER_MODE": "1", "BITFINEX_LIVE_ENABLED": "0"},
                engine_url="http://127.0.0.1:9/health",
                railway_url="http://127.0.0.1:9/x",
                vercel_url="http://127.0.0.1:9/",
                fly_task_states={"DoxxedFlyMirrorSync": "Ready"},
            )
        fly_tasks = next(c for c in report["checks"] if c["id"] == "fly_pull_tasks.disabled")
        self.assertEqual(fly_tasks["status"], h.RED)
        # Not an alert by itself (engine/live only)
        self.assertFalse(any(a.get("check") == "fly_pull_tasks.disabled" for a in report["alerts"]))


if __name__ == "__main__":
    unittest.main()

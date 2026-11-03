"""Local Railway/Neon decoupling (HM + Strategist / RK t781u)."""
from __future__ import annotations

import unittest

import local_cloud_decouple as lcd
import local_fresh_runtime as lfr
import platform_health as ph


class TestLocalCloudDecouple(unittest.TestCase):
    def _env(self) -> dict:
        return {
            "LOCAL_FRESH_START": "1",
            "LOCAL_FRESH_START_CONFIRM": lfr.LOCAL_FRESH_CONFIRMATION,
            "FORCE_PAPER_MODE": "1",
            "BITFINEX_LIVE_ENABLED": "0",
        }

    def test_cloud_coupling_disabled_on_local_fresh(self):
        self.assertTrue(lcd.cloud_coupling_disabled(self._env()))
        self.assertFalse(lcd.cloud_coupling_disabled({}))

    def test_coordination_override_never_railway_unconfirmed(self):
        fields = lcd.coordination_display_override()
        self.assertTrue(fields["live_copy_coordination_confirmed"])
        self.assertEqual(fields["live_copy_coordination_state"], lcd.COORD_STATE_LOCAL_PAPER_ISOLATED)
        self.assertNotIn("NOT_CONFIRMED_BY_RAILWAY", fields["live_copy_coordination_ui_reason"])
        self.assertFalse(fields["live_copy_coordination_railway_required"])

    def test_platform_probes_skip_railway_neon_fly(self):
        probes = lcd.platform_probes_for_mode(ph.PROBES, self._env())
        self.assertNotIn("railway", probes)
        self.assertNotIn("neon", probes)
        self.assertNotIn("fly", probes)
        self.assertIn("github_actions", probes)

    def test_diff_alerts_skips_skipped_platform(self):
        cur = lcd.skipped_platform_snapshot("railway", 1.0)
        alerts = ph.diff_alerts(None, cur, ph.deque(maxlen=5), 1.0)
        self.assertEqual(alerts, [])

    def test_apply_decouple_sets_research_only(self):
        env = {"CREDENTIALS_FROM": "railway", "RAILWAY_PROJECT_ID": "x"}
        lcd.apply_decouple_environ(env)
        self.assertEqual(env["RELAY_STACK_MODE"], "research_only")
        self.assertEqual(env["CREDENTIALS_FROM"], "local_env")
        self.assertNotIn("RAILWAY_PROJECT_ID", env)


if __name__ == "__main__":
    unittest.main()

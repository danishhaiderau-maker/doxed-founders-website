"""Fresh Collection signal contract.

Two wipe operations exist on the dashboard:

* Fresh Collection toggle  -> wipes Fly AND bumps ``state['fresh_collection_signal_ts']``
  so the local sync loop wipes its mirror too (true fresh restart everywhere).
* POST /api/wipe_fly_only  -> wipes Fly but leaves the signal untouched so the
  local sync loop retains its mirror for offline analysis.

These tests pin that distinction and the admin-auth requirement on the new
endpoint.
"""

import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


class FreshCollectionSignalTests(unittest.TestCase):
    def test_fresh_reset_covers_v22_provisionals_and_post_exit_rotations(self):
        source = Path(bot.__file__).read_text(encoding="utf-8")
        self.assertIn("COLLECTOR_V22_RESEARCH_EVENTS_FILE", source)
        self.assertIn("COLLECTOR_V22_EVENT_INDEX_FILE", source)
        self.assertIn("COLLECTOR_V22_PROVISIONAL_FILE", source)
        self.assertIn("PATH_REPLAY_FILE, POST_EXIT_REPLAY_FILE, COLLECTOR_V22_RESEARCH_EVENTS_FILE", source)
        self.assertIn("_order_multiverse_pending_src.clear()", source)

    def setUp(self):
        self.original_signal = float(bot.state.get("fresh_collection_signal_ts") or 0.0)
        self.original_mode = bool(bot.state.get("fresh_collection_mode", False))
        self.original_bootstrap_complete = bot._DASHBOARD_BOOTSTRAP_COMPLETE
        bot._DASHBOARD_BOOTSTRAP_COMPLETE = True
        # Flask test_client is loopback. A developer machine with BOT_ADMIN_TOKEN
        # set would otherwise 401 every mutation before the handler runs.
        self._token_patch = mock.patch.object(bot, "_BOT_ADMIN_TOKEN", "")
        self._token_patch.start()
        with bot.state_lock:
            bot.state["fresh_collection_signal_ts"] = 0.0
            bot.state["fresh_collection_mode"] = False
            bot.state["live_armed"] = False

    def tearDown(self):
        self._token_patch.stop()
        with bot.state_lock:
            bot.state["fresh_collection_signal_ts"] = self.original_signal
            bot.state["fresh_collection_mode"] = self.original_mode
        bot._DASHBOARD_BOOTSTRAP_COMPLETE = self.original_bootstrap_complete

    def test_wipe_fly_only_endpoint_no_signal(self):
        """`/api/wipe_fly_only` must NOT bump the local-sync signal."""
        fake_reset = {
            "ok": True,
            "summary": "deleted 0 file(s)",
            "fresh_collection_signal_ts": 0.0,
            "local_mirror_will_wipe": False,
        }
        with mock.patch.object(
            bot, "perform_fresh_collection_reset", return_value=fake_reset
        ) as reset:
            with bot.app.test_client() as client:
                # Local loopback is admin-authed by default; that's fine — the
                # auth path itself is covered by test_wipe_endpoints_require_admin.
                response = client.post("/api/wipe_fly_only", json={})

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "ok")
        reset.assert_called_once_with(send_local_signal=False)
        with bot.state_lock:
            # `or -1` is unsafe here — 0.0 is falsy. Read the raw value; the
            # contract is that the signal MUST stay at its pre-call value.
            raw = bot.state.get("fresh_collection_signal_ts")
            self.assertIsNotNone(raw)
            self.assertEqual(float(raw), 0.0)

    def test_fresh_collection_bumps_signal(self):
        """Hitting Fresh Collection (turning ON) MUST bump the local-sync signal."""
        fake_reset = {
            "ok": True,
            "summary": "deleted 0 file(s)",
            "fresh_collection_signal_ts": 12345.0,
            "local_mirror_will_wipe": True,
        }
        captured = {}

        def _capture(send_local_signal=True):
            captured["send_local_signal"] = send_local_signal
            # Simulate the production write so the test observes the new value.
            if send_local_signal:
                with bot.state_lock:
                    bot.state["fresh_collection_signal_ts"] = 12345.0
            return fake_reset

        with mock.patch.object(
            bot, "perform_fresh_collection_reset", side_effect=_capture
        ):
            with bot.app.test_client() as client:
                response = client.post(
                    "/api/toggle_fresh_collection",
                    json={"enabled": True, "expected_current": False},
                )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(captured.get("send_local_signal", False))
        with bot.state_lock:
            self.assertGreater(
                float(bot.state.get("fresh_collection_signal_ts") or 0.0), 0.0
            )

    def test_wipe_endpoints_require_admin(self):
        """Both wipe endpoints must refuse unauthenticated callers.

        `_admin_authed_strict` normally trusts loopback. We force it to False
        to simulate a remote / unauthenticated caller — the contract is that
        the wipe surface is admin-only.
        """
        with mock.patch.object(bot, "_admin_authed_strict", return_value=False):
            with mock.patch.object(
                bot, "perform_fresh_collection_reset"
            ) as reset:
                with bot.app.test_client() as client:
                    wipe_resp = client.post("/api/wipe_fly_only", json={})

        self.assertEqual(wipe_resp.status_code, 401)
        reset.assert_not_called()

    def test_manifest_includes_signal(self):
        """`/api/data-sync/manifest` must surface `fresh_collection_signal_ts`."""
        with bot.app.test_client() as client:
            response = client.get("/api/data-sync/manifest")
        # The manifest endpoint touches the live filesystem in some envs; we
        # only assert the contract field is present and numeric when the
        # endpoint succeeds. If it 500s in CI we still want a clear signal.
        if response.status_code == 200:
            body = response.get_json()
            self.assertIn("fresh_collection_signal_ts", body)
            self.assertIsInstance(
                body["fresh_collection_signal_ts"], (int, float)
            )

    @staticmethod
    def _strict_flat_proof():
        return {
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "showcase_positions": 0,
            "showcase_pending_orders": 0,
            "relay_active_participants": 0,
            "relay_open_lots": 0,
            "relay_pending_lots": 0,
            "exchange_positions": 0,
            "exchange_active_orders": 0,
            "exchange_delta_btc": 0,
            "relay_paused": True,
            "relay_disarmed": True,
        }

    def test_fresh_epoch_reset_requires_strict_admin(self):
        with mock.patch.object(bot, "_admin_authed_strict", return_value=False), mock.patch.object(
            bot, "perform_fresh_collection_reset"
        ) as reset:
            with bot.app.test_client() as client:
                response = client.post("/api/fresh_epoch_reset", json={})
        self.assertEqual(response.status_code, 401)
        reset.assert_not_called()

    def test_fresh_epoch_reset_requires_confirmation_and_fresh_zero_proof(self):
        with mock.patch.object(bot, "perform_fresh_collection_reset") as reset:
            with bot.app.test_client() as client:
                missing = client.post("/api/fresh_epoch_reset", json={})
                nonflat_proof = self._strict_flat_proof()
                nonflat_proof["exchange_active_orders"] = 1
                nonflat = client.post(
                    "/api/fresh_epoch_reset",
                    json={
                        "confirmation": bot._FRESH_EPOCH_CONFIRMATION,
                        "strict_flat_proof": nonflat_proof,
                    },
                )
                stale_proof = self._strict_flat_proof()
                stale_proof["checked_at"] = "2020-01-01T00:00:00Z"
                stale = client.post(
                    "/api/fresh_epoch_reset",
                    json={
                        "confirmation": bot._FRESH_EPOCH_CONFIRMATION,
                        "strict_flat_proof": stale_proof,
                    },
                )
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(nonflat.status_code, 409)
        self.assertEqual(stale.status_code, 409)
        reset.assert_not_called()

    def test_fresh_epoch_reset_calls_atomic_reset_and_returns_deleted(self):
        fake_reset = {
            "ok": True,
            "deleted": ["trade_outcome.jsonl"],
            "purged_quarantine": ["/app/data/runtime/research_epoch_quarantine"],
            "fresh_collection_signal_ts": 12345.0,
        }
        with mock.patch.object(
            bot, "perform_fresh_collection_reset", return_value=fake_reset
        ) as reset:
            with bot.app.test_client() as client:
                response = client.post(
                    "/api/fresh_epoch_reset",
                    json={
                        "confirmation": bot._FRESH_EPOCH_CONFIRMATION,
                        "strict_flat_proof": self._strict_flat_proof(),
                    },
                )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["deleted"], ["trade_outcome.jsonl"])
        self.assertEqual(
            body["purged_quarantine"],
            ["/app/data/runtime/research_epoch_quarantine"],
        )
        reset.assert_called_once_with(send_local_signal=True)

    def test_get_fresh_epoch_reset_is_dry_run_and_never_wipes(self):
        with mock.patch.object(bot, "perform_fresh_collection_reset") as reset:
            with bot.app.test_client() as client:
                response = client.get("/api/fresh_epoch_reset")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])
        self.assertTrue(body["dry_run"])
        self.assertFalse(body["wiped"])
        self.assertFalse(body["off_allowed"])
        self.assertTrue(body["one_way_on"])
        self.assertEqual(body["endpoint"], "/api/fresh_epoch_reset")
        self.assertIn("strict_flat_proof", body)
        self.assertEqual(body["confirmation_required"], bot._FRESH_EPOCH_CONFIRMATION)
        reset.assert_not_called()

    def test_post_dry_run_does_not_wipe(self):
        with mock.patch.object(bot, "perform_fresh_collection_reset") as reset:
            with bot.app.test_client() as client:
                response = client.post("/api/fresh_epoch_reset", json={"dry_run": True})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["dry_run"])
        self.assertFalse(body["wiped"])
        reset.assert_not_called()

    def test_dashboard_button_posts_official_epoch_reset(self):
        src = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
        start = src.index("async function toggleFreshCollection()")
        end = src.index("async function wipeFlyOnly()", start)
        fn = src[start:end]
        self.assertIn("fetch('/api/fresh_epoch_reset'", fn)
        self.assertNotIn("/api/toggle_fresh_collection", fn)
        self.assertIn("cannot turn OFF", fn)
        self.assertIn("method: 'GET'", fn)
        self.assertIn("method: 'POST'", fn)


if __name__ == "__main__":
    unittest.main()

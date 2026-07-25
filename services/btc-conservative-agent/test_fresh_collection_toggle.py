"""Fresh Collection control must reject stale/replayed dashboard requests."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


class FreshCollectionToggleTests(unittest.TestCase):
    def setUp(self):
        self.original_mode = bool(bot.state.get("fresh_collection_mode", False))
        self.original_bootstrap_complete = bot._DASHBOARD_BOOTSTRAP_COMPLETE
        bot._DASHBOARD_BOOTSTRAP_COMPLETE = True
        with bot.state_lock:
            bot.state["fresh_collection_mode"] = False

    def tearDown(self):
        with bot.state_lock:
            bot.state["fresh_collection_mode"] = self.original_mode
        bot._DASHBOARD_BOOTSTRAP_COMPLETE = self.original_bootstrap_complete

    def test_duplicate_on_request_is_rejected_as_stale(self):
        fake_reset = {"ok": True, "summary": "test reset"}
        with mock.patch.object(bot, "perform_fresh_collection_reset", return_value=fake_reset) as reset:
            with bot.app.test_client() as client:
                first = client.post(
                    "/api/toggle_fresh_collection",
                    json={"enabled": True, "expected_current": False},
                )
                with bot.state_lock:
                    bot.state["fresh_collection_mode"] = True
                replay = client.post(
                    "/api/toggle_fresh_collection",
                    json={"enabled": True, "expected_current": False},
                )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 409)
        self.assertTrue(replay.get_json()["stale_request"])
        reset.assert_called_once_with()

    def test_invalid_expected_state_fails_closed(self):
        with bot.app.test_client() as client:
            response = client.post(
                "/api/toggle_fresh_collection",
                json={"enabled": True, "expected_current": "false"},
            )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()

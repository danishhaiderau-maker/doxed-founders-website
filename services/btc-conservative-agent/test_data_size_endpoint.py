"""The storage dashboard route must never traverse the Fly volume."""
import os
import sys
import threading
import time
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")
import bot


class DataSizeEndpointTests(unittest.TestCase):
    def setUp(self):
        self.original_bootstrap = bot._DASHBOARD_BOOTSTRAP_COMPLETE
        self.original_condition = bot._data_sync_inventory_cache_condition
        self.original_async = bot._data_sync_async_inventory
        bot._DASHBOARD_BOOTSTRAP_COMPLETE = True
        bot._data_sync_inventory_cache_condition = threading.Condition()

    def tearDown(self):
        bot._DASHBOARD_BOOTSTRAP_COMPLETE = self.original_bootstrap
        bot._data_sync_inventory_cache_condition = self.original_condition
        bot._data_sync_async_inventory = self.original_async

    @staticmethod
    def _usage(used_mb=480, total_mb=1024):
        unit = 1024 * 1024
        return SimpleNamespace(total=total_mb * unit, used=used_mb * unit,
                               free=(total_mb - used_mb) * unit)

    def _state(self, status="CURRENT"):
        bot._data_sync_async_inventory = {
            "status": status, "generated_at": "2026-09-03T00:00:00Z",
            "expires_at": time.monotonic() + 60.0,
            "generation_id": "a" * 64, "error": None,
            "refreshing": status == "BUILDING",
            "generation": {
                "file_count": 2, "total_bytes": 430 * 1024 * 1024,
                "top_files": [
                    {"path": "runtime/a.jsonl", "name": "a.jsonl", "size": 320 * 1024 * 1024},
                    {"path": "runtime/b.jsonl", "name": "b.jsonl", "size": 110 * 1024 * 1024},
                ],
            },
        }

    def _get(self, used_mb=480):
        forbidden = AssertionError("request path attempted full-volume work")
        with mock.patch.object(bot, "_admin_authed_strict", return_value=True), \
             mock.patch.object(bot.shutil, "disk_usage", return_value=self._usage(used_mb)), \
             mock.patch.object(bot.subprocess, "run", side_effect=forbidden), \
             mock.patch.object(bot.os, "walk", side_effect=forbidden), \
             mock.patch.object(bot, "_data_sync_request_async_inventory", side_effect=forbidden), \
             mock.patch.object(bot, "storage_state") as storage_state, \
             mock.patch.object(bot, "project_capacity") as project_capacity:
            with bot.app.test_client() as client:
                response = client.get("/api/data_size")
        storage_state.assert_not_called()
        project_capacity.assert_not_called()
        return response

    def test_unauthenticated_request_is_rejected(self):
        with mock.patch.object(bot, "_admin_authed_strict", return_value=False):
            with bot.app.test_client() as client:
                response = client.get("/api/data_size")
        self.assertEqual(response.status_code, 401)

    def test_current_inventory_and_filesystem_usage_are_distinct(self):
        self._state()
        body = self._get().get_json()
        self.assertEqual(body["filesystem_used_mb"], 480.0)
        self.assertEqual(body["inventory_transferable_mb"], 430.0)
        self.assertEqual(body["runtime_size_status"], "CURRENT")
        self.assertAlmostEqual(body["volume_pct"], 46.9, delta=.05)
        self.assertEqual([row["name"] for row in body["top_files"]], ["a.jsonl", "b.jsonl"])
        self.assertEqual(body["line_count_status"], "UNAVAILABLE")
        self.assertTrue(all(value is None for value in body["line_counts"].values()))

    def test_stale_inventory_is_explicit_and_never_exposes_top_files(self):
        self._state("STALE")
        body = self._get().get_json()
        self.assertEqual(body["runtime_size_status"], "STALE")
        self.assertEqual(body["inventory_transferable_mb"], 430.0)
        self.assertEqual(body["top_files"], [])

    def test_building_or_empty_inventory_is_unavailable(self):
        for status in ("BUILDING", "EMPTY"):
            with self.subTest(status=status):
                self._state(status)
                bot._data_sync_async_inventory["generation"] = None
                body = self._get().get_json()
                self.assertEqual(body["runtime_size_status"], "UNAVAILABLE")
                self.assertIsNone(body["runtime_size_mb"])
                self.assertEqual(body["top_files"], [])

    def test_expired_current_inventory_is_stale_and_ranking_is_withheld(self):
        self._state("CURRENT")
        bot._data_sync_async_inventory["expires_at"] = time.monotonic() - 1.0
        body = self._get().get_json()
        self.assertEqual(body["runtime_size_status"], "STALE")
        self.assertEqual(body["inventory_transferable_mb"], 430.0)
        self.assertEqual(body["top_files"], [])

    def test_building_with_retained_generation_is_stale_revalidating(self):
        self._state("BUILDING")
        body = self._get().get_json()
        self.assertEqual(body["runtime_size_status"], "STALE_REVALIDATING")
        self.assertEqual(body["inventory_transferable_mb"], 430.0)
        self.assertEqual(body["top_files"], [])

    def test_capacity_threshold_uses_filesystem_usage(self):
        self._state()
        self.assertEqual(self._get(700).get_json()["cleanup_status"], "warn")
        self.assertEqual(self._get(900).get_json()["cleanup_status"], "critical")

    def test_inventory_lock_contention_fails_closed_without_waiting(self):
        self._state()
        held = threading.Event()
        release = threading.Event()

        def hold_inventory_condition():
            with bot._data_sync_inventory_cache_condition:
                held.set()
                release.wait(timeout=2.0)

        owner = threading.Thread(target=hold_inventory_condition, daemon=True)
        owner.start()
        self.assertTrue(held.wait(timeout=1.0))
        started = time.monotonic()
        try:
            body = self._get().get_json()
        finally:
            release.set()
            owner.join(timeout=1.0)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.25)
        self.assertEqual(body["runtime_size_status"], "UNAVAILABLE")
        self.assertIsNone(body["inventory_transferable_mb"])
        self.assertEqual(body["top_files"], [])
        self.assertTrue(body["inventory_refreshing"])
        self.assertEqual(body["inventory_error"], "INVENTORY_SNAPSHOT_LOCK_BUSY")

    def test_concurrent_pollers_do_not_queue_behind_inventory_lock(self):
        self._state()
        held = threading.Event()
        release = threading.Event()

        def hold_inventory_condition():
            with bot._data_sync_inventory_cache_condition:
                held.set()
                release.wait(timeout=2.0)

        owner = threading.Thread(target=hold_inventory_condition, daemon=True)
        owner.start()
        self.assertTrue(held.wait(timeout=1.0))
        started = time.monotonic()
        try:
            with mock.patch.object(bot, "_admin_authed_strict", return_value=True), \
                 mock.patch.object(bot.shutil, "disk_usage", return_value=self._usage()), \
                 mock.patch.object(bot.subprocess, "run", side_effect=AssertionError("subprocess")), \
                 mock.patch.object(bot.os, "walk", side_effect=AssertionError("walk")), \
                 mock.patch.object(bot, "_data_sync_request_async_inventory", side_effect=AssertionError("refresh")), \
                 mock.patch.object(bot, "storage_state") as storage_state, \
                 mock.patch.object(bot, "project_capacity") as project_capacity:
                responses = []

                def poll():
                    with bot.app.test_client() as client:
                        responses.append(client.get("/api/data_size"))

                pollers = [threading.Thread(target=poll) for _ in range(8)]
                for poller in pollers:
                    poller.start()
                for poller in pollers:
                    poller.join(timeout=0.5)
                self.assertTrue(all(not poller.is_alive() for poller in pollers))
                storage_state.assert_not_called()
                project_capacity.assert_not_called()
        finally:
            release.set()
            owner.join(timeout=1.0)
        elapsed = time.monotonic() - started

        self.assertLess(elapsed, 0.5)
        self.assertEqual(len(responses), 8)
        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertTrue(all(
            response.get_json()["runtime_size_status"] == "UNAVAILABLE"
            for response in responses
        ))

    def test_dashboard_explains_trigger_and_disabled_retention(self):
        self.assertIn("50 MB value is a synchronization trigger, not a storage cap", bot.HTML)
        self.assertIn("Retention: disabled", bot.HTML)
        self.assertIn("dataSizeInventoryStatus", bot.HTML)
        self.assertIn("ranking withheld", bot.DASHBOARD_JS)


if __name__ == "__main__":
    unittest.main()

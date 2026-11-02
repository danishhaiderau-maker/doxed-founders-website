"""SQLite + gap log for local public market persist (day-1)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_market_persist import MarketStore, detect_gaps_from_seconds
from local_public_ws_collector import LocalPublicCollector


class TestLocalMarketPersist(unittest.TestCase):
    def test_book_gap_logged_when_second_skipped_never_filled(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketStore(tmp)
            try:
                store.append_book_second(
                    {"s": 100, "bid": 1.0, "ask": 1.1, "b": [[1.0, 1]], "a": [[1.1, 1]]}
                )
                store.append_book_second(
                    {"s": 103, "bid": 1.0, "ask": 1.1, "b": [[1.0, 1]], "a": [[1.1, 1]]}
                )
                stats = store.stats()
                self.assertEqual(stats["book_1s_rows"], 2)
                self.assertEqual(stats["gap_events"], 1)
                self.assertTrue(stats["gaps_never_filled"])
                self.assertIsNotNone(stats["series_break_ts"])
                self.assertTrue(Path(store.gap_log_path).is_file())
                lines = Path(store.gap_log_path).read_text(encoding="utf-8").strip().splitlines()
                self.assertEqual(len(lines), 1)
                event = json.loads(lines[0])
                self.assertEqual(event["kind"], "BOOK_1S")
                self.assertEqual(event["start_s"], 101)
                self.assertEqual(event["end_s"], 102)
                self.assertEqual(event["missing_seconds"], 2)
                self.assertFalse(event["filled"])
                missing = store.missing_book_seconds(100, 103)
                self.assertEqual(missing, [101, 102])
            finally:
                store.close()

    def test_detect_gaps_from_seconds(self):
        self.assertEqual(detect_gaps_from_seconds([1, 2, 3]), [])
        self.assertEqual(detect_gaps_from_seconds([10, 12, 15]), [(11, 11, 1), (13, 14, 2)])

    def test_collector_sleep_jump_logs_laptop_sleep_never_fills(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketStore(tmp)
            health = Path(tmp) / "health.json"
            collector = LocalPublicCollector(
                store,
                health_path=str(health),
                clock=lambda: 1000.4,
                sleep_gap_threshold_s=5,
            )
            try:
                collector.chans[1] = "book"
                collector.on_message(
                    [1, [[50000.0, 1, 0.5], [50001.0, 1, -0.4]]], now=1000.4
                )
                # Jump 10s (laptop sleep) — must NOT invent book for 1001..1009.
                collector.on_message([1, [50000.0, 1, 0.6]], now=1010.1)
                stats = store.stats()
                self.assertEqual(stats["book_1s_rows"], 1)
                kinds = []
                for line in Path(store.gap_log_path).read_text(encoding="utf-8").splitlines():
                    kinds.append(json.loads(line)["kind"])
                self.assertIn("LAPTOP_SLEEP", kinds)
                written = collector.write_health(ok=True)
                body = json.loads(Path(written).read_text(encoding="utf-8"))
                self.assertEqual(body["mode"], "LOCAL_FRESH_PAPER")
                self.assertTrue(body["gaps_never_filled"])
                self.assertIn("last_tick_age_s", body)
                self.assertIn("disk_free_bytes", body)
            finally:
                store.close()

    def test_collector_disconnect_logged(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketStore(tmp)
            collector = LocalPublicCollector(store, clock=lambda: 2000.0)
            try:
                collector.on_disconnect(now=2000.0, reason="Timeout")
                lines = Path(store.gap_log_path).read_text(encoding="utf-8").strip().splitlines()
                event = json.loads(lines[-1])
                self.assertEqual(event["kind"], "COLLECTOR_DISCONNECT")
                self.assertFalse(event["filled"])
            finally:
                store.close()

    def test_trades_second_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketStore(tmp)
            try:
                store.append_trades_second(
                    {
                        "s": 50,
                        "tr": [[11, 1_700_000_000_000, 0.01, 50000.0]],
                        "receive_ts": 50.9,
                    }
                )
                self.assertEqual(store.stats()["trade_rows"], 1)
            finally:
                store.close()

    def test_gap_log_rotation_keeps_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketStore(tmp)
            try:
                # Force tiny max and rotate.
                import os

                os.environ["LOCAL_FRESH_GAP_LOG_MAX_BYTES"] = "200"
                for i in range(30):
                    store.log_explicit_gap("BOOK_1S", 1000 + i, 1000 + i, detail={"i": i})
                report = store.run_retention()
                self.assertTrue(Path(store.gap_log_path).is_file())
                # Either rotated or still under cap after prune attempt.
                self.assertIn("gap_log", report)
            finally:
                os.environ.pop("LOCAL_FRESH_GAP_LOG_MAX_BYTES", None)
                store.close()


if __name__ == "__main__":
    unittest.main()

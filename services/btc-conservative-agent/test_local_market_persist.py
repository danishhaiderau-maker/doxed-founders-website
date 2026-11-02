"""SQLite + gap log for local public market persist (day-1)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from local_market_persist import MarketStore, detect_gaps_from_seconds
from local_public_ws_collector import LocalPublicCollector


class TestLocalMarketPersist(unittest.TestCase):
    def test_book_gap_logged_when_second_skipped(self):
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
                self.assertTrue(Path(store.gap_log_path).is_file())
                lines = Path(store.gap_log_path).read_text(encoding="utf-8").strip().splitlines()
                self.assertEqual(len(lines), 1)
                event = json.loads(lines[0])
                self.assertEqual(event["kind"], "BOOK_1S")
                self.assertEqual(event["start_s"], 101)
                self.assertEqual(event["end_s"], 102)
                self.assertEqual(event["missing_seconds"], 2)
                missing = store.missing_book_seconds(100, 103)
                self.assertEqual(missing, [101, 102])
            finally:
                store.close()

    def test_detect_gaps_from_seconds(self):
        self.assertEqual(detect_gaps_from_seconds([1, 2, 3]), [])
        self.assertEqual(detect_gaps_from_seconds([10, 12, 15]), [(11, 11, 1), (13, 14, 2)])

    def test_collector_on_message_writes_book_on_second_roll(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketStore(tmp)
            health = Path(tmp) / "health.json"
            collector = LocalPublicCollector(
                store, health_path=str(health), clock=lambda: 1000.4
            )
            try:
                collector.chans[1] = "book"
                collector.on_message(
                    [1, [[50000.0, 1, 0.5], [50001.0, 1, -0.4]]], now=1000.4
                )
                # Roll to next second → close 1000.
                collector.on_message([1, [50000.0, 1, 0.6]], now=1001.1)
                stats = store.stats()
                self.assertGreaterEqual(stats["book_1s_rows"], 1)
                written = collector.write_health(ok=True)
                self.assertTrue(Path(written).is_file())
                body = json.loads(Path(written).read_text(encoding="utf-8"))
                self.assertEqual(body["mode"], "LOCAL_FRESH_PAPER")
                self.assertIn("store", body["collector"])
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


if __name__ == "__main__":
    unittest.main()

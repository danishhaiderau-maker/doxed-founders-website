"""Read-only analyzer path + no SQLite purge defaults (RK C / R51)."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from local_fresh_analyzer_read import (
    POOLED_ACROSS_SERIES_BREAK,
    LocalFreshStoreReadError,
    open_local_fresh_store_readonly,
    summarize_local_fresh_store,
)
from local_market_persist import MarketStore, StoreGrowthCapError


class TestLocalFreshNoSqlitePurge(unittest.TestCase):
    def test_default_retention_does_not_delete_old_rows(self):
        # RK (C): default retain_seconds=0 → purge disabled.
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketStore(tmp, clock=lambda: 2_000_000_000.0)
            try:
                store.append_book_second(
                    {"s": 1_000, "bid": 1.0, "ask": 1.1, "receive_ts": 1_000.0}
                )
                store.append_book_second(
                    {"s": 1_900_000_000, "bid": 2.0, "ask": 2.1, "receive_ts": 1_900_000_000.0}
                )
                report = store.run_retention()
                self.assertFalse(report["sqlite"].get("purge", True))
                self.assertEqual(report["sqlite"]["book_deleted"], 0)
                self.assertTrue(report["sqlite"].get("store_growth_cap_enforced"))
                self.assertEqual(store.stats()["book_1s_rows"], 2)
                self.assertFalse(store.stats()["sqlite_purge_enabled"])
                self.assertTrue(store.stats()["store_growth_cap_enforced"])
                self.assertGreater(store.stats()["store_max_bytes"], 0)
                self.assertIsNotNone(store.stats()["local_epoch_id"])
                self.assertIsNotNone(store.stats()["series_start_ts"])
            finally:
                store.close()

    def test_store_growth_cap_fails_closed_while_purge_off(self):
        # RK 20:20: prune off + no limit is FAIL — tiny cap must refuse writes.
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketStore(tmp, clock=lambda: 2_000_000_000.0)
            try:
                os.environ["LOCAL_FRESH_STORE_MAX_BYTES"] = "1"
                with self.assertRaises(StoreGrowthCapError):
                    store.append_book_second(
                        {"s": 1_000, "bid": 1.0, "ask": 1.1, "receive_ts": 1_000.0}
                    )
            finally:
                os.environ.pop("LOCAL_FRESH_STORE_MAX_BYTES", None)
                store.close()

    def test_positive_retention_still_prunes_when_explicitly_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LOCAL_FRESH_SQLITE_RETENTION_SECONDS"] = "10"
            store = MarketStore(tmp, clock=lambda: 1_000_100.0)
            try:
                store.append_book_second(
                    {"s": 1_000_000, "bid": 1.0, "ask": 1.1, "receive_ts": 1_000_000.0}
                )
                store.append_book_second(
                    {"s": 1_000_090, "bid": 2.0, "ask": 2.1, "receive_ts": 1_000_090.0}
                )
                report = store.run_retention()
                self.assertTrue(report["sqlite"]["purge"])
                self.assertGreaterEqual(report["sqlite"]["book_deleted"], 1)
                self.assertEqual(store.stats()["book_1s_rows"], 1)
            finally:
                os.environ.pop("LOCAL_FRESH_SQLITE_RETENTION_SECONDS", None)
                store.close()


class TestLocalFreshAnalyzerRead(unittest.TestCase):
    def test_readonly_reads_book_trades_and_series_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketStore(tmp, clock=lambda: 1_700_000_000.0)
            try:
                store.append_book_second(
                    {"s": 100, "bid": 10.0, "ask": 10.1, "receive_ts": 100.5}
                )
                store.append_trades_second(
                    {
                        "s": 100,
                        "tr": [[1, 1_700_000_000_000, 0.01, 10.05]],
                        "receive_ts": 100.6,
                    }
                )
                store.log_explicit_gap("COLLECTOR_DISCONNECT", 101, 105)
            finally:
                store.close()

            with open_local_fresh_store_readonly(tmp) as ro:
                ident = ro.identity()
                self.assertTrue(ident["read_only"])
                self.assertTrue(ident["writes_forbidden"])
                self.assertTrue(str(ident["local_epoch_id"]).startswith("local-fresh-"))
                self.assertEqual(ident["series_start_ts"], 1_700_000_000.0)
                counts = ro.count_rows()
                self.assertEqual(counts["book_1s_rows"], 1)
                self.assertEqual(counts["trade_rows"], 1)
                self.assertGreaterEqual(counts["gap_events"], 1)
                # Pre-break side only (s=100 << series_start); P5 forbids unbounded/straddle.
                books = list(ro.iter_book_seconds(start_s=0, end_s=200))
                self.assertEqual(books[0]["s"], 100)
                trades = list(ro.iter_trades(start_s=0, end_s=200))
                self.assertEqual(trades[0]["price"], 10.05)
                gaps = list(ro.iter_gap_log_lines())
                self.assertTrue(any(g.get("kind") == "COLLECTOR_DISCONNECT" for g in gaps))

            snap = summarize_local_fresh_store(tmp)
            self.assertEqual(snap["schema"], "local_fresh_analyzer_read_v1")
            self.assertEqual(snap["counts"]["book_1s_rows"], 1)

    def test_readonly_refuses_pooled_query_across_series_break(self):
        # P5 / RK HOLD #972: analyzer must error on any pooled window across series_break.
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketStore(tmp, clock=lambda: 1_700_000_000.0)
            try:
                store.append_book_second(
                    {"s": 100, "bid": 10.0, "ask": 10.1, "receive_ts": 100.5}
                )
            finally:
                store.close()

            with open_local_fresh_store_readonly(tmp) as ro:
                boundary = ro.series_start_s()
                self.assertEqual(boundary, 1_700_000_000)
                with self.assertRaises(LocalFreshStoreReadError) as ctx_unbounded:
                    list(ro.iter_book_seconds())
                self.assertIn(POOLED_ACROSS_SERIES_BREAK, str(ctx_unbounded.exception))
                with self.assertRaises(LocalFreshStoreReadError) as ctx_straddle:
                    list(
                        ro.iter_trades(
                            start_s=boundary - 10, end_s=boundary + 10
                        )
                    )
                self.assertIn(POOLED_ACROSS_SERIES_BREAK, str(ctx_straddle.exception))
                # Same-side windows remain allowed.
                list(ro.iter_book_seconds(start_s=0, end_s=boundary - 1))
                list(ro.iter_book_seconds(start_s=boundary, end_s=boundary + 60))

    def test_readonly_refuses_missing_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LocalFreshStoreReadError):
                open_local_fresh_store_readonly(tmp)


class TestTestsDirRequiredInAllowlist(unittest.TestCase):
    def test_agent_tests_dir_files_are_allowlisted(self):
        # RK (C): tests/ is part of required CI (allowlist → unit-tests job).
        root = Path(__file__).resolve().parent
        allow = {
            line.strip()
            for line in (root / "ci" / "unit_test_allowlist.txt").read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip() and not line.startswith("#")
        }
        tests_dir = root / "tests"
        self.assertTrue(tests_dir.is_dir())
        missing = []
        for path in sorted(tests_dir.glob("test_*.py")):
            rel = f"tests/{path.name}"
            if rel not in allow:
                missing.append(rel)
        self.assertEqual(missing, [], f"tests/ files missing from allowlist: {missing}")


if __name__ == "__main__":
    unittest.main()

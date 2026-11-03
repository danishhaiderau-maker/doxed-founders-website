"""RK 01:30 / HM P5: analyzer must CALL local_fresh_analyzer_read through P5 guard."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("pandas")

import analyzer_research_engine_v62 as analyzer  # noqa: E402
from local_fresh_analyzer_read import (  # noqa: E402
    POOLED_ACROSS_SERIES_BREAK,
    LocalFreshStoreReadError,
)
from local_market_persist import MarketStore  # noqa: E402


class TestAnalyzerCallsLocalFreshReader(unittest.TestCase):
    def test_call_local_fresh_analyzer_read_invokes_summarize(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketStore(tmp, clock=lambda: 1_700_000_000.0)
            try:
                store.append_book_second(
                    {"s": 1_700_000_010, "bid": 1.0, "ask": 1.1, "receive_ts": 1_700_000_010.0}
                )
            finally:
                store.close()

            # Same-side (post-break) window — boot path; still drives guarded iters.
            with mock.patch.object(
                analyzer._local_fresh_analyzer_read,
                "summarize_local_fresh_store",
                wraps=analyzer._local_fresh_analyzer_read.summarize_local_fresh_store,
            ) as mocked:
                snap = analyzer.call_local_fresh_analyzer_read(
                    tmp, start_s=1_700_000_000
                )
                mocked.assert_called_once()
                called_arg = Path(mocked.call_args.args[0]).resolve()
                self.assertEqual(called_arg, Path(tmp).resolve())
                self.assertEqual(mocked.call_args.kwargs.get("start_s"), 1_700_000_000)
            self.assertIsNotNone(snap)
            self.assertEqual(snap["schema"], "local_fresh_analyzer_read_v1")
            self.assertEqual(snap["series_start_ts"], 1_700_000_000.0)
            self.assertTrue(str(snap["local_epoch_id"]).startswith("local-fresh-"))

    def test_analyzer_path_refuses_straddle_and_unbounded_across_series_break(self):
        # HM ed3b6600d2ec P5: actual analyzer path (call → summarize → iters) must
        # raise LOCAL_FRESH_POOLED_ACROSS_SERIES_BREAK for straddle + unbounded.
        with tempfile.TemporaryDirectory() as tmp:
            store = MarketStore(tmp, clock=lambda: 1_700_000_000.0)
            try:
                store.append_book_second(
                    {"s": 100, "bid": 10.0, "ask": 10.1, "receive_ts": 100.5}
                )
                store.append_book_second(
                    {
                        "s": 1_700_000_010,
                        "bid": 11.0,
                        "ask": 11.1,
                        "receive_ts": 1_700_000_010.0,
                    }
                )
            finally:
                store.close()

            boundary = 1_700_000_000
            with self.assertRaises(LocalFreshStoreReadError) as ctx_straddle:
                analyzer.call_local_fresh_analyzer_read(
                    tmp, start_s=boundary - 10, end_s=boundary + 10
                )
            self.assertIn(POOLED_ACROSS_SERIES_BREAK, str(ctx_straddle.exception))

            with self.assertRaises(LocalFreshStoreReadError) as ctx_unbounded:
                analyzer.call_local_fresh_analyzer_read(tmp)
            self.assertIn(POOLED_ACROSS_SERIES_BREAK, str(ctx_unbounded.exception))

    def test_call_returns_none_without_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                analyzer._local_fresh_analyzer_read,
                "summarize_local_fresh_store",
            ) as mocked:
                self.assertIsNone(analyzer.call_local_fresh_analyzer_read(tmp))
                mocked.assert_not_called()

    def test_analyzer_main_path_source_calls_helper(self):
        # Binding proof: __main__ must invoke call_local_fresh_analyzer_read
        # with an explicit post-break window when series_break is present.
        src = Path(analyzer.__file__).read_text(encoding="utf-8")
        self.assertIn("call_local_fresh_analyzer_read(_canonical_data_root", src)
        self.assertIn("def call_local_fresh_analyzer_read(", src)
        self.assertIn("start_s=_local_fresh_boot_start", src)
        self.assertIn("series_start_s()", src)


if __name__ == "__main__":
    unittest.main()

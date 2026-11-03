"""RK 01:30: analyzer must CALL local_fresh_analyzer_read (not import-only)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("pandas")

import analyzer_research_engine_v62 as analyzer  # noqa: E402
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

            with mock.patch.object(
                analyzer._local_fresh_analyzer_read,
                "summarize_local_fresh_store",
                wraps=analyzer._local_fresh_analyzer_read.summarize_local_fresh_store,
            ) as mocked:
                snap = analyzer.call_local_fresh_analyzer_read(tmp)
                mocked.assert_called_once()
                called_arg = Path(mocked.call_args.args[0]).resolve()
                self.assertEqual(called_arg, Path(tmp).resolve())
            self.assertIsNotNone(snap)
            self.assertEqual(snap["schema"], "local_fresh_analyzer_read_v1")
            self.assertEqual(snap["series_start_ts"], 1_700_000_000.0)
            self.assertTrue(str(snap["local_epoch_id"]).startswith("local-fresh-"))

    def test_call_returns_none_without_sqlite(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                analyzer._local_fresh_analyzer_read,
                "summarize_local_fresh_store",
            ) as mocked:
                self.assertIsNone(analyzer.call_local_fresh_analyzer_read(tmp))
                mocked.assert_not_called()

    def test_analyzer_main_path_source_calls_helper(self):
        # Binding proof: __main__ must invoke call_local_fresh_analyzer_read.
        src = Path(analyzer.__file__).read_text(encoding="utf-8")
        self.assertIn("call_local_fresh_analyzer_read(_canonical_data_root)", src)
        self.assertIn("def call_local_fresh_analyzer_read(", src)


if __name__ == "__main__":
    unittest.main()

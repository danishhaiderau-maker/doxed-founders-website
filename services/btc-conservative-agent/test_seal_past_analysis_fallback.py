"""Seal-past-analysis fallback contract for the Fly wipe path.

Fly's .dockerignore excludes the ``research/`` Python package from the
container image. The wipe endpoint's ``from research.past_analysis import
seal_past_analysis`` therefore raises ImportError, which the safety guard
turns into HTTP 409 -- blocking every wipe on Fly.

These tests pin the new contract: ``_seal_past_analysis_with_fallback``
must (a) call the real seal when the package is importable, and (b)
degrade to an in-memory archive dict when it isn't, so the wipe can
proceed with an audit trail.
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot


class SealPastAnalysisFallbackTests(unittest.TestCase):
    def setUp(self):
        # Snapshot in-memory state so each test starts from a clean baseline.
        with bot.trade_lock:
            self._orig_trades = list(bot.trades)
            bot.trades.clear()

    def tearDown(self):
        with bot.trade_lock:
            bot.trades.clear()
            bot.trades.extend(self._orig_trades)

    # -- Test 1: package available -> real seal is called -------------------
    def test_fallback_calls_real_seal_when_package_available(self):
        """When ``research.past_analysis`` imports cleanly, the helper must
        defer to the real ``seal_past_analysis`` (strict behavior preserved
        for desktop / analyzer hosts)."""
        fake_real = {"archive_id": "real_archive_123", "schema": "past_analysis_manifest_v1"}
        # Build a fake module so the ``from ... import ...`` inside the helper
        # resolves to our stub instead of the real package.
        fake_module = mock.MagicMock()
        fake_module.seal_past_analysis = mock.MagicMock(return_value=fake_real)

        with mock.patch.dict(sys.modules, {"research.past_analysis": fake_module}):
            result = bot._seal_past_analysis_with_fallback(reason="unit_test")

        fake_module.seal_past_analysis.assert_called_once()
        # Confirm the helper passed the cwd + reason straight through.
        _, kwargs = fake_module.seal_past_analysis.call_args
        self.assertEqual(kwargs.get("reason"), "unit_test")
        # And that the real return value bubbled up unchanged.
        self.assertEqual(result, fake_real)
        self.assertNotIn("degraded_mode", result)

    # -- Test 2: package missing -> degraded dict built from bot state ------
    def test_fallback_returns_degraded_dict_when_package_unavailable(self):
        """When ``research.past_analysis`` cannot be imported (Fly image),
        the helper must build a minimal archive dict from in-memory bot
        state instead of raising."""
        fake_trade = {
            "trade_id": "T-001",
            "close_ts": 1234567890.0,
            "net_pnl_usd": 12.34,
            "extra_field": "ignored",
        }
        with bot.trade_lock:
            bot.trades.append(fake_trade)

        # Force ImportError on the inner ``from research.past_analysis import ...``.
        real_modules = dict(sys.modules)

        class _RaiseOnImport:
            def find_spec(self, name, path=None, target=None):
                if name == "research.past_analysis" or name == "research":
                    raise ImportError("forced for test")
                return None

        # Remove any cached research module so the import path is exercised.
        for key in list(sys.modules.keys()):
            if key == "research" or key.startswith("research."):
                # Keep research_genome_* intact (separate package, imported by bot).
                if key.startswith("research_genome"):
                    continue
                sys.modules.pop(key, None)

        sys.meta_path.insert(0, _RaiseOnImport())
        try:
            result = bot._seal_past_analysis_with_fallback(reason="fly_wipe")
        finally:
            sys.meta_path.pop(0)
            # Restore sys.modules without touching research_genome_*.
            for key in list(sys.modules.keys()):
                if key == "research" or (
                    key.startswith("research.") and not key.startswith("research_genome")
                ):
                    sys.modules.pop(key, None)
            for k, v in real_modules.items():
                sys.modules.setdefault(k, v)

        # Shape contract.
        self.assertTrue(result["archive_id"].startswith("degraded_"))
        self.assertEqual(result["reason"], "fly_wipe")
        self.assertTrue(result["degraded_mode"])
        self.assertIn("note", result)
        self.assertEqual(result["analyzer_sync_id"], bot.ANALYZER_SYNC_ID)
        # Audit trail -- the seeded trade must be preserved with only the
        # three contracted fields.
        self.assertEqual(len(result["trades"]), 1)
        self.assertEqual(
            result["trades"][0],
            {"trade_id": "T-001", "close_ts": 1234567890.0, "net_pnl_usd": 12.34},
        )
        # Session counters propagated.
        self.assertEqual(result["trade_count_session"], len(bot.trades))

    # -- Test 3: end-to-end wipe path with import failing -> ok: True -------
    def test_perform_fresh_collection_reset_ok_when_package_missing(self):
        """End-to-end: with ``research.past_analysis`` unimportable, the
        wipe pipeline must still return ``ok: True`` (no 409)."""
        # Force ImportError on the inner import path inside the helper.
        class _RaiseOnImport:
            def find_spec(self, name, path=None, target=None):
                if name == "research.past_analysis" or name == "research":
                    raise ImportError("forced for test")
                return None

        for key in list(sys.modules.keys()):
            if key == "research" or (
                key.startswith("research.") and not key.startswith("research_genome")
            ):
                sys.modules.pop(key, None)

        sys.meta_path.insert(0, _RaiseOnImport())
        try:
            result = bot._perform_fresh_collection_reset_locked(send_local_signal=False)
        finally:
            sys.meta_path.pop(0)

        # Contract: the wipe must NOT abort just because the research package
        # is missing on the Fly image.
        self.assertTrue(result.get("ok"), f"expected ok=True, got: {result}")
        self.assertFalse(result.get("wipe_aborted", False))
        self.assertIn("past_analysis_id", result)
        self.assertTrue(result["past_analysis_id"].startswith("degraded_"))


if __name__ == "__main__":
    unittest.main()

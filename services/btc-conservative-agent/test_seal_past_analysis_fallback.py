"""Optional sealing helper, legacy archive integrity, and reset admission tests.

The current reset uses its own exact deletion/receipt protocol. Successful
optional sealing does not establish the exclusive reset boundary or authorize
deletion. These tests do not claim complete destructive reset integration.
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path
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
            self._orig_pending = list(bot.pending_orders)
            self._orig_open = list(bot.open_positions)
            bot.trades.clear()
            bot.pending_orders.clear()
            bot.open_positions.clear()
        with bot.state_lock:
            self._orig_pause = (
                bot.state.get("execution_paused"), bot.state.get("execution_reason"),
                bot.state.get("_pause_priority"), bot.state.get("live_armed"),
            )
            bot.state["execution_paused"] = True
            bot.state["execution_reason"] = "TEST_PAUSE"
            bot.state["_pause_priority"] = 50
            bot.state["live_armed"] = False

    def tearDown(self):
        with bot.trade_lock:
            bot.trades.clear()
            bot.trades.extend(self._orig_trades)
            bot.pending_orders.clear()
            bot.pending_orders.extend(self._orig_pending)
            bot.open_positions.clear()
            bot.open_positions.extend(self._orig_open)
        with bot.state_lock:
            (bot.state["execution_paused"], bot.state["execution_reason"],
             bot.state["_pause_priority"], bot.state["live_armed"]) = self._orig_pause

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
                if name == "research.past_analysis":
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

    def test_public_reset_wrapper_holds_exclusive_gate_and_releases_on_failure(self):
        """Admission contract only; this does not claim a destructive reset passed."""
        import threading
        gate = threading.Lock()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(bot, "_data_sync_runtime_root", return_value=Path(tmp)), \
             mock.patch.object(bot, "_fresh_collection_lock", gate):
            def admitted(*, send_local_signal):
                self.assertFalse(send_local_signal)
                self.assertTrue(gate.locked())
                duplicate = bot.perform_fresh_collection_reset(send_local_signal=False)
                self.assertEqual(duplicate["error"], "reset_already_in_progress")
                self.assertTrue(duplicate["wipe_aborted"])
                raise RuntimeError("fixture boundary failure")
            with mock.patch.object(bot, "_perform_fresh_collection_reset_locked", side_effect=admitted) as body:
                with self.assertRaisesRegex(RuntimeError, "fixture boundary failure"):
                    bot.perform_fresh_collection_reset(send_local_signal=False)
                body.assert_called_once_with(send_local_signal=False)
            self.assertFalse(gate.locked())
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_reset_aborts_before_archive_when_boundary_is_not_paused_and_flat(self):
        with bot.state_lock:
            bot.state["execution_paused"] = False
        with mock.patch.object(bot, "_seal_past_analysis_with_fallback") as seal, \
             mock.patch.object(bot, "_perform_fresh_collection_reset_quiesced") as quiesced:
            result = bot._perform_fresh_collection_reset_locked(send_local_signal=False)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "fresh_collection_requires_paused_disarmed_flat_boundary")
        self.assertTrue(result.get("wipe_aborted"))
        seal.assert_not_called()
        quiesced.assert_not_called()

    def test_verified_archive_preserves_exact_deletion_set_and_truthful_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "one.jsonl")
            second = os.path.join(tmp, "two.csv")
            with open(first, "wb") as handle:
                handle.write(b"one\n")
            with open(second, "wb") as handle:
                handle.write(b"two,2\n")
            archive_root = os.path.join(tmp, "archive")
            with mock.patch.object(bot, "RESEARCH_ARCHIVE_DIR", archive_root), \
                 mock.patch.object(bot, "all_research_wipe_paths", return_value=[first, second]), \
                 mock.patch("research.epoch_quarantine.purge_quarantine_archives", return_value={"deleted_trees": [], "deleted_files": 0, "errors": []}):
                archive = bot.create_research_archive_receipt(
                    {"archive_id": "past-1", "analysis_generated_at": "now"},
                    "test", source_paths=[first, second],
                )
                result = bot.reset_all_research_files(archive_path=archive)
            self.assertFalse(result["wipe_aborted"])
            self.assertFalse(os.path.exists(first))
            self.assertFalse(os.path.exists(second))
            with open(os.path.join(archive, "archive_meta.json"), encoding="utf-8") as handle:
                meta = json.load(handle)
            with open(os.path.join(archive, "archive_compaction_receipt.json"), encoding="utf-8") as handle:
                receipt = json.load(handle)
            self.assertTrue(meta["integrity"]["verified"])
            self.assertEqual(meta["integrity"]["file_count"], 2)
            self.assertEqual(meta["integrity"]["total_source_bytes"], 10)
            self.assertTrue(meta["raw_payloads_retained"])
            self.assertTrue(receipt["deletion_verified"])
            self.assertEqual(receipt["deleted_files"], 2)
            self.assertEqual(receipt["deleted_bytes"], 10)
            for row in meta["source_inventory"]:
                preserved = os.path.join(archive, row["preserved_path"])
                self.assertTrue(os.path.isfile(preserved))
                self.assertEqual(bot._file_sha256(preserved), row["sha256"])

    def test_reset_aborts_without_deleting_when_source_changes_after_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "changing.jsonl")
            with open(source, "wb") as handle:
                handle.write(b"before\n")
            with mock.patch.object(bot, "RESEARCH_ARCHIVE_DIR", os.path.join(tmp, "archive")):
                archive = bot.create_research_archive_receipt(
                    {"archive_id": "past-2", "analysis_generated_at": "now"},
                    "test", source_paths=[source],
                )
            with open(source, "ab") as handle:
                handle.write(b"after\n")
            with mock.patch.object(bot, "all_research_wipe_paths", return_value=[source]):
                with self.assertRaises(bot.ArchiveIntegrityError):
                    bot.reset_all_research_files(archive_path=archive)
            self.assertTrue(os.path.isfile(source))

    def test_reset_refuses_any_unarchived_direct_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = os.path.join(tmp, "must-survive.jsonl")
            with open(source, "wb") as handle:
                handle.write(b"evidence\n")
            with mock.patch.object(bot, "all_research_wipe_paths", return_value=[source]):
                result = bot.reset_all_research_files()
            self.assertTrue(result["wipe_aborted"])
            self.assertTrue(os.path.isfile(source))
            self.assertIn("verified archive_path is required", result["errors"][0])

    def test_agent_debug_writes_pause_and_resume_around_epoch_reset(self):
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(bot, "_AGENT_DEBUG_LOG", os.path.join(tmp, "agent-debug.log")), \
             mock.patch.object(bot, "_AGENT_DEBUG_LOG_ALT", os.path.join(tmp, "agent-debug-alt.log")):
            bot._pause_agent_debug_writes()
            bot._agent_dbg("test", "paused", "must not write")
            self.assertFalse(os.path.exists(bot._AGENT_DEBUG_LOG))
            bot._resume_agent_debug_writes()
            bot._agent_dbg("test", "resumed", "must write")
            self.assertTrue(os.path.isfile(bot._AGENT_DEBUG_LOG))

        with bot.state_lock:
            bot.state["execution_paused"] = True
        with bot.trade_lock:
            bot.pending_orders.append({"trade_id": "live-pending"})
        with mock.patch.object(bot, "_seal_past_analysis_with_fallback") as seal, \
             mock.patch.object(bot, "_perform_fresh_collection_reset_quiesced") as quiesced:
            result = bot._perform_fresh_collection_reset_locked(send_local_signal=False)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "fresh_collection_requires_paused_disarmed_flat_boundary")
        self.assertTrue(result.get("wipe_aborted"))
        seal.assert_not_called()
        quiesced.assert_not_called()


if __name__ == "__main__":
    unittest.main()

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


BOT_PATH = Path(__file__).with_name("bot.py")
sys.path.insert(0, str(BOT_PATH.parent))


class PaperLifecycleRestartTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("bot_paper_lifecycle_test", BOT_PATH)
        cls.bot = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.bot)

    def setUp(self):
        env = mock.patch.dict(os.environ, {"FORCE_PAPER_MODE": "true"})
        env.start()
        self.addCleanup(env.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.cwd)
        self.bot.pending_orders.clear()
        self.bot.open_positions.clear()
        self.bot.trades.clear()
        self.bot.trades_map.clear()
        for rows in self.bot.lane_pending_orders.values():
            rows.clear()
        for rows in self.bot.lane_open_positions.values():
            rows.clear()
        self.bot.state["live_armed"] = False

    def _position(self):
        return {
            "trade_id": "family-pos-1", "status": "OPEN", "entry": 78000.0,
            "entry_ts": 1000.0, "research_lane": self.bot.COMBO_EXECUTION_LANES[0],
            "dir": "LONG", "exit_profile_id": "CHANDELIER_3", "atr_entry": 125.0,
            "atr14_3m": 125.0, "atr_tp_price": None,
            "tp": 79950.0, "sl": 77500.0, "sl_enforced": True,
            "peak_pct": 9.0, "mae_pct": -2.0,
        }

    def _order(self):
        return {
            "trade_id": "cont-order-1", "status": "PENDING", "limit_price": 77900.0,
            "created_ts": 1000.0, "entry_expires_ts": 9999999999.0,
            "research_lane": self.bot.COMBO_EXECUTION_LANES[1], "chase_count": 3,
            "last_chase_ts": 1200.0, "signal_dir": "LONG",
            "exit_profile_id": "ATR_TP_2.5_ATR_SL_1.5",
        }

    def _awaiting(self, *, expires_ts=9999999999.0):
        return {
            "trade_id": "family-await-1",
            "status": self.bot.SIGNAL_STATUS_AWAITING_DASHBOARD_CHASE,
            "created_ts": 1000.0, "created_ts_ts": 1000.0,
            "expires_ts": expires_ts,
            "research_lane": self.bot.COMBO_EXECUTION_LANES[0],
            "shared_ai_call_id": "scan-restart-1",
            "final_direction": "LONG", "order_placed": False,
        }

    def test_round_trip_restores_all_lanes_without_loss(self):
        with mock.patch.object(self.bot, "_get_pending_order_evidence_worker") as worker:
            worker.return_value.submit.return_value = True
            self.bot.lane_register_open_position(self._position())
            self.bot.lane_register_pending_order(self._order())
        self.assertTrue(Path(self.bot.PAPER_LIFECYCLE_FILE).exists())
        self.bot.pending_orders.clear(); self.bot.open_positions.clear()
        for rows in self.bot.lane_pending_orders.values(): rows.clear()
        for rows in self.bot.lane_open_positions.values(): rows.clear()
        with mock.patch.object(self.bot, "_get_pending_order_evidence_worker") as worker:
            worker.return_value.submit.return_value = True
            result = self.bot.load_paper_lifecycle()
        self.assertEqual((result["positions"], result["pending_orders"]), (1, 1))
        self.assertEqual(self.bot.open_positions[0]["atr_entry"], 125.0)
        self.assertEqual(self.bot.pending_orders[0]["chase_count"], 3)
        self.assertEqual(self.bot.pending_orders[0]["exit_profile_id"], "ATR_TP_2.5_ATR_SL_1.5")

    def test_family_snapshot_persists_only_enforced_policy_protection(self):
        self.bot.open_positions.append(self._position())
        self.assertTrue(self.bot.save_paper_lifecycle(reason="test"))
        payload = json.loads(Path(self.bot.PAPER_LIFECYCLE_FILE).read_text(encoding="utf-8"))
        saved = payload["positions"][0]
        self.assertIsNone(saved["tp"])
        self.assertIsNone(saved["atr_tp_price"])
        self.assertEqual(saved["tp_policy"], "CHANDELIER")
        self.assertEqual(saved["sl"], 77750.0)
        self.assertTrue(saved["sl_enforced"])
        self.assertEqual(saved["stop_policy"], "CHANDELIER")
        # Snapshot normalization must not rewrite the live accounting object.
        self.assertEqual(self.bot.open_positions[0]["sl"], 77500.0)

    def test_snapshot_retries_transient_nested_mapping_mutation(self):
        class MutatesOnce(dict):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.failed = False

            def items(self):
                if not self.failed:
                    self.failed = True
                    raise RuntimeError("dictionary changed size during iteration")
                return super().items()

        position = self._position()
        position["features"] = MutatesOnce({"velocity": 0.1})
        self.bot.open_positions.append(position)

        self.assertTrue(self.bot.save_paper_lifecycle(reason="transient-race"))
        payload = json.loads(Path(self.bot.PAPER_LIFECYCLE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(payload["positions"][0]["features"]["velocity"], 0.1)

    def test_snapshot_failure_keeps_prior_atomic_file_and_does_not_raise(self):
        self.bot.open_positions.append(self._position())
        self.assertTrue(self.bot.save_paper_lifecycle(reason="baseline"))
        baseline = Path(self.bot.PAPER_LIFECYCLE_FILE).read_bytes()

        with mock.patch.object(
            self.bot,
            "_stable_paper_lifecycle_copy",
            side_effect=RuntimeError("paper lifecycle row remained mutable during snapshot"),
        ):
            self.assertFalse(self.bot.save_paper_lifecycle(reason="persistent-race"))

        self.assertEqual(Path(self.bot.PAPER_LIFECYCLE_FILE).read_bytes(), baseline)

    def test_family_restore_repairs_generic_tp_sl_projection(self):
        payload = {"schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
                   "positions": [self._position()], "pending_orders": []}
        Path(self.bot.PAPER_LIFECYCLE_FILE).write_text(json.dumps(payload), encoding="utf-8")
        result = self.bot.load_paper_lifecycle()
        self.assertEqual(result["positions"], 1)
        restored = self.bot.open_positions[0]
        self.assertIsNone(restored["tp"])
        self.assertIsNone(restored["atr_tp_price"])
        self.assertEqual(restored["sl"], 77750.0)
        self.assertTrue(restored["sl_enforced"])
        self.assertEqual(restored["stop_policy"], "CHANDELIER")

    def test_second_restore_is_idempotent(self):
        payload = {"schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
                   "positions": [self._position()], "pending_orders": [self._order()]}
        Path(self.bot.PAPER_LIFECYCLE_FILE).write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(self.bot, "_get_pending_order_evidence_worker") as worker:
            worker.return_value.submit.return_value = True
            self.bot.load_paper_lifecycle(); result = self.bot.load_paper_lifecycle()
        self.assertEqual(len(self.bot.open_positions), 1)
        self.assertEqual(len(self.bot.pending_orders), 1)
        self.assertEqual(result["duplicates"], 2)

    def test_preorder_awaiting_signal_survives_restart_and_restore_is_idempotent(self):
        signal = self._awaiting()
        self.bot.trades_map[signal["trade_id"]] = {"signal_ref": signal, "ai": {}}
        self.assertTrue(self.bot.save_paper_lifecycle(reason="preorder-awaiting"))
        payload = json.loads(Path(self.bot.PAPER_LIFECYCLE_FILE).read_text(encoding="utf-8"))
        self.assertEqual([row["trade_id"] for row in payload["awaiting_signals"]], [signal["trade_id"]])
        self.bot.trades_map.clear()
        with mock.patch.object(self.bot, "_v3_matching_order_intent_exists", return_value=False):
            first = self.bot.load_paper_lifecycle()
            second = self.bot.load_paper_lifecycle()
        self.assertEqual(first["awaiting_signals"], 1)
        self.assertEqual(second["duplicates"], 1)
        restored = self.bot.trades_map[signal["trade_id"]]["signal_ref"]
        self.assertEqual(restored["status"], self.bot.SIGNAL_STATUS_AWAITING_DASHBOARD_CHASE)
        self.assertEqual(restored["_restart_recovery_provenance"]["schema"], "paper_awaiting_restart_provenance_v1")

    def test_overdue_restart_signal_reconciles_once_with_restart_provenance(self):
        payload = {
            "schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
            "saved_at": "2026-08-27T07:50:00+00:00", "git_rev": "abc123",
            "positions": [], "pending_orders": [],
            "awaiting_signals": [self._awaiting(expires_ts=1.0)],
        }
        Path(self.bot.PAPER_LIFECYCLE_FILE).write_text(json.dumps(payload), encoding="utf-8")
        recorded = []
        with mock.patch.object(self.bot, "_v3_matching_order_intent_exists", return_value=False), \
             mock.patch.object(self.bot, "_record_expired_order", side_effect=lambda row, reason: recorded.append((row, reason)) or {"ok": True}):
            first = self.bot.load_paper_lifecycle()
            second = self.bot.load_paper_lifecycle()
        self.assertEqual(first["overdue_reconciled"], 1)
        self.assertEqual(second["overdue_reconciled"], 0)
        self.assertEqual(len(recorded), 1)
        row, reason = recorded[0]
        self.assertEqual(reason, "RUNTIME_RESTART_SIGNAL_TTL_EXPIRED")
        self.assertEqual(row["_restart_recovery_provenance"]["snapshot_git_rev"], "abc123")

    def test_durable_order_intent_prevents_compensating_no_order(self):
        payload = {
            "schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
            "positions": [], "pending_orders": [],
            "awaiting_signals": [self._awaiting(expires_ts=1.0)],
        }
        Path(self.bot.PAPER_LIFECYCLE_FILE).write_text(json.dumps(payload), encoding="utf-8")
        with mock.patch.object(self.bot, "_v3_matching_order_intent_exists", return_value=True), \
             mock.patch.object(self.bot, "_record_expired_order") as record:
            result = self.bot.load_paper_lifecycle()
        self.assertEqual(result["intent_conflicts"], 1)
        self.assertNotIn("family-await-1", self.bot.trades_map)
        record.assert_not_called()

    def test_restart_indexes_order_intent_ledger_once_before_signal_checks(self):
        awaiting = []
        for index in range(20):
            row = self._awaiting()
            row["trade_id"] = f"family-await-{index}"
            row["shared_ai_call_id"] = f"scan-restart-{index}"
            awaiting.append(row)
        payload = {
            "schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
            "positions": [], "pending_orders": [], "awaiting_signals": awaiting,
        }
        Path(self.bot.PAPER_LIFECYCLE_FILE).write_text(json.dumps(payload), encoding="utf-8")

        identities = {("scan-restart-3", self.bot.COMBO_EXECUTION_LANES[0])}
        original_match = self.bot._v3_matching_order_intent_exists
        with mock.patch.object(
            self.bot, "_load_v3_order_intent_identities", return_value=(identities, True)
        ) as load_index, mock.patch.object(
            self.bot,
            "_v3_matching_order_intent_exists",
            wraps=original_match,
        ) as match_intent:
            result = self.bot.load_paper_lifecycle()

        load_index.assert_called_once_with()
        self.assertEqual(match_intent.call_count, 20)
        self.assertTrue(all("order_intent_identities" in call.kwargs for call in match_intent.call_args_list))
        self.assertEqual(result["intent_conflicts"], 1)
        self.assertEqual(result["awaiting_signals"], 19)

    def test_restored_position_remains_visible_to_exit_engine(self):
        payload = {"schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
                   "positions": [self._position()], "pending_orders": []}
        Path(self.bot.PAPER_LIFECYCLE_FILE).write_text(json.dumps(payload), encoding="utf-8")
        self.bot.load_paper_lifecycle()
        evaluated = []
        with mock.patch.object(self.bot, "refresh_bbo_state"), \
             mock.patch.object(self.bot, "refresh_order_book_state"), \
             mock.patch.object(self.bot, "_observable_exit_price", return_value=78100.0), \
             mock.patch.object(self.bot, "process_funding_accrual"), \
             mock.patch.object(self.bot, "get_mark_price", return_value=78100.0), \
             mock.patch.object(self.bot, "_apply_position_exits", side_effect=lambda pos, mark, now: evaluated.append(pos["trade_id"])):
            self.bot.process_positions()
        self.assertEqual(evaluated, ["family-pos-1"])

    def test_armed_snapshot_is_refused(self):
        payload = {"schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": True,
                   "positions": [self._position()], "pending_orders": []}
        Path(self.bot.PAPER_LIFECYCLE_FILE).write_text(json.dumps(payload), encoding="utf-8")
        result = self.bot.load_paper_lifecycle()
        self.assertEqual(result["invalid"], 1)
        self.assertFalse(self.bot.open_positions)

    def test_terminal_trade_cannot_resurrect_from_stale_paper_snapshot(self):
        position = self._position()
        order = self._order()
        order["trade_id"] = position["trade_id"]
        self.bot.open_positions.append(position)
        self.bot.pending_orders.append(order)
        self.bot.lane_open_positions[self.bot.COMBO_EXECUTION_LANES[0]].append(position)
        self.bot.lane_pending_orders[self.bot.COMBO_EXECUTION_LANES[1]].append(order)
        self.bot.trades.append({
            "trade_id": position["trade_id"],
            "exit_reason": "PATH_END_120M",
        })
        self.bot.trades_map[position["trade_id"]] = {
            "signal_ref": {
                "trade_id": position["trade_id"],
                "status": "CLOSED",
                "outcome": "PATH_END_120M",
            }
        }

        result = self.bot.reconcile_restored_paper_terminal_conflicts()

        self.assertEqual(result["positions"], 1)
        self.assertEqual(result["pending_orders"], 1)
        self.assertFalse(self.bot.open_positions)
        self.assertFalse(self.bot.pending_orders)
        persisted = json.loads(Path(self.bot.PAPER_LIFECYCLE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(persisted["positions"], [])
        self.assertEqual(persisted["pending_orders"], [])

    def test_terminal_reconcile_never_removes_bitfinex_backed_exposure(self):
        position = {**self._position(), "bitfinex_position_id": "live-pos"}
        self.bot.open_positions.append(position)
        self.bot.trades.append({"trade_id": position["trade_id"]})
        result = self.bot.reconcile_restored_paper_terminal_conflicts()
        self.assertEqual(result["positions"], 0)
        self.assertEqual(self.bot.open_positions, [position])

    def test_emergency_snapshot_is_hash_addressed_and_guarded(self):
        self.bot.lane_register_open_position(self._position())
        receipt = self.bot.capture_emergency_paper_lifecycle_snapshot()
        self.assertTrue(Path(receipt["path"]).exists())
        self.assertEqual(len(receipt["sha256"]), 64)
        self.bot.state["live_armed"] = True
        with self.assertRaises(PermissionError):
            self.bot.capture_emergency_paper_lifecycle_snapshot()


if __name__ == "__main__":
    unittest.main()

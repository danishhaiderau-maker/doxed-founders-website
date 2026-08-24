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
            "trade_id": "patient-pos-1", "status": "OPEN", "entry": 78000.0,
            "entry_ts": 1000.0, "research_lane": "OFFSET_029_ATR_TP_25",
            "dir": "LONG", "exit_profile_id": "atr_tp_2_5", "atr_entry": 125.0,
            "atr14_3m": 125.0, "atr_tp_price": 78312.5,
            "tp": 79950.0, "sl": 77500.0, "sl_enforced": True,
            "peak_pct": 9.0, "mae_pct": -2.0,
        }

    def _order(self):
        return {
            "trade_id": "cont-order-1", "status": "PENDING", "limit_price": 77900.0,
            "created_ts": 1000.0, "entry_expires_ts": 9999999999.0,
            "research_lane": "CONTINUOUS", "chase_count": 3,
            "last_chase_ts": 1200.0, "signal_dir": "LONG",
            "exit_profile_id": "scenario_c_ladder_12_to_10_v1",
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
        self.assertEqual(self.bot.pending_orders[0]["exit_profile_id"], "scenario_c_ladder_12_to_10_v1")

    def test_patient_snapshot_persists_only_enforced_atr_protection(self):
        self.bot.open_positions.append(self._position())
        self.assertTrue(self.bot.save_paper_lifecycle(reason="test"))
        payload = json.loads(Path(self.bot.PAPER_LIFECYCLE_FILE).read_text(encoding="utf-8"))
        saved = payload["positions"][0]
        self.assertEqual(saved["tp"], 78312.5)
        self.assertEqual(saved["atr_tp_price"], 78312.5)
        self.assertEqual(saved["tp_policy"], "FROZEN_3M_ATR_TP_2_5X")
        self.assertIsNone(saved["sl"])
        self.assertFalse(saved["sl_enforced"])
        self.assertEqual(saved["stop_policy"], "NONE_TP_ONLY_RESEARCH")
        # Snapshot normalization must not rewrite the live accounting object.
        self.assertEqual(self.bot.open_positions[0]["sl"], 77500.0)

    def test_patient_restore_repairs_legacy_tp_sl_projection(self):
        payload = {"schema": "paper_lifecycle_v1", "paper_only": True, "live_armed": False,
                   "positions": [self._position()], "pending_orders": []}
        Path(self.bot.PAPER_LIFECYCLE_FILE).write_text(json.dumps(payload), encoding="utf-8")
        result = self.bot.load_paper_lifecycle()
        self.assertEqual(result["positions"], 1)
        restored = self.bot.open_positions[0]
        self.assertEqual(restored["tp"], 78312.5)
        self.assertEqual(restored["atr_tp_price"], 78312.5)
        self.assertIsNone(restored["sl"])
        self.assertFalse(restored["sl_enforced"])
        self.assertEqual(restored["stop_policy"], "NONE_TP_ONLY_RESEARCH")

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
        self.assertEqual(evaluated, ["patient-pos-1"])

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
        self.bot.lane_open_positions["OFFSET_029_ATR_TP_25"].append(position)
        self.bot.lane_pending_orders["CONTINUOUS"].append(order)
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

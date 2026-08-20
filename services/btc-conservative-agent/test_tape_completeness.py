"""P0 completeness invariant: capture tape, don't explode the collector."""
import unittest

from chase_offset_touch_grid import orig_limit_price
from order_multiverse import (
    allows_exit_expectancy,
    build_order_multiverse,
    tape_completeness,
)
from path_replay_v1 import (
    COLLECTOR_VERSION,
    CONTROL_CELL,
    FEATURE_SCHEMA_VERSION,
    FILL_MODEL_IDEAL_TOUCH,
    PATH_SCHEMA_VERSION,
    REPLAY_VERSION,
    STAGE1_THESIS,
    expectancies_from_stage1,
    integer_thesis_sweep_0_100,
    mfe_mae_trajectory,
    price_for_short_unreal,
    stage1_replay,
)


def _1m(i, high, low, close, origin=1_700_000_000):
    return [int((origin + i * 60) * 1000), close, high, low, close, 1.0]


def _ticks_dip_then_green(dip=-14.0, recover=8.0):
    entry = 64000.0
    ticks = []
    t = 0.0
    level = 0.0
    while level > dip - 0.01:
        px = price_for_short_unreal(entry, level, 100)
        ticks.append({"t": t, "price": px, "best_ask": px, "best_bid": px - 5, "unreal_pct": level})
        t += 60.0
        level -= 1.0
    for unreal in (-10.0, 0.0, recover):
        px = price_for_short_unreal(entry, unreal, 100)
        ticks.append({"t": t, "price": px, "best_ask": px, "best_bid": px - 5, "unreal_pct": unreal})
        t += 60.0
    return entry, ticks


def _filled_candles(n, signal_price=100000.0, origin=1_700_000_000):
    fill_px = orig_limit_price(signal_price, "SHORT", 0.10)
    return [
        _1m(i, fill_px if i == 2 else signal_price, signal_price - 20, signal_price, origin=origin)
        for i in range(n)
    ]


class TapeCompletenessTests(unittest.TestCase):
    def test_filled_ticket_becomes_complete_with_120m_path(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        fill_ts = signal_ts + 120.0
        fill_px = orig_limit_price(signal_price, "SHORT", 0.10)
        candles = _filled_candles(130, signal_price=signal_price)
        open_row = build_order_multiverse(
            trade_id="cont-filled-open",
            signal_price=signal_price,
            signal_ts=signal_ts,
            candles_1m=candles[:40],
            live_fill_ts=fill_ts,
            live_fill_price=fill_px,
            ticket_closed=False,
            invert_on=False,
        )
        self.assertTrue(open_row["pending"])
        self.assertEqual(open_row["event"], "PENDING")
        self.assertEqual(open_row["lifecycle"], "FILLED")
        self.assertEqual(open_row["completeness"]["entry_outcome"], "FILLED")
        self.assertTrue(open_row["completeness"]["ENTRY_OUTCOME_KNOWN"])
        self.assertFalse(open_row["completeness"]["REPLAYABLE"])
        self.assertFalse(allows_exit_expectancy(open_row["completeness"]))
        self.assertIn("completeness_reason", open_row)
        closed = build_order_multiverse(
            trade_id="cont-filled-closed",
            signal_price=signal_price,
            signal_ts=signal_ts,
            candles_1m=candles,
            live_fill_ts=fill_ts,
            live_fill_price=fill_px,
            ticket_closed=True,
            invert_on=False,
        )
        self.assertFalse(closed["pending"])
        self.assertEqual(closed["event"], "COMPLETE")
        self.assertEqual(closed["lifecycle"], "COMPLETE")
        flags = closed["completeness"]
        self.assertTrue(flags["SIGNAL_RECORDED"])
        self.assertTrue(flags["ENTRY_OUTCOME_KNOWN"])
        self.assertEqual(flags["entry_outcome"], "FILLED")
        self.assertEqual(flags["exit_cohort"], "filled")
        self.assertFalse(flags["EXIT_SWEEP_COMPLETE"])
        self.assertEqual(closed["chase_exit_scores"], [])
        self.assertTrue(flags["REPLAYABLE"])
        self.assertTrue(flags["PATH_COMPLETE"])
        self.assertTrue(allows_exit_expectancy(flags))
        self.assertEqual(closed["invert_on"], False)
        self.assertTrue(closed["live_ticket_unchanged"])
        self.assertGreater(len(closed["path_1m"]), 0)
        self.assertGreater(len(closed["raw_path"]["path_1m"]), 0)
        self.assertIsNotNone(closed["live_fill_ts"])
        self.assertEqual(closed["collector_version"], COLLECTOR_VERSION)
        self.assertEqual(closed["feature_schema_version"], FEATURE_SCHEMA_VERSION)
        self.assertEqual(closed["path_schema_version"], PATH_SCHEMA_VERSION)
        self.assertEqual(closed["replay_version"], REPLAY_VERSION)
        self.assertEqual(closed["fill_model"], FILL_MODEL_IDEAL_TOUCH)
        self.assertIn("REALISTIC_TOUCH", closed["fill_models_supported"])
        self.assertIn("ACTUAL_LIVE", closed["fill_models_supported"])
        self.assertEqual(closed["fill_costs"]["fee_model"], "BITFINEX_ZERO")
        self.assertEqual(closed["control_cell"]["tag"], "CONTROL")
        self.assertTrue(closed["LIVE_CELL"])
        self.assertAlmostEqual(closed["control_cell"]["orig_offset_pct"], 0.10)
        self.assertEqual(closed["control_cell"]["thesis_cut"], -12.0)
        self.assertEqual(closed["control_cell"]["ladder"], [
            [8, 5], [12, 10], [19, 17], [40, 28],
            [60, 45], [80, 60], [100, 75], [150, 120],
        ])
        self.assertEqual(closed["control_cell"]["thesis_window_sec"], 300.0)
        self.assertEqual(closed["control_cell"]["hard_stop_pct"], 30.0)
        self.assertTrue(closed["control_cell"]["hard_stop_closes_paper"])
        self.assertFalse(closed["control_cell"]["invert_on"])
        self.assertIn("MFE", closed)
        self.assertIn("MFE_time", closed)
        self.assertIn("MAE", closed)
        self.assertIn("MAE_time", closed)
        self.assertIsNotNone(closed["derived_features"]["mfe_mae_trajectory"])
        self.assertTrue(closed["derived_features"]["mfe_mae_trajectory"]["path_complete_120m"])
        self.assertEqual(closed["replay_results"]["chase_exit_scores"], [])
        self.assertIn("completeness_reason", closed)
        self.assertIn("usable", closed["completeness_reason"].lower())

    def test_incomplete_120m_path_is_waiting_not_exit_stats(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        fill_ts = signal_ts + 120.0
        fill_px = orig_limit_price(signal_price, "SHORT", 0.10)
        candles = _filled_candles(40, signal_price=signal_price)
        closed = build_order_multiverse(
            trade_id="cont-waiting-120m",
            signal_price=signal_price,
            signal_ts=signal_ts,
            candles_1m=candles,
            live_fill_ts=fill_ts,
            live_fill_price=fill_px,
            ticket_closed=True,
            invert_on=False,
        )
        self.assertTrue(closed["pending"])
        self.assertEqual(closed["event"], "WAITING_120M")
        self.assertEqual(closed["lifecycle"], "WAITING_120M")
        flags = closed["completeness"]
        self.assertEqual(flags["entry_outcome"], "FILLED")
        self.assertFalse(flags["PATH_COMPLETE"])
        self.assertEqual(flags["PATH_INCOMPLETE_REASON"], "WAITING_120M")
        self.assertFalse(flags["REPLAYABLE"])
        self.assertFalse(allows_exit_expectancy(flags))
        self.assertIn("WAITING_120M", closed["completeness_reason"])
        self.assertGreater(len(closed["raw_path"]["path_1m"]), 0)

    def test_filled_closed_missing_path_is_data_error_not_complete(self):
        row = build_order_multiverse(
            trade_id="cont-missing-path",
            signal_price=100000.0,
            signal_ts=1_700_000_000.0,
            candles_1m=[],
            ticks_1s=[],
            live_fill_ts=1_700_000_120.0,
            live_fill_price=orig_limit_price(100000.0, "SHORT", 0.10),
            ticket_closed=True,
        )
        self.assertEqual(row["event"], "DATA_ERROR")
        self.assertEqual(row["lifecycle"], "DATA_ERROR")
        self.assertNotEqual(row["event"], "COMPLETE")
        self.assertTrue(row["pending"])
        flags = row["completeness"]
        self.assertEqual(flags["entry_outcome"], "FILLED")
        self.assertFalse(flags["PATH_COMPLETE"])
        self.assertEqual(flags["PATH_INCOMPLETE_REASON"], "MISSING_PATH")
        self.assertFalse(allows_exit_expectancy(flags))
        self.assertIn("DATA_ERROR", row["completeness_reason"])

    def test_ttl_unfilled_becomes_funnel_only(self):
        flags = tape_completeness(
            signal_recorded=True,
            entry_outcome="TTL_UNFILLED",
            path_complete=None,
            replayable=True,
        )
        self.assertTrue(flags["ENTRY_OUTCOME_KNOWN"])
        self.assertEqual(flags["exit_cohort"], "ttl_unfilled")
        self.assertEqual(flags["lifecycle"], "FUNNEL_ONLY")
        self.assertFalse(allows_exit_expectancy(flags))
        stats = expectancies_from_stage1(
            [{"touched": False}, {"touched": True, "filled": True}],
            cohort="ttl_unfilled",
        )
        self.assertIsNone(stats["p_profit_given_fill"])
        self.assertIsNone(stats["e_pnl_given_fill"])
        self.assertAlmostEqual(stats["p_fill"], 0.5)

        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        high_05 = orig_limit_price(signal_price, "SHORT", 0.05)
        candles = [
            _1m(i, high_05 if i == 4 else signal_price, signal_price - 25, signal_price)
            for i in range(35)
        ]
        row = build_order_multiverse(
            trade_id="cont-ttl-funnel",
            signal_price=signal_price,
            signal_ts=signal_ts,
            candles_1m=candles,
            path_complete=False,
            ttl_sec=1800.0,
        )
        self.assertEqual(row["event"], "FUNNEL_ONLY")
        self.assertEqual(row["lifecycle"], "FUNNEL_ONLY")
        self.assertFalse(row["pending"])
        self.assertEqual(row["completeness"]["entry_outcome"], "TTL_UNFILLED")
        self.assertFalse(allows_exit_expectancy(row["completeness"]))
        self.assertIn("completeness_reason", row)
        self.assertIn("funnel", row["completeness_reason"].lower())

    def test_invert_on_default_off_and_tagged(self):
        row = build_order_multiverse(
            trade_id="cont-inv",
            signal_price=100000.0,
            signal_ts=1_700_000_000.0,
            path_complete=True,
            invert_on=False,
        )
        self.assertFalse(row["invert_on"])
        self.assertFalse(row["control_cell"]["invert_on"])
        on = build_order_multiverse(
            trade_id="cont-inv-on",
            signal_price=100000.0,
            signal_ts=1_700_000_000.0,
            path_complete=True,
            invert_on=True,
        )
        self.assertTrue(on["invert_on"])
        self.assertFalse(on["control_cell"]["invert_on"])
        self.assertFalse(row["invert_on"])

    def test_collector_does_not_store_exit_grid(self):
        signal_price = 100000.0
        fill_px = orig_limit_price(signal_price, "SHORT", 0.10)
        candles = [_1m(i, fill_px if i == 1 else signal_price, signal_price - 10, signal_price) for i in range(40)]
        row = build_order_multiverse(
            trade_id="cont-no-explode",
            signal_price=signal_price,
            signal_ts=1_700_000_000.0,
            candles_1m=candles,
            live_fill_ts=1_700_000_120.0,
            live_fill_price=fill_px,
            ticket_closed=True,
            path_complete=True,
        )
        self.assertLess(row["n"], 5)
        self.assertEqual(row["chase_exit_scores"], [])
        self.assertEqual(row["replay_results"]["chase_exit_scores"], [])
        self.assertEqual(CONTROL_CELL["orig_offset_pct"], 0.10)


class Stage1ReplayTests(unittest.TestCase):
    def test_thesis_12_vs_22_vs_50_on_same_tape(self):
        entry, ticks = _ticks_dip_then_green(dip=-14.0, recover=8.0)
        rows = integer_thesis_sweep_0_100(
            ticks, direction="SHORT", entry_price=entry, hard_stop=None, ladder=((4, 2),),
        )
        by_cut = {row["thesis_cut"]: row for row in rows}
        self.assertEqual(by_cut[-12.0]["exit_reason"], "THESIS_FAST_CUT")
        self.assertEqual(by_cut[-12.0]["FIRST_EXIT"], "THESIS")
        self.assertFalse(by_cut[-12.0]["green"])
        self.assertNotEqual(by_cut[-22.0]["exit_reason"], "THESIS_FAST_CUT")
        self.assertTrue(by_cut[-22.0]["green"])
        self.assertNotEqual(by_cut[-50.0]["exit_reason"], "THESIS_FAST_CUT")
        self.assertTrue(by_cut[-50.0]["green"])
        self.assertIn(-12, STAGE1_THESIS)
        self.assertIn(-50, STAGE1_THESIS)
        self.assertNotIn(-22, STAGE1_THESIS)

    def test_stage1_first_hit_and_trajectory(self):
        entry, ticks = _ticks_dip_then_green(dip=-14.0, recover=8.0)
        sweep = stage1_replay(
            ticks, direction="SHORT", entry_price=entry, fill_t=0.0, invert_on=False,
        )
        self.assertEqual(sweep["schema"], "stage1_replay_v1")
        self.assertFalse(sweep["live_recommendation"])
        self.assertEqual(sweep["cohort"], "filled")
        by_exit = {row["exit"]: row for row in sweep["chase_exit_scores"]}
        self.assertEqual(by_exit["thesis_m12"]["first_hit"], "THESIS_FAST_CUT")
        self.assertNotEqual(by_exit["thesis_m50"]["first_hit"], "THESIS_FAST_CUT")
        traj = mfe_mae_trajectory(ticks, direction="SHORT", entry_price=entry, fill_t=0.0)
        self.assertIn(1, traj["points"])
        self.assertIn(120, traj["points"])
        self.assertLess(traj["final_mae_pct"], -12.0)
        self.assertGreater(traj["final_mfe_pct"], 0.0)
        self.assertEqual(traj["MFE"], traj["final_mfe_pct"])
        self.assertEqual(traj["MAE"], traj["final_mae_pct"])
        self.assertIsNotNone(traj["MFE_time"])
        self.assertIsNotNone(traj["MAE_time"])

    def test_filled_expectancy_not_winrate_only(self):
        stats = expectancies_from_stage1(
            [
                {"pnl": -2.0, "green": False},
                {"pnl": 4.0, "green": True},
                {"pnl": 1.0, "green": True},
            ],
            cohort="filled",
        )
        self.assertAlmostEqual(stats["p_profit_given_fill"], 2 / 3, places=4)
        self.assertAlmostEqual(stats["e_pnl_given_fill"], 1.0, places=4)
        self.assertIn("win rate", stats["note"].lower())


if __name__ == "__main__":
    unittest.main()

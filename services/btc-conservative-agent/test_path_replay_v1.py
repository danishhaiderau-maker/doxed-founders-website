"""Path replay: 4pp grouping is analysis-only; 1pp 21 vs 22 still works."""
import unittest

from path_replay_v1 import (
    LADDER_BUCKETS,
    MARGIN_PP_AS_PRICE_PCT,
    PATH_UNITS,
    STOP_4PP_GRID,
    THESIS_4PP_GRID,
    integer_levels_0_100,
    integer_thesis_sweep_0_100,
    ladder_for_bucket,
    mfe_bucket,
    one_point_thesis_sweep,
    price_for_short_unreal,
    replay_group_report,
    simulate_policy_on_path,
)


ENTRY = 64000.0
LEV = 100


def _tick(t, unreal):
    price = price_for_short_unreal(ENTRY, unreal, LEV)
    return {"t": t, "price": price, "best_ask": price, "best_bid": price - 5, "unreal_pct": unreal}


def _unreal_path(levels, start_t=0, step=1.0, pp=1.0):
    """Dense path so adjacent integer cuts stay distinct."""
    ticks = []
    t = start_t
    if not levels:
        return ticks
    current = float(levels[0])
    ticks.append(_tick(t, current))
    t += step
    for target in levels[1:]:
        target = float(target)
        increment = abs(float(pp)) if target != current else 0.0
        if target < current:
            while current > target + 1e-9:
                current = max(target, round(current - increment, 4))
                ticks.append(_tick(t, current))
                t += step
        elif target > current:
            while current < target - 1e-9:
                current = min(target, round(current + increment, 4))
                ticks.append(_tick(t, current))
                t += step
        else:
            ticks.append(_tick(t, current))
            t += step
    return ticks


def _short_ticks_dip_then_recover(dip_unreal: float):
    """1s marks: drift to ``dip_unreal`` then recover through +8% ladder trigger."""
    ticks = []
    t = 0
    # Approach the dip in 0.5pp steps so 21 vs 22 are distinct samples.
    level = 0.0
    while level > dip_unreal - 0.01:
        price = price_for_short_unreal(ENTRY, level, LEV)
        ticks.append({"t": t, "price": price, "best_ask": price, "best_bid": price - 5})
        t += 1
        level -= 0.5
    # Recover: price falls (short profits) up to +8% MFE then give back to +2 lock.
    for unreal in (-20.0, -10.0, 0.0, 4.0, 8.2, 6.0, 2.0):
        price = price_for_short_unreal(ENTRY, unreal, LEV)
        ticks.append({"t": t, "price": price, "best_ask": price, "best_bid": price - 5})
        t += 1
    return ticks


class PathReplayOnePointTests(unittest.TestCase):
    def test_units_are_margin_not_price(self):
        self.assertEqual(MARGIN_PP_AS_PRICE_PCT, 0.01)
        self.assertEqual(PATH_UNITS, "unrealized_pct_on_100x_margin")
        # −21% margin at 100x is 0.21% adverse price, ~$134 at 64k — not 21% BTC.
        px = price_for_short_unreal(ENTRY, -21.0, LEV)
        self.assertAlmostEqual(px, ENTRY * 1.0021, places=4)

    def test_thesis_21_vs_22_exit_at_different_times(self):
        ticks = _short_ticks_dip_then_recover(-21.5)
        cut_21 = simulate_policy_on_path(
            ticks, direction="SHORT", entry_price=ENTRY, thesis_cut=-21.0,
            hard_stop=None, ladder=((8, 5),),
        )
        cut_22 = simulate_policy_on_path(
            ticks, direction="SHORT", entry_price=ENTRY, thesis_cut=-22.0,
            hard_stop=None, ladder=((8, 5),),
        )
        self.assertEqual(cut_21["exit_reason"], "THESIS_FAST_CUT")
        self.assertEqual(cut_22["exit_reason"], "PROFIT_LOCK_LADDER")
        self.assertNotEqual(cut_21["exit_t"], cut_22["exit_t"])
        self.assertFalse(cut_21["green"])
        self.assertTrue(cut_22["green"])
        self.assertFalse(cut_21["live_recommendation"])

    def test_one_point_sweep_separates_adjacent_levels(self):
        ticks = _short_ticks_dip_then_recover(-21.5)
        rows = one_point_thesis_sweep(
            ticks, direction="SHORT", entry_price=ENTRY,
            thesis_levels=(-21.0, -22.0, -23.0),
            hard_stop=None, ladder=((8, 5),),
        )
        reasons = [row["exit_reason"] for row in rows]
        self.assertEqual(reasons[0], "THESIS_FAST_CUT")
        self.assertEqual(reasons[1], "PROFIT_LOCK_LADDER")
        self.assertEqual(reasons[2], "PROFIT_LOCK_LADDER")

    def test_mae_only_cannot_split_wider_than_path(self):
        # A single MAE of −14% answers tighter cuts, not 21 vs 22.
        ticks = [{"t": 0, "price": price_for_short_unreal(ENTRY, 0, LEV), "best_ask": price_for_short_unreal(ENTRY, 0, LEV)}]
        ticks.append({"t": 1, "price": price_for_short_unreal(ENTRY, -14.0, LEV), "best_ask": price_for_short_unreal(ENTRY, -14.0, LEV)})
        a = simulate_policy_on_path(ticks, direction="SHORT", entry_price=ENTRY, thesis_cut=-21, hard_stop=None)
        b = simulate_policy_on_path(ticks, direction="SHORT", entry_price=ENTRY, thesis_cut=-22, hard_stop=None)
        self.assertEqual(a["exit_t"], b["exit_t"])
        self.assertEqual(a["exit_reason"], "PATH_END")
        self.assertEqual(b["exit_reason"], "PATH_END")


class PathReplayGroupingTests(unittest.TestCase):
    def test_4pp_grids_match_danish_sequence(self):
        self.assertEqual(THESIS_4PP_GRID[:5], (-4, -8, -13, -17, -21))
        self.assertEqual(STOP_4PP_GRID[:5], (4, 8, 13, 17, 21))
        self.assertEqual(THESIS_4PP_GRID[-1], -100)
        self.assertEqual(STOP_4PP_GRID[-1], 100)
        self.assertNotIn(-12, THESIS_4PP_GRID)
        self.assertEqual(LADDER_BUCKETS[0], (0, 5))
        self.assertEqual(LADDER_BUCKETS[1], (5, 10))
        self.assertEqual(LADDER_BUCKETS[-1], (95, 100))
        self.assertEqual(len(LADDER_BUCKETS), 20)

    def test_plus7_mfe_then_minus13_mae_ladder_buckets(self):
        ticks = _unreal_path([0.0, 7.0, -13.0])
        empty = simulate_policy_on_path(
            ticks, direction="SHORT", entry_price=ENTRY, thesis_cut=-100.0,
            hard_stop=None, ladder=ladder_for_bucket((0, 5)),
        )
        five_ten = simulate_policy_on_path(
            ticks, direction="SHORT", entry_price=ENTRY, thesis_cut=-100.0,
            hard_stop=None, ladder=ladder_for_bucket((5, 10)),
        )
        self.assertEqual(empty["exit_reason"], "PATH_END")
        self.assertEqual(five_ten["exit_reason"], "PROFIT_LOCK_LADDER")
        self.assertFalse(empty["exit_reason"] == "PROFIT_LOCK_LADDER")
        report = replay_group_report(ticks, direction="SHORT", entry_price=ENTRY)
        by_bucket = {tuple(row["bucket"]): row for row in report["bucket_ladders"]}
        self.assertFalse(by_bucket[(0, 5)]["locked"])
        self.assertTrue(by_bucket[(5, 10)]["locked"])
        self.assertEqual(report["mfe_bucket"], [5, 10])
        self.assertAlmostEqual(report["mfe_pct"], 7.0, places=1)
        self.assertLessEqual(report["mae_pct"], -12.5)

    def test_thesis_minus8_vs_minus17_differ_on_mae13(self):
        ticks = _unreal_path([0.0, -13.0])
        cut_8 = simulate_policy_on_path(
            ticks, direction="SHORT", entry_price=ENTRY, thesis_cut=-8.0,
            hard_stop=None, ladder=(),
        )
        cut_17 = simulate_policy_on_path(
            ticks, direction="SHORT", entry_price=ENTRY, thesis_cut=-17.0,
            hard_stop=None, ladder=(),
        )
        self.assertEqual(cut_8["exit_reason"], "THESIS_FAST_CUT")
        self.assertEqual(cut_17["exit_reason"], "PATH_END")
        self.assertNotEqual(cut_8["exit_t"], cut_17["exit_t"])
        self.assertAlmostEqual(cut_8["exit_unreal_pct"], -8.0, places=1)

    def test_integer_sweep_still_splits_21_vs_22(self):
        levels = integer_levels_0_100(adverse=True)
        self.assertEqual(levels[21], -21)
        self.assertEqual(levels[22], -22)
        ticks = _short_ticks_dip_then_recover(-21.5)
        rows = integer_thesis_sweep_0_100(
            ticks, direction="SHORT", entry_price=ENTRY,
            hard_stop=None, ladder=((8, 5),),
        )
        by_cut = {row["thesis_cut"]: row for row in rows}
        self.assertEqual(by_cut[-21.0]["exit_reason"], "THESIS_FAST_CUT")
        self.assertEqual(by_cut[-22.0]["exit_reason"], "PROFIT_LOCK_LADDER")
        self.assertNotEqual(by_cut[-21.0]["exit_t"], by_cut[-22.0]["exit_t"])

    def test_shoot_through_second_rung_and_best_combo(self):
        # Peak into 10–15 without retracing to 0, then give back through 5.
        ticks = _unreal_path([0.0, 7.0, 12.0, 4.0])
        report = replay_group_report(ticks, direction="SHORT", entry_price=ENTRY)
        shoot = report["shoot_through"]
        self.assertEqual(mfe_bucket(12.0), (10, 15))
        self.assertTrue(shoot["rung_events"][0]["shot_through"])
        self.assertTrue(shoot["rung_events"][1]["armed"])
        self.assertEqual(shoot["rungs_required"], 2)
        self.assertFalse(shoot["rung_3_vs_4"]["rung_3_armed"])
        self.assertFalse(shoot["rung_3_vs_4"]["rung_4_armed"])
        self.assertIsNotNone(report["best"])
        self.assertFalse(report["live_recommendation"])
        self.assertTrue(report["integer_sweep_available"])
        self.assertEqual(report["live_policy_untouched"]["thesis_cut"], -12.0)


if __name__ == "__main__":
    unittest.main()

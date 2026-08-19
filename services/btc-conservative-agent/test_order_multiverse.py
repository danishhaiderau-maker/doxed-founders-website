import json
import os
import tempfile
import unittest

from chase_offset_touch_grid import orig_limit_price
from order_multiverse import (
    ORDER_MULTIVERSE_FILE,
    SCORE_CAP,
    build_order_multiverse,
    cap_chase_exit_scores,
    compact_json_line,
    paper_multiverse_trade_id,
    policy_reject_n1_perfect_green,
    write_order_multiverse,
)
from path_replay_v1 import first_hit_combo, price_for_short_unreal


def _1m(i, high, low, close, origin=1_700_000_000):
    return [int((origin + i * 60) * 1000), close, high, low, close, 1.0]


class OrderMultiverseTests(unittest.TestCase):
    def test_compact_write_is_one_json_line(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        high = signal_price * 1.0006
        candles = [
            _1m(i, high if i == 2 else signal_price, signal_price - 10, signal_price)
            for i in range(40)
        ]
        row = build_order_multiverse(
            trade_id="mv1",
            signal_price=signal_price,
            signal_ts=signal_ts,
            direction="SHORT",
            candles_1m=candles,
            path_complete=True,
            atr14_pct=0.12,
            donchian_high=signal_price * 1.002,
            donchian_low=signal_price * 0.998,
        )
        line = compact_json_line(row)
        self.assertEqual(line.count("\n"), 0)
        parsed = json.loads(line)
        self.assertEqual(parsed["schema"], "order_multiverse_v1")
        self.assertEqual(parsed["live_orig"], 0.10)
        self.assertTrue(parsed["live_ticket_unchanged"])
        self.assertEqual(len(parsed["touches"]), 30)
        self.assertIsNotNone(parsed["touches"]["0.05"])
        self.assertIsNone(parsed["touches"]["0.20"])
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, ORDER_MULTIVERSE_FILE)
            write_order_multiverse(path, row)
            write_order_multiverse(path, row)
            with open(path, encoding="utf-8") as handle:
                lines = [ln for ln in handle.read().splitlines() if ln]
            self.assertEqual(len(lines), 2)
            self.assertLess(len(lines[0]), 200_000)

    def test_touch_fill_not_blind_shadow(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        limit_05 = orig_limit_price(signal_price, "SHORT", 0.05)
        # Close never tags 0.05%; only a tagged 1m high does.
        candles = [
            _1m(i, limit_05 if i == 3 else signal_price, signal_price - 5, signal_price)
            for i in range(20)
        ]
        row = build_order_multiverse(
            trade_id="mv2",
            signal_price=signal_price,
            signal_ts=signal_ts,
            candles_1m=candles,
            path_complete=True,
        )
        self.assertIsNotNone(row["touches"]["0.05"])
        self.assertAlmostEqual(row["touches"]["0.05"], signal_ts + 3 * 60, delta=1.0)
        shadow = [
            _1m(i, signal_price + 1, signal_price - 5, signal_price)
            for i in range(20)
        ]
        miss = build_order_multiverse(
            trade_id="mv2b",
            signal_price=signal_price,
            signal_ts=signal_ts,
            candles_1m=shadow,
            path_complete=True,
        )
        self.assertIsNone(miss["touches"]["0.05"])
        self.assertEqual(miss["n_touched"], 0)

    def test_first_hit_stop_beats_later_tp(self):
        entry = 64000.0
        ticks = []
        t = 0
        for unreal in (0.0, -8.0, 20.0):
            px = price_for_short_unreal(entry, unreal, 100)
            ticks.append({"t": t, "price": px, "best_ask": px, "best_bid": px - 1, "unreal_pct": unreal})
            t += 10
        from path_replay_v1 import simulate_atr_stop, simulate_atr_take_profit
        tp = simulate_atr_take_profit(
            ticks, direction="SHORT", entry_price=entry, atr14_pct=0.05, k=2.0, fill_t=0,
        )
        sl = simulate_atr_stop(
            ticks, direction="SHORT", entry_price=entry, atr14_pct=0.05, k=1.0, fill_t=0,
        )
        hit = first_hit_combo(tp, sl)
        self.assertEqual(hit["winner"], "SL")
        self.assertTrue(hit["sl_fired_first"])
        self.assertFalse(hit["green"])

    def test_pending_when_path_short_then_complete(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        high = orig_limit_price(signal_price, "SHORT", 0.10)
        short_path = [_1m(i, high if i == 1 else signal_price, signal_price - 10, signal_price) for i in range(5)]
        pending = build_order_multiverse(
            trade_id="mv3",
            signal_price=signal_price,
            signal_ts=signal_ts,
            candles_1m=short_path,
            path_complete=False,
        )
        self.assertTrue(pending["pending"])
        self.assertEqual(pending["event"], "PENDING")
        self.assertEqual(pending["chase_exit_scores"], [])
        long_path = [
            _1m(i, high if i == 1 else signal_price * (0.999 if i > 10 else 1.0), signal_price - 40, signal_price)
            for i in range(150)
        ]
        done = build_order_multiverse(
            trade_id="mv3",
            signal_price=signal_price,
            signal_ts=signal_ts,
            candles_1m=long_path,
            path_complete=True,
            atr14_pct=0.10,
        )
        self.assertFalse(done["pending"])
        self.assertTrue(any(s["exit"] == "live_4_2_t12" for s in done["chase_exit_scores"]))
        self.assertTrue(any(s["orig"] == 0.10 for s in done["chase_exit_scores"]))

    def test_cap_keeps_live_baseline(self):
        scores = []
        for i in range(SCORE_CAP + 80):
            scores.append({
                "orig": 0.03,
                "chase": "all_on_s50_i60",
                "exit": f"noise_{i}",
                "pnl": float(i),
                "first_hit": "PATH_END",
                "green": True,
            })
        scores.append({
            "orig": 0.10, "chase": "no_chase", "exit": "live_4_2_t12",
            "pnl": 0.5, "first_hit": "PROFIT_LOCK_LADDER", "green": True,
        })
        scores.append({
            "orig": 0.10, "chase": "no_chase", "exit": "live_c_t12",
            "pnl": -0.2, "first_hit": "THESIS_FAST_CUT", "green": False,
        })
        capped = cap_chase_exit_scores(scores)
        self.assertLessEqual(len(capped), SCORE_CAP)
        exits = {(row["orig"], row["chase"], row["exit"]) for row in capped}
        self.assertIn((0.10, "no_chase", "live_4_2_t12"), exits)
        self.assertIn((0.10, "no_chase", "live_c_t12"), exits)

    def test_n1_100_green_is_not_policy(self):
        self.assertTrue(policy_reject_n1_perfect_green(1, True))
        self.assertFalse(policy_reject_n1_perfect_green(12, True))
        self.assertTrue(policy_reject_n1_perfect_green(0, False))

    def test_paper_id_not_lab_hunter_substitute(self):
        self.assertEqual(
            paper_multiverse_trade_id("lab-hunter-abc", "cont-deadbeef"),
            "cont-deadbeef",
        )
        self.assertEqual(paper_multiverse_trade_id("lab-continuous-xyz"), "")
        self.assertEqual(paper_multiverse_trade_id("tbh-hunter-1"), "")
        self.assertEqual(paper_multiverse_trade_id("cont-paper"), "cont-paper")

    def test_ttl_complete_without_waiting_120m_when_live_01_never_fills(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        high_05 = orig_limit_price(signal_price, "SHORT", 0.05)
        # ~35m of 1m path (covers TTL 30m). 0.05% high-touches; 0.10% and 0.30% never.
        candles = [
            _1m(i, high_05 if i == 4 else signal_price, signal_price - 25, signal_price)
            for i in range(35)
        ]
        row = build_order_multiverse(
            trade_id="cont-ttl-unfilled",
            signal_price=signal_price,
            signal_ts=signal_ts,
            direction="SHORT",
            candles_1m=candles,
            path_complete=False,
            ttl_sec=1800.0,
            atr14_pct=0.10,
        )
        self.assertFalse(row["pending"])
        self.assertEqual(row["event"], "COMPLETE")
        self.assertEqual(row["live_orig"], 0.10)
        self.assertIsNotNone(row["touches"]["0.05"])
        self.assertIsNone(row["touches"]["0.10"])
        self.assertIsNone(row["touches"]["0.30"])
        origs = {round(float(s["orig"]), 2) for s in row["chase_exit_scores"]}
        self.assertIn(0.05, origs)
        self.assertNotIn(0.10, origs)
        self.assertTrue(any(s["orig"] == 0.05 and s["green"] for s in row["chase_exit_scores"]))
        self.assertGreater(row["n_missed"], 0)
        # Adverse remaining path stays below 0.10% so live orig still misses.
        against = signal_price * 1.0009
        red_candles = [
            _1m(i, (high_05 if i == 4 else against if i >= 8 else signal_price), signal_price - 5, signal_price)
            for i in range(35)
        ]
        red = build_order_multiverse(
            trade_id="cont-ttl-unfilled-red",
            signal_price=signal_price,
            signal_ts=signal_ts,
            direction="SHORT",
            candles_1m=red_candles,
            path_complete=True,
            ttl_sec=1800.0,
        )
        self.assertFalse(red["pending"])
        self.assertTrue(any(s["orig"] == 0.05 and s["green"] is False for s in red["chase_exit_scores"]))
        self.assertIsNone(red["touches"]["0.10"])
        self.assertIsNone(red["touches"]["0.30"])


if __name__ == "__main__":
    unittest.main()

import unittest

from chase_offset_touch_grid import (
    OFFSET_PCT_GRID,
    arm_touch_grid_rows,
    new_grid_state,
    orig_limit_price,
    pending_limit_touched,
    poll_grid_state,
    simulate_touch_fill,
)


def _1m(i, high, low, close, origin=1_700_000_000):
    return [int((origin + i * 60) * 1000), close, high, low, close, 1.0]


class TouchGridTests(unittest.TestCase):
    def test_offset_grid_is_0_01_to_0_30(self):
        self.assertEqual(OFFSET_PCT_GRID[0], 0.01)
        self.assertEqual(OFFSET_PCT_GRID[9], 0.10)
        self.assertEqual(OFFSET_PCT_GRID[-1], 0.30)
        self.assertEqual(len(OFFSET_PCT_GRID), 30)

    def test_synthetic_1m_highs_fill_0_05_not_0_20(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        # Rally 0.06% — enough to touch 0.05% short limit, not 0.20%.
        high = signal_price * 1.0006
        candles = [_1m(i, high if i == 2 else signal_price, signal_price - 10, signal_price) for i in range(10)]
        hit = simulate_touch_fill(
            candles,
            signal_ts=signal_ts,
            signal_price=signal_price,
            direction="SHORT",
            offset_pct=0.05,
        )
        miss = simulate_touch_fill(
            candles,
            signal_ts=signal_ts,
            signal_price=signal_price,
            direction="SHORT",
            offset_pct=0.20,
        )
        self.assertTrue(hit["touched"])
        self.assertAlmostEqual(hit["fill_price"], orig_limit_price(signal_price, "SHORT", 0.05), places=4)
        self.assertFalse(miss["touched"])

    def test_pending_limit_touched_matches_short_high_or_bid(self):
        self.assertTrue(pending_limit_touched(side="sell", limit_price=100, high=100, last=99))
        self.assertTrue(pending_limit_touched(side="sell", limit_price=100, bid=100.1, last=99))
        self.assertFalse(pending_limit_touched(side="sell", limit_price=100, high=99.9, last=99.8, bid=99.7))

    def test_live_poll_does_not_place_orders_and_records_touch(self):
        rows = arm_touch_grid_rows(
            trade_id="t1",
            direction="SHORT",
            signal_price=100000.0,
            signal_ts=1.0,
        )
        self.assertEqual(len(rows), 30)
        self.assertTrue(any(r["places_live_order"] for r in rows if r["offset_pct"] == 0.10))
        self.assertEqual(sum(1 for r in rows if r["places_live_order"]), 1)
        state = new_grid_state(rows)
        updates = poll_grid_state(state, now_ts=2.0, last=100000.0, high=100000.0)
        self.assertEqual(updates, [])
        # 0.05% of 100000 = 50 → limit 100050. Touch with last 100060.
        updates = poll_grid_state(state, now_ts=3.0, last=100060.0, high=100060.0)
        touched_offsets = {row["offset_pct"] for row in updates}
        self.assertIn(0.05, touched_offsets)
        self.assertNotIn(0.20, touched_offsets)


if __name__ == "__main__":
    unittest.main()

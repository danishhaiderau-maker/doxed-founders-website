"""3-minute RSI / StochRSI / ADX from 1m resample — not 5m."""
import unittest

from cycle_3m_indicators import (
    DECISION_BAR,
    DECISION_BAR_SEC,
    SOURCE_BAR,
    SOURCE_NOTE,
    UNIVERSE_SCHEMA,
    atr14_pct_of_price,
    bollinger_width,
    compute_3m_exhaustion_snapshot,
    compute_3m_universe_snapshot,
    donchian_channel,
    resample_1m_to_3m,
    stoch_rsi,
    wilder_atr,
    wilder_rsi,
    would_block_short_3m,
)


def _1m_row(i, close, high=None, low=None, volume=1.0, origin_ms=1_700_000_000_000):
    # Align origin to a 3-minute epoch so 6 oneshot 1m bars become exactly 2 3m bars.
    aligned = (origin_ms // 180_000) * 180_000
    ts_ms = aligned + i * 60_000
    high = close + 1 if high is None else high
    low = close - 1 if low is None else low
    return [ts_ms, close, high, low, close, volume]


class Cycle3mIndicatorTests(unittest.TestCase):
    def test_resample_groups_three_one_minute_bars(self):
        rows = [_1m_row(i, 100 + i, high=110 + i, low=90 + i, volume=2) for i in range(6)]
        bars = resample_1m_to_3m(rows)
        self.assertEqual(len(bars), 2)
        first = bars[0]
        self.assertEqual(first[1], 100)  # open
        self.assertEqual(first[4], 102)  # close of third 1m
        self.assertEqual(first[5], 6)    # volume sum
        self.assertEqual(DECISION_BAR, "3m")
        self.assertEqual(SOURCE_BAR, "1m")
        self.assertEqual(DECISION_BAR_SEC, 180)
        self.assertIn("1m_resampled_to_3m", SOURCE_NOTE)

    def test_wilder_rsi_hits_bounds_on_monotone_series(self):
        up = list(range(40, 80))
        down = list(range(80, 40, -1))
        self.assertGreater(wilder_rsi(up), 70)
        self.assertLess(wilder_rsi(down), 30)

    def test_rsi_alone_does_not_block(self):
        self.assertFalse(would_block_short_3m(rsi=20.0, stoch_k=40.0, adx=25.0))
        self.assertFalse(would_block_short_3m(rsi=20.0, stoch_k=0.0, adx=10.0))
        self.assertTrue(would_block_short_3m(rsi=20.0, stoch_k=0.0, adx=25.0))

    def test_snapshot_uses_3m_not_5m_and_stamps_log_only(self):
        # Strong selloff 1m closes so 3m RSI is oversold.
        rows = [_1m_row(i, 1000 - i) for i in range(200)]
        snap = compute_3m_exhaustion_snapshot(rows, dist_to_support=0.002, structure_score=-4)
        self.assertEqual(snap["bar"], "3m")
        self.assertEqual(snap["source_bar"], "1m")
        self.assertFalse(snap["hard_veto"])
        self.assertIsNotNone(snap["rsi14"])
        self.assertIn("stoch_rsi_k", snap)
        self.assertIn("adx14", snap)
        self.assertIn("3m exhaustion", snap["line"])
        stoch = stoch_rsi([c[4] for c in resample_1m_to_3m(rows)])
        self.assertIsNotNone(stoch["k"])

    def test_atr_pct_matches_known_true_range(self):
        # Each 1m bar ranges 10 around close 1000; 3m TR stays 10 → ATR% = 1.0.
        rows = [_1m_row(i, 1000, high=1005, low=995) for i in range(120)]
        bars = resample_1m_to_3m(rows)
        atr = wilder_atr(bars)
        self.assertAlmostEqual(atr, 10.0, places=4)
        self.assertAlmostEqual(atr14_pct_of_price(bars), 1.0, places=4)

    def test_donchian_loc_at_high_and_low(self):
        lows = [_1m_row(i, 100, high=101, low=99) for i in range(60)]
        highs = [_1m_row(i + 60, 200, high=210, low=190) for i in range(3)]
        bars = resample_1m_to_3m(lows + highs)
        loc_high = donchian_channel(bars)
        self.assertGreater(loc_high["loc"], 0.9)
        self.assertEqual(loc_high["high"], 210)
        high_first = [_1m_row(i, 200, high=210, low=190) for i in range(60)]
        dump = [_1m_row(i + 60, 100, high=101, low=90) for i in range(3)]
        loc_low = donchian_channel(resample_1m_to_3m(high_first + dump))
        self.assertLess(loc_low["loc"], 0.1)
        self.assertEqual(loc_low["low"], 90)

    def test_universe_snapshot_has_collection_fields(self):
        rows = [_1m_row(i, 1000 + (i % 7), high=1010, low=990) for i in range(200)]
        snap = compute_3m_universe_snapshot(
            rows,
            dist_to_support=0.01,
            dist_to_resistance=0.02,
            delta_3m=12.5,
            imbalance_3m=0.4,
            ts=1_700_000_180,
            cycle_outcome="SKIPPED",
            skip_reason="NO_SETUP",
        )
        self.assertEqual(snap["schema"], UNIVERSE_SCHEMA)
        self.assertEqual(snap["bar"], "3m")
        self.assertFalse(snap["hard_veto"])
        self.assertFalse(snap["live_veto"])
        for key in (
            "rsi14", "stoch_rsi_k", "adx14", "atr14_pct_3m", "donchian_loc_3m",
            "bb_width_3m", "delta_3m", "imbalance_3m", "dist_to_support",
            "dist_to_resistance", "session_utc", "hour_utc",
        ):
            self.assertIn(key, snap)
        self.assertEqual(snap["delta_3m"], 12.5)
        self.assertEqual(snap["cycle_outcome"], "SKIPPED")
        self.assertIsNotNone(bollinger_width([c[4] for c in resample_1m_to_3m(rows)]))


if __name__ == "__main__":
    unittest.main()

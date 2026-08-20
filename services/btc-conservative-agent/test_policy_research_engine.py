"""Constrained path-replay extra collectors and P0 coverage contracts."""
import unittest

from counterfactual_coverage import compact_cf_row, cover_universe
from policy_research_engine import (
    LIVE_HARD_STOP_PCT,
    UNKNOWN_CANNOT_COLLECT,
    canonical_opportunity,
    cost_completeness,
    decision_pack,
    episode_tag,
    path_gaps,
    replay_path,
    setup_dna,
)


def _ticks(n=8, dt=60, bid=64000.0, ask=64010.0):
    return [
        {"t": i * dt, "best_bid": bid - i, "best_ask": ask - i, "observed_ts": 1_000_000 + i * dt, "price": bid - i}
        for i in range(n)
    ]


class ExtraCollectorsTests(unittest.TestCase):
    def test_five_clustered_shorts_are_one_episode(self):
        rows = []
        for i in range(5):
            rows.append({
                "trade_id": f"cont-short-{i}",
                "direction": "SHORT",
                "ts_unix": 1_000_000 + i * 45,
                "limit_price": 64100 + i,
            })
        tagged = episode_tag(rows, window_sec=240, price_bps=9)
        ids = {item["episode_id"] for item in tagged.values()}
        self.assertEqual(len(ids), 1)
        self.assertEqual(tagged["cont-short-0"]["n_signals_in_episode"], 5)
        self.assertEqual(tagged["cont-short-4"]["rank_in_episode"], 5)

    def test_gap_over_bound_is_censored_not_zero(self):
        ticks = [
            {"t": 0, "observed_ts": 1_000_000, "best_ask": 64000},
            {"t": 60, "observed_ts": 1_000_060, "best_ask": 64010},
            {"t": 120, "observed_ts": 1_000_120 + 40, "best_ask": 64020},  # 40s gap
        ]
        gaps = path_gaps(ticks)
        self.assertTrue(gaps["censored"])
        self.assertEqual(gaps["censor_reason"], "MAX_GAP_EXCEEDED")
        row = compact_cf_row({"trade_id": "cont-gap", "net_pnl_usd": 0, "direction": "SHORT"}, ticks=ticks)
        self.assertIsNone(row["net_pnl_usd"])
        self.assertTrue(row["not_a_trade"])

    def test_never_executable_is_not_zero(self):
        row = compact_cf_row({
            "trade_id": "cont-never",
            "fill_origin": {"classification": "NEVER_EXECUTABLE"},
            "not_a_trade": True,
            "net_pnl_usd": 0,
        }, ticks=[])
        self.assertIsNone(row["net_pnl_usd"])
        self.assertEqual(row["exclusion_reason"], "NEVER_EXECUTABLE")

    def test_setup_dna_is_numeric(self):
        dna = setup_dna({"adx": 28.4, "spread_bps": 6.2, "atr": 140, "features": {"imbalance": 0.31}})
        self.assertEqual(dna["adx"], 28.4)
        self.assertEqual(dna["spread_bps"], 6.2)
        self.assertEqual(dna["imbalance"], 0.31)

    def test_cost_incomplete_cannot_authorize(self):
        costs = cost_completeness({"actual_costs": {"entry_fee_usd": 0.02}})
        self.assertFalse(costs["cost_complete"])
        self.assertIn("exit_fee", costs["missing_legs"])
        pack = decision_pack("LADDER", "EARLY_TIGHT", [{"net_pnl_usd": 1, "cost_complete": False, "episode_id": "ep-1"}])
        self.assertFalse(pack["live_change_authorized"])
        self.assertIn("QUALIFIED_HOLDOUT_ZERO", pack["missing_evidence"] or ["QUALIFIED_HOLDOUT_ZERO"])

    def test_skip_and_time_stop_and_no_stop_research_only(self):
        ticks = _ticks(30, dt=60, bid=64100, ask=64120)
        skip = replay_path(ticks, direction="SHORT", entry_price=64110, fill_t=0, qty=0.03, leverage=10, ladder=(), thesis_cut=-12, thesis_min_age=0, hard_stop=13, skip=True)
        self.assertEqual(skip["exit_reason"], "SKIP")
        self.assertIsNone(skip["net_pnl_usd"])
        timed = replay_path(ticks, direction="SHORT", entry_price=64110, fill_t=0, qty=0.03, leverage=10, ladder=(), thesis_cut=-99, thesis_min_age=0, hard_stop=99, time_stop_sec=300)
        self.assertEqual(timed["exit_reason"], "TIME_STOP")
        nostop = replay_path(ticks, direction="SHORT", entry_price=64110, fill_t=0, qty=0.03, leverage=10, ladder=(), thesis_cut=-99, thesis_min_age=0, hard_stop=None)
        self.assertTrue(nostop["research_only_no_stop"])
        self.assertEqual(LIVE_HARD_STOP_PCT, 30.0)

    def test_ambiguous_same_sample_uses_conservative_order(self):
        ticks = [
            {"t": 0, "best_ask": 64000, "price": 64000},
            {"t": 10, "best_ask": 64832, "price": 64832},  # +13% on 10x ≈ huge; use price path
        ]
        # SHORT: price rise is adverse. Build a tick that hits hard stop and time stop together.
        ticks = [
            {"t": 0, "best_ask": 64000, "best_bid": 63990, "price": 64000},
            {"t": 300, "best_ask": 64850, "best_bid": 64840, "price": 64850},
        ]
        result = replay_path(
            ticks, direction="SHORT", entry_price=64000, fill_t=0, qty=0.03, leverage=10,
            ladder=((4, 2),), thesis_cut=-1, thesis_min_age=0, hard_stop=8, time_stop_sec=300,
        )
        self.assertTrue(result["ambiguous_same_sample"])
        self.assertEqual(result["exit_reason"], "HARD_STOP")

    def test_unknowns_are_not_imputed(self):
        record = canonical_opportunity({"trade_id": "cont-unk"}, ticks=[])
        for item in UNKNOWN_CANNOT_COLLECT:
            self.assertIn(item, record["unknown_cannot_collect"])
        self.assertEqual(record["microstructure"]["queue_position"], "UNKNOWN")

    def test_cover_universe_compacts_shadow_without_fat_obs(self):
        shadow = {
            "cont-a": {"trade_id": "cont-a", "direction": "SHORT", "limit_price": 64000},
            "cont-b": {"trade_id": "cont-b", "direction": "SHORT", "fill_origin": {"classification": "NEVER_EXECUTABLE"}, "not_a_trade": True},
        }
        cf = {"cont-a": {"trade_id": "cont-a", "direction": "SHORT"}}
        report = cover_universe(shadow, cf, replays={
            "cont-a": {"trade_id": "cont-a", "direction": "SHORT", "ticks": _ticks(5)},
        })
        self.assertEqual(report["n_compact_out"], 2)
        self.assertTrue(report["rows"]["cont-a"]["observations_elided"])
        self.assertNotIn("observations", report["rows"]["cont-a"])
        self.assertIsNone(report["rows"]["cont-b"]["net_pnl_usd"])
        self.assertFalse(report["live_policy_change_allowed"])


if __name__ == "__main__":
    unittest.main()

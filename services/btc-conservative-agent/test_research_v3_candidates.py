import tempfile
import unittest
from collections import Counter

from research_v3_candidates import (
    _conservative_ohlc_prices,
    evaluate_protection_screen,
    load_candidate_inputs,
    protection_screen,
)
from research_v3_store import V3EvidenceStore
from research_v3_ranking import rank_safe_policies


def source(event_id="event-1", episode_id="episode-1"):
    return {
        "event_id": event_id,
        "episode_id": episode_id,
        "signal_ts": 1000,
        "regime": "BULL",
        "direction": "LONG",
        "atr14_pct": 0.1,
        "leverage": 100,
        "margin_usd": 20,
        "entry_children": [{
            "entry_policy_id": "OFFSET_0.29_CHASE_patient",
            "offset_pct": 0.29,
            "chase_id": "patient",
            "fill_ts": 1000,
            "fill_price": 100,
            "fill_model": "IDEAL_TOUCH",
        }],
        "ordered_1s_prices": [
            {"ts": 1000, "price": 100},
            {"ts": 1001, "price": 100.3},
        ],
    }


class V3CandidateTests(unittest.TestCase):
    def test_current_actual_paper_schema_materializes_complete_policy_grid(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-clean")
            segment = store.put_market_segment(
                source="TEST_1S", symbol="BTCUSD", timeframe="1s",
                start_ts=1000, end_ts=1002,
                rows=[
                    {"ts": 1000, "price": 100},
                    {"ts": 1001, "price": 100.2},
                    {"ts": 1002, "price": 100.4},
                ],
            )
            store.append("opportunity", {
                "record_id": "opportunity:episode-1", "episode_id": "episode-1",
                "signal_ts": 1000,
                "feature_snapshot_at_signal": {"market_context": {"regime_label": "BULL"}},
            })
            store.append("order_intent", {
                "record_id": "order-intent:event-1:paper-submit", "episode_id": "episode-1",
                "event_id": "event-1", "executed_direction": "LONG",
                "signal_price": 99.71, "limit_price": 100.0,
                "policy_id": "OFFSET_0.29_CHASE_w234_s25_i60|atr_tp_k2.5",
                "paper_policy_spec": {
                    "entry_limit_policy": "OFFSET_0.29_CHASE_w234_s25_i60",
                    "entry_offset_fraction": 0.0029,
                },
            })
            store.append("execution", {
                "record_id": "execution:event-1:primary-fill", "episode_id": "episode-1",
                "event_id": "event-1", "fill_ts": 1000, "fill_price": 100,
                "fill_model": "PAPER_OBSERVED",
            })
            store.append("lifecycle", {
                "record_id": "lifecycle:event-1:terminal", "episode_id": "episode-1",
                "event_id": "event-1", "terminal": True,
                "outcome_state": "REALIZED_PROFIT", "market_segment_refs": [segment],
            })
            with open(store.root / "cycle_3m_universe.jsonl", "w", encoding="utf-8") as handle:
                handle.write('{"trade_id":"event-1","atr14_pct_3m":0.1}\n')

            rows = load_candidate_inputs(tmp, epoch_id="epoch-clean")
            report = evaluate_protection_screen(rows)

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["atr14_pct"], 0.1)
            self.assertEqual(rows[0]["entry_children"][0]["fill_price"], 100)
            self.assertEqual(rows[0]["entry_children"][0]["offset_pct"], 0.29)
            self.assertEqual(report["unique_policies_evaluated"], len(protection_screen()))

    def test_current_schema_identity_mismatch_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-clean")
            store.append("opportunity", {
                "record_id": "opportunity:episode-good", "episode_id": "episode-good",
                "signal_ts": 1000,
            })
            store.append("order_intent", {
                "record_id": "order-intent:event-1", "episode_id": "episode-wrong",
                "event_id": "event-1", "executed_direction": "LONG",
                "policy_id": "OFFSET_0.29_CHASE_patient",
            })
            store.append("lifecycle", {
                "record_id": "lifecycle:event-1:terminal", "episode_id": "episode-good",
                "event_id": "event-1", "terminal": True,
            })
            self.assertEqual(load_candidate_inputs(tmp, epoch_id="epoch-clean"), [])

    def test_candidate_regime_reads_nested_signal_market_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-clean")
            segment = store.put_market_segment(
                source="TEST_1S", symbol="BTCUSD", timeframe="1s",
                start_ts=1000, end_ts=1001,
                rows=[{"ts": 1000, "price": 100}, {"ts": 1001, "price": 101}],
            )
            store.append("opportunity", {
                "record_id": "opportunity:episode-1", "episode_id": "episode-1",
                "signal_ts": 1000,
                "feature_snapshot_at_signal": {"market_context": {"regime_label": "BULL"}},
            })
            store.append("order_intent", {
                "record_id": "order-intent:event-1", "episode_id": "episode-1",
                "event_id": "event-1", "executed_direction": "LONG",
            })
            store.append("lifecycle", {
                "record_id": "lifecycle:event-1:terminal", "episode_id": "episode-1",
                "event_id": "event-1", "terminal": True,
                "market_segment_refs": [segment],
            })

            rows = load_candidate_inputs(tmp, epoch_id="epoch-clean")

            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["regime"], "BULL")

    def test_one_minute_fallback_is_adverse_first(self):
        candle = [{"t": 60, "o": 100, "h": 102, "l": 98, "c": 101}]
        self.assertEqual([row["price"] for row in _conservative_ohlc_prices(candle, direction="LONG")], [100, 98, 102, 101])
        self.assertEqual([row["price"] for row in _conservative_ohlc_prices(candle, direction="SHORT")], [100, 102, 98, 101])
    def test_screen_contains_requested_loss_and_profit_protection_families(self):
        names = {row["protection_id"] for row in protection_screen()}
        self.assertIn("ATR_TP_2.5_ATR_SL_1", names)
        self.assertIn("ATR_TP_2.5_THESIS_12_HARD_30", names)
        self.assertIn("ATR_TP_2.5_SCENARIO_C", names)
        self.assertIn("ATR_TP_2.5_SCENARIO_C_ATR_SL_0.5", names)
        self.assertIn("ATR_TP_2.5_SCENARIO_C_ATR_SL_1", names)
        self.assertIn("ATR_TP_2.5_SCENARIO_C_ATR_SL_3", names)
        self.assertIn("ATR_TP_2.5_TIME_120", names)
        self.assertIn("ATR_TP_2.5_BE_4_LOCK_1", names)
        self.assertIn("ATR_TP_2.5_GIVEBACK_40PCT", names)
        self.assertIn("HYBRID_SL1_PT25_25_BE1.25_TRAIL1_TP2.5", names)

        candidate = next(
            row for row in protection_screen()
            if row["protection_id"] == "HYBRID_SL1_PT25_25_BE1.25_TRAIL1_TP2.5"
        )
        self.assertEqual(candidate["loss_protection"]["atr_stop_k"], 1.0)
        self.assertEqual(candidate["profit_protection"]["atr_tp_k"], 2.5)
        self.assertEqual(candidate["profit_protection"]["break_even_arm_atr_k"], 1.25)
        self.assertEqual(candidate["profit_protection"]["partial_take_profits"], [[1.0, 0.25], [1.5, 0.25]])

        scenario_with_stop = next(
            row for row in protection_screen()
            if row["protection_id"] == "ATR_TP_2.5_SCENARIO_C_ATR_SL_1"
        )
        self.assertEqual(scenario_with_stop["loss_protection"]["atr_stop_k"], 1.0)
        self.assertEqual(scenario_with_stop["loss_protection"]["thesis_cut_margin_pct"], -12)
        self.assertEqual(scenario_with_stop["profit_protection"]["atr_tp_k"], 2.5)
        self.assertTrue(scenario_with_stop["profit_protection"]["ladder"])

    def test_descriptive_policies_are_visible_but_never_safe_qualified(self):
        progress = []
        report = evaluate_protection_screen([source()], progress_callback=progress.append)
        self.assertEqual(report["unique_policies_evaluated"], len(protection_screen()))
        self.assertTrue(report["descriptive_top_100"])
        selection = report["descriptive_selection"]
        self.assertEqual(selection["method"], "GLOBAL_RANK_THEN_FAMILY_CAP")
        self.assertEqual(selection["per_family_cap"], 2)
        family_counts = Counter(
            row["policy_family"] for row in report["descriptive_top_100"]
        )
        self.assertTrue(family_counts)
        self.assertLessEqual(max(family_counts.values()), 2)
        self.assertEqual(selection["families_represented"], len(family_counts))
        self.assertEqual(selection["globally_ranked_policies"], len(report["candidates"]))
        self.assertTrue(all(row["family_rank"] in {1, 2} for row in report["descriptive_top_100"]))
        self.assertEqual(progress[-1]["phase"], "PROTECTION_REPLAY")
        self.assertEqual(progress[-1]["input_events_completed"], 1)
        self.assertEqual(progress[-1]["input_events_total"], 1)
        sweep = report["scenario_c_atr_stop_sweep"]
        self.assertEqual(sweep["qualification"], "DESCRIPTIVE_ONLY")
        self.assertEqual(
            set(sweep["leaders_by_stop"]),
            {"0.5", "0.75", "1", "1.25", "1.5", "2", "2.5", "3", "CONTROL_NO_ATR_STOP"},
        )
        self.assertEqual(
            list(sweep["leaders_by_stop"]),
            ["0.5", "0.75", "1", "1.25", "1.5", "2", "2.5", "3", "CONTROL_NO_ATR_STOP"],
        )
        chase_grid = sweep["best_by_chase_and_stop"]
        self.assertEqual(list(chase_grid), ["patient"])
        self.assertEqual(
            list(chase_grid["patient"]),
            ["0.5", "0.75", "1", "1.25", "1.5", "2", "2.5", "3", "CONTROL_NO_ATR_STOP"],
        )
        self.assertEqual(
            chase_grid["patient"]["1"]["policy_spec"]["entry"]["chase_id"],
            "patient",
        )
        ranking = rank_safe_policies(report["candidates"])
        self.assertIsNone(ranking["number_one"])
        self.assertTrue(all("conservative_execution_pass" in row["ranking_blockers"] for row in ranking["blocked"]))

    def test_correlated_lane_rows_do_not_inflate_episode_count(self):
        report = evaluate_protection_screen([source("z-lane"), source("a-lane")])
        self.assertTrue(report["candidates"])
        self.assertTrue(all(row["episodes_total"] == 1 for row in report["candidates"]))

    def test_missing_ordered_path_is_unsupported_not_zero_pnl(self):
        row = source()
        row["ordered_1s_prices"] = []
        report = evaluate_protection_screen([row])
        first = report["candidates"][0]["validation"]
        self.assertEqual(first["episodes_scored"], 0)
        self.assertIn("episode-1", first["missing_or_unsupported_episode_ids"])


if __name__ == "__main__":
    unittest.main()

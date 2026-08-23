import tempfile
import unittest

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
        self.assertIn("ATR_TP_2.5_TIME_120", names)
        self.assertIn("ATR_TP_2.5_BE_4_LOCK_1", names)
        self.assertIn("ATR_TP_2.5_GIVEBACK_40PCT", names)

    def test_descriptive_policies_are_visible_but_never_safe_qualified(self):
        report = evaluate_protection_screen([source()])
        self.assertEqual(report["unique_policies_evaluated"], len(protection_screen()))
        self.assertTrue(report["descriptive_top_100"])
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

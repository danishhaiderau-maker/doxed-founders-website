"""collector_v2.1: four cohorts, rejected persist, FUNNEL_ONLY not in exit WR."""
import json
import os
import tempfile
import unittest

from chase_offset_touch_grid import orig_limit_price
from opportunity_capture_v21 import (
    COHORT_ACTUAL_FILLED,
    COHORT_HYPOTHETICAL_FILLED,
    COHORT_REJECTED_SIGNAL,
    COHORT_SUBMITTED_UNFILLED,
    analyze_opportunity_capture,
    build_decision_tree,
    build_rejected_capture,
    classify_cohort,
    write_opportunity_capture,
)
from order_multiverse import allows_exit_expectancy, build_order_multiverse
from path_replay_v1 import COLLECTOR_VERSION, FILL_MODEL_CONSERVATIVE_TOUCH, FILL_MODEL_IDEAL_TOUCH


def _1m(i, high, low, close, origin=1_700_000_000):
    return [int((origin + i * 60) * 1000), close, high, low, close, 1.0]


class OpportunityCaptureV21Tests(unittest.TestCase):
    def test_four_cohorts_never_mixed(self):
        self.assertEqual(classify_cohort(submitted=True, live_filled=True), COHORT_ACTUAL_FILLED)
        self.assertEqual(
            classify_cohort(submitted=True, live_filled=False, entry_outcome="TTL_UNFILLED"),
            COHORT_SUBMITTED_UNFILLED,
        )
        self.assertEqual(
            classify_cohort(submitted=False, live_filled=False, rejected=True),
            COHORT_REJECTED_SIGNAL,
        )
        self.assertNotEqual(COHORT_ACTUAL_FILLED, COHORT_HYPOTHETICAL_FILLED)

    def test_rejected_candidate_is_first_class_row(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        tree = build_decision_tree(
            reason="SPREAD_BUCKET_BLOCKED",
            hard_reject=True,
            rsi=28.0,
            adx=22.0,
            would_block=True,
            would_block_reason="SHORT_3M_EXHAUSTION",
        )
        names = [node["name"] for node in tree["filters"]]
        self.assertIn("ADX", names)
        self.assertIn("RSI", names)
        self.assertIn("WOULD_BLOCK", names)
        row = build_rejected_capture(
            trade_id="cont-rejected-1",
            signal_price=signal_price,
            signal_ts=signal_ts,
            direction="SHORT",
            reason="SPREAD_BUCKET_BLOCKED",
            decision_tree=tree,
            candles_1m=[_1m(i, signal_price, signal_price - 10, signal_price) for i in range(40)],
            rsi_at_signal=28.0,
            would_block=True,
            would_block_reason="SHORT_3M_EXHAUSTION",
        )
        self.assertEqual(row["event"], "REJECTED_SIGNAL")
        self.assertEqual(row["cohort"], COHORT_REJECTED_SIGNAL)
        self.assertEqual(row["record_kind"], "rejected_opportunity")
        self.assertEqual(row["collector_version"], "collector_v2.2")
        self.assertEqual(row["envelope"]["path_from"], "signal_ts")
        self.assertEqual(row["decision_tree"]["exact_reason"], "SPREAD_BUCKET_BLOCKED")
        self.assertFalse(row["envelope"]["control_cell"]["invert_on"])
        self.assertEqual(row["fill_model"], FILL_MODEL_IDEAL_TOUCH)
        flags = {
            "lifecycle": "REJECTED_SIGNAL",
            "entry_outcome": "NOT_SUBMITTED",
            "PATH_COMPLETE": False,
        }
        self.assertFalse(allows_exit_expectancy(flags))

    def test_funnel_only_not_in_exit_wr(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        high_05 = orig_limit_price(signal_price, "SHORT", 0.05)
        candles = [
            _1m(i, high_05 if i == 4 else signal_price, signal_price - 25, signal_price)
            for i in range(35)
        ]
        row = build_order_multiverse(
            trade_id="cont-funnel",
            signal_price=signal_price,
            signal_ts=signal_ts,
            candles_1m=candles,
            ttl_sec=1800.0,
        )
        self.assertEqual(row["lifecycle"], "FUNNEL_ONLY")
        self.assertEqual(row["cohort"], COHORT_SUBMITTED_UNFILLED)
        self.assertFalse(allows_exit_expectancy(row["completeness"]))
        self.assertTrue(row["post_ttl_pending"])

    def test_post_ttl_010_is_alternative_not_original_order(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        fill_px = orig_limit_price(signal_price, "SHORT", 0.10)
        candles = [
            _1m(i, fill_px if i == 37 else signal_price, signal_price - 10, signal_price)
            for i in range(50)
        ]
        row = build_order_multiverse(
            trade_id="cont-alt-37m",
            signal_price=signal_price,
            signal_ts=signal_ts,
            candles_1m=candles,
            ttl_sec=1800.0,
        )
        self.assertEqual(row["lifecycle"], "FUNNEL_ONLY")
        self.assertIsNone(row["touches"]["0.10"])
        self.assertIsNotNone(row["alternative_touches"]["0.10"])
        self.assertFalse(row["original_order_fill"])
        self.assertTrue(row["alternative_entry_fill"])
        alt = [
            fill for fill in row["hypothetical_fills"]
            if abs(float(fill["orig"]) - 0.10) < 1e-9 and fill["chase_id"] == "no_chase"
        ]
        self.assertTrue(alt)
        self.assertFalse(alt[0]["original_order_fill"])
        self.assertTrue(alt[0]["alternative_entry_fill"])
        self.assertEqual(alt[0]["fill_window"], "POST_TTL")
        self.assertEqual(alt[0]["record_kind"], "hypothetical_fill")
        self.assertFalse(allows_exit_expectancy(row["completeness"]))

    def test_analyzer_empty_epoch_zeros_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = analyze_opportunity_capture(data_dir=tmp)
        self.assertTrue(report["empty_epoch"])
        self.assertEqual(report["cohorts"][COHORT_ACTUAL_FILLED]["n"], 0)
        self.assertEqual(report["cohorts"][COHORT_SUBMITTED_UNFILLED]["n"], 0)
        self.assertEqual(report["cohorts"][COHORT_REJECTED_SIGNAL]["n"], 0)
        self.assertEqual(report["cohorts"][COHORT_HYPOTHETICAL_FILLED]["n"], 0)
        self.assertEqual(COLLECTOR_VERSION, "collector_v2.2")
        self.assertEqual(FILL_MODEL_CONSERVATIVE_TOUCH, "CONSERVATIVE_TOUCH")

    def test_analyzer_splits_four_cohorts(self):
        signal_price = 100000.0
        signal_ts = 1_700_000_000.0
        fill_px = orig_limit_price(signal_price, "SHORT", 0.10)
        filled_candles = [
            _1m(i, fill_px if i == 2 else signal_price, signal_price - 20, signal_price)
            for i in range(130)
        ]
        filled = build_order_multiverse(
            trade_id="cont-filled",
            signal_price=signal_price,
            signal_ts=signal_ts,
            candles_1m=filled_candles,
            live_fill_ts=signal_ts + 120.0,
            live_fill_price=fill_px,
            ticket_closed=True,
        )
        unfilled = build_order_multiverse(
            trade_id="cont-unfilled",
            signal_price=signal_price,
            signal_ts=signal_ts,
            candles_1m=[
                _1m(i, orig_limit_price(signal_price, "SHORT", 0.05) if i == 4 else signal_price, signal_price - 25, signal_price)
                for i in range(40)
            ],
            ttl_sec=1800.0,
        )
        rejected = build_rejected_capture(
            trade_id="cont-rejected",
            signal_price=signal_price,
            signal_ts=signal_ts,
            reason="CAPACITY",
            candles_1m=[_1m(i, fill_px if i == 37 else signal_price, signal_price - 10, signal_price) for i in range(50)],
        )
        with tempfile.TemporaryDirectory() as tmp:
            mv = os.path.join(tmp, "order_multiverse.jsonl")
            cap = os.path.join(tmp, "opportunity_capture.jsonl")
            with open(mv, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(filled) + "\n" + json.dumps(unfilled) + "\n")
            write_opportunity_capture(cap, rejected)
            report = analyze_opportunity_capture(data_dir=tmp)
        self.assertFalse(report["empty_epoch"])
        self.assertGreaterEqual(report["cohorts"][COHORT_ACTUAL_FILLED]["n"], 1)
        self.assertGreaterEqual(report["cohorts"][COHORT_SUBMITTED_UNFILLED]["n"], 1)
        self.assertGreaterEqual(report["cohorts"][COHORT_REJECTED_SIGNAL]["n"], 1)
        self.assertTrue(report["ttl_never_in_exit_wr"])
        self.assertTrue(report["stage1"]["atr_tp"])
        self.assertTrue(report["stage1"]["atr_sl"])
        self.assertFalse(report["rsi"]["live_veto"])
        self.assertEqual(report["control_cell"]["orig_offset_pct"], 0.10)
        self.assertEqual(report["control_cell"]["thesis_cut"], -12.0)


if __name__ == "__main__":
    unittest.main()

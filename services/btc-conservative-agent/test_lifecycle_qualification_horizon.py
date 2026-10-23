import json
import tempfile
import unittest
from pathlib import Path

from lifecycle_qualification_horizon import (
    canonical_path_extrema_usd,
    canonical_terminal_economics,
    qualification_post_observation,
)
from research_v3_bridge import dual_write_lifecycle_qualification_horizon
from research_v3_store import V3EvidenceStore


def _tape(path: Path, timestamps, *, price_base=100.0):
    path.write_text("\n".join(json.dumps({
        "schema": "market_microstructure_1s_v1",
        "symbol": "tBTCF0:USTF0", "bucket_ts": ts,
        "last": price_base + (ts - min(timestamps)),
        "bid": price_base - 0.1, "ask": price_base + 0.1,
        "bid_qty": 2.0, "ask_qty": 3.0,
    }) for ts in timestamps) + "\n", encoding="utf-8")


class QualificationHorizonUnitTests(unittest.TestCase):
    def test_elapsed_time_without_gap_free_book_depth_remains_unknown(self):
        proof = qualification_post_observation({
            "requested_start_ts": 100, "requested_end_ts": 7300,
            "observed_start_ts": 100, "observed_end_ts": 7300,
            "requested_bounds_complete": True, "two_second_or_better": False,
            "max_gap_sec": 60, "all_rows_have_valid_bbo": False,
            "all_rows_have_visible_depth": False, "parse_errors": 0,
            "invalid_timestamp_rows": 0, "invalid_price_rows": 0,
            "invalid_bbo_rows": 1, "invalid_depth_rows": 1,
        }, terminal_ts=100)
        self.assertFalse(proof["complete"])
        self.assertFalse(proof["gaps_absent"])
        self.assertIsNone(proof["complete_through_ts"])
        self.assertIn("POST_OBSERVATION_GAP_PRESENT", proof["blockers"])
        self.assertIn("POST_OBSERVATION_DEPTH_INCOMPLETE", proof["blockers"])

    def test_costs_and_extrema_require_explicit_canonical_units(self):
        costs = canonical_terminal_economics({
            "gross_pnl_usd": 1.0, "trading_fees_usd": 0.1,
            "funding_fees_usd": 0.05, "entry_slippage_cost_usd": 0.01,
            "exit_slippage_cost_usd": 0.02, "latency_cost_usd": 0.02,
            "net_pnl_usd": 0.85,
        })
        self.assertEqual(costs["status"], "COMPLETE")
        self.assertAlmostEqual(costs["slippage_cost_usd"], 0.03)
        self.assertEqual(
            costs["attribution_only_not_subtracted"],
            ["slippage_cost_usd", "latency_cost_usd"],
        )
        extrema = canonical_path_extrema_usd(
            {"basis": "OBSERVED_1S_PRICE_PATH", "mfe_pct": 2, "mae_pct": -1},
            entry_price=100, filled_quantity=0.2,
        )
        self.assertEqual(extrema["status"], "COMPLETE")
        self.assertAlmostEqual(extrema["mfe_usd"], 0.4)
        self.assertAlmostEqual(extrema["mae_usd"], -0.2)

    def test_missing_cost_leg_or_conversion_input_stays_unknown(self):
        costs = canonical_terminal_economics({
            "gross_pnl_usd": 1, "trading_fees_usd": 0.1,
            "funding_fees_usd": 0, "net_pnl_usd": 0.9,
        })
        self.assertEqual(costs["status"], "UNKNOWN")
        self.assertIn("LATENCY_COST_USD_MISSING", costs["blockers"])
        extrema = canonical_path_extrema_usd(
            {"mfe_pct": 2, "mae_pct": -1}, entry_price=100, filled_quantity=None,
        )
        self.assertEqual(extrema["status"], "UNKNOWN")
        self.assertIsNone(extrema["mfe_usd"])


class QualificationHorizonBridgeTests(unittest.TestCase):
    def _sources(self, trade_id="qh-fill"):
        signal = {
            "trade_id": trade_id, "created_ts_ts": 100,
            "raw_direction": "LONG", "final_direction": "LONG",
            "shared_ai_call_id": f"scan-{trade_id}", "symbol": "tBTCF0:USTF0",
            "research_lane": "CONTINUOUS", "policy_id": "CONTINUOUS",
        }
        schedule = {
            "schema": "research_chase_schedule_v1", "authoritative": True,
            "intervals": [{"bucket_id": "b0", "start_ts": 100, "end_ts": 101,
                           "limit_price": 100}],
            "terminal_ts": 101, "terminal_ts_exact": 101,
            "terminal_reason": "FILLED",
        }
        order = {
            **signal, "status": "FILLED", "qty": 0.2, "requested_qty": 0.2,
            "limit_price": 100, "research_chase_schedule": schedule,
            "chase_schedule_authoritative": True,
        }
        position = {
            **order, "entry_ts": 101, "entry": 100, "dir": "LONG",
        }
        return signal, order, position

    def test_actual_bridge_invalid_outcome_is_preserved_as_unknown_not_no_fill(self):
        with tempfile.TemporaryDirectory() as tmp:
            _tape(Path(tmp) / "market_microstructure_1s.jsonl", range(400, 405))
            signal, order, _position = self._sources("qh-invalid")
            order["research_chase_schedule"] = {
                **order["research_chase_schedule"], "terminal_ts": 400,
                "terminal_ts_exact": 400, "terminal_reason": "UNRESOLVED",
            }
            receipt = dual_write_lifecycle_qualification_horizon(
                order, signal, order, entry_outcome="",
                epoch_id="epoch-qh", data_dir=tmp, lifecycle_horizon_sec=4,
            )
            self.assertEqual(receipt["entry_outcome"], "UNKNOWN")
            row = json.loads(
                V3EvidenceStore(tmp, epoch_id="epoch-qh").ledger_path("lifecycle").read_text()
            )
            self.assertEqual(row["outcome_state"], "UNKNOWN")
            self.assertEqual(row["unknown_reason"], "ENTRY_OUTCOME_INVALID")


if __name__ == "__main__":
    unittest.main()

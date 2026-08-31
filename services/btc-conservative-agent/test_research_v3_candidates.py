import tempfile
import time
import unittest
from unittest.mock import patch

import research_v3_candidates

from research_v3_candidates import (
    _PolicyNeighborIndex,
    _apply_multifactor_ranking,
    _bind_candidate_receipt_identity,
    _validation_receipt_identity,
    _comparison_cohort_receipt,
    _conservative_child_receipt,
    _conservative_ohlc_prices,
    evaluate_protection_screen,
    load_candidate_inputs,
    protection_screen,
)
from research_v3_store import V3EvidenceStore
from research_v3_ranking import rank_safe_policies
from research.quantity_execution import build_signed_quantity_constraints


def source(event_id="event-1", episode_id="episode-1"):
    return {
        "epoch_id": "epoch-test",
        "event_id": event_id,
        "episode_id": episode_id,
        "opportunity_id": f"opportunity:{episode_id}",
        "source_policy_signature": "paper-policy-source",
        "source_fill_ids": [f"execution:{event_id}:fill"],
        "tape_ids": ["tape-sha-1"],
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


def conservative_source(*, visible_qty=2.0, crossed=True):
    row = source()
    row["requested_qty"] = 1.0
    row["market_microstructure_symbol"] = "tBTCF0:USTF0"
    row["signed_quantity_constraints"] = build_signed_quantity_constraints(
        symbol="tBTCF0:USTF0", quantity_step="0.00000001", quantity_precision=8,
        min_lot="0.00000001", min_notional="0.000001",
        captured_at="2026-08-30T00:00:00Z", source_revision="test-revision",
        source="TEST_FIXTURE",
    )
    row["entry_children"][0]["chase_schedule"] = [{
        "active_from_ts": 1000.2,
        "active_until_ts": 1004.2,
        "chase_step_index": 0,
        "limit_price": 100.0,
    }]
    row["ordered_1s_prices"] = [
        {
            "schema": "market_microstructure_1s_v1",
            "symbol": "tBTCF0:USTF0",
            "bucket_ts": ts,
            "ts": float(ts),
            "price": 100.0 + (ts - 1000) * 0.1,
            "fresh": True,
            "valid_bbo": True,
            "bid": 99.9,
            "ask": 99.95 if crossed else 100.5,
            "bid_qty": 2.0,
            "ask_qty": visible_qty,
            "trade_count": 0,
            "buy_qty": 0.0,
            "sell_qty": 0.0,
        }
        for ts in range(998, 1005)
    ]
    return row


class V3CandidateTests(unittest.TestCase):
    def test_validation_receipt_identity_drops_unconsumed_receipt_evidence(self):
        receipt = {
            "identity": {
                "schema": "candidate_episode_receipt_identity_v1",
                "complete": False,
                "missing_required_identities": ["tape_ids", "schedule_sha256"],
                "fill_receipt_id": "candidate-fill-ignored-by-assessment",
                "large_ignored_identity_field": "x" * 1_000_000,
            },
            "diagnostics": {"large_ignored_receipt_field": "y" * 1_000_000},
            "evidence_bucket_ids": list(range(10_000)),
        }

        projected = _validation_receipt_identity(receipt)

        self.assertEqual(
            projected,
            {
                "complete": False,
                "missing_required_identities": ["tape_ids", "schedule_sha256"],
            },
        )
        self.assertNotIn("diagnostics", projected)
        self.assertNotIn("fill_receipt_id", projected)

    def test_comparison_cohort_matches_across_distinct_policy_treatments(self):
        rows = [
            {
                "epoch_id": "epoch-clean",
                "episode_id": f"episode-{index}",
                "opportunity_id": f"opportunity:episode-{index}",
                "tape_ids": [f"tape-{index}"],
                "signal_ts": 1000 + index,
            }
            for index in range(4)
        ]
        first = _comparison_cohort_receipt(rows, holdout_start=2, sealed_holdout=False)
        # Family-specific policy and exit material is deliberately outside this
        # receipt; exact treatment identity remains the policy signature.
        second = _comparison_cohort_receipt(
            [{**row, "policy_id": "DIFFERENT_EXIT_FAMILY"} for row in rows],
            holdout_start=2,
            sealed_holdout=False,
        )

        self.assertTrue(first["complete"])
        self.assertEqual(first["comparison_cohort_key"], second["comparison_cohort_key"])
        self.assertEqual(first["train_episode_count"], 2)
        self.assertEqual(first["oos_episode_count"], 2)
        diagnostic = _comparison_cohort_receipt(
            rows,
            holdout_start=2,
            sealed_holdout=False,
            evidence_world="IDEAL_TOUCH_DIAGNOSTIC_ONLY",
        )
        self.assertNotEqual(first["comparison_cohort_key"], diagnostic["comparison_cohort_key"])

    def test_comparison_cohort_rejects_same_count_with_different_oos_opportunity(self):
        rows = [
            {
                "epoch_id": "epoch-clean",
                "episode_id": f"episode-{index}",
                "opportunity_id": f"opportunity:episode-{index}",
                "tape_ids": [f"tape-{index}"],
                "signal_ts": 1000 + index,
            }
            for index in range(4)
        ]
        baseline = _comparison_cohort_receipt(rows, holdout_start=2, sealed_holdout=False)
        changed = [dict(row) for row in rows]
        changed[-1]["opportunity_id"] = "opportunity:different"
        mismatch = _comparison_cohort_receipt(changed, holdout_start=2, sealed_holdout=False)

        self.assertTrue(baseline["complete"])
        self.assertTrue(mismatch["complete"])
        self.assertNotEqual(
            baseline["comparison_cohort_key"],
            mismatch["comparison_cohort_key"],
        )

    def test_comparison_cohort_fails_closed_when_tape_identity_is_missing(self):
        receipt = _comparison_cohort_receipt([
            {
                "epoch_id": "epoch-clean",
                "episode_id": "episode-1",
                "opportunity_id": "opportunity:episode-1",
                "tape_ids": [],
                "signal_ts": 1000,
            },
            {
                "epoch_id": "epoch-clean",
                "episode_id": "episode-2",
                "opportunity_id": "opportunity:episode-2",
                "tape_ids": ["tape-2"],
                "signal_ts": 1001,
            },
        ], holdout_start=1, sealed_holdout=False)

        self.assertFalse(receipt["complete"])
        self.assertIsNone(receipt["comparison_cohort_key"])
        self.assertIn("TRAIN_TAPE_IDS", receipt["missing_required_identities"])

    @staticmethod
    def _ranking_row(policy_id, *, family="FIXED_TARGET", pnl=1.0, drawdown=-0.2,
                     cvar=-0.1, lcb=0.02, fills=8, partial=0, no_fills=2,
                     regimes=None, parameter=1.0):
        if regimes is None:
            regimes = {
                "BULL": {"scored_episodes": 3, "expectancy_usd": 0.03},
                "BEAR": {"scored_episodes": 3, "expectancy_usd": 0.02},
                "SIDEWAYS": {"scored_episodes": 4, "expectancy_usd": 0.01},
            }
        return {
            "policy_id": policy_id,
            "policy_family": family,
            "policy_spec": {"entry": {"offset_pct": parameter}, "exit": {"atr_k": 2.5}},
            "oos_episodes": 10,
            "full_fills": fills,
            "partial_fills": partial,
            "no_fills": no_fills,
            "sealed_oos_net_usd": pnl,
            "max_drawdown_usd": drawdown,
            "cvar95_usd": cvar,
            "expectancy_lcb_usd": lcb,
            "regime_breakdown": regimes,
        }

    def test_multifactor_ranking_penalizes_missing_risk_and_execution_evidence(self):
        complete = [
            self._ranking_row("balanced", pnl=1.0, parameter=0.10),
            self._ranking_row("balanced-neighbor-a", pnl=0.9, parameter=0.11),
            self._ranking_row("balanced-neighbor-b", pnl=0.8, parameter=0.12),
        ]
        raw_profit_only = self._ranking_row("raw-profit-only", pnl=99.0, parameter=0.9)
        raw_profit_only.update({
            "max_drawdown_usd": None,
            "cvar95_usd": None,
            "expectancy_lcb_usd": None,
            "full_fills": 0,
            "no_fills": 0,
            "regime_breakdown": {},
        })

        ranked = _apply_multifactor_ranking([raw_profit_only, *complete])
        raw = next(row for row in ranked if row["policy_id"] == "raw-profit-only")
        leader = ranked[0]

        self.assertNotEqual(leader["policy_id"], "raw-profit-only")
        self.assertFalse(raw["ranking_complete"])
        self.assertEqual(raw["qualification"], "DESCRIPTIVE_ONLY")
        self.assertIn("drawdown", raw["ranking_evidence"]["missing_metrics"])
        self.assertIn("expected_shortfall", raw["ranking_evidence"]["missing_metrics"])
        self.assertIn("fill_realism", raw["ranking_evidence"]["missing_metrics"])
        self.assertIsNone(raw["ranking_evidence"]["raw_metrics"]["drawdown"])
        self.assertGreater(raw["ranking_evidence"]["missing_metric_penalty"], 0)
        self.assertEqual(raw["ranking_status"], "INCOMPLETE_UNRANKED")
        self.assertIsNone(raw["ranking_score"])
        self.assertIsNone(raw["ranking_evidence"]["composite_score"])
        self.assertTrue(all(
            value is None for value in raw["ranking_evidence"]["component_scores"].values()
        ))

    def test_multifactor_ranking_exposes_tail_regime_and_neighbor_components(self):
        rows = [
            self._ranking_row("p1", parameter=0.10),
            self._ranking_row("p2", pnl=0.9, parameter=0.11),
            self._ranking_row("p3", pnl=0.8, parameter=0.12),
        ]
        ranked = _apply_multifactor_ranking(rows)
        evidence = ranked[0]["ranking_evidence"]

        self.assertTrue(evidence["complete"])
        self.assertEqual(evidence["qualification"], "DESCRIPTIVE_ONLY")
        self.assertEqual(set(evidence["weights"]), {
            "profit", "drawdown", "expected_shortfall", "fill_realism",
            "uncertainty_lower_bound", "regime_stability",
            "neighboring_parameter_robustness",
        })
        self.assertGreater(evidence["raw_metrics"]["regime_stability"], 0)
        self.assertEqual(evidence["neighbor_evidence"]["neighbors_supported"], 2)
        self.assertGreaterEqual(evidence["raw_metrics"]["neighboring_parameter_robustness"], 0)

    def test_multifactor_neighbor_index_scales_and_keeps_local_semantics(self):
        rows = []
        for family_index in range(5):
            family = f"FAMILY_{family_index}"
            for parameter_index in range(1000):
                rows.append(self._ranking_row(
                    f"{family}-{parameter_index:04d}",
                    family=family,
                    pnl=1.0 + parameter_index / 10000,
                    parameter=parameter_index / 1000,
                ))

        started = time.perf_counter()
        ranked = _apply_multifactor_ranking(rows)
        elapsed = time.perf_counter() - started

        self.assertEqual(len(ranked), 5000)
        target = next(row for row in ranked if row["policy_id"] == "FAMILY_2-0500")
        evidence = target["ranking_evidence"]["neighbor_evidence"]
        self.assertEqual(evidence["neighbors_considered"], 5)
        self.assertEqual(evidence["neighbors_supported"], 5)
        self.assertIsNotNone(evidence["score"])
        # The indexed implementation should comfortably avoid the former
        # 25-million-distance all-pairs path on ordinary CI hardware.
        self.assertLess(elapsed, 8.0)

    def test_multifactor_neighbor_index_matches_cartesian_nearest_neighbors(self):
        rows = []
        for offset in (0.02, 0.03, 0.04):
            for atr_k in (1.5, 2.0, 2.5):
                row = self._ranking_row(
                    f"policy-{offset}-{atr_k}",
                    parameter=offset,
                )
                row["policy_spec"]["exit"]["atr_k"] = atr_k
                rows.append(row)

        target_index = 4
        target = rows[target_index]
        expected = sorted(
            (candidate for candidate in rows if candidate is not target),
            key=lambda candidate: (
                # Import-free reference implementation for these two numeric
                # dimensions, whose scales are both one or greater here.
                abs(candidate["policy_spec"]["entry"]["offset_pct"] - target["policy_spec"]["entry"]["offset_pct"])
                + abs(candidate["policy_spec"]["exit"]["atr_k"] - target["policy_spec"]["exit"]["atr_k"])
                / max(candidate["policy_spec"]["exit"]["atr_k"], target["policy_spec"]["exit"]["atr_k"]),
                candidate["policy_id"],
            ),
        )[:5]
        actual = _PolicyNeighborIndex(rows).nearest(target_index)

        self.assertEqual(
            [row["policy_id"] for row in actual],
            [row["policy_id"] for row in expected],
        )

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
            self.assertEqual(rows[0]["epoch_id"], "epoch-clean")
            self.assertEqual(rows[0]["opportunity_id"], "opportunity:episode-1")
            self.assertEqual(rows[0]["tape_ids"], [segment["sha256"]])
            self.assertEqual(rows[0]["source_fill_ids"], ["execution:event-1:primary-fill"])
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

    def test_source_policy_signature_contamination_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = V3EvidenceStore(tmp, epoch_id="epoch-clean")
            store.append("opportunity", {
                "record_id": "opportunity:episode-1", "episode_id": "episode-1",
                "signal_ts": 1000,
            })
            store.append("order_intent", {
                "record_id": "order-intent:event-1", "episode_id": "episode-1",
                "event_id": "event-1", "executed_direction": "LONG",
                "policy_id": "OFFSET_0.29_CHASE_patient",
                "policy_signature": "paper-policy-a",
            })
            store.append("lifecycle", {
                "record_id": "lifecycle:event-1:terminal", "episode_id": "episode-1",
                "event_id": "event-1", "terminal": True,
                "policy_signature": "paper-policy-b",
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

    def test_zero_information_policies_stay_internal_and_unranked(self):
        progress = []
        report = evaluate_protection_screen([source()], progress_callback=progress.append)
        self.assertEqual(report["unique_policies_evaluated"], len(protection_screen()))
        self.assertTrue(report["candidates"])
        self.assertEqual(report["descriptive_top_100"], [])
        self.assertEqual(report["profit_capture_leaders"], {})
        self.assertEqual(report["drawdown_control_leaders"], [])
        selection = report["descriptive_selection"]
        self.assertEqual(
            selection["method"],
            "MULTIFACTOR_CONSERVATIVE_RANK_THEN_FAMILY_CAP",
        )
        self.assertIn("expected_shortfall", selection["ranking_dimensions"])
        self.assertEqual(selection["per_family_cap"], 2)
        self.assertEqual(selection["families_evaluated"], 5)
        self.assertEqual(selection["families_represented"], 0)
        self.assertEqual(selection["rows_displayed"], 0)
        self.assertEqual(selection["globally_ranked_policies"], len(report["candidates"]))
        self.assertEqual(progress[-1]["phase"], "PROTECTION_REPLAY")
        self.assertEqual(progress[-1]["input_events_completed"], 1)
        self.assertEqual(progress[-1]["input_events_total"], 1)
        sweep = report["scenario_c_atr_stop_sweep"]
        self.assertEqual(sweep["qualification"], "DESCRIPTIVE_ONLY")
        self.assertEqual(sweep["leaders_by_stop"], {})
        self.assertEqual(sweep["best_by_chase_and_stop"], {})
        self.assertGreater(sweep["policies_enumerated"], 0)
        self.assertEqual(sweep["policies_tested"], 0)
        self.assertEqual(
            sweep["unranked_reason"],
            "INSUFFICIENT_SHARED_COHORT_OR_EXECUTION_EVIDENCE",
        )
        ranking = rank_safe_policies(report["candidates"])
        self.assertIsNone(ranking["number_one"])
        self.assertTrue(all("conservative_execution_pass" in row["ranking_blockers"] for row in ranking["blocked"]))

    def test_correlated_lane_rows_do_not_inflate_episode_count(self):
        report = evaluate_protection_screen([source("z-lane"), source("a-lane")])
        self.assertTrue(report["candidates"])
        self.assertTrue(all(row["episodes_total"] == 1 for row in report["candidates"]))

    def test_complete_source_tile_policy_replaces_exit_without_concatenating_it(self):
        row = source()
        row["entry_children"][0]["entry_policy_id"] = (
            "OFFSET_0.27_CHASE_w234_s50_i180|ATR_TP_2.5_SCENARIO_C"
        )
        report = evaluate_protection_screen([row])

        self.assertTrue(report["candidates"])
        self.assertTrue(all(candidate["policy_id"].count("|") == 1 for candidate in report["candidates"]))
        self.assertTrue(all(
            candidate["policy_spec"]["entry"]["entry_policy_id"]
            == "OFFSET_0.27_CHASE_w234_s50_i180"
            for candidate in report["candidates"]
        ))

    def test_missing_ordered_path_is_unsupported_not_zero_pnl(self):
        row = source()
        row["ordered_1s_prices"] = []
        report = evaluate_protection_screen([row])
        first = report["candidates"][0]["validation"]
        self.assertEqual(first["episodes_scored"], 0)
        self.assertIn("episode-1", first["missing_or_unsupported_episode_ids"])
        risk = first["risk"]
        self.assertIsNone(risk["net_pnl_usd"])
        self.assertIsNone(risk["max_drawdown_usd"])
        candidate = report["candidates"][0]
        self.assertEqual(candidate["ranking_status"], "INCOMPLETE_UNRANKED")
        self.assertIsNone(candidate["ranking_score"])
        self.assertNotIn("global_rank", candidate)
        self.assertNotIn("family_rank", candidate)

    def test_actual_and_counterfactual_share_canonical_chase_identity(self):
        counterfactual = source("counterfactual-lane")
        actual = source("actual-lane")
        entry_id = "OFFSET_0.27_CHASE_w234_s50_i180"
        counterfactual["entry_children"][0].update({
            "entry_policy_id": entry_id,
            "chase_id": "w234_s50_i180",
        })
        actual["entry_children"][0].update({
            "entry_policy_id": f"{entry_id}|ATR_TP_2.5_SCENARIO_C",
            "chase_id": f"{entry_id}|ATR_TP_2.5_SCENARIO_C",
        })

        report = evaluate_protection_screen([counterfactual, actual])

        self.assertTrue(report["candidates"])
        self.assertTrue(all(
            candidate["policy_spec"]["entry"]["chase_id"] == "w234_s50_i180"
            for candidate in report["candidates"]
        ))

    def test_supported_and_unsupported_episodes_share_declared_fill_model(self):
        supported = source("supported-lane")
        unsupported = source("unsupported-lane")
        unsupported["entry_children"][0]["chase_schedule"] = []

        report = evaluate_protection_screen([supported, unsupported])

        self.assertTrue(report["candidates"])
        self.assertTrue(all(
            candidate["policy_spec"]["fill"]["source_fill_model"]
            == "public-tape-conservative-v3-quantity-aware"
            for candidate in report["candidates"]
        ))

    def test_conservative_full_fill_drives_execution_metrics(self):
        report = evaluate_protection_screen([conservative_source()])
        first = report["candidates"][0]
        self.assertEqual(first["full_fills"], 1)
        self.assertEqual(first["partial_fills"], 0)
        self.assertEqual(first["unsupported_episodes"], 0)
        self.assertEqual(first["evidence_world"], "CONSERVATIVE_BBO_DEPTH_V1")
        self.assertEqual(
            first["ideal_touch_diagnostic"]["evidence_world"],
            "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
        )
        self.assertFalse(first["ideal_touch_diagnostic"]["qualification_eligible"])
        self.assertFalse(first["gates"]["purged_walk_forward_pass"])
        self.assertIn(
            "INSUFFICIENT_COMPLETE_PURGED_FOLDS",
            first["validation"]["purged_walk_forward"]["blockers"],
        )

    def test_conservative_partial_fill_scales_execution_and_is_not_full(self):
        report = evaluate_protection_screen([conservative_source(visible_qty=0.25)])
        first = report["candidates"][0]
        self.assertEqual(first["full_fills"], 0)
        self.assertEqual(first["partial_fills"], 1)
        self.assertEqual(first["conservative_fill_rate"], 1.0)

    def test_policy_identity_excludes_trade_quantity_and_hashes_once(self):
        first = conservative_source()
        second = conservative_source()
        second.update({"event_id": "event-2", "episode_id": "episode-2", "signal_ts": 1005})
        second["opportunity_id"] = "opportunity:episode-2"
        second["requested_qty"] = 0.25
        real_hash = research_v3_candidates.canonical_hash
        policy_hash_calls = []

        def counted_hash(prefix, value, **kwargs):
            if prefix == "v3-policy":
                policy_hash_calls.append(value)
            return real_hash(prefix, value, **kwargs)

        with patch.object(research_v3_candidates, "canonical_hash", counted_hash):
            report = evaluate_protection_screen([first, second])

        self.assertEqual(len(policy_hash_calls), len(protection_screen()))
        self.assertTrue(report["candidates"])
        self.assertTrue(all(
            "requested_qty" not in candidate["policy_spec"]["fill"]
            for candidate in report["candidates"]
        ))
        self.assertTrue(all(candidate["episodes_total"] == 2 for candidate in report["candidates"]))

    def test_conservative_no_fill_contributes_no_execution_pnl(self):
        report = evaluate_protection_screen([conservative_source(crossed=False)])
        first = report["candidates"][0]
        self.assertEqual(first["full_fills"], 0)
        self.assertEqual(first["partial_fills"], 0)
        self.assertEqual(first["no_fills"], 1)
        self.assertEqual(first["validation"]["risk"]["non_execution_zero_contributions"], 1)
        self.assertFalse(first["gates"]["conservative_execution_pass"])

    def test_candidate_receipts_preserve_complete_causal_identity_for_all_outcomes(self):
        cases = (
            (conservative_source(), "FILL"),
            (conservative_source(visible_qty=0.25), "PARTIAL_FILL"),
            (conservative_source(crossed=False), "NO_FILL"),
        )
        for row, expected_outcome in cases:
            with self.subTest(expected_outcome=expected_outcome):
                raw = _conservative_child_receipt(row, row["entry_children"][0])
                receipt = _bind_candidate_receipt_identity(
                    raw, row, candidate_policy_signature="v3-policy-test",
                )
                identity = receipt["identity"]
                self.assertEqual(receipt["outcome"], expected_outcome)
                self.assertTrue(identity["complete"])
                self.assertEqual(identity["epoch_id"], "epoch-test")
                self.assertEqual(identity["event_id"], "event-1")
                self.assertEqual(identity["opportunity_id"], "opportunity:episode-1")
                self.assertEqual(identity["candidate_policy_signature"], "v3-policy-test")
                self.assertTrue(identity["schedule_sha256"])
                self.assertEqual(identity["tape_ids"], ["tape-sha-1"])
                if expected_outcome in {"FILL", "PARTIAL_FILL"}:
                    self.assertTrue(identity["fill_receipt_id"])
                else:
                    self.assertIsNone(identity["fill_receipt_id"])

    def test_missing_required_receipt_identity_fails_closed_without_fabrication(self):
        row = conservative_source()
        row["tape_ids"] = []
        raw = _conservative_child_receipt(row, row["entry_children"][0])
        receipt = _bind_candidate_receipt_identity(
            raw, row, candidate_policy_signature="v3-policy-test",
        )
        self.assertEqual(receipt["outcome"], "UNSUPPORTED")
        self.assertFalse(receipt["supported"])
        self.assertIsNone(receipt["identity"]["fill_receipt_id"])
        self.assertIn("tape_ids", receipt["identity"]["missing_required_identities"])
        self.assertIn("MISSING_REQUIRED_IDENTITY:tape_ids", receipt["negative_reasons"])

    def test_intrinsically_unsupported_receipt_still_preserves_available_identity(self):
        row = conservative_source()
        row["entry_children"][0]["chase_schedule"] = []
        raw = _conservative_child_receipt(row, row["entry_children"][0])
        receipt = _bind_candidate_receipt_identity(
            raw, row, candidate_policy_signature="v3-policy-test",
        )
        identity = receipt["identity"]
        self.assertEqual(receipt["outcome"], "UNSUPPORTED")
        self.assertEqual(identity["epoch_id"], "epoch-test")
        self.assertEqual(identity["event_id"], "event-1")
        self.assertEqual(identity["opportunity_id"], "opportunity:episode-1")
        self.assertEqual(identity["candidate_policy_signature"], "v3-policy-test")
        self.assertIsNone(identity["schedule_sha256"])
        self.assertIsNone(identity["fill_receipt_id"])
        self.assertIn("schedule_sha256", identity["missing_required_identities"])


if __name__ == "__main__":
    unittest.main()

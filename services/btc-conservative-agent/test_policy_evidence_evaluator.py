import hashlib
import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from research.policy_evidence_evaluator import (
    build_phase7_support_qualification, build_v3_conservative_results,
    persist_v3_conservative_results,
)
from research.policy_evidence_schema import canonical_json
from research.quantity_execution import build_signed_quantity_constraints
from lifecycle_bundles import LifecycleKey, materialize_bundle
from lifecycle_completion_reconciler import evaluate_lifecycle_completion
from lifecycle_completion_receipts import build_evidence_collected_receipt


def _write(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _constraints():
    return build_signed_quantity_constraints(
        symbol="BTCUSD", quantity_step="0.1", quantity_precision=1,
        min_lot="0.1", min_notional="1", captured_at="2026-01-01T00:00:00Z",
        source_revision="rev-1", source="fixture",
    )


def _row(ts, *, bid=99, ask=101, bid_qty=1, ask_qty=1):
    return {
        "schema": "market_microstructure_1s_v1", "symbol": "BTCUSD",
        "bucket_ts": ts, "fresh": True, "valid_bbo": True,
        "bid": bid, "ask": ask, "bid_qty": bid_qty, "ask_qty": ask_qty,
        "trade_count": 0, "buy_qty": 0, "sell_qty": 0,
    }


def _segment(v3, identity, role, rows, index):
    envelope = {
        "schema": "market_segment_v3", "source": "TEST_1S", "symbol": "BTCUSD",
        "timeframe": "1s", "start_ts": min(r["bucket_ts"] for r in rows),
        "end_ts": max(r["bucket_ts"] for r in rows) + 1, "rows": rows,
    }
    payload = canonical_json(envelope).encode()
    digest = hashlib.sha256(payload).hexdigest()
    relative = f"v3/market_segments/{digest[:2]}/{digest}.json"
    target = v3.parent / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return {
        **identity, "event_id": f"segment-{index}", "context_role": role,
        "coverage": {"context_role": role, "conservative_bbo_depth_eligible": True},
        "segment_ref": {"sha256": digest, "relative_path": relative, "source": "TEST_1S",
                        "symbol": "BTCUSD", "timeframe": "1s",
                        "start_ts": envelope["start_ts"], "end_ts": envelope["end_ts"],
                        "row_count": len(rows)},
    }


def _fixture(tmp_path, *, direction="LONG", entry_rows=None, qty=1, constraints=True):
    v3 = tmp_path / "v3"
    identity = {"epoch_id": "epoch-1", "opportunity_id": "opp-1", "episode_id": "ep-1",
                "research_lane": "CONTINUOUS"}
    decision = {**identity, "event_id": "decision-1", "policy_id": "policy-1",
                "policy_signature": "sig-1", "direction": direction,
                "policy_family": "ATR_TRAIL", "entry_offset_pct": .1,
                "chase_policy": "CHASE_13M", "exit_family": "ATR", "regime": "TREND", "split": "OOS"}
    _write(v3 / "ledgers/opportunity.jsonl", [{
        **identity,
        "raw_direction": direction,
        "feature_snapshot_at_signal": {
            "volatility_atr": 145.5,
            "volatility_percentile": 72.5,
            "atr14": 142.0,
            "atr14_pct_3m": 0.18,
            "market_context": {"regime_label": "BEAR"},
        },
    }])
    _write(v3 / "ledgers/decision.jsonl", [decision])
    schedule = {"schema": "schedule-v1", "authoritative": True,
                "intervals": [{"bucket_id": "s0", "start_ts": 10, "end_ts": 12,
                               "limit_price": 100}],
                "terminal_ts": 12, "terminal_reason": "FILLED"}
    intent = {**identity, "event_id": "intent-1", "policy_signature": "sig-1",
              "intent_kind": "AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL",
              "schedule_id": "schedule-1", "chase_schedule": schedule,
              "schedule_sha256": hashlib.sha256(canonical_json(schedule).encode()).hexdigest(),
              "requested_qty": qty, "symbol": "BTCUSD"}
    if constraints:
        intent["signed_quantity_constraints"] = _constraints()
    _write(v3 / "ledgers/order_intent.jsonl", [intent])
    rows = entry_rows if entry_rows is not None else [_row(10, ask=100), _row(11, ask=100)]
    segments = [_segment(v3, identity, "ENTRY_PATH", rows, 1),
                _segment(v3, identity, "POST_EXIT_PATH", [_row(20)], 2),
                _segment(v3, identity, "PRE_ENTRY_PATH", [_row(9)], 0)]
    _write(v3 / "ledgers/market_segment.jsonl", segments)
    for name in ("execution", "lifecycle"):
        _write(v3 / f"ledgers/{name}.jsonl", [])
    key = LifecycleKey("epoch-1", "ep-1", "sig-1", "CONTINUOUS")
    provenance = {"source_revision": "a" * 40, "deployed_revision": "a" * 40,
                  "tile_config_signature": "b" * 64}
    def lifecycle_row(ledger, record_id, **extra):
        return {**key.as_dict(), **provenance, "ledger": ledger, "record_id": record_id,
                "event_id": "decision-1", "observed_ts": 10_000.0, **extra}
    lifecycle_rows = [
        lifecycle_row(
            "order_intent", "terminal-schedule",
            intent_kind="AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL",
            schedule_lifecycle_final=True, chase_schedule_authoritative=True,
            schedule_sha256="c" * 64, requested_qty=1.0,
            chase_schedule={"terminal_ts": 10_000.0, "terminal_reason": "TTL_EXPIRED"},
        ),
        lifecycle_row("lifecycle", "terminal", terminal=True, terminal_no_fill=True),
        lifecycle_row("market_segment", "post", context_role="POST_EXIT_PATH",
                      coverage={"complete": True, "gaps_absent": True,
                                "complete_through_ts": 18_000.0}),
    ]
    completion = evaluate_lifecycle_completion(key, lifecycle_rows, now=20_000.0)["receipt"]
    collected = build_evidence_collected_receipt(
        completion, identity=key.as_dict(), event_id="decision-1",
        provenance=provenance, collected_at=20_000.0,
    )["receipt"]
    lifecycle_rows.extend([
        lifecycle_row("lifecycle", "bundle-completion", terminal=True,
                      observation_status="LIFECYCLE_BUNDLE_COMPLETE",
                      bundle_completion=completion),
        lifecycle_row("lifecycle", "evidence-collected", terminal=True,
                      observation_status="EVIDENCE_COLLECTION_COMPLETE",
                      evidence_collected_at=20_000.0,
                      evidence_collection_receipt=collected),
    ])
    assert materialize_bundle(tmp_path, key, lifecycle_rows, now=20_000.0)["written"] is True
    return v3


@pytest.mark.parametrize("direction,rows", [
    ("LONG", [_row(10, ask=100), _row(11, ask=100)]),
    ("SHORT", [_row(10, bid=100), _row(11, bid=100)]),
    ("LONG", [_row(10, ask=99), _row(11, ask=99)]),
])
def test_buy_sell_touch_and_trade_through_are_full_fills(tmp_path, direction, rows):
    result = build_v3_conservative_results(_fixture(tmp_path, direction=direction, entry_rows=rows))
    row = result["results"][0]
    assert row["classification"] == "FULL_FILL"
    assert row["filled_qty"] == 1
    assert row["comparison_cohort_key"].startswith("cohort-")
    assert row["fill_latency_sec"] == 0
    assert row["slippage_usd"] == (1 if direction == "LONG" and rows[0]["ask"] == 99 else 0)
    assert row["missed_entry_cost_usd"] is None
    assert row["missed_entry_cost_basis"] == "UNAVAILABLE_REQUIRES_DECLARED_MARK_HORIZON"
    assert row["qualification_evidence_collected"] is True
    assert row["lifecycle_evidence"]["status"] == "VERIFIED"
    assert result["lifecycle_evidence_coverage"]["coverage_complete"] is True
    assert row["volatility_at_signal"] == {
        "volatility_atr": 145.5,
        "volatility_percentile": 72.5,
        "atr14": 142.0,
        "atr14_pct_3m": 0.18,
        "realized_volatility": None,
        "volatility_metric": None,
    }


def test_missing_lifecycle_receipt_blocks_episode_before_ranking(tmp_path):
    v3 = _fixture(tmp_path)
    for manifest in (v3 / "lifecycle_bundles").glob("*/*/manifest.json"):
        import shutil
        shutil.rmtree(manifest.parent)
    result = build_v3_conservative_results(v3)
    row = result["results"][0]
    assert row["classification"] == "UNKNOWN"
    assert row["supported"] is False
    assert row["qualification_evidence_collected"] is False
    assert "UNKNOWN_LIFECYCLE_EVIDENCE_RECEIPT_MISSING" in row["unknown_reason_codes"]
    assert result["classification_counts"] == {
        "FULL_FILL": 0, "PARTIAL_FILL": 0, "NO_FILL": 0, "UNKNOWN": 1,
    }
    assert result["lifecycle_evidence_coverage"] == {
        "schema": "lifecycle_evidence_analyzer_coverage_v1",
        "episodes_total": 1,
        "verified_episode_count": 0,
        "unknown_episode_count": 1,
        "coverage_complete": False,
        "unknown_reason_counts": {"UNKNOWN_LIFECYCLE_EVIDENCE_RECEIPT_MISSING": 1},
        "bundle_index": {
            "schema": "lifecycle_evidence_join_index_v1", "manifest_count": 0,
            "valid_unique_count": 0, "invalid_count": 0,
            "duplicate_identity_count": 0, "defect_counts": {},
        },
    }


def test_partial_fill_preserves_all_quantity_boundaries(tmp_path):
    rows = [_row(10, ask=100, ask_qty=.45), _row(11, ask=100, ask_qty=.45)]
    row = build_v3_conservative_results(_fixture(tmp_path, entry_rows=rows))["results"][0]
    assert row["classification"] == "PARTIAL_FILL"
    assert row["requested_qty"] == 1
    assert row["available_qty"] == .45
    assert row["raw_partial_qty"] == .45
    assert row["rounded_executable_qty"] == .4
    assert row["accumulated_qty"] == .4
    assert row["minimum_lot_decision"] == "PASS"
    assert row["minimum_notional_decision"] == "PASS"
    assert row["quantity_attempts"][0]["accepted"] is True


def test_realized_terminal_outcome_requires_complete_measured_costs(tmp_path):
    v3 = _fixture(tmp_path)
    identity = {"epoch_id": "epoch-1", "opportunity_id": "opp-1", "episode_id": "ep-1",
                "policy_signature": "sig-1"}
    _write(v3 / "ledgers/execution.jsonl", [{
        **identity, "close_ts": 20, "gross_pnl_usd": 5.0,
        "trading_fees_usd": 0.5, "funding_fees_usd": 0.25,
        "entry_slippage_usd": 0.0, "exit_slippage_usd": 0.25,
        "filled_qty": 1.0, "net_pnl_usd": 4.0,
        "exit_price": 105, "exit_reason": "TARGET",
    }])
    _write(v3 / "ledgers/lifecycle.jsonl", [{
        **identity, "terminal": True, "outcome_state": "PAPER_REALIZED",
    }])
    row = build_v3_conservative_results(v3)["results"][0]
    assert row["classification"] == "FULL_FILL"
    assert row["terminal_outcome_status"] == "REALIZED_COST_COMPLETE"
    assert row["profitability_supported"] is True
    assert row["slippage_usd"] == 0.25
    assert row["net_pnl_usd"] == 4.0
    assert build_v3_conservative_results(v3)["terminal_outcome_counts"] == {
        "REALIZED_COST_COMPLETE": 1, "NOT_APPLICABLE_NO_FILL": 0, "UNKNOWN": 0,
    }


def test_missing_terminal_cost_is_unknown_without_changing_fill_classification(tmp_path):
    v3 = _fixture(tmp_path, direction="SHORT", entry_rows=[_row(10, bid=100), _row(11, bid=100)])
    identity = {"epoch_id": "epoch-1", "opportunity_id": "opp-1", "episode_id": "ep-1",
                "policy_signature": "sig-1"}
    _write(v3 / "ledgers/execution.jsonl", [{
        **identity, "close_ts": 20, "gross_pnl_usd": 2.0,
        "trading_fees_usd": 0.2, "funding_fees_usd": 0.0,
        "filled_qty": 1.0, "net_pnl_usd": 1.8,
    }])
    _write(v3 / "ledgers/lifecycle.jsonl", [{**identity, "terminal": True}])
    row = build_v3_conservative_results(v3)["results"][0]
    assert row["classification"] == "FULL_FILL"
    assert row["terminal_outcome_status"] == "UNKNOWN"
    assert row["profitability_supported"] is False
    assert "UNKNOWN_EXIT_SLIPPAGE_MISSING" in row["terminal_outcome_reason_codes"]
    assert row["net_pnl_usd"] is None


def test_true_no_fill_has_terminal_zero_outcome_without_execution_row(tmp_path):
    rows = [_row(10, ask=101), _row(11, ask=101)]
    row = build_v3_conservative_results(_fixture(tmp_path, entry_rows=rows))["results"][0]
    assert row["classification"] == "NO_FILL"
    assert row["terminal_outcome_status"] == "NOT_APPLICABLE_NO_FILL"
    assert row["profitability_supported"] is True
    assert row["net_pnl_usd"] == 0.0


def test_missing_pre_entry_path_blocks_profitability_but_preserves_fill_truth(tmp_path):
    v3 = _fixture(tmp_path)
    path = v3 / "ledgers/market_segment.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    _write(path, [row for row in rows if row.get("context_role") != "PRE_ENTRY_PATH"])
    row = build_v3_conservative_results(v3)["results"][0]
    assert row["classification"] == "FULL_FILL"
    assert row["pre_entry_path_status"] == "UNKNOWN"
    assert row["profitability_supported"] is False
    assert row["terminal_outcome_reason_codes"] == [
        "UNKNOWN_REQUIRED_PRE_ENTRY_BBO_DEPTH_TRADE_PATH_INCOMPLETE"
    ]


def test_regime_features_preserve_observed_sources_and_unknown_dimensions(tmp_path):
    v3 = _fixture(tmp_path)
    path = v3 / "ledgers/opportunity.jsonl"
    opportunity = json.loads(path.read_text().strip())
    opportunity["signal_ts"] = 1767225600
    opportunity["feature_snapshot_at_signal"].update({
        "realized_volatility": 0.0042,
        "spread_bps": 1.75,
        "order_book": {"bid_depth_qty": 2.5, "ask_depth_qty": 1.75},
        "session_bucket": "ASIA",
        "market_context": {
            "regime_label": "BEAR",
            "trend_strength": {"adx": 31.5, "trend_score": 0.8},
            "market_structure": {"structure_score": -2},
        },
    })
    path.write_text(json.dumps(opportunity) + "\n", encoding="utf-8")

    report = build_v3_conservative_results(v3)
    row = report["results"][0]
    assert row["regime_feature_schema"] == "phase7_regime_features_v1"
    features = row["regime_features_at_signal"]
    assert features["realized_volatility"] == {
        "status": "OBSERVED", "value": 0.0042,
        "source": "feature_snapshot.realized_volatility",
    }
    assert features["market_spread_bps"]["value"] == 1.75
    assert features["bid_depth_qty"]["value"] == 2.5
    assert features["ask_depth_qty"]["value"] == 1.75
    assert features["adx"]["value"] == 31.5
    assert features["trend_strength"]["value"] == 0.8
    assert features["market_structure"]["value"] == -2
    assert features["regime"]["value"] == "BEAR"
    assert features["session"]["value"] == "ASIA"
    assert features["signal_timestamp"]["value"] == 1767225600
    assert features["volatility_of_volatility"] == {
        "status": "UNKNOWN", "value": None, "source": None,
    }
    assert features["liquidity"]["status"] == "UNKNOWN"
    assert row["regime_feature_coverage"]["status"] == "PARTIAL"
    assert "volatility_of_volatility" in row["regime_feature_coverage"]["unknown_dimensions"]
    assert "liquidity" in row["regime_feature_coverage"]["unknown_dimensions"]
    aggregate = {item["name"]: item for item in report["regime_feature_coverage"]["dimensions"]}
    assert report["regime_feature_coverage"]["qualification_allowed"] is False
    assert report["regime_feature_coverage"]["profitability_calculated"] is False
    assert aggregate["realized_volatility"] == {
        "name": "realized_volatility", "observed_rows": 1,
        "unknown_rows": 0, "status": "OBSERVED",
    }
    assert aggregate["volatility_of_volatility"] == {
        "name": "volatility_of_volatility", "observed_rows": 0,
        "unknown_rows": 1, "status": "UNKNOWN",
    }
    support = report["phase7_support_qualification"]
    assert support["status"] == "NOT_SUPPORTED"
    assert support["qualification_allowed"] is False
    assert "REQUIRED_PHASE7_FEATURES_UNKNOWN_OR_INCONSISTENT" in support["reason_codes"]
    assert support["profitability_qualified"] is False
    assert support["live_trading_authorized"] is False


def _phase7_result(cohort, regime, direction, *, missing=None, source=True):
    values = {
        "realized_volatility": 0.004,
        "volatility_of_volatility": 0.0002,
        "market_spread_bps": 1.2,
        "bid_depth_qty": 2.0,
        "ask_depth_qty": 2.5,
        "liquidity": "NORMAL",
        "regime": regime,
        "adx": 27.0,
        "trend_strength": 0.6,
        "market_structure": "LOWER_HIGH",
        "session": "ASIA",
        "signal_timestamp": 1767225600,
    }
    features = {
        name: {
            "status": "UNKNOWN" if name == missing else "OBSERVED",
            "value": None if name == missing else value,
            "source": None if name == missing or not source else f"fixture.{name}",
        }
        for name, value in values.items()
    }
    return {
        "comparison_cohort_key": cohort,
        "side": direction,
        "regime_features_at_signal": features,
    }


def _small_phase7_config():
    return {
        "minimum_independent_cohorts": 6,
        "minimum_cohorts_per_regime_direction": 1,
    }


def test_phase7_support_uses_independent_cohorts_not_policy_row_count():
    repeated = [
        _phase7_result("cohort-1", "BEAR", "LONG") for _ in range(100)
    ]
    receipt = build_phase7_support_qualification(repeated, _small_phase7_config())
    assert receipt["row_count"] == 100
    assert receipt["independent_cohort_count"] == 1
    assert receipt["fully_observed_independent_cohort_count"] == 1
    assert receipt["qualification_allowed"] is False
    assert "INSUFFICIENT_INDEPENDENT_COHORTS" in receipt["reason_codes"]
    assert "INSUFFICIENT_REGIME_DIRECTION_COHORT_SUPPORT" in receipt["reason_codes"]


def test_phase7_support_requires_every_source_attributed_feature():
    rows = [_phase7_result("cohort-1", "BEAR", "LONG", source=False)]
    receipt = build_phase7_support_qualification(rows, {
        "minimum_independent_cohorts": 1,
        "minimum_cohorts_per_regime_direction": 1,
        "required_regimes": ["BEAR"],
        "required_directions": ["LONG"],
    })
    assert receipt["qualification_allowed"] is False
    assert all(not item["gate_passed"] for item in receipt["dimension_evidence"].values())
    assert "REQUIRED_PHASE7_FEATURES_UNKNOWN_OR_INCONSISTENT" in receipt["reason_codes"]


def test_phase7_missing_feature_on_one_policy_row_blocks_shared_cohort():
    rows = [
        _phase7_result("cohort-1", "BEAR", "LONG"),
        _phase7_result("cohort-1", "BEAR", "LONG", missing="liquidity"),
    ]
    receipt = build_phase7_support_qualification(rows, {
        "minimum_independent_cohorts": 1,
        "minimum_cohorts_per_regime_direction": 1,
        "required_regimes": ["BEAR"],
        "required_directions": ["LONG"],
    })
    assert receipt["qualification_allowed"] is False
    assert receipt["dimension_evidence"]["liquidity"]["unknown_cohorts"] == 1
    assert receipt["fully_observed_independent_cohort_count"] == 0


def test_phase7_support_passes_only_complete_balanced_independent_cohorts():
    rows = []
    for regime in ("BEAR", "BULL", "RANGE"):
        for direction in ("LONG", "SHORT"):
            rows.append(_phase7_result(f"cohort-{regime}-{direction}", regime, direction))
    receipt = build_phase7_support_qualification(rows, _small_phase7_config())
    assert receipt["status"] == "SUPPORTED_FOR_PHASE7_RESEARCH"
    assert receipt["qualification_allowed"] is True
    assert receipt["reason_codes"] == []
    assert all(receipt["gates"].values())
    assert receipt["profitability_qualified"] is False
    assert receipt["live_trading_authorized"] is False


def test_phase7_missing_feature_is_unknown_and_blocks_entire_cohort():
    rows = []
    for regime in ("BEAR", "BULL", "RANGE"):
        for direction in ("LONG", "SHORT"):
            missing = "volatility_of_volatility" if regime == "RANGE" and direction == "SHORT" else None
            rows.append(_phase7_result(
                f"cohort-{regime}-{direction}", regime, direction, missing=missing,
            ))
    receipt = build_phase7_support_qualification(rows, _small_phase7_config())
    assert receipt["qualification_allowed"] is False
    assert receipt["dimension_evidence"]["volatility_of_volatility"] == {
        "observed_cohorts": 5,
        "unknown_cohorts": 1,
        "inconsistent_cohorts": 0,
        "gate_passed": False,
    }
    assert receipt["regime_direction_cohorts"]["RANGE|SHORT"] == 0


def test_directional_score_gap_is_never_relabelled_as_exchange_spread(tmp_path):
    v3 = _fixture(tmp_path)
    opportunity_path = v3 / "ledgers/opportunity.jsonl"
    opportunity = json.loads(opportunity_path.read_text().strip())
    opportunity["feature_snapshot_at_signal"]["directional_spread"] = 27
    opportunity_path.write_text(json.dumps(opportunity) + "\n", encoding="utf-8")

    row = build_v3_conservative_results(v3)["results"][0]
    assert row["regime_features_at_signal"]["market_spread_bps"] == {
        "status": "UNKNOWN", "value": None, "source": None,
    }


def test_complete_non_crossing_tape_is_true_no_fill(tmp_path):
    rows = [_row(10, ask=101), _row(11, ask=101)]
    row = build_v3_conservative_results(_fixture(tmp_path, entry_rows=rows))["results"][0]
    assert row["classification"] == "NO_FILL"
    assert row["supported"] is True
    assert row["filled_qty"] == 0


def test_terminal_authoritative_schedule_is_used_instead_of_open_submit_version(tmp_path):
    v3 = _fixture(tmp_path)
    path = v3 / "ledgers/order_intent.jsonl"
    submit = json.loads(path.read_text().strip())
    submit_schedule = dict(submit["chase_schedule"])
    submit_schedule["intervals"] = [{"bucket_id": "s0", "start_ts": 10,
                                      "end_ts": None, "limit_price": 101}]
    submit["intent_kind"] = "ACTUAL_PAPER_LIMIT_SUBMIT"
    submit["chase_schedule"] = submit_schedule
    submit["schedule_sha256"] = hashlib.sha256(
        canonical_json(submit_schedule).encode()
    ).hexdigest()
    terminal = dict(submit)
    terminal_schedule = {
        "authoritative": True,
        "intervals": [{"bucket_id": "s0", "start_ts": 10,
                       "end_ts": 12, "limit_price": 100}],
        "terminal_ts": 12, "terminal_reason": "FILLED",
    }
    terminal["intent_kind"] = "AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL"
    terminal["chase_schedule"] = terminal_schedule
    terminal["schedule_sha256"] = hashlib.sha256(
        canonical_json(terminal_schedule).encode()
    ).hexdigest()
    _write(path, [submit, terminal])

    row = build_v3_conservative_results(v3)["results"][0]
    assert row["classification"] == "FULL_FILL"
    assert row["schedule_sha256"] == terminal["schedule_sha256"]


def test_complete_all_opportunity_future_tape_is_usable_as_exact_entry_path(tmp_path):
    v3 = _fixture(tmp_path)
    path = v3 / "ledgers/market_segment.jsonl"
    segments = [json.loads(line) for line in path.read_text().splitlines()]
    future = segments[0]
    future.pop("context_role", None)
    future["segment_role"] = "SIGNAL_TO_120M_FUTURE_PATH"
    future["future_path_status"] = "COMPLETE"
    future["coverage"] = {
        "conservative_bbo_depth_eligible": True,
        "required_horizons_sec": [60, 300, 900, 1800, 3600, 7200],
    }
    _write(path, [future])

    row = build_v3_conservative_results(v3)["results"][0]
    assert row["classification"] == "FULL_FILL"
    assert row["supported"] is True


def test_recovery_overlay_supersedes_raw_unknown_for_evaluation(tmp_path):
    v3 = _fixture(tmp_path)
    path = v3 / "ledgers/market_segment.jsonl"
    segments = [json.loads(line) for line in path.read_text().splitlines()]
    future = segments[0]
    future.pop("context_role", None)
    future.update({
        "record_id": "recovered-complete",
        "future_path_owner_key": "owner-1",
        "segment_role": "SIGNAL_TO_120M_FUTURE_PATH",
        "future_path_status": "COMPLETE",
        "coverage": {
            "conservative_bbo_depth_eligible": True,
            "required_horizons_sec": [60, 300, 900, 1800, 3600, 7200],
        },
    })
    unknown = {
        key: future[key] for key in ("epoch_id", "opportunity_id", "episode_id")
    }
    unknown.update({
        "record_id": "raw-unknown", "future_path_owner_key": "owner-1",
        "segment_role": "SIGNAL_TO_120M_FUTURE_PATH",
        "future_path_status": "UNKNOWN", "segment_ref": None,
    })
    _write(path, [unknown])
    _write(v3 / "recovery_ledgers/market_segment.jsonl", [future])

    row = build_v3_conservative_results(v3)["results"][0]
    assert row["classification"] == "FULL_FILL"
    assert row["supported"] is True
    assert row["tape_ids"] == [future["segment_ref"]["sha256"]]


def test_incomplete_all_opportunity_future_tape_remains_unknown(tmp_path):
    v3 = _fixture(tmp_path)
    path = v3 / "ledgers/market_segment.jsonl"
    segments = [json.loads(line) for line in path.read_text().splitlines()]
    future = segments[0]
    future.pop("context_role", None)
    future["segment_role"] = "SIGNAL_TO_120M_FUTURE_PATH"
    future["future_path_status"] = "COMPLETE"
    future["coverage"] = {
        "conservative_bbo_depth_eligible": True,
        "required_horizons_sec": [60, 300, 900, 1800, 3600],
    }
    _write(path, [future])

    row = build_v3_conservative_results(v3)["results"][0]
    assert row["classification"] == "UNKNOWN"
    assert "UNKNOWN_FUTURE_ENTRY_PATH_INCOMPLETE" in row["unknown_reason_codes"]


def test_missing_market_second_is_unknown_not_no_fill(tmp_path):
    row = build_v3_conservative_results(
        _fixture(tmp_path, entry_rows=[_row(10, ask=101)])
    )["results"][0]
    assert row["classification"] == "UNKNOWN"
    assert "UNKNOWN_CONSERVATIVE_EVALUATOR_EVIDENCE_GAP" in row["unknown_reason_codes"]


def test_missing_constraints_is_unknown_and_never_speculates(tmp_path):
    row = build_v3_conservative_results(
        _fixture(tmp_path, constraints=False)
    )["results"][0]
    assert row["classification"] == "UNKNOWN"
    assert any("SIGNED_QUANTITY_CONSTRAINTS_MISSING" in reason for reason in row["unknown_reason_codes"])


def test_generation_bound_artifact_and_query_cache_retain_every_result(tmp_path):
    root = tmp_path / "canonical-research-data"
    _fixture(root, entry_rows=[_row(10, ask=101), _row(11, ask=101)])
    manifest = {"entry_hash": "a" * 64, "dataset_epoch": "epoch-1",
                "source_revision": "rev-1", "tile_config_signature": "b" * 64}
    (root / "canonical_dataset_current.json").write_text(json.dumps(manifest), encoding="utf-8")
    summary = persist_v3_conservative_results(root, analyzer_revision="rev-1")
    artifact = root / summary["relative_path"]
    with gzip.open(artifact, "rt", encoding="utf-8") as handle:
        stored = [json.loads(line) for line in handle if line.strip()]
    assert len(stored) == summary["cache_rows_ingested"] == 1
    assert stored[0]["classification"] == "NO_FILL"
    cache = artifact.parent / "results.sqlite"
    with sqlite3.connect(cache) as connection:
        assert connection.execute("SELECT classification FROM episode_policy_result").fetchone()[0] == "NO_FILL"


def test_current_v3_nested_dimensions_are_preserved_and_queryable(tmp_path):
    root = tmp_path / "canonical-research-data"
    v3 = _fixture(root, entry_rows=[_row(10, ask=101), _row(11, ask=101)])
    decision_path = v3 / "ledgers/decision.jsonl"
    decision = json.loads(decision_path.read_text().strip())
    for field in ("direction", "policy_family", "entry_offset_pct", "chase_policy",
                  "exit_family", "regime"):
        decision.pop(field, None)
    decision.update({
        "executed_direction": "LONG", "raw_ai_decision": "APPROVE",
        "long_score": 78, "short_score": 22, "score_gap": 56,
        "paper_policy_spec": {
            "schema": "paper_policy_identity_spec_v3",
            "entry_offset_fraction": 0.003,
            "entry_limit_policy": "OFFSET_0.30_CHASE_W234_S50_I180",
            "exit_config": {"family": "CHANDELIER", "exit_profile_id": "CHANDELIER_1.5"},
        },
    })
    _write(decision_path, [decision])
    opportunity_path = v3 / "ledgers/opportunity.jsonl"
    opportunity = json.loads(opportunity_path.read_text().strip())
    opportunity["feature_snapshot_at_signal"] = {
        "market_context": {"regime_label": "BULL"}
    }
    _write(opportunity_path, [opportunity])
    manifest = {"entry_hash": "a" * 64, "dataset_epoch": "epoch-1",
                "source_revision": "rev-1", "tile_config_signature": "b" * 64}
    (root / "canonical_dataset_current.json").write_text(json.dumps(manifest), encoding="utf-8")
    summary = persist_v3_conservative_results(root, analyzer_revision="rev-1")
    assert summary["cache_rows_ingested"] == 1

    from research.policy_evidence_library import PolicyEvidenceLibrary
    library = PolicyEvidenceLibrary(str(root), manifest, analyzer_revision="rev-1")
    result = library.query({
        "evidence_world": "CONSERVATIVE_BBO_DEPTH_TAPE",
        "entry_offset_pct": "0.30", "family": "CHANDELIER",
        "chase_policy": "OFFSET_0.30_CHASE_W234_S50_I180",
        "exit_family": "CHANDELIER_1.5", "regime": "BULL",
        "side": "LONG", "ai_direction": "LONG", "ai_decision": "APPROVE",
        "policy_signature": "sig-1", "opportunity_id": "opp-1",
    })
    assert result["row_count"] == 1
    stored = result["rows"][0]
    assert stored["entry_offset_pct"] == "0.30"
    assert stored["long_score"] == 78
    assert stored["short_score"] == 22
    assert stored["score_gap"] == 56


def test_missing_opportunity_identity_remains_unknown_in_artifact_and_is_explicitly_skipped_from_cache(tmp_path):
    root = tmp_path / "canonical-research-data"
    v3 = _fixture(root)
    decision_path = v3 / "ledgers/decision.jsonl"
    decision = json.loads(decision_path.read_text().strip())
    decision["opportunity_id"] = None
    _write(decision_path, [decision])
    manifest = {"entry_hash": "a" * 64, "dataset_epoch": "epoch-1",
                "source_revision": "rev-1", "tile_config_signature": "b" * 64}
    (root / "canonical_dataset_current.json").write_text(json.dumps(manifest), encoding="utf-8")
    summary = persist_v3_conservative_results(root, analyzer_revision="rev-1")
    assert summary["row_count"] == 1
    assert summary["cache_rows_ingested"] == 0
    assert summary["cache_rows_skipped_missing_identity"] == 1
    assert summary["cache_skip_reason_counts"] == {
        "RESULT_IDENTITY_MISSING_COMPARISON_COHORT_KEY": 1,
        "RESULT_IDENTITY_MISSING_OPPORTUNITY_ID": 1,
    }
    artifact = root / summary["relative_path"]
    with gzip.open(artifact, "rt", encoding="utf-8") as handle:
        row = json.loads(next(handle))
    assert row["opportunity_id"] is None
    assert row["comparison_cohort_key"] is None
    assert row["classification"] == "UNKNOWN"
    assert "UNKNOWN_CAUSAL_IDENTITY_INCOMPLETE" in row["unknown_reason_codes"]

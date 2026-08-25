"""Canonical join for cont-a0bdfd55cd01: copy-only partial fill after chase.

The source Showcase order stayed unfilled. Bitfinex chased, partially filled
about seven minutes before source abandonment was processed, protected the
partial immediately, then closed on SHOWCASE_ABANDONED late-fill cleanup.
This test uses that exact lifecycle shape and must not invent a Showcase fill.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd

import analyzer_research_engine_v62 as analyzer
from research.analysis_eligibility import (
    BITFINEX_COPY_FIDELITY,
    COPY_ONLY_EXCHANGE_FILLS,
    CORRELATED_CLUSTER_BLOCKED,
    REAL_COPY_PARAMETER_OPTIMISATION,
    SHOWCASE_STRATEGY,
    classify_row,
)
from research.counterfactual_normalization import policy_comparability_key
from research.platform_relay_evidence import (
    _normalize_platform_bitfinex_evidence,
    _snapshot_with_platform_relay_evidence,
)

TRADE_ID = "cont-a0bdfd55cd01"
PARTICIPANT_ID = "cmsx2ngi10021o301wd8nwkwa"
ENTRY_ORDER_ID = 242028338338
CHASED_LIMIT = 63380.05
ORIGINAL_LIMIT = 63396.4
FILLED_QTY = 0.01291132
INTENDED_QTY = 0.03157
ENTRY_FILL_IDS = ["1958422273", "1958422271"]
EXIT_ORDER_ID = 242030567338
EXIT_FILL_ID = "1958422416"
ENTRY_VWAP = 63381
EXIT_PRICE = 63370
PNL_USD = 0.31
CLOSE_REQUEST_ID = "7c50d601-ec6a-4e21-90de-f02220d5e416"


def _event(event_id, event_type, created_at, payload):
    return {
        "id": event_id,
        "eventType": event_type,
        "createdAt": created_at,
        "payload": payload,
    }


def _cont_a0_records():
    authority = {
        "kind": "LATE_FILL_CLEANUP",
        "evidence": {
            "policy": "EXCHANGE_ONLY_PARTIAL_FILL_TERMINAL_CLEANUP",
            "filled_qty": FILLED_QTY,
            "fill_source": "ORDER_PARTIAL",
            "intended_qty": INTENDED_QTY,
            "cancel_context": "SHOWCASE_ABANDONED",
        },
        "canonicalTradeId": TRADE_ID,
        "lifecycleGeneration": "seq:3",
    }
    return [{
        "canonicalTradeId": TRADE_ID,
        "lifecycleId": "cyc_rel_conta0bdfd55cd01",
        "participantId": PARTICIPANT_ID,
        "events": [
            _event("order", "ORDER_PLACED", "2026-08-17T10:09:14.211Z", {
                "bitfinexOrderId": ENTRY_ORDER_ID,
                "clientOrderId": 398627330,
                "limitPrice": ORIGINAL_LIMIT,
                "originalLimitPrice": ORIGINAL_LIMIT,
                "limitChaseCount": 0,
                "qty": INTENDED_QTY,
                "leverage": 100,
                "sourceEventId": f"{TRADE_ID}:ORDER_PLACED:2:2026-08-17T10:09:11.039069+00:00",
                "sourceEventSeq": 2,
                "sourceEventAt": "2026-08-17T10:09:11.039Z",
                "sourceToPlatformMs": 90,
                "sourceToExchangeAckMs": 3036,
                "entryExchangeAckAtMs": 1786961354075,
                "fee_model": {
                    "schema": "execution_cost_profile_v1",
                    "venue": "bitfinex",
                    "configured_profile": "BITFINEX_ZERO",
                    "maker_fee_rate": 0,
                    "taker_fee_rate": 0,
                },
                "execution_profile": "bitfinex-live-limit-v1",
                "correlated_cluster_evidence": {
                    "schema": "correlated_exposure_cluster_v2",
                    "allowed": True,
                    "boundary_pct": 0.09,
                    "same_direction_managed_or_reserved_count": 1,
                },
            }),
            _event("chase", "UPDATE_STOPS", "2026-08-17T10:10:14.017Z", {
                "event": "BOT_ANCHOR_CHASE",
                "bitfinexOrderId": ENTRY_ORDER_ID,
                "clientOrderId": 398627330,
                "limitPrice": CHASED_LIMIT,
                "new_limit": CHASED_LIMIT,
                "prior_limit": ORIGINAL_LIMIT,
                "limitChaseCount": 1,
                "sourceEventId": f"{TRADE_ID}:LIMIT_UPDATED:3:2026-08-17T10:10:12.053297+00:00",
                "sourceEventSeq": 3,
                "sourceEventAt": "2026-08-17T10:10:12.053Z",
                "sourceToPlatformMs": 112,
                "sourceToExchangeAckMs": 1773,
                "replacementExchangeAckAtMs": 1786961413826,
                "replacementMode": "BITFINEX_IN_PLACE_UPDATE",
            }),
            _event("pstop1", "UPDATE_STOPS", "2026-08-17T10:16:02.204Z", {
                "event": "PARTIAL_FILL_STOP_REPLACEMENT_ACKNOWLEDGED",
                "bitfinexOrderId": ENTRY_ORDER_ID,
                "partialFillQty": 0.00677944,
                "remaining_qty": 0.02479056,
                "exchange_fill_price": ENTRY_VWAP,
                "intended_qty": INTENDED_QTY,
                "partialFillStopOrderId": 242025292549,
                "stopOrderId": 242025292549,
                "stopClientOrderId": 1847635826,
                "stop_exchange_ack_at": "2026-08-17T10:16:01.650Z",
                "detection_to_stop_ack_ms": 558,
                "copy_reconciliation_state": "COPY_FILLED_SOURCE_PENDING_OR_UNKNOWN",
                "source_model_fill_state": "INDEPENDENT_OR_PENDING",
                "exchange_fill_authoritative": True,
            }),
            _event("pstop2", "UPDATE_STOPS", "2026-08-17T10:16:05.619Z", {
                "event": "PARTIAL_FILL_STOP_REPLACEMENT_ACKNOWLEDGED",
                "bitfinexOrderId": ENTRY_ORDER_ID,
                "partialFillQty": FILLED_QTY,
                "remaining_qty": 0.01865868,
                "exchange_fill_price": ENTRY_VWAP,
                "intended_qty": INTENDED_QTY,
                "partialFillStopOrderId": 242026257497,
                "stopOrderId": 242026257497,
                "stopClientOrderId": 2066588504,
                "stop_exchange_ack_at": "2026-08-17T10:16:04.873Z",
                "detection_to_stop_ack_ms": 1929,
                "copy_reconciliation_state": "COPY_FILLED_SOURCE_PENDING_OR_UNKNOWN",
                "source_model_fill_state": "INDEPENDENT_OR_PENDING",
                "exchange_fill_authoritative": True,
            }),
            _event("filled", "FILLED", "2026-08-17T10:23:42.562Z", {
                "event": "CANCEL_RACE_FILL",
                "clientOrderId": 398627330,
                "bitfinexOrderId": ENTRY_ORDER_ID,
                "qty": FILLED_QTY,
                "intended_qty": INTENDED_QTY,
                "fill_price": ENTRY_VWAP,
                "exchange_fill_ids": ENTRY_FILL_IDS,
                "fill_detection_path": "ORDER_PARTIAL",
                "fill_detection_context": "SHOWCASE_ABANDONED",
                "entry_completion": "FILLED",
                "copy_reconciliation_state": "COPY_ONLY_FILL_AUTHENTICATED_SOURCE_UNCONFIRMED",
                "source_model_fill_state": "SOURCE_UNCONFIRMED",
                "exchange_fill_authoritative": True,
            }),
            _event("claim", "TERMINAL_CLOSE_CLAIM", "2026-08-17T10:23:44.754Z", {
                "schema": "terminal_close_fence_v1",
                "authority": authority,
                "request_id": CLOSE_REQUEST_ID,
                "phase": "CLAIMED",
            }),
            _event("submit", "TERMINAL_CLOSE_SUBMITTING", "2026-08-17T10:23:46.027Z", {
                "authority": authority,
                "request_id": CLOSE_REQUEST_ID,
                "phase": "SUBMITTING",
                "close_qty": FILLED_QTY,
            }),
            _event("ack", "TERMINAL_CLOSE_ACKNOWLEDGED", "2026-08-17T10:23:46.628Z", {
                "authority": authority,
                "request_id": CLOSE_REQUEST_ID,
                "phase": "ACKNOWLEDGED",
                "exchange_order_id": str(EXIT_ORDER_ID),
            }),
            _event("confirm", "TERMINAL_CLOSE_CONFIRMED", "2026-08-17T10:23:47.448Z", {
                "authority": authority,
                "request_id": CLOSE_REQUEST_ID,
                "phase": "CONFIRMED",
            }),
            _event("exit", "EXIT", "2026-08-17T10:23:50.160Z", {
                "authenticated_matched_quantity": FILLED_QTY,
                "bitfinex_order_ids": [ENTRY_ORDER_ID, EXIT_ORDER_ID],
                "close_exchange_order_id": EXIT_ORDER_ID,
                "exchange_order_id": EXIT_ORDER_ID,
                "exit_price": 63357,
                "exit_exchange_fill_price": EXIT_PRICE,
                "entry_exchange_fill_price": ENTRY_VWAP,
                "entry_fill_ids": ENTRY_FILL_IDS,
                "exit_fill_ids": [EXIT_FILL_ID],
                "pnl_usd": PNL_USD,
                "exit_reason": "SHOWCASE_MIRROR",
                "terminal_authority_kind": "LATE_FILL_CLEANUP",
                "terminal_close_request_id": CLOSE_REQUEST_ID,
                "entry_exchange_fees": [
                    {"amount": 0, "fillId": 1958422273, "currency": "USD"},
                    {"amount": 0, "fillId": 1958422271, "currency": "USD"},
                ],
                "exit_exchange_fees": [
                    {"amount": 0, "fillId": 1958422416, "currency": "USD"},
                ],
                "fee_model": {
                    "schema": "execution_cost_profile_v1",
                    "venue": "bitfinex",
                    "configured_profile": "BITFINEX_ZERO",
                    "maker_fee_rate": 0,
                    "taker_fee_rate": 0,
                },
                "execution_profile": "bitfinex-live-limit-v1",
                "final_reconciliation": {
                    "schema": "relay_final_reconciliation_v1",
                    "complete": True,
                    "order_delta": 0,
                    "orphan_order_ids": [],
                    "foreign_order_ids": [],
                    "orphan_order_count": 0,
                    "foreign_order_count": 0,
                    "position_reconciled": True,
                    "expected_ledger_amount": 0,
                    "exchange_position_amount": 0,
                    "managed_order_count_after": 0,
                    "exchange_vs_ledger_delta_sats": 0,
                },
            }),
        ],
    }]


def _relay_index(records, revision="af5b912f3de5e6afbe9ca1a366f892c0f8591b31"):
    return {
        TRADE_ID: {
            "schema": "relay_lifecycle_evidence_v1",
            "generated_at": "2026-08-17T11:06:56.624Z",
            "generating_revision": revision,
            "run_identity": "run-a0",
            "records": records,
            "evidence_revision": "rev-a0",
        }
    }


def _source_snapshot():
    return {
        "trade_id": TRADE_ID,
        "executed": False,
        "status": "EXPIRED",
        "filled": False,
        "exit_reason": "NO_FILL",
        "policy_snapshot_complete": True,
        "policy_version": "v31-two-lane-safe-policy",
        "policy_snapshot": {
            "policy_snapshot_schema": "exit_policy_v1",
            "policy_version": "v31-two-lane-safe-policy",
            "hard_stop_margin_pct": -13.0,
            "thesis_fast_exit_unreal_pct": -12.0,
            "thesis_mfe_protect_pct": 5.0,
            "trail_ladder": [[4, 2], [5, 3], [8, 5], [12, 10]],
            "exit_profile_id": "SCENARIO_C_RUNNER_4_v7_20260813",
            "chase_enabled": True,
            "correlated_cluster_boundary_pct": 0.09,
        },
        "replay_complete": True,
    }


def test_normalizer_preserves_showcase_unfilled_and_copy_partial_fill():
    records = _cont_a0_records()
    evidence = _normalize_platform_bitfinex_evidence(records, TRADE_ID)
    assert evidence["source_fill_status"] in {"UNFILLED", "UNKNOWN"}
    assert evidence["copy_fill_status"] in {"PARTIAL", "FILLED"}
    assert evidence["divergence_class"] == "COPY_ONLY_PARTIAL_FILL"
    assert evidence["terminal_class"] == "SHOWCASE_ABANDONED_LATE_FILL_CLEANUP"
    assert evidence["filled_quantity"] == FILLED_QTY
    assert evidence["entry_fill_ids"] == ENTRY_FILL_IDS
    assert EXIT_FILL_ID not in (evidence.get("entry_fill_ids") or [])
    assert evidence["exit_fill_ids"] == [EXIT_FILL_ID]
    assert evidence["actual_bitfinex_realized_pnl_usd"] == PNL_USD
    assert evidence["exit_evidence"]["order_id"] == EXIT_ORDER_ID
    assert evidence["reconciliation_complete"] is True
    chase = evidence["chase_history"]
    assert len(chase) == 1
    assert chase[0]["prior_price"] == ORIGINAL_LIMIT
    assert chase[0]["price"] == CHASED_LIMIT
    assert chase[0]["source_event_seq"] == 3
    assert chase[0]["source_to_exchange_ack_ms"] == 1773
    assert chase[0]["fill_relative_to_source_abandonment"] == "BEFORE_ABANDONMENT"
    assert evidence["copy_terminal_fence_complete"] is True
    assert evidence["linkage_complete"] is True
    assert evidence["cost_evidence_complete"] is True


def test_snapshot_join_does_not_overwrite_showcase_truth():
    records = _cont_a0_records()
    source = _source_snapshot()
    enriched = _snapshot_with_platform_relay_evidence(
        copy.deepcopy(source), TRADE_ID, _relay_index(records)
    )
    assert enriched["executed"] is False
    assert enriched["status"] == "EXPIRED"
    assert enriched["filled"] is False
    assert enriched["source_fill_status"] in {"UNFILLED", "UNKNOWN"}
    assert enriched["copy_fill_status"] in {"PARTIAL", "FILLED"}
    assert enriched["divergence_class"] == "COPY_ONLY_PARTIAL_FILL"
    assert enriched["terminal_class"] == "SHOWCASE_ABANDONED_LATE_FILL_CLEANUP"
    assert enriched["actual_bitfinex_realized_pnl_usd"] == PNL_USD
    truth = enriched["dual_execution_truth"]
    assert truth["showcase_simulated"]["executed"] is False
    assert truth["bitfinex_authenticated"]["authenticated"] is True
    assert truth["relationship"]["excluded_from_showcase_strategy_stats"] is True


def test_copy_only_partial_fill_is_not_a_showcase_win_or_loss():
    records = _cont_a0_records()
    source = _source_snapshot()
    enriched = _snapshot_with_platform_relay_evidence(
        copy.deepcopy(source), TRADE_ID, _relay_index(records)
    )
    result = classify_row(enriched)
    assert result["eligible"][SHOWCASE_STRATEGY] is False
    assert "COPY_ONLY_SOURCE_UNCONFIRMED" in result["exclusion_reasons"][SHOWCASE_STRATEGY]
    assert result["eligible"][COPY_ONLY_EXCHANGE_FILLS] is True
    assert result["eligible"][BITFINEX_COPY_FIDELITY] is True
    assert result["eligible"][REAL_COPY_PARAMETER_OPTIMISATION] is False
    assert result["eligible"][CORRELATED_CLUSTER_BLOCKED] is False


def test_cluster_blocked_rows_are_counterfactual_not_copy_failures():
    result = classify_row({
        "trade_id": "cont-05eb9fe78025",
        "policy_snapshot_complete": True,
        "policy_version": 5,
        "replay_complete": True,
        "terminal_provenance": "CORRELATED_CLUSTER_BLOCKED",
        "lifecycle_events": [{"event_type": "CORRELATED_CLUSTER_BLOCKED"}],
        "bitfinex_evidence": {
            "negative_events": [{"event": "CORRELATED_CLUSTER_BLOCKED"}],
            "analysis_exclusion_reasons": ["CORRELATED_CLUSTER_BLOCKED"],
        },
    })
    assert result["eligible"][CORRELATED_CLUSTER_BLOCKED] is True
    assert result["eligible"][BITFINEX_COPY_FIDELITY] is False
    assert result["eligible"][SHOWCASE_STRATEGY] is False
    assert "CORRELATED_CLUSTER_BLOCKED" in result["exclusion_reasons"][BITFINEX_COPY_FIDELITY]
    assert "MISSED_COPY_FAILURE" not in result["exclusion_reasons"][CORRELATED_CLUSTER_BLOCKED]


def test_chase_attribution_reads_relay_chase_not_funnel_unfilled(tmp_path, monkeypatch):
    records = _cont_a0_records()
    source = _source_snapshot()
    enriched = _snapshot_with_platform_relay_evidence(
        copy.deepcopy(source), TRADE_ID, _relay_index(records)
    )
    funnel = tmp_path / "execution_funnel.jsonl"
    funnel.write_text(
        json.dumps({"trade_id": TRADE_ID, "stage": "ORDER_SUBMITTED", "limit_price": ORIGINAL_LIMIT})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(analyzer, "_agent_data_path", lambda name: str(funnel if name == analyzer.EXECUTION_FUNNEL_FILE else tmp_path / name))
    monkeypatch.setattr(analyzer, "_platform_relay_evidence_index", lambda path=None: _relay_index(records))
    monkeypatch.setattr(
        analyzer,
        "_load_jsonl_by_trade_id",
        lambda path: {TRADE_ID: enriched} if "counterfactual" in str(path) else {},
    )
    trades = pd.DataFrame([{
        "trade_id": TRADE_ID,
        "net_pnl_usd": 0.0,
        "limit_chase_count": 0,
        "dur_min": 14.0,
    }])
    report = analyzer.chase_attribution_report(trades=trades, session={})
    row = next(item for item in report["trades"] if item["trade_id"] == TRADE_ID)
    assert row["source_fill_status"] in {"UNFILLED", "UNKNOWN"}
    assert row["copy_fill_status"] in {"PARTIAL", "FILLED"}
    assert row["chase_count"] == 1
    assert row["original_limit_price"] == ORIGINAL_LIMIT
    assert row["final_limit_price"] == CHASED_LIMIT
    assert row["copy_fill_price"] == ENTRY_VWAP
    assert row["net_pnl_usd"] == PNL_USD
    assert row["fill_relative_to_source_abandonment"] == "BEFORE_ABANDONMENT"
    assert row["fill_reason"] != "UNFILLED" or row["copy_fill_status"] in {"PARTIAL", "FILLED"}


def test_policy_comparability_key_fails_closed_until_complete_snapshot():
    policy = _source_snapshot()["policy_snapshot"]
    buf = {"leverage": 100}
    snapshot = {
        "fee_model": {"schema": "execution_cost_profile_v1", "venue": "bitfinex"},
        "execution_profile": "bitfinex-live-limit-v1",
        "bitfinex_evidence": {
            "cluster_evidence": {"boundary_pct": 0.09},
            "generating_revision": "af5b912f3de5e6afbe9ca1a366f892c0f8591b31",
        },
        "source_git_rev": "b99aceffb91a1345a96336ba123f651368b3be13",
        "executor_revision": "af5b912f3de5e6afbe9ca1a366f892c0f8591b31",
        "epoch_id": "epoch-d93cddc91653a5c7bba07162",
        "fill_gate_rev": "venue_fill_gate_v1",
    }
    key = policy_comparability_key(policy, buf, snapshot)
    assert key and key.startswith("policy_comparability_v1:")
    incomplete = policy_comparability_key(policy, buf, {"fee_model": None})
    assert incomplete is None


def test_copy_only_row_is_excluded_from_showcase_and_real_copy_opt_reports(tmp_path, monkeypatch):
    records = _cont_a0_records()
    enriched = _snapshot_with_platform_relay_evidence(
        copy.deepcopy(_source_snapshot()), TRADE_ID, _relay_index(records)
    )
    monkeypatch.chdir(tmp_path)

    def _eligible(cohort=analyzer.REAL_COPY_PARAMETER_OPTIMISATION):
        ids, exclusions = analyzer._cohort_eligible_trade_ids({TRADE_ID: enriched}, cohort)
        return ids, exclusions, 1

    monkeypatch.setattr(analyzer, "_load_jsonl_by_trade_id", lambda path: {TRADE_ID: enriched})
    monkeypatch.setattr(analyzer, "_analysis_eligible_trade_ids", _eligible)
    reports = analyzer.research_cohort_split_reports()
    showcase_ids = {row["trade_id"] for row in reports[SHOWCASE_STRATEGY]["trades"]}
    copy_only_ids = {row["trade_id"] for row in reports[COPY_ONLY_EXCHANGE_FILLS]["trades"]}
    opt_ids = {row["trade_id"] for row in reports[REAL_COPY_PARAMETER_OPTIMISATION]["trades"]}
    assert TRADE_ID not in showcase_ids
    assert TRADE_ID in copy_only_ids
    assert TRADE_ID not in opt_ids
    assert reports["overlap_guard"]["showcase_and_copy_only"] == []
    row = next(item for item in reports[COPY_ONLY_EXCHANGE_FILLS]["trades"] if item["trade_id"] == TRADE_ID)
    assert row["source_fill_status"] in {"UNFILLED", "UNKNOWN"}
    assert row["copy_fill_status"] in {"PARTIAL", "FILLED"}
    assert row["divergence_class"] == "COPY_ONLY_PARTIAL_FILL"
    assert row["qualified"] is False


def test_showcase_losing_cluster_is_preliminary_descriptive_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(analyzer, "_fresh_epoch_provenance", lambda: {"fresh_epoch_status": "BOUND"})
    trades = pd.DataFrame([
        {"trade_id": "cont-loss-1", "dir": "LONG", "exit_reason": "THESIS_FAST_CUT",
         "net_pnl_usd": -2.33, "max_profit": 0.0, "entry_delay_sec": 974.5, "limit_chase_count": 2},
        {"trade_id": "cont-loss-2", "dir": "LONG", "exit_reason": "ADMIN_MANUAL_CLOSE",
         "net_pnl_usd": -1.70, "max_profit": 1.43, "entry_delay_sec": 1390.2, "limit_chase_count": 4},
    ])
    report = analyzer.showcase_losing_cluster_descriptive_report(trades=trades, session={})
    assert report["classification"] == "PRELIMINARY_DESCRIPTIVE"
    assert report["parameter_recommendation"] is None
    assert report["live_policy_change_allowed"] is False
    assert report["closed_paper_trades"] == 2
    assert report["winners"] == 0
    assert report["losses"] == 2


def test_relay_only_trade_is_seeded_when_counterfactual_row_is_missing(tmp_path, monkeypatch):
    records = _cont_a0_records()
    snapshot = _source_snapshot()
    (tmp_path / "counterfactual.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "signal_snapshot.jsonl").write_text(json.dumps(snapshot) + "\n", encoding="utf-8")
    (tmp_path / "execution_funnel.jsonl").write_text(
        json.dumps({"trade_id": TRADE_ID, "stage": "ORDER_SUBMITTED"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "relay_lifecycle_evidence_v1.json").write_text(
        json.dumps({
            "schema": "relay_lifecycle_evidence_v1",
            "generatedAt": "2026-08-17T11:06:56.624Z",
            "generatingRevision": "af5b912f3de5e6afbe9ca1a366f892c0f8591b31",
            "runIdentity": "run-a0",
            "records": records,
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(analyzer, "_agent_data_path", lambda name: str(tmp_path / Path(name).name))
    rows = analyzer._load_jsonl_by_trade_id(analyzer.COUNTERFACTUAL_FILE)
    assert TRADE_ID in rows
    row = rows[TRADE_ID]
    assert row.get("executed") is not True
    assert row.get("source_fill_status") in {"UNFILLED", "UNKNOWN"}
    assert row.get("copy_fill_status") in {"PARTIAL", "FILLED"}
    assert row.get("divergence_class") == "COPY_ONLY_PARTIAL_FILL"
    assert row.get("actual_bitfinex_realized_pnl_usd") == PNL_USD


def test_catalog_exposes_separate_research_cohort_reports():
    names = {row[1] for row in analyzer.DEEP_DIVE_REPORT_CATALOG}
    assert analyzer.SHOWCASE_STRATEGY_OUTCOMES_REPORT_FILE in names
    assert analyzer.BITFINEX_COPY_FIDELITY_REPORT_FILE in names
    assert analyzer.CORRELATED_CLUSTER_BLOCKED_REPORT_FILE in names
    assert analyzer.COPY_ONLY_EXCHANGE_FILLS_REPORT_FILE in names
    assert analyzer.REAL_COPY_PARAMETER_OPTIMISATION_REPORT_FILE in names
    assert analyzer.SHOWCASE_LOSING_CLUSTER_REPORT_FILE in names
    assert analyzer.RESEARCH_HORIZON_MATURITY_REPORT_FILE in names

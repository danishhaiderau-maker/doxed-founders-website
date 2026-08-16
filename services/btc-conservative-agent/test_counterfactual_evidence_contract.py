import ast
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
from research import platform_relay_evidence as pure_relay
from research import counterfactual_normalization as pure_counterfactual
from research.analysis_eligibility import (
    BITFINEX_COPY_FIDELITY,
    REAL_COPY_PARAMETER_OPTIMISATION,
    classify_row,
)


ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")


def _load_evidence_functions():
    tree = ast.parse(SOURCE)
    names = {
        "_counterfactual_policy_comparability_key",
        "_counterfactual_post_exit_horizons",
        "_counterfactual_entry_horizons",
        "_counterfactual_bitfinex_evidence",
        "build_counterfactual_observability_fields",
        "_platform_relay_evidence_index",
        "_normalize_platform_bitfinex_evidence",
        "_snapshot_with_platform_relay_evidence",
    }
    selected = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {
        "copy": copy,
        "hashlib": hashlib,
        "json": json,
        "math": math,
        "os": os,
        "PLATFORM_RELAY_EVIDENCE_FILE": "relay_lifecycle_evidence_v1.json",
        "_buf_float": lambda value, default=0: float(value if value is not None else default),
        "_COUNTERFACTUAL_REQUIRED_POLICY_KEYS": (
            "policy_snapshot_schema",
            "policy_version",
            "hard_stop_margin_pct",
            "thesis_fast_exit_unreal_pct",
            "thesis_mfe_protect_pct",
            "trail_ladder",
            "exit_profile_id",
        ),
        "_COUNTERFACTUAL_REQUIRED_POST_EXIT_HORIZONS_SEC": {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "60m": 3600,
            "120m": 7200,
        },
        "_pure_platform_relay_evidence_index": pure_relay._platform_relay_evidence_index,
        "_pure_normalize_platform_bitfinex_evidence": pure_relay._normalize_platform_bitfinex_evidence,
        "_pure_snapshot_with_platform_relay_evidence": pure_relay._snapshot_with_platform_relay_evidence,
        "_pure_policy_comparability_key": pure_counterfactual.policy_comparability_key,
        "_pure_counterfactual_horizons": pure_counterfactual.horizons,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace


def test_platform_relay_evidence_join_is_provenanced_and_preserves_negative_events(tmp_path):
    funcs = _load_evidence_functions()
    artifact = tmp_path / "relay.json"
    artifact.write_text(json.dumps({
        "schema": "relay_lifecycle_evidence_v1",
        "generatedAt": "2026-08-16T00:00:00Z",
        "generatingRevision": "rev-1",
        "runIdentity": "run-1",
        "records": [{
            "canonicalTradeId": "cont-negative",
            "lifecycleId": "cycle-1",
            "participantId": "part-1",
            "events": [
                {"id": "e1", "eventType": "MIRROR_DIFF", "payload": {}, "createdAt": "2026-08-16T00:00:01Z"},
                {"id": "e2", "eventType": "STALE_NO_EXPOSURE", "payload": {}, "createdAt": "2026-08-16T00:00:02Z"},
            ],
        }],
    }), encoding="utf-8")
    index = funcs["_platform_relay_evidence_index"](artifact)
    assert index["cont-negative"]["generating_revision"] == "rev-1"
    funcs["_platform_relay_evidence_index"] = lambda: index
    source = {"trade_id": "cont-negative", "bitfinex_evidence": {}}
    joined = funcs["_snapshot_with_platform_relay_evidence"](source, "cont-negative")
    assert source == {"trade_id": "cont-negative", "bitfinex_evidence": {}}
    assert [event["event_type"] for event in joined["lifecycle_events"]] == [
        "MIRROR_DIFF", "STALE_NO_EXPOSURE"
    ]
    assert joined["platform_evidence_revision"]
    assert pure_relay._platform_relay_evidence_index(artifact) == index


def test_platform_relay_evidence_join_fails_closed_without_complete_provenance(tmp_path):
    funcs = _load_evidence_functions()
    artifact = tmp_path / "relay.json"
    artifact.write_text(json.dumps({
        "schema": "relay_lifecycle_evidence_v1", "records": []
    }), encoding="utf-8")
    assert funcs["_platform_relay_evidence_index"](artifact) == {}


def test_copy_order_without_showcase_is_explicit_negative_copy_evidence():
    records = [{
        "canonicalTradeId": "cont-orphan-pending",
        "participantId": "participant-1",
        "events": [{
            "id": "mirror-1",
            "eventType": "MIRROR_DIFF",
            "createdAt": "2026-08-16T00:00:01Z",
            "payload": {
                "trade_id": "cont-orphan-pending",
                "diff_type": "COPY_ORDER_NO_SHOWCASE",
                "copy_limit": 63066.52,
            },
        }],
    }]

    evidence = pure_relay._normalize_platform_bitfinex_evidence(
        records, "cont-orphan-pending"
    )
    assert evidence["negative_events"][0]["event"] == "COPY_ORDER_NO_SHOWCASE"
    assert "COPY_ORDER_NO_SHOWCASE" in evidence["analysis_exclusion_reasons"]

    assessment = classify_row({
        "trade_id": "cont-orphan-pending",
        "policy_snapshot_complete": True,
        "policy_version": "policy-v1",
        "replay_complete": True,
        "terminal_provenance": "SIGNAL_TTL_EXPIRED",
        "bitfinex_evidence": evidence,
    })
    assert "COPY_ORDER_NO_SHOWCASE" in assessment["exclusion_reasons"][BITFINEX_COPY_FIDELITY]
    assert assessment["eligible"][BITFINEX_COPY_FIDELITY] is False
    assert assessment["eligible"][REAL_COPY_PARAMETER_OPTIMISATION] is False


def test_pure_relay_module_import_has_no_runtime_side_effects(tmp_path):
    code = (
        "import pathlib,threading; before=set(pathlib.Path('.').iterdir()); "
        "import research.platform_relay_evidence as m; "
        "after=set(pathlib.Path('.').iterdir()); "
        "assert before == after; assert len(threading.enumerate()) == 1; "
        "assert not hasattr(m, 'app') and not hasattr(m, 'state')"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_bot_compatibility_validation_wrapper_matches_pure_contract():
    validate = _load_evidence_functions().get("_validate_platform_relay_evidence_payload")
    if validate is None:
        tree = ast.parse(SOURCE)
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_validate_platform_relay_evidence_payload")
        namespace = {"_pure_validate_platform_relay_evidence_payload": pure_relay._validate_platform_relay_evidence_payload}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
        validate = namespace["_validate_platform_relay_evidence_payload"]
    fixture = {"schema": "bad"}
    assert validate(fixture) == pure_relay._validate_platform_relay_evidence_payload(fixture)


def test_platform_events_normalize_explicit_exchange_lifecycle_without_invention():
    normalize = _load_evidence_functions()["_normalize_platform_bitfinex_evidence"]
    records = [{
        "canonicalTradeId": "cont-rich", "lifecycleId": "cycle-rich", "participantId": "part-rich",
        "events": [
            {"id": "order", "eventType": "ORDER_PLACED", "createdAt": "2026-08-16T00:00:01Z", "payload": {
                "bitfinexOrderId": 101, "clientOrderId": 501, "source_exact_qty_btc": 0.03,
                "venue_qty_btc": 0.02999, "entryExchangeAckAtMs": 1_000,
                "sourceEventId": "source-order-1", "sourceEventSeq": 7,
                "sourceEventAt": "2026-08-16T00:00:00Z",
                "platformReceivedAt": "2026-08-16T00:00:00.100Z",
                "entry_bbo": {"bid": 62999, "ask": 63001, "observedAtMs": 900,
                              "source": "BITFINEX_PUBLIC_TICKER"},
                "correlated_cluster_evidence": {"schema": "correlated_exposure_cluster_v2",
                                                "allowed": True, "same_direction_managed_or_reserved_count": 1},
            }},
            {"id": "fill", "eventType": "FILLED", "payload": {
                "bitfinex_order_id": 101, "fill_id": 201, "filled_qty": 0.02999,
                "fill_price": 63_000, "exchange_fill_received_at": "2026-08-16T00:00:02Z",
            }},
            {"id": "stop", "eventType": "STOP_LOSS_ARMED", "payload": {
                "stopOrderId": 301, "qty": 0.02999, "stop_price": 64_500,
                "stop_exchange_ack_at": "2026-08-16T00:00:03Z",
            }},
            {"id": "reprice", "eventType": "UPDATE_STOPS", "payload": {
                "event": "LIMIT_UPDATED", "bitfinex_order_id": 101, "newLimit": 63_050,
                "prior_limit": 63_000, "replacementMode": "BITFINEX_IN_PLACE_UPDATE",
                "limitChaseCount": 1, "exchange_ack_at": "2026-08-16T00:00:04Z",
            }},
            {"id": "exit", "eventType": "EXIT", "payload": {
                "bitfinex_order_id": 401, "fill_id": 402, "exit_price": 62_900,
                "exit_reason": "SOURCE_CONFIRMED", "actual_bitfinex_realized_pnl_usd": 2.25,
                "trading_fee_usd": 0.0,
                "close_bbo": {"bid": 62899, "ask": 62901, "observedAtMs": 4_000,
                              "source": "BITFINEX_PUBLIC_TICKER"},
                "terminal_authority_kind": "SIGNED_POSITION_CLOSED",
                "terminal_authority_evidence": {"trade_id": "cont-rich", "event_id": "close-1",
                    "event_seq": 8, "source_event_at_ms": 3_500, "platform_received_at_ms": 3_600,
                    "exit_price": 62_900, "exit_reason": "SOURCE_CONFIRMED"},
                "reconciliation": {"complete": True, "position_delta": 0, "order_delta": 0,
                                   "orphan_order_count": 0, "foreign_order_count": 0},
                "quantity_evidence_complete": True, "order_ack_history_complete": True,
                "stop_evidence_complete": True, "reconciliation_complete": True,
            }},
        ],
    }]
    evidence = normalize(records, "cont-rich")
    assert evidence["schema"] == "bitfinex_evidence_v1"
    assert evidence["participant_id"] == "part-rich"
    assert evidence["source_lifecycle_id"] == "cycle-rich"
    assert evidence["client_order_id"] == 501
    assert evidence["bitfinex_order_ids"] == [101, 301, 401]
    assert evidence["fill_ids"] == [201, 402]
    assert evidence["source_quantity"] == 0.03
    assert evidence["normalized_quantity"] == 0.02999
    assert evidence["filled_quantity"] == 0.02999
    assert evidence["protected_quantity"] == 0.02999
    assert evidence["fills"][0]["fill_id"] == 201
    assert evidence["reprices"][0]["price"] == 63050.0
    assert evidence["chase_history"][0]["prior_price"] == 63000.0
    assert evidence["chase_history"][0]["replacement_mode"] == "BITFINEX_IN_PLACE_UPDATE"
    assert evidence["source_identity"]["source_event_id"] == "source-order-1"
    assert evidence["source_identity"]["source_event_seq"] == 7
    assert evidence["cluster_evidence"]["allowed"] is True
    assert evidence["bbo_evidence"]["entry"]["ask"] == 63001
    assert evidence["bbo_evidence"]["exit"]["bid"] == 62899
    assert evidence["ack_history"][0]["ack_at"] == 1000
    assert evidence["stop_chain"][0]["order_id"] == 301
    assert evidence["exit_evidence"]["order_id"] == 401
    assert evidence["actual_bitfinex_realized_pnl_usd"] == 2.25
    assert evidence["terminal_authority"]["kind"] == "SIGNED_POSITION_CLOSED"
    assert evidence["cost_evidence"] == {"trading_fee_usd": 0.0}
    assert evidence["actual_costs"] == evidence["cost_evidence"]
    assert evidence["reconciliation_complete"] is True


def test_platform_event_normalizer_keeps_missing_ids_and_completeness_missing():
    normalize = _load_evidence_functions()["_normalize_platform_bitfinex_evidence"]
    evidence = normalize([{"participantId": "part-missing", "events": [
        {"id": "filled-no-id", "eventType": "FILLED", "createdAt": "2026-08-16T00:00:01Z",
         "payload": {"filled_qty": 0.01, "fill_price": 63_000,
                     "quantity_evidence_complete": True, "order_ack_history_complete": True}},
        {"id": "stop-no-id", "eventType": "STOP_LOSS_ARMED", "createdAt": "2026-08-16T00:00:02Z",
         "payload": {"qty": 0.01, "stop_price": 64_000, "stop_evidence_complete": True,
                     "reconciliation_complete": True}},
    ]}], "cont-missing")
    assert evidence["fill_ids"] == []
    assert evidence["bitfinex_order_ids"] == []
    assert evidence["ack_history"] == []
    assert evidence["stop_chain"] == []
    assert evidence["quantity_evidence_complete"] is False
    assert evidence["order_ack_history_complete"] is False
    assert evidence["stop_evidence_complete"] is False
    assert evidence["reconciliation_complete"] is False


def test_platform_event_normalizer_preserves_partial_fill_stop_chain_and_excludes_unsupported_exit():
    normalize = _load_evidence_functions()["_normalize_platform_bitfinex_evidence"]
    evidence = normalize([{"participantId": "part-partial", "events": [
        {"id": "order", "eventType": "ORDER_PLACED", "payload": {
            "bitfinexOrderId": 101, "clientOrderId": 501,
            "source_exact_qty_btc": 0.03, "venue_qty_btc": 0.03,
            "entryExchangeAckAtMs": 1_000,
        }},
        {"id": "f1", "eventType": "FILLED", "payload": {
            "bitfinexOrderId": 101, "fillId": 201, "partialFillQty": 0.01,
        }},
        {"id": "s1", "eventType": "UPDATE_STOPS", "payload": {
            "event": "PARTIAL_FILL_STOP_REPLACEMENT_ACKNOWLEDGED", "partialFillStopOrderId": 301,
            "stopClientOrderId": 601, "bitfinexOrderId": 101,
            "partialFillQty": 0.01, "remaining_qty": 0.02,
            "stop_exchange_ack_at": "2026-08-16T00:00:02Z",
        }},
        {"id": "f2", "eventType": "FILLED", "payload": {
            "bitfinexOrderId": 101, "exchange_fill_ids": [202, 203], "qty": 0.018,
            "fill_detection_path": "POSITION_DELTA", "fill_detection_context": "SIGNAL_TTL_EXPIRED",
            "entry_completion": "PARTIAL_FILL_TTL_EXPIRED", "unfilled_qty_cancelled": 0.012,
        }},
        {"id": "s2", "eventType": "UPDATE_STOPS", "payload": {
            "event": "PARTIAL_FILL_STOP_REPLACEMENT_ACKNOWLEDGED", "partialFillStopOrderId": 302,
            "stopClientOrderId": 602, "partialFillQty": 0.018, "supersededPartialStopOrderId": 301,
            "stop_exchange_ack_at": "2026-08-16T00:00:03Z",
        }},
        {"id": "ttl", "eventType": "UPDATE_STOPS", "payload": {
            "event": "PARTIAL_FILL_TTL_EXPIRED", "stopOrderId": 302,
            "protected_qty": 0.018, "final_filled_qty": 0.018,
            "unfilled_qty_cancelled": 0.012, "remaining_qty": 0,
            "remaining_entry_order_live": False,
        }},
        {"id": "x", "eventType": "EXIT", "payload": {
            "exit_reason": "PROTECTION_FAILURE_EMERGENCY_CLOSE", "exit_price": 63_100,
        }},
    ]}], "cont-partial")
    assert evidence["filled_quantity"] == 0.018
    assert evidence["protected_quantity"] == 0.018
    assert evidence["fill_ids"] == [201, 202, 203]
    assert evidence["client_order_ids"] == [501, 601, 602]
    assert evidence["bitfinex_order_ids"] == [101, 301, 302]
    assert [row["order_id"] for row in evidence["stop_chain"]] == [301, 302]
    assert evidence["stop_chain"][1]["predecessor_order_id"] == 301
    assert evidence["stop_chain"][0]["client_order_id"] == 601
    assert evidence["remaining_quantity"] == 0.0
    assert evidence["cancelled_quantity"] == 0.012
    assert evidence["fills"][1]["fill_ids"] == [202, 203]
    assert evidence["fills"][1]["detection_context"] == "SIGNAL_TTL_EXPIRED"
    assert evidence["fills"][1]["entry_completion"] == "PARTIAL_FILL_TTL_EXPIRED"
    assert any(row["order_id"] == 301 for row in evidence["ack_history"])
    assert "PROTECTION_FAILURE_EMERGENCY_CLOSE" in evidence["analysis_exclusion_reasons"]
    assert "TERMINAL_PROVENANCE_EXCLUDED" in evidence["analysis_exclusion_reasons"]
    assert evidence["reconciliation_complete"] is False


def test_platform_event_normalizer_keeps_concurrent_participants_separate():
    normalize = _load_evidence_functions()["_normalize_platform_bitfinex_evidence"]
    records = [
        {"participantId": "part-a", "lifecycleId": "cycle-a", "events": [{
            "id": "a", "eventType": "ORDER_PLACED",
            "payload": {"bitfinexOrderId": 101, "clientOrderId": 501, "entryExchangeAckAtMs": 1000},
        }]},
        {"participantId": "part-b", "lifecycleId": "cycle-b", "events": [{
            "id": "b", "eventType": "ORDER_PLACED",
            "payload": {"bitfinexOrderId": 102, "clientOrderId": 502, "entryExchangeAckAtMs": 1001},
        }]},
    ]
    evidence = normalize(records, "cont-shared")
    assert evidence["participant_id"] is None
    assert set(evidence["participants"]) == {"part-a", "part-b"}
    assert evidence["participants"]["part-a"]["client_order_id"] == 501
    assert evidence["participants"]["part-b"]["client_order_id"] == 502
    assert evidence["bitfinex_order_ids"] == [101, 102]
    assert evidence["quantity_evidence_complete"] is False


def _complete_fixture():
    policy = {
        "policy_snapshot_schema": "exit_policy_v1",
        "policy_version": "revision-1",
        "hard_stop_margin_pct": -13.0,
        "thesis_fast_exit_unreal_pct": -12.0,
        "thesis_mfe_protect_pct": 2.0,
        "trail_ladder": [[4, 2], [5, 3]],
        "exit_profile_id": "scenario-c",
    }
    evidence = {
        "participant_id": "participant-1",
        "source_lifecycle_id": "source-life-1",
        "source_identity": {"source_event_id": "source-1", "source_event_seq": 4},
        "client_order_id": "cid-1",
        "bitfinex_order_ids": ["order-1", "stop-1", "exit-1"],
        "fill_ids": ["fill-1", "exit-fill-1"],
        "source_quantity": 0.031729,
        "normalized_quantity": 0.03172,
        "filled_quantity": 0.03172,
        "protected_quantity": 0.03172,
        "remaining_quantity": 0.0,
        "fills": [{"fill_id": "fill-1", "quantity": 0.03172}],
        "reprices": [{"order_id": "order-1", "price": 63064.88}],
        "chase_history": [{"order_id": "order-1", "price": 63064.88, "chase_count": 1}],
        "cluster_evidence": {"allowed": True, "same_direction_managed_or_reserved_count": 1},
        "ack_history": [{"order_id": "order-1", "ack_at": "2026-08-16T00:00:01Z"}],
        "stop_chain": [{"order_id": "stop-1", "protected_quantity": 0.03172, "ack_at": "2026-08-16T00:00:02Z"}],
        "exit_evidence": {"order_id": "exit-1", "fill_id": "exit-fill-1"},
        "terminal_authority": {"kind": "SIGNED_POSITION_CLOSED"},
        "bbo_evidence": {"entry": {"bid": 1, "ask": 2}, "exit": {"bid": 2, "ask": 3}},
        "reconciliation": {
            "complete": True,
            "position_delta": 0,
            "order_delta": 0,
            "orphan_order_count": 0,
            "foreign_order_count": 0,
        },
        "source_snapshot_evidence": {
            "sequence": 42,
            "captured_at": "2026-08-16T00:00:00Z",
            "fresh": True,
            "complete": True,
        },
        "quantity_evidence_complete": True,
        "order_ack_history_complete": True,
        "stop_evidence_complete": True,
        "source_snapshot_evidence_complete": True,
        "reconciliation_complete": True,
        "cost_evidence_complete": True,
        "cost_evidence": {"trading_fee_usd": 0.0, "funding_fee_usd": 0.0,
                          "spread_cost_usd": 0.0, "slippage_usd": 0.0},
    }
    exit_t = 100.0
    ticks = [
        {"phase": "post_exit", "t": exit_t + seconds, "price": 63000 + seconds,
         "best_bid": 62999 + seconds, "best_ask": 63001 + seconds, "unreal_pct": 1.0}
        for seconds in (60, 300, 900, 1800, 3600, 7200)
    ]
    return (
        {"exit_config": policy, "leverage": 10},
        {
            "trade_id": "cont-qualified",
            "executed": True,
            "fee_model": "bitfinex-fees-v1",
            "execution_profile": "bitfinex-live-limit-v1",
            "terminal_provenance": "SOURCE_CONFIRMED",
            "actual_bitfinex_realized_pnl_usd": 1.25,
            "bitfinex_evidence": evidence,
        },
        {
            "trade_id": "cont-qualified",
            "replay_complete": True,
            "post_exit_complete": True,
            "exit_t_rel": exit_t,
            "virtual_fill_t": 0.0,
            "direction": "LONG",
            "ticks": ticks,
        },
        {},
    )


def test_complete_exchange_evidence_qualifies_all_three_cohorts():
    namespace = _load_evidence_functions()
    fields = namespace["build_counterfactual_observability_fields"](*_complete_fixture())
    assert fields["evidence_schema"] == "counterfactual_evidence_v1"
    assert fields["policy_comparability_key"].startswith("policy_comparability_v1:")
    assert fields["required_post_exit_horizons_complete"] is True
    assert fields["required_entry_horizons_complete"] is True
    assert fields["entry_horizons"]["required"]["120m"]["observed"] is True
    assert fields["bitfinex_evidence"]["linkage_complete"] is True
    assert fields["bitfinex_evidence"]["source_identity"]["source_event_seq"] == 4
    assert fields["bitfinex_evidence"]["chase_history"][0]["chase_count"] == 1
    assert fields["bitfinex_evidence"]["cluster_evidence"]["allowed"] is True
    assert fields["bitfinex_evidence"]["terminal_authority"]["kind"] == "SIGNED_POSITION_CLOSED"
    assert fields["bitfinex_evidence"]["actual_costs"] == fields["bitfinex_evidence"]["cost_evidence"]
    assert all(fields["analysis_cohorts"]["eligible"].values())
    assert fields["analysis_eligible"] is True


def test_missing_exchange_evidence_and_horizons_fail_closed_without_invention():
    namespace = _load_evidence_functions()
    buf, snapshot, replay, outcome = _complete_fixture()
    snapshot.pop("bitfinex_evidence")
    replay["post_exit_complete"] = False
    replay["ticks"] = replay["ticks"][:2]
    fields = namespace["build_counterfactual_observability_fields"](
        buf, snapshot, replay, outcome
    )
    assert fields["bitfinex_evidence"]["linkage_complete"] is False
    assert fields["required_post_exit_horizons_complete"] is False
    assert fields["required_entry_horizons_complete"] is False
    assert fields["post_exit_horizons"]["required"]["120m"]["observed"] is False
    assert fields["analysis_eligible"] is False
    assert "BITFINEX_LINKAGE_MISSING" in fields["analysis_exclusion_reasons"]
    assert "REQUIRED_POST_EXIT_HORIZON_INCOMPLETE" in fields["analysis_exclusion_reasons"]


def test_policy_key_changes_with_execution_cost_or_ladder():
    namespace = _load_evidence_functions()
    buf, snapshot, _replay, _outcome = _complete_fixture()
    key_one = namespace["_counterfactual_policy_comparability_key"](
        buf["exit_config"], buf, snapshot
    )
    changed = copy.deepcopy(snapshot)
    changed["fee_model"] = "bitfinex-fees-v2"
    key_two = namespace["_counterfactual_policy_comparability_key"](
        buf["exit_config"], buf, changed
    )
    assert key_one != key_two
    changed_profile = copy.deepcopy(snapshot)
    changed_profile["execution_profile"] = "bitfinex-live-cancel-recreate-v2"
    key_three = namespace["_counterfactual_policy_comparability_key"](
        buf["exit_config"], buf, changed_profile
    )
    assert key_one != key_three


def test_semantic_profiles_are_stable_and_sensitive_to_execution_facts():
    one = pure_counterfactual.canonical_profile(
        "execution_cost_profile_v1", venue="bitfinex", maker_fee_rate=0.0,
        taker_fee_rate=0.0, funding_simulation_enabled=True,
    )
    reordered = pure_counterfactual.canonical_profile(
        "execution_cost_profile_v1", funding_simulation_enabled=True,
        taker_fee_rate=0.0, maker_fee_rate=0.0, venue="bitfinex",
    )
    changed = pure_counterfactual.canonical_profile(
        "execution_cost_profile_v1", venue="bitfinex", maker_fee_rate=0.0,
        taker_fee_rate=0.0006, funding_simulation_enabled=True,
    )
    assert one == reordered
    assert one != changed
    assert max(pure_counterfactual.REQUIRED_HORIZONS_SEC.values()) == 7200


def test_execution_timing_projects_only_explicit_stages_and_reports_sla():
    record = {"participantId": "p1", "events": [{
        "id": "timing-1", "eventType": "EXECUTION_TIMING", "createdAt": "2026-08-16T00:00:09Z",
        "payload": {"schema": "relay_execution_timing_v1", "operation": "ORDER_PLACED",
                    "bitfinex_order_id": "42", "stages": {
                        "queueEnteredAtMs": 1000, "executorStartedAtMs": 1100,
                        "databasePreflightStartedAtMs": 1200, "databasePreflightCompletedAtMs": 1300,
                        "bitfinexRequestStartedAtMs": 1400, "exchangeAckAtMs": 4201,
                        "persistenceStartedAtMs": 4300, "persistenceCompletedAtMs": 4400,
                    }}
    }]}
    evidence = pure_relay._normalize_platform_bitfinex_evidence([record], "cont-timing")
    timing = evidence["execution_timing"][0]
    assert timing["complete"] is True
    assert timing["queue_to_exchange_ack_ms"] == 3201
    assert timing["sla_3s_verdict"] == "MISS"


def test_execution_timing_missing_ack_is_unknown_not_event_timestamp():
    record = {"participantId": "p1", "events": [{
        "id": "timing-2", "eventType": "EXECUTION_TIMING", "createdAt": "2026-08-16T00:00:09Z",
        "payload": {"schema": "relay_execution_timing_v1", "operation": "LIMIT_UPDATED",
                    "stages": {"queueEnteredAtMs": 1000}}
    }]}
    evidence = pure_relay._normalize_platform_bitfinex_evidence([record], "cont-unknown")
    timing = evidence["execution_timing"][0]
    assert timing["complete"] is False
    assert "exchangeAckAtMs" in timing["missing_stages"]
    assert timing["queue_to_exchange_ack_ms"] is None
    assert timing["sla_3s_verdict"] == "UNKNOWN"


def test_fill_stop_close_timing_preserves_explicit_stages_and_missing_truth():
    events = [
        {"id": "fill", "eventType": "FILLED", "createdAt": "2026-08-16T00:00:50Z",
         "payload": {"exchange_fill_last_at": "2026-08-16T00:00:01Z",
                     "exchange_fill_received_at": "2026-08-16T00:00:02Z",
                     "fill_detected_at": "2026-08-16T00:00:03Z",
                     "stop_submit_started_at": "2026-08-16T00:00:04Z",
                     "stop_exchange_ack_at": "2026-08-16T00:00:05Z"}},
        {"id": "stop", "eventType": "STOP_LOSS_ARMED", "createdAt": "2026-08-16T00:00:51Z",
         "payload": {"stopOrderId": "stop-1", "stop_submit_started_at": "2026-08-16T00:00:04Z",
                     "stop_exchange_ack_at": "2026-08-16T00:00:05Z"}},
        {"id": "exit", "eventType": "EXIT", "createdAt": "2026-08-16T00:01:50Z",
         "payload": {"close_preflight_started_at": "2026-08-16T00:01:01Z",
                     "close_submit_started_at": "2026-08-16T00:01:02Z",
                     "close_exchange_ack_at": "2026-08-16T00:01:03Z",
                     "close_confirmed_at": "2026-08-16T00:01:04Z"}},
        {"id": "exit-receipt", "eventType": "EXECUTION_TIMING", "createdAt": "2026-08-16T00:01:51Z",
         "payload": {"schema": "relay_execution_persistence_receipt_v1",
                     "operation": "EXIT_PERSISTED",
                     "stages": {"persistenceCompletedAtMs": 1786838465000}}},
    ]
    evidence = pure_relay._normalize_platform_bitfinex_evidence(
        [{"participantId": "p1", "events": events}], "cont-lifecycle"
    )
    rows = {row["operation"]: row for row in evidence["execution_timing"]}
    assert (rows["FILL_PROTECTED"]["stages"]["exchangeFillReceivedAtMs"]
            - rows["FILL_PROTECTED"]["stages"]["exchangeFillOccurredAtMs"]) == 1000
    assert "fillPersistenceCompletedAtMs" in rows["FILL_PROTECTED"]["missing_stages"]
    assert "fillDetectedAtMs" in rows["STOP_PROTECTION"]["missing_stages"]
    assert (rows["TERMINAL_CLOSE"]["stages"]["closeConfirmedAtMs"]
            - rows["TERMINAL_CLOSE"]["stages"]["closeExchangeAckAtMs"]) == 1000
    assert rows["TERMINAL_CLOSE"]["stages"]["closePersistenceCompletedAtMs"] == 1786838465000
    assert "closePersistenceCompletedAtMs" not in rows["TERMINAL_CLOSE"]["missing_stages"]
    assert all(row["complete"] is False for row in rows.values())


def test_persistence_receipts_are_post_write_and_fail_observability_only():
    service = (ROOT.parent.parent / "apps" / "api" / "src" / "trading-agents" /
               "signal-subscriber-execution.service.ts").read_text(encoding="utf-8")
    for lifecycle_type, receipt in (
        ("'FILLED'", "operation: 'FILLED_PERSISTED'"),
        ("'STOP_LOSS_ARMED'", "operation: 'STOP_LOSS_ARMED_PERSISTED'"),
        ("'EXIT'", "operation: 'EXIT_PERSISTED'"),
    ):
        receipt_at = service.index(receipt)
        assert service.rfind(lifecycle_type, 0, receipt_at) >= 0
        receipt_tail = service[receipt_at:receipt_at + 900]
        assert ".catch((err) => this.logger.warn" in receipt_tail


def test_deployed_event_field_names_qualify_quantity_ack_and_stop_without_claim_flags():
    events = [
        {"id": "order", "eventType": "ORDER_PLACED", "createdAt": "2026-08-16T00:00:00Z",
         "payload": {"bitfinexOrderId": 101, "source_exact_qty_btc": 0.031696,
                     "venue_qty_btc": 0.03169, "entryExchangeAckAtMs": 1000}},
        {"id": "reprice", "eventType": "UPDATE_STOPS", "createdAt": "2026-08-16T00:00:01Z",
         "payload": {"bitfinexOrderId": 101, "new_limit": 63000,
                     "replacementExchangeAckAtMs": 2000}},
        {"id": "fill", "eventType": "FILLED", "createdAt": "2026-08-16T00:00:02Z",
         "payload": {"bitfinexOrderId": 101, "qty": 0.03169}},
        {"id": "stop", "eventType": "STOP_LOSS_ARMED", "createdAt": "2026-08-16T00:00:03Z",
         "payload": {"stopOrderId": 202, "qty": 0.03169,
                     "stop_price": 63800, "stop_exchange_ack_at": "2026-08-16T00:00:03Z"}},
    ]
    evidence = pure_relay._normalize_platform_bitfinex_evidence(
        [{"participantId": "participant-1", "events": events}], "cont-producer"
    )
    assert evidence["source_quantity"] == 0.031696
    assert evidence["normalized_quantity"] == 0.03169
    assert evidence["filled_quantity"] == 0.03169
    assert evidence["protected_quantity"] == 0.03169
    assert evidence["quantity_evidence_complete"] is True
    assert evidence["order_ack_history_complete"] is True
    assert evidence["stop_evidence_complete"] is True
    assert len(evidence["ack_history"]) == 2
    assert evidence["source_snapshot_evidence_complete"] is False
    assert evidence["reconciliation_complete"] is False


def test_reprice_without_explicit_exchange_ack_keeps_ack_history_incomplete():
    events = [
        {"id": "order", "eventType": "ORDER_PLACED", "createdAt": "2026-08-16T00:00:00Z",
         "payload": {"bitfinexOrderId": 101, "entryExchangeAckAtMs": 1000}},
        {"id": "reprice", "eventType": "UPDATE_STOPS", "createdAt": "2026-08-16T00:00:01Z",
         "payload": {"bitfinexOrderId": 101, "new_limit": 63000}},
    ]
    evidence = pure_relay._normalize_platform_bitfinex_evidence(
        [{"participantId": "participant-1", "events": events}], "cont-missing-reprice-ack"
    )
    assert evidence["reprices"][0]["ack_at"] is None
    assert evidence["order_ack_history_complete"] is False


def test_platform_profile_fields_are_carried_without_default_invention():
    event = {"id": "order", "eventType": "ORDER_PLACED", "createdAt": "2026-08-16T00:00:00Z",
             "payload": {"fee_model": "cost-v1", "execution_profile": "limit-v1"}}
    evidence = pure_relay._normalize_platform_bitfinex_evidence(
        [{"participantId": "p1", "events": [event]}], "cont-profile"
    )
    assert evidence["fee_model"] == "cost-v1"
    assert evidence["execution_profile"] == "limit-v1"
    missing = pure_relay._normalize_platform_bitfinex_evidence(
        [{"participantId": "p1", "events": [{**event, "payload": {}}]}], "cont-missing"
    )
    assert missing["fee_model"] is None
    assert missing["execution_profile"] is None


def test_canonical_terminal_snapshot_is_projected_and_incomplete_signed_proof_is_not_upgraded():
    snapshot = {
        "source_git_rev": "6f279cc7", "sequence": 91,
        "captured_at": "2026-08-16T00:01:00Z", "snapshot_age_sec": 1.2,
        "positions_synced": True, "orders_synced": True, "trades_synced": True,
    }
    canonical = pure_relay._normalize_platform_bitfinex_evidence(
        [{"participantId": "p1", "events": [{
            "id": "exit-canonical", "eventType": "EXIT",
            "payload": {"terminal_authority_kind": "CANONICAL_TERMINAL_RECORD",
                        "terminal_authority_evidence": {
                "trade_id": "cont-canonical", "source_snapshot_evidence": snapshot,
            }},
        }]}],
        "cont-canonical",
    )
    assert canonical["source_snapshot_evidence"]["authority_kind"] == "CANONICAL_TERMINAL_RECORD"
    assert canonical["source_snapshot_evidence"]["source_snapshot_evidence"] == snapshot
    assert canonical["source_snapshot_evidence_complete"] is True

    signed = pure_relay._normalize_platform_bitfinex_evidence(
        [{"participantId": "p1", "events": [{
            "id": "exit-signed", "eventType": "EXIT",
            "payload": {"terminal_authority_kind": "SIGNED_POSITION_CLOSED",
                        "terminal_authority_evidence": {
                "trade_id": "cont-signed", "event_id": "close-7", "event_seq": 7,
            }},
        }]}],
        "cont-signed",
    )
    assert signed["source_snapshot_evidence"]["event_id"] == "close-7"
    assert signed["source_snapshot_evidence_complete"] is False


def test_signed_terminal_and_final_reconciliation_complete_future_copy_evidence():
    event = {"id": "exit", "eventType": "EXIT", "createdAt": "2026-08-16T00:01:00Z",
             "payload": {
                 "exit_reason": "SHOWCASE_MIRROR",
                 "terminal_authority_kind": "SIGNED_POSITION_CLOSED",
                 "terminal_authority_evidence": {
                     "trade_id": "cont-clean", "event_id": "close-7", "event_seq": 7,
                     "source_event_at_ms": 1000, "platform_received_at_ms": 1100,
                     "exit_price": 63000, "exit_reason": "PROFIT_LOCK",
                 },
                 "trading_fee_usd": 0.0, "funding_fee_usd": 0.02,
                 "spread_cost_usd": 0.11, "slippage_usd": 0.03,
                 "copy_exit_slippage_usd": 0.07,
                 "final_reconciliation": {
                     "schema": "relay_final_reconciliation_v1", "complete": True,
                     "position_reconciled": True, "exchange_vs_ledger_delta_sats": 0,
                     "order_delta": 0, "orphan_order_count": 0, "foreign_order_count": 0,
                 },
             }}
    evidence = pure_relay._normalize_platform_bitfinex_evidence(
        [{"participantId": "p1", "events": [event]}], "cont-clean"
    )
    assert evidence["source_snapshot_evidence_complete"] is True
    assert evidence["source_snapshot_evidence"]["event_id"] == "close-7"
    assert evidence["reconciliation_complete"] is True
    assert evidence["cost_evidence_complete"] is True
    assert evidence["cost_evidence"]["spread_cost_usd"] == 0.11
    assert evidence["cost_evidence"]["slippage_usd"] == 0.03
    assert evidence["cost_evidence"]["copy_exit_slippage_usd"] == 0.07

    evidence.update({
        "client_order_id": 501,
        "client_order_ids": [501, 601],
        "bitfinex_order_ids": [101, 301],
        "fill_ids": [201],
        "source_quantity": 0.03,
        "normalized_quantity": 0.03,
        "filled_quantity": 0.018,
        "protected_quantity": 0.018,
        "remaining_quantity": 0.0,
        "cancelled_quantity": 0.012,
        "ack_history": [{"order_id": 101, "ack_at": 1000}],
        "stop_chain": [{"order_id": 301, "protected_quantity": 0.018, "ack_at": 1200}],
        "quantity_evidence_complete": True,
        "order_ack_history_complete": True,
        "stop_evidence_complete": True,
    })
    normalized = _load_evidence_functions()["_counterfactual_bitfinex_evidence"](
        {}, {"bitfinex_evidence": evidence}, {}, {}
    )
    assert normalized["source_snapshot_evidence_complete"] is True
    assert normalized["reconciliation_complete"] is True
    assert normalized["linkage_complete"] is True
    assert normalized["client_order_ids"] == [501, 601]
    assert normalized["quantities"]["cancelled_quantity"] == 0.012


def test_incomplete_account_wide_reconciliation_never_qualifies():
    event = {"id": "exit", "eventType": "EXIT", "createdAt": "2026-08-16T00:01:00Z",
             "payload": {"final_reconciliation": {
                 "schema": "relay_final_reconciliation_v1", "complete": False,
                 "position_reconciled": True, "exchange_vs_ledger_delta_sats": 0,
                 "order_delta": 0, "orphan_order_count": None, "foreign_order_count": None,
             }}}
    evidence = pure_relay._normalize_platform_bitfinex_evidence(
        [{"participantId": "p1", "events": [event]}], "cont-incomplete"
    )
    assert evidence["reconciliation_complete"] is False

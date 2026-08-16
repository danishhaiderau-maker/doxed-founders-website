import ast
import copy
import hashlib
import json
import math
import os
from pathlib import Path


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


def test_platform_relay_evidence_join_fails_closed_without_complete_provenance(tmp_path):
    funcs = _load_evidence_functions()
    artifact = tmp_path / "relay.json"
    artifact.write_text(json.dumps({
        "schema": "relay_lifecycle_evidence_v1", "records": []
    }), encoding="utf-8")
    assert funcs["_platform_relay_evidence_index"](artifact) == {}


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
        "ack_history": [{"order_id": "order-1", "ack_at": "2026-08-16T00:00:01Z"}],
        "stop_chain": [{"order_id": "stop-1", "protected_quantity": 0.03172, "ack_at": "2026-08-16T00:00:02Z"}],
        "exit_evidence": {"order_id": "exit-1", "fill_id": "exit-fill-1"},
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

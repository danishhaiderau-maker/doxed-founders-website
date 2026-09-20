from __future__ import annotations

import copy
import ast
import json
import math
from pathlib import Path

import pytest

from combo_pathway_config import (
    SCORE_LED_ADMISSION_POLICY_ID,
    resolve_score_led_paper_admission,
)
from research_v3_bridge import (
    V3EvidenceStore,
    _paper_policy_identity,
    dual_write_lane_decision,
)


BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _load_bot_function(name, namespace):
    node = next(
        item for item in BOT_TREE.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), namespace)
    return namespace[name]


def _resolve(ai, **overrides):
    scope = {
        "score_led_enabled": True,
        "research_mode": True,
        "forced_paper": True,
        "live_armed": False,
        "bitfinex_live_enabled": False,
    }
    scope.update(overrides)
    return resolve_score_led_paper_admission(ai, **scope)


@pytest.mark.parametrize(
    ("long_score", "short_score", "direction"),
    [(42, 38, "LONG"), (38, 42, "SHORT"), (51, 49, "LONG"), (50, 49, "LONG")],
)
def test_valid_non_tie_chooses_stronger_side_despite_raw_abstention(
    long_score, short_score, direction,
):
    ai = {
        "raw_direction": "NO_TRADE",
        "direction": "NO_TRADE",
        "raw_decision": "REJECT",
        "decision": "REJECT",
        "long_score": long_score,
        "short_score": short_score,
    }
    original = copy.deepcopy(ai)
    result = _resolve(ai)
    assert result == {
        "schema": "score_led_paper_admission_v2",
        "policy_id": SCORE_LED_ADMISSION_POLICY_ID,
        "applied": True,
        "accepted": True,
        "effective_direction": direction,
        "reason": "SCORE_LED_VALID_NON_TIE",
        "long_score": float(long_score),
        "short_score": float(short_score),
        "score_gap": float(abs(long_score - short_score)),
    }
    assert ai == original


@pytest.mark.parametrize("scores", [(50, 50), (0, 0), (100, 100)])
def test_true_ties_reject(scores):
    result = _resolve({"long_score": scores[0], "short_score": scores[1]})
    assert result["applied"] is True
    assert result["accepted"] is False
    assert result["effective_direction"] is None
    assert result["reason"] == "SCORE_LED_TRUE_TIE"


@pytest.mark.parametrize(
    "ai",
    [
        {},
        {"long_score": 51},
        {"long_score": "51", "short_score": 49},
        {"long_score": math.nan, "short_score": 49},
        {"long_score": math.inf, "short_score": 49},
        {"long_score": 10**400, "short_score": 49},
        {"long_score": -1, "short_score": 49},
        {"long_score": 101, "short_score": 49},
        {"long_score": True, "short_score": 49},
        {"long_score": 51, "short_score": 49, "ai_error": True},
        {
            "long_score": 51,
            "short_score": 49,
            "factors": {"score_parse_error": "INVALID_LONG_SCORE"},
        },
    ],
)
def test_missing_malformed_nonfinite_or_out_of_range_scores_reject(ai):
    result = _resolve(ai)
    assert result["applied"] is True
    assert result["accepted"] is False
    assert result["reason"] in {
        "SCORE_LED_INVALID_OR_MISSING_SCORES",
        "SCORE_LED_AI_OR_SCORE_PARSE_ERROR",
    }


@pytest.mark.parametrize(
    "override",
    [
        {"score_led_enabled": False},
        {"research_mode": False},
        {"forced_paper": False},
        {"live_armed": True},
        {"bitfinex_live_enabled": True},
    ],
)
def test_inactive_or_nonpaper_live_scope_does_not_apply(override):
    result = _resolve({"long_score": 42, "short_score": 38}, **override)
    assert result["applied"] is False
    assert result["accepted"] is False
    assert result["effective_direction"] is None
    assert result["reason"] == "SCORE_LED_TREATMENT_INACTIVE"


def test_family_fanout_preserves_raw_ai_and_records_effective_admission():
    raw_ai = {
        "shared_ai_call_id": "scan-score-led",
        "raw_direction": "NO_TRADE",
        "direction": "NO_TRADE",
        "candidate_direction": "NO_TRADE",
        "raw_decision": "REJECT",
        "decision": "REJECT",
        "approved": False,
        "long_score": 42,
        "short_score": 38,
    }
    original = copy.deepcopy(raw_ai)
    writes = []
    stamps = []
    enqueued = []
    state = {
        "strategy_mode": "RESEARCH",
        "live_armed": False,
        "bitfinex_live_enabled": False,
        "invert_signal": False,
    }
    effective_namespace = {
        "state": state,
        "state_lock": _Lock(),
        "resolve_score_led_paper_admission": resolve_score_led_paper_admission,
        "SCORE_LED_PAPER_RESEARCH_ENABLED": True,
        "SCORE_LED_ADMISSION_POLICY_ID": SCORE_LED_ADMISSION_POLICY_ID,
        "is_research_data_collection": lambda: True,
        "_force_paper_mode_active": lambda: True,
        "copy": copy,
    }
    effective = _load_bot_function(
        "_effective_score_led_family_ai", effective_namespace,
    )
    namespace = {
        "_effective_score_led_family_ai": effective,
        "is_ai_scan_lane": lambda _lane: True,
        "is_research_data_collection": lambda: True,
        "state": state,
        "compute_directional_spread": (
            lambda direction, _ai: 4 if direction == "LONG" else -4
        ),
        "_enrich_combo_lane_features": lambda features, _ctx: dict(features),
        "COMBO_EXECUTION_LANES": ("FAMILY_ONE",),
        "is_independent_ai_lane": lambda _lane: False,
        "is_shared_ai_direction_lane": lambda _lane: True,
        "is_patient_chase_lane": lambda _lane: True,
        "is_deterministic_bracket_lane": lambda _lane: False,
        "combo_lane_match_detail": (
            lambda _lane, _ai, direction, spread, **_kwargs:
            {"passes": direction == "LONG" and spread == 4}
        ),
        "is_research_lane_enabled": lambda _lane: True,
        "_stamp_shared_ai_lane_verdict": (
            lambda *args, **kwargs: stamps.append((args, copy.deepcopy(kwargs)))
        ),
        "_shared_ai_call_id": lambda ai_result=None, ctx=None: "scan-score-led",
        "_v3_lane_policy_material": lambda _lane: {"policy_signature": "policy-v2"},
        "_write_v3_shared_lane_decision": (
            lambda *args, **kwargs: writes.append((copy.deepcopy(args), copy.deepcopy(kwargs))) or True
        ),
        "_enqueue_combo_lane_execution": (
            lambda *args, **kwargs: enqueued.append(copy.deepcopy(args))
        ),
        "COMBO_LANE_SPECS": {"FAMILY_ONE": {"combo_key": "ONE"}},
        "log_lane_opportunity_event": lambda *_args, **_kwargs: None,
        "logger": type("Logger", (), {"error": lambda *_args: None, "info": lambda *_args: None})(),
    }
    fanout = _load_bot_function("spawn_combo_lanes_from_ai_scan", namespace)
    fanout({"trade_id": "scan-score-led"}, raw_ai, 2.0, {}, "AI_SCAN")

    assert raw_ai == original
    written_ai = writes[0][0][1]
    assert written_ai["raw_direction"] == "NO_TRADE"
    assert written_ai["raw_decision"] == "REJECT"
    assert written_ai["direction"] == "LONG"
    assert written_ai["decision"] == "APPROVE"
    assert written_ai["effective_research_direction"] == "LONG"
    assert written_ai["effective_research_admission"]["accepted"] is True
    assert written_ai["effective_research_admission_policy_id"] == SCORE_LED_ADMISSION_POLICY_ID
    assert writes[0][1] == {
        "policy_decision": "ACCEPT",
        "execution_disposition": "ORDER_ELIGIBLE",
        "exact_reason": "SCORE_LED_VALID_NON_TIE",
    }
    assert stamps[0][1]["effective_direction"] == "LONG"
    assert stamps[0][1]["admission_policy_id"] == SCORE_LED_ADMISSION_POLICY_ID
    assert enqueued[0][1]["direction"] == "LONG"
    assert enqueued[0][1]["raw_direction"] == "NO_TRADE"


@pytest.mark.parametrize(
    "fields",
    [
        {"long_score": 50, "short_score": 50},
        {"long_score": 51},
        {"long_score": math.nan, "short_score": 49},
        {"long_score": 51, "short_score": 49, "ai_error": True},
    ],
)
def test_applied_rejection_overrides_raw_approve_only_on_effective_copy(fields):
    raw_ai = {
        "raw_direction": "LONG",
        "direction": "LONG",
        "raw_decision": "APPROVE",
        "decision": "APPROVE",
        "approved": True,
        **fields,
    }
    original = copy.deepcopy(raw_ai)
    state = {
        "strategy_mode": "RESEARCH",
        "live_armed": False,
        "bitfinex_live_enabled": False,
    }
    effective = _load_bot_function(
        "_effective_score_led_family_ai",
        {
            "state": state,
            "state_lock": _Lock(),
            "resolve_score_led_paper_admission": resolve_score_led_paper_admission,
            "SCORE_LED_PAPER_RESEARCH_ENABLED": True,
            "SCORE_LED_ADMISSION_POLICY_ID": SCORE_LED_ADMISSION_POLICY_ID,
            "is_research_data_collection": lambda: True,
            "_force_paper_mode_active": lambda: True,
            "copy": copy,
        },
    )
    lane_ai, admission = effective(raw_ai)
    assert raw_ai == original
    assert admission["applied"] is True
    assert admission["accepted"] is False
    assert lane_ai is not raw_ai
    assert lane_ai["raw_direction"] == "LONG"
    assert lane_ai["raw_decision"] == "APPROVE"
    assert lane_ai["direction"] == "NO_TRADE"
    assert lane_ai["candidate_direction"] == "NO_TRADE"
    assert lane_ai["decision"] == "REJECT"
    assert lane_ai["approved"] is False
    assert lane_ai["execution_tier"] == "REJECT"


@pytest.mark.parametrize(
    "fields",
    [
        {"long_score": 50, "short_score": 50},
        {"long_score": 51},
        {"long_score": math.nan, "short_score": 49},
        {"long_score": math.inf, "short_score": 49},
        {"long_score": "51", "short_score": 49},
        {"long_score": 51, "short_score": 49, "ai_error": True},
    ],
)
def test_applied_rejection_fanout_records_evidence_without_parsing_spread(fields):
    raw_ai = {
        "shared_ai_call_id": "scan-rejected-score-led",
        "raw_direction": "LONG",
        "direction": "LONG",
        "raw_decision": "APPROVE",
        "decision": "APPROVE",
        "approved": True,
        **fields,
    }
    original = copy.deepcopy(raw_ai)
    writes = []
    enqueued = []
    state = {
        "strategy_mode": "RESEARCH",
        "live_armed": False,
        "bitfinex_live_enabled": False,
        "invert_signal": False,
    }
    effective = _load_bot_function(
        "_effective_score_led_family_ai",
        {
            "state": state,
            "state_lock": _Lock(),
            "resolve_score_led_paper_admission": resolve_score_led_paper_admission,
            "SCORE_LED_PAPER_RESEARCH_ENABLED": True,
            "SCORE_LED_ADMISSION_POLICY_ID": SCORE_LED_ADMISSION_POLICY_ID,
            "is_research_data_collection": lambda: True,
            "_force_paper_mode_active": lambda: True,
            "copy": copy,
        },
    )
    namespace = {
        "_effective_score_led_family_ai": effective,
        "is_ai_scan_lane": lambda _lane: True,
        "is_research_data_collection": lambda: True,
        "state": state,
        "compute_directional_spread": (
            lambda *_args: (_ for _ in ()).throw(
                AssertionError("rejected score-led admission must not parse spread")
            )
        ),
        "_enrich_combo_lane_features": lambda features, _ctx: dict(features),
        "COMBO_EXECUTION_LANES": ("FAMILY_ONE",),
        "is_independent_ai_lane": lambda _lane: False,
        "is_shared_ai_direction_lane": lambda _lane: True,
        "is_patient_chase_lane": lambda _lane: True,
        "is_deterministic_bracket_lane": lambda _lane: False,
        "combo_lane_match_detail": lambda *_args, **_kwargs: {"passes": True},
        "is_research_lane_enabled": lambda _lane: True,
        "_stamp_shared_ai_lane_verdict": lambda *_args, **_kwargs: None,
        "_shared_ai_call_id": (
            lambda ai_result=None, ctx=None: "scan-rejected-score-led"
        ),
        "_v3_lane_policy_material": lambda _lane: {"policy_signature": "policy-v2"},
        "_write_v3_shared_lane_decision": (
            lambda *args, **kwargs:
            writes.append((copy.deepcopy(args), copy.deepcopy(kwargs))) or True
        ),
        "_enqueue_combo_lane_execution": (
            lambda *args, **kwargs: enqueued.append(copy.deepcopy(args))
        ),
        "COMBO_LANE_SPECS": {"FAMILY_ONE": {"combo_key": "ONE"}},
        "log_lane_opportunity_event": lambda *_args, **_kwargs: None,
        "logger": type(
            "Logger", (), {"error": lambda *_args: None, "info": lambda *_args: None}
        )(),
    }
    fanout = _load_bot_function("spawn_combo_lanes_from_ai_scan", namespace)
    fanout(
        {"trade_id": "scan-rejected-score-led"}, raw_ai, 2.0, {}, "AI_SCAN",
    )

    assert raw_ai == original
    assert len(writes) == 1
    assert writes[0][0][1]["decision"] == "REJECT"
    assert writes[0][0][1]["direction"] == "NO_TRADE"
    assert writes[0][1]["policy_decision"] == (
        "ERROR" if fields.get("ai_error") else "REJECT"
    )
    assert writes[0][1]["execution_disposition"] == "AI_REJECTED_NO_ORDER"
    assert enqueued == []


def test_lane_evidence_keeps_raw_and_effective_fields_separate():
    writer = ast.get_source_segment(
        BOT_SOURCE,
        next(
            item for item in BOT_TREE.body
            if isinstance(item, ast.FunctionDef)
            and item.name == "_write_v3_shared_lane_decision"
        ),
    )
    assert '"raw_direction": raw_direction' in writer
    assert '"raw_ai_decision":' in writer
    assert 'if (ai or {}).get("effective_research_admission") is not None:' in writer
    assert '"effective_research_direction": effective_research_direction' in writer
    assert '"effective_research_admission": copy.deepcopy(' in writer
    assert '"effective_research_admission_policy_id": (' in writer


@pytest.mark.parametrize(
    ("long_score", "short_score"),
    [
        (math.inf, 49),
        (-math.inf, 49),
        (math.nan, 49),
        (10**400, 49),
        ("malformed", 49),
        (51, None),
    ],
)
def test_real_lane_writer_records_none_gap_for_nonfinite_or_malformed_scores(
    long_score, short_score,
):
    captured = []
    namespace = {
        "datetime": __import__("datetime").datetime,
        "time": __import__("time"),
        "math": math,
        "copy": copy,
        "SYMBOL": "BTCUSD",
        "logger": type("Logger", (), {"error": lambda *_args: None})(),
        "_shared_ai_call_id": lambda ai_result=None, ctx=None: "writer-score-led",
        "invert_signal_active": lambda: False,
        "_v3_lane_policy_material": lambda _lane: {
            "policy_id": "SCORE_LED_NON_TIE_PAPER_V2::TEST",
            "paper_only": True,
            "relay_eligible": False,
        },
        "dual_write_lane_decision": (
            lambda source, **kwargs:
            captured.append((copy.deepcopy(source), copy.deepcopy(kwargs))) or {
                "store_verification": {"passed": True},
                "writes": [{"ledger": "pre_entry_features"}],
            }
        ),
        "_collector_v22_epoch_id": lambda: "epoch-score-led-writer",
        "os": __import__("os"),
        "_research_decision_integrity_failure": lambda **_kwargs: False,
    }
    writer = _load_bot_function("_write_v3_shared_lane_decision", namespace)
    ai = {
        "shared_ai_call_id": "writer-score-led",
        "shared_ai_call_ts": "1970-01-01T00:16:40+00:00",
        "raw_direction": "LONG",
        "direction": "NO_TRADE",
        "raw_decision": "APPROVE",
        "decision": "REJECT",
        "long_score": long_score,
        "short_score": short_score,
        "effective_research_direction": "NO_TRADE",
        "effective_research_admission": {"applied": True, "accepted": False},
        "effective_research_admission_policy_id": SCORE_LED_ADMISSION_POLICY_ID,
    }
    assert writer(
        "FAMILY_ONE", ai, {"symbol": "BTCUSD"}, {},
        policy_decision="REJECT",
        execution_disposition="AI_REJECTED_NO_ORDER",
        exact_reason="SCORE_LED_INVALID_OR_MISSING_SCORES",
    ) is True
    assert captured[0][0]["score_gap"] is None


def test_real_v3_decision_persists_raw_effective_and_bound_treatment(tmp_path):
    admission = _resolve({
        "raw_direction": "NO_TRADE",
        "raw_decision": "REJECT",
        "long_score": 42,
        "short_score": 38,
    })
    source = {
        "trade_id": "scan-score-led-stored",
        "shared_ai_call_id": "scan-score-led-stored",
        "shared_ai_call_ts_epoch": 1000.0,
        "symbol": "BTCUSD",
        "raw_direction": "NO_TRADE",
        "executed_direction": "LONG",
        "raw_ai_decision": "REJECT",
        "effective_research_direction": "LONG",
        "effective_research_admission": admission,
        "effective_research_admission_policy_id": SCORE_LED_ADMISSION_POLICY_ID,
        "long_score": 42,
        "short_score": 38,
        "score_gap": 4,
        "feature_snapshot_at_signal": {
            "research_feature_schema_version": "causal-v1",
            "price": 100.0,
            "regime": "BULL",
            "atr14_pct_3m": 0.42,
            "realized_volatility": 0.08,
            "volatility_of_volatility": 0.01,
            "adx": 27.0,
        },
    }
    lane_policy = {
        "policy_id": "SCORE_LED_NON_TIE_PAPER_V2::TEST",
        "raw_policy_id": "SCORE_LED_NON_TIE_PAPER_V2::TEST",
        "entry_ttl_sec": 1800.0,
        "exit_config": {"family": "TEST"},
        "paper_only": True,
        "relay_eligible": False,
        "admission_treatment": SCORE_LED_ADMISSION_POLICY_ID,
    }
    dual_write_lane_decision(
        source,
        lane="FAMILY_CHANDELIER_3",
        policy_decision="ACCEPT",
        execution_disposition="ORDER_ELIGIBLE",
        exact_reason="SCORE_LED_VALID_NON_TIE",
        epoch_id="epoch-score-led-test",
        data_dir=str(tmp_path),
        lane_policy=lane_policy,
    )
    store = V3EvidenceStore(str(tmp_path), epoch_id="epoch-score-led-test")
    row = json.loads(store.ledger_path("decision").read_text().splitlines()[-1])
    assert row["raw_direction"] == "NO_TRADE"
    assert row["raw_ai_decision"] == "REJECT"
    assert row["executed_direction"] == "LONG"
    assert row["effective_research_direction"] == "LONG"
    assert row["effective_research_admission"] == admission
    assert (
        row["effective_research_admission_policy_id"]
        == SCORE_LED_ADMISSION_POLICY_ID
    )
    assert row["admission_treatment"] == SCORE_LED_ADMISSION_POLICY_ID
    assert "admission_treatment" not in row["paper_policy_spec"]


def test_legacy_policy_identity_is_byte_identical_when_treatment_is_missing():
    material = {
        "policy_id": "LEGACY_TEST",
        "research_lane": "FAMILY_CHANDELIER_3",
        "entry_ttl_sec": 1800.0,
        "paper_only": True,
        "relay_eligible": False,
    }
    policy = _paper_policy_identity(
        "epoch-legacy-test",
        material,
    )
    metadata_only = _paper_policy_identity(
        "epoch-legacy-test",
        {**material, "admission_treatment": "AI_FILTERED_V1"},
    )
    assert "admission_treatment" not in policy["paper_policy_spec"]
    assert "admission_treatment" not in metadata_only["paper_policy_spec"]
    assert policy["policy_signature"] == "paper-policy-5fb72dc0c7dfd48b86d3"
    assert metadata_only["policy_signature"] == policy["policy_signature"]

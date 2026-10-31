"""Family tiles must still mark discovery coverage (no paper orders; R22 no grid rows)."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path


def _load_arm_fn(namespace: dict):
    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    start = source.index("def _discovery_touch_grid_admission(")
    end = source.index("\ndef _arm_shared_compressed_shadow_chase(", start)
    code = source[start:end]
    local_ns = dict(namespace)
    exec(code, local_ns, local_ns)
    return local_ns["_arm_shared_discovery_touch_grid"]


def _namespace(tmp_path, seen, coverage, *, score_led=None):
    def effective_score_led(ai):
        if score_led is None:
            return ai, {"applied": False}
        lane_ai = dict(ai, decision="APPROVE", direction=score_led, candidate_direction=score_led)
        return lane_ai, {"applied": True, "accepted": True, "effective_direction": score_led}

    return {
        "ai_decision_should_execute": lambda ai: ai.get("decision") == "APPROVE",
        "_effective_score_led_family_ai": effective_score_led,
        "_collector_record_touch_grid_call": lambda **kw: coverage.append(kw),
        "_shared_ai_call_id": lambda ai_result=None, ctx=None: "scan-discovery-1",
        "datetime": __import__("datetime").datetime,
        "time": time,
        "hashlib": hashlib,
        "state": {"price": 100000.0, "rest_price": 100000.0, "last_trade_price": 100000.0},
        "_discovery_touch_grid_seen_call_ids": seen,
        "logger": type("L", (), {"info": staticmethod(lambda *a, **k: None)})(),
    }


def test_shared_discovery_touch_grid_arms_once_without_paper_orders(tmp_path):
    seen, coverage = set(), []
    arm = _load_arm_fn(_namespace(tmp_path, seen, coverage))
    ctx = {"price": 100000.0}
    ai = {"decision": "APPROVE", "direction": "SHORT", "shared_ai_call_ts": "2026-09-03T10:00:00+00:00"}

    assert arm(ctx, ai) is True
    assert arm(ctx, ai) is False  # duplicate shared call
    assert "scan-discovery-1" in seen
    assert coverage == [{"armed": True, "ai_executes": True}]


def test_ai_rejected_tile_eligible_call_still_arms_discovery_grid(tmp_path):
    seen, coverage = set(), []
    arm = _load_arm_fn(_namespace(tmp_path, seen, coverage, score_led="LONG"))
    ai = {"decision": "REJECT", "direction": "NO_TRADE", "shared_ai_call_ts": "2026-09-03T10:03:00+00:00"}

    assert arm({"price": 100000.0}, ai) is True
    assert coverage == [{"armed": True, "ai_executes": False}]


def test_call_not_tile_eligible_arms_nothing(tmp_path):
    seen, coverage = set(), []
    arm = _load_arm_fn(_namespace(tmp_path, seen, coverage))
    ai = {"decision": "REJECT", "direction": "NO_TRADE"}

    assert arm({"price": 100000.0}, ai) is False
    assert seen == set() and coverage == []

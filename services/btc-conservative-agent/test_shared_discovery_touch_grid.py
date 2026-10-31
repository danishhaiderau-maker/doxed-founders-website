"""Family tiles must still arm discovery touch grid (no extra paper orders)."""
from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path

import pytest


def _load_arm_fn(namespace: dict):
    source = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
    start = source.index("def _discovery_touch_grid_admission(")
    end = source.index("\ndef _arm_shared_compressed_shadow_chase(", start)
    code = source[start:end]
    local_ns = dict(namespace)
    exec(code, local_ns, local_ns)
    return local_ns["_arm_shared_discovery_touch_grid"]


def _namespace(tmp_path, appended, book, seen, coverage, *, score_led=None):
    def arm_touch_grid_rows(**kwargs):
        assert kwargs["live_offset_pct"] == -1.0
        rows = []
        for offset in (0.01, 0.10, 0.30):
            rows.append({
                "trade_id": kwargs["trade_id"],
                "offset_pct": offset,
                "places_live_order": offset == kwargs["live_offset_pct"],
                "limit_price": 100.0 * (1 + offset / 100.0),
                "direction": kwargs["direction"],
                "signal_price": kwargs["signal_price"],
                "signal_ts": kwargs["signal_ts"],
                "expires_ts": kwargs["signal_ts"] + kwargs["ttl_sec"],
                "invert_on": kwargs["invert_on"],
            })
        return rows

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
        "invert_signal_active": lambda: False,
        "datetime": __import__("datetime").datetime,
        "time": time,
        "hashlib": hashlib,
        "state": {"price": 100000.0, "rest_price": 100000.0, "last_trade_price": 100000.0},
        "arm_touch_grid_rows": arm_touch_grid_rows,
        "new_grid_state": lambda rows: {"trade_id": rows[0]["trade_id"], "expires_ts": rows[0]["expires_ts"]},
        "_safe_append_jsonl": lambda path, row, label=None: appended.append(copy.deepcopy(row)),
        "_touch_grid_book": book,
        "_discovery_touch_grid_seen_call_ids": seen,
        "CHASE_OFFSET_TOUCH_GRID_FILE": str(tmp_path / "grid.jsonl"),
        "logger": type("L", (), {"info": staticmethod(lambda *a, **k: None)})(),
    }


def test_shared_discovery_touch_grid_arms_once_without_paper_orders(tmp_path, monkeypatch):
    appended, book, seen, coverage = [], {}, set(), []
    arm = _load_arm_fn(_namespace(tmp_path, appended, book, seen, coverage))
    ctx = {"price": 100000.0}
    ai = {"decision": "APPROVE", "direction": "SHORT", "shared_ai_call_ts": "2026-09-03T10:00:00+00:00"}

    assert arm(ctx, ai) is True
    assert arm(ctx, ai) is False  # duplicate shared call
    # R22: chase_offset_touch_grid writers removed — durable appends must stay empty.
    assert appended == []
    # RK 7524ab402ae2: no _touch_grid_book fill (touch-grid arm/poll half gone).
    assert len(book) == 0
    assert "scan-discovery-1" in seen
    assert coverage == [{"armed": True, "ai_executes": True}]


def test_ai_rejected_tile_eligible_call_still_arms_discovery_grid(tmp_path):
    appended, book, seen, coverage = [], {}, set(), []
    arm = _load_arm_fn(_namespace(tmp_path, appended, book, seen, coverage, score_led="LONG"))
    ai = {"decision": "REJECT", "direction": "NO_TRADE", "shared_ai_call_ts": "2026-09-03T10:03:00+00:00"}

    assert arm({"price": 100000.0}, ai) is True
    assert appended == []  # R22: no durable chase grid append
    # RK 7524ab402ae2: discovery marks seen/coverage without filling _touch_grid_book.
    assert len(book) == 0
    assert coverage == [{"armed": True, "ai_executes": False}]


def test_call_not_tile_eligible_arms_nothing(tmp_path):
    appended, book, seen, coverage = [], {}, set(), []
    arm = _load_arm_fn(_namespace(tmp_path, appended, book, seen, coverage))
    ai = {"decision": "REJECT", "direction": "NO_TRADE"}

    assert arm({"price": 100000.0}, ai) is False
    assert appended == [] and book == {} and coverage == []

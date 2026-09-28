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
    start = source.index("def _arm_shared_discovery_touch_grid(")
    end = source.index("\ndef _arm_shared_compressed_shadow_chase(", start)
    code = source[start:end]
    local_ns = dict(namespace)
    exec(code, local_ns, local_ns)
    return local_ns["_arm_shared_discovery_touch_grid"]


def test_shared_discovery_touch_grid_arms_once_without_paper_orders(tmp_path, monkeypatch):
    appended = []
    book = {}
    seen = set()

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

    namespace = {
        "ai_decision_should_execute": lambda ai: ai.get("decision") == "APPROVE",
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
    arm = _load_arm_fn(namespace)
    ctx = {"price": 100000.0}
    ai = {"decision": "APPROVE", "direction": "SHORT", "shared_ai_call_ts": "2026-09-03T10:00:00+00:00"}

    assert arm(ctx, ai) is True
    assert arm(ctx, ai) is False  # duplicate shared call
    assert len(appended) == 3
    assert all(row["places_live_order"] is False for row in appended)
    assert all(row["discovery_shadow_only"] is True for row in appended)
    assert all(row["shared_ai_call_id"] == "scan-discovery-1" for row in appended)
    assert len(book) == 1
    assert "scan-discovery-1" in seen

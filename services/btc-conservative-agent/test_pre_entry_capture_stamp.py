"""pre_entry_features.captured_at_ts must be produced (was always null)."""
from __future__ import annotations

import ast
import copy
import math
from pathlib import Path

BOT_PATH = Path(__file__).with_name("bot.py")
SOURCE = BOT_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _fn():
    node = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "_stamp_feature_capture")
    ns = {"copy": copy, "math": math}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node], type_ignores=[])), str(BOT_PATH), "exec"), ns)
    return ns["_stamp_feature_capture"]


def test_stamp_uses_shared_call_time_when_snapshot_has_no_clock():
    out = _fn()({"rsi": 51.0}, 1_791_103_272.0)
    assert out["capture_schema"] == "measured_feature_capture_v1"
    assert out["captured_at_ts"] == 1_791_103_272.0
    assert out["captured_at_source"] == "SHARED_AI_CALL_TS"
    assert out["rsi"] == 51.0


def test_stamp_prefers_the_snapshot_clock_and_never_post_dates_the_signal():
    fn = _fn()
    assert fn({"snapshot_ts": 100.0}, 105.0)["captured_at_ts"] == 100.0
    late = fn({"snapshot_ts": 110.0}, 105.0)
    assert late["captured_at_ts"] == 105.0 and late["captured_at_source"] == "SHARED_AI_CALL_TS"


def test_existing_capture_is_kept_and_input_not_mutated():
    src = {"capture_schema": "measured_feature_capture_v1", "captured_at_ts": 9.0}
    assert _fn()(src, 10.0) == src
    plain = {"a": 1}
    _fn()(plain, 10.0)
    assert plain == {"a": 1}


def test_lane_decision_writer_stamps_the_snapshot():
    assert '"feature_snapshot_at_signal": _stamp_feature_capture(features, signal_ts),' in SOURCE

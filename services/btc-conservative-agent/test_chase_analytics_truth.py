import ast
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOT_SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")
ANALYZER_SOURCE = (ROOT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")


_COMPILED_FUNCTIONS = {}


def _compile(source, name, namespace):
    # The same bot functions are extracted for every fixture case. Cache only
    # the immutable code object; execute it into each test namespace so the
    # fixture-specific globals remain isolated while CI avoids repeated AST
    # parsing/compilation CPU cost.
    key = (id(source), name)
    code = _COMPILED_FUNCTIONS.get(key)
    if code is None:
        tree = ast.parse(source)
        node = next(
            item for item in tree.body
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
        )
        module = ast.Module(body=[node], type_ignores=[])
        ast.fix_missing_locations(module)
        code = compile(module, name, "exec")
        _COMPILED_FUNCTIONS[key] = code
    exec(code, namespace)
    return namespace[name]


def test_after_cost_stats_exclude_missing_and_nonfinite_pnl_and_ignore_win_flag():
    namespace = {"math": math}
    namespace["_chase_count_bucket"] = _compile(ANALYZER_SOURCE, "_chase_count_bucket", namespace)
    stats = _compile(ANALYZER_SOURCE, "_chase_bucket_stats", namespace)([
        {"chase_count": 2, "net_pnl_usd": None, "win": True},
        {"chase_count": 2, "net_pnl_usd": float("nan"), "win": True},
        {"chase_count": 2, "net_pnl_usd": float("inf"), "win": True},
        {"chase_count": 2, "net_pnl_usd": -2.0, "win": True},
        {"chase_count": 2, "net_pnl_usd": 1.0, "win": False},
    ])
    assert stats["2"]["trades"] == 2
    assert stats["2"]["wins"] == 1
    assert stats["2"]["win_rate_pct"] == 50.0
    assert stats["2"]["sum_pnl_usd"] == -1.0
    assert stats["2"]["ev_usd"] == -0.5


def test_report_cohort_requires_finite_cost_and_current_settings_epoch(tmp_path):
    binding = {
        "schema": "execution_settings_binding_v1",
        "signature": "gap=3|chase=2,3,4",
        "effective_epoch": 100.0,
        "gap_buckets": ["3"],
        "chase_buckets": ["2", "3", "4"],
    }
    namespace = {
        "os": os,
        "json": json,
        "math": math,
        "datetime": datetime,
        "timezone": timezone,
        "ANALYZER_SYNC_ID": "analyzer-v1",
        "EXPECTED_BOT_VERSION": "test",
        "PIPELINE_ENFORCEMENT_TAG": "[TEST]",
        "CHASE_ATTRIBUTION_REPORT_FILE": "chase_attribution_report.json",
        "CHASE_EFFECTIVENESS_REPORT_FILE": "chase_effectiveness_report.json",
        "load_research_session": lambda: {},
        "_shadow_scope_label": lambda _session: "SESSION",
        "_current_execution_settings_binding": lambda _session: binding,
        "analyzer_report_path": lambda _name: str(tmp_path / _name),
    }
    namespace["_chase_count_bucket"] = _compile(ANALYZER_SOURCE, "_chase_count_bucket", namespace)
    namespace["_chase_bucket_stats"] = _compile(ANALYZER_SOURCE, "_chase_bucket_stats", namespace)
    report = _compile(ANALYZER_SOURCE, "chase_effectiveness_report", namespace)(
        session={},
        chase_payload={"trades": [
            {"chase_count": 2, "net_pnl_usd": -2.0, "win": True, "settings_observation_epoch": 101.0},
            {"chase_count": 2, "net_pnl_usd": 1.0, "win": False, "settings_observation_epoch": 102.0},
            {"chase_count": 2, "net_pnl_usd": None, "win": True, "settings_observation_epoch": 103.0},
            {"chase_count": 2, "net_pnl_usd": float("nan"), "win": True, "settings_observation_epoch": 104.0},
            {"chase_count": 2, "net_pnl_usd": 20.0, "win": True, "settings_observation_epoch": 99.0},
        ]},
    )
    assert report["metrics_status"] == "VERIFIED_CURRENT_SETTINGS_COHORT"
    assert report["execution_settings_binding"] == binding
    assert report["cohort_counts"] == {
        "input_attributions": 5,
        "included_finite_net_pnl": 2,
        "exclusions": {
            "MISSING_OR_NONFINITE_NET_PNL": 2,
            "MISSING_OR_INVALID_SETTINGS_TIME": 0,
            "BEFORE_CURRENT_SETTINGS_EPOCH": 1,
        },
    }
    assert report["buckets"]["2"]["trades"] == 2
    assert report["buckets"]["2"]["wins"] == 1
    assert report["buckets"]["2"]["ev_usd"] == -0.5


def test_settings_binding_recomputes_canonical_runtime_signature():
    class FakePandas:
        @staticmethod
        def isna(_value):
            return False

    rows = [
        {"epoch": 20.0, "signature": "forged", "gap_buckets": ["3"], "chase_buckets": ["2"]},
        {"epoch": 10.0, "signature": "gap=3|chase=2,3,4", "gap_buckets": ["3"], "chase_buckets": ["2", "3", "4"]},
    ]
    namespace = {
        "math": math,
        "pd": FakePandas,
        "_session_start_ts": lambda _session: datetime.fromtimestamp(1, tz=timezone.utc),
        "_load_jsonl_rows": lambda _path: rows,
    }
    binding = _compile(ANALYZER_SOURCE, "_current_execution_settings_binding", namespace)({})
    assert binding["signature"] == "gap=3|chase=2,3,4"
    assert binding["effective_epoch"] == 10.0


def test_producer_and_ui_disclose_finite_after_cost_basis_and_unavailable_state():
    assert '"ev_denominator": "current_settings_bucket_attributions_with_finite_net_pnl"' in ANALYZER_SOURCE
    assert '"metrics_status": (' in ANALYZER_SOURCE
    assert 'id="chaseAnalyticsStatus"' in BOT_SOURCE
    assert "ch.status === 'SIMULATED_SHADOW'" in BOT_SOURCE
    assert "'<strong style=\"color:#f59e0b\">UNAVAILABLE</strong>" in BOT_SOURCE
    assert "_chase_analytics_identity_error" not in BOT_SOURCE
    assert "_active_analyzer_mirror_dir()" not in BOT_SOURCE.split("def _load_chase_analytics_snapshot", 1)[1].split("\ndef ", 1)[0]

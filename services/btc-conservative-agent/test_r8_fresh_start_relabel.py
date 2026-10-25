"""RK PASS 12:48 (R8 B): /api/status, /api/monitor/summary and /api/state show the real fresh start."""
import ast
from datetime import datetime, timezone
from pathlib import Path

SRC = (Path(__file__).with_name("bot.py")).read_text()
TREE = ast.parse(SRC)


def _ns():
    ns = {"datetime": datetime, "timezone": timezone}
    want = {"_ANALYZER_BUNDLE_FRESH_FLOOR_TS", "FRESH_START_TS", "FRESH_START_UTC"}
    for node in TREE.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) in want for t in node.targets):
            exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
        if isinstance(node, ast.FunctionDef) and node.name == "_data_epoch_public":
            exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
    return ns


def test_fresh_start_constant_is_18_oct_0601_aedt():
    ns = _ns()
    assert ns["FRESH_START_TS"] == 1792263671
    assert ns["FRESH_START_UTC"] == "2026-10-17T19:01:11+00:00"


def test_status_data_epoch_started_is_fresh_start_and_epoch_id_unchanged():
    ns = _ns()
    ns.update({"_DATA_EPOCH_MANIFEST": {"epoch_id": "ce-x", "started_at_utc": "2026-10-04T08:13:26Z"},
               "DATA_EPOCH_ID": "ce-x", "_data_epoch_boundary_status": lambda m: {},
               "_data_epoch": type("D", (), {"STAMP_FIELD": "data_epoch"})})
    out = ns["_data_epoch_public"]()
    assert out["started_at_utc"] == "2026-10-17T19:01:11+00:00"
    assert out["manifest_started_at_utc"] == "2026-10-04T08:13:26Z"
    assert out["configured"] == "ce-x" and out["epoch_id"] == "ce-x"


def test_monitor_summary_and_state_session_start_use_fresh_start():
    assert '"data_epoch_started_at": FRESH_START_UTC,' in SRC
    assert '"session_start_time": float(FRESH_START_TS),' in SRC

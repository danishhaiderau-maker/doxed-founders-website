"""RK PASS 12:48 (R8 B): /api/status, /api/monitor/summary and /api/state show the real fresh start."""
import ast
from datetime import datetime, timezone
from pathlib import Path

import analyzer_book10_regime as abr

SRC = (Path(__file__).with_name("bot.py")).read_text()
TREE = ast.parse(SRC)


def _ns():
    """Load FRESH_START_* from bot.py AST.

    Rebuild pin binds the floor via ``import analyzer_book10_regime as _abr_rebuild_pin``
    immediately before the Assigns (R4 single source). The harness must exec that Import
    first or Assigns raise NameError (CI fail on tip c9b7f3e3e).
    """
    ns = {"datetime": datetime, "timezone": timezone}
    want = {"_ANALYZER_BUNDLE_FRESH_FLOOR_TS", "FRESH_START_TS", "FRESH_START_UTC"}
    for node in TREE.body:
        if isinstance(node, ast.Import) and any(
            alias.name == "analyzer_book10_regime" for alias in node.names
        ):
            exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) in want for t in node.targets):
            exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
        if isinstance(node, ast.FunctionDef) and node.name == "_data_epoch_public":
            exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
    return ns


def test_fresh_start_constant_is_18_oct_0601_aedt():
    ns = _ns()
    assert ns["FRESH_START_TS"] == 1792263671
    assert ns["FRESH_START_UTC"] == "2026-10-17T19:01:11+00:00"
    # R4 single-source: bot floor must equal analyzer_book10_regime.EPOCH_FLOOR_TS
    assert ns["FRESH_START_TS"] == abr.EPOCH_FLOOR_TS
    assert ns["_ANALYZER_BUNDLE_FRESH_FLOOR_TS"] == abr.EPOCH_FLOOR_TS
    # Mutation: skipping the abr Import in the harness would NameError — prove import is present.
    assert "_abr_rebuild_pin" in ns
    assert "import analyzer_book10_regime as _abr_rebuild_pin" in SRC


def test_status_data_epoch_started_is_fresh_start_and_epoch_id_unchanged():
    ns = _ns()
    ns.update({"_DATA_EPOCH_MANIFEST": {"epoch_id": "ce-x", "started_at_utc": "2026-10-04T08:13:26Z"},
               "DATA_EPOCH_ID": "ce-x", "_data_epoch_boundary_status": lambda m: {},
               "_data_epoch": type("D", (), {"STAMP_FIELD": "data_epoch"})})
    out = ns["_data_epoch_public"]()
    assert out["started_at_utc"] == "2026-10-17T19:01:11+00:00"
    assert out["manifest_started_at_utc"] == "2026-10-04T08:13:26Z"
    assert out["configured"] == "ce-x" and out["epoch_id"] == "ce-x"


class _Lock:
    def acquire(self, timeout=None):
        return False

    def release(self):
        pass


def test_state_session_start_time_is_the_fresh_start_not_the_bot_or_4_oct_start():
    """Behavioural: /api/state's dashboard truth (owner and public projection) reports 1792263671."""
    ns = _ns()
    names = {"_build_dashboard_truth", "_public_dashboard_truth"}
    for node in TREE.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
    ns.update({"time": __import__("time"), "get_effective_ai_cooldown_sec": lambda: 60.0, "trade_lock": _Lock(),
               "_API_STATE_LOCK_TIMEOUT_SEC": 0.0, "_dashboard_wal_summary_cached": lambda now: {},
               "_dashboard_truth_fields": lambda snap, now: {}, "_deepseek_dashboard_status": lambda *a: {},
               "_dashboard_operating_truth": lambda *a: {}, "process_boot_time": 1792400000.0,
               "_unavailable": lambda r: {"reason": r}, "_PUBLIC_OWNER_ONLY_REASON": "owner only"})
    four_oct = 1791101606.0  # 2026-10-04T08:13:26Z, the DATA_EPOCH manifest stamp
    truth = ns["_build_dashboard_truth"]({"bot_start_time": four_oct}, now=1792500000.0)
    assert truth["session_start_time"] == 1792263671.0
    assert truth["bot_start_time"] == four_oct  # the raw process/bot start stays visible, under its own name
    assert ns["_public_dashboard_truth"](truth)["session_start_time"] == 1792263671.0


def test_monitor_summary_data_epoch_started_at_uses_fresh_start():
    # _monitor_summary_payload needs the whole live bot; pin the field binding.
    assert '"data_epoch_started_at": FRESH_START_UTC,' in SRC

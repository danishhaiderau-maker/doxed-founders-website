"""Tile cards show current-epoch truth only (Health Monitor H-B finding).

The H-B tile (now retired in PHASE02) once read 24 closes / +$0.44 labelled
'historical/analyzer total' while the epoch's true figure was 2 closes / +$0.07.
The epoch-truth machinery is generic and lane-agnostic, so these tests pin the
fixes with H-A as the representative surviving lane: the persistent lane ledger
is segregated per epoch (copied aside, never deleted), forced closes stay out of
it, the tile scope uses the full epoch ledger rather than the display-capped
Trades list, and approval-based metrics read n/a when no approval count covers
the epoch.
"""

from __future__ import annotations

import ast
import contextlib
import copy
import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)

HA = "FAMILY_COMMITTED_FADE_TAKER_90"
FORCED = frozenset({"ADMIN_MANUAL_CLOSE", "ADMIN_FORCE_FLAT", "CIRCUIT_BREAKER_ADMIN_MANUAL"})
CUTOFF_UTC = "2026-10-04T08:37:00+00:00"
CUTOFF_TS = datetime.fromisoformat(CUTOFF_UTC).timestamp()


def _load(names, namespace):
    nodes = [n for n in BOT_TREE.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    module = ast.Module(body=nodes, type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace


def _stats_namespace(**extra):
    ns = {"copy": copy, "datetime": datetime, "_LANE_OPPORTUNITY_COUNTERS_SINCE_TS": 0.0,
          "_current_epoch_id_for_display": lambda: "ce-20261004-v31-freeze21b", **extra}
    return _load(
        ["_truthful_approve_to_fill_pct", "_win_rate_pct", "_session_stats_from_lane_metrics",
         "_lane_counters_cover_epoch", "_scope_pathway_specs_to_signed_epoch"],
        ns,
    )


def _ha_ledger():
    return {HA: {"lane": HA, "closes": 2, "net_pnl_usd": 0.07, "wins": 1, "losses": 1}}


def test_epoch_tile_uses_full_ledger_not_capped_trades_list():
    ns = _stats_namespace(_derive_lane_pnl_ledger_from_trades=lambda rows: {})
    capped_trades = [{"research_lane": "OTHER", "net_pnl_usd": 1.0}] * 5
    out = ns["_scope_pathway_specs_to_signed_epoch"](
        {"lanes": [{"lane": HA, "session_stats": {"real_fills": 24, "net_pnl_real": 0.44}}]},
        capped_trades, {HA: {"approves": 4}}, CUTOFF_UTC,
        ledger=_ha_ledger(), counters_since_ts=CUTOFF_TS - 60,
    )
    stats = out["lanes"][0]["session_stats"]
    assert stats["real_fills"] == 2
    assert stats["net_pnl_real"] == 0.07
    assert stats["scope"] == "SIGNED_FRESH_EPOCH"
    assert stats["epoch_id"] == "ce-20261004-v31-freeze21b"
    assert stats["approvals_known"] is True
    assert stats["approves"] == 4
    assert stats["approve_to_fill_pct"] == 50.0


def test_counters_younger_than_epoch_make_approval_metrics_na():
    ns = _stats_namespace(_derive_lane_pnl_ledger_from_trades=lambda rows: {})
    out = ns["_scope_pathway_specs_to_signed_epoch"](
        {"lanes": [{"lane": HA}]}, [], {HA: {"approves": 1}}, CUTOFF_UTC,
        ledger=_ha_ledger(), counters_since_ts=CUTOFF_TS + 3600,  # process restarted later
    )
    stats = out["lanes"][0]["session_stats"]
    assert stats["real_fills"] == 2 and stats["net_pnl_real"] == 0.07
    assert stats["approvals_known"] is False
    assert stats["approves"] is None
    assert stats["approve_to_fill_pct"] is None
    assert stats["per_approve_ev"] is None
    assert "n/a fill" in stats["summary_line"] and "EV n/a/approve" in stats["summary_line"]
    assert "100% fill" not in stats["summary_line"]


def test_merge_never_substitutes_closes_for_approvals():
    body = ast.get_source_segment(BOT_SOURCE, next(
        n for n in BOT_TREE.body
        if isinstance(n, ast.FunctionDef) and n.name == "_merge_pathway_specs_with_session_stats"))
    assert "approves or closes" not in body
    assert "round(pnl / closes, 2)" not in body
    assert '"approvals_known": approvals_known' in body


def test_tile_label_never_claims_history_for_unknown_scope():
    assert "historical/analyzer total" not in BOT_SOURCE
    assert "epoch scope unavailable (no epoch cutoff on this snapshot)" in BOT_SOURCE


def _ledger_namespace(tmp_path, trades, boundary_ts):
    state = {}
    ns = {
        "copy": copy, "json": json, "os": os, "shutil": shutil, "time": time, "Path": Path,
        "contextlib": contextlib,
        "state": state, "state_lock": contextlib.nullcontext(), "trade_lock": contextlib.nullcontext(),
        "STATS_EXCLUDED_EXIT_REASONS": FORCED, "STARTING_BALANCE": 1000.0,
        "LANE_LEDGER_WL_BASIS": "PRICE_BP_NET_OF_FEES", "monitor_api": __import__("monitor_api"),
        "LANE_PNL_LEDGER_FILE": str(tmp_path / "lane_pnl_ledger.json"),
        "LANE_PNL_EPOCH_RECEIPTS_FILE": str(tmp_path / "lane_pnl_ledger_epoch_receipts.jsonl"),
        "_lane_pnl_epoch_status": {},
        "_current_epoch_boundary": lambda force=False: (boundary_ts, "data_epoch"),
        "_current_epoch_id_for_display": lambda: "ce-20261004-v31-freeze21b",
        "_normalize_lane_key": lambda lane: str(lane or "").upper(),
        "utc_iso": lambda: "2026-10-04T10:00:00Z",
        "logger": type("L", (), {"info": staticmethod(lambda *a, **k: None)})(),
        "_LEDGER_WRITES": type("W", (), {"success": staticmethod(lambda *a: None),
                                          "failure": staticmethod(lambda *a: None)})(),
    }

    def append(path, row, label="JSONL"):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        return True

    def accounting(session_start):
        rows = [t for t in trades if float(t["exit_ts"]) >= session_start]
        return len(rows), 0.0, ns["_derive_lane_pnl_ledger_from_trades"](rows)

    ns["_safe_append_jsonl"] = append
    ns["_session_trade_accounting_locked"] = accounting
    _load(["_trade_row_is_forced_close", "_trade_row_net_pnl_usd", "_derive_lane_pnl_ledger_from_trades",
           "_lane_pnl_ledger_epoch_tag", "_write_lane_pnl_ledger_file",
           "_segregate_lane_pnl_ledger_for_epoch", "update_lane_pnl_ledger"], ns)
    return ns


def test_pre_epoch_ledger_is_archived_not_deleted_and_rebuilt_from_epoch(tmp_path):
    trades = [
        {"research_lane": HA, "net_pnl_usd": 0.10, "exit_ts": CUTOFF_TS - 900},  # previous epoch
        {"research_lane": HA, "net_pnl_usd": 0.12, "exit_ts": CUTOFF_TS + 60},
        {"research_lane": HA, "net_pnl_usd": -0.05, "exit_ts": CUTOFF_TS + 120},
        {"research_lane": HA, "net_pnl_usd": -0.30, "exit_ts": CUTOFF_TS + 180,
         "exit_reason": "ADMIN_MANUAL_CLOSE"},  # deploy flatten: excluded
    ]
    ns = _ledger_namespace(tmp_path, trades, CUTOFF_TS)
    stale = {"schema": "lane_pnl_ledger_v1", "lanes": {HA: {"closes": 24, "net_pnl_usd": 0.44}}}
    Path(ns["LANE_PNL_LEDGER_FILE"]).write_text(json.dumps(stale), encoding="utf-8")

    result = ns["_segregate_lane_pnl_ledger_for_epoch"]()
    assert result["status"] == "SEGREGATED" and result["deletion_invoked"] is False
    archived = json.loads(Path(result["archived_to"]).read_text(encoding="utf-8"))
    assert archived == stale  # the old totals survive byte-for-byte elsewhere
    assert "research_archive/pre_epoch/ce-20261004-v31-freeze21b" in result["archived_to"].replace("\\", "/")
    current = json.loads(Path(ns["LANE_PNL_LEDGER_FILE"]).read_text(encoding="utf-8"))
    assert current["epoch_id"] == "ce-20261004-v31-freeze21b"
    assert current["lanes"][HA]["closes"] == 2
    assert current["lanes"][HA]["net_pnl_usd"] == 0.07
    assert ns["state"]["lane_pnl_ledger"][HA]["closes"] == 2
    assert Path(ns["LANE_PNL_EPOCH_RECEIPTS_FILE"]).is_file()

    again = ns["_segregate_lane_pnl_ledger_for_epoch"]()
    assert again["status"] == "CURRENT" and "archived_to" not in again


def test_forced_close_never_enters_the_persistent_lane_ledger(tmp_path):
    ns = _ledger_namespace(tmp_path, [], CUTOFF_TS)
    ns["update_lane_pnl_ledger"](HA, "CLOSE", 0.12, "LONG", exit_reason="TP")
    ns["update_lane_pnl_ledger"](HA, "CLOSE", -0.40, "LONG", exit_reason="ADMIN_MANUAL_CLOSE")
    bucket = ns["state"]["lane_pnl_ledger"][HA]
    assert bucket["closes"] == 1 and bucket["net_pnl_usd"] == 0.12
    on_disk = json.loads(Path(ns["LANE_PNL_LEDGER_FILE"]).read_text(encoding="utf-8"))
    assert on_disk["epoch_id"] == "ce-20261004-v31-freeze21b"
    assert on_disk["lanes"][HA]["closes"] == 1


def test_boot_segregates_after_session_trades_load():
    main_src = BOT_SOURCE[BOT_SOURCE.index("    load_session_trades_from_csv()\n    _recompute_research_balance_from_trades()"):]
    assert main_src.index("_segregate_lane_pnl_ledger_for_epoch()") < main_src.index("_start_api_state_cache_refresher()")
    assert "exit_reason=exit_reason," in BOT_SOURCE


def test_execution_settings_history_rows_declare_epoch():
    """Every execution_settings_history row carries data_epoch_id + bot_version (data.compat_declared)."""
    body = ast.get_source_segment(BOT_SOURCE, next(
        n for n in BOT_TREE.body
        if isinstance(n, ast.FunctionDef) and n.name == "_record_execution_settings_epoch"))
    assert '"bot_version": EXECUTION_FIX_VERSION' in body
    assert 'row["data_epoch_id"] = epoch_id' in body
    assert 'globals().get("_DATA_EPOCH_MANIFEST")' in body
    import data_epoch
    assert "execution_settings_history.jsonl" in data_epoch.STAMPED_WRITER_BASES

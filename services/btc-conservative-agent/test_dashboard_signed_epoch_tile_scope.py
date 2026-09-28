"""Prevent prior-epoch lane totals from contaminating clean-epoch tiles."""

from __future__ import annotations

import ast
import copy
from pathlib import Path


BOT_PATH = Path(__file__).with_name("bot.py")
BOT_SOURCE = BOT_PATH.read_text(encoding="utf-8")
BOT_TREE = ast.parse(BOT_SOURCE)


def _function(name: str) -> ast.FunctionDef:
    return next(
        node for node in BOT_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _function_source(name: str) -> str:
    source = ast.get_source_segment(BOT_SOURCE, _function(name))
    assert source is not None
    return source


def _compile_scope_helper():
    fn = _function("_scope_pathway_specs_to_signed_epoch")
    module = ast.Module(body=[fn], type_ignores=[])
    ast.fix_missing_locations(module)

    def derive(rows):
        pnl = sum(float(row.get("net_pnl_usd") or 0) for row in rows)
        wins = sum(1 for row in rows if float(row.get("net_pnl_usd") or 0) > 0)
        return {
            "OFFSET_029_ATR_TP_25": {
                "closes": len(rows), "net_pnl_usd": pnl,
                "wins": wins, "losses": len(rows) - wins,
            }
        } if rows else {}

    namespace = {
        "copy": copy,
        "_derive_lane_pnl_ledger_from_trades": derive,
        "_settings_period_breakdown": lambda: {},
        "_reconcile_settings_periods_to_headline": lambda stats, rows: rows,
        "_session_stats_from_lane_metrics": lambda metrics: dict(metrics),
    }
    exec(compile(module, str(BOT_PATH), "exec"), namespace)
    return namespace["_scope_pathway_specs_to_signed_epoch"]


def test_clean_epoch_with_no_executions_zeros_stale_analyzer_headline() -> None:
    scope = _compile_scope_helper()
    payload = {
        "session_scope": "FRESH-COLLECTION",
        "lanes": [{
            "lane": "OFFSET_029_ATR_TP_25",
            "session_stats": {
                "approves": 14, "real_fills": 14, "net_pnl_real": -24.94,
            },
        }],
    }
    result = scope(
        payload,
        [],
        {"OFFSET_029_ATR_TP_25": {"approves": 3}},
        "2026-08-24T00:00:00+00:00",
    )
    stats = result["lanes"][0]["session_stats"]
    assert stats["approves"] == 3
    assert stats["real_fills"] == 0
    assert stats["net_pnl_real"] == 0.0
    assert stats["scope"] == "SIGNED_FRESH_EPOCH"
    assert result["session_scope"] == "SIGNED_FRESH_EPOCH"


def test_clean_epoch_uses_only_cutoff_filtered_trade_slice() -> None:
    scope = _compile_scope_helper()
    result = scope(
        {"lanes": [{"lane": "OFFSET_029_ATR_TP_25"}]},
        [{"net_pnl_usd": 2.5}],
        {"OFFSET_029_ATR_TP_25": {"approves": 2}},
        "2026-08-24T00:00:00+00:00",
    )
    stats = result["lanes"][0]["session_stats"]
    assert stats["real_fills"] == 1
    assert stats["net_pnl_real"] == 2.5
    assert stats["per_approve_ev"] == 1.25


def test_api_snapshot_scopes_ledger_and_tile_specs_from_same_trade_slice() -> None:
    body = _function_source("_build_api_state_snapshot")
    assert 'snapshot["lane_pnl_ledger"] = _derive_lane_pnl_ledger_from_trades(trades_copy)' in body
    assert "_scope_pathway_specs_to_signed_epoch(" in body
    assert "trades_copy," in body
    assert "_epoch_cutoff," in body
    assert "current signed clean-epoch total" in BOT_SOURCE


def test_fresh_reset_clears_in_memory_lane_totals_and_cached_tile_payload() -> None:
    reset = _function_source("reset_runtime_state")
    for field in (
        '"lane_opportunity_counters": {}',
        '"pipeline_funnel_counters": {}',
        '"lane_pnl_ledger": {}',
        '"lane_lab_pnl_ledger": {}',
    ):
        assert field in reset
    fresh_reset = _function_source("_perform_fresh_collection_reset_locked")
    assert "global _cached_pathway_lane_specs" in fresh_reset
    assert "_cached_pathway_lane_specs = {}" in fresh_reset

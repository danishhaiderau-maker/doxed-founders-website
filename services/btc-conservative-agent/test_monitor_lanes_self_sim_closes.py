"""Self-sim FADE3 private ledgers must count on /api/monitor/lanes (reporting honesty).

Proven bug: gf3 / gf3l write CLOSE rows only to gf3*_paper_ledger.jsonl; shared
``trades`` stays empty, so lanes/summary cards showed closes=0 forever even when
dedicated ``/api/monitor/tiles/gf3`` had closed_n>=1. Soft B / Force / wipe / arm
and entry gates are out of scope — reporting overlay only.
"""
from __future__ import annotations

import json
from pathlib import Path

import bot
import monitor_api
import tile_fade3
import tile_fade3_loose

GF3 = tile_fade3.LANE
GF3L = tile_fade3_loose.LANE
EPOCH_START = 1_791_100_000.0


def _close_row(mod, *, fill_ts: float, side: str = "SHORT", pnl_bp: float = 30.0, reason: str = "TP"):
    notional = float(mod.NOTIONAL_USD)
    entry = 100_000.0
    sign = -1.0 if side == "SHORT" else 1.0
    exit_px = entry * (1.0 + sign * pnl_bp / 1e4)
    return {
        "event": "CLOSE",
        "lane": mod.LANE,
        "tile_key": mod.TILE_KEY,
        "side": side,
        "entry_px": entry,
        "exit_px": exit_px,
        "entry_fill_ts": fill_ts - 60.0,
        "signal_ts": fill_ts - 61.0,
        "exit_reason": reason,
        "pnl_bp": pnl_bp,
        "pnl_usd": pnl_bp / 1e4 * notional,
        "exit_fill": {"fill_ts": fill_ts, "price": exit_px, "status": "FILLED"},
        "paper_only": True,
    }


def test_close_row_as_shared_trade_projects_monitor_fields():
    row = _close_row(tile_fade3, fill_ts=EPOCH_START + 100.0, pnl_bp=30.0)
    trade = tile_fade3.close_row_as_shared_trade(row)
    assert trade["research_lane"] == GF3
    assert trade["dir"] == "SHORT"
    assert trade["self_simulated_paper"] is True
    assert trade["net_pnl_bp"] == 30.0
    assert abs(trade["net_pnl_usd"] - 0.075) < 1e-9
    assert trade["ts"].startswith("20")
    assert tile_fade3.close_row_as_shared_trade({"event": "OPEN", "lane": GF3}) is None
    assert tile_fade3_loose.close_row_as_shared_trade is tile_fade3.close_row_as_shared_trade


def test_monitor_lane_rows_include_gf3_and_gf3l_private_closes(tmp_path, monkeypatch):
    gf3_path = tmp_path / "gf3_paper_ledger.jsonl"
    gf3l_path = tmp_path / "gf3l_paper_ledger.jsonl"
    gf3_path.write_text(
        json.dumps(_close_row(tile_fade3, fill_ts=EPOCH_START + 500.0, pnl_bp=30.0)) + "\n"
        + json.dumps({"event": "OPEN", "lane": GF3}) + "\n"
        + json.dumps(_close_row(tile_fade3, fill_ts=EPOCH_START - 100.0, pnl_bp=10.0)) + "\n",  # pre-session
        encoding="utf-8",
    )
    gf3l_path.write_text(
        json.dumps(_close_row(tile_fade3_loose, fill_ts=EPOCH_START + 700.0, side="LONG", pnl_bp=25.0)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "_gf3_ledger_path", lambda: str(gf3_path))
    monkeypatch.setattr(bot, "_gf3l_ledger_path", lambda: str(gf3l_path))
    monkeypatch.setattr(bot, "trades", [])  # shared book empty — the proven blind spot
    monkeypatch.setattr(bot, "_showcase_trade_session_start", lambda: EPOCH_START)

    shaped, start = bot._monitor_lane_rows([GF3, GF3L])
    assert start == EPOCH_START
    assert len(shaped[GF3]) == 1
    assert len(shaped[GF3L]) == 1
    gf3_stats = monitor_api.lane_stats(shaped[GF3])
    gf3l_stats = monitor_api.lane_stats(shaped[GF3L])
    assert gf3_stats["closes"] == 1
    assert gf3l_stats["closes"] == 1
    assert gf3_stats["last_trade_at"] is not None
    assert gf3l_stats["last_trade_at"] is not None
    assert abs(gf3_stats["net_usd"] - 0.075) < 1e-6
    assert abs(gf3l_stats["net_usd"] - 0.0625) < 1e-6


def test_monitor_lanes_payload_counts_self_sim_closes(tmp_path, monkeypatch):
    gf3_path = tmp_path / "gf3_paper_ledger.jsonl"
    gf3l_path = tmp_path / "gf3l_paper_ledger.jsonl"
    gf3_path.write_text(json.dumps(_close_row(tile_fade3, fill_ts=EPOCH_START + 10.0)) + "\n", encoding="utf-8")
    gf3l_path.write_text(json.dumps(_close_row(tile_fade3_loose, fill_ts=EPOCH_START + 20.0)) + "\n", encoding="utf-8")
    monkeypatch.setattr(bot, "_gf3_ledger_path", lambda: str(gf3_path))
    monkeypatch.setattr(bot, "_gf3l_ledger_path", lambda: str(gf3l_path))
    monkeypatch.setattr(bot, "trades", [])
    monkeypatch.setattr(bot, "_showcase_trade_session_start", lambda: EPOCH_START)
    monkeypatch.setattr(bot, "_current_epoch_boundary", lambda force=False: (EPOCH_START, "data_epoch"))

    payload = bot._monitor_lanes_payload(EPOCH_START + 100.0)
    assert "gf3_paper_ledger.jsonl" in payload["scope"]["self_sim_paper_ledgers"]
    by_lane = {row["lane"]: row for row in payload["lanes"]}
    assert by_lane[GF3]["closes"] == 1
    assert by_lane[GF3L]["closes"] == 1
    assert by_lane[GF3]["last_trade_at"] is not None
    assert by_lane[GF3L]["last_trade_at"] is not None


def test_session_lane_ledger_overlays_self_sim_closes(tmp_path, monkeypatch):
    """Pathway cards / tile_route_counts.closed read session_lane_ledger — must include gf3/gf3l."""
    gf3_path = tmp_path / "gf3_paper_ledger.jsonl"
    gf3l_path = tmp_path / "gf3l_paper_ledger.jsonl"
    gf3_path.write_text(json.dumps(_close_row(tile_fade3, fill_ts=EPOCH_START + 50.0, pnl_bp=30.0)) + "\n",
                        encoding="utf-8")
    gf3l_path.write_text(json.dumps(_close_row(tile_fade3_loose, fill_ts=EPOCH_START + 60.0, pnl_bp=25.0)) + "\n",
                         encoding="utf-8")
    monkeypatch.setattr(bot, "_gf3_ledger_path", lambda: str(gf3_path))
    monkeypatch.setattr(bot, "_gf3l_ledger_path", lambda: str(gf3l_path))
    monkeypatch.setattr(bot, "trades", [])

    count, realized, ledger = bot._session_trade_accounting_locked(EPOCH_START, trades_src=[])
    assert count == 2
    assert ledger[GF3]["closes"] == 1
    assert ledger[GF3L]["closes"] == 1
    assert abs(ledger[GF3]["net_pnl_usd"] - 0.075) < 1e-6
    # Session headline PnL is cents-rounded (same as shared-book accounting).
    assert realized == round(0.075 + 0.0625, 2)


def test_reporting_overlay_does_not_change_fade3_entry_gates():
    """Soft B N: this fix is reporting-only — LOOSE/E8 thresholds stay pinned."""
    assert tile_fade3.Z_ENTRY == 2.5 and tile_fade3.MOM_ATR_MULT == 2.0
    assert tile_fade3.IMB_MIN == 0.2 and tile_fade3.SPREAD_MAX_BP == 2.0
    assert tile_fade3_loose.Z_ENTRY == 1.75 and tile_fade3_loose.MOM_ATR_MULT == 1.25
    assert tile_fade3_loose.IMB_MIN == 0.10 and tile_fade3_loose.SPREAD_MAX_BP == 3.0
    assert tile_fade3_loose.TP_BP == 25.0 and tile_fade3.TP_BP == 30.0
    src = Path(bot.__file__).read_text(encoding="utf-8")
    assert "def _self_sim_paper_trade_rows" in src
    assert "Soft B" in src or "self_sim_paper_ledgers" in src

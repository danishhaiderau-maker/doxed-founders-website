"""Daily-5 (3): /api/monitor/tiles/totals must count FADE3 self-sim closes.

R1: `_monitor_tiles_rows` previously read only the shared ``trades`` book, so
FADE3_TP30 / FADE3_LOOSE_TP25 totals stayed 0 while ``tile_route_counts.closed``
(from the session ledger overlay) showed real closes. Fix: overlay the same
`_self_sim_paper_trade_rows` basis used by lanes / route_counts (R4).
"""
from __future__ import annotations

import json

import bot
import monitor_tiles as mt
import tile_fade3
import tile_fade3_loose

GF3 = tile_fade3.LANE
GF3L = tile_fade3_loose.LANE
EPOCH_START = 1_791_100_000.0


def _close_row(mod, *, fill_ts: float, side: str = "SHORT", pnl_bp: float = 30.0):
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
        "exit_reason": "TP",
        "pnl_bp": pnl_bp,
        "pnl_usd": pnl_bp / 1e4 * notional,
        "exit_fill": {"fill_ts": fill_ts, "price": exit_px, "status": "FILLED"},
        "paper_only": True,
    }


def test_monitor_tiles_rows_includes_fade3_private_closes(tmp_path, monkeypatch):
    gf3_path = tmp_path / "gf3_paper_ledger.jsonl"
    gf3l_path = tmp_path / "gf3l_paper_ledger.jsonl"
    # Six TP30 closes + twenty LOOSE closes (symptom scale from daily-5).
    gf3_path.write_text(
        "".join(
            json.dumps(_close_row(tile_fade3, fill_ts=EPOCH_START + 100.0 + i)) + "\n"
            for i in range(6)
        ),
        encoding="utf-8",
    )
    gf3l_path.write_text(
        "".join(
            json.dumps(_close_row(tile_fade3_loose, fill_ts=EPOCH_START + 200.0 + i, pnl_bp=25.0))
            + "\n"
            for i in range(20)
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "_gf3_ledger_path", lambda: str(gf3_path))
    monkeypatch.setattr(bot, "_gf3l_ledger_path", lambda: str(gf3l_path))
    monkeypatch.setattr(bot, "trades", [])
    monkeypatch.setattr(bot, "open_positions", [])
    monkeypatch.setattr(bot, "pending_orders", [])
    monkeypatch.setattr(bot, "expired_orders", [])
    monkeypatch.setattr(bot, "_tile_fill_index_snapshot", lambda: {})
    monkeypatch.setattr(bot, "_monitor_tile_short_names", lambda: {})

    scope = {
        "lanes": [GF3, GF3L],
        "epoch": "ce-test",
        "since": EPOCH_START,
        "until": None,
        "fresh_only": False,
        "be_band_bp": 0.0,
    }
    by_lane = bot._monitor_tiles_rows(scope, ("closed",))
    assert len(by_lane[GF3]) == 6
    assert len(by_lane[GF3L]) == 20

    route_counts = {GF3: {"closed": 6}, GF3L: {"closed": 20}}
    view = mt.totals_view(
        {k: [r for r in v if r.get("status") == "CLOSED"] for k, v in by_lane.items()},
        include_forced=False,
        be_band_bp=0.0,
        route_counts=route_counts,
    )
    assert view["lanes"][GF3]["closes"] == 6
    assert view["lanes"][GF3L]["closes"] == 20
    assert view["parity"][GF3]["tile_route_counts_match"] is True
    assert view["parity"][GF3L]["tile_route_counts_match"] is True


def test_mutation_skipping_self_sim_overlay_breaks_fade3_parity(tmp_path, monkeypatch):
    """Mutation: if closed overlay is skipped, FADE3 totals stay 0 vs route_counts."""
    gf3_path = tmp_path / "gf3_paper_ledger.jsonl"
    gf3_path.write_text(
        json.dumps(_close_row(tile_fade3, fill_ts=EPOCH_START + 10.0)) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "_gf3_ledger_path", lambda: str(gf3_path))
    monkeypatch.setattr(bot, "_gf3l_ledger_path", lambda: str(tmp_path / "missing.jsonl"))
    monkeypatch.setattr(bot, "trades", [])
    monkeypatch.setattr(bot, "open_positions", [])
    monkeypatch.setattr(bot, "pending_orders", [])
    monkeypatch.setattr(bot, "expired_orders", [])
    monkeypatch.setattr(bot, "_tile_fill_index_snapshot", lambda: {})
    monkeypatch.setattr(bot, "_monitor_tile_short_names", lambda: {})
    monkeypatch.setattr(bot, "_self_sim_paper_trade_rows", lambda *_a, **_k: [])

    scope = {
        "lanes": [GF3],
        "epoch": "ce-test",
        "since": EPOCH_START,
        "until": None,
        "fresh_only": False,
        "be_band_bp": 0.0,
    }
    by_lane = bot._monitor_tiles_rows(scope, ("closed",))
    view = mt.totals_view(
        {GF3: [r for r in by_lane[GF3] if r.get("status") == "CLOSED"]},
        include_forced=False,
        be_band_bp=0.0,
        route_counts={GF3: {"closed": 1}},
    )
    assert view["lanes"][GF3]["closes"] == 0
    assert view["parity"][GF3]["tile_route_counts_match"] is False

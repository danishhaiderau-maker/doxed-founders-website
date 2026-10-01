"""Win % on every registry tile: same ledger as PnL, wins = net PnL > 0 after costs."""
import csv
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import bot
import tile_paired_comparison as tpc
from combo_pathway_config import ACTIVE_TILE_ORDER, ACTIVE_TILE_REGISTRY

BOT_SOURCE = Path(__file__).with_name("bot.py").read_text(encoding="utf-8")
DASH_SOURCE = (Path(__file__).parent / "research" / "research_dashboard.py").read_text(encoding="utf-8")


def _render_chunk() -> str:
    start = BOT_SOURCE.index("function renderPathwayLab")
    return BOT_SOURCE[start:BOT_SOURCE.index("function renderPathwayScorecard", start)]


def test_win_rate_is_wins_over_closed_and_none_without_closes():
    assert bot._win_rate_pct(2, 5) == 40.0
    assert bot._win_rate_pct(0, 0) is None


def test_session_stats_carry_ledger_wins_and_losses_from_the_pnl_ledger():
    trades = [
        {"research_lane": ACTIVE_TILE_ORDER[0], "net_pnl_usd": value}
        for value in (0.05, -0.02, 0.01, -0.03, -0.01)
    ]
    lb = bot._derive_lane_pnl_ledger_from_trades(trades)[ACTIVE_TILE_ORDER[0]]
    stats = bot._session_stats_from_lane_metrics({
        "approves": 6, "real_fills": lb["closes"], "net_pnl_real": lb["net_pnl_usd"],
        "wins": lb["wins"], "losses": lb["losses"],
    })
    assert (stats["wins"], stats["losses"], stats["real_fills"]) == (2, 3, 5)
    assert stats["win_rate_pct"] == 40.0
    assert stats["net_pnl_real"] == round(0.05 - 0.02 + 0.01 - 0.03 - 0.01, 2)
    empty = bot._session_stats_from_lane_metrics({"approves": 3, "real_fills": 0, "win_rate_pct": 0.0})
    assert empty["win_rate_pct"] is None and empty["wins"] == 0


def test_settings_periods_count_wins_from_the_same_rows_as_period_pnl(tmp_path, monkeypatch):
    lane = ACTIVE_TILE_ORDER[-1]
    start = 1_790_000_000.0
    history = tmp_path / "history.jsonl"
    history.write_text(json.dumps({"epoch": start, "signature": "s1", "gap_buckets": [5], "chase_buckets": []}) + "\n",
                       encoding="utf-8")
    trades = tmp_path / "trades.csv"
    with trades.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["research_lane", "ts", "net_pnl_usd"])
        writer.writeheader()
        for i, pnl in enumerate((0.04, -0.02, 0.0, -0.01)):
            ts = datetime.fromtimestamp(start + 60 * (i + 1), tz=timezone.utc).isoformat()
            writer.writerow({"research_lane": lane, "ts": ts, "net_pnl_usd": pnl})
    monkeypatch.setattr(bot, "EXECUTION_SETTINGS_HISTORY_FILE", str(history))
    monkeypatch.setattr(bot, "CSV_TRADES", str(trades))
    monkeypatch.setattr(bot, "_fresh_collection_start_epoch", lambda: start)
    monkeypatch.setattr(bot, "_settings_breakdown_cache", {"key": None, "value": {}})
    periods = bot._settings_period_breakdown()
    assert set(periods) == set(ACTIVE_TILE_ORDER)
    current = [row for row in periods[lane] if row["current"]][0]
    assert current["executed"] == 4 and current["pnl_usd"] == 0.01
    assert (current["wins"], current["losses"], current["win_rate_pct"]) == (1, 2, 25.0)
    other = [row for row in periods[ACTIVE_TILE_ORDER[0]] if row["current"]][0]
    assert other["executed"] == 0 and other["win_rate_pct"] is None


def test_fly_card_and_settings_table_render_win_pct_generically():
    chunk = _render_chunk()
    assert "statRow('Win %', headlineWinLabel)" in chunk
    start = chunk.index("const winPctLabel = function")
    source = chunk[start:chunk.index("};", start) + 2]
    script = source + ";console.log(JSON.stringify([winPctLabel(2, 3, 5), winPctLabel(12, 9, 27), winPctLabel(0, 0, 0)]))"
    labels = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True).stdout)
    assert labels == ["40% (2W/3L)", "44% (12W/9L) · 6 flat", "—"]
    assert "if (!(n > 0)) return '—';" in chunk
    assert "winPctLabel(period.wins, period.losses, period.executed)" in chunk
    assert '<th style="padding:5px;text-align:right;">Win %</th>' in chunk
    for lane in ACTIVE_TILE_ORDER:
        assert lane not in chunk


def test_analyzer_lane_rows_and_paired_panel_show_win_pct():
    assert "<th>Executed EV / approval</th><th>Win %</th>" in DASH_SOURCE
    assert "laneWinMetric(current, row)" in DASH_SOURCE
    assert "winPctLabel(s.wins, s.losses, s.fills)" in DASH_SOURCE
    assert "winPctLabel(p.control_wins, p.control_losses, p.paired_signals)" in DASH_SOURCE
    assert '"wins", "losses", "win_rate_pct",' in DASH_SOURCE


def test_paired_comparison_reports_win_counts_and_excludes_unfilled_rows():
    t1, t2 = ACTIVE_TILE_ORDER[0], ACTIVE_TILE_ORDER[1]
    base = 1_790_000_000.0
    rows = []
    for i, (a, b) in enumerate(((-0.01, 0.02), (0.01, 0.03), (-0.02, -0.01), (0.0, 0.01), (-0.03, 0.02))):
        rows.append({"research_lane": t1, "shared_ai_call_id": f"c{i}", "net_pnl_usd": a, "close_ts": base + i})
        rows.append({"research_lane": t2, "shared_ai_call_id": f"c{i}", "net_pnl_usd": b, "close_ts": base + i})
    rows.append({"research_lane": t2, "shared_ai_call_id": "nf", "net_pnl_usd": 0.0, "close_ts": base,
                 "exit_reason": "NO_FILL"})
    report = tpc.build_report(trades=rows, registry=ACTIVE_TILE_REGISTRY, tile_order=ACTIVE_TILE_ORDER,
                              now_ts=base + 3600)
    s1, s2 = report["tiles"][t1], report["tiles"][t2]
    assert (s1["fills"], s1["wins"], s1["losses"], s1["win_rate_pct"]) == (5, 1, 3, 20.0)
    assert (s2["fills"], s2["wins"], s2["losses"], s2["win_rate_pct"]) == (5, 4, 1, 80.0)
    pair = next(p for p in report["paired"] if p["control"] == t1 and p["challenger"] == t2)
    assert (pair["control_wins"], pair["control_losses"]) == (1, 3)
    assert (pair["challenger_wins"], pair["challenger_losses"], pair["challenger_win_rate_pct"]) == (4, 1, 80.0)
    assert report["tiles"][ACTIVE_TILE_ORDER[-1]] == {"fills": 0}

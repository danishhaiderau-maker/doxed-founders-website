"""PR-D: offline per-signal research table + walk-forward scorer, on a small synthetic fixture."""
from __future__ import annotations

import csv
import gzip
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from research import fill_model as fm
from research import signal_research_table as srt
from research import walk_forward_scorer as wfs

T0 = int(datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc).timestamp())  # 00:00 UTC = ASIA session


def tape_row(ts: int, bid: float, ask: float, **kw):
    row = {"schema": srt.TAPE_SCHEMA, "symbol": "tBTCF0:USTF0", "bucket_ts": ts, "source_ts": ts - 0.1,
           "source_age_sec": 0.1, "fresh": True, "valid_bbo": True, "bid": bid, "ask": ask, "bid_qty": 5.0,
           "ask_qty": 5.0, "last": (bid + ask) / 2, "spread_usd": ask - bid, "trade_count": 0, "buy_qty": 0.0,
           "sell_qty": 0.0, "buy_vwap": None, "sell_vwap": None, "trade_high": None, "trade_low": None}
    row.update(kw)
    return row


def ramp_rows(start: int, seconds: int, p0: float = 100000.0, slope: float = 1.0, spread: float = 2.0):
    """Mid rises ``slope`` USD per second; spread fixed."""
    out = []
    for k in range(seconds):
        mid = p0 + slope * k
        out.append(tape_row(start + k, mid - spread / 2, mid + spread / 2))
    return out


def write_jsonl(path: Path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


@pytest.fixture()
def fixture_dir(tmp_path: Path) -> Path:
    """Two rotations of a 5 h rising tape, three AI calls, one cross-venue trigger, one filled + one expired order."""
    rows = ramp_rows(T0, 5 * 3600, slope=0.01)
    write_jsonl(tmp_path / "market_microstructure_1s.jsonl.1", rows[:9000])
    write_jsonl(tmp_path / "market_microstructure_1s.jsonl", rows[8990:])  # overlapping seam
    write_jsonl(tmp_path / "decision_feature_snapshots.jsonl", [
        {"schema": "decision_feature_snapshot_v1", "row_kind": "SNAPSHOT", "shared_ai_call_id": "scan-a",
         "decision_ts": T0 + 600.4, "epoch_id": "ep1",
         "ai": {"decision": "APPROVE", "raw_direction": "LONG", "long_score": 80, "short_score": 20,
                "ai_committed": True, "ai_error": False}},
    ])
    write_jsonl(tmp_path / "adaptive_entry_decisions.jsonl", [
        {"shared_ai_call_id": "scan-b", "signal_ts": T0 + 1200.2, "lane": "X",
         "ai_feature": {"raw_decision": "REJECT", "raw_direction": "NO_TRADE", "long_score": 40, "short_score": 55}},
        {"shared_ai_call_id": "scan-c", "signal_ts": T0 + 1800.0, "lane": "X",
         "ai_feature": {"raw_decision": "APPROVE", "raw_direction": "SHORT", "long_score": 45, "short_score": 60}},
        {"shared_ai_call_id": "xvl-1", "signal_ts": T0 + 2400.0, "lane": "FAMILY_XVENUE_LEAD_60S",
         "direction": "LONG", "direction_source": "CROSS_VENUE_LEAD"},
    ])
    write_jsonl(tmp_path / "taker_signal_counterfactuals.jsonl", [
        {"shared_ai_call_id": "scan-b", "signal_ts": T0 + 1200.2, "direction": "SHORT", "raw_ai_decision": "REJECT"},
    ])
    with open(tmp_path / "trades_3factor.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["trade_id", "research_lane", "dir", "shared_ai_call_ts", "pnl", "entry",
                                           "epoch_id", "execution_entry_type", "shared_ai_call_id"])
        w.writeheader()
        w.writerow({"trade_id": "cft-1", "research_lane": "FAMILY_COMMITTED_FADE_TAKER_90", "dir": "SHORT",
                    "shared_ai_call_ts": datetime.fromtimestamp(T0 + 600.4, tz=timezone.utc).isoformat(),
                    "pnl": "-3.5", "entry": "100006", "epoch_id": "ep1", "execution_entry_type": "TAKER",
                    "shared_ai_call_id": "scan-a"})
    with open(tmp_path / "expired_orders_3factor.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["trade_id", "research_lane", "dir", "created_ts", "limit_price"])
        w.writeheader()
        w.writerow({"trade_id": "ntf-1", "research_lane": "FAMILY_NOTRADE_FOLLOW_MAKER_60", "dir": "SHORT",
                    "created_ts": datetime.fromtimestamp(T0 + 1200.2, tz=timezone.utc).isoformat(),
                    "limit_price": "100030"})
    return tmp_path


# ------------------------------------------------------------------ table

def test_tape_merges_rotations_and_forward_fills(fixture_dir):
    tape = srt.load_tape(fixture_dir)
    assert tape.t0 == T0 and tape.t1 == T0 + 5 * 3600 - 1
    assert tape.n_rows == 5 * 3600 and tape.valid.all()
    assert tape.rows()[T0 + 5]["bid"] == pytest.approx(100000.05 - 1.0)


def test_signals_merge_sources_and_commit_rule(fixture_dir):
    sigs = {s["signal_id"]: s for s in srt.load_signals(fixture_dir)}
    assert set(sigs) == {"scan-a", "scan-b", "scan-c", "xvl-1", "trade:cft-1", "expired:ntf-1"}
    a, b, c = sigs["scan-a"], sigs["scan-b"], sigs["scan-c"]
    assert a["ai_committed"] and a["ai_explicit_aligned"] and a["direction"] == "LONG"
    assert b["explicit_abstain"] and b["score_led_side"] == "SHORT" and b["direction"] == "NONE"
    assert b["counterfactual_direction"] == "SHORT" and b["sources"] == {"adaptive_entry_decisions",
                                                                          "taker_signal_counterfactuals"}
    # gap 15 < 30: aligned with the score-led side but not committed under the bot's telemetry rule
    assert c["ai_explicit_aligned"] and not c["ai_committed"]
    assert sigs["xvl-1"]["signal_kind"] == "XVENUE_SIGNAL" and sigs["xvl-1"]["direction"] == "LONG"
    assert sigs["trade:cft-1"]["tile_filled"] is True and sigs["trade:cft-1"]["tile_recorded_pnl_bp"] == -3.5
    assert sigs["expired:ntf-1"]["tile_filled"] is False


def test_ai_commit_flags_match_bot_rule():
    assert srt.ai_commit_flags("LONG", 70, 40)["ai_committed"] is True       # gap 30
    assert srt.ai_commit_flags("LONG", 69, 40)["ai_committed"] is False      # gap 29
    assert srt.ai_commit_flags("LONG", 30, 70)["score_direction_mismatch"] is True
    assert srt.ai_commit_flags("NO_TRADE", 50, 50)["score_tie"] is True
    assert srt.ai_commit_flags("SHORT", 10, 90, ai_error=True)["ai_committed"] is False


def test_forward_paths_horizons_mfe_mae_and_taker_marks(fixture_dir):
    tape = srt.load_tape(fixture_dir)
    sig = {"signal_id": "scan-a", "signal_kind": "AI_CALL", "signal_ts": T0 + 600.4, "direction": "SHORT"}
    row = srt.build_row(sig, tape)
    assert row["tape_status"] == "OK" and row["anchor_ts"] == T0 + 601 and row["session"] == "ASIA"
    mid0 = 100000.0 + 0.01 * 601
    assert row["anchor_mid"] == pytest.approx(mid0)
    for label, sec in srt.HORIZONS:
        exp = 0.01 * sec / mid0 * 1e4
        assert row[f"mid_{label}_bp"] == pytest.approx(exp, abs=1e-3)
        assert row[f"mfe_up_{label}_bp"] == pytest.approx(exp, abs=1e-3)   # monotone rise: MFE = end
        assert row[f"mae_dn_{label}_bp"] == pytest.approx(0.0, abs=1e-9)
        # SHORT signal: direction-signed path is the negative of the mid path; MAE is the rise
        assert row[f"dir_{label}_bp"] == pytest.approx(-exp, abs=1e-3)
        assert row[f"dir_mae_{label}_bp"] == pytest.approx(-exp, abs=1e-3)
        assert row[f"dir_mfe_{label}_bp"] == pytest.approx(0.0, abs=1e-9)
    # taker round trip pays the 2 USD spread: long buys the ask, sells the bid
    assert row["long_taker_1s_bp"] == pytest.approx((0.01 - 2.0) / (mid0 + 1) * 1e4, abs=1e-3)
    assert row["short_taker_1s_bp"] == pytest.approx(-(0.01 + 2.0) / (mid0 - 1) * 1e4, abs=1e-3)


def test_paths_null_past_tape_end_and_over_holes(tmp_path):
    rows = ramp_rows(T0, 1000)
    rows = rows[:300] + rows[400:]  # 100 s hole (> PATH_MAX_QUOTE_AGE_SEC)
    tape = srt.Tape(rows)
    row = srt.build_row({"signal_id": "s", "signal_kind": "AI_CALL", "signal_ts": T0 + 200, "direction": "LONG"},
                        tape)
    assert row["mid_1m_bp"] is not None
    assert row["path_complete_sec"] == 159      # last quote T0+299; older than 60 s from T0+360
    assert row["mid_3m_bp"] is None             # T0+380 sits in the hole (age 81 s)
    assert row["mid_5m_bp"] is not None         # T0+500 is after the hole
    assert row["mid_15m_bp"] is None            # past the tape end
    out = srt.build_row({"signal_id": "x", "signal_kind": "AI_CALL", "signal_ts": T0 - 50}, tape)
    assert out["tape_status"] == "OUTSIDE_TAPE"


def test_cli_end_to_end_writes_table_and_manifest(fixture_dir, tmp_path):
    out = tmp_path / "out"
    assert srt.main(["--data-dir", str(fixture_dir), "--out-dir", str(out)]) == 0
    rows = srt.read_table(out / "per_signal_research_table.csv.gz")
    man = json.loads((out / "per_signal_research_table.manifest.json").read_text())
    assert len(rows) == 6 and man["rows"] == 6 and man["table_schema"] == srt.TABLE_SCHEMA
    assert list(rows[0].keys()) == srt.table_columns()
    assert man["fill_model"]["fill_model"] == "REALISTIC_V1" and man["paper_only"] is True
    assert [h["label"] for h in man["horizons"]] == ["1s", "5s", "15s", "30s", "1m", "3m", "5m", "15m", "30m",
                                                     "60m", "90m", "120m", "240m"]
    assert all(r["tape_status"] == "OK" for r in rows)


def test_tool_is_not_imported_by_runtime():
    agent = Path(__file__).resolve().parent
    for name in ("bot.py", "fly-entrypoint.sh"):
        p = agent / name
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="replace")
            assert "signal_research_table" not in text and "walk_forward_scorer" not in text


# ------------------------------------------------------------------ scorer: entries and exits

def _flat_tape(overrides=None, seconds=4000, bid=100000.0, ask=100002.0):
    rows = [tape_row(T0 + k, bid, ask) for k in range(seconds)]
    for k, kw in (overrides or {}).items():
        rows[k] = tape_row(T0 + k, **kw)
    return srt.Tape(rows)


def _row(ts, kind="AI_CALL", **kw):
    r = {"signal_id": f"s{ts}", "signal_kind": kind, "signal_ts": str(ts), "utc_day": "2026-10-01",
         "tape_status": "OK", "anchor_mid": "100001.0", "session": "ASIA"}
    r.update(kw)
    return r


def test_taker_entry_is_fill_model_taker():
    tape = _flat_tape()
    rows = tape.rows()
    ent = wfs.simulate_entry(tape, rows, _row(T0 + 10.2), "LONG", wfs.Entry("TAKER", "TAKER"), 1.5)
    ref = fm.taker_fill_rows(rows, side="LONG", qty=25 / 100001.0, decision_ts=T0 + 10.2, latency_sec=1.5)
    assert ent["status"] == "FILLED" and ent["fill_price"] == ref["fill_price"] == 100002.0
    assert ent["fill_ts"] == T0 + 12


def test_maker_needs_trade_through_not_touch():
    entry = wfs.Entry("MAKER_TOUCH_5M", "MAKER", ttl_sec=300)
    tape = _flat_tape()  # touch never moves, no prints -> REALISTIC_V1 never fills a resting bid
    assert wfs.simulate_entry(tape, tape.rows(), _row(T0 + 10), "LONG", entry, 1.0)["status"] == "NO_FILL"
    # a sell print below our 100000 bid at T0+100 proves a trade-through
    tape = _flat_tape({100: dict(bid=99999.0, ask=100001.0, sell_qty=0.5, sell_vwap=99999.5)})
    ent = wfs.simulate_entry(tape, tape.rows(), _row(T0 + 10), "LONG", entry, 1.0)
    assert ent["status"] == "FILLED" and ent["liquidity"] == "MAKER" and ent["fill_price"] == 100000.0
    assert ent["basis"] == "TRADE_THROUGH" and ent["fill_ts"] == T0 + 100


def test_maker_offset_limit_is_passive_and_never_past_touch():
    entry = wfs.Entry("MAKER_10BP_30M", "MAKER", ttl_sec=1800, offset_bp=10.0)
    tape = _flat_tape({500: dict(bid=99880.0, ask=99882.0, sell_qty=1.0, sell_vwap=99880.0)})
    ent = wfs.simulate_entry(tape, tape.rows(), _row(T0 + 10), "LONG", entry, 1.0)
    # 10 bp below last 100001 = 99900.999 -> rounded passively down to 99900
    assert ent["limit_price"] == 99900.0 and ent["status"] == "FILLED" and ent["fill_price"] == 99900.0


def test_exit_stop_booked_worse_of_trigger_and_latency():
    # LONG at 100000; bid falls to 99590 (-41 bp) at +50 s then recovers -> stop at trigger mark (worse)
    tape = _flat_tape({60: dict(bid=99590.0, ask=99592.0)})
    ex = wfs.simulate_exit(tape, "LONG", T0 + 10, 100000.0, wfs.Exit("S", 5400, stop_bp=40.0))
    assert ex["exit_reason"] == "HARD_STOP" and ex["hold_sec"] == 50 and ex["gross_bp"] == pytest.approx(-41.0)
    assert (ex["gross_bp"], ex["hold_sec"]) == fm.realistic_exit_margin(
        np.array([0.0] * 50 + [-41.0, 0.0]), np.arange(52.0), 50, "HARD_STOP", latency_sec=fm.EXIT_LATENCY_SEC)


def test_exit_breakeven_trail_earlycut_and_time():
    up = {k: dict(bid=100000.0 + 300 * min(k - 100, 1), ask=100002.0 + 300 * min(k - 100, 1))
          for k in range(100, 110)}  # +30 bp at T0+101..109
    up.update({k: dict(bid=100040.0, ask=100042.0) for k in range(110, 4000)})  # back to +4 bp
    tape = _flat_tape(up)
    be = wfs.simulate_exit(tape, "LONG", T0 + 10, 100000.0,
                           wfs.Exit("BE", 5400, stop_bp=40.0, be_arm_bp=20.0, be_lock_bp=5.0))
    # triggers at +100 s; the post-latency mark is equal, so REALISTIC_V1 books it one second later
    assert be["exit_reason"] == "BREAKEVEN_LOCK" and be["hold_sec"] == 101 and be["gross_bp"] == pytest.approx(4.0)
    tr = wfs.simulate_exit(tape, "LONG", T0 + 10, 100000.0,
                           wfs.Exit("TR", 5400, stop_bp=40.0, trail_arm_bp=15.0, trail_bp=10.0))
    assert tr["exit_reason"] == "ATR_TRAIL" and tr["gross_bp"] == pytest.approx(4.0)
    tm = wfs.simulate_exit(tape, "LONG", T0 + 10, 100000.0, wfs.Exit("T", 900))
    assert tm["exit_reason"] == "TIME_EXIT" and tm["hold_sec"] == 900 and tm["gross_bp"] == pytest.approx(4.0)
    down = _flat_tape({k: dict(bid=99870.0, ask=99872.0) for k in range(60, 4000)})  # -13 bp, never above 0
    ec = wfs.simulate_exit(down, "LONG", T0 + 10, 100000.0,
                           wfs.Exit("EC", 5400, stop_bp=40.0, early_cut_bp=12.0, early_cut_window_sec=300,
                                    early_cut_max_mfe_bp=2.0))
    assert ec["exit_reason"] == "EARLY_CUT" and ec["hold_sec"] == 51 and ec["gross_bp"] == pytest.approx(-13.0)
    assert wfs.simulate_exit(down, "LONG", T0 + 3500, 100000.0, wfs.Exit("T", 900))["status"] == "CENSORED_TAPE_END"


def test_short_exit_uses_ask():
    tape = _flat_tape(bid=100000.0, ask=100010.0)
    ex = wfs.simulate_exit(tape, "SHORT", T0 + 10, 100000.0, wfs.Exit("T", 60))
    assert ex["gross_bp"] == pytest.approx(-1.0)  # bought back at the ask, 10 USD above the entry


# ------------------------------------------------------------------ scorer: stats and walk-forward

def _trade(vid, day, ts, bp):
    rule, entry, exit_ = vid.split("|")
    return {"variant_id": vid, "side_rule": rule, "entry": entry, "exit": exit_, "utc_day": day, "signal_ts": ts,
            "entry_status": "FILLED", "exit_status": "CLOSED", "net_bp": bp, "exit_reason": "TIME_EXIT",
            "hold_sec": 60, "liquidity": "TAKER"}


def test_walk_forward_selects_on_past_days_only():
    days = ["2026-10-01", "2026-10-02", "2026-10-03"]
    trades = []
    for k, d in enumerate(days):
        base = T0 + k * 86400
        good_day1 = 5.0 if k == 0 else -5.0     # A wins day 1 only, B wins days 2-3
        for i in range(40):
            trades.append(_trade("R|E|A", d, base + i * 600, good_day1))
            trades.append(_trade("R|E|B", d, base + i * 600, -good_day1 * 0.5))
    res = wfs.score(trades, min_train=30)
    steps = res["walk_forward"]["rule:R"]["steps"]
    assert [s["chosen"] for s in steps] == ["R|E|A", "R|E|A"]   # day-1 winner; then A still leads on days 1-2 pooled
    assert steps[0]["test_mean_bp"] == -5.0
    oos = res["walk_forward"]["rule:R"]["oos"]
    assert oos["closed"] == 80 and oos["mean_bp"] == -5.0      # in-sample A looks flat, OOS shows the decay
    v = res["variants"]["R|E|B"]
    assert v["days"] == 3 and v["days_positive"] == 2 and v["fill_rate"] == 1.0


def test_summary_stats_and_cluster_ci():
    trades = [_trade("R|E|A", "2026-10-01", T0 + i * 1200, float(i % 5 - 1)) for i in range(60)]
    trades += [{"variant_id": "R|E|A", "side_rule": "R", "entry": "E", "exit": "A", "utc_day": "2026-10-01",
                "signal_ts": T0, "entry_status": "NO_FILL"}]
    s = wfs.summarize(trades)
    assert s["signals"] == 61 and s["fills"] == 60 and s["fill_rate"] == pytest.approx(60 / 61, abs=1e-4)
    assert s["mean_bp"] == pytest.approx(1.0) and s["hit_rate"] == pytest.approx(0.6)
    lo, hi = s["ci95_1h_cluster"]
    assert lo < 1.0 < hi
    assert wfs.cluster_bootstrap_ci([1.0, 2.0], ["h", "h"]) == (None, None)


def test_scorer_end_to_end_on_fixture(fixture_dir, tmp_path):
    out = tmp_path / "t"
    srt.main(["--data-dir", str(fixture_dir), "--out-dir", str(out)])
    res = wfs.run(out / "per_signal_research_table.csv.gz", fixture_dir, tmp_path / "s",
                  latency={"source": "TEST", "latency_sec": 1.0})
    assert res["schema"] == wfs.SCORER_SCHEMA and res["fill_model"]["fill_model"] == "REALISTIC_V1"
    v = res["variants"]["AI_COMMITTED_FOLLOW|TAKER|TIME_60M"]
    # one committed LONG (scan-a) + one aligned SHORT (scan-c) on a steady rise: long wins, short loses
    assert v["signals"] == 2 and v["closed"] == 2
    trades = [json.loads(l) for l in gzip.open(tmp_path / "s" / "walk_forward_trades.jsonl.gz", "rt")]
    by = {t["signal_id"]: t for t in trades if t["variant_id"] == "AI_COMMITTED_FOLLOW|TAKER|TIME_60M"}
    assert by["scan-a"]["net_bp"] > 0 > by["scan-c"]["net_bp"]
    assert (tmp_path / "s" / "walk_forward_scores.md").read_text().startswith("# Walk-forward variant scores")
    # random-side ablation and NO_TRADE follow are always in the grid
    assert any(k.startswith("AI_RANDOM_SIDE|") for k in res["variants"])
    assert "NOTRADE_SCORE_FOLLOW|TAKER|TIME_60M" in res["variants"]
    assert all(math.isfinite(t.get("net_bp", 0.0)) for t in trades)

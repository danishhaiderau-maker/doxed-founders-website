import pandas as pd

import analysis_hygiene as h
import late_window_reprice as lw


def test_forced_and_late_window_excluded_and_bp_added():
    late_id = next(iter(lw.DATA["trades"]))
    df = pd.DataFrame([
        {"trade_id": "a", "exit_reason": "BREAKEVEN_LOCK", "net_pnl_usd": 0.01, "notional_usd": 25.0, "close_ts": 1.0},
        {"trade_id": "b", "exit_reason": "ADMIN_MANUAL_CLOSE", "net_pnl_usd": -0.5, "notional_usd": 25.0, "close_ts": 1.0},
        {"trade_id": "c", "exit_reason": "DEPLOY_FORCE_FLAT", "net_pnl_usd": -0.1, "notional_usd": 25.0, "close_ts": 1.0},
        {"trade_id": late_id, "exit_reason": "BREAKEVEN_LOCK", "net_pnl_usd": 0.0125, "notional_usd": 25.0, "close_ts": 1.0},
        {"trade_id": "e", "exit_reason": "PATH_END_60M", "net_pnl_usd": 0.0, "notional_usd": 25.0,
         "close_ts": h.LATE_W0 + 10},
    ])
    out = h.apply(df, include_forced=False, include_late=False)
    assert list(out["trade_id"]) == ["a"]
    assert abs(out["net_bp"].iloc[0] - 4.0) < 1e-9
    assert h.SUMMARY["forced_excluded"] == 2 and h.SUMMARY["late_window_excluded"] == 2
    assert len(h.apply(df, include_forced=True, include_late=True)) == 5


def test_all_epochs_study_reads_archive_and_disloc(tmp_path):
    import json
    from realism_v2_fixture import V2_STAMPS
    cur = pd.DataFrame([{**V2_STAMPS, "trade_id": "a", "research_lane": "CTRL", "exit_reason": "X", "net_pnl_usd": 0.01,
                         "notional_usd": 25, "close_ts": 3600.0,
                             "fill_model_version": 2, "exit_status": "PAPER_FILL",
                             "fill_model": "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY"}])
    cur.to_csv(tmp_path / "trades_3factor.csv", index=False)
    old = tmp_path / "research_archive" / "pre_epoch" / "ce-old"
    old.mkdir(parents=True)
    pd.DataFrame([{"trade_id": "b", "research_lane": "CTRL", "exit_reason": "X", "net_pnl_usd": -0.005,
                   "notional_usd": 25, "close_ts": 7200.0},
                  {"trade_id": "c", "research_lane": "CTRL", "exit_reason": "ADMIN_MANUAL_CLOSE", "net_pnl_usd": -1,
                   "notional_usd": 25, "close_ts": 7200.0}]).to_csv(old / "trades_3factor.csv", index=False)
    (tmp_path / "disloc_trades.jsonl").write_text(json.dumps(
        {"event": "DISLOC_CLOSED", "pnl_bp_vs_touch": 2.0, "exit_ts": 100.0, "lane": "DISLOC"}) + "\n")
    out = h.all_epochs_bp_study(str(tmp_path))
    assert out["by_tile"]["CTRL"]["n"] == 2 and out["by_tile"]["DISLOC"]["n"] == 1
    assert set(out["by_epoch"]) == {"current_file", "ce-old", "disloc_store"}
    assert out["hygiene"]["forced_excluded"] == 1

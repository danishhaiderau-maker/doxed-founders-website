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

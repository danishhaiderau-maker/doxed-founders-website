from research.fill_time_guard_counterfactual import build_fill_time_guard_counterfactual


def _tape(start=1000):
    return [
        {"bucket_ts": start, "last": 100.0, "buy_qty": 1.0, "sell_qty": 3.0},
        {"bucket_ts": start + 30, "last": 100.2, "buy_qty": 4.0, "sell_qty": 1.0},
        {"bucket_ts": start + 60, "last": 100.5, "buy_qty": 5.0, "sell_qty": 0.0},
    ]


def test_counterfactual_uses_pre_fill_features_and_clusters_siblings():
    trades = [
        {"trade_id": "a", "shared_ai_call_id": "scan-1", "dir": "SHORT", "net_pnl_usd": -2, "regime": "BEAR"},
        {"trade_id": "b", "shared_ai_call_id": "scan-1", "dir": "SHORT", "net_pnl_usd": 1, "regime": "BEAR"},
    ]
    executions = [
        {"event_id": key, "epoch_id": "epoch-1", "fill_ts": 1060, "fill_price": 100.5,
         "shared_ai_call_id": "scan-1"} for key in ("a", "b")
    ]
    ai = [{"trade_id": "scan-1", "features": {
        "ema9": 99, "ema21": 100, "ema200": 101, "plus_di": 10, "minus_di": 30, "adx": 20,
    }}]
    source = [{"canonical_trade_id": key, "fill_gate_verdict": "EXECUTABLE"} for key in ("a", "b")]
    report = build_fill_time_guard_counterfactual(
        trades=trades, executions=executions, ai_inputs=ai,
        tape_rows=_tape(), source_observations=source, epoch_id="epoch-1",
        adverse_thresholds_bps=(10,), momentum_horizons_sec=(60,),
        flow_windows_sec=(60,), short_confirmation_max=(0,),
    )
    assert report["observed_trades"] == 2
    assert report["independent_clusters"] == 1
    assert all(row["slow_ema_dmi_direction_confirmed"] for row in report["trade_features"])
    candidate = report["momentum_ema_dmi_candidates"][0]
    assert candidate["blocked"] == 2
    assert candidate["blocked_losers"] == 1
    assert candidate["blocked_winners"] == 1
    assert candidate["loss_avoided_usd"] == 2
    assert candidate["winner_opportunity_cost_usd"] == 1
    assert candidate["net_saved_usd"] == 1
    assert candidate["clusters"][0]["cluster_id"] == "scan-1"


def test_order_flow_confirmation_is_direction_normalized():
    common = {
        "executions": [
            {"event_id": "short", "epoch_id": "e", "fill_ts": 1060, "fill_price": 100.5},
            {"event_id": "long", "epoch_id": "e", "fill_ts": 1060, "fill_price": 100.5},
        ],
        "ai_inputs": [], "tape_rows": _tape(), "epoch_id": "e",
        "source_observations": [], "momentum_horizons_sec": (30,),
        "adverse_thresholds_bps": (999,), "flow_windows_sec": (30,),
        "short_confirmation_max": (0,),
    }
    report = build_fill_time_guard_counterfactual(
        trades=[
            {"trade_id": "short", "shared_ai_call_id": "s", "dir": "SHORT", "net_pnl_usd": -1},
            {"trade_id": "long", "shared_ai_call_id": "l", "dir": "LONG", "net_pnl_usd": 1},
        ], **common,
    )
    values = {row["trade_id"]: row["order_flow_adverse_imbalance"]["30"] for row in report["trade_features"]}
    assert values["short"] > 0
    assert values["long"] < 0
    assert report["microstructure_order_flow_candidates"][0]["blocked"] == 1


def test_wrong_epoch_and_missing_receipt_fail_closed():
    report = build_fill_time_guard_counterfactual(
        trades=[{"trade_id": "missing"}, {"trade_id": "wrong"}],
        executions=[{"event_id": "wrong", "epoch_id": "old", "fill_ts": 1, "fill_price": 2}],
        ai_inputs=[], tape_rows=[], epoch_id="current",
    )
    assert report["observed_trades"] == 0
    assert report["exclusions"] == {
        "EPOCH_ID_MISMATCH": 1,
        "FILL_EXECUTION_RECEIPT_MISSING": 1,
    }
    assert "FEWER_THAN_30_INDEPENDENT_CLUSTERS" in report["insufficiency"]

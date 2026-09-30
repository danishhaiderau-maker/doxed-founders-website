"""Fixed vs dynamic tile selector: walk-forward, no lookahead, honest NOT_ENOUGH_DATA."""
from research import fixed_vs_dynamic_selector as fds

EPOCH = "epoch-test"
T0 = 1_790_000_000.0
LANES = ("TILE_X", "TILE_Y")
REGISTRY = {"TILE_X": {"label": "X"}, "TILE_Y": {"label": "Y"}}
REGIMES = {"A": ("BEAR", 15.0, 0.05), "B": ("RANGE", 27.0, 0.20)}


def _trade(lane, pnl, decision, *, regime="A", hold=600.0, signal=None, exit_reason="TRAIL_STOP", sig="sig"):
    label, adx, atr = REGIMES[regime]
    return {
        "trade_id": f"{lane}-{signal or decision}", "research_lane": lane, "epoch_id": EPOCH,
        "net_pnl_usd": pnl, "shared_ai_call_ts": decision, "close_ts": decision + hold,
        "shared_ai_call_id": signal or f"scan-{decision}", "exit_reason": exit_reason, "policy_signature": sig,
        "entry_context": str({"regime": label, "adx": adx, "atr14_pct_3m": atr}),
    }


def _build(trades):
    return fds.build_fixed_vs_dynamic_selector(registry=REGISTRY, tile_order=LANES, trades=trades,
                                               epoch_id=EPOCH, v2_start_ts=T0 - 1)


def _regime_world(signals_per_regime):
    """X earns in regime A and loses in B; Y the opposite. Both tiles trade every signal."""
    trades, t = [], T0
    for i in range(signals_per_regime):
        for regime in ("A", "B"):
            t += 900
            good, bad = 0.02 + 0.001 * (i % 3), -0.02 - 0.001 * (i % 2)
            x, y = (good, bad) if regime == "A" else (bad, good)
            trades += [_trade("TILE_X", x, t, regime=regime), _trade("TILE_Y", y, t, regime=regime)]
    return trades


def test_regime_key_comes_from_entry_context_with_chronological_oos_buckets():
    row = _trade("TILE_X", 0.1, T0, regime="B")
    assert fds.trade_regime_key(row) == "RANGE|ADX_25_30|ATR_HIGH"
    assert fds.trade_regime_key({"entry_context": "{}"}) is None


def test_small_sample_publishes_not_enough_data_never_a_winner():
    report = _build(_regime_world(10))
    assert report["verdict"] == "NOT_ENOUGH_DATA"
    assert report["verdict_text"].startswith("NOT ENOUGH DATA")
    assert all(g["status"] == "NOT_ENOUGH_DATA" for g in report["gates"])
    assert report["live_policy_change_allowed"] is False


def test_selector_never_learns_from_closes_that_finish_after_the_signal():
    # 40 closes per tile, all still open when the last signal fires: nothing is known yet.
    trades = [_trade(lane, 0.01, T0 + i, hold=10 ** 6) for i in range(40) for lane in LANES]
    report = _build(trades)
    assert report["pick_counts"] == {"ABSTAIN_NO_TILE_WITH_ENOUGH_TRAINING": 40}
    assert report["oos"]["fixed"]["n"] == 0


def test_dynamic_selector_beats_fixed_when_regimes_differ():
    report = _build(_regime_world(80))
    assert report["verdict"] == "DYNAMIC_BETTER", report["verdict_text"]
    diff = report["oos"]["dynamic_minus_fixed_paired"]
    assert diff["ci95_usd"][0] > 0
    assert report["pick_counts"]["DYNAMIC_REGIME_SPECIFIC"] >= 30
    choices = {r["regime"]: r["dynamic_choices"] for r in report["per_regime"]}
    assert max(choices["BEAR|ADX_LT20|ATR_LOW"], key=choices["BEAR|ADX_LT20|ATR_LOW"].get) == "TILE_X"
    assert max(choices["RANGE|ADX_25_30|ATR_HIGH"], key=choices["RANGE|ADX_25_30|ATR_HIGH"].get) == "TILE_Y"


def test_identical_tiles_give_no_significant_difference():
    trades, t = [], T0
    for i in range(120):
        t += 900
        pnl = 0.01 if i % 2 else -0.01
        regime = "A" if i % 3 else "B"
        trades += [_trade("TILE_X", pnl, t, regime=regime), _trade("TILE_Y", pnl, t, regime=regime)]
    report = _build(trades)
    assert report["verdict"] == "NO_SIGNIFICANT_DIFFERENCE", report["verdict_text"]


def test_forced_exits_are_excluded_and_identity_is_reported():
    trades = _regime_world(5) + [_trade("TILE_X", -5.0, T0 + 10 ** 5, exit_reason="ADMIN_MANUAL_CLOSE", sig="other")]
    report = _build(trades)
    assert report["inputs"]["excluded"]["FORCED_EXIT"] == 1
    assert report["tiles"]["TILE_X"]["full_sample"]["n"] == 10
    assert report["tiles"]["TILE_X"]["identity_single_signature"] is False
    assert report["tiles"]["TILE_Y"]["identity_single_signature"] is True


def test_chronological_halves_gate_needs_both_halves_positive():
    trades, t = [], T0
    for i in range(40):
        t += 900
        trades.append(_trade("TILE_X", 0.02 if i >= 20 else -0.01, t))
        trades.append(_trade("TILE_Y", 0.01, t))
    tiles = _build(trades)["tiles"]
    assert tiles["TILE_X"]["chronological_halves"]["status"] == "NOT_CONSISTENT_POSITIVE"
    assert tiles["TILE_Y"]["oos_consistent_positive"] is True

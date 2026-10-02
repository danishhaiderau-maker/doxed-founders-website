"""Dedicated contract for the cross-venue lead tile (xvl) and its per-second evaluator."""
import json
import threading
import time

import pytest

import bot
import cross_venue_lead as xvl
import cross_venue_tape as cvt
import paper_policy_family_xvenue_lead as policy
import tile_paired_comparison as tpc
from ai_shadow_challengers import TapeRing
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_EXECUTION_LANES,
    COMBO_LANE_SPECS,
    CROSS_VENUE_LEAD_ADMISSION_POLICY_ID,
    CROSS_VENUE_SIGNAL_CLOCK,
    RETIRED_TILE_LANES,
    cross_venue_clock_lanes,
    is_cross_venue_clock_lane,
    validate_tile_registry,
)

ENTRY = 65000.0
NOW = 1_790_000_000.0
RULE = policy.RULE


def _decide(direction="LONG", bid=64999.0, ask=65000.0, bbo_age=0.5):
    return policy.decide_entry(
        direction=direction, signal_ts=NOW, bid=bid, ask=ask, bbo_ts=NOW - bbo_age,
        reference_price=(bid + ask) / 2.0,
    )


def _live(now, venue_moves_bp, *, venue_age=0.4, collector_age=0.3, venues=("binance", "bybit", "okx")):
    """Collector live file (cross_venue_live_v1) whose venue mids move by ``bp`` over the window."""
    anchor = int(now) - 1
    start = anchor - 30
    cells, mids = {}, {}
    for venue in venues:
        move = venue_moves_bp.get(venue, 0.0)
        history = []
        for sec in range(start, anchor + 1):
            frac = min(1.0, max(0.0, (sec - (anchor - RULE.lookback_sec)) / RULE.lookback_sec))
            history.append(ENTRY * (1.0 + move * frac / 1e4))
        mids[venue] = history
        cells[venue] = {"last_bbo_ts": now - venue_age}
    return {"schema": cvt.LIVE_SCHEMA, "written_ts": now - collector_age, "venues": cells,
            "history_start_ts": start, "mids": mids}


def _bfx(now, move_bp=0.0, spread_bp=1.0, extra_after=0):
    anchor = int(now) - 1
    quotes = {}
    for sec in range(anchor - 30, anchor + 1 + extra_after):
        frac = min(1.0, max(0.0, (sec - (anchor - RULE.lookback_sec)) / RULE.lookback_sec))
        mid = ENTRY * (1.0 + move_bp * frac / 1e4)
        half = mid * spread_bp / 2e4
        quotes[sec] = (mid - half, mid + half)
    return quotes


def test_registry_owns_a_paper_only_relay_ineligible_cross_venue_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert policy.LANE in ACTIVE_TILE_ORDER and policy.LANE in COMBO_EXECUTION_LANES
    assert spec["paper_only"] is True and spec["execution_scope"] == "PAPER_ONLY"
    assert spec["platform_relay_eligible"] is False and spec["live_copy_eligible"] is False
    assert spec["relay_capability"] == "BLOCKED_UNQUALIFIED"
    assert spec["id_prefix"] == "xvl" and spec["max_active_signals"] == 1
    assert spec["requested_margin_usd"] == 0.25
    assert spec["implementation_modules"] == ("paper_policy_family_xvenue_lead.py",)
    assert spec["dedicated_test_modules"] == ("test_paper_policy_family_xvenue_lead.py",)
    assert spec["admission_treatment"] == CROSS_VENUE_LEAD_ADMISSION_POLICY_ID
    assert spec["raw_policy_id"] == "XVENUE_LEAD_W10S_TH8BP_BOTHFRESH_SPREADLE3BP_TAKER_CAP5BPS|TIME_60_HARD40BP"
    assert policy.POLICY_ID == spec["raw_policy_id"]
    assert policy.POLICY_SIGNATURE == spec["policy_signature"]
    assert spec["signal_clock"] == CROSS_VENUE_SIGNAL_CLOCK == xvl.SIGNAL_CLOCK
    assert spec["uses_shared_ai_direction"] is False
    assert is_cross_venue_clock_lane(policy.LANE) and cross_venue_clock_lanes()[0] == policy.LANE
    assert policy.LANE not in RETIRED_TILE_LANES
    assert "HINT" in spec["subtitle"] and "12h evidence" in spec["subtitle"]
    assert "RELAY INELIGIBLE" in spec["subtitle"]
    assert spec["presentation"]["hypothesis_result"]["status"] == "HINT_12H_EVIDENCE"
    exit_policy = spec["exit_policy"]
    assert exit_policy["max_duration_sec"] == 60 and exit_policy["hard_stop_bps"] == 40.0
    assert exit_policy["max_open_positions"] == 1
    for absent in ("ladder", "breakeven", "trail", "take_profit"):
        assert exit_policy.get(absent) is None


def test_other_venues_are_price_data_only():
    entry = COMBO_LANE_SPECS[policy.LANE]["entry_policy"]
    assert entry["leader_venues"] == ("binance", "bybit")
    assert not any("fee" in key for key in entry)
    source = open(xvl.__file__, encoding="utf-8").read().lower()
    assert "fee_bps" not in source and "maker_fee" not in source and "taker_fee" not in source


def test_pre_registration_matches_the_research_spec():
    pre = COMBO_LANE_SPECS[policy.LANE]["pre_registration"]
    assert pre["schema"] == "tile_pre_registration_xvl_v1"
    assert pre["hypothesis_id"] == "H5_XVENUE_LEAD_60S_20261002"
    promote, kill = pre["promotion"], pre["kill"]
    assert promote["min_fills"] == 1000 and promote["min_utc_days"] == 5
    assert promote["min_asia_sessions"] == 3 and promote["min_asia_session_fills"] == 50
    assert promote["max_single_hour_profit_share"] == 0.15
    assert promote["max_replay_parity_gap_bp"] == 1.0
    assert promote["max_median_signal_to_fill_sec"] == 2.0
    assert kill["k1_after_fills"] == 150 and kill["k1_mean_bp_at_or_below"] == 0.0
    assert kill["k2_after_fills"] == 400 and kill["k2_upper_ci95_lt_bp"] == 0.5
    assert kill["k3_worst_trade_bp_below"] == -45.0 and kill["k3_max_stale_feed_fill_share"] == 0.01
    assert kill["k4_max_drawdown_usd"] == 0.50 and kill["k5_max_days_without_promotion"] == 10


def test_lane_admission_never_takes_a_side_from_the_shared_ai_call():
    view = policy.lane_admission({"direction": "LONG", "decision": "APPROVE"},
                                 {"applied": True, "accepted": True, "effective_direction": "LONG"})
    assert view["accepted"] is False and view["direction"] == "NO_TRADE"
    assert view["reason"].endswith("NOT_A_SHARED_AI_TILE")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_taker_limit_is_capped_at_five_bp_with_three_second_ttl(direction):
    decision = _decide(direction)
    assert decision["action"] == policy.ACTION_TAKER
    assert decision["entry_ttl_sec"] == 3
    assert decision["direction_source"] == "CROSS_VENUE_LEAD"
    if direction == "LONG":
        assert 65000.0 < decision["limit_price"] <= 65000.0 * 1.0005
    else:
        assert 64999.0 * 0.9995 <= decision["limit_price"] < 64999.0
    assert policy.decision_is_executable(decision, direction)


@pytest.mark.parametrize("kwargs,reason", [
    ({"bid": 64970.0, "ask": 65000.0}, "SPREAD_ABOVE_MAX"),
    ({"bbo_age": 2.5}, "BBO_STALE"),
    ({"bid": 0.0}, "BBO_UNAVAILABLE"),
])
def test_stands_aside_on_wide_spread_or_stale_bitfinex_quote(kwargs, reason):
    decision = _decide(**kwargs)
    assert decision["action"] == policy.ACTION_STAND_ASIDE and decision["reason"] == reason


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_sixty_second_time_exit_and_forty_bp_catastrophic_stop(direction):
    sign = 1 if direction == "LONG" else -1
    kwargs = dict(entry=ENTRY, direction=direction, atr_abs=60.0, leverage=100.0)
    assert policy.exit_action(price=ENTRY * (1 - sign * 0.0039), age_sec=30, **kwargs) is None
    stop = policy.exit_action(price=ENTRY * (1 - sign * 0.0045), age_sec=30, **kwargs)
    assert stop.reason == "PHYSICAL_HARD_STOP_40PCT"
    timed = policy.exit_action(price=ENTRY, age_sec=60, **kwargs)
    assert timed.reason == "PATH_END_1M"
    assert bot._is_trigger_consistent_exit_reason("PATH_END_1M")


def test_dashboard_discloses_hint_label_and_no_ai():
    payload = policy.dashboard_policy()
    chips = payload["filter_chips"]
    assert "PAPER ONLY" in chips and "HINT — 12h evidence" in chips
    assert any("Any feed >2s old" in chip for chip in chips)
    assert "60s time exit" in chips
    assert "no AI" in payload["entry"]["trigger"]
    assert payload["entry"]["signal_clock"] == CROSS_VENUE_SIGNAL_CLOCK
    assert payload["exit"]["fixed_time_exit"] == "60s"
    assert payload["pre_registration"]["hypothesis_id"] == "H5_XVENUE_LEAD_60S_20261002"


def test_strategy_box_names_the_registry_signal_source_not_the_shared_ai_call():
    import taker_time_exit_binding as binding
    from family_policy_common import SHARED_AI_SIGNAL_DETAIL

    detail = policy.dashboard_policy()["strategy_detail"]
    assert SHARED_AI_SIGNAL_DETAIL not in detail
    assert not any("shared AI call;" in line.lower() for line in detail)
    signal_line = detail[1]
    assert signal_line == binding.signal_source_detail(COMBO_LANE_SPECS[policy.LANE])
    assert signal_line.startswith("Per second cross venue evaluator (Binance/Bybit lead vs Bitfinex over 10s)")
    assert "no AI call" in signal_line
    card = next(row for row in bot.build_static_pathway_lane_specs()["lanes"] if row["lane"] == policy.LANE)
    assert card["strategy_detail"][1] == signal_line
    assert "shared entry-direction call" not in card["expected_advantage"]
    for lane in ACTIVE_TILE_ORDER:
        spec = COMBO_LANE_SPECS[lane]
        if spec["uses_shared_ai_direction"] and not spec.get("signal_clock"):
            assert binding.signal_source_detail(spec) == SHARED_AI_SIGNAL_DETAIL


def test_evaluator_counts_stale_seconds_by_feed_reason():
    evaluator = xvl.LeadEvaluator(RULE)
    for step, bbo_age in enumerate((0.3, 2.5, 3.1)):
        now = NOW + step + 0.6
        evaluator.step(now=now, live=_live(now, {}), bfx_quotes=_bfx(now), bfx_bbo_ts=now - bbo_age)
    snap = evaluator.snapshot()
    assert snap["by_status"][xvl.STATUS_STALE] == 2
    assert snap["stale_by_reason"] == {"BFX_BBO_STALE": 2}


# ---- evaluator ----------------------------------------------------------------

def _eval(live, quotes, now=NOW + 0.6, bbo_age=0.4):
    return xvl.evaluate_second(RULE, now=now, live=live, bfx_quotes=quotes, bfx_bbo_ts=now - bbo_age)


def test_mean_binance_bybit_lead_over_bitfinex_triggers_in_their_direction():
    now = NOW + 0.6
    out = _eval(_live(now, {"binance": 12.0, "bybit": 10.0, "okx": -50.0}), _bfx(now, 2.0))
    assert out["status"] == xvl.STATUS_TRIGGER and out["side"] == "LONG"
    assert out["lead_bp"] == pytest.approx(9.0, abs=0.01)
    assert out["anchor_bucket_ts"] == int(NOW) - 1
    down = _eval(_live(now, {"binance": -10.0, "bybit": -9.0}), _bfx(now, 0.0))
    assert down["status"] == xvl.STATUS_TRIGGER and down["side"] == "SHORT"


def test_below_threshold_and_wide_spread_do_not_trigger():
    now = NOW + 0.6
    assert _eval(_live(now, {"binance": 7.0, "bybit": 7.0}), _bfx(now))["status"] == xvl.STATUS_BELOW
    wide = _eval(_live(now, {"binance": 12.0, "bybit": 12.0}), _bfx(now, spread_bp=4.0))
    assert wide["status"] == xvl.STATUS_SPREAD and xvl.is_qualifying_lead(wide, RULE)


@pytest.mark.parametrize("mutate,reason", [
    (lambda live, q: live["venues"]["bybit"].update(last_bbo_ts=NOW + 0.6 - 2.5), "VENUE_STALE:bybit"),
    (lambda live, q: live.update(written_ts=NOW + 0.6 - 3.0), "COLLECTOR_STALE"),
    (lambda live, q: live["venues"].pop("binance"), "VENUE_STALE:binance"),
    (lambda live, q: live["mids"].pop("bybit"), "VENUE_WINDOW_INCOMPLETE:bybit"),
    (lambda live, q: q.pop(int(NOW) - 1 - RULE.lookback_sec), "BFX_WINDOW_INCOMPLETE"),
])
def test_any_stale_or_incomplete_feed_fails_closed(mutate, reason):
    now = NOW + 0.6
    live, quotes = _live(now, {"binance": 20.0, "bybit": 20.0}), _bfx(now)
    mutate(live, quotes)
    out = _eval(live, quotes, now=now)
    assert out["status"] == xvl.STATUS_STALE and reason in out["stale_reasons"]


def test_stale_bitfinex_bbo_fails_closed():
    now = NOW + 0.6
    out = _eval(_live(now, {"binance": 20.0, "bybit": 20.0}), _bfx(now), bbo_age=2.5)
    assert out["status"] == xvl.STATUS_STALE and "BFX_BBO_STALE" in out["stale_reasons"]


def test_shadow_outcome_is_after_spread_taker_markout_without_fees():
    long = xvl.shadow_outcome("LONG", (99.99, 100.01), (100.04, 100.06))
    assert long["entry_price"] == 100.01 and long["exit_price"] == 100.04
    assert long["net_bp_after_spread"] == pytest.approx((100.04 / 100.01 - 1) * 1e4, abs=1e-3)
    assert long["win"] is True
    short = xvl.shadow_outcome("SHORT", (99.99, 100.01), (100.04, 100.06))
    assert short["net_bp_after_spread"] < 0 and short["win"] is False
    assert xvl.shadow_outcome("LONG", None, (1, 2))["status"] == "MISSING_QUOTE"


def test_evaluator_logs_every_qualifying_second_and_matures_the_sixty_second_outcome():
    evaluator = xvl.LeadEvaluator(RULE, policy_id=policy.POLICY_ID, policy_signature=policy.POLICY_SIGNATURE)
    rows, outcomes = [], []
    quotes = {}
    for step in range(0, 70):
        now = NOW + step + 0.6
        lead = {"binance": 12.0, "bybit": 12.0} if step < 3 else {}
        live = _live(now, lead)
        quotes.update(_bfx(now, 0.0))
        _, trigger, matured = evaluator.step(now=now, live=live, bfx_quotes=quotes, bfx_bbo_ts=now - 0.3)
        if trigger:
            rows.append(trigger)
        outcomes.extend(matured)
    assert [r["qualifies"] for r in rows] == [True, True, True]
    assert [r["episode_first"] for r in rows] == [True, False, False]
    assert len({r["episode_id"] for r in rows}) == 1
    assert [r["cap1_take"] for r in rows] == [True, False, False]
    first = rows[0]
    assert first["schema"] == xvl.TRIGGER_SCHEMA and first["side"] == "LONG"
    assert set(first["venue_ret_bp"]) == {"binance", "bybit"}
    assert first["bfx_bid"] and first["bfx_ask"] and first["shadow_only"] is True
    assert first["exit_bucket_ts"] - first["entry_bucket_ts"] == 60
    assert len(outcomes) == 3 and all(o["status"] == "OK" for o in outcomes)
    assert all(o["schema"] == xvl.OUTCOME_SCHEMA and o["fee_applied"] is False for o in outcomes)
    assert outcomes[0]["net_bp_after_spread"] == pytest.approx(-1.0, abs=0.01)
    assert evaluator.snapshot()["triggers_logged"] == 3


def test_duplicate_anchor_is_ignored_and_missed_seconds_are_counted():
    evaluator = xvl.LeadEvaluator(RULE)
    now = NOW + 0.6
    quotes = _bfx(now)
    evaluator.step(now=now, live=_live(now, {}), bfx_quotes=quotes, bfx_bbo_ts=now)
    dup, trigger, _ = evaluator.step(now=now + 0.2, live=_live(now, {}), bfx_quotes=quotes, bfx_bbo_ts=now)
    assert dup["status"] == xvl.STATUS_DUPLICATE and trigger is None
    later = now + 4
    evaluator.step(now=later, live=_live(later, {}), bfx_quotes=_bfx(later), bfx_bbo_ts=later)
    assert evaluator.stats["missed_seconds"] == 3


def test_tape_ring_tail_returns_newest_quotes():
    ring = TapeRing()
    for sec in range(100, 110):
        ring.append_bucket({"bucket_ts": sec, "bid": 1.0 + sec, "ask": 2.0 + sec, "fresh": True,
                            "valid_bbo": True})
    tail = ring.tail(3)
    assert sorted(tail) == [107, 108, 109] and tail[109] == (110.0, 111.0)


# ---- runtime wiring -----------------------------------------------------------

def test_ai_fan_out_never_routes_the_cross_venue_tile():
    import inspect
    source = inspect.getsource(bot.spawn_combo_lanes_from_ai_scan)
    assert "is_cross_venue_clock_lane(lane)" in source


def test_shadow_rows_are_written_whatever_the_toggle_and_no_attempt_when_off(monkeypatch, tmp_path):
    written = []
    monkeypatch.setattr(bot, "_safe_append_jsonl", lambda path, row, **kw: written.append((path, dict(row))) or True)
    monkeypatch.setattr(bot, "is_research_lane_enabled", lambda lane: False)
    started = []
    monkeypatch.setattr(bot.threading, "Thread", lambda *a, **k: started.append(k) or type("T", (), {"start": lambda self: None})())
    evaluator = xvl.LeadEvaluator(RULE, policy_id=policy.POLICY_ID, policy_signature=policy.POLICY_SIGNATURE)
    monkeypatch.setitem(bot._XVL_EVALUATORS, policy.LANE, evaluator)
    now = time.time()
    monkeypatch.setattr(bot, "_cross_venue_live", lambda max_age_sec=0.5: _live(now, {"binance": 15.0, "bybit": 15.0}))

    class Tape:
        def tail(self, seconds):
            return _bfx(now)
    monkeypatch.setattr(bot, "_AI_SHADOW_TAPE", Tape())
    monkeypatch.setitem(bot.state, "bbo_ts", now - 0.2)
    bot._xvl_tick(now)
    assert [path for path, _ in written] == [bot.XVL_SHADOW_FILE]
    row = written[0][1]
    assert row["schema"] == xvl.TRIGGER_SCHEMA and row["research_lane"] == policy.LANE
    assert row["qualifies"] is True
    assert started == []


def test_paper_attempt_is_rate_capped_and_one_slot(monkeypatch):
    monkeypatch.setattr(bot, "is_research_lane_enabled", lambda lane: True)
    started = []

    class FakeThread:
        def __init__(self, *a, **k):
            started.append(k)

        def start(self):
            pass
    monkeypatch.setattr(bot.threading, "Thread", FakeThread)
    monkeypatch.setattr(bot, "_xvl_lane_runtime", {})
    now = time.time()
    trigger = {"trigger_id": "xvl-1", "side": "LONG", "evaluated_ts": now}
    evaluation = {"status": xvl.STATUS_TRIGGER}
    bot._xvl_maybe_attempt_paper(policy.LANE, evaluation, trigger, now)
    assert len(started) == 1
    bot._xvl_maybe_attempt_paper(policy.LANE, evaluation, trigger, now + 0.5)
    assert len(started) == 1
    assert bot._xvl_lane_runtime[policy.LANE]["skips"] == {"WORKER_BUSY": 1}
    bot._xvl_lane_runtime[policy.LANE]["busy"] = False
    bot._xvl_maybe_attempt_paper(policy.LANE, evaluation, trigger, now + 2)
    assert bot._xvl_lane_runtime[policy.LANE]["skips"]["MIN_SUBMIT_INTERVAL"] == 1
    bot._xvl_maybe_attempt_paper(policy.LANE, {"status": xvl.STATUS_SPREAD}, trigger, now + 10)
    assert len(started) == 1


def test_stale_trigger_never_reaches_the_order_path(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, "_spawn_combo_lane", lambda *a, **k: calls.append(a))
    old = {"trigger_id": "xvl-2", "side": "LONG", "evaluated_ts": time.time() - 5}
    assert bot._xvl_paper_attempt_inner(policy.LANE, old) == "TRIGGER_STALE"
    assert calls == []


def test_paper_attempt_writes_decision_evidence_then_spawns_the_tile(monkeypatch):
    now = time.time()
    monkeypatch.setattr(bot, "is_research_data_collection", lambda: True)
    monkeypatch.setattr(bot, "is_research_lane_enabled", lambda lane: True)
    monkeypatch.setattr(bot, "invert_signal_active", lambda: False)
    monkeypatch.setattr(bot, "ensure_lane_signal_capacity", lambda lane: True)
    monkeypatch.setattr(bot, "_record_adaptive_entry_decision", lambda lane, d: None)
    v3 = []
    monkeypatch.setattr(bot, "_write_v3_shared_lane_decision",
                        lambda lane, ai, ctx, feats, **kw: v3.append((lane, ai, ctx, feats, kw)) or True)
    spawned = []
    monkeypatch.setattr(bot, "_spawn_combo_lane", lambda *a: spawned.append(a))
    monkeypatch.setattr(bot, "_xvl_lane_runtime", {})
    for key, value in {"bid": 64999.0, "ask": 65000.0, "bbo_ts": now - 0.2, "price": 64999.5}.items():
        monkeypatch.setitem(bot.state, key, value)
    trigger = {"trigger_id": "xvl-77", "side": "SHORT", "evaluated_ts": now, "lead_bp": -9.1,
               "anchor_bucket_ts": int(now) - 1}
    assert bot._xvl_paper_attempt_inner(policy.LANE, trigger) == "ORDER_ELIGIBLE"
    lane, ai, ctx, feats, kw = v3[0]
    assert lane == policy.LANE and kw["execution_disposition"] == "ORDER_ELIGIBLE"
    assert ai["shared_ai_call_id"] == ctx["shared_ai_call_id"] == "xvl-77"
    assert ai["direction"] == "SHORT" and ai["raw_decision"] == "XVL_TRIGGER"
    assert "xvl_trigger" in feats and "adaptive_entry_decision" not in feats
    ctx2, ai2, edge, lane_features, target, reason = spawned[0]
    assert target == policy.LANE and reason.startswith("XVL_TRIGGER_")
    assert lane_features["adaptive_entry_decision"]["action"] == policy.ACTION_TAKER
    json.dumps(lane_features, default=str)


def test_evaluator_health_is_reported_for_monitoring(monkeypatch):
    monkeypatch.setitem(bot._xvl_status, "last_tick_ts", time.time() - 30)
    snap = bot.xvl_evaluator_snapshot()
    assert snap["schema"] == bot.XVL_HEALTH_SCHEMA
    assert snap["status"] in {"STALE", "DISABLED"}
    assert policy.LANE in snap["lanes"] or snap["status"] == "DISABLED"


def test_xvl_verdict_kills_on_a_trade_worse_than_minus_45_bp():
    spec = COMBO_LANE_SPECS[policy.LANE]
    base = NOW
    trades = [
        {"research_lane": policy.LANE, "shared_ai_call_id": f"xvl-{i}", "net_pnl_usd": 0.02,
         "close_ts": base + i * 120, "margin_usdt": 0.25, "leverage": 100, "exit_reason": "PATH_END_1M"}
        for i in range(20)
    ]
    trades.append({"research_lane": policy.LANE, "shared_ai_call_id": "xvl-bad", "net_pnl_usd": -0.12,
                   "close_ts": base + 3000, "margin_usdt": 0.25, "leverage": 100,
                   "exit_reason": "PHYSICAL_HARD_STOP_40PCT"})
    report = tpc.build_report(trades=trades, registry={policy.LANE: spec}, tile_order=[policy.LANE],
                              now_ts=base + 4000)
    verdict = report["pre_registered"][policy.LANE]["verdict"]
    assert "K3_STOP_OR_STALE_FEED_FAILURE" in verdict["kill_reasons"] and verdict["status"] == "KILL"
    assert verdict["promotion_checks"]["min_fills"] is False

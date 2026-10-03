"""Dedicated contract for the cross-venue premium-extreme tile (xvp) and its per-second evaluator."""
import time

import pytest

import bot
import cross_venue_premium as xvp
import cross_venue_tape as cvt
import paper_policy_family_xvenue_premium as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_EXECUTION_LANES,
    COMBO_LANE_SPECS,
    CROSS_VENUE_PREMIUM_ADMISSION_POLICY_ID,
    CROSS_VENUE_SIGNAL_CLOCK,
    RESEARCH_LANE_FAMILY_XVENUE_LEAD_60S,
    RETIRED_TILE_LANES,
    cross_venue_clock_lanes,
    is_cross_venue_clock_lane,
    validate_tile_registry,
)

BFX = 65000.0
NOW = 1_790_000_000.0
RULE = policy.RULE
SMALL = xvp.PremiumRule(mean_window_sec=20, min_mean_samples=10)


def _live(now, premium_bp, *, venue_age=0.4, collector_age=0.3, venues=("binance", "bybit")):
    """Collector live file whose leader mids sit ``premium_bp`` above a flat Bitfinex mid."""
    anchor = int(now) - 1
    start = anchor - 3
    mids = {v: [BFX * (1.0 + premium_bp.get(v, 0.0) / 1e4)] * 4 for v in venues}
    cells = {v: {"last_bbo_ts": now - venue_age} for v in venues}
    return {"schema": cvt.LIVE_SCHEMA, "written_ts": now - collector_age, "venues": cells,
            "history_start_ts": start, "mids": mids}


def _bfx(now, spread_bp=1.0, mid=BFX, back=3):
    anchor = int(now) - 1
    half = mid * spread_bp / 2e4
    return {sec: (mid - half, mid + half) for sec in range(anchor - back, anchor + 1)}


def _warm(evaluator_or_tracker, rule, n, premium=1.0, start=NOW):
    """Feed ``n`` buckets at a steady premium so the trailing mean is ``premium``."""
    for i in range(n):
        now = start + i + 0.6
        if isinstance(evaluator_or_tracker, xvp.PremiumTracker):
            evaluator_or_tracker.observe(int(now) - 1, {v: BFX * (1 + premium / 1e4) for v in rule.venues}, BFX)
        else:
            evaluator_or_tracker.step(now=now, live=_live(now, {v: premium for v in rule.venues}),
                                      bfx_quotes=_bfx(now), bfx_bbo_ts=now - 0.3)
    return start + n


def _decide(direction="LONG", bid=64999.0, ask=65000.0, bbo_age=0.5):
    return policy.decide_entry(
        direction=direction, signal_ts=NOW, bid=bid, ask=ask, bbo_ts=NOW - bbo_age,
        reference_price=(bid + ask) / 2.0,
    )


# ---- registry ------------------------------------------------------------------

def test_registry_owns_a_paper_only_relay_ineligible_premium_tile():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[1] == policy.LANE and policy.LANE in COMBO_EXECUTION_LANES
    assert ACTIVE_TILE_ORDER.index(policy.LANE) == ACTIVE_TILE_ORDER.index(RESEARCH_LANE_FAMILY_XVENUE_LEAD_60S) + 1
    assert spec["paper_only"] is True and spec["execution_scope"] == "PAPER_ONLY"
    assert spec["platform_relay_eligible"] is False and spec["live_copy_eligible"] is False
    assert spec["default_enabled"] is False
    assert spec["id_prefix"] == "xvp" and spec["max_active_signals"] == 1
    assert spec["requested_margin_usd"] == 0.25
    assert spec["implementation_modules"] == ("paper_policy_family_xvenue_premium.py",)
    assert spec["dedicated_test_modules"] == ("test_paper_policy_family_xvenue_premium.py",)
    assert spec["admission_treatment"] == CROSS_VENUE_PREMIUM_ADMISSION_POLICY_ID
    assert policy.POLICY_ID == spec["raw_policy_id"]
    assert policy.POLICY_SIGNATURE == spec["policy_signature"]
    assert len({COMBO_LANE_SPECS[lane]["policy_signature"] for lane in ACTIVE_TILE_ORDER}) == len(ACTIVE_TILE_ORDER)
    assert len({COMBO_LANE_SPECS[lane]["id_prefix"] for lane in ACTIVE_TILE_ORDER}) == len(ACTIVE_TILE_ORDER)
    assert spec["signal_clock"] == CROSS_VENUE_SIGNAL_CLOCK
    assert spec["uses_shared_ai_direction"] is False
    assert is_cross_venue_clock_lane(policy.LANE) and policy.LANE in cross_venue_clock_lanes()
    assert policy.LANE not in RETIRED_TILE_LANES
    assert "HINT" in spec["subtitle"] and "8h holdout evidence" in spec["subtitle"]
    assert spec["presentation"]["hypothesis_result"]["status"] == "HINT_8H_HOLDOUT_EVIDENCE"
    exit_policy = spec["exit_policy"]
    assert exit_policy["max_duration_sec"] == 60 and exit_policy["hard_stop_bps"] == 40.0
    assert exit_policy["max_open_positions"] == 1
    for absent in ("ladder", "breakeven", "trail", "take_profit"):
        assert exit_policy.get(absent) is None


def test_rule_is_the_frozen_model_a_signal():
    assert RULE.venues == ("binance", "bybit")
    assert (RULE.mean_window_sec, RULE.min_mean_samples) == (3600, 1200)
    assert (RULE.long_threshold_bps, RULE.short_threshold_bps) == (1.75, -1.88)
    assert RULE.hold_sec == 60 and RULE.max_fill_forward_sec == 5
    entry = COMBO_LANE_SPECS[policy.LANE]["entry_policy"]
    assert entry["taker_protection_bps"] == 5.0
    assert not any("fee" in key for key in entry)


def test_pre_registration_has_keep_and_kill_gates():
    pre = COMBO_LANE_SPECS[policy.LANE]["pre_registration"]
    assert pre["hypothesis_id"] == "H7_XVENUE_PREMIUM_60S_20261002"
    assert pre["promotion"]["min_fills"] == 500
    assert pre["kill"]["k5_max_days_without_promotion"] == 14


def test_lane_admission_never_takes_a_side_from_the_shared_ai_call():
    raw = {"raw_direction": "LONG", "direction": "LONG", "long_score": 90, "short_score": 10}
    view = policy.lane_admission(raw, {"applied": True, "accepted": True, "effective_direction": "LONG"})
    assert view["accepted"] is False and view["direction"] == "NO_TRADE"
    assert view["reason"].endswith("NOT_A_SHARED_AI_TILE")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_taker_limit_is_capped_at_five_bp(direction):
    decision = _decide(direction)
    assert decision["action"] == policy.ACTION_TAKER
    touch = 65000.0 if direction == "LONG" else 64999.0
    cap = touch * (1 + 5e-4) if direction == "LONG" else touch * (1 - 5e-4)
    assert abs(decision["limit_price"] - touch) <= abs(cap - touch) + 1e-6


def test_stands_aside_on_wide_spread():
    assert _decide(bid=64970.0, ask=65000.0)["action"] == policy.ACTION_STAND_ASIDE


def test_dashboard_discloses_premium_rule_and_8h_hint():
    payload = policy.dashboard_policy()
    chips = " ".join(payload["filter_chips"])
    assert "PAPER ONLY" in chips and "HINT — 8h holdout evidence" in chips
    assert "premium" in chips and "+1.75bp" in chips and "-1.88bp" in chips
    assert "lead" not in payload["entry"]["trigger"]
    assert "no AI" in payload["entry"]["trigger"]


# ---- evaluator -------------------------------------------------------------------

def test_premium_is_mean_of_venue_premia_over_bitfinex():
    mean, per = xvp.premium_bp({"binance": BFX * 1.0003, "bybit": BFX * 1.0001}, BFX, ("binance", "bybit"))
    assert per["binance"] == pytest.approx(3.0, abs=1e-3) and per["bybit"] == pytest.approx(1.0, abs=1e-3)
    assert mean == pytest.approx(2.0, abs=1e-3)
    assert xvp.premium_bp({"binance": BFX}, None, ("binance",))[0] is None


def test_mean_needs_minimum_samples_and_is_trailing_window():
    tracker = xvp.PremiumTracker(RULE)
    _warm(tracker, RULE, RULE.min_mean_samples - 1)
    assert tracker.mean() is None
    _warm(tracker, RULE, 1, start=NOW + RULE.min_mean_samples - 1)
    assert tracker.mean() == pytest.approx(1.0, abs=1e-6)
    small = xvp.PremiumTracker(SMALL)
    _warm(small, SMALL, 20, premium=1.0)
    _warm(small, SMALL, 20, premium=5.0, start=NOW + 20)
    assert small.mean() == pytest.approx(5.0, abs=1e-6)


def test_gaps_are_missing_samples_and_fill_forward_is_bounded():
    tracker = xvp.PremiumTracker(SMALL)
    tracker.observe(100, {"binance": BFX * 1.0002, "bybit": BFX * 1.0002}, BFX)
    filled = tracker.observe(103, {"binance": None, "bybit": BFX * 1.0002}, BFX)
    assert filled["venue_premium_bp"]["binance"] == pytest.approx(2.0, abs=1e-3)
    assert filled["mean_samples"] == 2
    expired = tracker.observe(110, {"binance": None, "bybit": BFX * 1.0002}, BFX)
    assert expired["venue_premium_bp"]["binance"] is None


def test_warming_up_never_triggers():
    evaluator = xvp.PremiumEvaluator(SMALL)
    now = NOW + 0.6
    out, trigger, _ = evaluator.step(now=now, live=_live(now, {"binance": 50.0, "bybit": 50.0}),
                                     bfx_quotes=_bfx(now), bfx_bbo_ts=now - 0.3)
    assert out["status"] == xvp.STATUS_WARMING and trigger is None


@pytest.mark.parametrize("jump,side", [(4.0, "LONG"), (-4.0, "SHORT")])
def test_extreme_premium_deviation_triggers_in_the_leaders_direction(jump, side):
    evaluator = xvp.PremiumEvaluator(SMALL, policy_id="p", policy_signature="s")
    t = _warm(evaluator, SMALL, 15, premium=1.0)
    now = t + 0.6
    out, trigger, _ = evaluator.step(now=now, live=_live(now, {"binance": 1.0 + jump, "bybit": 1.0 + jump}),
                                     bfx_quotes=_bfx(now), bfx_bbo_ts=now - 0.3)
    assert out["status"] == xvp.STATUS_TRIGGER and out["side"] == side
    assert trigger["schema"] == xvp.TRIGGER_SCHEMA and trigger["trigger_id"].startswith("xvp-")
    assert trigger["episode_id"].startswith("xvp-ep-") and trigger["qualifies"] is True
    assert trigger["premium_dev_bp"] == pytest.approx(out["premium_dev_bp"])
    assert set(trigger["venue_premium_bp"]) == {"binance", "bybit"}
    assert trigger["policy_signature"] == "s" and trigger["shadow_only"] is True


def test_below_threshold_and_wide_spread_do_not_trigger():
    evaluator = xvp.PremiumEvaluator(SMALL)
    t = _warm(evaluator, SMALL, 15, premium=1.0)
    now = t + 0.6
    below, trig, _ = evaluator.step(now=now, live=_live(now, {"binance": 2.0, "bybit": 2.0}),
                                    bfx_quotes=_bfx(now), bfx_bbo_ts=now - 0.3)
    assert below["status"] == xvp.STATUS_BELOW and trig is None
    now += 1
    wide, trig, _ = evaluator.step(now=now, live=_live(now, {"binance": 9.0, "bybit": 9.0}),
                                   bfx_quotes=_bfx(now, spread_bp=4.0), bfx_bbo_ts=now - 0.3)
    assert wide["status"] == xvp.STATUS_SPREAD and trig["qualifies"] is False


@pytest.mark.parametrize("kwargs,reason", [
    ({"venue_age": 2.5}, "VENUE_STALE:binance"),
    ({"collector_age": 3.0}, "COLLECTOR_STALE"),
    ({"venues": ("binance",)}, "VENUE_STALE:bybit"),
])
def test_any_stale_feed_fails_closed(kwargs, reason):
    tracker = xvp.PremiumTracker(SMALL)
    _warm(tracker, SMALL, 15)
    now = NOW + 15 + 0.6
    out = xvp.evaluate_second(SMALL, tracker, now=now, live=_live(now, {"binance": 9.0, "bybit": 9.0}, **kwargs),
                              bfx_quotes=_bfx(now), bfx_bbo_ts=now - 0.3)
    assert out["status"] == xvp.STATUS_STALE and reason in out["stale_reasons"]


def test_stale_bitfinex_bbo_fails_closed():
    tracker = xvp.PremiumTracker(SMALL)
    now = NOW + 0.6
    out = xvp.evaluate_second(SMALL, tracker, now=now, live=_live(now, {}), bfx_quotes=_bfx(now),
                              bfx_bbo_ts=now - 2.5)
    assert out["status"] == xvp.STATUS_STALE and "BFX_BBO_STALE" in out["stale_reasons"]


def test_outcome_matures_after_sixty_seconds():
    rule = xvp.PremiumRule(mean_window_sec=20, min_mean_samples=10, hold_sec=60)
    evaluator = xvp.PremiumEvaluator(rule)
    t = _warm(evaluator, rule, 15, premium=1.0)
    quotes, triggers, outcomes = {}, [], []
    for step in range(70):
        now = t + step + 0.6
        prem = 6.0 if step == 0 else 1.0
        quotes.update(_bfx(now))
        _, trig, matured = evaluator.step(now=now, live=_live(now, {"binance": prem, "bybit": prem}),
                                          bfx_quotes=quotes, bfx_bbo_ts=now - 0.3)
        if trig:
            triggers.append(trig)
        outcomes.extend(matured)
    assert len(triggers) == 1 and triggers[0]["cap1_take"] is True
    assert len(outcomes) == 1 and outcomes[0]["schema"] == xvp.OUTCOME_SCHEMA
    assert outcomes[0]["status"] == "OK" and outcomes[0]["fee_applied"] is False
    assert "premium_dev_bp" in outcomes[0]
    assert outcomes[0]["net_bp_after_spread"] == pytest.approx(-1.0, abs=0.01)


def test_latest_features_expose_premium_and_leads_for_prompts_and_snapshots():
    evaluator = xvp.PremiumEvaluator(SMALL)
    _warm(evaluator, SMALL, 15)
    facts = evaluator.latest_features()
    assert facts["schema"] == xvp.FEATURES_SCHEMA
    assert facts["premium_bp"] == pytest.approx(1.0, abs=1e-3)
    assert facts["premium_dev_bp"] == pytest.approx(0.0, abs=1e-3)
    assert {"lead_60s_bp", "lead_300s_bp", "mean_samples"} <= set(facts)


# ---- runtime wiring --------------------------------------------------------------

def test_policy_builds_a_premium_evaluator_with_its_own_shadow_file():
    evaluator = policy.make_evaluator()
    assert isinstance(evaluator, xvp.PremiumEvaluator)
    assert evaluator.SHADOW_FILE == "xvp_shadow_signals.jsonl" != bot.XVL_SHADOW_FILE
    assert evaluator.policy_signature == policy.POLICY_SIGNATURE


def test_shadow_rows_go_to_the_premium_file_whatever_the_toggle(monkeypatch):
    written = []
    monkeypatch.setattr(bot, "_safe_append_jsonl", lambda path, row, **kw: written.append((path, dict(row))) or True)
    monkeypatch.setattr(bot, "is_research_lane_enabled", lambda lane: False)
    started = []
    monkeypatch.setattr(bot.threading, "Thread", lambda *a, **k: started.append(k) or type("T", (), {"start": lambda self: None})())
    evaluator = xvp.PremiumEvaluator(SMALL, policy_id=policy.POLICY_ID, policy_signature=policy.POLICY_SIGNATURE)
    now = time.time()
    _warm(evaluator, SMALL, 15, start=now - 16)
    monkeypatch.setattr(bot, "_XVL_EVALUATORS", {policy.LANE: evaluator})
    monkeypatch.setattr(bot, "_cross_venue_live", lambda max_age_sec=0.5: _live(now, {"binance": 9.0, "bybit": 9.0}))

    class Tape:
        def tail(self, seconds):
            return _bfx(now)
    monkeypatch.setattr(bot, "_AI_SHADOW_TAPE", Tape())
    monkeypatch.setitem(bot.state, "bbo_ts", now - 0.2)
    bot._xvl_tick(now)
    assert [path for path, _ in written] == [xvp.SHADOW_FILE]
    row = written[0][1]
    assert row["schema"] == xvp.TRIGGER_SCHEMA and row["research_lane"] == policy.LANE
    assert started == []


def test_paper_attempt_writes_premium_evidence_then_spawns_the_tile(monkeypatch):
    now = time.time()
    monkeypatch.setattr(bot, "is_research_data_collection", lambda: True)
    monkeypatch.setattr(bot, "is_research_lane_enabled", lambda lane: True)
    monkeypatch.setattr(bot, "invert_signal_active", lambda: False)
    monkeypatch.setattr(bot, "ensure_lane_signal_capacity", lambda lane: True)
    monkeypatch.setattr(bot, "_record_adaptive_entry_decision", lambda lane, d: None)
    monkeypatch.setattr(bot, "_XVL_EVALUATORS", {policy.LANE: policy.make_evaluator()})
    v3 = []
    monkeypatch.setattr(bot, "_write_v3_shared_lane_decision",
                        lambda lane, ai, ctx, feats, **kw: v3.append((lane, ai, ctx, feats, kw)) or True)
    spawned = []
    monkeypatch.setattr(bot, "_spawn_combo_lane", lambda *a: spawned.append(a))
    monkeypatch.setattr(bot, "_xvl_lane_runtime", {})
    for key, value in {"bid": 64999.0, "ask": 65000.0, "bbo_ts": now - 0.2, "price": 64999.5}.items():
        monkeypatch.setitem(bot.state, key, value)
    trigger = {"trigger_id": "xvp-9", "side": "LONG", "evaluated_ts": now, "premium_dev_bp": 2.4,
               "anchor_bucket_ts": int(now) - 1}
    assert bot._xvl_paper_attempt_inner(policy.LANE, trigger) == "ORDER_ELIGIBLE"
    lane, ai, ctx, feats, kw = v3[0]
    assert lane == policy.LANE and ai["raw_decision"] == "XVP_TRIGGER"
    assert ai["direction_source"] == "CROSS_VENUE_PREMIUM"
    assert feats["xvp_trigger"]["premium_dev_bp"] == 2.4 and "xvl_trigger" not in feats
    assert spawned[0][-1].startswith("XVP_TRIGGER_")


def test_evidence_badge_is_derived_from_registry_status():
    payload = bot.build_static_pathway_lane_specs()
    lanes = payload["lanes"]
    by_lane = {row["lane"]: row for row in lanes}
    assert by_lane[policy.LANE]["evidence_badge"] == "HINT — 8h holdout evidence"
    assert by_lane[RESEARCH_LANE_FAMILY_XVENUE_LEAD_60S]["evidence_badge"] == "HINT — 12h evidence"
    assert [row["lane"] for row in lanes] == list(ACTIVE_TILE_ORDER)


def test_analyzer_replay_matches_the_live_rule_shape():
    import types

    import numpy as np
    from research import lead_lag_report as llr

    n = 300
    bfx = np.full(n, BFX)
    lead = np.full(n, BFX * 1.0001)
    lead[150] = BFX * 1.0006
    al = types.SimpleNamespace(
        n=n, start=int(NOW), mid={"binance": lead.copy(), "bybit": lead.copy()}, bfx_mid=bfx,
        bid=bfx - 0.5, ask=bfx + 0.5, hour=(np.arange(n) + int(NOW)) // 3600,
    )
    trades = llr.xvp_replay_trades(al, SMALL)
    assert [t["anchor"] for t in trades] == [int(NOW) + 150]
    assert trades[0]["side"] == "LONG" and trades[0]["premium_dev_bp"] > SMALL.long_threshold_bps
    assert llr._replay_for(SMALL) is llr.xvp_replay_trades
    assert set(llr._xvl_rules()) == set(cross_venue_clock_lanes())

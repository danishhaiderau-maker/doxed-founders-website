"""Dedicated contract for Danish A: confirmed fade of committed calls, Asia+EU, confirm-to-market entry, late break-even and conditional early cut."""
import copy

import pytest

import bot
import paper_policy_family_danish_cf as policy
from adaptive_regime_entry import ACTION_MAKER, ACTION_STAND_ASIDE
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    DANISH_CF_ADMISSION_POLICY_ID,
    RETIRED_POLICY_IDENTITIES,
    resolve_score_led_paper_admission,
    tile_card_sections,
    tile_number,
    validate_tile_registry,
)

NOW = 1_790_000_000.0  # 14:13 UTC (EU session)
US_TS = NOW + 4 * 3600
ASIA_TS = NOW + 12 * 3600


def _admission(ai):
    return resolve_score_led_paper_admission(
        ai, score_led_enabled=True, research_mode=True, forced_paper=True,
        live_armed=False, bitfinex_live_enabled=False,
    )


def _ai(long_score=70, short_score=30, raw_direction=None, **extra):
    if raw_direction is None:
        raw_direction = "LONG" if long_score >= short_score else "SHORT"
    return {"raw_direction": raw_direction, "raw_decision": "APPROVE", "direction": raw_direction,
            "decision": "APPROVE", "long_score": long_score, "short_score": short_score, **extra}


def _entry(ts=NOW, **kwargs):
    args = {"direction": "LONG", "signal_ts": ts, "bid": 99_999.0, "ask": 100_001.0,
            "bbo_ts": ts - 1.0, "reference_price": 100_000.0, **kwargs}
    return policy.decide_entry(**args)


def test_registry_owns_tile_one_paper_only_relay_ineligible_default_off():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[0] == policy.LANE and tile_number(policy.LANE) == 1
    assert spec["paper_only"] is True and spec["platform_relay_eligible"] is False
    assert spec["live_copy_eligible"] is False and policy.LANE not in bot.PLATFORM_RELAY_ELIGIBLE_LANES
    assert spec["default_enabled"] is False
    assert spec["id_prefix"] == "dcf" and spec["max_active_signals"] == 3
    assert spec["requested_margin_usd"] == 0.25
    assert spec["implementation_modules"] == ("paper_policy_family_danish_cf.py",)
    assert spec["dedicated_test_modules"] == ("test_paper_policy_family_danish_cf.py",)
    assert spec["admission_treatment"] == DANISH_CF_ADMISSION_POLICY_ID
    assert policy.POLICY_ID == spec["raw_policy_id"] not in RETIRED_POLICY_IDENTITIES
    entry = spec["entry_policy"]
    assert entry["mode"] == "MAKER_LIMIT_OFFSET_CONFIRM_MARKET" and entry["fill_model"] == "REALISTIC_V1"
    assert entry["offset_pct"] == 0.10 and entry["maker_ttl_sec"] == 1800
    assert (entry["confirm_move_bps"], entry["confirm_market_cap_bps"], entry["confirm_taker_ttl_sec"]) == (3.0, 5.0, 3)
    assert entry["max_spread_bps"] == 3.0 and entry["allowed_sessions"] == ("ASIA", "EU")
    assert entry["commit_rule"] == "EXPLICIT_RAW_SIDE_EQUALS_SCORE_LED"


def test_pre_registration_uses_the_owner_kill_rule():
    pre = COMBO_LANE_SPECS[policy.LANE]["pre_registration"]
    assert pre["evidence_world"] == "REALISTIC_V1" and pre["ci_method"] == "1H_CLUSTER_BOOTSTRAP_95"
    assert pre["kill"]["k1_after_fills"] == 80 and pre["kill"]["k1_mean_bp_at_or_below"] == 0.0
    assert pre["kill"]["k4_max_drawdown_usd"] == 1.0
    assert pre["promotion"]["min_fills"] == 150
    assert pre["promotion"]["meaning"] == "ELIGIBLE_FOR_OWNER_REVIEW_NEVER_RELAY"
    assert pre["promotion"]["sessions"] == ("ASIA", "EU")


@pytest.mark.parametrize("long_score,short_score,ours", [(70, 30, "SHORT"), (30, 70, "LONG")])
def test_committed_call_is_faded(long_score, short_score, ours):
    raw = _ai(long_score, short_score)
    original = copy.deepcopy(raw)
    view = policy.lane_admission(raw, _admission(raw))
    assert raw == original
    assert (view["accepted"], view["direction"]) == (True, ours)
    assert view["lane_ai"]["effective_research_admission_policy_id"] == DANISH_CF_ADMISSION_POLICY_ID


@pytest.mark.parametrize("raw", [
    _ai(62, 38, raw_direction="NO_TRADE", direction="NO_TRADE", decision="REJECT"),
    _ai(70, 30, raw_direction="SHORT"),
    _ai(50, 50),
    _ai(70, 30, ai_error=True),
])
def test_uncommitted_calls_are_never_faded(raw):
    view = policy.lane_admission(raw, _admission(raw))
    assert view["accepted"] is False and view["direction"] == "NO_TRADE"


@pytest.mark.parametrize("direction,limit", [("LONG", 99_900.0), ("SHORT", 100_100.0)])
def test_decide_entry_rests_a_maker_limit_in_asia_and_eu(direction, limit):
    for ts in (NOW, ASIA_TS):
        decision = _entry(ts, direction=direction)
        assert decision["action"] == ACTION_MAKER and decision["limit_price"] == limit
        assert decision["reason"] == "MAKER_OFFSET_AT_SIGNAL_CONFIRM_MARKET"
        assert decision["confirm_move_bps"] == 3.0 and decision["confirm_market_cap_bps"] == 5.0
        assert policy.decision_is_executable(decision, direction)


@pytest.mark.parametrize("kwargs,reason", [
    ({"ts": US_TS}, "SESSION_GATED"),
    ({"bid": 99_990.0, "ask": 100_030.0}, "SPREAD_ABOVE_MAX"),
    ({"bbo_ts": NOW - 6.0}, "BBO_STALE"),
    ({"direction": "NO_TRADE"}, "NO_DIRECTION"),
])
def test_decide_entry_stands_aside(kwargs, reason):
    decision = _entry(**kwargs)
    assert decision["action"] == ACTION_STAND_ASIDE and decision["reason"] == reason


def _confirm(direction="LONG", bid=99_999.0, ask=100_001.0, confirmed_ts=None, now=NOW):
    limit = 99_900.0 if direction == "LONG" else 100_100.0
    return policy.confirm_market_action(direction=direction, signal_price=100_000.0, limit_price=limit,
                                        bid=bid, ask=ask, confirmed_ts=confirmed_ts, now=now)


def test_confirm_holds_until_the_move_reaches_three_bp():
    verdict = _confirm(bid=100_019.0, ask=100_021.0)
    assert verdict["action"] == "HOLD" and verdict["limit_price"] == 99_900.0


@pytest.mark.parametrize("direction,bid,ask", [("LONG", 100_029.0, 100_031.0), ("SHORT", 99_969.0, 99_971.0)])
def test_confirmed_move_converts_to_a_capped_taker(direction, bid, ask):
    verdict = _confirm(direction, bid, ask)
    assert verdict["action"] == "MARKET" and verdict["reason"] == "CONFIRMED_MOVE_TAKER"
    confirm = verdict["confirm_price"]
    cap = confirm * (1 + (5e-4 if direction == "LONG" else -5e-4))
    if direction == "LONG":
        assert ask <= verdict["limit_price"] <= cap
    else:
        assert cap <= verdict["limit_price"] <= bid


@pytest.mark.parametrize("direction,bid,ask", [("LONG", 100_090.0, 100_092.0), ("SHORT", 99_908.0, 99_910.0)])
def test_executable_side_past_the_cap_drops_the_signal(direction, bid, ask):
    verdict = _confirm(direction, bid, ask)
    assert verdict["action"] == "DROP" and verdict["reason"] == "CONFIRM_CAP_EXCEEDED"


def test_unfilled_confirmation_taker_drops_after_its_ttl():
    assert _confirm(confirmed_ts=NOW - 1.0)["action"] == "HOLD"
    verdict = _confirm(confirmed_ts=NOW - 3.0)
    assert verdict["action"] == "DROP" and verdict["reason"] == "CONFIRM_TAKER_UNFILLED"


def test_exit_is_hard_stop_breakeven_early_cut_then_time():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert spec["live_exit_order"] == ("HARD_STOP", "BREAKEVEN_LOCK", "EARLY_CUT", "TIME_EXIT")
    assert policy.EXIT["max_duration_sec"] == 5400 and policy.EXIT["hard_stop_bps"] == 40.0
    assert policy.SPEC.breakeven_trigger_margin_pct == 20.0 and policy.SPEC.breakeven_lock_margin_pct == 5.0
    assert policy.SPEC.thesis_cut_margin_pct == -12.0 and policy.SPEC.thesis_window_sec == 300
    assert policy.SPEC.thesis_cut_max_peak_margin_pct == 2.0
    hard = policy.exit_action(entry=100_000.0, direction="LONG", price=99_590.0, age_sec=60)
    assert hard is not None and "STOP" in hard.reason
    locked = policy.exit_action(entry=100_000.0, direction="LONG", price=100_040.0, age_sec=1200,
                                peak_price=100_210.0)
    assert locked is not None and locked.trigger_price == pytest.approx(100_050.0)
    cut = policy.exit_action(entry=100_000.0, direction="LONG", price=99_875.0, age_sec=120)
    assert cut is not None and cut.reason == "THESIS_FAST_CUT"
    assert policy.exit_action(entry=100_000.0, direction="LONG", price=99_875.0, age_sec=120,
                              peak_price=100_030.0) is None
    assert policy.exit_action(entry=100_000.0, direction="LONG", price=99_875.0, age_sec=400) is None
    timed = policy.exit_action(entry=100_000.0, direction="LONG", price=100_010.0, age_sec=5400)
    assert timed is not None and timed.reason == "PATH_END_90M"


def test_dashboard_and_card_disclose_entry_exit_and_risk():
    chips = " ".join(policy.dashboard_policy()["filter_chips"])
    assert "PAPER ONLY" in chips and "Sessions ASIA+EU only" in chips
    assert "taker within 5bp cap" in chips and "Break-even armed at +20bp" in chips
    assert "Early cut -12bp in 5m" in chips and "Stop 40bp" in chips
    sections = tile_card_sections(policy.LANE)
    assert sections["entry"] and sections["risk"]
    assert any("$0.25 margin @100x" in line for line in sections["risk"])
    assert not any("max loss" in line.lower() for line in sections["risk"])


def _hook_order(trade_id="dcf-hook-1"):
    return {"trade_id": trade_id, "status": "PENDING", "research_lane": policy.LANE, "signal_dir": "LONG",
            "limit_price": 99_900.0, "signal_price": 100_000.0, "relay_eligible": False}


@pytest.mark.parametrize("bid,ask,expected", [
    (100_029.0, 100_031.0, "MARKET"), (100_090.0, 100_092.0, "DROP"), (100_009.0, 100_011.0, "HOLD"),
])
def test_bot_hook_converts_drops_or_holds_without_touching_bitfinex(monkeypatch, bid, ask, expected):
    order = _hook_order()
    calls = {"commit": [], "cancel": []}

    def commit(o, signal, **kw):
        calls["commit"].append(kw)
        return o

    def cancel(o, reason, **kw):
        calls["cancel"].append(reason)
        return {"finalized": True}

    monkeypatch.setattr(bot, "pending_orders", [order])
    monkeypatch.setattr(bot, "trades_map", {order["trade_id"]: {"signal_ref": {"signal_price": 100_000.0}}})
    monkeypatch.setattr(bot, "lane_orders_allowed", lambda lane: True)
    monkeypatch.setattr(bot, "_commit_relay_limit_chase", commit)
    monkeypatch.setattr(bot, "_cancel_pending_order_confirmed", cancel)
    monkeypatch.setattr(bot, "_emit_genome_execution_event", lambda *a, **k: None)
    monkeypatch.setattr(bot, "pipeline_state_sync", lambda *a, **k: None)
    monkeypatch.setitem(bot.state, "bid", bid)
    monkeypatch.setitem(bot.state, "ask", ask)
    acted = bot._process_family_confirm_market(NOW)
    if expected == "MARKET":
        assert acted == 1 and len(calls["commit"]) == 1 and not calls["cancel"]
        assert calls["commit"][0]["urgent_marketable"] is True
        assert order["relay_eligible"] is False
    elif expected == "DROP":
        assert acted == 1 and calls["cancel"] == ["CONFIRM_CAP_EXCEEDED"] and not calls["commit"]
    else:
        assert acted == 0 and not calls["commit"] and not calls["cancel"]


def test_bot_hook_skips_orders_with_exchange_ids(monkeypatch):
    order = {**_hook_order(), "bitfinex_order_id": 123}
    monkeypatch.setattr(bot, "pending_orders", [order])
    monkeypatch.setattr(bot, "trades_map", {})
    monkeypatch.setattr(bot, "lane_orders_allowed", lambda lane: True)
    monkeypatch.setattr(bot, "_commit_relay_limit_chase", lambda *a, **k: pytest.fail("must not convert"))
    monkeypatch.setattr(bot, "_cancel_pending_order_confirmed", lambda *a, **k: pytest.fail("must not cancel"))
    monkeypatch.setitem(bot.state, "bid", 100_029.0)
    monkeypatch.setitem(bot.state, "ask", 100_031.0)
    assert bot._process_family_confirm_market(NOW) == 0

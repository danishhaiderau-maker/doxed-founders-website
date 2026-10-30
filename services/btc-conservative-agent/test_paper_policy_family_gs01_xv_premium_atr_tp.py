"""Dedicated contract for FREEZE21B GS-01: cross-venue premium follow with the ATR maker take-profit."""
import paper_policy_family_gs01_xv_premium_atr_tp as policy
from combo_pathway_config import COMBO_LANE_SPECS, CROSS_VENUE_SIGNAL_CLOCK, cross_venue_clock_lanes
from cross_venue_premium import PremiumEvaluator
from gs_tile_contract_support import assert_dashboard, assert_paper_only_registry_tile, bar, decide
from regime_adaptive_binding import GsPremiumEvaluator


def test_registry_owns_a_paper_only_non_ai_premium_tile():
    spec = assert_paper_only_registry_tile(policy, index=3, prefix="gs1", hypothesis_id="GS-20261004-01",
                                           bonferroni_k=4)
    assert spec["signal_clock"] == CROSS_VENUE_SIGNAL_CLOCK and spec["uses_shared_ai_direction"] is False
    assert policy.LANE in cross_venue_clock_lanes()
    entry = spec["entry_policy"]
    assert entry["direction_source"] == "CROSS_VENUE_PREMIUM" and entry["max_spread_bps"] == 3.0
    assert spec["pre_registration"]["kill"]["latency_p50_above_sec"] == 5.0


def test_own_evaluator_reuses_the_premium_rule_without_a_shadow_file():
    evaluator = policy.make_evaluator()
    assert isinstance(evaluator, GsPremiumEvaluator) and isinstance(evaluator, PremiumEvaluator)
    # HM 30 Oct: xvp stream RETIRED — SHADOW_FILE None stops recording; signal path intact.
    assert evaluator.SHADOW_FILE is None and evaluator.ID_PREFIX == "gsxvp"
    assert evaluator.policy_id == policy.POLICY_ID


def test_gs01_still_triggers_via_premium_rule_side_for():
    """Retiring the xvp stream must not disable GS-01: drive evaluator.evaluate() on venue+BFX mids."""
    from cross_venue_premium import LONG, STATUS_TRIGGER
    import cross_venue_tape as cvt

    evaluator = policy.make_evaluator()
    assert evaluator.SHADOW_FILE is None
    rule = evaluator.rule
    start = 1_800_000_000
    # Warm the trailing mean the same way evaluate() does (tracker.observe).
    for i in range(rule.min_mean_samples):
        evaluator.tracker.observe(start + i, {v: 100_000.0 for v in rule.venues}, 100_000.0)
    trigger_bucket = start + rule.min_mean_samples
    venue_mid = 100_000.0 * (1.0 + 3.0e-4)  # +3 bp premium vs BFX
    now = float(trigger_bucket + 1)
    live = {
        "schema": cvt.LIVE_SCHEMA,
        "written_ts": now - 0.1,
        "history_start_ts": trigger_bucket,
        "mids": {v: [venue_mid] for v in rule.venues},
        "venues": {
            v: {"last_bbo_ts": now - 0.1, "bid": venue_mid, "ask": venue_mid}
            for v in rule.venues
        },
    }
    bfx = 100_000.0
    ev = evaluator.evaluate(
        now=now, live=live,
        bfx_quotes={trigger_bucket: (bfx - 0.5, bfx + 0.5)},
        bfx_bbo_ts=now - 0.1,
    )
    assert ev["status"] == STATUS_TRIGGER and ev["side"] == LONG
    assert float(ev["premium_dev_bp"]) >= rule.long_threshold_bps
    tid = f"{evaluator.ID_PREFIX}-alive"
    feature_key = evaluator.TRIGGER_FEATURE_KEY
    ai_feature = {
        f"{evaluator.ID_PREFIX}_trigger_id": tid,
        feature_key: {
            "trigger_id": tid, "side": ev["side"], "premium_dev_bp": float(ev["premium_dev_bp"]),
        },
    }
    assert feature_key == f"{evaluator.ID_PREFIX}_trigger"
    d = decide(policy, direction=ev["side"], engine_bar=bar(), ai_feature=ai_feature)
    assert d["action"] == "TAKER" and d["trigger_kind"] == "CROSS_VENUE_PREMIUM"


def test_premium_trigger_is_a_taker_in_every_regime():
    for b in (bar(), bar(atr_pct=95.0), bar(adx=40.0)):
        d = decide(policy, engine_bar=b, ai_feature={"gsxvp_trigger_id": "x1"})
        assert d["action"] == "TAKER" and d["trigger_kind"] == "CROSS_VENUE_PREMIUM" and d["exit_profile"] == "ALL"
        assert d["limit_price"] >= 60000.5


def test_spread_above_three_bp_stands_aside():
    d = decide(policy, engine_bar=bar(), ai_feature={"gsxvp_trigger_id": "x"}, bid=60000.0, ask=60030.0)
    assert d["action"] == "STAND_ASIDE" and d["reason"] == "SPREAD_ABOVE_MAX"


def test_exit_take_profit_books_the_maker_target():
    ex = COMBO_LANE_SPECS[policy.LANE]["exit_policy"]["profiles"]["ALL"]
    assert (ex["tp_atr"], ex["tp_floor"], ex["be_atr"], ex["lock_bp"], ex["hard_bp"]) == (2.5, 8.0, 2.0, 1.0, 35.0)
    decision = decide(policy, engine_bar=bar(atr_bp=4.0), ai_feature={"gsxvp_trigger_id": "x"})
    state = {}
    entry = 60000.0
    assert policy.exit_action(entry=entry, direction="LONG", price=entry * 1.0005, age_sec=5,
                              policy_state=state, entry_decision=decision, fill_ts=0) is None
    action = policy.exit_action(entry=entry, direction="LONG", price=entry * 1.0011, age_sec=6,
                                policy_state=state, entry_decision=decision, fill_ts=0)
    # REALISM_V2: the TP is a BOT_SENT reduce-only market leg (not a maker limit at the target); the policy books the
    # trigger price and the shared pricer walks the order book at trigger + delay.
    assert action.reason == "GS_ATR_TAKE_PROFIT" and action.maker is False
    assert abs(action.book_price - entry * 1.0011) < 1e-6 and action.remaining_fraction == 0.0


def test_dashboard_discloses_the_rule():
    assert_dashboard(policy, "GS-20261004-01")

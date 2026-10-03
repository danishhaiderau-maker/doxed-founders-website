"""Exact-replica contract for the August 2026 Continuous benchmark tile."""
import ast
import copy
import json
from pathlib import Path

import pytest

import paper_policy_family_continuous_aug_original as policy
from combo_pathway_config import (
    ACTIVE_TILE_ORDER,
    COMBO_LANE_SPECS,
    CONTINUOUS_AUG_LADDER,
    RETIRED_TILE_LANES,
    combo_toggle_defaults,
    validate_tile_registry,
)
from scenario_c_config import TRAIL_LADDER_SCENARIO_C

E = 60_000.0


def _reply(long_score, short_score, direction=None):
    direction = direction or ("LONG" if long_score >= short_score else "SHORT")
    return json.dumps({"direction": direction, "long_score": long_score,
                       "short_score": short_score, "reason": "structure"})


def _decide(long_score, short_score, ctx=None, direction=None):
    return policy.decide(ctx or {}, policy.parse_response(_reply(long_score, short_score, direction)))


def _ctx_exit(**overrides):
    base = {"peak_pct": 0.0, "in_post_fill_grace": False, "early_fail_enabled": True,
            "conviction_spread": 0, "trend_health": {}, "entry_thesis": {}, "market_context": {}}
    base.update(overrides)
    return base


def _price(margin_pct, direction="LONG"):
    sign = 1 if direction == "LONG" else -1
    return E * (1 + sign * margin_pct / 10_000.0)


def _exit(margin_pct, *, direction="LONG", age=60.0, peak_margin=None, **ctx):
    peak = _price(peak_margin if peak_margin is not None else max(margin_pct, 0.0), direction)
    return policy.exit_action(entry=E, direction=direction, price=_price(margin_pct, direction),
                              age_sec=age, leverage=100, peak_price=peak,
                              exit_context=_ctx_exit(**ctx))


def test_registry_identity_is_paper_only_default_on_and_never_relay():
    spec = COMBO_LANE_SPECS[policy.LANE]
    assert validate_tile_registry() == ()
    assert ACTIVE_TILE_ORDER[3] == policy.LANE
    assert spec["raw_policy_id"] == policy.POLICY_ID
    assert spec["paper_only"] is True and spec["execution_scope"] == "PAPER_ONLY"
    assert spec["platform_relay_eligible"] is False and spec["live_copy_eligible"] is False
    assert spec["default_enabled"] is True and combo_toggle_defaults()[policy.LANE] is True
    assert spec["id_prefix"] == "caug" and spec["own_ai_call"] is True
    assert spec["uses_shared_ai_direction"] is False
    # The retired label stays retired; the replica is a new lane, not a revival.
    assert "CONTINUOUS" in RETIRED_TILE_LANES and policy.LANE not in RETIRED_TILE_LANES
    assert policy.SPEC.margin_cap_usd == 0.25


def test_prompt_is_the_august_v3_text_verbatim():
    assert policy.PROMPT_ID == "shared_direction_adx_evidence_v3_20260721"
    assert policy.PROMPT_SHA256 == "e307d8bd7e7bea65c5bb62cb46d5b2217b00ec5aa491076e1ee0f8a36d2cbef5"
    assert policy.AI_TEMPERATURE == 0.0
    messages, receipt = policy.render_messages({"price": E, "ret_1m": 0.02, "unknown_key": 1})
    assert len(messages) == 1 and messages[0]["role"] == "user"
    assert messages[0]["content"].endswith(policy.RESEARCH_AI_PROMPT_ADDENDUM)
    assert '"unknown_key"' not in messages[0]["content"]
    assert receipt["schema"] == "aug_input_projection_v1"
    assert receipt["dropped_keys"] == ["unknown_key"]
    assert receipt["aug_zero_inputs_now_populated"]["ret_1m"] is True
    assert receipt["aug_zero_inputs_now_populated"]["ret_5m"] is False
    assert len(receipt["prompt_sha256"]) == 64


def test_projection_keeps_context_order_and_nested_schema():
    projected, receipt = policy.project_context({
        "velocity": 1.0, "market_context": {"multi_tf": {"agreement": "BULL_ALIGNED", "extra": 1}},
        "delta": 2.0,
    })
    assert list(projected) == ["velocity", "market_context", "delta"]
    assert projected["market_context"] == {"multi_tf": {"agreement": "BULL_ALIGNED"}}
    assert "market_context.multi_tf.extra" in receipt["dropped_keys"]


@pytest.mark.parametrize("long_score,short_score,accepted,tier,reason", [
    (70, 30, True, "STRONG_APPROVE", "AUG_EXECUTE_TIER_STRONG_APPROVE"),
    (62, 50, True, "APPROVE", "AUG_EXECUTE_TIER_APPROVE"),
    (56, 50, True, "SOFT_APPROVE", "AUG_EXECUTE_TIER_SOFT_APPROVE"),
    (54, 50, False, "REJECT", "SCORE_GAP_BELOW_5"),
    (30, 15, False, "REJECT", "AI_RETURNED_ZERO_SCORES"),
])
def test_higher_score_gap_tiers(long_score, short_score, accepted, tier, reason):
    verdict = _decide(long_score, short_score)
    assert verdict["accepted"] is accepted
    assert verdict["tier"] == tier and verdict["reason"] == reason
    assert verdict["direction"] == ("LONG" if accepted else "NO_TRADE")


def test_side_is_the_higher_score_even_when_the_model_names_the_other():
    verdict = _decide(30, 70, direction="LONG")
    assert verdict["accepted"] and verdict["direction"] == "SHORT"


def test_structure_conflict_is_a_hard_reject():
    ctx = {"market_context": {"market_structure": {"structure_score": -3}}}
    verdict = _decide(70, 30, ctx=ctx)
    assert verdict["accepted"] is False and "STRUCTURE" in verdict["reason"]


def test_unparseable_reply_rejects():
    verdict = policy.decide({}, policy.parse_response("no json here"))
    assert verdict["accepted"] is False and verdict["reason"] == "AI_PARSE_FAILED"


def test_entry_is_a_ten_basis_point_maker_limit_with_flat_size():
    fields = policy.entry_fields("LONG", E)
    assert fields["planned_limit_price"] == pytest.approx(E * 0.999)
    assert fields["paper_only"] is True and fields["relay_eligible"] is False
    assert fields["fill_model"] == "PLATFORM_REALISTIC_BBO_DEPTH"
    assert fields["shadow_fill_model"] == "AUG_OPTIMISTIC_TOUCH"
    assert policy.entry_fields("SHORT", E)["planned_limit_price"] == pytest.approx(E * 1.001)
    sized = policy.account_risk_quantity(equity_usd=500.0, entry_price=E, atr_abs=50.0, leverage=100)
    assert sized["quantity"] == pytest.approx(0.25 * 100 / E)
    assert sized["capped_by"] == "FLAT_MARGIN"


def test_chase_runs_every_minute_for_ten_minutes_from_creation():
    assert policy.CHASE_STEP == 0.25
    assert policy.chase_due(created_ts=0, last_chase_ts=0, now=60)
    assert not policy.chase_due(created_ts=0, last_chase_ts=0, now=59)
    assert not policy.chase_due(created_ts=0, last_chase_ts=540, now=599)
    assert not policy.chase_due(created_ts=0, last_chase_ts=0, now=600)


def test_chase_holds_when_near_fill_or_the_original_gap_was_small():
    assert policy.chase_permitted(direction="LONG", limit_price=59_940.0,
                                  original_limit=59_940.0, market_price=60_100.0)[0]
    near = policy.chase_permitted(direction="LONG", limit_price=59_995.0,
                                  original_limit=59_940.0, market_price=60_000.0)
    assert near == (False, "NEAR_FILL")
    small = policy.chase_permitted(direction="LONG", limit_price=59_940.0,
                                   original_limit=59_995.0, market_price=60_004.0)
    assert small == (False, "ORIGINAL_GAP_TOO_SMALL")


def test_same_side_duplicate_within_fifteen_dollars_or_quarter_percent():
    rows = [{"direction": "LONG", "reference_price": 59_950.0, "trade_id": "a", "source": "PENDING_ORDER"}]
    assert policy.duplicate_exposure("LONG", 59_940.0, rows)["trade_id"] == "a"
    assert policy.duplicate_exposure("SHORT", 59_940.0, rows) is None
    assert policy.duplicate_exposure("LONG", 59_810.0, rows) is not None  # 0.25% band
    assert policy.duplicate_exposure("LONG", 59_500.0, rows) is None


def test_touch_shadow_matches_the_august_touch_rule():
    order = {"limit_price": 59_940.0, "side": "buy", "min_price_since_order": 59_939.0}
    assert policy.touch_shadow(order, price=60_000.0, bid=59_999.0, ask=60_000.0)
    order = {"limit_price": 59_940.0, "side": "buy"}
    assert not policy.touch_shadow(order, price=60_000.0, bid=59_999.0, ask=60_000.0)


def test_ladder_is_scenario_c():
    assert tuple(map(tuple, TRAIL_LADDER_SCENARIO_C)) == policy.LADDER == CONTINUOUS_AUG_LADDER


def test_exit_order_early_fail_before_stop():
    action = _exit(-33.0)
    assert action.reason == "EARLY_FAIL" and action.close_fraction == 1.0
    assert action.remaining_fraction == 0.0


def test_stop_loss_fires_when_early_fail_is_off_or_in_grace():
    assert _exit(-31.0, early_fail_enabled=False).reason == "STOP_LOSS"
    assert _exit(-31.0, in_post_fill_grace=True).reason == "STOP_LOSS"
    assert _exit(-29.0, early_fail_enabled=False, entry_thesis={}) is None


def test_profit_lock_ladder_and_peak_never_loser():
    assert _exit(4.0, peak_margin=9.0).reason == "PROFIT_LOCK_LADDER"
    assert _exit(6.0, peak_margin=9.0) is None
    assert policy.profit_lock_floor(45.0) == 28.0
    assert policy.profit_lock_floor(39.0) == 17.0


def test_spread_penalty_tightens_the_floor_by_one_point():
    assert policy.profit_lock_floor(12.0, conviction_spread=6, direction="LONG") == 11.0
    assert policy.profit_lock_floor(12.0, conviction_spread=5, direction="LONG",
                                    trend_health={"trend_state": "BULL_WEAKENING"}) == 11.0
    assert policy.profit_lock_floor(12.0, conviction_spread=5, direction="LONG") == 10.0
    assert policy.profit_lock_floor(8.2, conviction_spread=6) == pytest.approx(6.0)


def test_thesis_fast_cut_and_mfe_protection():
    thesis = {"mtf_agreement": "MIXED"}
    assert _exit(-12.5, entry_thesis=thesis, early_fail_enabled=False).reason == "THESIS_FAST_CUT"
    assert _exit(-12.5, peak_margin=6.0, entry_thesis=thesis, early_fail_enabled=False) is None
    assert _exit(-12.5, entry_thesis={}, early_fail_enabled=False) is None


def test_thesis_flip_after_five_minutes():
    thesis = {"mtf_agreement": "BULL_ALIGNED", "structure_score": 3}
    mc = {"multi_tf": {"agreement": "BEAR_ALIGNED"}, "market_structure": {"structure_score": -3}}
    assert _exit(-5.0, age=301.0, entry_thesis=thesis, market_context=mc).reason == "THESIS_INVALIDATED"
    assert _exit(-5.0, age=200.0, entry_thesis=thesis, market_context=mc) is None


def test_time_exit_after_two_hours():
    assert _exit(1.0, age=7201.0).reason == "TIME_EXIT"
    assert _exit(1.0, age=7199.0) is None


def test_card_states_realistic_primary_and_touch_shadow():
    card = policy.dashboard_policy()
    text = json.dumps(card)
    assert "realistic fills" in text and "touch-fill shadow" in text
    assert len(policy.AUG_DIFFERENCES) == 6


# ---------------------------------------------------------------------------
# bot.py wiring, compiled in isolation.
# ---------------------------------------------------------------------------
BOT = Path(__file__).with_name("bot.py")
BOT_TREE = ast.parse(BOT.read_text(encoding="utf-8"))


def _load(names, namespace):
    for name in names:
        node = next(i for i in BOT_TREE.body if isinstance(i, ast.FunctionDef) and i.name == name)
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(BOT), "exec"), namespace)
    return namespace


class _Quiet:
    def __getattr__(self, _name):
        return lambda *_a, **_k: None


class _Lock:
    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


class _Worker:
    def __init__(self):
        self.jobs = []

    def submit(self, key, payload, source_ts=None):
        self.jobs.append((key, payload))
        return True


def _wiring_namespace(enabled=True, reply=None, pending=(), positions=()):
    worker = _Worker()
    calls, writes, stamps, spawns = [], [], [], []

    def deepseek(messages, temperature, *, purpose, **_kw):
        calls.append((messages, temperature, purpose))
        return reply, 42, {"requested_model": "deepseek-flash", "served_model": "deepseek-flash",
                           "system_fingerprint": "fp"}

    ns = {
        "copy": copy, "time": __import__("time"), "logger": _Quiet(),
        "trade_lock": _Lock(), "state_lock": _Lock(),
        "pending_orders": list(pending), "open_positions": list(positions),
        "state": {"price": E},
        "nz": lambda v: v or 0,
        "TILE_POLICY_MODULES": {policy.LANE: policy},
        "_patient_chase_policy": lambda lane: policy,
        "COMBO_LANE_SPECS": COMBO_LANE_SPECS,
        "CONTINUOUS_AUG_ADMISSION_POLICY_ID": COMBO_LANE_SPECS[policy.LANE]["entry_policy"].get("admission_policy_id", "AUG"),
        "is_research_lane_enabled": lambda _lane: enabled,
        "_get_combo_lane_execution_worker": lambda _lane: worker,
        "_shared_ai_call_id": lambda ai_result=None, ctx=None: (ai_result or {}).get("shared_ai_call_id") or (ctx or {}).get("shared_ai_call_id"),
        "_stamp_shared_ai_lane_verdict": lambda *a, **k: stamps.append((a, k)),
        "_v3_lane_policy_material": lambda _lane: {"policy_signature": policy.POLICY_SIGNATURE},
        "_write_v3_shared_lane_decision": lambda *a, **k: writes.append((a, k)) or True,
        "enrich_ai_context_upgrade": lambda ctx: {**ctx, "ai_input_upgrade": {}},
        "sanitize_ai_inputs": lambda ctx: ctx,
        "validate_ai_features": lambda _ctx: (True, "ok"),
        "call_deepseek_api_with_meta": deepseek,
        "invert_signal_active": lambda: False,
        "compute_directional_spread": lambda direction, ai: 4,
        "combo_lane_match_detail": lambda *a, **k: {"passes": True},
        "_adaptive_regime_entry_decision": lambda *a, **k: None,
        "_enrich_combo_lane_features": lambda features, _ctx: features,
        "_spawn_combo_lane": lambda *a: spawns.append(a),
        "log_lane_opportunity_event": lambda *a, **k: None,
    }
    _load(("_lane_same_side_exposure", "_route_own_ai_tile", "_run_own_ai_tile_call",
           "_record_tile_decision_and_dispatch"), ns)
    return ns, worker, calls, writes, stamps, spawns


def test_tile_off_makes_no_model_call_and_records_the_skip():
    ns, worker, calls, writes, stamps, _ = _wiring_namespace(enabled=False)
    ns["_route_own_ai_tile"](policy.LANE, {"shared_ai_call_id": "c1"}, {"shared_ai_call_id": "c1"}, 1.0, {})
    assert worker.jobs == [] and calls == []
    assert writes[0][1]["execution_disposition"] == "LANE_DISABLED_NO_ORDER"
    assert writes[0][1]["exact_reason"] == "OWN_AI_CALL_SKIPPED_TILE_OFF"
    assert stamps and stamps[0][0][2] is False


def test_tile_on_queues_its_own_call_on_the_tile_worker():
    ns, worker, calls, writes, _, _ = _wiring_namespace()
    ns["_route_own_ai_tile"](policy.LANE, {"shared_ai_call_id": "c1"}, {"shared_ai_call_id": "c1"}, 1.0, {})
    assert calls == [] and writes == []
    key, payload = worker.jobs[0]
    assert key == f"{policy.LANE}:c1:own_ai" and payload["own_ai"] is True


def test_own_call_uses_the_august_prompt_and_spawns_on_the_higher_score():
    ns, _, calls, writes, _, spawns = _wiring_namespace(reply=_reply(30, 70, direction="LONG"))
    ns["_run_own_ai_tile_call"]({"target_lane": policy.LANE, "ctx": {"price": E},
                                 "ai": {"shared_ai_call_id": "c1"}, "features": {}, "edge_score": 1.0})
    messages, temperature, purpose = calls[0]
    assert temperature == 0.0 and purpose == "trading_direction_continuous_aug"
    assert messages[0]["content"].endswith(policy.RESEARCH_AI_PROMPT_ADDENDUM)
    assert writes[0][1]["execution_disposition"] == "ORDER_ELIGIBLE"
    tile_ai = spawns[0][1]
    assert tile_ai["decision"] == "APPROVE" and tile_ai["direction"] == "SHORT"
    assert tile_ai["own_ai_call"] is True and tile_ai["deepseek_served_model"] == "deepseek-flash"
    assert tile_ai["aug_input_projection"]["schema"] == "aug_input_projection_v1"


def test_own_call_reject_writes_a_verdict_and_never_spawns():
    ns, _, _, writes, _, spawns = _wiring_namespace(reply=_reply(52, 50))
    ns["_run_own_ai_tile_call"]({"target_lane": policy.LANE, "ctx": {}, "ai": {"shared_ai_call_id": "c1"},
                                 "features": {}, "edge_score": 1.0})
    assert spawns == []
    assert writes[0][1]["execution_disposition"] == "AI_REJECTED_NO_ORDER"
    assert writes[0][1]["exact_reason"] == "SCORE_GAP_BELOW_5"


def test_same_side_resting_limit_suppresses_a_duplicate_entry():
    pending = [{"status": "PENDING", "research_lane": policy.LANE, "signal_dir": "LONG",
                "limit_price": E * 0.999, "trade_id": "caug-1"}]
    ns, _, _, writes, _, spawns = _wiring_namespace(reply=_reply(70, 30), pending=pending)
    ns["_run_own_ai_tile_call"]({"target_lane": policy.LANE, "ctx": {}, "ai": {"shared_ai_call_id": "c2"},
                                 "features": {}, "edge_score": 1.0})
    assert spawns == []
    assert writes[0][1]["exact_reason"].startswith("AUG_DUPLICATE_EXPOSURE PENDING_ORDER caug-1")


def test_touch_shadow_is_recorded_without_deciding_the_fill():
    ns = {"time": __import__("time"), "TILE_POLICY_MODULES": {policy.LANE: policy}}
    _load(("_pending_limit_ready_for_fill",), ns)
    ns["_pending_limit_touched"] = lambda *_a, **_k: False
    order = {"research_lane": policy.LANE, "side": "buy", "limit_price": 59_940.0,
             "min_price_since_order": 59_930.0}
    assert ns["_pending_limit_ready_for_fill"](order, 60_000.0, bid=59_999.0, ask=60_000.0, now=5.0) is False
    shadow = order["aug_touch_fill_shadow"]
    assert shadow["fill_model"] == "AUG_OPTIMISTIC_TOUCH" and shadow["touch_price"] == 59_940.0
    assert shadow["touched_ts"] == 5.0


def test_exit_hook_passes_the_legacy_exit_context():
    source = ast.get_source_segment(
        BOT.read_text(encoding="utf-8"),
        next(i for i in BOT_TREE.body if isinstance(i, ast.FunctionDef) and i.name == "_apply_family_tile_exit"),
    )
    for key in ("peak_pct", "in_post_fill_grace", "early_fail_enabled", "conviction_spread",
                "trend_health", "entry_thesis", "market_context"):
        assert f'"{key}"' in source
    assert 'getattr(policy, "EXIT_CONTEXT", False)' in source

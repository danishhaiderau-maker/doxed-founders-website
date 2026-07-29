"""Section 10: Tile 2 (SR_MICRO_TILE_V2_STATIC) static-integrity test suite.

Covers the 18 required scenarios from Section 10 of the static integrity
repair prompt. Uses FORCE_PAPER_MODE so no real Bitfinex orders are ever
placed; the paper pending_orders list is inspected directly.

Run: cd services/btc-conservative-agent && python test_tile2_static.py
"""
import json
import inspect
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Boot safe: paper mode + research mode (no real Bitfinex calls).
os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import bot

# Never let this test suite overwrite the live holdout counter file. The
# functions below resolve TILE2_COUNTERS_FILE dynamically from the bot module.
_TEST_RUNTIME_DIR = tempfile.mkdtemp(prefix="tile2-static-tests-")
bot.TILE2_COUNTERS_FILE = os.path.join(_TEST_RUNTIME_DIR, "tile2_counters.json")

from bot import (
    execution_mode_for_lane,
    lane_can_place_new_entry,
    lane_is_live,
    EXEC_MODE_LAB_SHADOW,
    EXEC_MODE_PAPER,
    EXEC_MODE_LIVE,
    EXEC_MODE_EXIT_ONLY,
    RESEARCH_LANE_SR_MICRO_TILE_V2,
    RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC,
    pending_orders,
    open_positions,
    trades_map,
    trade_lock,
    state_lock,
    state,
    tile2_dashboard_metrics,
    tile2_policy_descriptor,
    tile2_entry_policy_hash,
    should_skip_fast_cut_for_mfe_protection,
    THESIS_FAST_EXIT_UNREAL_PCT,
    THESIS_MFE_PROTECT_PCT,
    TILE2_POLICY_ID,
    TILE2_EXIT_PROFILE_ID,
    TILE2_EXIT_PROFILE_ID_PROVISIONAL,
    reset_tile2_counters_for_fresh_holdout,
    load_tile2_counters_from_disk,
    reconcile_tile2_pending_orders_on_startup,
    record_tile2_eligible_long,
    record_tile2_eligible_side,
    _record_tile2_order_lifecycle,
    _record_tile2_event,
    _tile2_counters_bucket,
    _submit_tile2_paper_resting_limit,
    _tile2_paper_resting_limit_for_lane,
    _tile2_active_trade_for_side,
    _tile2_open_episode_ids,
    _cancel_tile2_paper_resting_limit,
    _is_static_no_chase_order,
    _apply_marketable_limit_fallback,
    _collect_dashboard_active_signals,
)
from sr_micro_tile_v2 import (
    evaluate_bracket,
    should_enter_bracket_leg,
    derive_sr_episode_id,
    utc_session_bucket_for_hour,
    london_blackout_active,
    POLICY_ID,
    EXIT_PROFILE_ID,
    EXIT_PROFILE_ID_PROVISIONAL,
    STATIC_ADX_TRENDING_THRESHOLD,
    STATIC_DISABLE_SHORT_LEG,
    STATIC_SESSION_BLACKLIST,
    SESSION_BUCKET_BOUNDARIES_UTC,
)

LANE = RESEARCH_LANE_SR_MICRO_TILE_V2_STATIC
passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    status = "PASS" if cond else "FAIL"
    line = f"  [{status}] {name}"
    if detail and not cond:
        line += f"  ({detail})"
    print(line)
    if cond:
        passed += 1
    else:
        failed += 1


def set_lane(on: bool):
    with state_lock:
        m = dict(state.get("research_lane_enabled") or {})
        m[LANE] = bool(on)
        state["research_lane_enabled"] = m


def set_bx(on: bool):
    with state_lock:
        state["bitfinex_live_enabled"] = bool(on)
        state["live_armed"] = bool(on)


def reset_state():
    """Clean pending orders + open positions + tile2 counters."""
    with trade_lock:
        pending_orders.clear()
        open_positions.clear()
    set_lane(True)
    set_bx(False)
    reset_tile2_counters_for_fresh_holdout()


def clear_pending():
    with trade_lock:
        pending_orders.clear()


print("=" * 78)
print("Section 10: Tile 2 (SR_MICRO_TILE_V2_STATIC) static-integrity tests")
print("=" * 78)

if bot.is_research_lane_retired(LANE):
    print("\n[RETIRED] Fail-closed production contract")
    set_lane(True)
    check("retired Tile 2 cannot be enabled", bot.is_research_lane_enabled(LANE) is False)
    check("retired Tile 2 cannot place new entries", lane_can_place_new_entry(LANE) is False)
    check("retired Tile 2 is not relay eligible", bot.relay_publishes_approve_outcome(LANE) is False)
    check(
        "retired Tile 2 is absent from active dashboard specs",
        all(row.get("lane") != LANE for row in bot.build_static_pathway_lane_specs()["lanes"]),
    )
    check(
        "retired Tile 2 evaluator exits before collection",
        "if is_research_lane_retired(lane):" in inspect.getsource(
            bot.maybe_tick_sr_micro_tile_v2_static_bracket
        ),
    )
    print(f"\nRESULT: {passed} passed, {failed} failed")
    raise SystemExit(0 if failed == 0 else 1)


# ---------------------------------------------------------------------------
# Group A: policy identifier sanity (Section 1/8 ground truth)
# ---------------------------------------------------------------------------
print("\n[A] Frozen policy identifiers")
check(
    "POLICY_ID matches the frozen Tile 2 identifier",
    POLICY_ID == "sr_micro_static_dual_leg_normalized_adx_vol_v2_20260720",
    f"got {POLICY_ID!r}",
)
check(
    "STATIC_ADX_TRENDING_THRESHOLD == 40",
    STATIC_ADX_TRENDING_THRESHOLD == 40,
    f"got {STATIC_ADX_TRENDING_THRESHOLD!r}",
)
check("SHORT is enabled in the dual-leg policy", STATIC_DISABLE_SHORT_LEG is False)
check(
    "LONDON is in the session blacklist",
    "LONDON" in STATIC_SESSION_BLACKLIST,
    f"got {sorted(STATIC_SESSION_BLACKLIST)!r}",
)
check(
    "tile2_policy_descriptor returns the correct policy_id",
    tile2_policy_descriptor().get("policy_id") == TILE2_POLICY_ID,
)
check(
    "tile2_policy_descriptor exposes the normalization version",
    tile2_policy_descriptor().get("indicator_normalization_version")
    == "normalized_adx_atr_percentile_v1",
)
check(
    "tile2_entry_policy_hash is a 12-char string",
    isinstance(tile2_entry_policy_hash(), str)
    and len(tile2_entry_policy_hash()) == 12,
)
check(
    "EXIT_PROFILE_ID_PROVISIONAL is the 12->10 ladder cohort",
    EXIT_PROFILE_ID_PROVISIONAL.endswith("_provisional"),
)

reset_state()
_record_tile2_event(
    "BRACKET_EVAL",
    block_reason="ADX_TRENDING",
    trade_id="srmv2s-bracket-observability",
    details={"adx_normalized": 54.3, "volatility_percentile": 61.0, "armed": False},
)
last_eval = tile2_dashboard_metrics().get("last_evaluation") or {}
check("latest deterministic gate reason is dashboard-visible", last_eval.get("block_reason") == "ADX_TRENDING")
check("latest deterministic ADX is dashboard-visible", (last_eval.get("details") or {}).get("adx_normalized") == 54.3)


# ---------------------------------------------------------------------------
# Group B: simultaneous independent LONG and SHORT direction slots
# ---------------------------------------------------------------------------
print("\n[B] Dual independent LONG/SHORT slots")
reset_state()

# A qualified STATIC edge observation arms both resting opportunities.
r = evaluate_bracket(
    price=60000,
    micro_support=59950,
    micro_resistance=60100,
    swing_low=59800,
    swing_high=60300,
    adx=25,
    vol_pct=40,
    lane=LANE,
    session_bucket="ASIA",
)
check("STATIC bracket arms SHORT at resistance", r.get("short_armed") is True)
check(
    "STATIC bracket arms LONG at support",
    r.get("long_armed") is True,
    f"result={r}",
)

# SHORT is a valid, non-marketable sell limit above market.
with state_lock:
    state["price"] = 60000.0
res_short = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-short"},
    side="SHORT",
    limit_price=60100.0,
    edge_score=4.0,
    features={},
    bracket_eval=r,
)
check(
    "SHORT resistance limit is submitted",
    res_short == "SPAWNED",
    f"got {res_short!r}",
)
res_long = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-long"},
    side="LONG",
    limit_price=59950.0,
    edge_score=4.0,
    features={},
    bracket_eval=r,
)
check("LONG support limit is also submitted", res_long == "SPAWNED", f"got {res_long!r}")
with trade_lock:
    dual_pending = [
        order for order in pending_orders
        if order.get("research_lane") == LANE and order.get("status") == "PENDING"
    ]
check("exactly two Tile 2 pending limits coexist", len(dual_pending) == 2)
check(
    "dual slots contain one LONG and one SHORT",
    {order.get("signal_dir") for order in dual_pending} == {"LONG", "SHORT"},
)
check(
    "SHORT keeps exchange side sell and exact resistance",
    any(
        order.get("signal_dir") == "SHORT"
        and order.get("side") == "sell"
        and abs(float(order.get("limit_price")) - 60100.0) < 0.01
        for order in dual_pending
    ),
)


# ---------------------------------------------------------------------------
# Group C: LONDON blackout (08:00-12:59 UTC)
# ---------------------------------------------------------------------------
print("\n[C] LONDON blackout")
reset_state()

# Helper sanity.
check(
    "london_blackout_active(8) is True",
    london_blackout_active(8) is True,
)
check(
    "london_blackout_active(12) is True",
    london_blackout_active(12) is True,
)
check(
    "london_blackout_active(13) is False",
    london_blackout_active(13) is False,
)
check(
    "utc_session_bucket_for_hour(9) == LONDON",
    utc_session_bucket_for_hour(9) == "LONDON",
)
check(
    "SESSION_BUCKET_BOUNDARIES_UTC LONDON is 08:00-12:59",
    SESSION_BUCKET_BOUNDARIES_UTC["LONDON"] == (8, 13),
)

# evaluate_bracket must refuse LONDON.
r_lon = evaluate_bracket(
    price=60000,
    micro_support=59950,
    micro_resistance=60100,
    swing_low=59800,
    swing_high=60300,
    adx=25,
    vol_pct=40,
    lane=LANE,
    session_bucket="LONDON",
)
check(
    "STATIC bracket refused in LONDON",
    str(r_lon.get("block_reason", "")).startswith("SESSION_BLACKLISTED")
    and r_lon.get("zone") == "SUSPENDED",
    f"got {r_lon}",
)


# ---------------------------------------------------------------------------
# Group D: ADX boundaries (fail-closed on missing, accept 35-40, refuse >40)
# ---------------------------------------------------------------------------
print("\n[D] ADX fail-closed + boundaries")
reset_state()

# Missing ADX -> MISSING_ADX (fail-closed).
r_no_adx = evaluate_bracket(
    price=60000,
    micro_support=59950,
    micro_resistance=60100,
    swing_low=59800,
    swing_high=60300,
    adx=None,
    vol_pct=40,
    lane=LANE,
    session_bucket="ASIA",
)
check(
    "STATIC refuses when ADX is missing (MISSING_ADX)",
    r_no_adx.get("block_reason") == "MISSING_ADX"
    and r_no_adx.get("zone") == "SUSPENDED",
    f"got {r_no_adx}",
)

# Missing vol_pct -> MISSING_VOLATILITY (fail-closed).
r_no_vol = evaluate_bracket(
    price=60000,
    micro_support=59950,
    micro_resistance=60100,
    swing_low=59800,
    swing_high=60300,
    adx=25,
    vol_pct=None,
    lane=LANE,
    session_bucket="ASIA",
)
check(
    "STATIC refuses when volatility is missing (MISSING_VOLATILITY)",
    r_no_vol.get("block_reason") == "MISSING_VOLATILITY",
    f"got {r_no_vol}",
)

# ADX 35 -> accepted (boundary below 40).
r_35 = evaluate_bracket(
    price=60000,
    micro_support=59950,
    micro_resistance=60100,
    swing_low=59800,
    swing_high=60300,
    adx=35,
    vol_pct=40,
    lane=LANE,
    session_bucket="ASIA",
)
check("STATIC accepts ADX=35", r_35.get("long_armed") is True, f"got {r_35}")

# ADX 40 -> accepted (cap is inclusive).
r_40 = evaluate_bracket(
    price=60000,
    micro_support=59950,
    micro_resistance=60100,
    swing_low=59800,
    swing_high=60300,
    adx=40,
    vol_pct=40,
    lane=LANE,
    session_bucket="ASIA",
)
check("STATIC accepts ADX=40 (inclusive cap)", r_40.get("long_armed") is True, f"got {r_40}")

# ADX 41 -> refused with ADX_TRENDING.
r_41 = evaluate_bracket(
    price=60000,
    micro_support=59950,
    micro_resistance=60100,
    swing_low=59800,
    swing_high=60300,
    adx=41,
    vol_pct=40,
    lane=LANE,
    session_bucket="ASIA",
)
check(
    "STATIC refuses ADX=41",
    r_41.get("block_reason") == "ADX_TRENDING",
    f"got {r_41}",
)


# ---------------------------------------------------------------------------
# Group E: exact support fill, no chase/reprice/slide, midpoint block
# ---------------------------------------------------------------------------
print("\n[E] Exact support fill + midpoint + no chase")
reset_state()

# Exact support fill: paper limit must be at exactly micro_support.
with state_lock:
    state["price"] = 60050.0  # market is above support
res = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-long"},
    side="LONG",
    limit_price=59950.0,
    edge_score=4.0,
    features={},
    bracket_eval=r_35,
    sr_episode_id="ep-1",
)
check("paper limit submit returns SPAWNED", res == "SPAWNED", f"got {res!r}")
pending = _tile2_paper_resting_limit_for_lane()
check("one paper limit exists after submit", pending is not None)
if pending:
    check(
        "limit is at exact micro_support (59950)",
        abs(float(pending.get("limit_price")) - 59950.0) < 0.01,
        f"got {pending.get('limit_price')}",
    )
    check(
        "paper limit marked paper_only",
        pending.get("paper_only") is True,
    )
    check(
        "paper limit marked exchange_submission_blocked",
        pending.get("exchange_submission_blocked") is True,
    )
    check(
        "paper limit stamped with policy_id",
        pending.get("policy_id") == TILE2_POLICY_ID,
    )
    check(
        "paper limit stamped with sr_episode_id",
        pending.get("sr_episode_id") == "ep-1",
    )
    check(
        "paper limit max_chase_count is 0 (no chase/reprice/slide)",
        pending.get("max_chase_count") == 0,
    )
    check(
        "paper limit stores internal direction as LONG",
        pending.get("signal_dir") == "LONG" and pending.get("dir") == "LONG",
        f"got signal_dir={pending.get('signal_dir')!r} dir={pending.get('dir')!r}",
    )
    check(
        "paper limit retains exchange side separately as buy",
        pending.get("side") == "buy",
        f"got {pending.get('side')!r}",
    )
    check(
        "static order is structurally excluded from all chase paths",
        _is_static_no_chase_order(pending) is True,
    )
    signal_ref = trades_map.get(pending.get("trade_id"), {}).get("signal_ref")
    check(
        "Tile 2 order has an active signal record",
        isinstance(signal_ref, dict)
        and signal_ref.get("status") == "ORDERED"
        and signal_ref.get("research_lane") == LANE,
    )
    active_rows, _ = _collect_dashboard_active_signals(
        list(pending_orders),
        list(open_positions),
        trades_map,
    )
    check(
        "Tile 2 pending order appears in dashboard Active Signals",
        any(
            row.get("trade_id") == pending.get("trade_id")
            and row.get("research_lane") == LANE
            for row in active_rows
        ),
    )
    check(
        "marketable fallback cannot move a static Tile 2 order",
        _apply_marketable_limit_fallback(
            pending,
            signal_ref or {},
            60100.0,
            float(pending.get("created_ts") or 0) + 3600,
        )
        is False,
    )
    check(
        "static Tile 2 limit price remains unchanged after fallback check",
        abs(float(pending.get("limit_price")) - 59950.0) < 0.01,
    )

# No chase / reprice: a second same-direction submit at the same level keeps
# the existing lifecycle and returns RESTING.
res2 = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-long-2"},
    side="LONG",
    limit_price=59950.0,
    edge_score=4.0,
    features={},
    bracket_eval=r_35,
    sr_episode_id="ep-1",
)
check(
    "second submit at same level refused (no respawn)",
    res2 == "RESTING",
    f"got {res2!r}",
)
_with_trade = 0
with trade_lock:
    _with_trade = sum(
        1 for o in pending_orders
        if isinstance(o, dict)
        and o.get("research_lane") == LANE
        and o.get("status") == "PENDING"
    )
check("exactly one PENDING Tile 2 limit after duplicate submit", _with_trade == 1)

# Would-cross refusal: a LONG limit above market must be refused.
clear_pending()
with state_lock:
    state["price"] = 59900.0
res_cross = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-cross"},
    side="LONG",
    limit_price=60000.0,  # above market
    edge_score=4.0,
    features={},
    bracket_eval=r_35,
    sr_episode_id="ep-cross",
)
check(
    "LONG limit above market refused (would cross)",
    res_cross == "REFUSED_WOULD_CROSS",
    f"got {res_cross!r}",
)

# Midpoint block: evaluate_bracket must refuse when price is in midpoint.
_ms, _mr = 59950.0, 60100.0
_mp = (_ms + _mr) / 2  # 60025
r_mid = evaluate_bracket(
    price=_mp,  # exactly at midpoint
    micro_support=_ms,
    micro_resistance=_mr,
    swing_low=59800,
    swing_high=60300,
    adx=25,
    vol_pct=40,
    lane=LANE,
    session_bucket="ASIA",
)
check(
    "STATIC bracket blocks at midpoint",
    r_mid.get("zone") == "MIDPOINT" or r_mid.get("block_reason") == "MIDPOINT",
    f"got {r_mid}",
)


# ---------------------------------------------------------------------------
# Group F: toggle routing (OFF=LAB, ON=local paper, platform relay is separate)
# ---------------------------------------------------------------------------
print("\n[F] Toggle routing")
reset_state()

# OFF -> LAB_SHADOW (no paper limit submitted).
set_lane(False)
clear_pending()
mode_off = execution_mode_for_lane(LANE)
check(
    "Tile OFF -> execution_mode is LAB_SHADOW",
    mode_off == EXEC_MODE_LAB_SHADOW,
    f"got {mode_off!r}",
)
check(
    "Tile OFF -> cannot place new entry",
    lane_can_place_new_entry(LANE) is False,
)

# ON + Bitfinex OFF -> PAPER.
set_lane(True)
set_bx(False)
mode_on = execution_mode_for_lane(LANE)
check(
    "Tile ON + Bitfinex OFF -> PAPER",
    mode_on == EXEC_MODE_PAPER,
    f"got {mode_on!r}",
)
check(
    "Tile ON + Bitfinex OFF -> can place new entry",
    lane_can_place_new_entry(LANE) is True,
)
check(
    "Tile 2 is explicitly eligible for the separate platform relay",
    bot.relay_publishes_approve_outcome(LANE) is True,
)
tile2_spec = next(
    row for row in bot.build_static_pathway_lane_specs()["lanes"]
    if row.get("lane") == LANE
)
check(
    "Tile 2 dashboard spec exposes the relay-gated operational contract",
    tile2_spec.get("platform_relay_eligible") is True
    and tile2_spec.get("exec_mode") == EXEC_MODE_PAPER,
    f"got {tile2_spec.get('exec_mode')!r} / {tile2_spec.get('platform_relay_eligible')!r}",
)
check(
    "dashboard tile renders lane pending/open counters",
    "d.lane_position_counts" in bot.DASHBOARD_JS
    and "statRow('Pending'" in bot.DASHBOARD_JS
    and "statRow('Open'" in bot.DASHBOARD_JS,
)

# ON stays a local PAPER lifecycle even if the bot's legacy direct-Bitfinex
# toggle is set. The platform relay is the only live-money path for Tile 2.
set_bx(True)
mode_live_attempt = execution_mode_for_lane(LANE)
check(
    "Tile ON + legacy direct Bitfinex flag still uses local PAPER",
    mode_live_attempt == EXEC_MODE_PAPER,
    f"got {mode_live_attempt!r}",
)
# Submit still creates the same local dashboard order.
with state_lock:
    state["price"] = 60050.0
res_live = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-live"},
    side="LONG",
    limit_price=59950.0,
    edge_score=4.0,
    features={},
    bracket_eval=r_35,
    sr_episode_id="ep-live",
)
p = _tile2_paper_resting_limit_for_lane()
check(
    "Tile ON creates local pending order while direct exchange stays blocked",
    res_live == "SPAWNED"
    and p is not None
    and p.get("paper_only") is True
    and p.get("exchange_submission_blocked") is True,
)
clear_pending()


# ---------------------------------------------------------------------------
# Group G: one active lifecycle per direction; opposite direction may coexist
# ---------------------------------------------------------------------------
print("\n[G] One active lifecycle per direction")
reset_state()
with state_lock:
    state["price"] = 60050.0

ep_id = derive_sr_episode_id(59950.0, 60100.0, session_bucket="ASIA")
check(
    "derive_sr_episode_id returns a non-empty string",
    isinstance(ep_id, str) and len(ep_id) > 0,
    f"got {ep_id!r}",
)
# Submit first paper limit.
res1 = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-ep-1"},
    side="LONG",
    limit_price=59950.0,
    edge_score=4.0,
    features={},
    bracket_eval=r_35,
    sr_episode_id=ep_id,
)
check("first paper limit on episode SPAWNED", res1 == "SPAWNED", f"got {res1!r}")

# Second LONG on the same level remains the same working lifecycle.
res2 = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-ep-2"},
    side="LONG",
    limit_price=59950.0,
    edge_score=4.0,
    features={},
    bracket_eval=r_35,
    sr_episode_id=ep_id,
)
check(
    "second same-direction paper limit does not clone",
    res2 == "RESTING",
    f"got {res2!r}",
)

# The opposite SHORT slot is independent and may coexist on the same episode.
res_short = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-ep-short"},
    side="SHORT",
    limit_price=60100.0,
    edge_score=4.0,
    features={},
    bracket_eval=r_35,
    sr_episode_id=ep_id,
)
check(
    "opposite-direction paper limit coexists on the same episode",
    res_short == "SPAWNED",
    f"got {res_short!r}",
)
check(
    "one LONG and one SHORT direction slot are occupied",
    _tile2_active_trade_for_side("LONG") is not None
    and _tile2_active_trade_for_side("SHORT") is not None,
)

# Distinct episode -> not in open set.
open_eps = _tile2_open_episode_ids()
check(
    "open episode set contains the active episode",
    ep_id in open_eps,
    f"got {open_eps}",
)


# ---------------------------------------------------------------------------
# Group H: entry TTL (30 min) on the paper limit
# ---------------------------------------------------------------------------
print("\n[H] Entry TTL = 30 minutes on unfilled limit")
reset_state()
with state_lock:
    state["price"] = 60050.0
before = time.time()
res = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-ttl"},
    side="LONG",
    limit_price=59950.0,
    edge_score=4.0,
    features={},
    bracket_eval=r_35,
    sr_episode_id="ep-ttl",
)
check("TTL test submit SPAWNED", res == "SPAWNED", f"got {res!r}")
p = _tile2_paper_resting_limit_for_lane()
check("TTL pending exists", p is not None)
if p:
    exp = float(p.get("entry_expires_ts") or 0)
    created = float(p.get("created_ts") or 0)
    ttl = exp - created
    check(
        "entry TTL is ~30 minutes (1800s +/- 5)",
        1795 <= ttl <= 1805,
        f"got ttl={ttl:.1f}s",
    )
    check(
        "entry_expires_ts is in the future",
        exp >= before,
    )


# ---------------------------------------------------------------------------
# Group I: peak-MFE protection parity (replay == live)
# ---------------------------------------------------------------------------
print("\n[I] Peak-MFE protection parity (Section 5)")
check(
    "skip fast cut when peak >= 2 and unreal <= -12",
    should_skip_fast_cut_for_mfe_protection(unreal_pct=-12, peak_mfe_pct=3) is True,
)
check(
    "apply fast cut when peak < 2 and unreal <= -12",
    should_skip_fast_cut_for_mfe_protection(unreal_pct=-12, peak_mfe_pct=1) is False,
)
check(
    "no fast cut at all when unreal > -12",
    should_skip_fast_cut_for_mfe_protection(unreal_pct=-5, peak_mfe_pct=3) is False,
)
check(
    "no skip when mfe_protect is 0 (gate disabled)",
    should_skip_fast_cut_for_mfe_protection(unreal_pct=-12, peak_mfe_pct=5, mfe_protect_pct=0) is False,
)
# Threshold values must match the frozen policy.
check(
    "THESIS_FAST_EXIT_UNREAL_PCT is -12.0 (frozen)",
    THESIS_FAST_EXIT_UNREAL_PCT == -12.0,
    f"got {THESIS_FAST_EXIT_UNREAL_PCT}",
)
check(
    "THESIS_MFE_PROTECT_PCT is 2.0 (frozen)",
    THESIS_MFE_PROTECT_PCT == 2.0,
    f"got {THESIS_MFE_PROTECT_PCT}",
)


# ---------------------------------------------------------------------------
# Group J: dashboard metrics reconcile + counters survive restart
# ---------------------------------------------------------------------------
print("\n[J] Dashboard metrics + restart survival")
reset_state()
# Record 5 eligible LONG plus 5 eligible SHORT direction slots.
for _ in range(5):
    record_tile2_eligible_long(episode_id=f"ep-{_}")
    record_tile2_eligible_side("SHORT", episode_id=f"ep-{_}")
# Simulate the real order lifecycle via the counter folder.
from bot import _record_tile2_event
for _ in range(3):
    _record_tile2_event("ORDER_SUBMITTED", direction="LONG", trade_id=f"order-{_}")
for _ in range(2):
    _record_tile2_event("FILLED", direction="LONG", trade_id=f"order-{_}")
    _record_tile2_event("CLOSED", direction="LONG", trade_id=f"order-{_}")
_record_tile2_event("TTL_EXPIRED")
_record_tile2_event("CANCELLED")
_record_tile2_event(
    "BRACKET_EVAL",
    block_reason="ADX_TRENDING",
    details={"adx_normalized": 54.3, "armed": False},
)

m = tile2_dashboard_metrics()
check(
    "metrics report bracket_evals >= 0",
    int(m.get("bracket_evals", -1)) >= 0,
)
check(
    "metrics report eligible_long == 5",
    m.get("eligible_long") == 5,
    f"got {m.get('eligible_long')}",
)
check(
    "metrics report eligible_short == 5 and total == 10",
    m.get("eligible_short") == 5 and m.get("eligible_total") == 10,
    f"got short={m.get('eligible_short')} total={m.get('eligible_total')}",
)
check(
    "metrics report paper_limits == 3",
    m.get("paper_limits") == 3,
    f"got {m.get('paper_limits')}",
)
check(
    "metrics report filled_closes == 2",
    m.get("filled_closes") == 2,
    f"got {m.get('filled_closes')}",
)
check(
    "metrics report ttl_expiries == 1",
    m.get("ttl_expiries") == 1,
    f"got {m.get('ttl_expiries')}",
)
check(
    "metrics report cancellations == 1",
    m.get("cancellations") == 1,
    f"got {m.get('cancellations')}",
)
check(
    "metrics carry the frozen policy_id",
    m.get("policy_id") == TILE2_POLICY_ID,
)
check(
    "metrics carry the frozen exit_profile_id",
    m.get("exit_profile_id") == TILE2_EXIT_PROFILE_ID,
)

# fill_rate = entry_fills / paper_limits = 2/3.
check(
    "fill_rate == 2/3 (0.6667)",
    abs(float(m.get("fill_rate", 0)) - (2 / 3)) < 0.01,
    f"got {m.get('fill_rate')}",
)

# Restart simulation: remove the in-memory bucket, then restore from disk.
with state_lock:
    state.pop("tile2_counters", None)
load_tile2_counters_from_disk()
m2 = tile2_dashboard_metrics()
check(
    "counters survive reload (both eligible directions preserved)",
    m2.get("eligible_long") == 5 and m2.get("eligible_short") == 5,
    f"got long={m2.get('eligible_long')} short={m2.get('eligible_short')}",
)
check(
    "cohort_id is always taken from running code after reload",
    m2.get("cohort_id") == TILE2_POLICY_ID,
)
check(
    "latest deterministic evaluation survives restart",
    (m2.get("last_evaluation") or {}).get("block_reason") == "ADX_TRENDING",
)
check(
    "Fresh Collection resets Tile 2 in-memory counters after archive/wipe",
    "reset_tile2_counters_for_fresh_holdout()"
    in inspect.getsource(bot._perform_fresh_collection_reset_locked),
)


# ---------------------------------------------------------------------------
# Group K: cancel path + filled position survives past 30m
# ---------------------------------------------------------------------------
print("\n[K] Cancel path + filled-position duration")
reset_state()
with state_lock:
    state["price"] = 60050.0
res = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-cancel"},
    side="LONG",
    limit_price=59950.0,
    edge_score=4.0,
    features={},
    bracket_eval=r_35,
    sr_episode_id="ep-cancel",
)
check("cancel-path submit SPAWNED", res == "SPAWNED", f"got {res!r}")
cancelled = _cancel_tile2_paper_resting_limit(reason="STRUCTURAL_CANCEL")
check(
    "cancel returns True when a limit existed",
    cancelled is True,
)
check(
    "no PENDING Tile 2 limit after cancel",
    _tile2_paper_resting_limit_for_lane() is None,
)

# Filled position must survive past 30m. We model this by directly inserting
# an open Tile 2 position with an entry_ts 45 minutes in the past and
# checking that the LAB TTL extension logic does NOT force-close it.
fake_now = time.time()
fake_pos = {
    "trade_id": "t-filled-survives",
    "research_lane": LANE,
    "status": "OPEN",
    "dir": "LONG",
    "entry": 59950.0,
    "entry_ts": fake_now - (45 * 60),  # 45 minutes ago (past 30m entry TTL)
    "leverage": 5,
    "sr_episode_id": "ep-filled-survives",
}
with trade_lock:
    open_positions.append(fake_pos)
open_eps = _tile2_open_episode_ids()
check(
    "filled position's episode is in the open set (no clone)",
    "ep-filled-survives" in open_eps,
    f"got {open_eps}",
)
# A new paper-limit submit on the SAME episode must still be refused because
# the filled position is open on it (no clone).
with state_lock:
    state["price"] = 60050.0
res_clone = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-clone-attempt"},
    side="LONG",
    limit_price=59950.0,
    edge_score=4.0,
    features={},
    bracket_eval=r_35,
    sr_episode_id="ep-filled-survives",
)
check(
    "no same-direction clone while a filled LONG position is open",
    res_clone == "REFUSED_DIRECTION_ACTIVE",
    f"got {res_clone!r}",
)
res_opposite = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-opposite-slot"},
    side="SHORT",
    limit_price=60100.0,
    edge_score=4.0,
    features={},
    bracket_eval=r_35,
    sr_episode_id="ep-filled-survives",
)
check(
    "open LONG does not suppress the independent SHORT slot",
    res_opposite == "SPAWNED",
    f"got {res_opposite!r}",
)
# Clean up.
with trade_lock:
    open_positions.clear()


# ---------------------------------------------------------------------------
# Group L: pending-order restart reconciliation and terminal lifecycle
# ---------------------------------------------------------------------------
print("\n[L] Pending-order restart reconciliation")
reset_state()
with state_lock:
    state["price"] = 60050.0
res_restart = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-restart"},
    side="LONG",
    limit_price=59950.0,
    edge_score=4.0,
    features={},
    bracket_eval=r_35,
    sr_episode_id="ep-restart",
)
with trade_lock:
    pending_before = list(pending_orders)
restart_tid = pending_before[0]["trade_id"] if pending_before else ""
check("restart test submitted a pending order", res_restart == "SPAWNED" and bool(restart_tid))
with state_lock:
    lifecycle_before = _tile2_counters_bucket().get("order_lifecycle") or {}
check(
    "pending order snapshot is durable",
    lifecycle_before.get(restart_tid, {}).get("status") == "PENDING"
    and isinstance(lifecycle_before.get(restart_tid, {}).get("order"), dict),
)
clear_pending()
reconciled = reconcile_tile2_pending_orders_on_startup()
check("unexpired pending order restores after restart", restart_tid in reconciled["restored"])
check(
    "restored order is back in the real pending registry",
    _tile2_paper_resting_limit_for_lane() is not None,
)

# An order whose TTL elapsed while the process was down becomes terminal.
restored_order = _tile2_paper_resting_limit_for_lane()
clear_pending()
expired_snapshot = dict(restored_order or {})
expired_snapshot["entry_expires_ts"] = time.time() - 1
_record_tile2_order_lifecycle(
    restart_tid,
    "PENDING",
    "TEST_EXPIRE_DURING_RESTART",
    order=expired_snapshot,
)
expired_reconcile = reconcile_tile2_pending_orders_on_startup()
check("expired-during-restart order is terminalized", restart_tid in expired_reconcile["expired"])
with state_lock:
    expired_lifecycle = _tile2_counters_bucket()["order_lifecycle"][restart_tid]
check("expired lifecycle status is explicit", expired_lifecycle["status"] == "TTL_EXPIRED")

# A legacy/incomplete pending snapshot must never be silently discarded.
_record_tile2_order_lifecycle("legacy-incomplete", "PENDING", "TEST_INCOMPLETE", order={})
orphan_reconcile = reconcile_tile2_pending_orders_on_startup()
check("incomplete pending snapshot is marked orphaned", "legacy-incomplete" in orphan_reconcile["orphaned"])
with state_lock:
    orphan_lifecycle = _tile2_counters_bucket()["order_lifecycle"]["legacy-incomplete"]
check("orphan lifecycle status is explicit", orphan_lifecycle["status"] == "ORPHANED_ON_RESTART")


# ---------------------------------------------------------------------------
# Group M: touched static limit opens a normal paper position
# ---------------------------------------------------------------------------
print("\n[M] Static fill direction and position lifecycle")
reset_state()
with state_lock:
    state["price"] = 60050.0
res_fill = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-fill-direction"},
    side="LONG",
    limit_price=59950.0,
    edge_score=4.0,
    features={"regime": "RANGE"},
    bracket_eval=r_35,
    sr_episode_id="ep-fill-direction",
)
fill_candidate = _tile2_paper_resting_limit_for_lane()
check("fill-direction submit SPAWNED", res_fill == "SPAWNED" and fill_candidate is not None)
_patched_names = (
    "log_lane_opportunity_event",
    "_emit_genome_execution_event",
    "mark_approve_research_executed",
    "persist_signal",
    "save_positions",
    "_relay_mirror",
    "pipeline_state_sync",
)
_originals = {name: getattr(bot, name) for name in _patched_names}
try:
    for name in _patched_names:
        setattr(bot, name, lambda *args, **kwargs: None)
    fill_candidate["fill_price"] = fill_candidate["limit_price"]
    fill_candidate["status"] = "FILLED"
    bot.fill_order(fill_candidate)
finally:
    for name, original in _originals.items():
        setattr(bot, name, original)
check(
    "touched Tile 2 order opens a LONG position without direction error",
    any(
        pos.get("trade_id") == fill_candidate.get("trade_id")
        and pos.get("dir") == "LONG"
        and pos.get("research_lane") == LANE
        for pos in open_positions
    ),
)
check(
    "filled Tile 2 order leaves the pending registry",
    not any(order.get("trade_id") == fill_candidate.get("trade_id") for order in pending_orders),
)

# The opposite slot must preserve SHORT through the same fill lifecycle even
# while the LONG position remains open in the showcase paper book.
with state_lock:
    state["price"] = 60050.0
res_short_fill = _submit_tile2_paper_resting_limit(
    ctx={"trade_id": "t-short-fill-direction"},
    side="SHORT",
    limit_price=60100.0,
    edge_score=4.0,
    features={"regime": "RANGE"},
    bracket_eval=r_35,
    sr_episode_id="ep-short-fill-direction",
)
short_fill_candidate = _tile2_paper_resting_limit_for_lane("SHORT")
check(
    "SHORT fill-direction submit SPAWNED",
    res_short_fill == "SPAWNED" and short_fill_candidate is not None,
)
_originals = {name: getattr(bot, name) for name in _patched_names}
try:
    for name in _patched_names:
        setattr(bot, name, lambda *args, **kwargs: None)
    short_fill_candidate["fill_price"] = short_fill_candidate["limit_price"]
    short_fill_candidate["status"] = "FILLED"
    bot.fill_order(short_fill_candidate)
finally:
    for name, original in _originals.items():
        setattr(bot, name, original)
check(
    "touched resistance order opens a SHORT position beside the LONG",
    any(
        pos.get("trade_id") == short_fill_candidate.get("trade_id")
        and pos.get("dir") == "SHORT"
        and pos.get("research_lane") == LANE
        for pos in open_positions
    ),
)
check(
    "showcase paper book holds one LONG and one SHORT position",
    {pos.get("dir") for pos in open_positions if pos.get("research_lane") == LANE}
    == {"LONG", "SHORT"},
)
check(
    "filled SHORT leaves the pending registry",
    not any(
        order.get("trade_id") == short_fill_candidate.get("trade_id")
        for order in pending_orders
    ),
)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
print()
print("=" * 78)
print(f"RESULT: {passed} passed, {failed} failed")
print("=" * 78)
sys.exit(0 if failed == 0 else 1)

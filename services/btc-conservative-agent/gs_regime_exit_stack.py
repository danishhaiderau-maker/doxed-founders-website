"""Streaming first-trigger-wins exit stacks of the GS-20261004 pre-registrations.

Pure and deterministic: one call per side-correct tick of an open position.
The offline references are Grok Strategist's ``gslib.simulate_exit`` (GS-01..04,
profile ``stack == "GS_SIMPLE_V1"``) and ``dynlib.simulate`` (B1..B3,
``"DYNLIB_REGIME_V1"``); the profile dicts live in the tile registry
(combo_pathway_config.GSB_PROFILES / _gs_simple_exit) and are part of each
tile's policy signature.

Semantics replicated per tick ``i`` (``cur`` = side-correct mark vs entry, bp):

* hard stop ``cur <= -hard`` (never on the fill tick);
* thesis cut ``cur <= -cut`` while ``age <= cut_win`` (MOM / GS: any tick;
  REV: only the first tick at or after each ``cut_close_sec`` close); when the
  profile sets ``cut_max_peak_bp`` the cut is conditional: it fires only while
  the trade's MFE (peak side-correct bp) has never exceeded that value;
* break-even: armed on a tick with ``cur >= max(be_floor, be_atr*ATR)``; fires
  on a LATER tick with ``cur <= lock``;
* ATR trail: armed at ``cur >= max(arm_floor, arm_atr*ATR)``; fires on a later
  tick with ``cur <= peak - max(trail_floor, trail_atr*ATR)``;
* MFE give-back: armed at ``cur >= gb_arm``; fires at ``cur <= peak*(1-gb_frac)``;
* volatility shock: 60 s mid range ``R60 >= shock_k*ATR`` and the 60 s move
  against the side ``<= -0.5*R60``, not in the first 5 s (REV: only on ticks
  strictly after break-even armed, except REV TREND);
* indicator flip: a bar closing after the fill whose flip score turns against
  the side (previous bar not against); REV: only bars after break-even armed;
* maker take-profit at ``max(tp_floor, tp_atr*ATR)``; ladder TP1 (50 %) at
  ``max(tp1_floor, tp1_atr*ATR)`` when TP1 < TP (or no TP), then the remainder
  exits at ``cur <= lock`` (LADDER_LOCK) unless a main exit fires first (a
  main exit wins a same-tick tie);
* time backstop ``age >= time_sec``.

Ties on one tick resolve by the profile's ``order``. Maker take-profits book
the target price; every other exit books the side-correct tick that fired it.
"""
from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Optional

DEFAULT_ATR_BP = 4.0
SHOCK_MIN_AGE_SEC = 5.0
STATE_SCHEMA = "gs_regime_exit_state_v1"


def atr_or_default(atr_bp) -> float:
    try:
        value = float(atr_bp)
    except (TypeError, ValueError):
        return DEFAULT_ATR_BP
    return value if value > 0 and value == value and value != float("inf") else DEFAULT_ATR_BP


def _scaled(k, atr: float, floor) -> Optional[float]:
    return None if k is None else max(float(floor), float(k) * atr)


def levels(profile: Mapping[str, Any], atr_bp) -> dict:
    """Resolved bp levels of one profile at the signal ATR (None = rule absent)."""
    atr = atr_or_default(atr_bp)
    tp = _scaled(profile.get("tp_atr"), atr, profile.get("tp_floor", 8.0))
    tp1 = _scaled(profile.get("tp1_atr"), atr, profile.get("tp1_floor", 6.0))
    if tp1 is not None and tp is not None and not tp1 < tp:
        tp1 = None
    return {
        "atr_bp": atr,
        "hard": float(profile["hard_bp"]),
        "cut": None if profile.get("cut_bp") is None else float(profile["cut_bp"]),
        "be": _scaled(profile.get("be_atr"), atr, profile.get("be_floor", 6.0)),
        "trail_arm": _scaled(profile.get("trail_arm_atr"), atr, profile.get("trail_arm_floor", 8.0))
        if profile.get("trail_atr") else None,
        "trail_dist": _scaled(profile.get("trail_atr"), atr, profile.get("trail_floor", 5.0)),
        "gb_arm": None if profile.get("gb_arm") is None else float(profile["gb_arm"]),
        "tp": tp,
        "tp1": tp1,
        "shock": None if profile.get("shock_k") is None else float(profile["shock_k"]) * atr,
    }


def new_state() -> dict:
    return {"schema": STATE_SCHEMA, "peak_bp": None, "be_armed_age": None, "trail_armed": False,
            "gb_armed": False, "tp1_done": False, "cut_closes_checked": 0, "flip_seen_until": None,
            "ticks": 0}


def evaluate_tick(profile: Mapping[str, Any], state: MutableMapping[str, Any], *, cur_bp: float, age_sec: float,
                  atr_bp, maker_hit_bp: float | None = None, shock: Mapping[str, Any] | None = None,
                  bars: list | None = None, side_sign: int = 1, fill_ts: float | None = None) -> Optional[dict]:
    """One tick. Returns None or {"rule", "partial", "close_fraction", "book_bp", "maker"}; mutates ``state``.

    ``maker_hit_bp``: the favourable bp at which a resting maker exit at this
    tick's side-correct mark would have been traded through (the mark itself
    when it is used as the trade-through proxy). ``shock``: {"r60_bp",
    "ret60_bp"}. ``bars``: [{"available_ts", "score", "prev_score"}] of the
    flip indicator for bars available after the fill, oldest first.
    """
    lv = levels(profile, atr_bp)
    cur = float(cur_bp)
    age = float(age_sec)
    first_tick = int(state.get("ticks") or 0) == 0 and age <= 0.0
    state["ticks"] = int(state.get("ticks") or 0) + 1
    prev_peak = state.get("peak_bp")
    peak = cur if prev_peak is None else max(float(prev_peak), cur)
    state["peak_bp"] = peak
    be_armed_age = state.get("be_armed_age")
    be_armed_before = be_armed_age is not None
    hit = {}

    if not first_tick and cur <= -lv["hard"]:
        hit["HARD_STOP"] = cur
    cut_peak_cap = profile.get("cut_max_peak_bp")
    cut_allowed = cut_peak_cap is None or peak <= float(cut_peak_cap)
    if lv["cut"] is not None and age <= float(profile.get("cut_win_sec") or 300):
        step = int(profile.get("cut_close_sec") or 1)
        if step <= 1:
            if not first_tick and cut_allowed and cur <= -lv["cut"]:
                hit["THESIS_CUT"] = cur
        else:
            due = int(age // step)
            checked = int(state.get("cut_closes_checked") or 0)
            if due > checked:
                state["cut_closes_checked"] = due
                if cut_allowed and cur <= -lv["cut"]:
                    hit["THESIS_CUT"] = cur
    lock_bp = float(profile.get("lock_bp", 1.0))
    # Once BE is armed the stop floor is entry+lock_bp in the trade's favour.
    # Book at that floor even when the tick has already gapped through it.
    if be_armed_before and cur <= lock_bp:
        hit["BREAKEVEN_LOCK"] = lock_bp
    if lv["trail_dist"] is not None and state.get("trail_armed") and cur <= peak - lv["trail_dist"]:
        trail_level = peak - lv["trail_dist"]
        if be_armed_before:
            trail_level = max(trail_level, lock_bp)
        hit["ATR_TRAIL"] = trail_level
    if lv["gb_arm"] is not None and state.get("gb_armed") and cur <= peak * (1.0 - float(profile["gb_frac"])):
        hit["MFE_GIVEBACK"] = cur
    if lv["shock"] is not None and shock and age >= SHOCK_MIN_AGE_SEC:
        r60, ret60 = shock.get("r60_bp"), shock.get("ret60_bp")
        allowed = be_armed_before if profile.get("shock_profit_only") else True
        if allowed and r60 is not None and ret60 is not None and float(r60) >= lv["shock"] \
                and side_sign * float(ret60) <= -0.5 * float(r60):
            hit["VOL_SHOCK"] = cur
    if profile.get("flip") and bars:
        seen = state.get("flip_seen_until")
        for bar in bars:
            avail = float(bar["available_ts"])
            if seen is not None and avail <= float(seen):
                continue
            state["flip_seen_until"] = avail
            score, prev = int(bar.get("score") or 0), int(bar.get("prev_score") or 0)
            if score * side_sign < 0 and prev * side_sign >= 0:
                if profile.get("flip_profit_only"):
                    armed_ts = None if be_armed_age is None or fill_ts is None else float(fill_ts) + float(be_armed_age)
                    if armed_ts is None or not avail > armed_ts:
                        continue
                hit["INDICATOR_FLIP"] = cur
                break
    favourable = cur if maker_hit_bp is None else float(maker_hit_bp)
    if lv["tp"] is not None and not first_tick and favourable > lv["tp"]:
        hit["ATR_TAKE_PROFIT"] = lv["tp"]

    # Arming uses this tick (exits above only saw arming from earlier ticks).
    if lv["be"] is not None and be_armed_age is None and cur >= lv["be"]:
        state["be_armed_age"] = age
    if lv["trail_arm"] is not None and not state.get("trail_armed") and cur >= lv["trail_arm"]:
        state["trail_armed"] = True
    if lv["gb_arm"] is not None and not state.get("gb_armed") and cur >= lv["gb_arm"]:
        state["gb_armed"] = True

    order = tuple(profile.get("order") or ())
    for rule in order:
        if rule in hit:
            return {"rule": rule, "partial": False, "close_fraction": None, "book_bp": hit[rule],
                    "maker": rule == "ATR_TAKE_PROFIT"}
    if state.get("tp1_done") and cur <= float(profile.get("lock_bp", 2.0)):
        ladder_lock = float(profile.get("lock_bp", 2.0))
        return {"rule": "LADDER_LOCK", "partial": False, "close_fraction": None,
                "book_bp": ladder_lock, "maker": False}
    if lv["tp1"] is not None and not state.get("tp1_done") and not first_tick and favourable > lv["tp1"]:
        state["tp1_done"] = True
        return {"rule": "LADDER_TP1", "partial": True, "close_fraction": float(profile.get("tp1_frac", 0.5)),
                "book_bp": lv["tp1"], "maker": True}
    if age >= float(profile["time_sec"]):
        return {"rule": "TIME_BACKSTOP", "partial": False, "close_fraction": None, "book_bp": cur, "maker": False}
    return None

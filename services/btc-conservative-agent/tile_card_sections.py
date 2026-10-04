"""Plain-English ENTRY / EXIT / RISK MANAGEMENT card sections from registry metadata.

Pure functions of one registry tile spec (combo_pathway_config). The Fly
dashboard, the authenticated API, the :9001 analyzer tile views and the
registry validator all use this one generator, so a card can never describe a
rule the registry does not carry. Size is always stated as margin and notional;
margin is never described as the maximum loss.
"""
from __future__ import annotations

CARD_SECTIONS_SCHEMA = "tile_card_sections_v1"
DEFAULT_LEVERAGE = 100.0

_SESSION_HOURS = {"ASIA": (0, 8), "EU": (8, 16), "US": (16, 24)}
_SESSION_NAMES = {"ASIA": "Asia", "EU": "EU", "US": "US"}


def _num(value) -> str:
    v = float(value)
    return f"{v:g}"


def _bp(value) -> str:
    v = float(value)
    return f"{'+' if v > 0 else ''}{v:g} bp"


def _minutes(sec) -> str:
    s = int(sec or 0)
    if s and s % 3600 == 0:
        return f"{s // 3600} h"
    if s % 60 == 0:
        return f"{s // 60} min"
    return f"{s} s"


def _sessions_text(entry: dict) -> str:
    sessions = tuple(entry.get("allowed_sessions") or ())
    if not sessions or set(sessions) == set(_SESSION_HOURS):
        return "Sessions: all (Asia, EU and US)"
    hours = entry.get("session_hours_utc") or _SESSION_HOURS
    parts = []
    for s in sessions:
        lo, hi = (hours.get(s) or _SESSION_HOURS.get(s) or (0, 0))[:2]
        parts.append(f"{_SESSION_NAMES.get(s, s)} {int(lo):02d}-{int(hi):02d}")
    return "Sessions: " + " + ".join(parts) + " UTC only"


def _side_text(entry: dict) -> str:
    source = str(entry.get("direction_source") or "")
    return {
        "INVERTED_SCORE_LED_SIDE": "Side: opposite of the AI's committed side (fade)",
        "SCORE_LED_SIDE": "Side: the higher AI score's side (follow)",
        "OWN_AI_CALL_HIGHER_SCORE": "Side: the higher score's side from the tile's own AI call",
        "CROSS_VENUE_LEAD_OR_PREMIUM": "Side: the leading venues' direction; opposite triggers never trade",
        "CROSS_VENUE_PREMIUM": "Side: toward the leading venues when their premium leaves its mean (convergence)",
        "RANDOM_COIN_ON_COMMITTED_CALL": "Side: deterministic coin flip per call (execution-cost control, not the AI)",
    }.get(source, f"Side: {source.replace('_', ' ').lower() or 'not declared'}")


def entry_lines(spec: dict) -> list[str]:
    entry = dict(spec.get("entry_policy") or {})
    mode = str(entry.get("mode") or "")
    lines = [f"Signal: {spec.get('signal_summary') or 'not declared'}", _side_text(entry)]
    offset = entry.get("offset_pct")
    ttl = entry.get("maker_ttl_sec") or spec.get("entry_ttl_sec")
    if mode == "MAKER_LIMIT_OFFSET_CONFIRM_MARKET":
        lines.append(f"Order: resting limit {_num(offset)}% better than the signal price")
        lines.append(
            f"Chase: if price moves {_num(entry['confirm_move_bps'])} bp our way before the fill, cancel and take "
            f"the market within a {_num(entry['confirm_market_cap_bps'])} bp cap (skip if the cap is exceeded)"
        )
        lines.append(f"Time limit: neither within {_minutes(ttl)} - signal dropped")
    elif mode == "MAKER_LIMIT_OFFSET":
        lines.append(f"Order: passive maker limit {_num(offset)}% beyond the decision price, never past the touch")
        lines.append("Chase: none")
        lines.append(f"Time limit: unfilled after {_minutes(ttl)} - expires")
    elif mode == "MAKER_LIMIT_OFFSET_CHASE":
        lines.append(f"Order: passive maker limit {_num(offset)}% beyond the decision price")
        step = entry.get("remaining_gap_step_pct")
        windows = tuple(entry.get("chase_windows") or ())
        if entry.get("chase_max_age_sec"):
            lines.append(f"Chase: {_num(step)}% of the remaining gap every {_minutes(entry.get('reprice_sec'))} "
                         f"for the first {_minutes(entry['chase_max_age_sec'])}")
        elif windows:
            # chase_windows are 5-minute buckets after order creation.
            lo, hi = min(windows) * 5, (max(windows) + 1) * 5
            lines.append(f"Chase: {_num(step)}% of the remaining gap every {_minutes(entry.get('reprice_sec'))} "
                         f"in minutes {lo}-{hi}")
        else:
            lines.append("Chase: none")
        lines.append(f"Time limit: unfilled after {_minutes(ttl)} - expires")
    elif mode == "TAKER_AT_SIGNAL":
        lines.append(f"Order: taker at the signal within a {_num(entry.get('taker_protection_bps') or 0)} bp cap")
        lines.append("Chase: none")
        lines.append(f"Time limit: unfilled after {_minutes(entry.get('taker_ttl_sec') or spec.get('entry_ttl_sec'))}"
                     " - cancelled")
    else:
        lines.append(f"Order: {mode.replace('_', ' ').lower() or 'not declared'}")
    lines.append(_sessions_text(entry))
    gates = []
    if entry.get("max_spread_bps") is not None:
        gates.append(f"spread > {_num(entry['max_spread_bps'])} bp")
    feed = entry.get("max_bbo_age_sec")
    if feed is not None:
        gates.append(f"quote feed older than {_num(feed)} s")
    if entry.get("max_venue_age_sec") is not None:
        gates.append(f"any venue feed older than {_num(entry['max_venue_age_sec'])} s")
    if gates:
        lines.append("Stand aside if: " + ", ".join(gates))
    return lines


def _live_exit_text(rule: str, exit_policy: dict, spec: dict) -> str | None:
    be = exit_policy.get("breakeven") or {}
    trail = exit_policy.get("trail") or {}
    cut = exit_policy.get("early_cut") or {}
    if rule == "HARD_STOP":
        return f"Hard stop {_bp(-float(exit_policy.get('hard_stop_bps') or exit_policy.get('hard_stop_margin_pct') or 0))}"
    if rule == "BREAKEVEN_LOCK" and be:
        return (f"Break-even armed at {_bp(be['trigger_margin_pct'])} → stop moves to {_bp(be['lock_margin_pct'])}")
    if rule == "ATR_TRAIL" and trail:
        return (f"ATR trail {_num(trail['atr_k'])} ATR behind the peak, armed after +{_num(trail['arm_atr_k'])} ATR")
    if rule == "EARLY_CUT" and cut:
        return (f"Early cut at {_bp(cut['cut_margin_pct'])} within {_minutes(cut['window_sec'])}, only if the trade "
                f"never ran past {_bp(cut['max_peak_margin_pct'])}")
    if rule == "TIME_EXIT":
        return f"Time backstop: close after {_minutes(exit_policy.get('max_duration_sec') or spec.get('path_end_sec'))}"
    # Continuous (August replica) rules, margin % at its leverage.
    if rule == "EARLY_FAIL":
        return (f"Early fail at {_num(exit_policy['early_fail_margin_pct'])}% margin after a "
                f"{_minutes(exit_policy.get('post_fill_grace_sec'))} grace")
    if rule == "STOP_LOSS":
        return f"Stop loss at -{_num(exit_policy['hard_stop_margin_pct'])}% margin"
    if rule == "PROFIT_LOCK_LADDER":
        ladder = tuple(spec.get("ladder") or ())
        first = f" (first rung +{_num(ladder[0][0])}% → lock +{_num(ladder[0][1])}%)" if ladder else ""
        floor = ""
        if exit_policy.get("peak_never_loser_min_peak") is not None:
            floor = (f"; peak floor +{_num(exit_policy['peak_never_loser_floor'])}% once the peak reaches "
                     f"+{_num(exit_policy['peak_never_loser_min_peak'])}%")
        return f"Scenario C profit-lock ladder{first}{floor}"
    if rule == "THESIS_FAST_CUT":
        return (f"Thesis cut at {_num(exit_policy['thesis_cut_margin_pct'])}% margin when the AI re-call no longer "
                f"supports the side (MFE protect {_num(exit_policy.get('thesis_mfe_protect_pct') or 0)}%)")
    if rule == "THESIS_INVALIDATED":
        return "Thesis invalidated: the AI re-call flips or decays below the entry thesis"
    return None


def exit_sections(spec: dict, shadow_exit_set: dict | None = None) -> dict:
    exit_policy = dict(spec.get("exit_policy") or {})
    live = []
    for rule in tuple(spec.get("live_exit_order") or ()):
        if rule in ("HARD_STOP", "EARLY_CUT", "EARLY_FAIL", "STOP_LOSS"):
            continue
        text = _live_exit_text(rule, exit_policy, spec)
        if text:
            live.append(text)
    shadow_set = shadow_exit_set or {}
    shadow = [str((shadow_set.get(key) or {}).get("label") or key) for key in tuple(spec.get("shadow_exits") or ())]
    return {"order": "first trigger wins", "live": live, "shadow": shadow}


def risk_lines(spec: dict, leverage: float = DEFAULT_LEVERAGE) -> list[str]:
    exit_policy = dict(spec.get("exit_policy") or {})
    lines = []
    order = tuple(spec.get("live_exit_order") or ())
    cut_rules = [r for r in order if r in ("EARLY_CUT", "EARLY_FAIL")]
    if cut_rules:
        for rule in cut_rules:
            lines.append(_live_exit_text(rule, exit_policy, spec))
    elif spec.get("early_cut_shadow_reason"):
        lines.append(f"Early cut: shadow only - {spec['early_cut_shadow_reason']}")
    else:
        lines.append("Early cut: none")
    for rule in order:
        if rule in ("HARD_STOP", "STOP_LOSS"):
            lines.append(_live_exit_text(rule, exit_policy, spec))
    margin = float(spec.get("requested_margin_usd") or spec.get("margin_usd") or 0.0)
    lev = float(spec.get("leverage") or leverage)
    lines.append(f"Size: ${margin:.2f} margin @{lev:g}x ≈ ${margin * lev:,.0f} notional")
    lines.append(f"Max concurrent: {int(spec.get('max_active_signals') or 0)}")
    kill = str(spec.get("kill_criteria") or "")
    if kill:
        lines.append(f"Kill rules: {kill}")
    elif spec.get("is_benchmark") or spec.get("default_enabled"):
        lines.append("Kill rules: none - permanent baseline, never promoted or retired for performance")
    else:
        lines.append("Kill rules: not declared")
    paper = "Paper only" if spec.get("paper_only") else "NOT paper-only"
    relay = "relay-ineligible" if not spec.get("platform_relay_eligible") else "relay-eligible"
    lines.append(f"{paper} · {relay} (a tile toggle never arms Bitfinex)")
    return lines


def card_sections(spec: dict, shadow_exit_set: dict | None = None, leverage: float = DEFAULT_LEVERAGE) -> dict:
    """{"entry": [...], "exit": {"order", "live": [...], "shadow": [...]}, "risk": [...]} for one tile."""
    return {
        "schema": CARD_SECTIONS_SCHEMA,
        "entry": entry_lines(spec),
        "exit": exit_sections(spec, shadow_exit_set),
        "risk": risk_lines(spec, leverage),
    }


def card_section_defects(spec: dict, shadow_exit_set: dict | None = None) -> list[str]:
    """Missing-metadata defects for the registry validator."""
    out = []
    try:
        sections = card_sections(spec, shadow_exit_set)
    except (KeyError, TypeError, ValueError) as exc:
        return [f"CARD_SECTIONS_UNRENDERABLE:{type(exc).__name__}:{exc}"]
    if not spec.get("signal_summary"):
        out.append("CARD_ENTRY_MISSING_SIGNAL")
    if not sections["entry"] or any("not declared" in line for line in sections["entry"]):
        out.append("CARD_ENTRY_INCOMPLETE")
    if not sections["exit"]["live"]:
        out.append("CARD_EXIT_MISSING_LIVE_RULES")
    if not any(r in ("HARD_STOP", "STOP_LOSS") for r in tuple(spec.get("live_exit_order") or ())):
        out.append("CARD_RISK_MISSING_HARD_STOP")
    if not sections["risk"] or any("not declared" in line for line in sections["risk"]):
        out.append("CARD_RISK_INCOMPLETE")
    text = " ".join(sections["entry"] + sections["exit"]["live"] + sections["exit"]["shadow"] + sections["risk"]).lower()
    if "max loss" in text or "maximum loss" in text:
        out.append("CARD_FORBIDDEN_MAX_LOSS_WORDING")
    return out

"""Offline Pin 8/15/16 arithmetic. No statistical validation or live authority.

Record-i confidence and risk are caller supplied. Backtest record-ii is gross;
no fee, funding, spread or slippage allowance is deducted here.
"""
# PROVISIONAL, pre-freeze, non-evidential. Live OFF. No runtime integration.
PIN7_SPEC_SHA256 = "dc0cc45f24f5afee9a69718fcc120fc27450f80b6e35de1058f0ed0e68eedf79"
P8_SPEC_SHA256 = "28315aeb4423e88d4e3dadcf3e42005b29ddbba1f2e054467aeab7827135641e"
CT_TABLE_SHA256 = "37603987e6dabdc42ce89aec9482a3366d881f14c5d7c53356307e890c7595f0"
RULES_SHA256 = "4305f12a02d23f5cad689fd388387ffad3ecc50e02e614c883894d7ef3bf94f2"
RK_PROVISIONAL_RULING = "72c250a0eb82"

import math
from collections import Counter, defaultdict
from .fill_model import taker_fill_rows


def sign(side):
    if side not in ("LONG", "SHORT"):
        raise ValueError("INVALID_SIDE")
    return 1 if side == "LONG" else -1


def worst_extreme(bars, side):
    sign(side)
    if not bars:
        return None
    key = "low" if side == "LONG" else "high"
    values = [b[key] for b in bars]
    if any(not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError("INVALID_EXTREME")
    return min(values) if side == "LONG" else max(values)


def unpriced_without_entry_basis(holding_bars, side):
    """OPEN 15(c) interpretation: never manufacture an entry/basis or a fill.

    Keep (a) NO_FILL in capture, not filled denominator; disclose (c)'s
    worst holding-window exit reference separately, with no numerical P&L.
    This is provisional, not a board resolution of (a)/(c) precedence.
    """
    return dict(category="NO_FILL", missing_price_layer="UNPRICED_WORST", filled=False, capture=1,
                win=False, pnl_bp=None, exit=worst_extreme(holding_bars, side), open_interpretation="PIN15_C_MISSING_ENTRY_BASIS")


def score_pin15(*, side, entry, entry_fresh, exit_fresh, exit_quote=None, exit_request_s,
                bar_1s=None, holding_bars=(), entry_status=None):
    """Score an explicitly conditioned fill; executable entry adapter is below.

    entry_fresh is a trusted fact supplied by the unchanged fill model adapter,
    not derived from candles. Candle fallbacks are missing-data marks only.
    """
    direction = sign(side)
    if entry is None or not isinstance(entry, (int, float)) or not math.isfinite(entry) or entry <= 0:
        return unpriced_without_entry_basis(holding_bars, side)
    if not entry_fresh or entry_status in ("NO_FRESH_BBO", "STALE_ENTRY", "NO_FILL"):
        return dict(category="NO_FILL", filled=False, capture=1, win=False, pnl_bp=None)
    if exit_fresh and exit_quote is not None:
        px, category = exit_quote, "PRICED"
    elif bar_1s is not None:
        if bar_1s["t"] != exit_request_s + 1:
            raise ValueError("EXIT_BAR_NOT_AT_1S")
        px, category = worst_extreme([bar_1s], side), "EXIT_WORST_1S"
    else:
        px, category = worst_extreme(holding_bars, side), "UNPRICED_WORST"
    if px is None:
        return dict(category="UNPRICED_WORST", filled=True, capture=1, win=False, pnl_bp=None, exit=None, status="HOLD_NO_EXTREME")
    if not math.isfinite(px) or px <= 0:
        raise ValueError("INVALID_EXIT")
    pnl = direction * (px / entry - 1) * 10000
    return dict(category=category, filled=True, capture=1, exit=px, pnl_bp=pnl, win=pnl > 0)


def taker_entry_and_score(rows_by_ts, *, side, qty, decision_ts, latency_sec, exit_request_s,
                          exit_fresh=False, exit_quote=None, bar_1s=None, holding_bars=()):
    """Invoke only shared fill_model.taker_fill_rows, read-only; no duplicate pricer."""
    if qty <= 0 or not math.isfinite(qty):
        raise ValueError("INVALID_QTY")
    fill = taker_fill_rows(rows_by_ts, side=side, qty=qty, decision_ts=decision_ts, latency_sec=latency_sec)
    if fill["status"] != "FILLED":
        result = dict(category="NO_FILL", filled=False, capture=1, win=False, pnl_bp=None)
    else:
        result = score_pin15(side=side, entry=fill["fill_price"], entry_fresh=True, exit_fresh=exit_fresh,
                             exit_quote=exit_quote, exit_request_s=exit_request_s, bar_1s=bar_1s, holding_bars=holding_bars)
    return dict(result, entry_fill=fill)


def pin15_counts(rows):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["strategy"], row["fold"])].append(row)
    result = {}
    for (strategy, fold), group in grouped.items():
        categories = Counter(r["category"] for r in group)
        if set(categories) - {"NO_FILL", "EXIT_WORST_1S", "UNPRICED_WORST", "PRICED"}:
            raise ValueError("UNKNOWN_PIN15_CATEGORY")
        capture, a, b, c = len(group), categories["NO_FILL"], categories["EXIT_WORST_1S"], categories["UNPRICED_WORST"]
        filled = capture - a
        result[f"{strategy}/{fold}"] = dict(capture=capture, filled=filled, a=a, b=b, c=c,
            a_share=a / capture if capture else None, b_share=b / filled if filled else None,
            c_share=c / filled if filled else None, bad_share=(b + c) / filled if filled else None,
            flag=(b + c) * 100 > 5 * filled if filled else False, zero_filled=filled == 0)
    return result


def dual_record(*, i, ii, backtest, N, confidence_ok, risk_ok, signal_id, entry_time, exit_time):
    """Defined arithmetic only; predicates must come from the caller's own method.

    Both marks share signal/time identity. A boolean predicate is not a computed
    confidence bound, bootstrap, drawdown model, sample-size or promotion gate.
    """
    if type(N) is not int or N <= 0 or any(not math.isfinite(v) for v in (i, ii, backtest)) or exit_time < entry_time:
        raise ValueError("INVALID_DUAL_INPUT")
    if type(confidence_ok) is not bool or type(risk_ok) is not bool:
        raise TypeError("PREDICATES_REQUIRED")
    ratio = ii / backtest if backtest > 0 else None
    decision = "HOLD" if ratio is None else ("ELIGIBLE_UNIT_ONLY" if i > 0 and confidence_ok and risk_ok and ratio >= .5 else "FAIL")
    return dict(signal_id=signal_id, entry_time=entry_time, exit_time=exit_time, i=i, ii=ii, alpha=.05 / N,
                slippage_bp=ii - i, ratio=ratio, paper_confident=confidence_ok, risk_record="i", decision=decision,
                validation="NOT_TESTED_CALLER_SUPPLIED_PREDICATES")


def pin8_coverage(entries, *, terminal_day, parent_days, A_independent_pass=False):
    """Terminal 28 UTC dates only, distinct completed intents; no moved clock."""
    complete = {}
    for row in entries:
        if not row.get("filled") or not row.get("completed", True) or row.get("partial", False) or row["utc_day"] > terminal_day:
            continue
        ident = row["intent"]
        previous = complete.get(ident)
        if previous is not None and previous != row:
            raise ValueError("CONFLICTING_COMPLETED_INTENT")
        complete[ident] = row
    branches = {}
    for branch in ("U", "D", "Q"):
        rows = [r for r in complete.values() if r["branch"] == branch and terminal_day - 27 <= r["utc_day"] <= terminal_day]
        branches[branch] = dict(trades=len(rows), days=len({r["utc_day"] for r in rows}))
    parent_bar = len(complete) >= 100 and parent_days >= 28
    coverage = parent_bar and all(v["trades"] >= 30 and v["days"] >= 14 for v in branches.values())
    action = "COVERAGE_ONLY_NOT_PASS" if coverage else ("A_ONLY_INDEPENDENT" if A_independent_pass else "FLAT")
    return dict(branches=branches, parent_trades=len(complete), parent_bar=parent_bar, coverage_only=coverage,
                action=action, pnl_clock_splice=False, live_authority=False)


def branch_ledger(closed):
    seen, total_i, total_ii = {}, 0, 0
    branches = {"U": 0, "D": 0, "Q": 0}
    for row in closed:
        key = row["close_key"]
        if key in seen:
            if row != seen[key]:
                raise ValueError("CONFLICTING_CLOSE")
            continue
        seen[key] = row
        branches[row["entry_branch"]] += row["i"]
        total_i += row["i"]
        total_ii += row["ii"]
    return dict(branch_i=branches, combined_i=total_i, combined_ii=total_ii, risk_record="i", duplicate_close=False)


def utc_days(seconds):
    days = sorted({math.floor(t / 86400) for t in seconds})
    return dict(days=days, count=len(days))


def dual_from_pvalue(data):
    """Use a supplied synthetic p-value; this is not a bootstrap estimator."""
    confidence = 0 <= data["p_value"] <= .05 / data["N"]
    return dual_record(i=data["i"], ii=data["ii"], backtest=data["backtest"], N=data["N"], confidence_ok=confidence,
                       risk_ok=bool(data["dd_ok"] and not data["k3_kill"]), signal_id=data["signal_id"], entry_time=data["entry_time"], exit_time=data["exit_time"])

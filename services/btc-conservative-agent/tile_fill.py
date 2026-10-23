"""Shared paper fill sites for tiles 1-5 (Danish 16 Oct 17:01 HARD RULE: one order-book pricer for every tile).

``price_entry`` / ``price_exit`` are the ONLY places lanes 1-5 price a paper fill. Each is a single
``return paper_fill.paper_fill(...)`` (CI: paper_fill.fill_site_violations); the book is passed
positionally as ``paper_fill.book10l_at``; only the leg / delay_source / cap_px keywords are used.
Queue, trade-through, maker-at-limit and BBO-state pricing are not kept for these lanes.
The helpers below never read bot state: callers hand in plain values.
"""
from __future__ import annotations

import paper_fill

# The five paper lanes migrated off the legacy touch model (Tiles 6/7 DISLOC / DISLOC-FOI removed 23 Oct 2026, R9).
MIGRATED_LANES = frozenset({
    "FAMILY_RANDOM_CONTROL_TAKER_90", "FAMILY_GS01_XV_PREMIUM_ATR_TP", "FAMILY_GSB1_CVD_DIV_REGIME",
    "FAMILY_GSB2_REGIME_SWITCHER", "FAMILY_GS06_COMMITTED_FADE_ATR_TP"})
IOC_CAP_BP = 5.0          # signal ask +5 bp (long) / signal bid -5 bp (short), same cap as paper_fill step B
EXIT_FORCE_AFTER_S = 10.0  # unpriced exit is force-closed (FORCED_NO_BOOK) after this
EXIT_FORCE_CAP_S = 30.0
TP1_MAX_RESTARTS = 3        # then the TP1 leg is priced with the no-wait forced pricer (livelock guard)
TP1_PENDING_MAX_S = 5.0     # a pending TP1 marker older than this is restarted (its book second is too old)
FORCED_LOOKBACK_S = paper_fill.MIN_DELAY_S  # forced closes: fill second = now, book = latest complete second <= now
FEE_MODEL = "NO_TRADING_FEE, funding only"  # Danish 16 Oct: Bitfinex has no trading fees, only funding
FUNDING_INTERVAL_S = 8 * 3600
FUNDING_SOURCE = "bot state funding rate (Bitfinex deriv status), 8h settlement grid 00/08/16 UTC"


def price_entry(o):
    return paper_fill.paper_fill(o["side"], o["qty"], o["signal_ts"], o["delay_s"], paper_fill.book10l_at,
                                 leg="entry", delay_source=o["delay_source"], cap_px=o["ioc_cap_px"])


def price_exit(p, ex):
    return paper_fill.paper_fill(p["side"], p["open_qty"], ex["trigger_ts"], ex["delay_s"], paper_fill.book10l_at,
                                 leg="exit", delay_source=ex["delay_source"])


def price_exit_resident(p, ex):
    """REALISM_V2: the exchange-resident stop fills as a full book walk at its trigger instant (delay 0)."""
    return paper_fill.paper_fill(p["side"], p["open_qty"], ex["trigger_ts"], ex["delay_s"], paper_fill.book10l_at,
                                 leg="exit_resident", delay_source=ex["delay_source"])


def price_exit_forced(p, ex):
    """RK 17:18 (4): a BOT_SENT exit with no fresh book row by EXIT_FORCE_AFTER_S is priced on the FIRST book row
    available at or after its model fill time (price age recorded), UNVERIFIED, kept; no synthetic penalty."""
    return paper_fill.paper_fill(p["side"], p["open_qty"], ex["trigger_ts"], ex["delay_s"], paper_fill.book10l_at,
                                 leg="exit_forced", delay_source=ex["delay_source"])


def signal_cap_px(direction: str, signal_ts: float):
    """IOC cap from the 10-level book at the signal second; None when there is no book."""
    book = paper_fill.book10l_at(float(signal_ts))
    levels = (book or {}).get("a" if str(direction).upper() == "LONG" else "b") or []
    try:
        px = float(levels[0][0])
    except (IndexError, TypeError, ValueError):
        return None
    k = IOC_CAP_BP / 1e4
    return px * (1 + k) if str(direction).upper() == "LONG" else px * (1 - k)


def entry_fill(direction: str, qty: float, signal_ts: float, delay_s, delay_source: str) -> dict:
    cap = signal_cap_px(direction, signal_ts)
    if cap is None:
        return {"fill_model": paper_fill.FILL_MODEL, "fill_model_version": paper_fill.FILL_MODEL_VERSION,
                "status": "REJECT_NO_BOOK", "price": None, "filled_qty": 0.0, "leg": "entry",
                "delay_s": delay_s, "delay_source": delay_source, "signal_ts": float(signal_ts)}
    res = price_entry({"side": str(direction).upper(), "qty": qty, "signal_ts": float(signal_ts),
                       "delay_s": delay_s, "delay_source": delay_source, "ioc_cap_px": cap})
    return {**res, "cap_px": cap, "ioc_cap_bp": IOC_CAP_BP}


def exit_fill(direction: str, qty: float, trigger_ts: float, delay_s, delay_source: str) -> dict:
    return price_exit({"side": str(direction).upper(), "open_qty": qty},
                      {"trigger_ts": float(trigger_ts), "delay_s": delay_s, "delay_source": delay_source})


def exit_resident_fill(direction: str, qty: float, trigger_ts: float) -> dict:
    return price_exit_resident({"side": str(direction).upper(), "open_qty": qty},
                               {"trigger_ts": float(trigger_ts), "delay_s": 0.0,
                                "delay_source": "EXCHANGE_RESIDENT_NO_DELAY"})


def exit_forced_fill(direction: str, qty: float, trigger_ts: float, delay_s, delay_source: str) -> dict:
    return price_exit_forced({"side": str(direction).upper(), "open_qty": qty},
                             {"trigger_ts": float(trigger_ts), "delay_s": delay_s, "delay_source": delay_source})


def provenance(res) -> dict:
    """Row fields proving a paper price came from paper_fill (and the explicit fee model)."""
    r = res if isinstance(res, dict) else {}
    return {"fill_model": r.get("fill_model"), "fill_model_version": r.get("fill_model_version"),
            "delay_s": r.get("delay_s"), "delay_source": r.get("delay_source"), "ask_age_ms": r.get("ask_age_ms"),
            "depth_snapshot_signal": r.get("depth_snapshot_signal"),
            "depth_snapshot_fill": r.get("depth_snapshot_fill"), "fee_model": FEE_MODEL}


def row_provenance_complete(row) -> bool:
    """Closed-row guard: model, version, delay source, and entry+exit depth snapshots all present."""
    if not isinstance(row, dict):
        return False
    e, x = row.get("entry_provenance") or {}, row.get("exit_provenance") or {}
    return all(p.get("fill_model") == paper_fill.FILL_MODEL and p.get("fill_model_version")
               and p.get("delay_source") and p.get("depth_snapshot_fill") for p in (e, x))


def settlements_crossed(entry_ts, exit_ts) -> list:
    """The 00/08/16 UTC settlement instants t with entry_ts < t <= exit_ts (strict / inclusive, as Bitfinex)."""
    try:
        a, b = float(entry_ts), float(exit_ts)
    except (TypeError, ValueError):
        return []
    t = (int(a // FUNDING_INTERVAL_S) + 1) * FUNDING_INTERVAL_S
    crossed = []
    while t <= b and len(crossed) < 100:
        crossed.append(t)
        t += FUNDING_INTERVAL_S
    return crossed


def parse_funding_ts_crossed(value) -> list:
    """Reader for funding_ts_crossed in every stored form: a real list, or the pre-19-Oct string form
    ('[]' / '[1792310400]', HM: 277 rows, 13 of them real crossings that must never read as empty)."""
    import json
    if isinstance(value, (list, tuple)):
        return [int(float(x)) for x in value]
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            out = json.loads(text)
        except ValueError:
            return []
        return [int(float(x)) for x in out] if isinstance(out, list) else []
    return []


def qty_schedule(original_qty, receipts=None):
    """qty_at(t): the open quantity at instant t = original qty minus every partial reduction filled before t."""
    import datetime as _dt
    try:
        q0 = float(original_qty)
    except (TypeError, ValueError):
        q0 = 0.0
    cuts, unresolved = [], []
    for r in receipts or []:
        if not isinstance(r, dict) or not (float(r.get("remaining_fraction") or 0) > 0):
            continue  # only genuine partial reductions (a terminal receipt is the close itself)
        ts = r.get("leg_fill_ts")
        if ts is None and isinstance(r.get("ts"), str):
            try:
                ts = _dt.datetime.fromisoformat(r["ts"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                ts = None
        try:
            cuts.append((float(ts), float(r.get("closed_qty") or 0)))
        except (TypeError, ValueError):
            unresolved.append(r)  # HM #849 item 4: a partial without a fill ts cannot be placed -> fail closed

    def qty_at(t):
        return max(0.0, q0 - sum(q for ts, q in cuts if ts < float(t)))
    qty_at.unresolved_partials = len(unresolved)  # funding_fields marks the row UNVERIFIED_PARTIAL_TS_MISSING
    return qty_at


FUNDING_MODEL_V2 = "SETTLEMENT_EXACT_V2"
FUNDING_SOURCE_V2 = ("bfx_deriv_status_1s: rate = CURRENT_FUNDING of the last row before the settlement "
                     "(confirmed by the next_fund_ts flip), notional = qty x MARK_PRICE at the settlement")


def funding_fields(entry_ts, exit_ts, rate=None, settlements=None, *, direction=None, qty=None, qty_at=None,
                   deriv=None) -> dict:
    """Funding kept separate from the fill price.

    REALISM_V2 F5 (direction + qty/qty_at given): paid only at each settlement t with entry_fill_ts < t <=
    exit_fill_ts; amount = sign x qty_at(t) x MARK(t) x rate_applied(t) (positive rate: longs pay, shorts
    receive; positive USD = cost). Raw, unrounded per-settlement fields (Rules Keeper); no fee term; no
    pro-rating; never the predicted state rate. A settlement with no deriv-status row is unpriced: its USD
    is None and the row is UNVERIFIED for funding. Non-crossers book exactly 0.0.

    Without direction/qty: the legacy evidence shape (crossings + the accrual's recorded rates), kept only for
    non-paper lanes.
    """
    crossed = settlements_crossed(entry_ts, exit_ts)
    try:
        hold = round(max(0.0, float(exit_ts) - float(entry_ts)), 3)
    except (TypeError, ValueError):
        hold = None
    passthrough = [dict(x) for x in (settlements or []) if isinstance(x, dict)]
    if direction is None or (qty is None and qty_at is None):
        return {"funding_rate": rate, "funding_source": FUNDING_SOURCE, "funding_interval_s": FUNDING_INTERVAL_S,
                "hold_seconds": hold, "funding_ts_crossed": crossed, "funding_settlement_rates": passthrough}
    deriv = deriv or paper_fill.deriv_settlement
    if qty_at is None:
        qty_at = (lambda _t, _q=float(qty): _q)
    sign = 1.0 if str(direction).upper() == "LONG" else -1.0
    rates, rts, accr, marks, mts, qtys, usds, stat = [], [], [], [], [], [], [], []
    max_age = float(getattr(paper_fill, "DERIV_MAX_AGE_S", 2.0))
    ages = []
    for t in crossed:
        d = deriv(t)
        q = float(qty_at(t))
        qtys.append(q)
        if not d or d.get("rate_applied") is None or d.get("mark_price") is None:
            rates.append(None); rts.append(None); accr.append(None); marks.append(None); mts.append(None)
            usds.append(None); stat.append("NO_DERIV_STATUS")
            continue
        r, m = float(d["rate_applied"]), float(d["mark_price"])
        rates.append(r); rts.append(d.get("rate_ts")); accr.append(d.get("rate_accrued"))
        marks.append(m); mts.append(d.get("mark_ts"))
        usds.append(sign * q * m * r)
        # HM #849 item 5: the rate/mark row must be at most DERIV_MAX_AGE_S before the settlement instant
        age = max((float(t) - float(x)) for x in (d.get("rate_ts"), d.get("mark_ts")) if x is not None) \
            if (d.get("rate_ts") is not None or d.get("mark_ts") is not None) else None
        ages.append(age)
        if age is None or age > max_age:
            stat.append("STALE_DERIV_ROW")
        else:
            stat.append("CONFIRMED" if d.get("confirmed") else "UNCONFIRMED_NO_FLIP")
    unresolved = int(getattr(qty_at, "unresolved_partials", 0) or 0)
    if unresolved:
        status, total = "UNVERIFIED_PARTIAL_TS_MISSING", None  # HM #849 item 4: open qty unknown -> fail closed
    elif not crossed:
        status, total = "NO_CROSSING", 0.0
    elif "NO_DERIV_STATUS" in stat:
        status, total = "UNVERIFIED_NO_DERIV_STATUS", None
    elif "STALE_DERIV_ROW" in stat:
        status, total = "UNVERIFIED_STALE_DERIV", float(sum(usds))
    elif "UNCONFIRMED_NO_FLIP" in stat:
        status, total = "UNVERIFIED_UNCONFIRMED", float(sum(usds))
    else:
        status, total = "PRICED", float(sum(usds))
    return {"funding_model": FUNDING_MODEL_V2, "funding_source": FUNDING_SOURCE_V2,
            "funding_interval_s": FUNDING_INTERVAL_S, "hold_seconds": hold,
            "funding_entry_fill_ts": entry_ts, "funding_exit_fill_ts": exit_ts,
            "funding_ts_crossed": crossed,
            # per settlement, aligned with funding_ts_crossed; raw and unrounded
            "funding_rate_applied": rates, "funding_rate_ts": rts, "funding_rate_accrued": accr,
            "funding_mark_price": marks, "funding_mark_ts": mts, "funding_qty": qtys,
            "funding_usd_by_settlement": usds, "funding_settlement_status": stat,
            "funding_deriv_age_s": ages, "funding_deriv_max_age_s": max_age, "funding_unresolved_partials": unresolved,
            "funding_rate": rates[-1] if rates else None,
            "funding_usd_raw": total, "funding_status": status, "funding_verified": status in ("PRICED", "NO_CROSSING"),
            "funding_settlement_rates": passthrough}

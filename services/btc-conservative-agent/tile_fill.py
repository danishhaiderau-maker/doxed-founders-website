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


def funding_fields(entry_ts, exit_ts, rate, settlements=None) -> dict:
    """Funding kept separate from the fill price: rate, hold time, the 8h settlement times crossed, the
    rate recorded at each settlement (when the position's accrual ticks saw it) and where the rate came from."""
    base = {"funding_rate": rate, "funding_source": FUNDING_SOURCE, "funding_interval_s": FUNDING_INTERVAL_S}
    try:
        a, b = float(entry_ts), float(exit_ts)
    except (TypeError, ValueError):
        return {**base, "hold_seconds": None, "funding_ts_crossed": [], "funding_settlement_rates": []}
    t = (int(a // FUNDING_INTERVAL_S) + 1) * FUNDING_INTERVAL_S
    crossed = []
    while t <= b and len(crossed) < 100:
        crossed.append(t)
        t += FUNDING_INTERVAL_S
    return {**base, "hold_seconds": round(max(0.0, b - a), 3), "funding_ts_crossed": crossed,
            "funding_settlement_rates": [dict(x) for x in (settlements or []) if isinstance(x, dict)]}

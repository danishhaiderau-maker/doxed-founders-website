"""REALISM_V2 order-type model (pure functions, no per-tile code, never prices a fill).

Binding model (tracker "REALISM FIXES F1-F7", HM #807 a-h, Rules Keeper, steering 23 Oct):
- EXCHANGE_RESIDENT: every tile carries an exchange-resident stop at
  min(hard + 25 bp, liq - 15 bp) = live_copy_control.exchange_stop_plan(spec) (LIVE_CODE, relay master 51d926f93),
  anchored at the paper ENTRY FILL and never moved (APPROVED live copies never move it whatever
  EXCHANGE_DYNAMIC_STOPS_ENABLED says). It is sized to the open qty (resized on partials), races the paper hard stop
  and every in-flight bot exit, and fills as a delay-0 full book walk at its trigger instant.
- BOT_SENT: every other exit (TP, TP1, thesis, time, trail/lock, BE, paper hard stop, forced/admin) is a reduce-only
  MARKET book walk at trigger + delay. No maker exits, no booking at the target.
- Entries: WALK_THEN_REST (GS-01 / GS-06 / CTRL: walk to a 5 bp protection limit, remainder rests under maker rules
  until the 3 s TTL then cancels), MAKER_REST (B1 / B2 regime cells: rest 600-1200 s, repriced at the
  LIMIT_UPDATED cadence, queue reset on every reprice), IOC_MARKET (entry_policy.mode SELF_SIMULATED /
  MARKET_AT_REAL_TOUCH only; DISLOC is retired and removed from TILE_FILL_SITES / COMBO_EXECUTION_LANES).
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Mapping, Optional

EXCHANGE_RESIDENT = "EXCHANGE_RESIDENT"
BOT_SENT = "BOT_SENT"
PARAM_SOURCE_LIVE_CODE = "LIVE_CODE"
LIVE_CODE_REF = "relay master 51d926f93; live_copy_control.exchange_stop_plan"
EXCHANGE_STOP_REASON_PREFIX = "EXCHANGE_STOP_"
_EXCHANGE_REASON = re.compile(r"EXCHANGE_STOP_\d+(\.\d+)?BP")

ENTRY_WALK_THEN_REST = "WALK_THEN_REST"
ENTRY_MAKER_REST = "MAKER_REST"
ENTRY_IOC_MARKET = "IOC_MARKET"
NO_FILL = "NO_FILL"
DEFAULT_PROTECTION_BP = 5.0
DEFAULT_TAKER_TTL_S = 3.0
# HM 17:26 R6: PER-TILE registry options on the shared helpers (GS-FADE3-TP30 AMEND1 model). Defaults keep every
# existing tile's behaviour; a tile opts in through its registry spec "realism_options" and every row stamps them.
REST_FILL_QUEUE_CLEAR = "queue_clear"       # opt-in: at-limit prints deplete the queue, then fill us
REST_FILL_TRADE_THROUGH = "trade_through"   # DEFAULT (HM C1): only prints strictly through the limit fill us
REST_FILL_BASES = (REST_FILL_QUEUE_CLEAR, REST_FILL_TRADE_THROUGH)
REST_FILL_DEFAULT = REST_FILL_TRADE_THROUGH  # HM C1 23 Oct: the conservative basis is the default for every tile
UNSIZED_PRINT = "UNSIZED_PRINT_UNVERIFIED"  # a print with no |amt| fills nothing and flags the rest UNVERIFIED
STOP_TRIGGER_TRADES = "trades"  # RK 17:24 BINDING: the only resident-stop trigger basis (first trade at/through)
STOP_TRIGGER_BASES = (STOP_TRIGGER_TRADES,)
LIQ_MODEL = "MARK_BFXCI_ISOLATED_V1"  # mark-price liquidation check on bfx_deriv_status_1s (UNVALIDATED)
# The live exchange stop exactly as the relay executor submits it (apps/api/src/exchanges/bitfinex-api.client.ts
# submitStopOrder -> POST v2/auth/w/order/submit). Bitfinex does not document the plain STOP trigger basis
# (support 115003506125 / 115003291045): STOP_BASIS_UNVERIFIED on every resident-stop row.
LIVE_EXCHANGE_STOP_PARAMS = {"type": "STOP", "symbol": "tBTCF0:USTF0", "flags": 1024, "flags_named": ["REDUCE_ONLY"],
                             "price": "stop level (min(hard + 25 bp, liq - 15 bp) from the entry fill)",
                             "amount": "-qty (LONG) / +qty (SHORT)", "lev": "bitfinexOrderLev(leverage)",
                             "price_trigger": None, "price_aux_limit": None, "placed": "once at entry, never moved",
                             "price_rounding": "normalizeBitfinexOrderPrice: truncate to 5 significant figures "
                                               "(bitfinex-api.client.ts:526-532)",
                             "anchor": "live exchange fill (subscriber-exit.ts:175-184)"}
PARAM_SOURCE_CONFIG = "CONFIG"  # combo_pathway_config / regime_exec cell (same values the live executor reads)


def _f(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _dir(d: Any) -> Optional[str]:
    s = str(d or "").upper()
    return "LONG" if s in ("LONG", "BUY") else ("SHORT" if s in ("SHORT", "SELL") else None)


# ------------------------------------------------------------------ exits
def exchange_stop_reason(bp: float) -> str:
    return f"{EXCHANGE_STOP_REASON_PREFIX}{float(bp):g}BP"


def is_exchange_stop_reason(reason: Any) -> bool:
    return bool(_EXCHANGE_REASON.fullmatch(str(reason or "").upper()))


def exit_order_class(reason: Any) -> str:
    """EXCHANGE_RESIDENT only for the exchange stop; every other exit (incl. paper hard stop, forced, admin) is BOT_SENT."""
    return EXCHANGE_RESIDENT if is_exchange_stop_reason(reason) else BOT_SENT


def exchange_stop_level(direction: Any, entry_fill_px: Any, spec: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """The exchange-resident stop of one position: anchored at the paper entry fill, never moved."""
    import live_copy_control as _lcc
    d, px = _dir(direction), _f(entry_fill_px)
    if d is None or px is None or px <= 0:
        return None
    plan = _lcc.exchange_stop_plan(spec)
    if not plan:
        return None
    bp = float(plan["exchange_stop_bp"])
    level = px * (1 - bp / 1e4) if d == "LONG" else px * (1 + bp / 1e4)
    return {"exchange_stop_px": bfx_truncate_price(level), "exchange_stop_px_untruncated": round(level, 8),
            "exchange_stop_bp": bp, "exchange_stop_anchor_px": px, "exchange_stop_anchor": "PAPER_ENTRY_FILL",
            "exchange_stop_rounding": "TRUNC_5SF", "exchange_stop_liq_bp": plan.get("liquidation_bp"),
            "exchange_stop_hard_bp": plan.get("hard_stop_bp"), "exchange_stop_cap_applied": plan.get("cap_applied"),
            "exchange_stop_param_source": PARAM_SOURCE_LIVE_CODE, "exchange_stop_param_ref": LIVE_CODE_REF,
            "exchange_stop_never_moved": True}


def bfx_truncate_price(price: Any) -> Optional[float]:
    """Exactly the live client's normalizeBitfinexOrderPrice (bitfinex-api.client.ts:526-532): truncate toward zero
    to 5 significant figures (quantum 10^(floor(log10 p) - 4)); at ~$100k the quantum is $10 (~1 bp)."""
    import math
    p = _f(price)
    if p is None or p <= 0:
        return None
    q = 10.0 ** (math.floor(math.log10(p)) - 4)
    n = math.trunc(p / q + 2.220446049250313e-16)  # Number.EPSILON, as in the TS
    return n * q


def anchor_drift(direction: Any, paper_anchor_px: Any, live_fill_px: Any) -> dict:
    """RK 17:24: paper-vs-live stop-anchor drift per trade (paper anchors on the paper fill, live on the live fill).
    bp is signed adverse-positive for the position: live filled worse than paper -> positive."""
    pa, lv = _f(paper_anchor_px), _f(live_fill_px)
    d = str(direction or "").upper()
    if pa is None or lv is None or pa <= 0:
        return {"stop_anchor_paper_px": pa, "stop_anchor_live_px": lv, "stop_anchor_drift_bp": None,
                "stop_anchor_drift_status": "NO_LIVE_TWIN" if lv is None else "UNVERIFIED"}
    bp = (lv - pa) / pa * 1e4 * (1 if d == "LONG" else -1)
    return {"stop_anchor_paper_px": pa, "stop_anchor_live_px": lv, "stop_anchor_drift_bp": round(bp, 3),
            "stop_anchor_drift_status": "MEASURED"}


MAINTENANCE_MARGIN = 0.005  # Bitfinex derivatives maintenance margin 0.5% (support 360035475374)


def liquidation_check(direction: Any, mark_at_open: Any, leverage: Any, *, marks: Optional[list],
                      trades: Optional[list], stop_trigger_ts: Optional[float], stop_fill_px: Any) -> dict:
    """HM M10 / tracker 17:30 mark-price liquidation model (isolated, UNVALIDATED).

    Liquidation price = the REAL BFXCI mark at open (bfx_deriv_status_1s) moved adverse by (1/leverage - 0.5%
    maintenance margin). Liquidation fires on the FIRST of the BFXCI mark OR a tBTCF0:USTF0 last trade reaching that
    level (liq_trigger_source mark | trade). liq_unverified = True when liquidation fired before the exchange stop,
    OR the stop fill slipped past the liquidation price, OR the mark data is missing."""
    m0, lev = _f(mark_at_open), _f(leverage)
    d = str(direction or "").upper()
    out = {"liq_model": LIQ_MODEL, "liq_model_validated": False, "liq_mark_at_open": m0, "liq_bp": None,
           "liq_px": None, "liq_trigger_ts": None, "liq_trigger_source": None, "liq_before_stop": None,
           "liq_stop_slipped_past": None, "liq_mark_rows_n": None if marks is None else len(marks),
           "liq_unverified": False, "liq_reason": None}
    if m0 is None or m0 <= 0 or lev is None or lev <= 0 or d not in ("LONG", "SHORT"):
        out.update(liq_unverified=True, liq_reason="NO_MARK_AT_OPEN")
        return out
    bp = (1.0 / lev - MAINTENANCE_MARGIN) * 1e4
    lpx = m0 * (1 - bp / 1e4) if d == "LONG" else m0 * (1 + bp / 1e4)
    out.update(liq_bp=round(bp, 4), liq_px=round(lpx, 8))

    def reached(px):
        return px is not None and (px <= lpx if d == "LONG" else px >= lpx)
    ev = []
    for m in marks or ():
        if reached(_f(m.get("mark"))):
            ev.append((float(m["s"]) + 1.0, "mark"))  # a 1 s mark row is known at the end of its second
            break
    for tr in trades or ():
        if reached(_f(tr.get("px"))) and _f(tr.get("ts")) is not None:
            ev.append((float(tr["ts"]), "trade"))
            break
    if ev:
        ts, src = min(ev)
        out.update(liq_trigger_ts=ts, liq_trigger_source=src)
        st = _f(stop_trigger_ts)
        out["liq_before_stop"] = bool(st is None or ts < st)
    fp = _f(stop_fill_px)
    if fp is not None:
        out["liq_stop_slipped_past"] = bool(reached(fp))
    if marks is None:
        out.update(liq_unverified=True, liq_reason="NO_MARK_DATA")
    if out["liq_before_stop"]:
        out.update(liq_unverified=True, liq_reason="LIQ_BEFORE_STOP")
    if out["liq_stop_slipped_past"]:
        out.update(liq_unverified=True, liq_reason=out["liq_reason"] or "STOP_SLIPPED_PAST_LIQ")
    return out


def race(exchange_trigger_ts: Optional[float], bot_trigger_ts: Optional[float], bot_delay_s: float) -> Optional[str]:
    """Which order closes the position: the resident stop wins if it triggers at or before the bot order's fill
    time (bot trigger + delay); exact ties go to the resident stop (it is already on the exchange)."""
    e, b = _f(exchange_trigger_ts), _f(bot_trigger_ts)
    if e is None and b is None:
        return None
    if e is None:
        return BOT_SENT
    if b is None:
        return EXCHANGE_RESIDENT
    return EXCHANGE_RESIDENT if e <= b + max(0.0, float(bot_delay_s or 0)) else BOT_SENT


def tile_options(spec: Optional[Mapping[str, Any]]) -> dict:
    """The tile's realism options (registry spec ``realism_options``), validated, with defaults + per-row flags.

    resident_stop_trigger_basis: trades (the only basis, RK 17:24 BINDING; anything else is ignored).
    entry_rest_fill_basis: trade_through (default, HM C1) | queue_clear (opt-in). stop_basis_unverified is True and
    stop_basis_verified False on every row (plain STOP basis undocumented for derivatives) unless the spec declares
    it verified; funding_unverified is True unless the spec declares a verified funding source."""
    o = (spec or {}).get("realism_options") or {}
    rb = str(o.get("entry_rest_fill_basis") or REST_FILL_DEFAULT).lower()
    verified = bool(o.get("stop_basis_verified"))
    return {"resident_stop_trigger_basis": STOP_TRIGGER_TRADES,
            "entry_rest_fill_basis": rb if rb in REST_FILL_BASES else REST_FILL_DEFAULT,
            "time_exit_basis": "CLOCK", "stop_basis_unverified": not verified, "stop_basis_verified": verified,
            "funding_unverified": not bool(o.get("funding_verified"))}


# ------------------------------------------------------------------ entries
def entry_model(spec: Optional[Mapping[str, Any]], decision: Optional[Mapping[str, Any]] = None,
                exec_cell: Optional[Mapping[str, Any]] = None) -> dict:
    """Data-driven entry order type from the tile spec + the regime exec cell (no per-tile code)."""
    e = (spec or {}).get("entry_policy") or (spec or {}).get("entry") or {}
    mode = str(e.get("mode") or "").upper()
    prot = _f(e.get("taker_protection_bps"))
    ttl = _f(e.get("taker_ttl_sec"))
    if "SELF_SIMULATED" in mode or "MARKET_AT_REAL_TOUCH" in mode:
        return {"entry_order_model": ENTRY_IOC_MARKET, "entry_param_source": PARAM_SOURCE_CONFIG}
    kind = str((exec_cell or {}).get("kind") or (decision or {}).get("exec_kind") or "").upper()
    rest_basis = tile_options(spec)["entry_rest_fill_basis"]
    if exec_cell and kind and kind != "TAKER":
        return {"entry_order_model": ENTRY_MAKER_REST, "entry_rest_fill_basis": rest_basis, "entry_rest_ttl_s": _f(exec_cell.get("ttl_sec")),
                "entry_reprice_s": _f(exec_cell.get("reprice_sec")), "entry_queue_reset_on_reprice": True,
                "entry_exec_kind": kind, "entry_param_source": PARAM_SOURCE_CONFIG}
    return {"entry_order_model": ENTRY_WALK_THEN_REST, "entry_rest_fill_basis": rest_basis,
            "entry_protection_bp": prot if prot is not None else DEFAULT_PROTECTION_BP,
            "entry_rest_ttl_s": ttl if ttl is not None else DEFAULT_TAKER_TTL_S,
            "entry_param_source": PARAM_SOURCE_CONFIG if (prot is not None and ttl is not None) else "DEFAULT"}


def queue_ahead_at(direction: Any, limit_px: Any, book: Optional[Mapping[str, Any]]) -> float:
    """Resting size already at our limit price on our (passive) side when we join / reprice."""
    d, lp = _dir(direction), _f(limit_px)
    if d is None or lp is None or not isinstance(book, Mapping):
        return 0.0
    side = book.get("b" if d == "LONG" else "a") or []
    tot = 0.0
    for lv in side:
        try:
            p, s = float(lv[0]), float(lv[1])
        except (TypeError, ValueError, IndexError):
            continue
        if abs(p - lp) <= 1e-9:
            tot += s
    return tot


def maker_rest_step(direction: Any, limit_px: Any, remaining: float, queue_ahead: float,
                    trades: Iterable[Mapping[str, Any]], fill_basis: str = REST_FILL_DEFAULT) -> dict:
    """Advance a resting entry limit over trade prints (oldest first). A print THROUGH the limit (sell below a buy
    limit / buy above a sell limit) fills us (trade-through), capped at that print's printed |amt|; with
    ``fill_basis`` trade_through (default, HM C1) at-limit prints never fill us; with queue_clear (opt-in) a print AT
    the limit on the aggressor side first depletes the queue ahead, then fills us. HM C1: a print whose amt is None
    or 0 has no known size or aggressor: it fills NOTHING and is counted in ``unsized_prints`` (the caller stamps
    UNSIZED_PRINT_UNVERIFIED). Partials allowed. Returns fills [(ts, px, qty)], remaining, queue_ahead,
    unsized_prints."""
    d, lp = _dir(direction), _f(limit_px)
    rem = max(0.0, float(remaining or 0))
    q = max(0.0, float(queue_ahead or 0))
    fills = []
    unsized = 0
    if d is None or lp is None or rem <= 0:
        return {"fills": fills, "remaining": rem, "queue_ahead": q, "unsized_prints": 0}
    for tr in trades or ():
        px, ts, amt = _f(tr.get("px")), _f(tr.get("ts")), _f(tr.get("amt"))
        if px is None or ts is None or rem <= 0:
            continue
        size = abs(amt) if amt else 0.0
        through = px < lp - 1e-9 if d == "LONG" else px > lp + 1e-9
        at = abs(px - lp) <= 1e-9
        if size <= 0:
            # HM C1: no printed size -> size 0 (never the whole remainder) and no known aggressor (never "hits us").
            if through or at:
                unsized += 1
            continue
        # aggressor: Bitfinex signed amount, negative = sell aggressor (hits bids), positive = buy aggressor
        hits_us = amt < 0 if d == "LONG" else amt > 0
        if through:
            take = min(rem, size)  # capped at the print's printed |amt|
            fills.append((ts, lp, take))
            rem -= take
        elif at and hits_us and size > 0 and fill_basis != REST_FILL_TRADE_THROUGH:
            if q >= size:
                q -= size
                continue
            avail = size - q
            q = 0.0
            take = min(rem, avail)
            fills.append((ts, lp, take))
            rem -= take
    return {"fills": fills, "remaining": round(rem, 12), "queue_ahead": q, "unsized_prints": unsized}


MAKER_REST_FULL_SIZE_RULE = "TRADE_THROUGH_FULL_SIZE_ONLY"  # RK R1 item 5: B1/B2 maker entry rest


def maker_rest_full_size(direction: Any, limit_px: Any, requested_qty: float, through_seen: float,
                         trades: Iterable[Mapping[str, Any]]) -> dict:
    """RK R1 item 5: a resting MAKER entry fills ONLY on trade-through prints, and ONLY for the FULL size.

    Prints strictly through the limit (sell below a buy limit / buy above a sell limit) with a printed |amt| add to
    ``through_seen``; at-limit prints, touches and book crosses never count (no queue_clear credit). When the
    cumulative through volume covers ``requested_qty`` the whole order fills at the limit, stamped at the print
    that completed it; until then nothing is booked (no partials). Unsized prints fill nothing and are counted.
    Returns {"filled": bool, "fill_ts", "through_seen", "unsized_prints"}."""
    d, lp = _dir(direction), _f(limit_px)
    need = max(0.0, float(requested_qty or 0))
    seen = max(0.0, float(through_seen or 0))
    unsized = 0
    if d is None or lp is None or need <= 0:
        return {"filled": False, "fill_ts": None, "through_seen": seen, "unsized_prints": 0}
    for tr in sorted((t for t in trades or () if _f(t.get("ts")) is not None), key=lambda t: float(t["ts"])):
        px, ts, amt = _f(tr.get("px")), _f(tr.get("ts")), _f(tr.get("amt"))
        if px is None:
            continue
        through = px < lp - 1e-9 if d == "LONG" else px > lp + 1e-9
        if not through:
            continue
        size = abs(amt) if amt else 0.0
        if size <= 0:
            unsized += 1
            continue
        seen += size
        if seen + 1e-12 >= need:
            return {"filled": True, "fill_ts": ts, "through_seen": round(seen, 12), "unsized_prints": unsized}
    return {"filled": False, "fill_ts": None, "through_seen": round(seen, 12), "unsized_prints": unsized}


def vwap(fills: Iterable) -> tuple:
    q = sum(f[2] for f in fills)
    return (sum(f[1] * f[2] for f in fills) / q if q > 0 else None), q


def combine_entry(walk: Optional[Mapping[str, Any]], rest_fills: Iterable, requested_qty: float, *,
                  model: Mapping[str, Any], rest_end_ts: Optional[float], rest_end_reason: str) -> dict:
    """Final entry result = the book walk (paper_fill, may be empty) + maker rest fills at the limit (may be empty).
    FILLED / PARTIAL (unfilled qty recorded, the rest is cancelled) / NO_FILL. Walk metadata is kept."""
    walk = dict(walk or {})
    wq = _f(walk.get("filled_qty")) or 0.0
    wpx = _f(walk.get("price"))
    fills = [(float(t), float(p), float(q)) for t, p, q in (rest_fills or ()) if q and q > 0]
    if wq > 0 and wpx:
        fills_all = [(walk.get("fill_ts"), wpx, wq)] + fills
    else:
        wq, fills_all = 0.0, list(fills)
    px, q = vwap(fills_all)
    req = max(0.0, float(requested_qty or 0))
    unfilled = max(0.0, req - q)
    status = NO_FILL if q <= 0 else ("FILLED" if unfilled <= 1e-12 else "PARTIAL")
    out = {k: v for k, v in walk.items() if k not in ("price", "filled_qty", "status")}
    rest_q = sum(f[2] for f in fills)
    last_ts = max(f[0] for f in fills) if fills else None
    out.update({
        "status": status, "price": px, "filled_qty": q, "requested_qty": req,
        "entry_unfilled_qty": round(unfilled, 12), "entry_walk_status": walk.get("status"),
        "entry_walk_filled_qty": wq, "entry_walk_px": wpx if wq > 0 else None,
        "entry_rest_filled_qty": rest_q, "entry_rest_fills": [[t, p, qq] for t, p, qq in fills],
        "entry_rest_end_ts": rest_end_ts, "entry_rest_end_reason": rest_end_reason,
        **{k: v for k, v in model.items()},
    })
    if fills:
        out["walk_fill_ts"] = walk.get("fill_ts")
        out["fill_ts"] = last_ts
        out["entry_model_fill_ts"] = last_ts
        out["entry_rest_fill_px"] = vwap(fills)[0]
    else:
        out["entry_model_fill_ts"] = walk.get("fill_ts") if wq > 0 else None
    if status == NO_FILL:
        out["no_fill_reason"] = rest_end_reason
    return out

"""Read-only live-vs-paper pairs per signal (HM 19 Oct, GS-01 live test).

One row per Fly signal/trade id. Pure functions over the live-copy chains (executor reports) and the paper twins; no I/O,
nothing here touches trading state. Fields the executor or the paper ledger does not report stay null and are listed in
``missing`` so a gap is visible instead of silently averaged away. No trading fees: only funding is modelled.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

import live_ack_pool as _lap
import trigger_measure as _tm

SCHEMA = "live_vs_paper_v1"
_NOT_TRADED_REASONS = ("LIVE_BUSY_SKIPPED",)


# HM R2 K3 / RK C (R8-live, both legs): mirrors apps/api/src/trading-agents/live-row-countability.ts
# (LIVE_FORCED_EXIT_REASONS = paper_pnl_canon.FORCED_EXIT_REASONS + relay-only USER_RELAY_STOP, and /FORCE_FLAT|MAINTENANCE/).
LIVE_FORCED_EXIT_REASONS = frozenset({"ADMIN_MANUAL_CLOSE", "ADMIN_FORCE_FLAT", "CIRCUIT_BREAKER_ADMIN_MANUAL",
                                      "DEPLOY_FORCE_FLAT", "USER_RELAY_STOP"})
_FORCED_PATTERN = ("FORCE_FLAT", "MAINTENANCE")
_DISPLAY_PREFIXES = ("SOURCE_CONFIRMED_", "LATE_ENTRY_BETTER_ONLY_CONTINUATION: ", "LATE_ENTRY_BETTER_ONLY_CONTINUATION")


def forced_reason(*reasons):
    """The first forced close reason (canonical, upper case) among the candidates, else None."""
    for raw in reasons:
        s = str(raw or "").strip().upper()
        for p in _DISPLAY_PREFIXES:
            if s.startswith(p):
                s = s[len(p):].lstrip(":").strip()
        if s and (s in LIVE_FORCED_EXIT_REASONS or any(x in s for x in _FORCED_PATTERN)):
            return s
    return None


def _truthy(v) -> bool:
    return v is True or str(v).strip().lower() in ("1", "true", "yes")


def pair_countability(chain: Mapping[str, Any] | None, twin: Mapping[str, Any] | None,
                      fly_exit_send: Mapping[str, Any] | None = None) -> dict:
    """R8-live both legs: a pair is non-countable when EITHER leg was force-closed; a live-only forced close
    (USER_RELAY_STOP, ADMIN_FORCE_FLAT, deploy/maintenance flatten ...) marks the paper twin non-countable too,
    and a paper twin that is itself non-countable (forced, R17, default delay ...) makes the pair non-countable."""
    chain, twin = chain or {}, twin or {}
    live = forced_reason(chain.get("exit_reason"), chain.get("close_reason"), chain.get("terminal_reason"))
    paper_forced = forced_reason(twin.get("exit_reason"))
    reasons = []
    if live:
        reasons.append("LIVE_FORCED_CLOSE:" + live)
    if paper_forced:
        reasons.append("PAPER_FORCED_CLOSE:" + paper_forced)
    elif twin and _truthy(twin.get("non_countable")):
        reasons.append("PAPER_NON_COUNTABLE:" + str(twin.get("non_countable_reason") or twin.get("non_countable_reasons")
                                                    or "STAMPED"))
    # HM 01:43 item 3: every live leg (signal, Fly send, relay receive, ack/fill) must be measured and ordered
    legs = _lap.live_legs(chain, fly_exit_send, closed=bool(chain.get("closed") or chain.get("exit_ack_at_ts")),
                          exit_resident=str(chain.get("exit_order_class") or "") == "EXCHANGE_RESIDENT")
    legs_nc = bool(chain) and legs["non_countable"]
    if legs_nc:
        reasons.extend(legs["non_countable_reasons"])
    twin_nc = bool(twin) and bool(live or paper_forced or _truthy(twin.get("non_countable")))
    live_reason = ("FORCED_CLOSE:" + live) if live else (legs["non_countable_reasons"][0] if legs_nc else None)
    return {"live_non_countable": bool(live) or legs_nc, "live_non_countable_reason": live_reason,
            "live_legs": legs["legs"] if chain else None,
            "paper_twin_non_countable": twin_nc,
            "paper_twin_non_countable_reason": (("LIVE_TWIN_FORCED_CLOSE:" + live) if live else
                                                (reasons[0] if twin_nc and reasons else None)),
            "non_countable": bool(reasons), "non_countable_reasons": reasons or None}


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _pos(v):
    x = _f(v)
    return x if x and x > 0 else None


def _ms(a, b):
    return round((b - a) * 1000.0, 1) if a is not None and b is not None else None


def _bp(side_sign: float, exit_px, entry_px):
    return round(side_sign * (exit_px / entry_px - 1.0) * 1e4, 2) if exit_px and entry_px else None


def _sign(side) -> float:
    return -1.0 if str(side or "").upper() in ("SHORT", "SELL") else 1.0


def _anchor_drift(side, paper_anchor, live_fill, traded: bool) -> dict:
    import order_model as _om
    out = _om.anchor_drift(side, paper_anchor, live_fill)
    if not traded:
        out["stop_anchor_drift_status"] = "LIVE_NOT_TRADED"
    return out


def pair_row(cid: str, chain: Mapping[str, Any] | None, twin: Mapping[str, Any] | None, intent: Mapping[str, Any] | None = None,
             skip_reason: str | None = None, fly_exit_send: Mapping[str, Any] | None = None) -> dict:
    chain, twin, intent = chain or {}, twin or {}, intent or {}
    sg = _sign(twin.get("side"))
    p_in, p_out = _pos(twin.get("entry_price")), _pos(twin.get("exit_price"))
    l_in, l_out = _pos(chain.get("fill_price")), _pos(chain.get("exit_price"))
    sig_ts = _f(chain.get("fly_signal_at_ts")) or _f(intent.get("fly_signal_at_ts")) or _f(twin.get("signal_ts"))
    sent, fill, stop = _f(chain.get("order_sent_at_ts")), _f(chain.get("fill_at_ts")), _f(chain.get("stop_confirmed_at_ts"))
    stop_ts = _f(chain.get("stop_placed_at_ts")) or stop
    traded = bool(l_in or fill)
    reason = None
    if not traded:
        dec = chain.get("executor_decision") or {}
        reason = (skip_reason or chain.get("declined_reason") or dec.get("reason")
                  or next((e.get("code") for e in chain.get("errors") or [] if e.get("code")), None)
                  or ("NO_LIVE_CHAIN" if not chain else "NO_FILL"))
    live_bp, paper_bp = _bp(sg, l_out, l_in), _bp(sg, p_out, p_in)
    row = {
        "trade_id": cid, "lane": chain.get("lane") or intent.get("lane"), "side": twin.get("side"),
        "live_traded": traded, "live_not_traded_reason": reason,
        "paper_fill_price": p_in, "paper_fill_ts": _f(twin.get("entry_fill_ts") or twin.get("entry_ts")),
        "live_fill_price": l_in, "live_fill_ts": fill,
        "entry_slippage_bp": round(sg * (l_in / p_in - 1.0) * 1e4, 2) if l_in and p_in else None,   # live minus paper, adverse > 0
        "paper_exit_price": p_out, "paper_exit_ts": _f(twin.get("exit_ts")),
        "live_exit_price": l_out, "live_exit_ts": _f(chain.get("exit_at_ts")),
        "exit_slippage_bp": round(-sg * (l_out / p_out - 1.0) * 1e4, 2) if l_out and p_out else None,  # adverse > 0
        "signal_to_send_ms": _ms(sig_ts, sent), "send_to_fill_ms": _ms(sent, fill), "fill_to_stop_ms": _ms(fill, stop_ts),
        "outcome_bp_paper": paper_bp, "outcome_bp_live": live_bp,
        "outcome_delta_bp": round(live_bp - paper_bp, 2) if live_bp is not None and paper_bp is not None else None,
        "funding_usd_paper": _f(twin.get("funding_usd_raw") if twin.get("funding_usd_raw") is not None else twin.get("funding_fees_usd")),
        "funding_usd_live": _f(chain.get("funding_usd")),
        "paper_exit_reason": twin.get("exit_reason"), "live_exit_reason": chain.get("exit_reason"),
        # #807: the paper exit's trigger vs the 1 s book at the trigger second (+ = book better than the trigger) and the
        # ticker BBO age it fired on. Measurement only.
        "paper_trigger_vs_book_bp": _f(twin.get("trigger_vs_book_bp")), "paper_trigger_tape_age_s": _f(twin.get("trigger_tape_age_s")),
        "paper_trigger_book_age_s": _f(twin.get("trigger_book_age_s")),
        "paper_trigger_vs_book_reason": twin.get("trigger_vs_book_reason"),
        "paper_trigger_tape_age_flag": twin.get("trigger_tape_age_flag"),
        # #809: the paper entry decision quote vs the book stored at the entry signal (measurement only) and the limit
        # price the live executor actually sent (LIMIT entries only, else null).
        **{f"paper_{k}": twin.get(f"paper_{k}") for k in _tm.ENTRY_QUOTE_KEYS + _tm.ENTRY_BOOK_KEYS},
        "live_limit_price_sent": _tm.live_limit_price_sent(chain),
        "closed": bool(chain.get("closed")),
        # HM G5/B2: one pair row per live signal; the twin status says whether both sides exist, and the stop-anchor
        # drift (paper anchors the exchange stop on the paper fill, live on the live fill) is measured from the pair.
        "twin_status": ("PAIRED" if chain and twin else "NO_PAPER_TWIN" if chain else "PAPER_ONLY"),
        **_anchor_drift(twin.get("side"), twin.get("exchange_stop_anchor_px") or p_in, l_in, traded),
        # HM R2 K3 / RK C: pair-level countability (both legs; live-only forced close -> paper twin non-countable)
        **pair_countability(chain, twin, fly_exit_send),
    }
    row["missing"] = sorted(k for k in ("paper_fill_ts", "live_exit_price", "live_exit_ts", "funding_usd_paper", "funding_usd_live",
                                         "fill_to_stop_ms") if traded and row.get(k) is None)
    return row


def build(chains: Iterable[Mapping[str, Any]], twins: Mapping[str, Mapping[str, Any]], intents: Mapping[str, Mapping[str, Any]] | None = None,
          paper_signal_ids: Iterable[str] = (), skip_reasons: Mapping[str, str] | None = None, lane_filter=None, limit: int = 200,
          exit_send_for=None) -> dict:
    """Rows for every live chain plus every paper signal live never traded (with a reason code). ``exit_send_for``
    (trade id -> this bot's Fly exit-send record) feeds the live per-leg check (HM 01:43 item 3); absent -> the
    exit Fly-send / relay-receive legs are missing and the live row is non-countable."""

    def _exit_send(ch):
        if not callable(exit_send_for):
            return None
        try:
            return exit_send_for(str(ch.get("trade_id") or ch.get("correlation_id") or ""))
        except Exception:  # noqa: BLE001 - a failed lookup is a missing stamp (fail closed)
            return None

    intents, skip_reasons = intents or {}, skip_reasons or {}
    rows, seen, live_ids = [], set(), set()
    for ch in chains:
        cid = str(ch.get("correlation_id") or "")
        if not cid or cid in seen:
            continue
        if lane_filter and not lane_filter(ch, twins.get(cid)):
            continue
        seen.add(cid)
        live_ids.add(cid)
        rows.append(pair_row(cid, ch, twins.get(cid), intents.get(cid), skip_reasons.get(cid), fly_exit_send=_exit_send(ch)))
    live_spans = [(_f(c.get("fill_at_ts")), _f(c.get("exit_at_ts")) or (None if not c.get("closed") else _f(c.get("closed_at_ts"))), str(c.get("correlation_id")))
                  for c in chains if _f(c.get("fill_at_ts"))]
    for cid in paper_signal_ids:
        cid = str(cid)
        if cid in seen:
            continue
        if cid not in skip_reasons:   # a paper signal that arrived while another live GS-01 position was open => LIVE_BUSY_SKIPPED
            sig = _f((intents.get(cid) or {}).get("fly_signal_at_ts"))
            if sig is not None and any(a <= sig and (b is None or sig < b) and o != cid for a, b, o in live_spans):
                skip_reasons = {**skip_reasons, cid: "LIVE_BUSY_SKIPPED"}
        if lane_filter and not lane_filter({}, twins.get(cid)):
            continue
        seen.add(cid)
        rows.append(pair_row(cid, None, twins.get(cid), intents.get(cid), skip_reasons.get(cid)))
    rows = rows[-limit:]
    traded = [r for r in rows if r["live_traded"]]
    live_rows = [r for r in rows if r["twin_status"] != "PAPER_ONLY"]
    by_reason: dict = {}
    for r in rows:
        if not r["live_traded"]:
            by_reason[r["live_not_traded_reason"]] = by_reason.get(r["live_not_traded_reason"], 0) + 1
    return {"schema": SCHEMA, "n_signals": len(rows), "n_live_traded": len(traded), "n_paper_only": len(rows) - len(traded),
            "paper_only_by_reason": by_reason, "n_missing_fields": sum(1 for r in traded if r["missing"]), "rows": rows,
            # HM G5: exactly one pair row per live signal (duplicate chains collapse by correlation id);
            # n_live_signals is counted before the row limit
            "n_live_signals": len(live_ids), "n_live_pair_rows": len(live_rows),
            "n_no_paper_twin": sum(1 for r in live_rows if r["twin_status"] == "NO_PAPER_TWIN"),
            "one_pair_row_per_live_signal": len(live_rows) == len({r["trade_id"] for r in live_rows}),
            # HM R2 K3: paper twins any stat must exclude because their live twin was force-closed (R8-live both legs)
            "n_non_countable_pairs": sum(1 for r in rows if r["non_countable"]),
            "paper_twin_non_countable_ids": sorted(r["trade_id"] for r in rows if r["paper_twin_non_countable"]),
            "note": "read-only; no summary computed here (HM). Null = not reported by the executor/paper ledger (see missing). Funding only, no fees."}

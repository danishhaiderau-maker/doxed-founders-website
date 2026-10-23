"""Read-only tile audit data API helpers (``/api/monitor/tiles/*``, ``/api/monitor/tape``).

Pure functions behind five GET routes (design: DATA-API-DESIGN.md, 4 Oct 2026).
Nothing here touches trading, relay, exchange or AI state: handlers in
``bot.py`` hand in shallow copies of in-memory rows and this module shapes,
filters, pages and totals them.  Every payload is bounded by a ``MAX_*_BYTES``
budget and carries a ``scope`` block that says exactly what was counted.

Maths follow ``monitor_api``: bp is price-based and net of fees
(``monitor_api.trade_net_bp``), USD is unrounded, and W/L/BE is decided by bp
with an explicit break-even band (never by cent-rounded USD).
"""
from __future__ import annotations

import base64
import trigger_measure as _trigger_measure
import json
import math
import statistics
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import fills_by_model as _fm
import monitor_api

TILE_SPECS_SCHEMA = "tile_specs_v1"
TILE_TRADES_SCHEMA = "tile_trades_v1"
TILE_TRADE_ROW_SCHEMA = "tile_trade_row_v1"
TILE_COUNTERS_SCHEMA = "tile_counters_v1"
TILE_TOTALS_SCHEMA = "tile_totals_v1"
TAPE_SCHEMA = "monitor_tape_v1"

MAX_TILE_SPECS_BYTES = 160 * 1024
MAX_TILE_SPECS_LANE_BYTES = 20 * 1024
MAX_TILE_TRADES_BYTES = 256 * 1024
MAX_TILE_COUNTERS_BYTES = 32 * 1024
MAX_TILE_TOTALS_BYTES = 16 * 1024
MAX_TAPE_BYTES = 192 * 1024
UNAUTHORIZED_BYTES = 256

TRADES_DEFAULT_LIMIT = 100
TRADES_MAX_LIMIT = 500
MAX_BE_BAND_BP = 2.0
MAX_TAPE_SLICE_SEC = 900
TAPE_RING_SECONDS = 3 * 3600
TRADE_STATUSES = ("all", "open", "closed", "expired", "pending")

TAPE_FIELDS = (
    "bid", "ask", "bid_qty", "ask_qty", "last", "buy_qty", "sell_qty", "buy_vwap", "sell_vwap",
    "trade_high", "trade_low", "source_age_sec", "fresh", "valid_bbo",
)
FILL_BASIS_UNCLASSIFIED = "UNCLASSIFIED"
FILL_BASIS_NOT_INDEXED = "NOT_INDEXED_SINCE_BOOT"
GIVEBACK_MFE_BP = 10.0


class BadRequest(ValueError):
    """A query parameter the route cannot honour (handler answers 400)."""


# --------------------------------------------------------------------------- params

def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_ts_param(value: Any, name: str) -> float | None:
    """ISO-8601 or epoch seconds -> epoch seconds; ``None`` when absent."""
    if value in (None, ""):
        return None
    number = _finite(value)
    if number is not None:
        return number
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise BadRequest(f"BAD_{name.upper()}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def parse_lanes(values: Iterable[str], known: Iterable[str]) -> list[str]:
    """Repeatable or comma-separated ``lane``; ids or short names; unknown -> 400."""
    known = list(known)
    out: list[str] = []
    for raw in values or ():
        for part in str(raw or "").split(","):
            lane = part.strip().upper()
            if not lane:
                continue
            if lane not in known:
                raise BadRequest(f"UNKNOWN_LANE:{lane[:64]}")
            if lane not in out:
                out.append(lane)
    return out


def parse_bool(value: Any, default: bool) -> bool:
    if value in (None, ""):
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes"):
        return True
    if text in ("0", "false", "no"):
        return False
    raise BadRequest("BAD_BOOLEAN")


def parse_be_band(value: Any) -> float:
    if value in (None, ""):
        return monitor_api.WL_BE_BAND_BP
    band = _finite(value)
    if band is None or band < 0 or band > MAX_BE_BAND_BP:
        raise BadRequest("BAD_BE_BAND_BP")
    return band


def parse_limit(value: Any) -> int:
    if value in (None, ""):
        return TRADES_DEFAULT_LIMIT
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise BadRequest("BAD_LIMIT") from exc
    if limit < 1:
        raise BadRequest("BAD_LIMIT")
    return min(limit, TRADES_MAX_LIMIT)


def resolve_epoch(value: Any, current_epoch_id: str | None) -> str | None:
    """Only the current epoch is held in memory; another epoch id is a 400."""
    text = str(value or "current").strip()
    if text in ("", "current") or (current_epoch_id and text == current_epoch_id):
        return current_epoch_id
    raise BadRequest("EPOCH_NOT_IN_MEMORY")


def encode_cursor(ts: float, trade_id: str, epoch: str | None) -> str:
    raw = json.dumps({"t": ts, "id": trade_id, "e": epoch}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(value: str | None) -> tuple[float, str, str | None] | None:
    if not value:
        return None
    try:
        padded = value + "=" * (-len(value) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return float(data["t"]), str(data["id"]), data.get("e")
    except Exception as exc:  # any malformed cursor is the caller's error
        raise BadRequest("BAD_CURSOR") from exc


def json_safe(value: Any, depth: int = 0) -> Any:
    """Registry objects -> JSON (tuples/sets to lists, non-finite floats to None)."""
    if depth > 12:
        return str(value)[:120]
    if isinstance(value, Mapping):
        return {str(k): json_safe(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v, depth + 1) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((json_safe(v, depth + 1) for v in value), key=str)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)[:240]


# --------------------------------------------------------------------------- trade rows

def trade_pnl(row: Mapping[str, Any], be_band_bp: float = monitor_api.WL_BE_BAND_BP) -> dict:
    """Price-based net bp, unrounded USD and a bp class for one closed row."""
    bp = monitor_api.trade_net_bp(row)
    raw = _finite(row.get("net_pnl_usd_raw"))
    booked = _finite(row.get("net_pnl_usd"))
    net = raw if raw is not None else booked
    basis = ("net_pnl_bp" if _finite(row.get("net_pnl_bp")) is not None
             else "PRICES_AND_LEGS" if bp is not None else None)
    return {
        "bp": round(bp, 4) if bp is not None else None,
        "bp_basis": basis,
        "gross_usd": _finite(row.get("gross_pnl_usd")),
        "trading_fees_usd": _finite(row.get("trading_fees_usd", row.get("fees_usd"))),
        "funding_usd": _finite(row.get("funding_fees_usd")),
        "funding_usd_raw": _finite(row.get("funding_usd_raw")),
        "funding_bp_raw": _finite(row.get("funding_bp_raw")),
        "net_usd": round(net, 6) if net is not None else None,
        "net_usd_basis": "net_pnl_usd_raw" if raw is not None else "net_pnl_usd_cents_booked",
        "class": monitor_api.wl_class(bp, be_band_bp=be_band_bp, net_usd=net),
        "be_band_bp": be_band_bp,
        "net_usd_cents_booked": booked,
    }


def _ts(value: Any, parse_ts=None) -> float | None:
    number = _finite(value)
    if number is not None:
        return number
    if value and parse_ts is not None:
        try:
            parsed = parse_ts(str(value))
        except Exception:
            return None
        return float(parsed) if parsed else None
    return None


def _side(row: Mapping[str, Any]) -> str | None:
    side = str(row.get("dir") or row.get("direction") or row.get("final_direction")
               or row.get("signal_dir") or row.get("side") or "").upper()
    if side in ("BUY", "LONG"):
        return "LONG"
    if side in ("SELL", "SHORT"):
        return "SHORT"
    return side or None


def _legs(row: Mapping[str, Any]) -> list:
    """Exit legs with the FILLED price. A partial leg's receipt price is already its book fill; the terminal leg's
    receipt price is the rule's trigger price, so it is replaced by the terminal fill (``exit_fill_sim.price``, or the
    row's ``exit_price`` when the row has a single leg) and the trigger kept as ``trigger_price``."""
    out = []
    receipts = monitor_api.partial_exit_legs(row)
    sim = row.get("exit_fill_sim") if isinstance(row.get("exit_fill_sim"), Mapping) else {}
    terminal_fill = _finite(sim.get("price")) if sim.get("status") == "FILLED" else None
    if terminal_fill is None and len(receipts) == 1:
        terminal_fill = _finite(row.get("exit_price"))
    for leg in receipts:
        price = _finite(leg.get("price"))
        trigger = _finite(leg.get("leg_trigger_price"))
        if (_finite(leg.get("remaining_fraction")) or 0.0) <= 0.0:  # terminal leg: receipt price is the trigger level
            trigger = price if trigger is None else trigger
            if terminal_fill is not None:
                price = terminal_fill
        out.append({
            "ts": _finite(leg.get("ts") or leg.get("observed_ts")),
            "qty": _finite(leg.get("closed_qty")),
            "price": price,
            "trigger_price": trigger,
            "reason": leg.get("reason") or leg.get("exit_reason"),
            "maker": leg.get("maker"),
        })
    return out


def paper_pricer_evidence(raw: Mapping[str, Any]) -> dict:
    """Pricer-own evidence of a book-walked paper fill (entry_fill_sim / fill_sim), else {}."""
    import paper_fill
    sim = raw.get("entry_fill_sim") if isinstance(raw.get("entry_fill_sim"), Mapping) else raw.get("fill_sim")
    if not isinstance(sim, Mapping) or str(sim.get("fill_model") or "") not in paper_fill.ALLOWED_FILL_MODELS:
        return {}
    model = str(sim["fill_model"])
    complete = sim.get("evidence_status") == paper_fill.EVIDENCE_COMPLETE  # explicit: missing/UNKNOWN is not COMPLETE
    return {"fill_model": model, "fill_basis": paper_fill.FILL_BASIS_TAKER_WALK,
            "execution_basis": (f"{model}:{paper_fill.FILL_BASIS_TAKER_WALK}" if complete else "UNSUPPORTED"),
            "liquidity": "TAKER", "evidence_status": sim.get("evidence_status")}


def _fill_block(fill_evidence: Mapping[str, Any] | None, *, filled: bool,
                raw: Mapping[str, Any] | None = None) -> dict | None:
    if not filled:
        return None
    evidence = dict(fill_evidence or {})
    paper = paper_pricer_evidence(raw) if isinstance(raw, Mapping) else {}
    if paper:  # the pricer's own evidence wins over venue-gate labels that never ran for these lanes
        evidence.update(paper)
    basis = evidence.get("fill_basis")
    if not evidence:
        basis = FILL_BASIS_NOT_INDEXED
    elif not basis:
        basis = FILL_BASIS_UNCLASSIFIED
    return {
        "fill_ts": _finite(evidence.get("fill_ts")),
        "fill_price": _finite(evidence.get("fill_price")),
        "fill_basis": basis,
        "fill_model": evidence.get("fill_model"),
        "execution_basis": evidence.get("execution_basis"),
        "liquidity": evidence.get("liquidity"),
        "queue_estimate": evidence.get("queue_estimate"),
        # fill_sim.evidence_status (COMPLETE/INCOMPLETE/NO_FILL); closed rows carry it flat (no sim object)
        "evidence_status": evidence.get("evidence_status") or ((raw or {}).get("evidence_status") if isinstance(raw, Mapping) else None),
    }


TRADE_TIMING_SCHEMA = "tile_trade_timing_v1"


def _gap(a, b):
    return round(b - a, 3) if a is not None and b is not None else None


def _gap_ms(a, b):
    g = _gap(a, b)
    return int(round(g * 1000.0)) if g is not None else None


def trade_timing(signal_ts, sent_ts, fill_ts, exit_trigger_ts, exit_fill_ts, *, recorded: bool,
                 exit_sent_ts=None) -> dict:
    """Per-trade stage stamps (signal -> sent -> fill -> exit trigger -> exit fill).

    Only stamps recorded when each stage happened (``event_timestamps``) are used;
    None means not observed, never back-calculated from durations.
    """
    return {
        "schema": TRADE_TIMING_SCHEMA,
        "basis": "RECORDED_EVENT_TIMESTAMPS" if recorded else "NOT_RECORDED",
        "signal_ts": signal_ts, "order_sent_ts": sent_ts, "fill_ts": fill_ts,
        "exit_trigger_ts": exit_trigger_ts, "exit_sent_ts": exit_sent_ts, "exit_fill_ts": exit_fill_ts,
        "exit_trigger_to_sent_sec": _gap(exit_trigger_ts, exit_sent_ts),
        "signal_to_sent_sec": _gap(signal_ts, sent_ts),
        "sent_to_fill_sec": _gap(sent_ts, fill_ts),
        "signal_to_fill_sec": _gap(signal_ts, fill_ts),
        "signal_to_fill_ms": _gap_ms(signal_ts, fill_ts),
        "signal_to_sent_ms": _gap_ms(signal_ts, sent_ts),
        "exit_trigger_to_fill_sec": _gap(exit_trigger_ts, exit_fill_ts),
    }


# --------------------------------------------------------------------------- late window
# 8 Oct 22:19 -> 10 Oct 00:46 AEDT: closes ran minutes late (trade_lock stall) but booked the
# stale trigger level. Rows closed in the window are flagged; stop-type exits listed in
# late_window_reprice.py are repriced at market (HM PNL-INTEGRITY-20261012). The booked
# value is kept under pnl.displayed_* so nothing is lost.
_LATE_WINDOW_CACHE: dict = {}


def late_window_table() -> dict:
    if "doc" not in _LATE_WINDOW_CACHE:
        try:
            import late_window_reprice as _lwr
            _LATE_WINDOW_CACHE["doc"] = _lwr.DATA
        except Exception:
            _LATE_WINDOW_CACHE["doc"] = {"window_ts": [0, 0], "trades": {}}
    return _LATE_WINDOW_CACHE["doc"]


def apply_late_window(row: dict, close_ts: Any, notional: Any, be_band_bp: float) -> None:
    doc = late_window_table()
    w0, w1 = (doc.get("window_ts") or [0, 0])[:2]
    ct = _finite(close_ts)
    in_window = ct is not None and w0 <= ct <= w1
    fix = (doc.get("trades") or {}).get(str(row.get("trade_id") or ""))
    if not in_window and not fix:
        return
    pnl = row.setdefault("pnl", {})
    pnl["late_window"] = True
    pnl["repriced"] = False
    if not fix:
        return
    mbp = _finite(fix.get("market_bp"))
    if mbp is None:
        return
    old_bp, old_usd = _finite(pnl.get("bp")), _finite(pnl.get("net_usd"))
    usd_per_bp = None
    if old_bp and old_usd is not None and abs(old_bp) > 1e-9:
        usd_per_bp = old_usd / old_bp
    elif _finite(notional):
        usd_per_bp = float(notional) / 1e4
    pnl["displayed_bp"], pnl["displayed_net_usd"] = old_bp, old_usd
    pnl["bp"] = round(mbp, 4)
    if usd_per_bp is not None:
        pnl["net_usd"] = round(mbp * usd_per_bp, 6)
    pnl["class"] = monitor_api.wl_class(pnl["bp"], be_band_bp=be_band_bp, net_usd=pnl.get("net_usd"))
    pnl["repriced"] = True
    pnl["repriced_basis"] = "LATE_WINDOW_MARKET_1M_CLOSE"


def _late_window_block(rows) -> dict:
    late = [r for r in rows if (r.get("pnl") or {}).get("late_window")]
    rep = [r for r in late if (r.get("pnl") or {}).get("repriced")]
    over = sum((_finite(r["pnl"].get("displayed_net_usd")) or 0.0) - (_finite(r["pnl"].get("net_usd")) or 0.0)
               for r in rep)
    return {"closes_in_window": len(late), "repriced": len(rep), "displayed_overstatement_usd": round(over, 6),
            "window_aedt": late_window_table().get("window_aedt"), "totals_use_repriced": True}


PRICING_KEYS = ("non_countable", "non_countable_reason", "fill_model", "fill_model_version", "delay_s", "delay_source", "priced_delay_s", "priced_delay_model", "ask_age_ms", "exit_ask_age_ms",
                "depth_snapshot_signal", "depth_snapshot_fill", "exit_depth_snapshot_fill", "exit_price_basis",
                "fee_model", "funding_rate", "funding_source", "funding_rate_source_detail", "hold_seconds", "funding_ts_crossed",
                "funding_settlement_rates", "fill_model_tag", "legacy_pricing", "exit_status",
                "exit_depth_snapshot_signal", "exit_depth_snapshot_legs", "pricer_signal_ts", "model_fill_ts",
                "exit_pricer_signal_ts", "exit_model_fill_ts", "book_slippage_usd_entry", "book_slippage_usd_exit",
                "book_slippage_usd_total", "entry_walk_bp", "exit_walk_bp", "entry_delay_drift_usd",
                "entry_delay_drift_bp", "exit_delay_drift_usd", "exit_delay_drift_bp", "exit_touch_drift_usd",
                "exit_touch_drift_bp", "exit_trigger_overshoot_bp", "exit_legs", "exit_legs_unmeasured",
                # #731: repriced exits are distinct and carry their lag (the pre-registered valid-trade filter needs both)
                "exit_pricing_basis", "exit_repriced_lag_s",
                # REALISM_V2 F5: settlement-exact funding (raw, per settlement, aligned with funding_ts_crossed)
                "funding_model", "funding_status", "funding_verified", "funding_usd_raw", "funding_rate_applied",
                "funding_rate_ts", "funding_mark_price", "funding_mark_ts", "funding_usd_by_settlement",
                "funding_settlement_status")


_PRICING_NUMERIC = ("fill_model_version", "delay_s", "ask_age_ms", "exit_ask_age_ms",
                    "pricer_signal_ts", "model_fill_ts", "exit_pricer_signal_ts", "exit_model_fill_ts",
                    "book_slippage_usd_entry", "book_slippage_usd_exit", "book_slippage_usd_total", "entry_walk_bp",
                    "exit_walk_bp", "entry_delay_drift_usd", "entry_delay_drift_bp", "exit_delay_drift_usd",
                    "exit_delay_drift_bp", "exit_touch_drift_usd", "exit_touch_drift_bp",
                    "exit_trigger_overshoot_bp", "exit_legs", "exit_legs_unmeasured", "exit_repriced_lag_s",
                    "funding_rate", "funding_usd_raw")
_PRICING_STRUCT = ("depth_snapshot_signal", "depth_snapshot_fill", "exit_depth_snapshot_fill",
                   "exit_depth_snapshot_signal", "exit_depth_snapshot_legs",
                   "entry_provenance", "exit_provenance", "funding_settlement_rates",
                   "funding_rate_applied", "funding_rate_ts", "funding_mark_price", "funding_mark_ts",
                   "funding_usd_by_settlement", "funding_settlement_status")
_PRICING_BOOL = ("legacy_pricing", "funding_verified")


def _restore_pricing_value(key: str, value: Any) -> Any:
    """CSV-reloaded rows carry strings: give numbers, bools and dict/list reprs their types back."""
    import ast as _ast
    import paper_fill as _pf
    if key == "funding_ts_crossed":
        # F5/HM: 277 pre-19-Oct rows hold the string form ('[]' x264, '[1792310400]' etc x13): JSON-parse, never "empty"
        import tile_fill as _tf
        return _tf.parse_funding_ts_crossed(value)
    if key in _PRICING_NUMERIC:
        n = _pf.as_number(value)
        return int(n) if n is not None and n == int(n) and key in ("fill_model_version", "exit_legs", "exit_legs_unmeasured") else n
    if key in _PRICING_BOOL:
        return _pf.as_bool(value)
    if key in _PRICING_STRUCT and isinstance(value, str):
        text = value.strip()
        if text[:1] in ("{", "["):
            try:
                return _ast.literal_eval(text)
            except (ValueError, SyntaxError):
                return None
        return None if text == "" else value
    return None if value == "" else value


def pricing_block(raw: Mapping[str, Any]) -> dict:
    """Pricing provenance read from the row itself (never the old fill-evidence index).
    fresh_eligible is recomputed from the pricing facts (row_fresh_strict) and a stored flag may only
    lower it; stored strings from a CSV reload ("False") are parsed, never bool()-ed."""
    import paper_fill as _pf
    block = {k: _restore_pricing_value(k, raw.get(k)) for k in PRICING_KEYS}
    typed = {**dict(raw), **{k: block[k] for k in block}}
    stored = _pf.as_bool(raw.get("fresh_eligible"))
    block["fresh_eligible"] = bool(_pf.row_fresh_strict(typed) and stored is not False)
    if not block["fresh_eligible"]:
        import r17_exclusion as _r17
        reason = _r17.non_countable_reason(typed)
        if reason:  # the tile shows WHY the row is not counted (R17_UNPROVEN:<failed checks>), never a bare "stale"
            block["non_countable"] = True
            block["non_countable_reason"] = reason
            block["r17_label"] = "R17_UNPROVEN" if reason.startswith("R17_UNPROVEN") else "R17"
    return block


def open_pricing_block(raw: Mapping[str, Any]) -> dict | None:
    """Open rows have only the entry receipt (exit is priced at close): show it, never as fresh data."""
    efs = raw.get("entry_fill_sim")
    if not isinstance(efs, Mapping) or not efs:
        return None
    out = {k: _restore_pricing_value(k, efs.get(k)) for k in
           ("fill_model", "fill_model_version", "delay_s", "delay_source", "ask_age_ms",
            "depth_snapshot_signal", "depth_snapshot_fill")}
    out["state"] = "OPEN: entry priced, exit priced at close"
    out["fresh_eligible"] = None
    return out


def row_is_fresh(row: Mapping[str, Any]) -> bool:
    """fresh_only=1 filter: closed rows must be fresh_eligible (open/pending/expired rows are not trades)."""
    if row.get("status") != "CLOSED":
        return True
    return bool((row.get("pricing") or {}).get("fresh_eligible")) and not row.get("identity_quarantined")


def tile_trade_row(raw: Mapping[str, Any], *, status: str, short_names: Mapping[str, str],
                   epoch_id: str | None, forced_reasons: Iterable[str],
                   fill_evidence: Mapping[str, Any] | None = None, parse_ts=None,
                   be_band_bp: float = monitor_api.WL_BE_BAND_BP) -> dict:
    """One ``tile_trade_row_v1`` from a closed row, open position, pending or expired order."""
    lane = str(raw.get("research_lane") or "").upper() or None
    forced = {str(r).upper() for r in forced_reasons}
    leverage = _finite(raw.get("leverage"))
    margin = _finite(raw.get("margin_usdt") or raw.get("margin_usd"))
    entry = _finite(raw.get("entry") or raw.get("entry_price") or raw.get("fill_price"))
    qty = _finite(raw.get("policy_original_qty") or raw.get("qty") or raw.get("execution_qty"))
    notional = (margin * leverage) if margin and leverage else (entry * qty if entry and qty else None)
    signal_ts = _ts(raw.get("shared_ai_call_ts"), parse_ts)
    decision = raw.get("adaptive_entry_decision") if isinstance(raw.get("adaptive_entry_decision"), Mapping) else {}
    # GS/B regime tiles stamp regime_at_signal / regime_cell on the decision at
    # entry; surface them on every trade row so Health Monitor can conformance-
    # check Tile 12/13 without reading adaptive_entry_decisions.jsonl.
    trigger = {"shared_ai_call_id": raw.get("shared_ai_call_id"), "signal_ts": signal_ts,
               "entry_path": raw.get("entry_path"), "entry_type": raw.get("entry_type")}
    for key in ("regime_at_signal", "regime_at_entry", "regime", "regime_cell", "exit_profile",
                "action", "reason", "trigger_kind"):
        value = decision.get(key)
        if value is None and key == "regime_at_signal":
            value = decision.get("regime_at_entry") or decision.get("regime") or raw.get("regime_at_signal")
        if value is not None and value != "":
            trigger[key] = value
    if "regime_at_signal" in trigger:
        trigger.setdefault("regime_at_signal_source", raw.get("regime_at_signal_source")
                           or ("adaptive_entry_decision" if decision else "trade_row"))
    else:  # Strategist 10:37: make the gap explicit instead of silently absent
        trigger["regime_at_signal"] = None
        trigger["regime_at_signal_source"] = "MISSING"
    row: dict = {
        "schema": TILE_TRADE_ROW_SCHEMA,
        "trade_id": str(raw.get("trade_id") or ""),
        "lane": lane,
        "tile_short": short_names.get(lane or "") if lane else None,
        "status": status.upper(),
        "data_epoch_id": epoch_id,
        # Canonical fresh id (fse-<epoch_ts>) beside the pinned data_epoch_id: a LABEL ONLY, never a gate (freshness is
        # decided by paper_fill.row_fresh_strict on the pricing facts and the epoch floor, not by this string).
        "fresh_epoch_id": raw.get("fresh_epoch_id"),
        # The stored numeric stamps paper_fill.row_before_epoch reads (None = row fails closed as never-fresh).
        "entry_ts": _finite(raw.get("entry_ts")),
        "exit_ts": _finite(raw.get("exit_ts")),
        "collector_epoch_id": raw.get("epoch_id"),
        # Separate from pricing.fresh_eligible (row_fresh_strict stays pinned): a trade of an episode whose decision and
        # execution minted different policy signatures is shown but must never be counted (cleared at the next reset).
        "identity_quarantined": str(raw.get("trade_id") or "") in _fm.IDENTITY_QUARANTINED_TRADE_IDS,
        "policy_signature": raw.get("policy_signature"),
        "side": _side(raw),
        "requested_qty": _finite(raw.get("requested_qty") or raw.get("policy_original_qty") or raw.get("qty")),
        "notional_usd": round(notional, 6) if notional else None,
        "leverage": leverage,
        "trigger": trigger,
    }
    if status == "closed":
        close_ts = _ts(raw.get("close_ts") or raw.get("ts"), parse_ts)
        duration = _finite(raw.get("outcome_duration_sec"))
        if duration is None and _finite(raw.get("duration_min")) is not None:
            duration = _finite(raw.get("duration_min")) * 60.0
        entry_ts = (close_ts - duration) if close_ts and duration is not None else None
        reason = str(raw.get("exit_reason") or "").upper()
        mfe_pct = _finite(raw.get("max_profit"))
        mfe_bp = (mfe_pct * 100.0 / leverage) if mfe_pct is not None and leverage else None
        events = raw.get("event_timestamps") if isinstance(raw.get("event_timestamps"), Mapping) else {}
        ev_signal = _ts(events.get("signal_ts"), parse_ts)
        ev_sent = _ts(events.get("order_sent_ts"), parse_ts)
        ev_fill = _ts(events.get("fill_ts"), parse_ts)
        ev_trigger = _ts(events.get("exit_trigger_ts"), parse_ts)
        ev_exit_fill = _ts(events.get("exit_fill_ts"), parse_ts)
        ev_exit_sent = _ts(events.get("exit_sent_ts"), parse_ts)
        if row["trigger"].get("signal_ts") is None and ev_signal:
            row["trigger"]["signal_ts"] = ev_signal
        if ev_fill and entry_ts is None:
            entry_ts = ev_fill
        row["order"] = {"submitted_ts": ev_sent, "limit_chase_count": raw.get("limit_chase_count"),
                        "terminal": "FILLED"}
        row["fill"] = _fill_block(fill_evidence, filled=True, raw=raw) or {}
        row["pricing"] = pricing_block(raw)
        row["pricing"].update({"fresh_epoch_id": raw.get("fresh_epoch_id"), "fill_valid": raw.get("fill_valid"),
                               "non_fill_category": raw.get("non_fill_category"),
                               "all_legs_book10l": raw.get("all_legs_book10l")})
        if raw.get("fill_model"):
            row["fill"]["fill_model"] = raw.get("fill_model")  # the row's own model beats the evidence index
        if row["fill"].get("fill_price") is None:
            row["fill"]["fill_price"] = entry
        if row["fill"].get("fill_ts") is None:
            if ev_fill:
                row["fill"]["fill_ts"] = ev_fill
                row["fill"]["fill_ts_basis"] = events.get("fill_ts_basis") or "RECORDED_AT_PAPER_FILL"
            else:
                row["fill"]["fill_ts"] = entry_ts
                row["fill"]["fill_ts_basis"] = "CLOSE_TS_MINUS_DURATION" if entry_ts else None
        row["exit"] = {
            "close_ts": close_ts, "exit_price": _finite(raw.get("exit_price") if raw.get("exit_price_basis") == "VWAP_ALL_LEGS"
                                                      else (raw.get("exit") or raw.get("exit_price"))),
            "exit_price_basis": raw.get("exit_price_basis"),
            "exit_bid": _finite(raw.get("exit_bid")), "exit_ask": _finite(raw.get("exit_ask")),  # legacy rows only (ticker tape)
            "exit_book_top_bid": _finite(raw.get("exit_book_top_bid")), "exit_book_top_ask": _finite(raw.get("exit_book_top_ask")),
            "exit_book_top_s": _finite(raw.get("exit_book_top_s")),
            "exit_reason": raw.get("exit_reason"), "close_origin": raw.get("close_origin"),
            "forced_close": reason in forced, "legs": _legs(raw),
            "deploy_flatten": bool(reason in forced and (
                raw.get("deploy_flatten") is True
                or str(raw.get("close_origin") or "").upper() == "DEPLOY_MAINTENANCE")),
            "filled_qty_final_leg": _finite(raw.get("execution_qty")),
            "exit_trigger_ts": ev_trigger,
            "exit_trigger_source": events.get("exit_trigger_source"),
            "exit_trigger_price": _finite(events.get("exit_trigger_price")),
            "exit_sent_ts": ev_exit_sent,
            "exit_fill_ts": ev_exit_fill,
        }
        # #807 measurement (measurement only, never a fill input): trigger price vs the 1 s book stored at the trigger
        # (null above 2 s book age), the book age, and trigger_tape_age_s = time since the last ticker MESSAGE at the
        # trigger (cannot see a frozen BBO; frozen-BBO detection is streams/health tape_freeze). Journal-twin rows
        # without event_timestamps / exit snapshot carry nulls.
        row["exit"].update(_trigger_measure.fields(
            side=row.get("side"), trigger_price=row["exit"].get("exit_trigger_price"), trigger_ts=ev_trigger,
            bbo_ts=_ts(events.get("exit_trigger_bbo_ts"), parse_ts),
            book_signal=(row.get("pricing") or {}).get("exit_depth_snapshot_signal")))
        # #809 entry measurement: the decision quote vs the book stored with the trade at the entry signal. Computed
        # here at row-build time (no hot-path book read); measurement only.
        row["trigger"].update(_trigger_measure.entry_fields(
            row.get("side"), decision, row["pricing"].get("depth_snapshot_signal"), row["pricing"].get("pricer_signal_ts")))
        row["timing"] = trade_timing(ev_signal or row["trigger"].get("signal_ts"), ev_sent,
                                     row["fill"].get("fill_ts") if ev_fill else None, ev_trigger, ev_exit_fill,
                                     recorded=bool(events), exit_sent_ts=ev_exit_sent)
        row["path"] = {"mfe_bp": round(mfe_bp, 4) if mfe_bp is not None else None,
                       "basis": "max_pnl_pct_over_leverage" if mfe_bp is not None else None}
        row["pnl"] = trade_pnl(raw, be_band_bp)
        apply_late_window(row, close_ts, notional, be_band_bp)
        row["sort_ts"] = entry_ts or signal_ts or close_ts or 0.0
    elif status == "open":
        state = raw.get("policy_state") if isinstance(raw.get("policy_state"), Mapping) else {}
        entry_ts = _ts(raw.get("entry_ts"), parse_ts)
        row["order"] = {"submitted_ts": _ts(raw.get("order_created_ts"), parse_ts), "terminal": "FILLED"}
        row["fill"] = _fill_block(fill_evidence, filled=True, raw=raw) or {}
        if row["fill"].get("fill_price") is None:
            row["fill"]["fill_price"] = entry
        if row["fill"].get("fill_ts") is None:
            row["fill"]["fill_ts"] = entry_ts
        row["protection"] = {
            "be_armed": state.get("be_armed"), "trail_armed": state.get("trail_armed"),
            "tp1_done": state.get("tp1_done"), "peak_bp": _finite(state.get("peak_bp")),
            "remaining_fraction": _finite(raw.get("policy_remaining_fraction")),
        }
        row["exit"] = {"legs": _legs(raw)}
        _open_pricing = open_pricing_block(raw)
        if _open_pricing:
            row["pricing"] = _open_pricing
            if _open_pricing.get("fill_model"):
                row["fill"]["fill_model"] = _open_pricing["fill_model"]  # the receipt's own model, not the evidence index
        row["sort_ts"] = row["order"]["submitted_ts"] or entry_ts or signal_ts or 0.0
    elif status == "pending":
        submitted = _ts(raw.get("order_created_ts") or raw.get("created_ts"), parse_ts)
        row["order"] = {"submitted_ts": submitted, "limit_price": _finite(raw.get("limit_price")),
                        "limit_chase_count": raw.get("limit_chase_count"), "status": raw.get("status"),
                        "terminal": None}
        row["sort_ts"] = submitted or signal_ts or 0.0
    else:  # expired
        created = _ts(raw.get("created_ts"), parse_ts)
        row["order"] = {"submitted_ts": created, "limit_price": _finite(raw.get("limit_price")),
                        "expired_ts": _ts(raw.get("expired_ts"), parse_ts),
                        "terminal": "EXPIRED", "terminal_reason": raw.get("reason"),
                        "touched_limit": raw.get("touched_limit"),
                        "missed_by_usd": _finite(raw.get("missed_by_usd"))}
        row["sort_ts"] = created or _ts(raw.get("expired_ts"), parse_ts) or signal_ts or 0.0
    row["sort_ts"] = float(row["sort_ts"] or 0.0)
    return row


def row_in_window(row: Mapping[str, Any], since: float | None, until: float | None) -> bool:
    """A row belongs to the window by its terminal time (close/expiry) or, if live, its start."""
    status = row.get("status")
    if status == "CLOSED":
        ts = (row.get("exit") or {}).get("close_ts")
    elif status == "EXPIRED":
        ts = (row.get("order") or {}).get("expired_ts") or row.get("sort_ts")
    else:
        ts = None  # open/pending rows are always current
    if ts is None:
        return True
    if since is not None and ts < since - 1.0:
        return False
    if until is not None and ts > until:
        return False
    return True


def trades_page(rows: list[dict], *, cursor: str | None, limit: int, max_bytes: int,
                epoch: str | None, descending: bool = False) -> dict:
    """Whole rows ordered by (sort_ts, trade_id) (newest first when ``descending``), cut at ``limit`` or the byte budget."""
    def _key(r):
        return (float(r.get("sort_ts") or 0.0), str(r.get("trade_id") or ""))
    ordered = sorted(rows, key=_key, reverse=descending)
    after = decode_cursor(cursor)
    if after is not None:
        if after[2] != epoch:
            raise BadRequest("CURSOR_EPOCH_MISMATCH")
        key = (after[0], after[1])
        ordered = [r for r in ordered if (_key(r) < key if descending else _key(r) > key)]
    page: list[dict] = []
    used = 1024  # envelope allowance
    for row in ordered:
        if len(page) >= limit:
            break
        # Measured with default separators (larger than the compact wire form).
        size = len(json.dumps(row, default=str).encode("utf-8")) + 2
        if page and used + size > max_bytes:
            break
        page.append(row)
        used += size
    more = len(page) < len(ordered)
    next_cursor = None
    if more and page:
        last = page[-1]
        next_cursor = encode_cursor(float(last.get("sort_ts") or 0.0), str(last.get("trade_id") or ""), epoch)
    return {"rows": page, "returned": len(page), "remaining_after_page": len(ordered) - len(page),
            "next_cursor": next_cursor}


# --------------------------------------------------------------------------- totals

def _drawdown(values: Iterable[float]) -> float:
    cumulative = peak = drawdown = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    return drawdown


def _deploy_flatten_block(rows) -> dict:
    """Separate stats for closes forced by a guarded deploy's flatten (excluded from strategy stats)."""
    bps = [b for b in (_finite((r.get("pnl") or {}).get("bp")) for r in rows) if b is not None]
    nets = [_finite((r.get("pnl") or {}).get("net_usd")) or 0.0 for r in rows]
    return {"closes": len(rows), "net_usd": round(sum(nets), 6),
            "sum_bp": round(sum(bps), 4) if bps else 0.0,
            "mean_bp": round(sum(bps) / len(bps), 4) if bps else None,
            "excluded_from_strategy_stats": True}


def lane_totals(rows: Iterable[Mapping[str, Any]], *, include_forced: bool,
                be_band_bp: float) -> dict:
    """Closed-trade totals for one lane from ``tile_trade_row_v1`` rows (by bp, unrounded USD)."""
    closed = sorted((r for r in rows if r.get("status") == "CLOSED"),
                    key=lambda r: float((r.get("exit") or {}).get("close_ts") or 0.0))
    forced = [r for r in closed if (r.get("exit") or {}).get("forced_close")]
    deploy_flat = [r for r in forced if (r.get("exit") or {}).get("deploy_flatten")]
    counted = closed if include_forced else [r for r in closed if not (r.get("exit") or {}).get("forced_close")]
    bps, nets = [], []
    wins = losses = be = 0
    gross_w = gross_l = long_net = short_net = 0.0
    long_n = short_n = givebacks = mfe_known = 0
    hours = set()
    for row in counted:
        pnl = row.get("pnl") or {}
        bp = _finite(pnl.get("bp"))
        net = _finite(pnl.get("net_usd")) or 0.0
        cls = monitor_api.wl_class(bp, be_band_bp=be_band_bp, net_usd=net)
        wins += cls == "W"
        losses += cls == "L"
        be += cls == "BE"
        if bp is not None:
            bps.append(bp)
        nets.append(net)
        if net > 0:
            gross_w += net
        elif net < 0:
            gross_l += net
        if row.get("side") == "LONG":
            long_n += 1
            long_net += net
        elif row.get("side") == "SHORT":
            short_n += 1
            short_net += net
        close_ts = _finite((row.get("exit") or {}).get("close_ts"))
        if close_ts:
            hours.add(int(close_ts // 3600))
        mfe = _finite((row.get("path") or {}).get("mfe_bp"))
        if mfe is not None:
            mfe_known += 1
            if mfe >= GIVEBACK_MFE_BP and (bp if bp is not None else net) <= 0:
                givebacks += 1
    n = len(counted)
    return {
        "closes": n, "wins": wins, "losses": losses, "be": be,
        "forced_closes": len(forced), "forced_closes_counted": len(forced) if include_forced else 0,
        "deploy_flatten_closes": len(deploy_flat),
        # Deploy force-flattens shown separately (never part of strategy stats above).
        "deploy_flatten": _deploy_flatten_block(deploy_flat),
        "late_window": _late_window_block(counted),
        "sum_bp": round(sum(bps), 4) if bps else 0.0,
        "mean_bp": round(sum(bps) / len(bps), 4) if bps else None,
        "median_bp": round(statistics.median(bps), 4) if bps else None,
        "bp_known": len(bps),
        "net_usd": round(sum(nets), 6),
        "gross_wins_usd": round(gross_w, 6), "gross_losses_usd": round(gross_l, 6),
        "long_closes": long_n, "long_net_usd": round(long_net, 6),
        "short_closes": short_n, "short_net_usd": round(short_net, 6),
        "n_eff_hours": len(hours),
        "max_drawdown_usd": round(_drawdown(nets), 6),
        "worst_trade_bp": round(min(bps), 4) if bps else None,
        "giveback_share": round(givebacks / mfe_known, 4) if mfe_known else None,
        "giveback_rule": f"mfe_bp>={GIVEBACK_MFE_BP:g} and closed<=0 (mfe from max_pnl_pct/leverage)",
    }


def totals_view(rows_by_lane: Mapping[str, list], *, include_forced: bool, be_band_bp: float,
                route_counts: Mapping[str, Any] | None = None) -> dict:
    """Per-lane totals plus parity against the dashboard ``tile_route_counts.closed``."""
    lanes = {}
    parity = {}
    for lane, rows in rows_by_lane.items():
        totals = lane_totals(rows, include_forced=include_forced, be_band_bp=be_band_bp)
        recount = sum(1 for r in rows if r.get("status") == "CLOSED"
                      and (include_forced or not (r.get("exit") or {}).get("forced_close")))
        lanes[lane] = totals
        check = {"sum_of_trades_closes": recount, "totals_closes": totals["closes"],
                 "totals_equal_sum_of_trades": recount == totals["closes"]}
        counts = (route_counts or {}).get(lane) if isinstance(route_counts, Mapping) else None
        if isinstance(counts, Mapping) and counts.get("closed") is not None and not include_forced:
            try:
                closed = int(counts.get("closed"))
            except (TypeError, ValueError):
                closed = None
            check["tile_route_counts_closed"] = closed
            check["tile_route_counts_match"] = closed == totals["closes"] if closed is not None else None
        parity[lane] = check
    return {"lanes": lanes, "parity": parity}


# --------------------------------------------------------------------------- counters

def counters_view(lane: str, *, rows: list[dict], route_counts: Mapping[str, Any] | None,
                  xvl_lane: Mapping[str, Any] | None, opportunity: Mapping[str, Any] | None,
                  boot_id: str | None, adaptive_lane: Mapping[str, Any] | None = None) -> dict:
    """Epoch counters for one lane from trade rows + the dashboard's route counts."""
    counts = dict(route_counts or {}) if isinstance(route_counts, Mapping) else {}
    closes_policy = sum(1 for r in rows if r.get("status") == "CLOSED"
                        and not (r.get("exit") or {}).get("forced_close"))
    closes_forced = sum(1 for r in rows if r.get("status") == "CLOSED"
                        and (r.get("exit") or {}).get("forced_close"))
    open_n = sum(1 for r in rows if r.get("status") == "OPEN")
    pending_n = sum(1 for r in rows if r.get("status") == "PENDING")
    fills = closes_policy + closes_forced + open_n
    unclassified = sum(1 for r in rows if (r.get("fill") or {}).get("fill_basis") == FILL_BASIS_UNCLASSIFIED)
    not_indexed = sum(1 for r in rows if (r.get("fill") or {}).get("fill_basis") == FILL_BASIS_NOT_INDEXED)
    fill_hours = set()
    for r in rows:
        ts = _finite((r.get("fill") or {}).get("fill_ts"))
        if ts and r.get("status") in ("CLOSED", "OPEN"):
            fill_hours.add(int(ts // 3600))

    def count(key):
        try:
            return int(counts.get(key)) if counts.get(key) is not None else None
        except (TypeError, ValueError):
            return None

    expired = count("expired")
    orders = (fills + pending_n + expired) if expired is not None else None
    xvl_paper = dict((xvl_lane or {}).get("paper") or {}) if isinstance(xvl_lane, Mapping) else {}
    skips = dict(xvl_paper.get("skips") or {})
    epoch = {
        "selected_calls": count("selected_calls"),
        "approved_no_order": count("approved_no_order"),
        "orders": orders,
        "fills": fills,
        "expiries": expired,
        "closes_policy": closes_policy,
        "closes_forced": closes_forced,
        "open": open_n,
        "pending": pending_n,
        "fill_rate": round(fills / (fills + expired), 4) if expired is not None and (fills + expired) else None,
        "distinct_fill_hours": len(fill_hours),
        "route_counts": {k: counts.get(k) for k in sorted(counts) if not isinstance(counts.get(k), (dict, list))},
    }
    identity = {
        "fills_eq_closes_plus_open": fills == closes_policy + closes_forced + open_n,
        "route_counts_closed": count("closed"),
        "route_counts_closed_matches_policy_closes": (count("closed") == closes_policy
                                                      if count("closed") is not None else None),
        "route_counts_open_matches": (count("open") == open_n if count("open") is not None else None),
    }
    return {
        "lane": lane,
        "epoch": epoch,
        "since_boot": {
            "boot_id": boot_id,
            "lane_opportunity_counters": dict(opportunity or {}),
            "xvl_paper": {k: xvl_paper.get(k) for k in ("attempts", "orders_eligible", "submissions_last_hour")
                          if k in xvl_paper},
            "rejects_and_skips": skips,
            "last_attempt_outcome": ((xvl_paper.get("last_attempt") or {}).get("outcome")
                                     if isinstance(xvl_paper.get("last_attempt"), Mapping) else None),
            # GS-05 QUIET stand-aside / GS-06 VIOLENT cell: adaptive_entry_decisions since boot.
            "adaptive_entry": {
                "decisions": int((adaptive_lane or {}).get("decisions") or 0),
                "stand_aside": int((adaptive_lane or {}).get("stand_aside") or 0),
                "shadow_stand_aside": int((adaptive_lane or {}).get("shadow_stand_aside") or 0),
                "submit": int((adaptive_lane or {}).get("submit") or 0),
                "by_action": dict((adaptive_lane or {}).get("by_action") or {}),
                "by_regime": dict((adaptive_lane or {}).get("by_regime") or {}),
                "by_reason": dict((adaptive_lane or {}).get("by_reason") or {}),
            },
        },
        "integrity": {
            "accepted_without_order": count("approved_no_order"),
            "pre_entry_evidence_unavailable_since_boot": int(skips.get("PRE_ENTRY_EVIDENCE_UNAVAILABLE") or 0),
            "unclassified_fills": unclassified,
            "fills_not_indexed_since_boot": not_indexed,
            "identity": identity,
        },
    }


# --------------------------------------------------------------------------- specs

def tile_spec(lane: str, spec: Mapping[str, Any], *, signal_clocks: Mapping[str, Any],
              manifest_row: Mapping[str, Any] | None) -> dict:
    out = json_safe(dict(spec))
    out["lane"] = lane
    out["signal_clock_registry"] = spec.get("signal_clock")
    out["signal_clocks"] = json_safe(dict(signal_clocks or {}))
    if manifest_row is not None:
        out["lifecycle_manifest"] = json_safe(dict(manifest_row))
    return out


# --------------------------------------------------------------------------- tape

class TapeSliceRing:
    """Last ``TAPE_RING_SECONDS`` of 1 s microstructure rows (all buckets, own lock).

    Fed beside the existing tape writer; reads copy only the requested slice.
    """

    def __init__(self, max_seconds: int = TAPE_RING_SECONDS, fields: tuple = TAPE_FIELDS) -> None:
        self._lock = threading.Lock()
        self._fields = tuple(fields)
        self._ts: deque = deque(maxlen=int(max_seconds))
        self._rows: deque = deque(maxlen=int(max_seconds))
        self.dropped_out_of_order = 0

    @property
    def fields(self) -> tuple:
        return self._fields

    def append(self, row: Mapping[str, Any]) -> bool:
        if not isinstance(row, Mapping):
            return False
        try:
            ts = int(row.get("bucket_ts"))
        except (TypeError, ValueError):
            return False
        values = []
        for name in self._fields:
            value = row.get(name)
            if isinstance(value, bool) or value is None:
                values.append(value)
            else:
                number = _finite(value)
                values.append(round(number, 8) if number is not None else None)
        item = tuple(values)
        with self._lock:
            if self._ts and ts <= self._ts[-1]:
                self.dropped_out_of_order += 1
                return False
            self._ts.append(ts)
            self._rows.append(item)
        return True

    def bounds(self) -> tuple[int | None, int | None, int]:
        with self._lock:
            if not self._ts:
                return None, None, 0
            return self._ts[0], self._ts[-1], len(self._ts)

    def slice(self, from_ts: float, to_ts: float) -> tuple[list, list]:
        with self._lock:
            if not self._ts or to_ts < self._ts[0] or from_ts > self._ts[-1]:
                return [], []
            pairs = [(t, r) for t, r in zip(self._ts, self._rows) if from_ts <= t <= to_ts]
        return [t for t, _ in pairs], [r for _, r in pairs]


def tape_view(ring: TapeSliceRing | None, *, from_ts: float, to_ts: float, fields: list[str] | None,
              now: float) -> dict:
    """Columnar slice from the ring, or a pointer when the range is outside it."""
    if to_ts < from_ts:
        raise BadRequest("TO_BEFORE_FROM")
    if to_ts - from_ts > MAX_TAPE_SLICE_SEC:
        raise BadRequest("SLICE_OVER_900S")
    if ring is None:
        return {"rows": [], "cols": [], "ring": None, "error": "TAPE_RING_UNAVAILABLE"}
    wanted = list(fields or ring.fields)
    unknown = [f for f in wanted if f not in ring.fields]
    if unknown:
        raise BadRequest(f"UNKNOWN_FIELD:{unknown[0][:40]}")
    index = [ring.fields.index(f) for f in wanted]
    first, last, size = ring.bounds()
    ring_info = {"first_bucket_ts": first, "last_bucket_ts": last, "rows": size,
                 "max_seconds": TAPE_RING_SECONDS}
    if first is None or from_ts < first:
        return {
            "cols": ["bucket_ts", *wanted], "rows": [], "ring": ring_info,
            "pointer": {
                "reason": "RANGE_OLDER_THAN_RING" if first is not None else "RING_EMPTY",
                "file": "market_microstructure_1s.jsonl (rotated .N on the analyzer mirror)",
                "requested_from_ts": from_ts, "requested_to_ts": to_ts,
                "segments_head": "/api/research-segments/v3/head",
            },
        }
    stamps, rows = ring.slice(from_ts, to_ts)
    expected = int(math.floor(min(to_ts, last)) - math.ceil(from_ts) + 1) if last is not None else 0
    gaps = []
    prev = None
    for ts in stamps:
        if prev is not None and ts - prev > 1:
            gaps.append([prev + 1, ts - 1])
        prev = ts
    return {
        "cols": ["bucket_ts", *wanted],
        "rows": [[ts, *(row[i] for i in index)] for ts, row in zip(stamps, rows)],
        "ring": ring_info,
        "continuity": {"expected_rows": max(0, expected), "returned_rows": len(stamps),
                       "missing_seconds": max(0, expected - len(stamps)), "gaps": gaps[:50],
                       "stale_rows": sum(1 for row in rows if "fresh" in ring.fields
                                         and row[ring.fields.index("fresh")] is False)},
    }

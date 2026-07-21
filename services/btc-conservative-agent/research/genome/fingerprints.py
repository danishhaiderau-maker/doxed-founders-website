"""DNA fingerprints — join genome layers into market identity vectors."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional


def _finite_number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _first_number(row: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        value = _finite_number(row.get(key))
        if value is not None:
            return value
    return None


def _bucket_adx(adx: Optional[float]) -> str:
    if adx is None:
        return "UNKNOWN_ADX"
    if adx >= 35:
        return "HIGH_ADX"
    if adx >= 22:
        return "MID_ADX"
    return "LOW_ADX"


def _bucket_spread(spread: Optional[float]) -> str:
    if spread is None:
        return "UNKNOWN_SPREAD"
    s = int(spread)
    if s >= 5:
        return "SPREAD5+"
    if s >= 4:
        return "SPREAD4"
    return f"SPREAD{s}"


def market_fingerprint(row: Dict[str, Any]) -> Dict[str, Any]:
    """Single market-genome identity (DNA-first, lane-agnostic)."""
    adx = _first_number(row, "adx", "adx_at_signal", "adx_at_entry")
    spread = _first_number(row, "spread", "directional_spread", "conviction_spread")
    atr = _first_number(row, "atr", "volatility_atr")
    bull_score = _first_number(row, "bull_score")
    bear_score = _first_number(row, "bear_score")
    long_score = _first_number(row, "long_score")
    short_score = _first_number(row, "short_score")
    return {
        "session": row.get("trading_session") or row.get("session") or "UNKNOWN",
        "is_weekend": bool(row.get("is_weekend")),
        "adx_bucket": _bucket_adx(adx),
        "adx": round(adx, 2) if adx is not None else None,
        "atr": round(atr, 4) if atr is not None else None,
        "spread_bucket": _bucket_spread(spread),
        "spread": round(spread, 2) if spread is not None else None,
        "regime": row.get("regime") or "",
        "volatility_percentile": row.get("volatility_percentile"),
        "volume_percentile": row.get("volume_percentile"),
        "funding_window": bool(row.get("funding_window")),
        "structure": _first_number(row, "structure", "structure_score"),
        "momentum": _first_number(row, "momentum"),
        "bull_score": bull_score,
        "bear_score": bear_score,
        "long_score": long_score,
        "short_score": short_score,
        "ai_confidence": row.get("ai_confidence") or row.get("win_prob"),
        "direction": row.get("direction") or "",
    }


def fingerprint_key(fp: Dict[str, Any]) -> str:
    wk = "WEEKEND" if fp.get("is_weekend") else "WEEKDAY"
    return "|".join([
        wk,
        str(fp.get("session") or "?"),
        str(fp.get("adx_bucket") or "?"),
        str(fp.get("spread_bucket") or "?"),
        str(fp.get("regime") or "?"),
    ])


def outcome_fingerprint(
    trade: Dict[str, Any],
    market: Optional[Dict[str, Any]] = None,
    decision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Outcome DNA — why a trade won/lost in market-context terms."""
    mkt = market or {}
    dec = decision or {}
    base = market_fingerprint({**mkt, **dec, **trade})
    pnl = float(trade.get("pnl_usd") or 0)
    return {
        **base,
        "trade_id": trade.get("trade_id"),
        "research_lane": trade.get("research_lane") or "",
        "outcome": "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "FLAT"),
        "pnl_usd": round(pnl, 4),
        "mfe_pct": trade.get("mfe_pct"),
        "mae_pct": trade.get("mae_pct"),
        "capture_pct": trade.get("capture_pct"),
        "exit_reason": trade.get("exit_reason") or "",
        "duration_sec": trade.get("duration_sec"),
        "block_reason": dec.get("block_reason") or "",
        "decision": dec.get("decision") or "",
    }


def index_by_id(rows: List[Dict[str, Any]], key: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        rid = str(row.get(key) or "")
        if rid:
            out[rid] = row
    return out

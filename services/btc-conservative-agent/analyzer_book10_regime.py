"""AMEND2 look-ahead-free regimes from book10 + bfx_trades only (seal 2ac09364eb63).

Binding: ANALYZER-SPEC AMEND2 whole-file sha12 ``659e184f2435``;
param provenance ``4e89e0d0…`` (literal copy of dyn_regime freeze; N=1).
Never opens ``market_segment``. Labels at t use only data with ts <= t.

Canonical epoch floor / segment cut live here only (R4) — consumers import these
constants rather than re-stating the literals.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping, Sequence

REGIME_METHOD_ID = "BOOK10_TRADES_DIRVOL_V1"
DIR_WINDOW_SEC = 900
VOL_WINDOW_SEC = 900
STEP_SEC = 10
DIR_BULL_BP = 12.2
DIR_BEAR_BP = -12.2
VOL_HIGH_RV_BP = 20.0
VOL_MED_RV_BP = 10.0
MIN_RETURNS = 72
MAX_MID_AGE_SEC = 5.0
# Single source for R5 fresh-start floor (also used by analyzer_mirror fresh gate).
EPOCH_FLOOR_TS = 1792263671  # 18 Oct 2026 06:01:11 AEDT
SEGMENT_CUT_TS = 1792830000  # 24 Oct 19:20 AEDT write-stop
AMEND2_SPEC_SHA12 = "659e184f2435"
ANALYZER_COMPLETED_TIP = "e70bac80cd5004f8d95bb4641146b102518f096e"
ANALYZER_COMPLETED_TIP_SHORT = "e70bac80cd50"
BOOK10_FILE = "bfx_book_10l_1s.jsonl"
TRADES_FILE = "bfx_trades.jsonl"
UNKNOWN = "UNKNOWN"
BULL = "bull"
BEAR = "bear"
SIDEWAYS = "sideways"
VOL_HIGH = "vol_high"
VOL_MED = "vol_med"
VOL_LOW = "vol_low"

# Frozen constant block (+ method id) — RK prereg hash N=1. Do not retune.
_PREREG_CONSTANTS = {
    "AMEND2_SPEC_SHA12": AMEND2_SPEC_SHA12,
    "DIR_BEAR_BP": DIR_BEAR_BP,
    "DIR_BULL_BP": DIR_BULL_BP,
    "DIR_WINDOW_SEC": DIR_WINDOW_SEC,
    "EPOCH_FLOOR_TS": EPOCH_FLOOR_TS,
    "MAX_MID_AGE_SEC": MAX_MID_AGE_SEC,
    "MIN_RETURNS": MIN_RETURNS,
    "REGIME_METHOD_ID": REGIME_METHOD_ID,
    "SEGMENT_CUT_TS": SEGMENT_CUT_TS,
    "STEP_SEC": STEP_SEC,
    "VOL_HIGH_RV_BP": VOL_HIGH_RV_BP,
    "VOL_MED_RV_BP": VOL_MED_RV_BP,
    "VOL_WINDOW_SEC": VOL_WINDOW_SEC,
}


def params_prereg_payload() -> str:
    return json.dumps(_PREREG_CONSTANTS, sort_keys=True, separators=(",", ":"))


def params_prereg_hash() -> str:
    return hashlib.sha256(params_prereg_payload().encode("utf-8")).hexdigest()


PARAMS_PREREG_HASH = params_prereg_hash()


def regime_derivation_manifest_block() -> dict[str, Any]:
    """Block written to the analyzer run manifest before scoring (AMEND2 / B1)."""
    return {
        "schema": "regime_derivation_v1",
        "regime_method_id": REGIME_METHOD_ID,
        "amend2_sha12": AMEND2_SPEC_SHA12,
        "params_prereg_hash": PARAMS_PREREG_HASH,
        "params": dict(_PREREG_CONSTANTS),
        "book_file": BOOK10_FILE,
        "trades_file": TRADES_FILE,
        "n_param_sets": 1,
        "look_ahead_free": True,
    }


def rebuild_pin_block(*, run_status: str = "COMPLETED", analyzer_git_rev: str = ANALYZER_COMPLETED_TIP) -> dict[str, Any]:
    """Fly install/serve pin (seal 2ac09364eb63 B1)."""
    return {
        "run_status": run_status,
        "analyzer_git_rev": analyzer_git_rev,
        "amend2_sha12": AMEND2_SPEC_SHA12,
        "regime_method_id": REGIME_METHOD_ID,
        "params_prereg_hash": PARAMS_PREREG_HASH,
    }


def assert_trades_source(path_name: str) -> None:
    if path_name != TRADES_FILE:
        raise ValueError(f"AMEND2 trades source must be {TRADES_FILE}, got {path_name!r}")


def assert_book_source(path_name: str) -> None:
    if path_name != BOOK10_FILE:
        raise ValueError(f"AMEND2 book source must be {BOOK10_FILE}, got {path_name!r}")


def guard_no_market_segment_open(path: str | Any) -> None:
    """Raise if a path names market_segment (H2/C2 mutation target). Wired on open paths."""
    text = str(path)
    if "market_segment" in text:
        raise RuntimeError(f"AMEND2 forbids opening market_segment path: {text}")


def _mid_from_book_row(row: Mapping[str, Any]) -> float | None:
    bid = row.get("bid")
    ask = row.get("ask")
    if bid is None or ask is None:
        # book10 often stores levels as bids/asks arrays
        bids = row.get("bids") or row.get("b")
        asks = row.get("asks") or row.get("a")
        try:
            if bids and asks:
                bid = float(bids[0][0]) if isinstance(bids[0], (list, tuple)) else float(bids[0].get("price"))
                ask = float(asks[0][0]) if isinstance(asks[0], (list, tuple)) else float(asks[0].get("price"))
        except (TypeError, ValueError, IndexError, AttributeError, KeyError):
            return None
    try:
        bid_f = float(bid)
        ask_f = float(ask)
    except (TypeError, ValueError):
        return None
    if bid_f <= 0 or ask_f <= 0:
        return None
    return (bid_f + ask_f) / 2.0


def _row_ts(row: Mapping[str, Any]) -> float | None:
    for key in ("ts", "bucket_ts", "t", "time", "timestamp"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _trade_intensity_in_window(
    t: float,
    trade_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Look-ahead-free trade-print diagnostics from bfx_trades only (plan intensity role)."""
    start = t - float(VOL_WINDOW_SEC)
    n = 0
    notional = 0.0
    for row in trade_rows:
        ts = _row_ts(row)
        if ts is None or ts > t or ts < start:
            continue
        if ts < EPOCH_FLOOR_TS:
            raise ValueError(
                f"AMEND2 pre-epoch trade row ts={ts} — halt (R5); do not filter silently"
            )
        n += 1
        try:
            px = float(row.get("price") or row.get("px") or 0.0)
            qty = abs(float(row.get("amount") or row.get("qty") or row.get("size") or 0.0))
            notional += px * qty
        except (TypeError, ValueError):
            continue
    return {"trade_prints": n, "trade_notional": notional}


def classify_at(
    t: float,
    book_rows: Sequence[Mapping[str, Any]],
    *,
    trade_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Look-ahead-free label at time t from book10 (+ trades diagnostics).

    Only rows with ts <= t enter the window. Pre-epoch contamination → halt (raise).
    ``trade_rows is None`` is forbidden (callers must pass a sequence, possibly empty).
    """
    if trade_rows is None:
        raise ValueError("AMEND2 requires trade_rows sequence (use () if none); refuse discard-open")
    if book_rows is None:
        raise ValueError("AMEND2 requires book_rows; refuse silent UNKNOWN")

    if t < EPOCH_FLOOR_TS:
        return {
            "regime_key": UNKNOWN,
            "direction": UNKNOWN,
            "vol_bucket": UNKNOWN,
            "reason": "PRE_EPOCH",
            "method_id": REGIME_METHOD_ID,
        }

    # Slot mids from book rows with ts <= t only (no look-ahead).
    slots: dict[int, float] = {}
    last_mid_ts: float | None = None
    for row in book_rows:
        ts = _row_ts(row)
        if ts is None or ts > t:
            continue
        if ts < EPOCH_FLOOR_TS:
            raise ValueError(
                f"AMEND2 pre-epoch book row ts={ts} — halt (R5); do not filter silently"
            )
        mid = _mid_from_book_row(row)
        if mid is None:
            continue
        slots[int(ts) // STEP_SEC] = mid
        last_mid_ts = float(ts)

    trade_diag = _trade_intensity_in_window(t, trade_rows)

    if last_mid_ts is None or (t - last_mid_ts) > MAX_MID_AGE_SEC:
        return {
            "regime_key": UNKNOWN,
            "direction": UNKNOWN,
            "vol_bucket": UNKNOWN,
            "reason": "STALE_OR_EMPTY_MID",
            "method_id": REGIME_METHOD_ID,
            **trade_diag,
        }

    end_slot = int(t) // STEP_SEC
    start_slot = end_slot - (DIR_WINDOW_SEC // STEP_SEC)
    returns: list[float] = []
    prev_mid = None
    for slot in range(start_slot + 1, end_slot + 1):
        mid = slots.get(slot)
        if mid is None:
            prev_mid = None
            continue
        if prev_mid is not None and prev_mid > 0 and mid > 0:
            returns.append(math.log(mid / prev_mid))
        prev_mid = mid

    if len(returns) < MIN_RETURNS:
        return {
            "regime_key": UNKNOWN,
            "direction": UNKNOWN,
            "vol_bucket": UNKNOWN,
            "reason": "THIN_COVERAGE",
            "n_returns": len(returns),
            "method_id": REGIME_METHOD_ID,
            **trade_diag,
        }

    drift_bp = sum(returns) * 10_000.0
    rv_bp = math.sqrt(sum(r * r for r in returns)) * 10_000.0

    if drift_bp >= DIR_BULL_BP:
        direction = BULL
    elif drift_bp <= DIR_BEAR_BP:
        direction = BEAR
    else:
        direction = SIDEWAYS

    if rv_bp >= VOL_HIGH_RV_BP:
        vol_bucket = VOL_HIGH
    elif rv_bp >= VOL_MED_RV_BP:
        vol_bucket = VOL_MED
    else:
        vol_bucket = VOL_LOW

    return {
        "regime_key": f"{direction}|{vol_bucket}",
        "direction": direction,
        "vol_bucket": vol_bucket,
        "drift_bp": drift_bp,
        "rv_bp": rv_bp,
        "n_returns": len(returns),
        "method_id": REGIME_METHOD_ID,
        "reason": "OK",
        **trade_diag,
    }


def tags_from_trade_row(
    row: Mapping[str, Any],
    book_rows: Sequence[Mapping[str, Any]] | None = None,
    *,
    trade_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Analyzer regime tags for one trade/fill row (AMEND2 path; never market_segment).

    ``book_rows`` and ``trade_rows`` are required (pass ``()`` only when intentionally empty
    after a successful load). ``None`` means the caller forgot to wire inputs → fail closed.
    """
    if book_rows is None:
        raise ValueError("AMEND2 tags_from_trade_row requires book_rows; refuse silent UNKNOWN")
    if trade_rows is None:
        raise ValueError("AMEND2 tags_from_trade_row requires trade_rows; refuse discard-open")

    ts = None
    for key in ("close_ts", "entry_ts", "ts", "filled_at_ts"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            ts = float(raw)
            break
        except (TypeError, ValueError):
            # ISO timestamps
            try:
                from datetime import datetime

                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
                break
            except (TypeError, ValueError):
                continue
    if ts is None:
        return {
            "regime_key": UNKNOWN,
            "direction": UNKNOWN,
            "vol_bucket": UNKNOWN,
            "day_type": "unknown",
            "method_id": REGIME_METHOD_ID,
            "reason": "NO_TS",
        }
    labels = classify_at(ts, book_rows, trade_rows=trade_rows)
    day_type = "unknown"
    try:
        from datetime import datetime, timezone

        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        day_type = "weekend" if dt.weekday() >= 5 else "weekday"
    except (OverflowError, OSError, ValueError):
        pass
    return {
        **labels,
        "day_type": day_type,
        "adx": "unk",
        "spread_bucket": "unk",
        "session": "unk",
        "volatility": labels.get("vol_bucket"),
        "funding": "unk",
        "liquidity": "unk",
    }

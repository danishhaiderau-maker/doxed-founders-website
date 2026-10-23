"""Record-only per-trade research tags and price paths (owner 2026-10-11).

Never gates, delays or changes a trade: every function is best-effort and
swallows its own errors. Feeds OLD-TILES-DEEP-20261011 item 5 (exit and
delay studies):
  * price_path_10s: [[sec_since_entry, mid], ...] sampled every 10 s while open
  * gs07_with_1h_move: trade side agrees with a >3 bp move over the last hour
  * snapback_same_dir: always None. Its only input was a DISLOC signal; DISLOC (Tiles 6/7)
    was removed 23 Oct 2026 and the bot never wired that input, so prod only ever wrote None.
    The key stays so the trade_research_tags_v1 row shape is unchanged.
  * signal_to_fill_sec: entry fill minus the originating signal time
"""
from __future__ import annotations

import collections
import time
from typing import Any, Iterable, Optional

SAMPLE_SEC = 10.0
PATH_MAX_POINTS = 1080          # 3 h at 10 s
ONE_HOUR_MOVE_BP = 3.0
_HISTORY: collections.deque = collections.deque(maxlen=int(3900 / SAMPLE_SEC))
_last_sample = 0.0


def _f(v) -> Optional[float]:
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _entry_ts(pos: dict) -> Optional[float]:
    for k in ("fill_ts", "entry_fill_ts", "entry_ts", "open_ts", "opened_at_ts", "created_ts"):
        v = _f(pos.get(k))
        if v and v > 1e9:
            return v
    return None


def _signal_ts(pos: dict) -> Optional[float]:
    for k in ("signal_ts", "signal_at_ts", "decision_ts", "intent_ts"):
        v = _f(pos.get(k))
        if v and v > 1e9:
            return v
    return None


def _side(pos: dict) -> int:
    d = str(pos.get("dir") or pos.get("direction") or pos.get("side") or "").upper()
    return 1 if d in ("LONG", "BUY") else -1 if d in ("SHORT", "SELL") else 0


def move_1h_bp(now: float) -> Optional[float]:
    if not _HISTORY:
        return None
    t0, p0 = _HISTORY[0]
    _, p1 = _HISTORY[-1]
    if now - t0 < 3000 or not p0:
        return None
    for t, p in _HISTORY:
        if t >= now - 3600:
            t0, p0 = t, p
            break
    return (p1 - p0) / p0 * 1e4 if p0 else None


def entry_tags(pos: dict, now: float) -> dict:
    side = _side(pos)
    mv = move_1h_bp(now)
    tags: dict[str, Any] = {"schema": "trade_research_tags_v1", "tagged_at_ts": round(now, 3),
                            "move_1h_bp": None if mv is None else round(mv, 2)}
    tags["gs07_with_1h_move"] = (None if mv is None or not side
                                 else bool(abs(mv) > ONE_HOUR_MOVE_BP and (mv > 0) == (side > 0)))
    tags["snapback_same_dir"] = None  # DISLOC input removed 23 Oct 2026 (was always None on prod)
    sig, fill = _signal_ts(pos), _entry_ts(pos)
    tags["signal_to_fill_sec"] = round(fill - sig, 3) if sig and fill and fill >= sig else None
    return tags


def tick(price: Any, positions: Iterable[dict], now: Optional[float] = None) -> None:
    """Call from the position-manager loop. Record-only; never raises."""
    global _last_sample
    try:
        now = time.time() if now is None else now
        px = _f(price)
        if not px or px <= 0 or now - _last_sample < SAMPLE_SEC:
            return
        _last_sample = now
        _HISTORY.append((now, px))
        for pos in list(positions or []):
            if not isinstance(pos, dict):
                continue
            if "research_tags" not in pos:
                pos["research_tags"] = entry_tags(pos, now)
            ent = _entry_ts(pos)
            path = pos.setdefault("price_path_10s", [])
            if isinstance(path, list) and len(path) < PATH_MAX_POINTS:
                path.append([round(now - ent, 1) if ent else None, round(px, 2)])
    except Exception:  # noqa: BLE001 - research logging must never affect trading
        return


def outcome_fields(pos: dict) -> dict:
    try:
        tags = dict(pos.get("research_tags") or {})
        return {"research_tags": tags or None,
                "signal_to_fill_sec": tags.get("signal_to_fill_sec"),
                "price_path_10s": list(pos.get("price_path_10s") or [])[:PATH_MAX_POINTS]}
    except Exception:  # noqa: BLE001
        return {}

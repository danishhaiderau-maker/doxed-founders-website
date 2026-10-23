"""Two-tile live gate (HM gate N1-N3 / G6; RK 24 Oct: its own PR). ONE shared helper for every live tile (R4): no per-tile code.

Pure decision logic, no I/O. bot.py feeds it the live-copy chains (executor reports), the relay's exchange mirror, the
persisted per-trade decisions and the per-tile switch rows; the first ENTRY event of a trade is admitted only when every
check passes. A refused signal stays paper-only (the paper tile is never touched) and is logged with the reason.

1. one live position account-wide (``LIVE_SKIPPED_MUTEX``): no other open / pending / in-flight live trade on ANY tile;
2. tile key: every live tile has a stable key (its trade-id prefix) and a stable 7-bit code carried in the Bitfinex cid
   (``encode_cid`` / ``decode_cid``; the relay mirror is ``live-tile-gate.ts``); an unknown tile is refused;
3. margin caps: per-tile cap (default $0.25) and a total cap (default = sum of the ARMED tiles' caps), checked every entry;
4. at most ``MAX_ENTRIES_PER_DAY`` (20) live entries per tile per **UTC** day (HM G6 / AMEND3 "per UTC day");
5. net position 0 before every new entry, from a fresh exchange mirror (no position, no resting non-reduce-only order);
6. (guardian side) ``live_guardian.evaluate`` attributes violations with ``chain_tile_key`` for every live tile.

Caps are in USD **margin** (the system's $0.25 per entry is margin at 100x = $25 notional, see live_copy_control
MAX_MARGIN_USD); the cap compares like with like. No fees anywhere (R7).
"""
from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any, Iterable, Mapping

# Stable tile key (= trade-id prefix) -> cid code (1..127). NEVER renumber or reuse a code; a new tile takes the next free one.
# Display tile numbers are NOT stable (derived from ACTIVE_TILE_ORDER), so they are never used as the key.
TILE_KEY_CODES: dict = {
    "rnd": 1,   # FAMILY_RANDOM_CONTROL_TAKER_90
    "gs1": 2,   # FAMILY_GS01_XV_PREMIUM_ATR_TP (GS-01)
    "gb1": 3,   # FAMILY_GSB1_CVD_DIV_REGIME
    "gb2": 4,   # FAMILY_GSB2_REGIME_SWITCHER
    "gs6": 5,   # FAMILY_GS06_COMMITTED_FADE_ATR_TP
}
# Retired tiles keep their codes reserved forever (never reissued): dsl=6, dsf=7.
RESERVED_TILE_KEY_CODES = {"dsl": 6, "dsf": 7}

# cid layout (positive int32): bits 28-30 = 0b101 marker, bits 21-27 = tile code, bits 0-20 = hash.
CID_MARKER = 0b101
CID_MARKER_SHIFT = 28
CID_CODE_SHIFT = 21
CID_HASH_MASK = (1 << CID_CODE_SHIFT) - 1

DEFAULT_TILE_MARGIN_CAP_USD = 0.25
MAX_ENTRIES_PER_DAY = 20
DAY_BASIS = "UTC"
PENDING_EXPOSURE_SEC = 300.0      # same bound the guardian uses for a sent-but-unfilled order
INFLIGHT_APPROVAL_SEC = 180.0     # relay placement bound (FLY_APPROVAL_PLACEMENT_MAX_AGE_SEC): approved, not yet reported
MIRROR_MAX_AGE_SEC = 20.0

SKIP_MUTEX = "LIVE_SKIPPED_MUTEX"
DENY_TILE_KEY_UNKNOWN = "LIVE_TILE_KEY_UNKNOWN"
DENY_TILE_CAP = "LIVE_TILE_MARGIN_CAP"
DENY_TOTAL_CAP = "LIVE_TOTAL_MARGIN_CAP"
DENY_DAILY_LIMIT = "LIVE_DAILY_ENTRY_LIMIT"
DENY_NOT_FLAT = "ACCOUNT_NOT_FLAT"
DENY_MIRROR_BLIND = "EXCHANGE_MIRROR_BLIND"
DENY_GATE_ERROR = "LIVE_TILE_GATE_ERROR"


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _registry():
    from combo_pathway_config import ACTIVE_TILE_REGISTRY
    return ACTIVE_TILE_REGISTRY


def tile_key_for_lane(lane: str | None, registry: Mapping[str, Mapping] | None = None) -> str | None:
    """Stable tile key (trade-id prefix) for an active lane with a cid code; None for anything else (fail closed)."""
    reg = _registry() if registry is None else registry
    prefix = str(((reg.get(str(lane or "").upper()) or {}).get("id_prefix")) or "").lower()
    return prefix if prefix in TILE_KEY_CODES else None


def tile_key_for_trade_id(trade_id: str | None) -> str | None:
    tid = str(trade_id or "").strip().lower()
    for wrap in ("adopt:", "relink:"):
        if tid.startswith(wrap):
            tid = tid[len(wrap):]
    head, sep, rest = tid.partition("-")
    return head if sep and rest and head in TILE_KEY_CODES else None


def chain_tile_key(chain: Mapping[str, Any], registry: Mapping[str, Mapping] | None = None) -> str | None:
    """Tile of a live-copy chain: its lane, else its correlation/trade id prefix."""
    return (tile_key_for_lane(chain.get("lane"), registry) if chain.get("lane") else None) \
        or tile_key_for_trade_id(chain.get("correlation_id") or chain.get("trade_id"))


def encode_cid(tile_key: str, hash_int: int) -> int:
    code = TILE_KEY_CODES[tile_key]
    return (CID_MARKER << CID_MARKER_SHIFT) | (code << CID_CODE_SHIFT) | (int(hash_int) & CID_HASH_MASK)


def decode_cid(cid) -> str | None:
    """Tile key carried in a tile-keyed cid; None for legacy / foreign / stop cids (they are attributed via the ledger)."""
    try:
        c = int(cid)
    except (TypeError, ValueError):
        return None
    if c <= 0 or c > 0x7FFFFFFF or (c >> CID_MARKER_SHIFT) != CID_MARKER:
        return None
    code = (c >> CID_CODE_SHIFT) & 0x7F
    return next((k for k, v in TILE_KEY_CODES.items() if v == code), None)


def utc_day(ts: float) -> str:
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).strftime("%Y-%m-%d")


def load_caps(environ: Mapping[str, str] | None = None) -> dict:
    """Config: LIVE_TILE_MARGIN_CAPS_USD_JSON ({tile_key: usd}), LIVE_TOTAL_MARGIN_CAP_USD (optional, can only LOWER the
    armed-sum default), LIVE_TILE_MAX_ENTRIES_PER_DAY. Bad values fall back to the safe defaults (never raise)."""
    env = os.environ if environ is None else environ
    per_tile: dict = {}
    try:
        raw = json.loads(env.get("LIVE_TILE_MARGIN_CAPS_USD_JSON") or "{}")
        if isinstance(raw, dict):
            for k, v in raw.items():
                x = _f(v)
                if str(k).lower() in TILE_KEY_CODES and x is not None and x >= 0:
                    per_tile[str(k).lower()] = x
    except ValueError:
        per_tile = {}
    total = _f(env.get("LIVE_TOTAL_MARGIN_CAP_USD"))
    try:
        max_day = int(env.get("LIVE_TILE_MAX_ENTRIES_PER_DAY") or MAX_ENTRIES_PER_DAY)
    except ValueError:
        max_day = MAX_ENTRIES_PER_DAY
    return {"per_tile_usd": per_tile, "total_usd": total if total is not None and total >= 0 else None,
            "max_entries_per_day": max(0, min(max_day, MAX_ENTRIES_PER_DAY))}


def tile_cap_usd(tile_key: str, caps: Mapping[str, Any]) -> float:
    return float((caps.get("per_tile_usd") or {}).get(tile_key, DEFAULT_TILE_MARGIN_CAP_USD))


def total_cap_usd(armed_tile_keys: Iterable[str], caps: Mapping[str, Any]) -> float:
    s = sum(tile_cap_usd(k, caps) for k in set(armed_tile_keys) if k in TILE_KEY_CODES)
    explicit = caps.get("total_usd")
    return min(s, float(explicit)) if explicit is not None else s


def live_exposures(chains: Iterable[Mapping[str, Any]], decisions: Iterable[tuple], now: float,
                   exclude_trade_id: str | None = None) -> list:
    """Other live trades that are open, pending on the exchange, or approved and still in flight to the relay."""
    out, seen = [], set()
    ex = str(exclude_trade_id or "")
    by_cid: dict = {}
    for c in chains or []:
        cid = str(c.get("correlation_id") or "")
        if cid:
            by_cid.setdefault(cid, []).append(c)
        if cid == ex or c.get("closed"):
            continue
        filled = c.get("fill_at_ts")
        sent = _f(c.get("order_sent_at_ts"))
        pending = sent is not None and now - sent < PENDING_EXPOSURE_SEC
        if filled or pending:
            out.append({"trade_id": cid, "tile": chain_tile_key(c), "state": "OPEN" if filled else "PENDING"})
            seen.add(cid)
    for tid, row in decisions or []:
        tid = str(tid)
        if tid == ex or tid in seen or not (row or {}).get("approved"):
            continue
        ats = _f((row or {}).get("approval_ts"))
        if ats is None or now - ats >= INFLIGHT_APPROVAL_SEC:
            continue
        rel = by_cid.get(tid) or []
        if any(c.get("closed") or (c.get("executor_decision") or {}).get("decision") == "SKIPPED"
               or any(e.get("type") in ("INTENT_SKIPPED", "INTENT_REJECTED", "ORDER_REJECTED") for e in c.get("errors") or [])
               for c in rel):
            continue
        out.append({"trade_id": tid, "tile": tile_key_for_trade_id(tid) or tile_key_for_lane((row or {}).get("lane")),
                    "state": "IN_FLIGHT"})
    return out


def flat_check(mirror: Mapping[str, Any] | None) -> list:
    """Net position 0 from a fresh mirror: no position and no resting entry (non-reduce-only) order."""
    m = mirror if isinstance(mirror, Mapping) else None
    if m is None or m.get("ok") is not True:
        return [DENY_MIRROR_BLIND]
    age = _f(m.get("read_age_s"))
    if age is not None and age > MIRROR_MAX_AGE_SEC:
        return [DENY_MIRROR_BLIND]
    reasons = []
    positions = [p for p in (m.get("positions") or []) if (_f((p or {}).get("amount")) or 0.0) != 0.0 or "amount" not in (p or {})]
    if positions:
        reasons.append(f"{DENY_NOT_FLAT}:POSITION")
    entries = [o for o in (m.get("open_orders") or []) if not (o or {}).get("reduce_only") and "STOP" not in str((o or {}).get("type") or "").upper()]
    if entries:
        reasons.append(f"{DENY_NOT_FLAT}:ENTRY_ORDER")
    return reasons


def admit_entry(*, lane: str, trade_id: str, now: float, chains: Iterable[Mapping[str, Any]], mirror: Mapping[str, Any] | None,
                decisions: Iterable[tuple], armed_lanes: Iterable[str], entry_margin_usd: float,
                caps: Mapping[str, Any] | None = None, registry: Mapping[str, Mapping] | None = None) -> tuple[bool, list, dict]:
    """(ok, reasons, info) for the FIRST entry event of a live trade. Pure; fail closed on any error.

    ``decisions``: iterable of (trade_id, decision_row) (TradeDecisions rows; persisted, so the daily count and the in-flight
    mutex survive restarts). ``armed_lanes``: lanes whose live switch is ON now (the total cap is their caps' sum)."""
    try:
        caps = load_caps() if caps is None else caps
        key = tile_key_for_lane(lane, registry)
        info: dict = {"tile_key": key, "day_basis": DAY_BASIS}
        if key is None or tile_key_for_trade_id(trade_id) != key:
            return False, [DENY_TILE_KEY_UNKNOWN], info
        reasons: list = []
        rows = list(decisions or [])
        chains = list(chains or [])
        # 1. mutex: one live position account-wide
        others = live_exposures(chains, rows, now, exclude_trade_id=trade_id)
        info["exposures"] = others[:5]
        if others:
            reasons.append(SKIP_MUTEX)
        # 5. net = 0 before this entry
        reasons.extend(flat_check(mirror))
        # 3. margin caps
        margin = float(entry_margin_usd)
        armed = {k for k in (tile_key_for_lane(l, registry) for l in armed_lanes or []) if k}
        armed.add(key)   # the tile being entered is armed by definition (its switch passed entry_gate)
        tcap, total = tile_cap_usd(key, caps), total_cap_usd(armed, caps)
        open_margin = margin * len(others)
        info.update(entry_margin_usd=margin, tile_cap_usd=tcap, total_cap_usd=total, open_margin_usd=open_margin)
        if margin > tcap + 1e-9:
            reasons.append(DENY_TILE_CAP)
        if open_margin + margin > total + 1e-9:
            reasons.append(DENY_TOTAL_CAP)
        # 4. max entries per tile per UTC day (the trade itself is not yet decided, so it is never in this count)
        day = utc_day(now)
        n = sum(1 for tid, r in rows if (r or {}).get("approved") and str(tid) != str(trade_id)
                and (tile_key_for_trade_id(tid) or tile_key_for_lane((r or {}).get("lane"), registry)) == key
                and _f((r or {}).get("approval_ts")) is not None and utc_day(float(r["approval_ts"])) == day)
        info.update(entries_today=n, max_entries_per_day=int(caps.get("max_entries_per_day", MAX_ENTRIES_PER_DAY)), utc_day=day)
        if n >= info["max_entries_per_day"]:
            reasons.append(DENY_DAILY_LIMIT)
        return (not reasons), reasons, info
    except Exception as exc:  # noqa: BLE001 - any error refuses the live entry (paper unaffected)
        return False, [f"{DENY_GATE_ERROR}:{type(exc).__name__}"], {}

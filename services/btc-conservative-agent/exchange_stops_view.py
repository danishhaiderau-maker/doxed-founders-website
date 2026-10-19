"""Read-only view of the protective stop(s) on the exchange for the open position (HM 19 Oct, arming blocker).

Pure function over the relay's exchange mirror (relay_exchange_mirror_v1: a private read of the live account by the executor).
A position is ``protected`` only when a reduce-only STOP order on the opposite side covers its full amount. No I/O.
"""
from __future__ import annotations

from typing import Any, Mapping

SCHEMA = "exchange_stops_v1"
MAX_MIRROR_AGE_SEC = 20.0


def _f(v):
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def build(mirror: Mapping[str, Any] | None, account: str | None = None, fly_audit: Mapping[str, Any] | None = None,
          relay_cap: Mapping[str, Any] | None = None) -> dict:
    m = mirror if isinstance(mirror, Mapping) else {}
    age = _f(m.get("read_age_s"))
    out = {"schema": SCHEMA, "account": account, "mirror_ok": bool(m.get("ok")), "mirror_error": m.get("error"),
           "read_at": m.get("read_at"), "read_age_s": age, "fresh": bool(m.get("ok")) and age is not None and age <= MAX_MIRROR_AGE_SEC,
           "balance_usd": _f((m.get("balance") or {}).get("derivatives_available_usd")), "positions": [], "stops": [],
           "orphans": list(m.get("orphans") or []), "open_orders": len(m.get("open_orders") or []),
           "relay_cap": dict(relay_cap) if isinstance(relay_cap, Mapping) else None,
           "fly_audit": ({k: fly_audit.get(k) for k in ("checked_ts", "authoritative", "fresh", "flat", "open_order_count",
                                                         "open_position_count", "orphan_order_ids", "orphan_position_ids", "error")}
                         if isinstance(fly_audit, Mapping) else None)}
    if not m.get("ok"):
        out["verdict"] = "UNKNOWN_MIRROR_NOT_OK"
        return out
    stale = not out["fresh"]
    stops = [o for o in (m.get("stops") or []) if isinstance(o, Mapping)]
    out["stops"] = [{k: o.get(k) for k in ("id", "symbol", "type", "amount", "amount_orig", "price", "status", "reduce_only", "oco", "cid")}
                    for o in stops]
    all_protected = True
    for p in m.get("positions") or []:
        amt = _f(p.get("amount")) or 0.0
        side = str(p.get("side") or ("LONG" if amt > 0 else "SHORT")).upper()
        want_sign = -1.0 if side == "LONG" else 1.0       # a stop closing a LONG sells (negative amount)
        covering = [o for o in stops if o.get("symbol") == p.get("symbol") and (_f(o.get("amount")) or 0) * want_sign > 0
                    and o.get("reduce_only") is True]
        covered = sum(abs(_f(o.get("amount")) or 0.0) for o in covering)
        prot = covered + 1e-9 >= abs(amt) > 0
        all_protected = all_protected and prot
        out["positions"].append({"symbol": p.get("symbol"), "side": side, "amount": amt, "position_amount_abs": abs(amt),
                                 "covered_vs_position": f"{round(covered, 8)} / {abs(amt)}", "base_price": _f(p.get("base_price")),
                                 "pnl_usd": _f(p.get("pnl_usd")), "stop_ids": [o.get("id") for o in covering],
                                 "stop_prices": [_f(o.get("price")) for o in covering], "stop_amount_covered": round(covered, 8),
                                 "protected": prot})
    if stale and not out["positions"]:
        out["verdict"] = "STALE_READ"
    elif not out["positions"]:
        out["verdict"] = "FLAT_WITH_STOP_ORPHANS" if out["stops"] else "FLAT_NO_STOPS"
    else:
        out["verdict"] = ("STALE_READ_NOT_PROTECTED" if stale else "PROTECTED") if all_protected else "UNPROTECTED_POSITION"
    out["note"] = "read-only; stops come from the executor's private exchange read (relay mirror), cross-checked against Fly's own cached audit counts"
    return out

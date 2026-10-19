"""Read-only /api/monitor/reconcile payload (Strategist dashboard spec S1), Fly side only.

fly_n is what the Fly process holds: the epoch's closed rows per tile, tiles 1-5 from the bot's in-memory trades list
and tiles 6/7 (DISLOC) from their on-disk paper ledgers (read through the 10 s ledger cache). Tiles 6/7 are counted, but
their fly_fresh_n is 0 by design (no paper_fill-priced rows yet). Fly can count its own closed rows per tile and read the archive restore-test receipt. The mirror and the
analyzer live on other machines, so their counts are reported as null with an explicit reason: this route
never invents parity and never reaches out to another host. ok is None (unknown) until a source reports.

Per-lane push (PR reconcile-per-lane): the laptop watcher attaches ``ledger_reconcile`` (ledger_reconcile_push_v1:
per lane mirror_n, analyzer_n, missing_ids_n, pnl_diff_usd, plus epoch_id and generated_at) to its existing
POST /api/system-health/report. :func:`build` fills mirror_n / analyzer_n / missing_ids / pnl_diff_usd ONLY when that
block's epoch_id equals Fly's data epoch and it is fresh (generated <= PUSH_MAX_AGE_SEC ago and received <=
PUSH_MAX_RECEIVE_AGE_SEC ago). Otherwise the sources stay NOT_REPORTED_TO_FLY and ``sources_reason`` says why.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

SCHEMA = "monitor_reconcile_v1"
TOLERANCE = {"ids": "identical", "pnl_usd": 0.01}
PUSH_SCHEMA = "ledger_reconcile_push_v1"
PUSH_MAX_AGE_SEC = 2 * 3600          # analyzer reconciliation runs every cycle (~30 min); 2 h = several missed cycles
PUSH_MAX_RECEIVE_AGE_SEC = 30 * 60   # watcher pushes every few minutes
MAX_PUSH_LANES = 32
UNAVAILABLE = "NOT_REPORTED_TO_FLY"


def _num(value: Any):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _count(value: Any):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return int(value) if float(value).is_integer() else None


def _parse_ts(value: Any):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).timestamp()


def sanitize_push(raw: Any) -> dict | None:
    """Untrusted laptop block -> bounded, typed per-lane numbers (None when not a ledger_reconcile_push_v1 block)."""
    if not isinstance(raw, Mapping) or raw.get("schema") != PUSH_SCHEMA:
        return None
    lanes = raw.get("per_lane") if isinstance(raw.get("per_lane"), Mapping) else {}
    per_lane = {}
    for lane, v in list(lanes.items())[:MAX_PUSH_LANES]:
        if not isinstance(lane, str) or not isinstance(v, Mapping):
            continue
        diff = _num(v.get("pnl_diff_usd"))
        per_lane[lane[:64]] = {"mirror_n": _count(v.get("mirror_n")), "analyzer_n": _count(v.get("analyzer_n")),
                               "missing_ids_n": _count(v.get("missing_ids_n")),
                               "pnl_diff_usd": round(diff, 6) if diff is not None and math.isfinite(diff) else None,
                               "bounded": v.get("bounded") if isinstance(v.get("bounded"), bool) else None}
    epoch = raw.get("epoch_id")
    generated = raw.get("generated_at")
    return {"schema": PUSH_SCHEMA, "epoch_id": epoch[:64] if isinstance(epoch, str) and epoch else None,
            "generated_at": generated[:40] if isinstance(generated, str) else None,
            "compared": raw.get("compared") is True,
            "bounded": raw.get("bounded") if isinstance(raw.get("bounded"), bool) else None, "per_lane": per_lane}


def push_status(pushed: Mapping[str, Any] | None, epoch_id: Any, now: float) -> tuple[bool, str]:
    """(usable, reason). Usable only for the same epoch and fresh data; the reason is shown when not usable."""
    block = (pushed or {}).get("block") if isinstance(pushed, Mapping) else None
    if not isinstance(block, Mapping):
        return False, "NO_LAPTOP_PUSH"
    if not block.get("compared"):
        return False, "LAPTOP_COULD_NOT_COMPARE"
    if not epoch_id or block.get("epoch_id") != epoch_id:
        return False, f"EPOCH_MISMATCH (laptop {block.get('epoch_id')}, fly {epoch_id})"
    generated = _parse_ts(block.get("generated_at"))
    if generated is None:
        return False, "GENERATED_AT_MISSING"
    if now - generated > PUSH_MAX_AGE_SEC:
        return False, f"STALE (generated {int(now - generated)} s ago > {PUSH_MAX_AGE_SEC} s)"
    received = _num(pushed.get("received_ts"))
    if received is None or now - received > PUSH_MAX_RECEIVE_AGE_SEC:
        return False, f"STALE (last push {int(now - received) if received is not None else 'never'} s ago)"
    return True, "OK"


def build(by_lane: Mapping[str, Iterable[Mapping[str, Any]]], short_names: Mapping[str, str],
          receipt: Mapping[str, Any] | None, common: Mapping[str, Any], *,
          pushed: Mapping[str, Any] | None = None, now: float | None = None) -> dict:
    per_tile = []
    for lane, rows in by_lane.items():
        closed = [r for r in rows if r.get("status") == "CLOSED"]
        net = sum(_num((r.get("pnl") or {}).get("net_usd")) or 0.0 for r in closed)
        per_tile.append({
            "lane": lane, "tile": short_names.get(lane), "fly_n": len(closed),
            "fly_fresh_n": sum(1 for r in closed if (r.get("pricing") or {}).get("fresh_eligible") is True
                                and not r.get("identity_quarantined")),
            "fly_net_pnl_usd": round(net, 6), "mirror_n": None, "analyzer_n": None,
            "missing_ids": None, "missing_ids_n": None, "pnl_diff_usd": None, "bounded": None, "ok": None,
        })
    epoch_id = common.get("data_epoch_id")
    usable, reason = push_status(pushed, epoch_id, datetime.now(timezone.utc).timestamp() if now is None else now)
    block = (pushed or {}).get("block") or {}
    if usable:
        lanes = block.get("per_lane") or {}
        for tile in per_tile:
            v = lanes.get(tile["lane"])
            if not v:
                continue
            missing, diff = v.get("missing_ids_n"), v.get("pnl_diff_usd")
            bounded = v.get("bounded") if v.get("bounded") is not None else block.get("bounded")
            tile.update(mirror_n=v.get("mirror_n"), analyzer_n=v.get("analyzer_n"), missing_ids=missing,
                        missing_ids_n=missing, pnl_diff_usd=diff, bounded=bounded)
            # ok only when the laptop's comparison was bounded (Fly's listed closes reach the mirror head); while
            # the mirror lags the counts are a lower bound, so ok stays null instead of reading false
            if bounded is True and None not in (missing, diff, v.get("mirror_n"), v.get("analyzer_n")):
                tile["ok"] = (missing == 0 and abs(diff) <= TOLERANCE["pnl_usd"]
                              and v.get("mirror_n") == v.get("analyzer_n"))
    source = "LAPTOP_PUSH" if usable else UNAVAILABLE
    return {"schema": SCHEMA, **common, "epoch_id": epoch_id,
            "sources": {"fly": "OK", "mirror": source, "analyzer": source},
            "sources_reason": None if usable else reason,
            "laptop_push": {"epoch_id": block.get("epoch_id"), "generated_at": block.get("generated_at"),
                            "bounded": block.get("bounded"),
                            "received_ts": (pushed or {}).get("received_ts")} if block else None,
            "per_tile": per_tile, "tolerance": dict(TOLERANCE),
            "stream_gaps": None,
            "fly_n_basis": "FLY_PROCESS_CLOSED_ROWS (tiles 1-5: in-memory trades list; tiles 6/7: on-disk DISLOC ledgers via the 10 s cache; fly_fresh_n is 0 for 6/7 by design)",
            "note": ("mirror/analyzer per-lane numbers from the laptop watcher push (same epoch, fresh); ok = no missing "
                     "ids, PnL diff <= $0.01 and mirror_n == analyzer_n, only when bounded (else null)" if usable else
                     "Fly-side counts only; mirror/analyzer parity needs those hosts to report (ok stays null, never assumed).")}

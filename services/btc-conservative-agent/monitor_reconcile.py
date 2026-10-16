"""Read-only /api/monitor/reconcile payload (Strategist dashboard spec S1), Fly side only.

Fly can count its own closed rows per tile and read the archive restore-test receipt. The mirror and the
analyzer live on other machines, so their counts are reported as null with an explicit reason: this route
never invents parity and never reaches out to another host. ok is None (unknown) until a source reports.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

SCHEMA = "monitor_reconcile_v1"
TOLERANCE = {"ids": "identical", "pnl_usd": 0.01}


def _num(value: Any):
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def archive_restore(receipt: Mapping[str, Any] | None) -> dict:
    rt = (receipt or {}).get("restore_test") if isinstance(receipt, Mapping) else None
    if not isinstance(rt, Mapping):
        return {"tests_total": None, "tests_failed": None, "receipt_present": bool(receipt)}
    checked, bad = _num(rt.get("checked")), _num(rt.get("bad"))
    return {"tests_total": int(checked) if checked is not None else None,
            "tests_failed": int(bad) if bad is not None else None,
            "receipt_present": True, "restore_test_ts": rt.get("ts")}


def build(by_lane: Mapping[str, Iterable[Mapping[str, Any]]], short_names: Mapping[str, str],
          receipt: Mapping[str, Any] | None, common: Mapping[str, Any]) -> dict:
    per_tile = []
    for lane, rows in by_lane.items():
        closed = [r for r in rows if r.get("status") == "CLOSED"]
        net = sum(_num((r.get("pnl") or {}).get("net_usd")) or 0.0 for r in closed)
        per_tile.append({
            "lane": lane, "tile": short_names.get(lane), "fly_n": len(closed),
            "fly_fresh_n": sum(1 for r in closed if (r.get("pricing") or {}).get("fresh_eligible") is True),
            "fly_net_pnl_usd": round(net, 6), "mirror_n": None, "analyzer_n": None,
            "missing_ids": None, "pnl_diff_usd": None, "ok": None,
        })
    unavailable = "NOT_REPORTED_TO_FLY"
    return {"schema": SCHEMA, **common, "epoch_id": common.get("data_epoch_id"),
            "sources": {"fly": "OK", "mirror": unavailable, "analyzer": unavailable},
            "per_tile": per_tile, "tolerance": dict(TOLERANCE),
            "archive_restore": archive_restore(receipt), "stream_gaps": None,
            "note": "Fly-side counts only; mirror/analyzer parity needs those hosts to report (ok stays null, never assumed)."}

"""Read-only /api/monitor/reconcile payload (Strategist dashboard spec S1), Fly side only.

fly_n is what the Fly process holds: the epoch's closed rows per tile 1-5 from the bot's in-memory trades list
(Tiles 6/7 DISLOC / DISLOC-FOI were removed 23 Oct 2026, R9). Fly can count its own closed rows per tile and read the archive restore-test receipt. The mirror and the
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


def build(by_lane: Mapping[str, Iterable[Mapping[str, Any]]], short_names: Mapping[str, str],
          receipt: Mapping[str, Any] | None, common: Mapping[str, Any]) -> dict:
    per_tile = []
    for lane, rows in by_lane.items():
        closed = [r for r in rows if r.get("status") == "CLOSED"]
        net = sum(_num((r.get("pnl") or {}).get("net_usd")) or 0.0 for r in closed)
        per_tile.append({
            "lane": lane, "tile": short_names.get(lane), "fly_n": len(closed),
            "fly_fresh_n": sum(1 for r in closed if (r.get("pricing") or {}).get("fresh_eligible") is True
                                and not r.get("identity_quarantined")),
            "fly_net_pnl_usd": round(net, 6), "mirror_n": None, "analyzer_n": None,
            "missing_ids": None, "pnl_diff_usd": None, "ok": None,
        })
    unavailable = "NOT_REPORTED_TO_FLY"
    return {"schema": SCHEMA, **common, "epoch_id": common.get("data_epoch_id"),
            "sources": {"fly": "OK", "mirror": unavailable, "analyzer": unavailable},
            "per_tile": per_tile, "tolerance": dict(TOLERANCE),
            "stream_gaps": None,
            "fly_n_basis": "FLY_PROCESS_CLOSED_ROWS (tiles 1-5: in-memory trades list)",
            "note": "Fly-side counts only; mirror/analyzer parity needs those hosts to report (ok stays null, never assumed)."}

"""Read-only /api/monitor/fill-provenance payload: how every closed row was priced, per tile.

Built only from the pricing blocks already on the tile rows plus the paper IOC shadow file (NO_FILL / REJECT
entries that never became trades). No raw books, no strategy fields, no writes, no calls to other hosts.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

import paper_fill

SCHEMA = "monitor_fill_provenance_v1"
MISSING_LIST_CAP = 20
FORCED_STATUSES = ("FORCED_NO_BOOK", "PAPER_FILL_FORCED")


def _present(snapshot: Any) -> bool:
    return isinstance(snapshot, Mapping) and bool(snapshot.get("a") or snapshot.get("b"))


def _shadow_counts(shadow_rows: Iterable[Mapping[str, Any]], since_ts: float | None) -> dict:
    out: dict = {}
    for r in shadow_rows or ():
        if not isinstance(r, Mapping) or r.get("event") != "PAPER_SHADOW_NO_FILL":
            continue
        ts = paper_fill.as_number(r.get("ts"))
        if since_ts and ts is not None and ts < since_ts:
            continue
        lane = str(r.get("research_lane") or "UNKNOWN")
        status = str(r.get("status") or "")
        bucket = out.setdefault(lane, {"no_fill": 0, "reject": 0, "by_status": {}})
        key = "reject" if status.startswith("REJECT") else "no_fill"
        bucket[key] += 1
        bucket["by_status"][status or "UNKNOWN"] = bucket["by_status"].get(status or "UNKNOWN", 0) + 1
    return out


def build(by_lane: Mapping[str, Iterable[Mapping[str, Any]]], short_names: Mapping[str, str],
          shadow_rows: Iterable[Mapping[str, Any]], epoch: Mapping[str, Any] | None,
          common: Mapping[str, Any], shadow_truncated: bool | None = None) -> dict:
    since_ts = paper_fill.as_number((epoch or {}).get("epoch_ts"))
    shadow = _shadow_counts(shadow_rows, since_ts)
    per_tile = []
    for lane, rows in by_lane.items():
        closed = [r for r in rows if r.get("status") == "CLOSED"]
        models, versions, delay_sources = Counter(), Counter(), Counter()
        forced = Counter()
        fresh = not_fresh = legacy = 0
        cov = {"entry_signal": 0, "entry_fill": 0, "exit_fill": 0}
        missing: list = []
        v1_rows = 0
        for r in closed:
            p = r.get("pricing") or {}
            models[str(p.get("fill_model") or "UNLABELLED")] += 1
            versions[str(p.get("fill_model_version")) if p.get("fill_model_version") is not None else "none"] += 1
            delay_sources[str(p.get("delay_source") or "none")] += 1
            status = str(p.get("exit_status") or "")
            if status in FORCED_STATUSES or paper_fill.is_excluded(p.get("fresh_data_excluded")):
                forced[status or "EXCLUDED"] += 1
            if p.get("fresh_eligible") is True:
                fresh += 1
            else:
                not_fresh += 1
            version = paper_fill.as_number(p.get("fill_model_version"))
            if version is None or version < 1:
                legacy += 1
                continue
            v1_rows += 1
            legs = {"entry_signal": _present(p.get("depth_snapshot_signal")),
                    "entry_fill": _present(p.get("depth_snapshot_fill")),
                    "exit_fill": _present(p.get("exit_depth_snapshot_fill"))}
            for k, ok in legs.items():
                cov[k] += int(ok)
            lost = [k for k, ok in legs.items() if not ok]
            if lost and len(missing) < MISSING_LIST_CAP:
                missing.append({"trade_id": r.get("trade_id"), "legs_without_snapshot": lost})
        sh = shadow.get(lane) or {"no_fill": 0, "reject": 0, "by_status": {}}
        per_tile.append({
            "lane": lane, "tile": short_names.get(lane), "closed": len(closed),
            "fresh_eligible": fresh, "not_fresh": not_fresh, "legacy_rows": legacy, "v1_rows": v1_rows,
            "fill_model_counts": dict(models), "fill_model_version_counts": dict(versions),
            "delay_source_counts": dict(delay_sources), "forced_counts": dict(forced),
            "shadow": sh,
            "snapshot_coverage": {**cov, "of_v1_rows": v1_rows,
                                  "legs_without_snapshot": sum(v1_rows - v for v in cov.values())},
            "rows_with_missing_snapshots": missing,
        })
    return {"schema": SCHEMA, **common, "epoch": {
                "data_epoch_id": common.get("data_epoch_id"), "fresh_epoch_ts": (epoch or {}).get("epoch_ts"),
                "fresh_epoch_stamped": bool(since_ts), "shadow_rows_counted_since": since_ts},
            # True when the shadow file is larger than the tail that was read: NO_FILL/REJECT counts are then a
            # lower bound for the window. None = not known (caller did not say).
            "shadow_truncated": shadow_truncated,
            "per_tile": per_tile,
            "notes": ["NO_FILL and REJECT entries are shadow rows, never trades and never in closed counts.",
                      "legs_without_snapshot counts v1 rows only; legacy rows are listed under legacy_rows."]}

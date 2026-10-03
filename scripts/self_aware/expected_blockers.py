"""Declared expected blockers: known gaps whose fix exists but waits for a deploy window.

Each blocker names its fix and an ETA. Matching findings are annotated (never downgraded),
and one summary finding reports the set: AMBER while every ETA is pending, RED once any
ETA passes so a stale declaration cannot silently become permanent.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .facts import iso, parse_ts

REGISTRY = Path(__file__).with_name("expected_blockers.json")
REQUIRED = ("id", "ledger", "reason", "fix", "deploy", "eta")


def load(path: Path = REGISTRY) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    out, seen = [], set()
    for b in doc.get("blockers") or []:
        missing = [k for k in REQUIRED if not b.get(k)]
        if missing:
            raise ValueError(f"expected blocker {b.get('id')} missing {missing}")
        if b["id"] in seen:
            raise ValueError(f"expected blocker {b['id']} declared twice")
        if parse_ts(b["eta"]) is None:
            raise ValueError(f"expected blocker {b['id']} eta is not a timestamp")
        seen.add(b["id"])
        out.append(b)
    return out


def _matches(blocker: dict[str, Any], finding: Any) -> bool:
    m = blocker.get("match") or {}
    if not m or finding.id != m.get("finding") or finding.severity not in ("AMBER", "RED"):
        return False
    needle = m.get("observed_contains")
    return not needle or needle in str(finding.observed)


def assess(findings: list[Any], now: float, blockers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    blockers = load() if blockers is None else blockers
    rows = []
    for b in blockers:
        eta = parse_ts(b["eta"])
        overdue = now >= eta
        matched = [f.id for f in findings if _matches(b, f)]
        for f in findings:
            if f.id in matched:
                f.evidence.setdefault("expected_blockers", []).append(
                    {"id": b["id"], "fix": b["fix"], "eta": b["eta"], "overdue": overdue})
        rows.append({"id": b["id"], "ledger": b["ledger"], "reason": b["reason"], "fix": b["fix"],
                     "deploy": b["deploy"], "eta": b["eta"], "overdue": overdue,
                     "eta_in_sec": int(eta - now), "annotates": matched})
    overdue = [r for r in rows if r["overdue"]]
    if not rows:
        severity, observed = "GREEN", "no expected blockers declared"
    elif overdue:
        severity = "RED"
        observed = (f"{len(overdue)} of {len(rows)} expected blocker(s) past ETA: "
                    + "; ".join(f"{r['id']} (eta {r['eta']}, fix {r['fix']})" for r in overdue))
    else:
        severity = "AMBER"
        first = min(rows, key=lambda r: r["eta_in_sec"])
        observed = (f"{len(rows)} declared expected blocker(s) awaiting deploy; earliest ETA {first['eta']}: "
                    + ", ".join(r["id"] for r in rows))
    return {"severity": severity, "observed": observed, "blockers": rows, "checked_at": iso(now)}

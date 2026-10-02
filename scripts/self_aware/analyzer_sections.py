"""Analyzer dashboard sections: every :9001 section populated, fresh, dimension-complete and consistent.

Runs every 2 h. Reads the dashboard's own section health (/api/sections/health,
which derives each section's JSON endpoints from the dashboard code) and adds
independent laptop-side evidence: the genome grid report's age on disk, how
many collected opportunities it covers against the mirror ledger, and whether a
core section shrank sharply since the previous run. A section that collapses
(e.g. the Top-100 panel falling back to the legacy ADX x gap x entry x lane
cohort) or goes stale is AMBER/RED, never GREEN.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .facts import http_json, iso, parse_ts

DASHBOARD = os.environ.get("SELF_AWARE_ANALYZER_URL", "http://127.0.0.1:9001")
GENOME_GRID_REPORT = Path(os.environ.get(
    "ANALYZER_GENOME_GRID_REPORT", r"C:\DoxxedCrypto\analyzer-exports\genome-grid\genome_grid_report.json"))
GRID_AMBER_SEC = 3 * 3600
GRID_RED_SEC = 12 * 3600
SHRINK_RATIO = 0.2
SHRINK_MIN_ROWS = 20


def _line_count(path: Path) -> int | None:
    try:
        with open(path, "rb") as handle:
            return sum(1 for line in handle if line.strip())
    except OSError:
        return None


def _grid_on_disk(now: float) -> dict[str, Any]:
    try:
        rep = json.loads(GENOME_GRID_REPORT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"present": False, "error": f"{type(exc).__name__}: {exc}"[:200], "path": str(GENOME_GRID_REPORT)}
    gen = parse_ts(rep.get("generated_at"))
    cov, grid = rep.get("coverage") or {}, rep.get("grid") or {}
    return {"present": True, "path": str(GENOME_GRID_REPORT), "generated_at": rep.get("generated_at"),
            "age_sec": round(now - gen, 1) if gen else None, "episodes_evaluated": cov.get("episodes_evaluated"),
            "episodes_collected": cov.get("episodes_collected"), "last_signal_utc": cov.get("last_signal_utc"),
            "policies_evaluated": grid.get("policies_evaluated"), "policies_ranked": grid.get("policies_ranked"),
            "parity": (rep.get("canonical_parity") or {}).get("status")}


def run(paths, state: dict[str, Any], now: float | None = None, fetch=http_json) -> dict[str, Any]:
    now = now or time.time()
    body, error, elapsed = fetch(f"{DASHBOARD}/api/sections/health", 240)
    sections = (body or {}).get("sections") or [] if isinstance(body, dict) else []
    previous = (state.get("analyzer_sections") or {}).get("rows") or {}
    shrank = []
    for s in sections:
        before = int(previous.get(s["id"]) or 0)
        if s.get("core") and before >= SHRINK_MIN_ROWS and s.get("rows", 0) < before * SHRINK_RATIO:
            shrank.append({"id": s["id"], "rows_before": before, "rows_now": s.get("rows", 0)})
    if sections:
        state["analyzer_sections"] = {"at": iso(now), "rows": {s["id"]: s.get("rows", 0) for s in sections}}
    grid = _grid_on_disk(now)
    collected = _line_count(paths.mirror / "v3" / "ledgers" / "opportunity.jsonl")
    return {
        "schema": "self_aware_analyzer_sections_v1", "generated_at": iso(now),
        "dashboard": {"url": f"{DASHBOARD}/api/sections/health", "error": error, "elapsed_sec": elapsed,
                      "verdict": (body or {}).get("verdict") if isinstance(body, dict) else None,
                      "counts": (body or {}).get("counts") if isinstance(body, dict) else None,
                      "generated_at": (body or {}).get("generated_at") if isinstance(body, dict) else None},
        "sections": [{k: s.get(k) for k in ("id", "label", "kind", "core", "severity", "rows", "newest_generated_at",
                                              "apis", "checks")} for s in sections],
        "genome_grid": grid, "collected_opportunities": collected, "shrank": shrank,
    }


def findings(doc: dict[str, Any] | None, now: float, max_age_sec: float) -> list[dict[str, Any]]:
    """Plain finding dicts (diagnose.check_analyzer_sections wraps them in Finding)."""
    ids = ("analyzer.sections", "analyzer.dimensions", "analyzer.consistency")
    gen = parse_ts((doc or {}).get("generated_at"))
    if not doc or not gen or now - gen > max_age_sec:
        return [{"id": i, "severity": "SKIP", "observed": "section check has not run in the last 3 h",
                 "expected": "sections job every 2 h", "emit_alarm": False} for i in ids]
    dash = doc.get("dashboard") or {}
    secs = doc.get("sections") or []
    out = []
    if dash.get("error") or not secs:
        out.append({"id": "analyzer.sections", "severity": "RED",
                    "observed": f"{dash.get('url')} unreadable: {dash.get('error') or 'no sections'}",
                    "expected": "every :9001 section readable as JSON"})
    else:
        bad = [s for s in secs if s.get("core") and s.get("severity") in ("RED", "AMBER")
               and any(c["id"] in ("api_ok", "populated", "fresh", "has_api") and c["severity"] in ("RED", "AMBER")
                       for c in s.get("checks") or [])]
        sev = "RED" if any(s["severity"] == "RED" for s in bad) else "AMBER" if bad else "GREEN"
        detail = "; ".join(f"{s['id']}: " + ", ".join(f"{c['id']} {c['observed']}" for c in s["checks"]
                                                      if c["severity"] in ("RED", "AMBER") and c["id"] in
                                                      ("api_ok", "populated", "fresh", "has_api")) for s in bad[:5])
        out.append({"id": "analyzer.sections", "severity": sev,
                    "observed": detail or f"{len(secs)} sections readable; core sections populated and fresh "
                                          f"(dashboard verdict {dash.get('verdict')}, counts {dash.get('counts')})",
                    "expected": "core sections (overview, top combos, safe genome, lanes, coverage, data health, decision) "
                                "answer 200, are non-empty and <= 3 h old"})
    dim_checks = [c for s in secs if s["id"] in ("combos", "genome") for c in s.get("checks") or []
                  if c["id"].startswith(("genome_", "safe_genome_"))]
    dim_bad = [c for c in dim_checks if c["severity"] in ("RED", "AMBER")]
    sev = "RED" if any(c["severity"] == "RED" for c in dim_bad) else "AMBER" if dim_bad else ("GREEN" if dim_checks else "AMBER")
    out.append({"id": "analyzer.dimensions", "severity": sev,
                "observed": "; ".join(f"{c['id']}: {c['observed']}" for c in dim_bad[:6]) or
                            (f"{len(dim_checks)} policy-dimension checks pass" if dim_checks else "no policy-dimension checks published"),
                "expected": "Top-100 panel backed by the full genome grid (offset, chase, TTL, exits, side) and the "
                            "Safe Policy Genome shortlist non-empty with replay coverage",
                "evidence": {"checks": dim_checks}})
    grid, collected = doc.get("genome_grid") or {}, doc.get("collected_opportunities")
    problems = []
    if not grid.get("present"):
        problems.append(("RED", f"genome grid report missing: {grid.get('error')}"))
    else:
        age = grid.get("age_sec") or 0
        if age > GRID_RED_SEC:
            problems.append(("RED", f"genome grid {age / 3600:.1f} h old"))
        elif age > GRID_AMBER_SEC:
            problems.append(("AMBER", f"genome grid {age / 3600:.1f} h old"))
        if collected and (grid.get("episodes_evaluated") or 0) < 0.5 * collected:
            problems.append(("AMBER", f"genome grid evaluated {grid.get('episodes_evaluated')} episodes vs "
                                      f"{collected} collected opportunities"))
        if grid.get("parity") == "MISMATCH":
            problems.append(("RED", "genome grid replay disagrees with the engine's canonical replay"))
    for s in doc.get("shrank") or []:
        problems.append(("AMBER", f"{s['id']} shrank {s['rows_before']} -> {s['rows_now']} rows since the last check"))
    sev = "RED" if any(p[0] == "RED" for p in problems) else "AMBER" if problems else "GREEN"
    out.append({"id": "analyzer.consistency", "severity": sev,
                "observed": "; ".join(p[1] for p in problems) or
                            f"genome grid {grid.get('episodes_evaluated')} episodes / {collected} collected opportunities, "
                            f"{(grid.get('age_sec') or 0) / 3600:.1f} h old, parity {grid.get('parity')}",
                "expected": "genome grid <= 3 h old, covers >= 50% of collected opportunities, engine parity MATCH, "
                            "no core section shrinks > 80% between checks",
                "evidence": {"genome_grid": grid, "collected_opportunities": collected, "shrank": doc.get("shrank")}})
    return out
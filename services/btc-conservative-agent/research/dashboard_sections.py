"""Section index and section health for the :9001 research dashboard.

Every dashboard section is reachable as JSON: the endpoints a section reads are
derived from the dashboard's own ``SECTION_LOADERS`` and loader bodies (and,
for standalone pages, from the ``fetch('/api/...')`` calls in the page), so
there is no second hand-maintained section list. Health per section is:
populated (non-empty payload), fresh (generated_at age), and for the policy
sections dimension-complete (the genome grid still spans its axes instead of
collapsing to the legacy ADX x gap x entry x lane cohort).
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any, Iterable, Mapping

GREEN, AMBER, RED, INFO = "GREEN", "AMBER", "RED", "INFO"
_RANK = {RED: 3, AMBER: 2, INFO: 1, GREEN: 0}

FRESH_AMBER_SEC = 3 * 3600
FRESH_RED_SEC = 12 * 3600
LEGACY_COMBO_DIMENSIONS = ("adx_bucket", "directional_spread_bucket", "entry_mode_bucket", "research_lane")
# Minimum distinct values per genome axis before the section counts as collapsed.
GENOME_AXIS_MINIMUMS = {
    "direction_rule": 2, "fill_world": 2, "entry_offset_pct": 5, "chase_id": 3, "entry_ttl_sec": 2,
    "exit_family": 4, "atr_stop_k": 3, "atr_tp_k": 1, "profit_ladder": 2, "thesis_cut_margin_pct": 2,
    "time_stop_min": 2, "mfe_giveback": 2, "atr_trail_k": 2, "chandelier_atr_k": 2, "partial_plan": 2,
}
CORE_SECTIONS = ("summary", "combos", "genome", "lanes", "evidence-coverage")
CORE_PAGES = ("/data-health", "/safe-policy-genome-v3.1", "/decision")
EXTRA_SECTION_APIS = {"combos": ("/api/genome-grid",), "genome": ("/api/safe-policy-genome-v3.1",)}

_API_LITERAL_RE = re.compile(r"[`'\"](/api/[A-Za-z0-9_\-./]+)")
# Shared by every page (status strip, system-health banner); not a section's own data.
COMMON_APIS = ("/api/status", "/api/system-health")
_TS_KEYS = ("generated_at", "generatedAt", "report_generated_at", "updated_at")


def section_loaders(html: str) -> dict[str, list[str]]:
    """``SECTION_LOADERS`` from the dashboard script: section id -> loader function names."""
    match = re.search(r"const SECTION_LOADERS = \{(.*?)\};", html, re.S)
    if not match:
        return {}
    out: dict[str, list[str]] = {}
    for key, body in re.findall(r"['\"]?([A-Za-z0-9_\-]+)['\"]?\s*:\s*\[([^\]]*)\]", match.group(1)):
        out[key] = [name.strip() for name in body.split(",") if name.strip()]
    return out


def loader_apis(html: str, loader: str) -> list[str]:
    """``/api/...`` endpoints fetched inside one loader function body."""
    start = re.search(r"(?:async\s+)?function\s+" + re.escape(loader) + r"\s*\(", html)
    if not start:
        return []
    nxt = re.compile(r"\n\s*(?:async\s+)?function\s+\w+\s*\(|\}(?:async\s+)?function\s+\w+\s*\(")
    end = nxt.search(html, start.end())
    return page_apis(html[start.end(): end.start() if end else len(html)])


def page_apis(html: str) -> list[str]:
    """Quoted ``/api/...`` literals (fetch arguments or URL variables); dynamic prefixes ending in / are skipped."""
    return [api for api in dict.fromkeys(_API_LITERAL_RE.findall(html or ""))
            if not api.endswith("/") and api not in COMMON_APIS]


def section_index(html: str, nav_groups: Iterable[tuple[str, str, Iterable[tuple[str, str, Any]]]],
                  pages: Iterable[tuple[str, str]], page_html: Mapping[str, str] | None = None,
                  routes: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Every /details section and decision page; server-rendered pages gain their registered JSON twin."""
    routes = set(routes)
    loaders = section_loaders(html)
    out = []
    for gid, glabel, items in nav_groups:
        for sid, label, report_file in items:
            apis: list[str] = []
            for loader in loaders.get(sid, []):
                apis += loader_apis(html, loader)
            apis += list(EXTRA_SECTION_APIS.get(sid, ()))
            apis = [a for a in dict.fromkeys(apis) if a not in COMMON_APIS]
            out.append({"id": sid, "label": label, "group": glabel, "kind": "details_section",
                        "report_file": report_file, "loaders": loaders.get(sid, []), "apis": apis,
                        "json": f"/api/sections/{sid}", "core": sid in CORE_SECTIONS})
    for label, path in pages:
        if path.startswith("/api/") or path == "/details":
            continue
        apis = page_apis((page_html or {}).get(path, ""))
        apis += [twin for twin in (f"/api{path}", f"/api/streams{path}") if twin in routes]
        apis += list(EXTRA_SECTION_APIS.get(path, ()))
        sid = "page" + path.replace("/", "-").rstrip("-")
        out.append({"id": sid, "label": label, "group": "Decision pages", "kind": "page", "path": path,
                    "apis": list(dict.fromkeys(apis)), "json": f"/api/sections/{sid}", "core": path in CORE_PAGES})
    return out


def _parse_ts(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value > 1e9:
        return float(value)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def payload_shape(obj: Any, depth: int = 0) -> dict[str, Any]:
    """Count non-empty lists / rows and find the newest generated_at within three levels."""
    shape = {"lists": 0, "rows": 0, "scalars": 0, "generated_at": None}
    if depth > 3:
        return shape
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            if key in _TS_KEYS:
                ts = _parse_ts(value)
                if ts and (shape["generated_at"] is None or ts > shape["generated_at"]):
                    shape["generated_at"] = ts
            if isinstance(value, (Mapping, list)):
                sub = payload_shape(value, depth + 1)
                shape["lists"] += sub["lists"]
                shape["rows"] += sub["rows"]
                shape["scalars"] += sub["scalars"]
                if sub["generated_at"] and (shape["generated_at"] is None or sub["generated_at"] > shape["generated_at"]):
                    shape["generated_at"] = sub["generated_at"]
            elif value not in (None, "", 0, False):
                shape["scalars"] += 1
    elif isinstance(obj, list) and obj:
        shape["lists"] += 1
        shape["rows"] += len(obj)
        for item in obj[:20]:
            sub = payload_shape(item, depth + 1)
            shape["scalars"] += sub["scalars"]
            if sub["generated_at"] and (shape["generated_at"] is None or sub["generated_at"] > shape["generated_at"]):
                shape["generated_at"] = sub["generated_at"]
    return shape


def genome_grid_checks(grid: Mapping[str, Any] | None, combos: Mapping[str, Any] | None, now: float,
                       collected_opportunities: int | None = None) -> list[dict[str, Any]]:
    """Dimension completeness of the Top-100 panel: genome grid present, axes spread, parity, coverage."""
    checks: list[dict[str, Any]] = []
    combos = combos or {}
    dims = tuple(combos.get("dimensions") or ())
    if not grid or grid.get("status") == "UNAVAILABLE":
        sev = RED if dims == LEGACY_COMBO_DIMENSIONS or not dims else AMBER
        checks.append({"id": "genome_grid_present", "severity": sev,
                       "observed": f"genome grid unavailable; Top-100 panel shows only {list(dims) or 'nothing'}",
                       "expected": "genome_grid_report.json with entry/exit/direction axes"})
        return checks
    checks.append({"id": "genome_grid_present", "severity": GREEN, "observed": f"generated {grid.get('generated_at')}",
                   "expected": "genome_grid_report.json present"})
    age = now - (_parse_ts(grid.get("generated_at")) or 0)
    checks.append({"id": "genome_grid_fresh", "severity": RED if age > FRESH_RED_SEC else AMBER if age > FRESH_AMBER_SEC else GREEN,
                   "observed": f"age {age / 3600:.1f} h", "expected": f"<= {FRESH_AMBER_SEC // 3600} h"})
    summary = grid.get("dimension_summary") or {}
    collapsed = [f"{axis}={(summary.get(axis) or {}).get('distinct_values', 0)}<{need}"
                 for axis, need in GENOME_AXIS_MINIMUMS.items()
                 if int((summary.get(axis) or {}).get("distinct_values") or 0) < need]
    checks.append({"id": "genome_axes_complete", "severity": AMBER if collapsed else GREEN,
                   "observed": ("collapsed axes: " + ", ".join(collapsed)) if collapsed else f"{len(summary)} axes populated",
                   "expected": "every genome axis spans its minimum distinct values"})
    grid_counts = grid.get("grid") or {}
    ranked = int(grid_counts.get("policies_ranked") or 0)
    checks.append({"id": "genome_ranked_rows", "severity": AMBER if ranked == 0 else GREEN,
                   "observed": f"{ranked} of {grid_counts.get('policies_evaluated')} policies have >= "
                               f"{(grid.get('holdout') or {}).get('min_oos_fills_for_rank')} OOS fills",
                   "expected": "at least one ranked policy"})
    parity = (grid.get("canonical_parity") or {}).get("status")
    checks.append({"id": "genome_engine_parity", "severity": RED if parity == "MISMATCH" else AMBER if parity != "MATCH" else GREEN,
                   "observed": f"canonical replay parity {parity} ({(grid.get('canonical_parity') or {}).get('checked')} checked)",
                   "expected": "MATCH against research_v3_policy_replay"})
    cov = grid.get("coverage") or {}
    evaluated = int(cov.get("episodes_evaluated") or 0)
    if collected_opportunities:
        ratio = evaluated / collected_opportunities
        checks.append({"id": "genome_vs_collected", "severity": AMBER if ratio < 0.5 else GREEN,
                       "observed": f"{evaluated} episodes evaluated vs {collected_opportunities} collected opportunities ({ratio:.0%})",
                       "expected": ">= 50% of collected opportunities (rest censored by tape window/ATR)"})
    return checks


def safe_genome_checks(report: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Wiring of the Safe Policy Genome section: replay window, shortlist, integrity."""
    report = report or {}
    if not report or report.get("schema") == "current_generation_report_unavailable_v1":
        return [{"id": "safe_genome_present", "severity": RED, "observed": "safe_policy_genome_v3 report unavailable",
                 "expected": "current generation report"}]
    checks = []
    win = report.get("protection_replay_window") or ((report.get("candidate_screen") or {}).get("input_window")) or {}
    level = win.get("alert_level") or ("AMBER" if win.get("truncated") else "GREEN")
    checks.append({"id": "safe_genome_replay_window", "severity": AMBER if level in ("RED", "AMBER") else GREEN,
                   "observed": f"replayed {win.get('events_replayed', win.get('events_selected'))} of "
                               f"{win.get('events_eligible')} eligible events ({win.get('coverage_ratio')}); "
                               "full history is covered by the genome grid",
                   "expected": "replay covers the epoch or the genome grid covers it"})
    screen = report.get("candidate_screen") or {}
    shown = sum(len(screen.get(k) or []) for k in ("descriptive_top_100", "profitable_conservative_top_100",
                                                   "profitable_ideal_touch_diagnostic_top_100"))
    checks.append({"id": "safe_genome_shortlist", "severity": AMBER if shown == 0 else GREEN,
                   "observed": f"{shown} shortlist rows of {screen.get('unique_policies_evaluated')} enumerated",
                   "expected": "a non-empty descriptive or diagnostic shortlist"})
    status = str(report.get("status") or "")
    checks.append({"id": "safe_genome_integrity", "severity": AMBER if "FAILED" in status else GREEN,
                   "observed": status, "expected": "integrity checked and passing (fail-closed otherwise)"})
    return checks


def evaluate_section(entry: Mapping[str, Any], responses: Mapping[str, tuple[int, Any]], now: float | None = None,
                     extra_checks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    now = now or time.time()
    checks: list[dict[str, Any]] = []
    rows = lists = 0
    newest = None
    failed = []
    for api in entry.get("apis") or []:
        status, payload = responses.get(api, (0, None))
        if status != 200:
            failed.append(f"{api}:{status}")
            continue
        shape = payload_shape(payload)
        rows += shape["rows"]
        lists += shape["lists"]
        if shape["generated_at"] and (newest is None or shape["generated_at"] > newest):
            newest = shape["generated_at"]
    core = bool(entry.get("core"))
    if failed:
        checks.append({"id": "api_ok", "severity": RED if core else AMBER, "observed": ", ".join(failed),
                       "expected": "every section API answers 200"})
    if not entry.get("apis"):
        checks.append({"id": "has_api", "severity": AMBER if core else INFO, "observed": "no JSON endpoint derived",
                       "expected": "section readable as JSON"})
    elif rows == 0:
        checks.append({"id": "populated", "severity": AMBER if core else INFO,
                       "observed": "every list in the section payload is empty", "expected": "rows to display"})
    if newest is not None:
        age = now - newest
        sev = RED if age > FRESH_RED_SEC else AMBER if age > FRESH_AMBER_SEC else GREEN
        checks.append({"id": "fresh", "severity": sev if core else (INFO if sev != GREEN else GREEN),
                       "observed": f"newest generated_at {age / 3600:.1f} h old", "expected": f"<= {FRESH_AMBER_SEC // 3600} h"})
    checks += extra_checks or []
    severity = max((c["severity"] for c in checks), key=lambda s: _RANK[s], default=GREEN)
    return {"id": entry["id"], "label": entry["label"], "kind": entry.get("kind"), "core": core,
            "severity": severity, "rows": rows, "lists": lists,
            "newest_generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(newest)) if newest else None,
            "apis": entry.get("apis") or [], "checks": checks}


def verdict(sections: list[dict[str, Any]]) -> str:
    return max((s["severity"] for s in sections if s["severity"] != INFO), key=lambda s: _RANK[s], default=GREEN)
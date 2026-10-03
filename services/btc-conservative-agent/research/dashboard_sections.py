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
    "direction_rule": 2, "entry_offset_pct": 5, "chase_id": 3, "entry_ttl_sec": 2,
    "exit_family": 4, "atr_stop_k": 3, "atr_tp_k": 1, "profit_ladder": 2, "thesis_cut_margin_pct": 2,
    "time_stop_min": 2, "mfe_giveback": 2, "atr_trail_k": 2, "chandelier_atr_k": 2, "partial_plan": 2,
}
GENOME_HEADLINE_FILL_WORLD = "REALISTIC_V1"
GENOME_AI_EPISODE_CLASSES = ("AI_COMMITTED", "AI_COMMITTED_SCORE_CONFLICT", "AI_NO_TRADE_SCORE_LED", "AI_SIGNAL_REPLAY")
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
    declared = int(cov.get("v3_opportunities_excluded_declared") or 0)
    if collected_opportunities:
        ratio = (evaluated + declared) / collected_opportunities
        checks.append({"id": "genome_vs_collected", "severity": AMBER if ratio < 0.5 else GREEN,
                       "observed": f"{evaluated} AI episodes evaluated + {declared} declared exclusions (cross-venue, "
                                   f"duplicates, no side) vs {collected_opportunities} collected opportunities ({ratio:.0%})",
                       "expected": ">= 50% of collected opportunities accounted (rest censored by tape window/ATR)"})
    checks.extend(genome_content_checks(grid))
    return checks


def genome_content_checks(grid: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Content contract: one episode per AI decision, no mixed classes, REALISTIC_V1-only axes that differ per value."""
    checks: list[dict[str, Any]] = []
    integ = grid.get("episode_integrity") or {}
    classes = (grid.get("coverage") or {}).get("evaluated_by_class") or integ.get("classes") or {}
    foreign = {c: n for c, n in classes.items() if c not in GENOME_AI_EPISODE_CLASSES}
    if not integ:
        sev, obs = RED, "no episode_integrity declared: cross-venue triggers and duplicate rows may be counted as AI episodes"
    elif integ.get("status") != "PASS" or integ.get("duplicate_decision_ids") or foreign:
        sev, obs = RED, (f"integrity {integ.get('status')}, duplicate decision ids {integ.get('duplicate_decision_ids')}, "
                         f"foreign classes {foreign}: {'; '.join(integ.get('violations') or [])[:200]}")
    else:
        sev, obs = GREEN, f"{integ.get('episodes')} episodes = {integ.get('unique_decision_ids')} unique AI decisions; {classes}"
    checks.append({"id": "genome_episode_integrity", "severity": sev, "observed": obs,
                   "expected": "one episode per unique AI decision; only AI episode classes in the genome cohort"})
    summary = grid.get("dimension_summary") or {}
    head = grid.get("headline_fill_world")
    optimistic = sorted(axis for axis, s in summary.items()
                        if axis == "fill_world" or s.get("headline_fill_world") != GENOME_HEADLINE_FILL_WORLD
                        or any(v.get("best_fill_world") not in (None, GENOME_HEADLINE_FILL_WORLD)
                               for v in s.get("values") or []))
    bad = head != GENOME_HEADLINE_FILL_WORLD or optimistic
    checks.append({"id": "genome_axes_headline_world", "severity": RED if bad else GREEN,
                   "observed": (f"headline_fill_world={head}; axes not restricted to {GENOME_HEADLINE_FILL_WORLD}: "
                                f"{optimistic[:6]}") if bad else f"{len(summary)} axes select within {GENOME_HEADLINE_FILL_WORLD}",
                   "expected": "headline and every axis winner are REALISTIC_V1; optimistic fills only in shadow columns"})
    identical = []
    for axis, s in summary.items():
        picks = [v.get("best_policy_id") for v in s.get("values") or [] if v.get("best_policy_id")]
        if len(picks) >= 2 and len(set(picks)) < 2:
            identical.append(axis)
    tops = {((s.get("values") or [{}])[0] or {}).get("best_policy_id") for s in summary.values()}
    rows_identical = len(summary) > 1 and all(len(s.get("values") or []) <= 1 for s in summary.values()) and len(tops) == 1
    checks.append({"id": "genome_axes_distinct", "severity": RED if identical or rows_identical else GREEN,
                   "observed": (f"axes whose value rows all show one policy: {identical[:6]}" if identical else
                                "every axis shows the same single row" if rows_identical else
                                f"{sum(len(s.get('values') or []) for s in summary.values())} per-value rows"),
                   "expected": "each axis value shows its own best train-selected policy"})
    top = (grid.get("top_100_by_world") or {}).get(GENOME_HEADLINE_FILL_WORLD) or []
    lacking = sum(1 for r in top if not r.get("by_episode_class") or not r.get("cluster_1h"))
    wf = grid.get("walk_forward_by_utc_day") or {}
    checks.append({"id": "genome_top100_inference", "severity": AMBER if lacking or not wf else GREEN,
                   "observed": f"{len(top) - lacking}/{len(top)} headline rows carry class split + 1h-cluster CI; "
                               f"walk-forward cohorts {sorted(wf)}",
                   "expected": "every headline Top-100 row has by_episode_class and cluster_1h; walk-forward by UTC day"})
    return checks


def _descending(values: Iterable[Any]) -> bool:
    vals = [v for v in values if v is not None]
    return all(a >= b for a, b in zip(vals, vals[1:]))


def research_api_checks(index: Mapping[str, Any] | None, grid: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Materialized research API: present, same generation as the genome report, complete, sorted, reconciled."""
    index = index or {}
    if index.get("status") != "OK":
        return [{"id": "research_api_present", "severity": RED, "observed": f"research API cache {index.get('status')}: "
                 f"{index.get('reason') or 'missing'}", "expected": "research_api_cache.sqlite3 materialized by the genome cycle"}]
    ci = index.get("contract_inputs") or {}
    checks = [{"id": "research_api_present", "severity": GREEN, "observed": f"generation {index.get('generation')}",
               "expected": "research API cache present"}]
    same = (grid or {}).get("generated_at") == index.get("generated_at")
    checks.append({"id": "research_api_generation", "severity": GREEN if same else AMBER,
                   "observed": f"cache {index.get('generated_at')} vs genome report {(grid or {}).get('generated_at')}",
                   "expected": "cache materialized from the current genome report"})
    layer = ci.get("research_layer_status")
    checks.append({"id": "research_layer_ok", "severity": GREEN if layer == "OK" else RED,
                   "observed": f"research layer {layer} {ci.get('research_layer_error') or ''}".strip(),
                   "expected": "mix-and-match, totals and forward tracker computed"})
    missing_fam = sorted(set(ci.get("families_expected") or []) - set(ci.get("families_present_totals") or []))
    missing_tab = sorted(set(ci.get("families_expected") or []) - set(ci.get("families_present_table") or []))
    missing_reg = sorted(set(ci.get("regimes_expected") or []) - set(ci.get("regimes_present") or []))
    incomplete = missing_fam or missing_tab or missing_reg or not ci.get("families_expected")
    checks.append({"id": "research_families_regimes_complete", "severity": AMBER if incomplete else GREEN,
                   "observed": (f"missing families totals {missing_fam}, table {missing_tab}; regimes {missing_reg}"
                                if incomplete else f"{len(ci.get('families_expected') or [])} families, "
                                f"{len(ci.get('regimes_expected') or [])} regimes, cohorts {ci.get('cohorts_present')}"),
                   "expected": "every family group and every regime appears"})
    unsorted = [k for k, s in (ci.get("sort_keys") or {}).items() if not _descending(s.get("values") or [])]
    checks.append({"id": "research_sort_order", "severity": RED if unsorted else GREEN,
                   "observed": f"not descending: {unsorted}" if unsorted else f"{len(ci.get('sort_keys') or {})} tables descending by their key",
                   "expected": "Top 100 by OOS net $, family totals and live lanes sorted descending"})
    bad = [t["policy_id"] for t in ci.get("top_100_totals") or []
           if t["wins"] + t["losses"] > t["fills"] or abs(t["net_in_sample_usd"] + t["net_oos_usd"] - t["net_pnl_usd"]) > 1e-4]
    rec = (ci.get("reconciliation") or {}).get("status")
    checks.append({"id": "research_totals_reconcile", "severity": RED if bad or rec != "PASS" else GREEN,
                   "observed": f"matrix reconciliation {rec}; inconsistent rows {bad[:3]}",
                   "expected": "totals equal the per-trade outcome matrix; in-sample + OOS = all; wins + losses <= fills"})
    chain = ci.get("forward_chain_ok")
    checks.append({"id": "research_forward_chain", "severity": RED if chain is False else GREEN if chain else AMBER,
                   "observed": f"forward hash chain {chain}; verdicts {ci.get('forward_verdicts')}",
                   "expected": "frozen candidates append-only (prev_sha chain intact)"})
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
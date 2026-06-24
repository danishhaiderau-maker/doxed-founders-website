#!/usr/bin/env python3
"""
Research Dashboard v1.5 — read-only viewer for analyzer outputs.

Runs independently from the trading bot. Never touches execution.
Default: http://0.0.0.0:9500  →  global showcase analyzer (see config/home-showcase.lock.json)

  python research_dashboard.py
  RESEARCH_DASHBOARD_PORT=9001 RESEARCH_DASHBOARD_BIND_HOST=0.0.0.0 python research_dashboard.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")


def format_melbourne_dt(value) -> str:
    """24h Melbourne display for dashboard (matches Agent Hub)."""
    if value is None or value == "":
        return "—"
    try:
        if isinstance(value, (int, float)):
            dt = datetime.fromtimestamp(value, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(MELBOURNE_TZ)
        abbrev = local.strftime("%Z")
        return local.strftime(f"%Y-%m-%d %H:%M:%S {abbrev}")
    except Exception:
        return str(value)[:19] if value else "—"


from flask import Flask, jsonify, render_template_string, send_file, abort, request

try:
    from combo_pathway_config import (
        ANALYZER_SYNC_ID as EXPECTED_ANALYZER_SYNC_ID,
        BENCHMARK_LANE,
        COMPARISON_BENCHMARK_LANE,
        CONTINUOUS_PROXY_LANES,
        EXECUTION_FIX_VERSION as EXPECTED_BOT_VERSION,
        PRIMARY_PRODUCTION_LANE,
        RESEARCH_DASHBOARD_VERSION,
    )
    from pathway_lane_roster import ANALYZER_COMPARE_LANES, DASHBOARD_PATHWAY_LANES
    ALL_PATHWAY_LANES = ANALYZER_COMPARE_LANES
except ImportError:
    BENCHMARK_LANE = "CONTINUOUS"
    COMPARISON_BENCHMARK_LANE = "CONTINUOUS"
    CONTINUOUS_PROXY_LANES = ("COMBO_65_SP5_CHASE_3PLUS", "COMBO_604_SP4_CHASE_3PLUS")
    PRIMARY_PRODUCTION_LANE = "COMBO_65_SP5_CHASE_3PLUS"
    EXPECTED_BOT_VERSION = "unknown"
    EXPECTED_ANALYZER_SYNC_ID = "unknown"
    RESEARCH_DASHBOARD_VERSION = "v9.83-quality-roster-4-tiles-2026-06-21"
    ALL_PATHWAY_LANES = (
        "COMBO_65_SP5_CHASE_3PLUS",
        "COMBO_604_SP4_CHASE_3PLUS",
        "CONTINUOUS",
        "AI_DISAGREEMENT_REPLAY",
        "COMBO_65_SP5_DIRECT",
        "COMBO_604_SP4_DIRECT",
        "RECOVERY_MONSTER_V1",
        "TYPE_B_PREDICTOR_V1",
        "AI_DISAGREEMENT_ALPHA",
        "EXTREME_EDGE",
        "EDGE_PLUS_STACK",
        "AI_SCAN",
        "HIGH_EDGE_RUNNER",
        "SHADOW_RUNNER",
        "EDGE_ALPHA_4",
        "TYPE_B_HUNTER",
        "SHORT_BEAR_ALPHA",
        "AI_60_65_ALPHA",
        "URGENT_CHASE_ALPHA",
        "CHASE_3PLUS_ALPHA",
    )

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
ROOT = Path(os.path.abspath(os.path.dirname(__file__) or os.getcwd()))
_parent = ROOT.parent
DATA_ROOT = Path(os.getenv("BTC_AGENT_DATA_DIR", str(_parent if (_parent / "trades_3factor.csv").is_file() else ROOT)))
BIND_HOST = os.getenv("RESEARCH_DASHBOARD_BIND_HOST", "0.0.0.0")
BIND_PORT = int(os.getenv("RESEARCH_DASHBOARD_PORT", "9001"))
PUBLIC_URL = os.getenv("RESEARCH_DASHBOARD_PUBLIC_URL", f"http://127.0.0.1:{BIND_PORT}")

REPORT_MANIFEST_FILE = "report_manifest.json"
COMPACT_SUMMARY_FILE = "research_compact_summary.json"
EXECUTIVE_SUMMARY_FILE = "executive_summary.txt"
HIGHLIGHTS_FILE = "research_highlights.txt"
FINDINGS_FILE = "research_findings.txt"
COVERAGE_FILE = "research_coverage.txt"
DEEP_DIVE_INDEX_FILE = "research_deep_dive_index.txt"
ANALYSIS_DASHBOARD_HTML = "analysis_dashboard.html"
ANALYZER_LOG_FILE = "analyzer_run.log"
REPORTS_DIR = "reports"
ALL_DATA_REPORTS_DIR = os.path.join(REPORTS_DIR, "all_data")
ARCHIVE_DIR = "research_session_archives"
ARCHIVE_INDEX_FILE = "research_session_index.json"
ZIP_BUNDLE_NAME = "reports_bundle.zip"
COMPLETE_BUNDLE_NAME = "trading_sessions_complete.zip"
COMPLETE_BUNDLE_FALLBACKS = (
    "trading_sessions_complete_v2.zip",
    "trading_sessions_complete_verified.zip",
)
_parent = ROOT.parent
HISTORY_ROOT = Path(os.getenv(
    "RESEARCH_HISTORY_ROOT",
    str(ROOT if (ROOT / ARCHIVE_DIR).is_dir() else (_parent if (_parent / ARCHIVE_DIR).is_dir() else ROOT)),
))

REPORT_NAV = (
    ("summary", "Overview", None),
    ("findings", "Findings", None),
    ("lanes", "Lanes", "benchmark_vs_lanes_report.json"),
    ("lanes-retire", "Lane Retirement", "lane_retirement_report.json"),
    ("lanes-def", "Lane Definitions", "lane_definition_report.json"),
    ("regime", "Regime", "regime_leaderboard.json"),
    ("regime-conf", "Regime × Conf", "regime_confidence_matrix.json"),
    ("chase", "Chase", "chase_attribution_report.json"),
    ("chase-threshold", "Chase Threshold", "chase_threshold_report.json"),
    ("chase-delay", "Chase Delay", "chase_delay_report.json"),
    ("chase-iso", "Chase Isolation", "lane_chase_isolation_report.json"),
    ("combos", "Top Combos", "top_combinations_report.json"),
    ("exit-combos", "Exit Combos", "exit_combinations_report.json"),
    ("exit-reason-leak", "Exit Reason Leak", "exit_leakage_by_reason_report.json"),
    ("ladder-sim", "Ladder Simulator", "exit_ladder_simulator_report.json"),
    ("pathway-audit", "Pathway Audit", "tile_independence_report.json"),
    ("typeb", "Type B Predictor", "type_b_predictor_report.json"),
    ("exits", "Exit Leakage", "top_leakage_report.json"),
    ("horizon", "Recovery", "horizon_profitability_report.json"),
    ("ai", "AI Lab", "ai_calibration_report.json"),
    ("edge", "Edge & Features", "feature_importance_report.json"),
    ("explorer", "Report Explorer", None),
    ("archives", "Archives", None),
    ("download", "Downloads", None),
)

BUNDLE_FILES = (
    EXECUTIVE_SUMMARY_FILE,
    HIGHLIGHTS_FILE,
    FINDINGS_FILE,
    COVERAGE_FILE,
    DEEP_DIVE_INDEX_FILE,
    ANALYSIS_DASHBOARD_HTML,
    ANALYZER_LOG_FILE,
    COMPACT_SUMMARY_FILE,
    REPORT_MANIFEST_FILE,
)

app = Flask("research_dashboard")


def _load_bot_session():
    for base in (DATA_ROOT, ROOT):
        path = base / "research_session.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return _read_json("research_session.json")


def _summary_stale_meta(compact: dict) -> dict:
    """Detect when dashboard JSON is from pre-wipe / pre-session analyzer run."""
    session = _load_bot_session() or {}
    compact = compact or {}
    reasons = []
    stale = False
    gen_at = compact.get("generated_at")
    bot_start = session.get("fresh_collection_start_time") if session.get("fresh_collection_mode") else session.get("bot_start_time")
    data_scope = str(compact.get("data_scope") or "all").lower()
    scope_label = compact.get("session_scope") or "ALL-DATA"
    trades_csv = DATA_ROOT / "trades_3factor.csv"
    trades_rows = 0
    if trades_csv.is_file():
        try:
            trades_rows = max(0, sum(1 for _ in trades_csv.open(encoding="utf-8", errors="replace")) - 1)
        except Exception:
            trades_rows = -1

    if gen_at and bot_start:
        try:
            gen_ts = datetime.fromisoformat(str(gen_at).replace("Z", "+00:00")).timestamp()
            if gen_ts < float(bot_start) - 30:
                stale = True
                reasons.append("Report generated before current bot session started")
        except Exception:
            pass

    if data_scope == "all" and scope_label == "ALL-DATA":
        if session.get("fresh_collection_mode") or not trades_csv.is_file():
            stale = True
            reasons.append("ALL-DATA scope includes pre-wipe history — run analyzer after fresh collection")
        elif trades_rows == 0 and int((compact.get("performance") or {}).get("trades") or 0) > 0:
            stale = True
            reasons.append("Trades CSV empty but report shows historical trades")

    return {
        "stale": stale,
        "reasons": reasons,
        "fresh_collection_mode": bool(session.get("fresh_collection_mode")),
        "bot_start_iso": session.get("bot_start_iso"),
        "bot_version": session.get("bot_version"),
        "trades_csv_rows": trades_rows,
        "report_generated_at": gen_at,
    }


def _report_is_empty(name: str, data: dict) -> bool:
    data = data or {}
    if name == "top_combinations_report.json":
        return not (data.get("top") or [])
    if name == "chase_attribution_report.json":
        totals = data.get("overnight_watch") or data.get("totals") or {}
        return not any(
            int(totals.get(k) or 0)
            for k in ("total_fills", "chase_assisted_fills", "chase_events", "orders_created", "approve")
        )
    if name == "exit_combinations_report.json":
        return not (data.get("top") or [])
    return False


def _scope_priority(payload: dict, *, fresh_collection: bool) -> int:
    scope = str(
        payload.get("session_scope") or payload.get("data_scope") or payload.get("scope") or ""
    ).upper()
    if fresh_collection:
        if "FRESH" in scope or scope == "SESSION":
            return 3
        if scope in ("ALL-DATA", "ALL-TIME", "ALL"):
            return 0
    if scope in ("ALL-DATA", "ALL-TIME"):
        return 1
    return 2


def _iter_data_payloads(name: str):
    for path in _data_file_candidates(name):
        if not path.is_file():
            continue
        try:
            mtime = path.stat().st_mtime
            payload = json.loads(path.read_text(encoding="utf-8"))
            yield path, payload, mtime
        except Exception:
            continue


def _pick_best_payload(name: str, default=None):
    if default is None:
        default = {}
    session = _load_bot_session() or {}
    fresh = bool(session.get("fresh_collection_mode"))
    best = None
    best_key = (-1, -1.0)
    for _path, payload, mtime in _iter_data_payloads(name):
        key = (_scope_priority(payload, fresh_collection=fresh), mtime)
        if key > best_key:
            best_key = key
            best = payload
    return best if best is not None else default


def _read_report(name: str, default=None):
    """Load JSON from project root, reports/, or all_data fallback when SESSION file is empty."""
    if default is None:
        default = {}
    session = _load_bot_session() or {}
    fresh = bool(session.get("fresh_collection_mode"))
    primary = None
    primary_key = (-1, -1.0)
    fallback = None
    fallback_key = (-1, -1.0)
    for path, payload, mtime in _iter_data_payloads(name):
        key = (_scope_priority(payload, fresh_collection=fresh), mtime)
        is_all_data = str(path).endswith(os.path.join(ALL_DATA_REPORTS_DIR, name).replace("/", os.sep))
        if is_all_data:
            if not _report_is_empty(name, payload) and key > fallback_key:
                fallback_key = key
                fallback = payload
        elif key > primary_key:
            primary_key = key
            primary = payload
    if primary is not None and not _report_is_empty(name, primary):
        return primary
    if fallback is not None:
        return fallback
    if primary is not None:
        return primary
    return default


def _data_file_candidates(name: str) -> list[Path]:
    """Analyzer writes to agent root (DATA_ROOT); legacy copies may sit under research/."""
    bases = [DATA_ROOT, ROOT]
    seen: set[Path] = set()
    out: list[Path] = []
    for base in bases:
        for candidate in (base / name, base / REPORTS_DIR / name, base / ALL_DATA_REPORTS_DIR / name):
            if candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    return out


def _read_json(name: str, default=None):
    return _pick_best_payload(name, default)


def _read_text(name: str) -> str:
    for path in _data_file_candidates(name):
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
    return ""


def _file_mtime(name: str):
    best = None
    best_ts = 0.0
    for path in _data_file_candidates(name):
        if not path.is_file():
            continue
        try:
            ts = path.stat().st_mtime
            if ts >= best_ts:
                best_ts = ts
                best = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            continue
    return best


def _manifest_reports():
    manifest = _read_json(REPORT_MANIFEST_FILE)
    reports = manifest.get("reports") or []
    if reports:
        return reports
    out = []
    seen = set()
    for p in sorted(ROOT.glob("*.json")):
        if p.name in (ARCHIVE_INDEX_FILE, REPORT_MANIFEST_FILE):
            continue
        if p.name.startswith("research_session"):
            continue
        out.append({
            "title": p.stem.replace("_", " ").title(),
            "file": p.name,
            "category": "Reports",
            "description": "",
        })
        seen.add(p.name)
    rep_dir = ROOT / REPORTS_DIR
    if rep_dir.is_dir():
        for p in sorted(rep_dir.glob("*.json")):
            if p.name not in seen:
                out.append({
                    "title": p.stem.replace("_", " ").title(),
                    "file": p.name,
                    "category": "Reports",
                    "description": "",
                })
    return out


def _bundle_paths():
    paths = []
    seen = set()
    for name in BUNDLE_FILES:
        p = ROOT / name
        if p.is_file() and str(p) not in seen:
            paths.append(p)
            seen.add(str(p))
    manifest = _read_json(REPORT_MANIFEST_FILE)
    for entry in manifest.get("reports") or []:
        fname = entry.get("file") if isinstance(entry, dict) else entry
        if not fname:
            continue
        for candidate in (ROOT / fname, ROOT / REPORTS_DIR / fname):
            if candidate.is_file() and str(candidate) not in seen:
                paths.append(candidate)
                seen.add(str(candidate))
    reports_path = ROOT / REPORTS_DIR
    if reports_path.is_dir():
        for p in sorted(reports_path.glob("*.json")):
            if str(p) not in seen:
                paths.append(p)
                seen.add(str(p))
    return paths


def _lane_rows():
    bench = _read_json("benchmark_vs_lanes_report.json")
    all_data_bench_path = ROOT / ALL_DATA_REPORTS_DIR / "benchmark_vs_lanes_report.json"
    all_data_bench = {}
    if all_data_bench_path.is_file():
        try:
            all_data_bench = (json.loads(all_data_bench_path.read_text(encoding="utf-8")) or {}).get("lanes") or {}
        except Exception:
            all_data_bench = {}
    lanes = dict(bench.get("lanes") or {})
    for lane_key, metrics in all_data_bench.items():
        cur = lanes.get(lane_key) or {}
        if not cur.get("all_time"):
            at = metrics.get("all_time") or {}
            if not at and (metrics.get("real_fills") or metrics.get("net_pnl_real")):
                at = {
                    "real_fills": metrics.get("real_fills"),
                    "net_pnl_real": metrics.get("net_pnl_real"),
                    "ev_usd": metrics.get("per_approve_ev"),
                }
            if at:
                cur = dict(cur)
                cur["all_time"] = at
                lanes[lane_key] = cur
    ledger_file = _read_json("lane_pnl_ledger.json")
    ledger = ledger_file.get("lanes") or {}
    lane_def = _read_report("lane_definition_report.json")
    benchmark_lane = str(bench.get("benchmark_lane") or COMPARISON_BENCHMARK_LANE or BENCHMARK_LANE)
    bench_metrics = lanes.get(benchmark_lane) or {}
    benchmark_pnl = float(bench_metrics.get("net_pnl_real") or bench_metrics.get("net_pnl_usd") or 0)
    benchmark_ev = float(bench_metrics.get("per_approve_ev") or 0)

    lane_def_by_lane = {}
    for row in lane_def.get("lanes") or []:
        if isinstance(row, dict) and row.get("lane"):
            lane_def_by_lane[str(row["lane"])] = row

    all_keys = set(ALL_PATHWAY_LANES)
    all_keys.update(lanes.keys())
    all_keys.update(ledger.keys())
    for row in lane_def.get("lanes") or []:
        if isinstance(row, dict) and row.get("lane"):
            all_keys.add(str(row["lane"]))
    for ln in lane_def.get("retired_lanes") or []:
        all_keys.add(str(ln))
    for ln in lane_def.get("active_roster") or []:
        all_keys.add(str(ln))

    status_by_lane = {}
    for row in lane_def.get("lanes") or []:
        if isinstance(row, dict) and row.get("lane"):
            status_by_lane[str(row["lane"])] = row.get("pathway_status") or ""

    rows = []
    for lane in sorted(all_keys):
        m = lanes.get(lane) or {}
        lb = ledger.get(lane) or {}
        ld = lane_def_by_lane.get(lane) or {}
        all_time = m.get("all_time") or {}
        if not all_time:
            ad = all_data_bench.get(lane) or {}
            all_time = ad.get("all_time") or {}
            if not all_time and (ad.get("real_fills") or ad.get("net_pnl_real")):
                all_time = {
                    "real_fills": ad.get("real_fills"),
                    "net_pnl_real": ad.get("net_pnl_real"),
                    "ev_usd": ad.get("per_approve_ev"),
                }
        fills = int(m.get("real_fills") or m.get("fills") or lb.get("closes") or ld.get("sample_size") or 0)
        approves = int(m.get("approves") or ld.get("approves") or 0)
        shadow_filled = int(m.get("shadow_filled") or 0)
        shadow_pnl = float(m.get("net_pnl_shadow_blocked") or 0)
        shadow_fill_pct = float(m.get("shadow_fill_pct") or 0)
        costly_blocks = float(m.get("costly_blocks_usd") or 0)
        good_blocks_saved = float(m.get("good_blocks_saved_usd") or 0)
        pnl = float(m.get("net_pnl_real") or m.get("net_pnl_usd") or lb.get("net_pnl_usd") or ld.get("pnl_usd") or 0)
        ev = float(m.get("per_approve_ev") or ld.get("ev_per_approve") or 0)
        at_fills = int(all_time.get("real_fills") or ld.get("all_time_fills") or 0)
        at_pnl = float(all_time.get("net_pnl_real") or ld.get("all_time_pnl_usd") or 0)
        at_ev = float(all_time.get("ev_usd") or (at_pnl / at_fills if at_fills else 0))
        pathway_status = status_by_lane.get(lane) or ld.get("pathway_status") or ""
        is_retired = pathway_status in ("RETIRED", "DATA_RETIRED", "BENCHMARK") or lane in (lane_def.get("retired_lanes") or [])
        is_shadow = pathway_status == "SHADOW_COLLECTING"
        is_benchmark = lane == benchmark_lane or pathway_status == "BENCHMARK"
        has_any_data = (
            fills or approves or pnl or at_fills or at_pnl
            or shadow_filled or abs(shadow_pnl) > 0.01
        )
        if lane in ALL_PATHWAY_LANES:
            pass  # always show full catalog
        elif not has_any_data and lane != "AI_SCAN" and lane != benchmark_lane:
            continue
        compare_pnl = pnl if fills else at_pnl
        compare_ev = ev if approves else at_ev
        if is_benchmark:
            status = "BENCHMARK"
        elif lane == PRIMARY_PRODUCTION_LANE:
            status = "PRIMARY_PRODUCTION"
        elif is_retired and at_fills:
            status = "HISTORICAL"
        elif is_shadow:
            status = "SHADOW_COLLECTING"
        elif is_retired:
            status = pathway_status or "DATA_RETIRED"
        elif compare_ev >= benchmark_ev and compare_pnl > benchmark_pnl and lane != benchmark_lane:
            status = "BEATS BENCHMARK"
        elif compare_pnl < benchmark_pnl or (benchmark_ev and compare_ev < benchmark_ev * 0.85):
            status = "UNDERPERFORMING"
        else:
            status = "NEUTRAL"
        rows.append({
            "lane": lane,
            "trades": fills,
            "approves": approves,
            "shadow_filled": shadow_filled,
            "shadow_fill_pct": round(shadow_fill_pct, 1),
            "shadow_pnl": round(shadow_pnl, 2),
            "costly_blocks_usd": round(costly_blocks, 2),
            "good_blocks_saved_usd": round(good_blocks_saved, 2),
            "wr": None,
            "pnl": round(pnl, 2),
            "ev": round(ev, 2),
            "all_time_fills": at_fills,
            "all_time_pnl": round(at_pnl, 2),
            "all_time_ev": round(at_ev, 2),
            "status": status,
            "pathway_status": pathway_status or status,
            "verdict": m.get("verdict") or "",
            "retired": is_retired,
        })
    rows.sort(key=lambda x: (-(x["all_time_pnl"] if x["all_time_fills"] else x["pnl"]), x["lane"]))
    return rows, benchmark_pnl


def _chase_payload():
    attr = _read_report("chase_attribution_report.json")
    eff = _read_report("chase_effectiveness_report.json")
    threshold = _read_report("chase_threshold_report.json")
    delay = _read_report("chase_delay_report.json")
    totals = attr.get("overnight_watch") or attr.get("totals") or {}
    buckets = eff.get("buckets") or {}
    bucket_rows = []
    if isinstance(buckets, dict):
        for key, b in buckets.items():
            if int((b or {}).get("trades") or 0):
                bucket_rows.append({"bucket": key, **(b or {})})
    threshold_rows = []
    for key, b in (threshold.get("thresholds") or {}).items():
        if int((b or {}).get("trades") or 0):
            threshold_rows.append({"threshold": key, **(b or {})})
    return {
        "totals": totals,
        "buckets": bucket_rows,
        "thresholds": threshold_rows,
        "threshold_question": threshold.get("question"),
        "delay_lanes": (delay.get("lanes") or {}),
        "delay_verdict": delay.get("verdict"),
        "delay_delta": delay.get("delta_chase_3plus_vs_continuous"),
        "question": eff.get("question"),
    }


def _combos_payload():
    rep = _read_report("top_combinations_report.json")
    top = rep.get("top") or []
    return {
        "generated_at": rep.get("generated_at"),
        "total_combos": rep.get("total_combos", len(top)),
        "min_trades": rep.get("min_trades_per_combo"),
        "dimensions": rep.get("dimensions") or [],
        "top": top[:50],
    }


def _typeb_payload():
    rep = _read_report("type_b_predictor_report.json")
    cohorts = rep.get("cohorts") or {}
    cohort_rows = []
    for key in ("TYPE_A", "TYPE_B", "MIXED"):
        c = cohorts.get(key) or {}
        if c.get("trades"):
            cohort_rows.append({"cohort": key, **c})
    return {
        "generated_at": rep.get("generated_at"),
        "classification": rep.get("classification"),
        "cohorts": cohort_rows,
        "separators": (rep.get("top_separators") or rep.get("separators_ranked") or [])[:12],
        "rules": rep.get("predictor_rules") or rep.get("rules") or [],
    }


def _chase_threshold_payload():
    rep = _read_report("chase_threshold_report.json")
    rows = []
    for key, block in (rep.get("thresholds") or {}).items():
        if int((block or {}).get("trades") or 0):
            rows.append({"threshold": key, **(block or {})})
    return {
        "generated_at": rep.get("generated_at"),
        "question": rep.get("question"),
        "thresholds": rows,
    }


def _chase_delay_payload():
    rep = _read_report("chase_delay_report.json")
    lanes_map = rep.get("lanes") or {}
    lane_order = rep.get("lane_order") or list(lanes_map.keys())
    lane_rows = []
    for key in lane_order:
        block = lanes_map.get(key) or {}
        if block:
            lane_rows.append({"lane": key, **block})
    delta = (
        rep.get("delta_chase_vs_direct_primary")
        or rep.get("delta_chase_3plus_vs_continuous")
        or {}
    )
    return {
        "generated_at": rep.get("generated_at"),
        "question": rep.get("question"),
        "verdict": rep.get("verdict"),
        "benchmark_lane": rep.get("benchmark_lane") or BENCHMARK_LANE,
        "direct_reference_lane": rep.get("direct_reference_lane"),
        "delta": delta,
        "lanes": lane_rows,
    }


def _chase_iso_payload():
    rep = _read_report("lane_chase_isolation_report.json")
    primary = rep.get("primary_pair") or {}
    direct = primary.get("direct") or rep.get("continuous_benchmark") or {}
    chase = primary.get("chase") or rep.get("urgent_chase_alpha") or {}
    return {
        "generated_at": rep.get("generated_at"),
        "verdict": rep.get("verdict"),
        "notes": rep.get("isolation_notes") or [],
        "continuous": direct,
        "urgent": chase,
        "primary_pair": primary,
        "pairs": rep.get("pairs") or [],
        "direct_lane": primary.get("direct_lane"),
        "chase_lane": primary.get("chase_lane"),
        "direct_label": primary.get("direct_label") or "Direct",
        "chase_label": primary.get("chase_label") or "Chase 3+",
        "global_fill_model": rep.get("global_fill_model") or {},
    }


def _lanes_def_payload():
    rep = _read_report("lane_definition_report.json")
    return {
        "generated_at": rep.get("generated_at"),
        "active_roster": rep.get("active_roster") or [],
        "retired_lanes": rep.get("retired_lanes") or [],
        "lanes": rep.get("lanes") or [],
    }


def _exit_combos_payload():
    rep = _read_report("exit_combinations_report.json")
    return {
        "generated_at": rep.get("generated_at"),
        "benchmark_lane": rep.get("benchmark_lane"),
        "overall_left_on_table_usd": rep.get("overall_left_on_table_usd"),
        "total_combos": rep.get("total_combos", 0),
        "top": (rep.get("top") or [])[:50],
        "worst_leakage": (rep.get("worst_leakage") or [])[:30],
    }


def _exit_reason_leak_payload():
    rep = _read_report("exit_leakage_by_reason_report.json")
    return {
        "generated_at": rep.get("generated_at"),
        "overall_left_usd": rep.get("overall_left_usd"),
        "overall_booked_usd": rep.get("overall_booked_usd"),
        "overall_peak_usd": rep.get("overall_peak_usd"),
        "reasons": rep.get("reasons") or [],
    }


def _ladder_sim_payload():
    rep = _read_report("exit_ladder_simulator_report.json")
    return {
        "generated_at": rep.get("generated_at"),
        "actual_realized_usd": rep.get("actual_realized_usd"),
        "actual_trades": rep.get("actual_trades"),
        "replays_available": rep.get("replays_available"),
        "best_profile_id": rep.get("best_profile_id"),
        "profiles": rep.get("profiles") or [],
    }


def _pathway_audit_payload():
    tiles = _read_report("tile_independence_report.json")
    type_b = _read_report("type_b_execution_audit.json")
    ai_scan = _read_report("ai_scan_independence_report.json")
    ai_scan_role = _read_report("ai_scan_role_validation.json")
    lane_mem = _read_report("lane_memory_validation.json")
    lane_mem_violation = _read_report("lane_memory_violation.json")
    runtime_integrity = _read_report("runtime_pathway_integrity.json")
    exit_val = _read_report("exit_reports_validation.json")
    sync = _read_report("repo_version_sync.json")
    bot_sync = _read_report("bot_analyzer_sync.json")
    return {
        "tile_independence": tiles,
        "type_b_audit": type_b,
        "ai_scan_independence": ai_scan,
        "ai_scan_role": ai_scan_role,
        "lane_memory": lane_mem,
        "lane_memory_violation": lane_mem_violation,
        "runtime_pathway_integrity": runtime_integrity,
        "exit_reports_validation": exit_val,
        "version_sync": sync,
        "bot_analyzer_sync": bot_sync,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "expected_analyzer_sync_id": EXPECTED_ANALYZER_SYNC_ID,
        "expected_exchange": "bitfinex",
        "dashboard_version": RESEARCH_DASHBOARD_VERSION,
        "benchmark_lane": BENCHMARK_LANE,
    }


def _horizon_payload():
    rep = _read_json("horizon_profitability_report.json")
    if not rep:
        rep = _read_json(str(Path(REPORTS_DIR) / "horizon_profitability_report.json"))
    recovery = rep.get("recovery_summary") or []
    if not recovery:
        horizons = rep.get("horizons") or {}
        recovery = [
            {
                "horizon": label,
                "recovery_rate_pct": (horizons.get(label) or {}).get("profitable_pct"),
                "profitable": (horizons.get(label) or {}).get("profitable", 0),
                "still_loss": (horizons.get(label) or {}).get("still_loss", 0),
                "unknown": (horizons.get(label) or {}).get("unknown", 0),
                "coverage_pct": (horizons.get(label) or {}).get("coverage_pct"),
                "conclusion_allowed": (horizons.get(label) or {}).get("coverage_pct", 0) >= 80,
            }
            for label in ("5m", "10m", "15m", "30m", "60m", "120m")
        ]
    return {
        "horizons": recovery,
        "losing_trades": rep.get("losing_trades", 0),
        "fast_cut_recovery": rep.get("fast_cut_recovery"),
        "fast_cut_recovery_summary": rep.get("fast_cut_recovery_summary") or [],
        "conclusions_allowed": rep.get("conclusions_allowed", False),
        "max_horizon_coverage_pct": rep.get("max_horizon_coverage_pct"),
        "min_coverage_pct_for_conclusions": rep.get("min_coverage_pct_for_conclusions", 80),
        "note": rep.get("note"),
    }


def _leakage_payload():
    rep = _read_json("top_leakage_report.json") or _read_json(str(Path(REPORTS_DIR) / "top_leakage_report.json"))
    leak = _read_json("scenario_c_leakage_report.json") or _read_json(str(Path(REPORTS_DIR) / "scenario_c_leakage_report.json"))
    return {
        "overall_left_usd": rep.get("overall_left_usd") or (leak.get("overall") or {}).get("left_on_table_usd"),
        "by_exit_reason": rep.get("by_exit_reason") or {},
        "trades": rep.get("trades") or [],
    }


def _lane_retirement_payload():
    rep = _read_json("lane_retirement_report.json") or _read_json(str(Path(REPORTS_DIR) / "lane_retirement_report.json"))
    return {
        "lanes": rep.get("lanes") or [],
        "retire_candidates": rep.get("retire_candidates") or [],
    }


def _feature_payload():
    rep = _read_json("feature_importance_report.json") or _read_json(str(Path(REPORTS_DIR) / "feature_importance_report.json"))
    return {"features": rep.get("features") or [], "weak_signals": rep.get("weak_signals") or []}


def _ai_payload():
    cal = _read_json("ai_calibration_report.json")
    funnel = _read_json("ai_funnel_report.json")
    fp = _read_json("ai_decision_fingerprint_report.json")
    conf = _read_json("confidence_band_report.json")
    return {
        "calibration_buckets": cal.get("confidence_buckets") or [],
        "expected_vs_actual": cal.get("expected_vs_actual") or {},
        "feature_attribution": cal.get("feature_attribution") or {},
        "funnel": funnel,
        "fingerprints": fp.get("clusters") or fp.get("fingerprints") or fp,
        "confidence_bands": conf.get("filled_trades_by_band") or [],
    }


def _findings_payload():
    compact = _read_json(COMPACT_SUMMARY_FILE)
    findings = compact.get("key_findings") or []
    hl = compact.get("highlights") or {}
    return {"findings": findings, "highlights": hl, "coverage": compact.get("coverage") or {}}


def _normalize_archive_session(entry, folder_name=None):
    if not isinstance(entry, dict):
        return None
    sid = entry.get("id") or entry.get("session_id") or folder_name
    if not sid:
        return None
    return {
        "id": str(sid),
        "session_id": str(sid),
        "generated_at": entry.get("generated_at"),
        "trades": entry.get("trades"),
        "net_pnl_usd": entry.get("net_pnl_usd"),
        "win_rate_pct": entry.get("win_rate_pct"),
        "path": entry.get("path") or (str(ROOT / ARCHIVE_DIR / sid) if sid else None),
    }


def _archives_index():
    sessions = []
    idx_path = ROOT / ARCHIVE_INDEX_FILE
    if idx_path.is_file():
        raw = _read_json(ARCHIVE_INDEX_FILE, {"sessions": []})
        for entry in raw.get("sessions") or []:
            norm = _normalize_archive_session(entry)
            if norm:
                sessions.append(norm)
    else:
        arch_root = ROOT / ARCHIVE_DIR
        if arch_root.is_dir():
            for d in sorted(arch_root.iterdir(), reverse=True):
                if d.is_dir():
                    meta = _read_json(str(d / "session_meta.json"), {})
                    norm = _normalize_archive_session(meta, folder_name=d.name)
                    if norm:
                        sessions.append(norm)
    return {"sessions": sessions}


# ---------------------------------------------------------------------------
# Routes — API
# ---------------------------------------------------------------------------
@app.route("/api/status")
def api_status():
    manifest = _read_json(REPORT_MANIFEST_FILE)
    compact = _read_json(COMPACT_SUMMARY_FILE)
    manifest_sync = manifest.get("analyzer_sync_id") or compact.get("analyzer_sync_id")
    sync_ok = manifest_sync == EXPECTED_ANALYZER_SYNC_ID if manifest_sync else None
    return jsonify({
        "ok": True,
        "read_only": True,
        "dashboard_version": RESEARCH_DASHBOARD_VERSION,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "expected_analyzer_sync_id": EXPECTED_ANALYZER_SYNC_ID,
        "benchmark_lane": BENCHMARK_LANE,
        "analyzer_sync_match": sync_ok,
        "cwd": str(ROOT),
        "public_url": PUBLIC_URL,
        "analyzer_sync_id": manifest_sync,
        "generated_at": manifest.get("generated_at") or compact.get("generated_at"),
        "generated_at_melbourne": format_melbourne_dt(manifest.get("generated_at") or compact.get("generated_at")),
        "melbourne_now": format_melbourne_dt(datetime.now(timezone.utc).isoformat()),
        "timezone": "Australia/Melbourne",
        "report_count": len(_manifest_reports()),
        "last_files": {
            "manifest": _file_mtime(REPORT_MANIFEST_FILE),
            "compact": _file_mtime(COMPACT_SUMMARY_FILE),
        },
    })


@app.route("/api/summary")
def api_summary():
    compact = _read_json(COMPACT_SUMMARY_FILE)
    real = _read_json("real_edge_summary.json")
    stale_meta = _summary_stale_meta(compact)
    p = dict(compact.get("performance") or {})
    re = compact.get("real_edge") or real
    if not p.get("trades") and int(re.get("executed") or 0):
        p["trades"] = int(re.get("executed") or 0)
        if p.get("net_pnl_usd") is None and re.get("executed_pnl_usd") is not None:
            p["net_pnl_usd"] = re.get("executed_pnl_usd")
        if p.get("expectancy_usd") is None and re.get("per_approve_ev_executed") is not None:
            p["expectancy_usd"] = re.get("per_approve_ev_executed")
    approves = int(re.get("approve_attempts") or 0)
    executed = int(re.get("executed") or p.get("trades") or 0)
    fill_pct = round(100.0 * executed / approves, 1) if approves else None
    session_empty = not int(p.get("trades") or 0)
    all_data_active = (
        session_empty
        and (ROOT / ALL_DATA_REPORTS_DIR / "top_combinations_report.json").is_file()
    )
    return jsonify({
        "scope": compact.get("session_scope"),
        "data_scope": compact.get("data_scope"),
        "generated_at": compact.get("generated_at"),
        "performance": p,
        "real_edge": re,
        "approve_to_fill_pct": fill_pct,
        "executive_text": _read_text(EXECUTIVE_SUMMARY_FILE),
        "coverage_status": (compact.get("coverage") or {}).get("confidence_status"),
        "stale": stale_meta,
        "all_data_fallback_active": all_data_active,
    })


@app.route("/api/findings")
def api_findings():
    return jsonify(_findings_payload())


@app.route("/api/lanes")
def api_lanes():
    rows, bench_pnl = _lane_rows()
    return jsonify({"lanes": rows, "benchmark_pnl": bench_pnl})


@app.route("/api/chase")
def api_chase():
    return jsonify(_chase_payload())


@app.route("/api/combos")
def api_combos():
    return jsonify(_combos_payload())


@app.route("/api/typeb")
def api_typeb():
    return jsonify(_typeb_payload())


@app.route("/api/chase-threshold")
def api_chase_threshold():
    return jsonify(_chase_threshold_payload())


@app.route("/api/chase-delay")
def api_chase_delay():
    return jsonify(_chase_delay_payload())


@app.route("/api/chase-iso")
def api_chase_iso():
    return jsonify(_chase_iso_payload())


@app.route("/api/lanes-def")
def api_lanes_def():
    return jsonify(_lanes_def_payload())


@app.route("/api/exit-combos")
def api_exit_combos():
    return jsonify(_exit_combos_payload())


@app.route("/api/exit-reason-leak")
def api_exit_reason_leak():
    return jsonify(_exit_reason_leak_payload())


@app.route("/api/ladder-sim")
def api_ladder_sim():
    return jsonify(_ladder_sim_payload())


@app.route("/api/pathway-audit")
def api_pathway_audit():
    return jsonify(_pathway_audit_payload())


@app.route("/api/horizon")
def api_horizon():
    return jsonify(_horizon_payload())


@app.route("/api/leakage")
def api_leakage():
    return jsonify(_leakage_payload())


@app.route("/api/lane-retirement")
def api_lane_retirement():
    return jsonify(_lane_retirement_payload())


@app.route("/api/features")
def api_features():
    return jsonify(_feature_payload())


@app.route("/api/ai")
def api_ai():
    return jsonify(_ai_payload())


@app.route("/api/manifest")
def api_manifest():
    return jsonify(_read_json(REPORT_MANIFEST_FILE))


@app.route("/api/report/<path:filename>")
def api_report(filename):
    safe = os.path.basename(filename)
    for base in (ROOT, ROOT / REPORTS_DIR):
        path = base / safe
        if path.is_file() and path.suffix.lower() == ".json":
            try:
                with open(path, encoding="utf-8") as f:
                    return jsonify(json.load(f))
            except Exception:
                abort(500)
    abort(404)


@app.route("/api/archives")
def api_archives():
    return jsonify(_archives_index())


_RESEARCH_ARTIFACT_NAMES = frozenset({
    EXECUTIVE_SUMMARY_FILE,
    HIGHLIGHTS_FILE,
    FINDINGS_FILE,
    COVERAGE_FILE,
    DEEP_DIVE_INDEX_FILE,
    ANALYSIS_DASHBOARD_HTML,
    ANALYZER_LOG_FILE,
    COMPACT_SUMMARY_FILE,
    REPORT_MANIFEST_FILE,
})


def _serve_research_artifact(filename: str):
    safe = os.path.basename(filename)
    if safe not in _RESEARCH_ARTIFACT_NAMES:
        abort(404)
    path = ROOT / safe
    if not path.is_file():
        abort(404)
    if safe.endswith(".html"):
        mimetype = "text/html"
    elif safe.endswith(".json"):
        mimetype = "application/json"
    elif safe.endswith(".log"):
        mimetype = "text/plain"
    else:
        mimetype = "text/plain"
    as_attachment = request.args.get("download") in ("1", "true", "yes")
    return send_file(
        path,
        mimetype=mimetype,
        as_attachment=as_attachment,
        download_name=safe if as_attachment else None,
    )


@app.route("/research_highlights.txt")
def artifact_highlights():
    return _serve_research_artifact(HIGHLIGHTS_FILE)


@app.route("/research_findings.txt")
def artifact_findings():
    return _serve_research_artifact(FINDINGS_FILE)


@app.route("/research_coverage.txt")
def artifact_coverage():
    return _serve_research_artifact(COVERAGE_FILE)


@app.route("/research_deep_dive_index.txt")
def artifact_deep_dive_index():
    return _serve_research_artifact(DEEP_DIVE_INDEX_FILE)


@app.route("/executive_summary.txt")
def artifact_executive_summary():
    return _serve_research_artifact(EXECUTIVE_SUMMARY_FILE)


@app.route("/analysis_dashboard.html")
def artifact_analysis_dashboard():
    return _serve_research_artifact(ANALYSIS_DASHBOARD_HTML)


@app.route("/analyzer_run.log")
def artifact_analyzer_log():
    return _serve_research_artifact(ANALYZER_LOG_FILE)


@app.route("/download/reports")
def download_reports():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in _bundle_paths():
            arcname = path.name if path.parent == ROOT else f"{REPORTS_DIR}/{path.name}"
            zf.write(path, arcname=arcname)
        zf.writestr(
            "README.txt",
            f"Research reports bundle\nGenerated by research dashboard\nRoot: {ROOT}\n",
        )
    buf.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"reports_bundle_{stamp}.zip",
    )


@app.route("/download/archive/<session_id>")
def download_archive(session_id):
    safe = os.path.basename(session_id)
    arch = ROOT / ARCHIVE_DIR / safe
    if not arch.is_dir():
        abort(404)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in arch.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(arch)))
    buf.seek(0)
    return send_file(buf, mimetype="application/zip", as_attachment=True, download_name=f"{safe}.zip")


@app.route("/download/all-sessions")
def download_all_sessions():
    """One ZIP: every session archive + live reports + CSVs + CONTINUOUS timeline."""
    try:
        from build_complete_session_bundle import build_bundle, FINAL_BOTS_ROOT, DEFAULT_AGENT_ROOT
    except ImportError:
        abort(503, description="build_complete_session_bundle.py not found next to research_dashboard.py")
    history = HISTORY_ROOT if (HISTORY_ROOT / ARCHIVE_DIR).is_dir() else FINAL_BOTS_ROOT
    live = ROOT if (ROOT / "trades_3factor.csv").is_file() or (ROOT / REPORTS_DIR).is_dir() else DEFAULT_AGENT_ROOT
    out_dir = history / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / COMPLETE_BUNDLE_NAME
    build_bundle(history, live, out_path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return send_file(
        out_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"trading_sessions_complete_{stamp}.zip",
    )


@app.route("/download/complete")
def download_complete_cached():
    """Serve pre-built complete bundle if it exists (no rebuild)."""
    for base in (HISTORY_ROOT, ROOT, ROOT.parent):
        for name in (COMPLETE_BUNDLE_NAME,) + COMPLETE_BUNDLE_FALLBACKS:
            candidate = Path(base) / "downloads" / name
            if candidate.is_file():
                try:
                    with zipfile.ZipFile(candidate) as zf:
                        if zf.testzip() is not None:
                            continue
                except zipfile.BadZipFile:
                    continue
                stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                return send_file(
                    candidate,
                    mimetype="application/zip",
                    as_attachment=True,
                    download_name=f"trading_sessions_complete_{stamp}.zip",
                )
    return download_all_sessions()


@app.route("/download/chatgpt")
def download_chatgpt_bundle():
    """ChatGPT-safe bundle: CSV + key reports + manifest (atomic ZIP, verified)."""
    agent_root = ROOT.parent if (ROOT.parent / "trades_3factor.csv").is_file() else ROOT
    if str(agent_root) not in sys.path:
        sys.path.insert(0, str(agent_root))
    try:
        from build_chatgpt_research_bundle import build, OUT_DIR, ZIP_NAME
    except ImportError as exc:
        abort(503, description=f"build_chatgpt_research_bundle.py not found: {exc}")
    for base in (RESEARCH_ROOT if (RESEARCH_ROOT := agent_root / "research").is_dir() else ROOT, ROOT, agent_root):
        candidate = base / "downloads" / ZIP_NAME
        if candidate.is_file() and candidate.stat().st_size > 10_000:
            try:
                with zipfile.ZipFile(candidate) as zf:
                    if zf.testzip() is None:
                        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                        return send_file(
                            candidate,
                            mimetype="application/zip",
                            as_attachment=True,
                            download_name=f"chatgpt_research_bundle_{stamp}.zip",
                        )
            except zipfile.BadZipFile:
                pass
    try:
        out_zip, _ = build(agent_root=agent_root)
    except Exception as exc:
        abort(500, description=f"ChatGPT bundle build failed: {exc}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return send_file(
        out_zip,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"chatgpt_research_bundle_{stamp}.zip",
    )


@app.route("/api/accumulator")
def api_accumulator():
    try:
        from research_trade_accumulator import build_status

        return jsonify(build_status(root=ROOT))
    except ImportError:
        abort(503)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/download/accumulator")
def download_accumulator():
    """Week-collection DB export: SQLite + accumulated CSV + status JSON."""
    try:
        from research_trade_accumulator import (
            ACCUMULATOR_DIR,
            DB_NAME,
            EXPORT_CSV,
            STATUS_FILE,
            sync_accumulator,
        )
    except ImportError:
        abort(503)
    sync_accumulator(root=ROOT)
    acc_dir = ROOT / ACCUMULATOR_DIR
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in (DB_NAME, EXPORT_CSV, STATUS_FILE):
            p = acc_dir / name
            if p.is_file():
                zf.write(p, arcname=name)
        zf.writestr(
            "README.txt",
            "research_accumulator — v9.83+ week collection (no historical backfill)\n"
            "Updated each analyzer run (~30 min). Start with research_accumulator_status.json\n",
        )
    buf.seek(0)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"research_accumulator_{stamp}.zip",
    )


# ---------------------------------------------------------------------------
# Main UI
# ---------------------------------------------------------------------------
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Research Dashboard</title>
<style>
  :root {
    --bg: #0b0f14; --panel: #141b24; --border: #243041; --text: #e8eef4;
    --muted: #8b9aab; --accent: #5eb8ff; --green: #3dd68c; --red: #ff6b6b; --amber: #f0b429;
  }
  * { box-sizing: border-box; }
  body { margin: 0; font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); }
  header { padding: 16px 24px; border-bottom: 1px solid var(--border); display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
  header h1 { margin: 0; font-size: 1.25rem; }
  .meta { color: var(--muted); font-size: 0.85rem; }
  .badge { background: var(--panel); border: 1px solid var(--border); padding: 4px 10px; border-radius: 999px; font-size: 0.75rem; }
  .badge.ok { border-color: var(--green); color: var(--green); }
  nav { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 24px; border-bottom: 1px solid var(--border); background: #0e1319; }
  nav button { background: var(--panel); color: var(--text); border: 1px solid var(--border); padding: 8px 14px; border-radius: 8px; cursor: pointer; font-size: 0.85rem; }
  nav button.active { border-color: var(--accent); color: var(--accent); }
  main { padding: 20px 24px; max-width: 1200px; }
  section { display: none; }
  section.active { display: block; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 12px; margin-bottom: 20px; }
  .kpi { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
  .kpi .lbl { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; }
  .kpi .val { font-size: 1.4rem; font-weight: 700; margin-top: 4px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; margin-top: 12px; }
  th, td { border: 1px solid var(--border); padding: 8px 10px; text-align: left; }
  th { background: var(--panel); }
  tr:nth-child(even) { background: #101820; }
  .green { color: var(--green); font-weight: 600; }
  .red { color: var(--red); font-weight: 600; }
  .amber { color: var(--amber); }
  pre { background: var(--panel); border: 1px solid var(--border); padding: 12px; border-radius: 8px; overflow: auto; font-size: 0.8rem; white-space: pre-wrap; }
  .btn { display: inline-block; background: var(--accent); color: #001018; padding: 12px 20px; border-radius: 8px; text-decoration: none; font-weight: 700; margin: 8px 8px 8px 0; }
  .btn.secondary { background: var(--panel); color: var(--text); border: 1px solid var(--border); }
  .grid2 { display: grid; grid-template-columns: 280px 1fr; gap: 16px; }
  @media (max-width: 800px) { .grid2 { grid-template-columns: 1fr; } }
  ul.findings li { margin-bottom: 8px; line-height: 1.4; }
  .explorer-list { list-style: none; padding: 0; margin: 0; max-height: 70vh; overflow: auto; }
  .explorer-list li { padding: 8px 10px; border-bottom: 1px solid var(--border); cursor: pointer; font-size: 0.85rem; }
  .explorer-list li:hover { background: var(--panel); }
  .explorer-list li.sel { background: #1a2838; color: var(--accent); }
  h2 { font-size: 1rem; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
  .note { color: var(--muted); font-size: 0.8rem; }
  .stale-banner { background: #3d1f1f; border: 1px solid #f85149; color: #ffb4b4; padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; font-size: 0.9rem; }
</style></head><body>
<div id="stale-banner" class="stale-banner" style="display:none;"></div>
<header>
  <div>
    <h1>Research Dashboard</h1>
    <div class="meta">Read-only · analyzer outputs · <span id="scope">loading…</span></div>
  </div>
  <div>
    <span class="badge ok" id="health">READ-ONLY</span>
    <span class="badge" id="sync">—</span>
    <span class="badge" id="melb-clock" title="Australia/Melbourne">Melbourne —</span>
    <span class="badge" id="updated">—</span>
  </div>
</header>
<nav id="nav"></nav>
<main>
  <section id="sec-summary" class="active">
    <h2>Executive Summary</h2>
    <div class="kpis" id="kpis"></div>
    <pre id="exec-text"></pre>
    <p class="note">Auto-refreshes every 60s. Analyzer (30 min loop): <code>python analyzer_research_engine_v62.py</code></p>
  </section>
  <section id="sec-findings">
    <h2>Research Findings</h2>
    <div class="kpis" id="hl-kpis"></div>
    <ol class="findings" id="findings-list"></ol>
  </section>
  <section id="sec-lanes">
    <h2>Lane Laboratory</h2>
    <p class="note">All lanes in code — real fills from live tiles; shadow cols = virtual sim (blocked counterfactual + shadow-collecting lanes). Green = beats benchmark on comparable PnL/EV.</p>
    <table><thead><tr><th>Lane</th><th>Appr</th><th>Sess Fills</th><th>Shadow</th><th>Sess PnL</th><th>Shadow PnL</th><th>EV/appr</th><th>All Fills</th><th>All PnL</th><th>Role</th></tr></thead><tbody id="lane-body"></tbody></table>
  </section>
  <section id="sec-lanes-retire">
    <h2>Lane Retirement Engine</h2>
    <p class="note">Automatic KEEP / RETIRE / COLLECT MORE — removes guesswork on pathway lanes.</p>
    <table><thead><tr><th>Lane</th><th>Sess Trades</th><th>All Trades</th><th>Sess PnL</th><th>All PnL</th><th>EV/appr</th><th>Recommendation</th><th>Reason</th></tr></thead><tbody id="retire-body"></tbody></table>
  </section>
  <section id="sec-lanes-def">
    <h2>Lane Definitions</h2>
    <p class="note" id="lanes-def-note">Active roster, entry conditions, and research questions per pathway lane.</p>
    <div class="kpis" id="lanes-def-kpis"></div>
    <table><thead><tr><th>Lane</th><th>Status</th><th>Sess Fills</th><th>All Fills</th><th>Sess PnL</th><th>All PnL</th><th>EV/appr</th><th>Role</th></tr></thead><tbody id="lanes-def-body"></tbody></table>
  </section>
  <section id="sec-regime">
    <h2>Regime Leaderboard</h2>
    <p class="note" id="regime-note">Recommend-only — best lane per weekend/weekday × ADX × spread bucket.</p>
    <div class="kpis" id="regime-kpis"></div>
    <table><thead><tr><th>Regime</th><th>Trades</th><th>Best lane</th><th>Best EV</th><th>2nd lane</th><th>OK?</th></tr></thead><tbody id="regime-body"></tbody></table>
    <h3>Roster policy (recommend only)</h3>
    <pre id="roster-policy-json">Loading…</pre>
  </section>
  <section id="sec-regime-conf">
    <h2>Regime × Confidence Matrix</h2>
    <p class="note" id="regime-conf-note">Which AI confidence band wins in each market regime (recommend-only).</p>
    <div class="kpis" id="regime-conf-kpis"></div>
    <table><thead><tr><th>Regime</th><th>Band</th><th>Trades</th><th>WR%</th><th>EV</th><th>PnL</th><th>OK?</th></tr></thead><tbody id="regime-conf-body"></tbody></table>
  </section>
  <section id="sec-chase">
    <h2>Chase Analytics</h2>
    <div class="kpis" id="chase-kpis"></div>
    <table><thead><tr><th>Bucket</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th></tr></thead><tbody id="chase-body"></tbody></table>
  </section>
  <section id="sec-chase-threshold">
    <h2>Chase Threshold Analysis</h2>
    <p class="note" id="chase-threshold-note">Cumulative limit_chase_count thresholds — when does EV turn positive?</p>
    <table><thead><tr><th>Threshold</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th></tr></thead><tbody id="chase-threshold-body"></tbody></table>
  </section>
  <section id="sec-chase-delay">
    <h2>Chase Delay (Pathway Lab)</h2>
    <p class="note" id="chase-delay-note">COMBO Direct vs Chase 3+ — delayed virtual-chase entry within each AI/spread tier.</p>
    <div class="kpis" id="chase-delay-kpis"></div>
    <table><thead><tr><th>Lane</th><th>Approves</th><th>Fills</th><th>Fill%</th><th>WR%</th><th>PnL</th><th>EV/appr</th><th>EV/trade</th><th>Avg age(s)</th></tr></thead><tbody id="chase-delay-body"></tbody></table>
  </section>
  <section id="sec-chase-iso">
    <h2>Chase Isolation</h2>
    <p class="note" id="chase-iso-note">COMBO Direct vs Chase 3+ — fill_model and chase policy per tile pair.</p>
    <div class="kpis" id="chase-iso-kpis"></div>
    <ul class="findings" id="chase-iso-notes"></ul>
    <table id="chase-iso-table"><thead><tr><th>Metric</th><th id="chase-iso-direct-h">Direct</th><th id="chase-iso-chase-h">Chase 3+</th></tr></thead><tbody id="chase-iso-body"></tbody></table>
  </section>
  <section id="sec-combos">
    <h2>Top Combinations</h2>
    <p class="note" id="combos-note">Best AI × spread × TYPE × lane combos (min trades filter).</p>
    <div class="kpis" id="combos-kpis"></div>
    <table><thead><tr><th>Combo</th><th>AI</th><th>Spread</th><th>Entry</th><th>Lane</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th></tr></thead><tbody id="combos-body"></tbody></table>
  </section>
  <section id="sec-pathway-audit">
    <h2>Pathway Audit</h2>
    <p class="note">Startup validation — TYPE_B not an entry gate, tile independence, version sync, exit reports.</p>
    <div class="kpis" id="audit-kpis"></div>
    <h3>Tile independence tests</h3>
    <table><thead><tr><th>Test</th><th>Pass</th><th>Detail</th></tr></thead><tbody id="audit-tile-body"></tbody></table>
    <h3>AI scan pipeline independence</h3>
    <table><thead><tr><th>Test</th><th>Pass</th><th>Detail</th></tr></thead><tbody id="audit-aiscan-body"></tbody></table>
    <h3>AI scan role (coordinator-only)</h3>
    <table><thead><tr><th>Check</th><th>Pass</th><th>Detail</th></tr></thead><tbody id="audit-aiscan-role-body"></tbody></table>
    <h3>Runtime pathway integrity</h3>
    <table><thead><tr><th>Issue</th><th>Severity</th></tr></thead><tbody id="audit-runtime-body"></tbody></table>
    <h3>TYPE_B execution audit</h3>
    <table><thead><tr><th>Check</th><th>Pass</th><th>Detail</th></tr></thead><tbody id="audit-typeb-body"></tbody></table>
  </section>
  <section id="sec-typeb">
    <h2>Type B Predictor</h2>
    <p class="note" id="typeb-note">TYPE_A vs TYPE_B vs MIXED cohorts — MFE-based trade classification.</p>
    <div class="kpis" id="typeb-kpis"></div>
    <h3>Cohorts</h3>
    <table><thead><tr><th>Cohort</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th></tr></thead><tbody id="typeb-cohort-body"></tbody></table>
    <h3>Top separators (TYPE_A vs TYPE_B)</h3>
    <table><thead><tr><th>Feature</th><th>TYPE_A mean</th><th>TYPE_B mean</th><th>|Δ|</th><th>Direction</th></tr></thead><tbody id="typeb-sep-body"></tbody></table>
  </section>
  <section id="sec-exit-combos">
    <h2>Exit Combinations</h2>
    <p class="note" id="exit-combos-note">Exit reason × AI × spread × peak MFE × time-in-trade × TYPE × lane.</p>
    <div class="kpis" id="exit-combos-kpis"></div>
    <h3>Best exit combos (by EV)</h3>
    <table><thead><tr><th>Combo</th><th>Exit</th><th>AI</th><th>Spread</th><th>MFE</th><th>Time</th><th>Type</th><th>Lane</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Left</th></tr></thead><tbody id="exit-combos-body"></tbody></table>
    <h3>Worst leakage cohorts</h3>
    <table><thead><tr><th>Combo</th><th>Exit</th><th>N</th><th>Left on table</th><th>Avg left</th><th>EV</th></tr></thead><tbody id="exit-leak-body"></tbody></table>
  </section>
  <section id="sec-exit-reason-leak">
    <h2>Leakage by Exit Reason</h2>
    <p class="note" id="exit-reason-note">Which exit path destroys the most value — sorted by total left on table.</p>
    <div class="kpis" id="exit-reason-kpis"></div>
    <table><thead><tr><th>Exit reason</th><th>N</th><th>Left $</th><th>Avg left $</th><th>Avg MFE%</th><th>Realized%</th><th>Leak%</th><th>Capture%</th></tr></thead><tbody id="exit-reason-body"></tbody></table>
  </section>
  <section id="sec-ladder-sim">
    <h2>Optimal Ladder Simulator</h2>
    <p class="note" id="ladder-sim-note">Tick replay of executed trades — compare alternate ladder rungs vs current live vs actual booked PnL.</p>
    <div class="kpis" id="ladder-sim-kpis"></div>
    <table><thead><tr><th>Profile</th><th>Ladder rungs</th><th>N sim</th><th>Sum PnL</th><th>Avg PnL</th><th>WR%</th><th>Ladder exit%</th><th>Δ vs actual</th></tr></thead><tbody id="ladder-sim-body"></tbody></table>
  </section>
  <section id="sec-exits">
    <h2>Exit Leakage Report</h2>
    <p class="note">Per trade: peak MFE vs realized vs leakage — sorted by money left on table.</p>
    <div class="kpis" id="leak-kpis"></div>
    <table><thead><tr><th>Trade</th><th>Lane</th><th>Exit</th><th>Peak MFE%</th><th>Realized%</th><th>Leak%</th><th>Realized $</th><th>Peak $</th><th>Left $</th></tr></thead><tbody id="leak-body"></tbody></table>
  </section>
  <section id="sec-horizon">
    <h2>Horizon Recovery</h2>
    <p class="note" id="horizon-note">Would losing trades have been green N minutes after exit?</p>
    <table><thead><tr><th>Horizon</th><th>Green</th><th>Still loss</th><th>Unknown</th><th>Coverage</th><th>Recovery %</th></tr></thead><tbody id="horizon-body"></tbody></table>
    <h3>Fast Cut recovery</h3>
    <table><thead><tr><th>Horizon</th><th>Green</th><th>Still loss</th><th>Coverage</th><th>Recovery %</th></tr></thead><tbody id="horizon-fc-body"></tbody></table>
  </section>
  <section id="sec-ai">
    <h2>AI Laboratory</h2>
    <h3>Confidence calibration</h3>
    <table><thead><tr><th>Band</th><th>N</th><th>WR%</th><th>PnL</th></tr></thead><tbody id="ai-cal-body"></tbody></table>
    <h3>Executed confidence bands</h3>
    <table><thead><tr><th>Band</th><th>N</th><th>WR%</th><th>PnL</th></tr></thead><tbody id="ai-conf-body"></tbody></table>
  </section>
  <section id="sec-edge">
    <h2>Edge &amp; Feature Importance</h2>
    <p class="note">Pearson correlation with PnL — validation only, not for auto-tuning.</p>
    <table><thead><tr><th>Feature</th><th>|r|</th><th>Correlation</th><th>N</th></tr></thead><tbody id="feat-body"></tbody></table>
    <p class="note" id="weak-signals"></p>
  </section>
  <section id="sec-explorer">
    <h2>Report Explorer</h2>
    <div class="grid2">
      <ul class="explorer-list" id="report-list"></ul>
      <pre id="report-json">Select a report…</pre>
    </div>
  </section>
  <section id="sec-archives">
    <h2>Session Archive</h2>
    <p class="note">One archive folder per analyzer run (when enabled).</p>
    <table><thead><tr><th>Session</th><th>Time</th><th>Trades</th><th>PnL</th><th>Download</th></tr></thead><tbody id="archive-body"></tbody></table>
  </section>
  <section id="sec-download">
    <h2>Download Center</h2>
    <p><b>Complete history</b> — all session archives, CONTINUOUS timeline, weekend vs weekday breakdown, live reports, CSVs.</p>
    <a class="btn" href="/download/all-sessions" id="dl-all-sessions">⬇ All Sessions ZIP (full rebuild)</a>
    <a class="btn secondary" href="/download/complete" id="dl-complete">⬇ Complete Bundle (cached)</a>
    <p style="margin-top:16px"><b>ChatGPT upload</b> — small verified ZIP with trade counts manifest (use this instead of the 38MB archive).</p>
    <a class="btn" href="/download/chatgpt" id="dl-chatgpt">⬇ ChatGPT Research Bundle</a>
    <p style="margin-top:16px"><b>Week collection DB</b> — auto-updated every analyzer run (~30 min), v9.83+ trades only.</p>
    <a class="btn secondary" href="/download/accumulator" id="dl-accumulator">⬇ Accumulator DB + CSV</a>
    <a class="btn secondary" href="/api/accumulator" target="_blank">View accumulator status JSON</a>
    <p style="margin-top:16px"><b>Research layers</b> — text summaries from the latest analyzer run.</p>
    <a class="btn secondary" href="/research_highlights.txt?download=1">⬇ research_highlights.txt</a>
    <a class="btn secondary" href="/research_findings.txt?download=1">⬇ research_findings.txt</a>
    <a class="btn secondary" href="/research_coverage.txt?download=1">⬇ research_coverage.txt</a>
    <a class="btn secondary" href="/research_deep_dive_index.txt?download=1">⬇ research_deep_dive_index.txt</a>
    <a class="btn secondary" href="/analysis_dashboard.html?download=1">⬇ analysis_dashboard.html</a>
    <a class="btn secondary" href="/analyzer_run.log?download=1">⬇ analyzer_run.log</a>
    <p style="margin-top:16px"><b>Latest snapshot only</b> — current analyzer run reports.</p>
    <a class="btn" href="/download/reports" id="dl-zip">⬇ Latest Reports ZIP</a>
    <a class="btn secondary" href="/api/manifest" target="_blank">View report_manifest.json</a>
    <pre id="bundle-list"></pre>
  </section>
</main>
<script>
const NAV = {{ nav_json|safe }};
const navEl = document.getElementById('nav');
NAV.forEach(([id, label]) => {
  const b = document.createElement('button');
  b.textContent = label;
  b.onclick = () => show(id);
  b.dataset.sec = id;
  navEl.appendChild(b);
});
function show(id) {
  document.querySelectorAll('main section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.sec === id));
  const sec = document.getElementById('sec-' + id);
  if (sec) sec.classList.add('active');
  try { localStorage.setItem('research_dashboard_prefs_v1', JSON.stringify({activeSection: id})); } catch (e) {}
}
let _rdPrefs = {};
try { _rdPrefs = JSON.parse(localStorage.getItem('research_dashboard_prefs_v1') || '{}'); } catch (e) {}
show(_rdPrefs.activeSection || 'summary');

function fmtUsd(v) { return v == null ? 'n/a' : (v >= 0 ? '+' : '') + Number(v).toFixed(2); }
function fmtMelb(iso) {
  if (!iso) return '—';
  try {
    const d = new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z');
    return new Intl.DateTimeFormat('en-AU', { timeZone: 'Australia/Melbourne', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hour12:false, timeZoneName:'short' }).format(d).replace(',', '');
  } catch (e) { return iso.slice(0,19); }
}

async function loadSummary() {
  const r = await fetch('/api/summary');
  const d = await r.json();
  const p = d.performance || {};
  const re = d.real_edge || {};
  const stale = d.stale || {};
  const banner = document.getElementById('stale-banner');
  if (banner) {
    if (stale.stale) {
      const reasons = (stale.reasons || []).join(' · ');
      banner.style.display = 'block';
      banner.innerHTML = '<strong>⚠ Stale report — not current session data.</strong> '
        + reasons
        + '<br>Dashboard reads saved JSON files; it does not re-run the analyzer. '
        + 'Run: <code>python analyzer_research_engine_v62.py</code> from Final Bots '
        + '(runs now, then every 30 min).';
    } else if (d.all_data_fallback_active) {
      banner.style.display = 'block';
      banner.style.background = '#1f2d3d';
      banner.style.borderColor = '#58a6ff';
      banner.style.color = '#c9d1d9';
      banner.innerHTML = '<strong>ℹ Using fresh-collection window from reports/all_data/</strong> '
        + '(full CSV since last Fresh Collection ON). Run analyzer after bot restart if sections look empty.';
    } else {
      banner.style.display = 'none';
    }
  }
  const scopeLabel = d.all_data_fallback_active
    ? 'FRESH COLLECTION · reports/all_data fallback'
    : (d.scope || 'ALL-DATA') + ' · ' + (d.data_scope || '').toUpperCase();
  document.getElementById('scope').textContent = scopeLabel;
  document.getElementById('updated').textContent = d.generated_at ? d.generated_at.slice(0, 19) : 'no run yet';
  document.getElementById('exec-text').textContent = d.executive_text || '(Run analyzer first)';
  const kpis = [
    ['Net PnL', '$' + fmtUsd(p.net_pnl_usd)],
    ['Win Rate', (p.win_rate_pct ?? 'n/a') + '%'],
    ['Trades', p.trades ?? 0],
    ['EV/trade', '$' + (p.expectancy_usd ?? 'n/a')],
    ['MFE Capture', (p.mfe_capture_pct ?? 'n/a') + '%'],
    ['APPROVE→Fill', (d.approve_to_fill_pct ?? 'n/a') + '%'],
    ['Gate Damage', '$' + fmtUsd(re.gate_damage_usd)],
    ['Sample', d.coverage_status || 'n/a'],
  ];
  document.getElementById('kpis').innerHTML = kpis.map(([l,v]) =>
    `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
}

async function loadFindings() {
  const r = await fetch('/api/findings');
  const d = await r.json();
  const hl = d.highlights || {};
  const hlk = [
    ['Top Lane', (hl.best_lane||{}).lane || 'n/a'],
    ['Worst Lane', (hl.worst_lane||{}).lane || 'n/a'],
    ['Best Conf', (hl.best_confidence||{}).bucket || 'n/a'],
    ['Edge corr', hl.edge_correlation ?? 'n/a'],
  ];
  document.getElementById('hl-kpis').innerHTML = hlk.map(([l,v]) =>
    `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('findings-list').innerHTML = (d.findings||[]).map(f => `<li>${f}</li>`).join('') || '<li>Run analyzer to generate findings.</li>';
}

async function loadLanes() {
  const r = await fetch('/api/lanes');
  const d = await r.json();
  document.getElementById('lane-body').innerHTML = (d.lanes||[]).map(row => {
    let cls = '';
    if (row.pathway_status === 'SHADOW_COLLECTING') cls = 'amber';
    else if (row.retired || (row.pathway_status || '').includes('RETIRED')) cls = 'amber';
    else if (row.status === 'UNDERPERFORMING') cls = 'red';
    else if (row.status === 'BEATS BENCHMARK' || row.status === 'PRIMARY_PRODUCTION') cls = 'green';
    const role = row.pathway_status || (row.retired ? 'RETIRED' : row.status);
    const atF = row.all_time_fills || 0;
    const atP = row.all_time_pnl || 0;
    const sh = row.shadow_filled || 0;
    const shPnl = row.shadow_pnl || 0;
    return `<tr class="${cls}"><td>${row.lane}</td><td>${row.approves ?? 0}</td><td>${row.trades}</td><td>${sh}${row.shadow_fill_pct ? ' ('+row.shadow_fill_pct+'%)' : ''}</td><td>$${fmtUsd(row.pnl)}</td><td class="${shPnl>=0?'green':'red'}">$${fmtUsd(shPnl)}</td><td>$${fmtUsd(row.ev)}</td><td>${atF || '—'}</td><td>${atF ? '$'+fmtUsd(atP) : '—'}</td><td>${role}</td></tr>`;
  }).join('') || '<tr><td colspan="10">Run analyzer: python analyzer_research_engine_v62.py</td></tr>';
}

async function loadChase() {
  const r = await fetch('/api/chase');
  const d = await r.json();
  const t = d.totals || {};
  const ck = [
    ['Assisted', (t.chase_assisted_fills||0) + '/' + (t.total_fills||0)],
    ['Saved', t.saved_fills_heuristic||0],
    ['TTL expired', t.ttl_expired||0],
  ];
  document.getElementById('chase-kpis').innerHTML = ck.map(([l,v]) =>
    `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('chase-body').innerHTML = (d.buckets||[]).map(b =>
    `<tr><td>${b.bucket}</td><td>${b.trades}</td><td>${b.win_rate_pct}%</td><td>$${fmtUsd(b.sum_pnl_usd)}</td><td>$${fmtUsd(b.ev_usd)}</td></tr>`).join('');
}

async function loadCombos() {
  const r = await fetch('/api/combos');
  const d = await r.json();
  const note = document.getElementById('combos-note');
  if (note) note.textContent = `Best AI × spread × TYPE × lane combos (min ${d.min_trades ?? 3} trades, ${d.total_combos ?? 0} total).`;
  document.getElementById('combos-kpis').innerHTML = [
    ['Total combos', d.total_combos ?? 0],
    ['Shown', (d.top||[]).length],
    ['Dimensions', (d.dimensions||[]).join(', ') || 'n/a'],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('combos-body').innerHTML = (d.top||[]).map(c => {
    const cls = (c.ev_usd ?? 0) >= 2 ? 'green' : '';
    return `<tr class="${cls}"><td>${c.combo||''}</td><td>${c.ai_bucket||''}</td><td>${c.spread_bucket||''}</td><td>${c.entry_mode||c.type||''}</td><td>${c.lane||''}</td><td>${c.trades||0}</td><td>${c.wr_pct ?? 'n/a'}%</td><td>$${fmtUsd(c.pnl_usd)}</td><td>$${fmtUsd(c.ev_usd)}</td></tr>`;
  }).join('') || '<tr><td colspan="9">No combo data — run analyzer first.</td></tr>';
}

async function loadTypeB() {
  const r = await fetch('/api/typeb');
  const d = await r.json();
  const note = document.getElementById('typeb-note');
  if (note) note.textContent = d.classification || 'TYPE_A vs TYPE_B vs MIXED cohorts.';
  const tb = (d.cohorts||[]).find(c => c.cohort === 'TYPE_B') || {};
  document.getElementById('typeb-kpis').innerHTML = [
    ['TYPE_B trades', tb.trades ?? 0],
    ['TYPE_B WR', (tb.wr_pct ?? 'n/a') + '%'],
    ['TYPE_B EV', '$' + fmtUsd(tb.ev_usd)],
    ['TYPE_B PnL', '$' + fmtUsd(tb.pnl_usd)],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('typeb-cohort-body').innerHTML = (d.cohorts||[]).map(c => {
    const cls = c.cohort === 'TYPE_B' ? 'green' : (c.cohort === 'TYPE_A' ? 'red' : '');
    return `<tr class="${cls}"><td>${c.cohort}</td><td>${c.trades||0}</td><td>${c.wr_pct ?? 'n/a'}%</td><td>$${fmtUsd(c.pnl_usd)}</td><td>$${fmtUsd(c.ev_usd)}</td></tr>`;
  }).join('') || '<tr><td colspan="5">No cohort data.</td></tr>';
  document.getElementById('typeb-sep-body').innerHTML = (d.separators||[]).map(s =>
    `<tr><td>${s.feature||''}</td><td>${s.type_a_mean ?? 'n/a'}</td><td>${s.type_b_mean ?? 'n/a'}</td><td>${s.delta_abs ?? 'n/a'}</td><td>${s.direction||''}</td></tr>`).join('') ||
    '<tr><td colspan="5">No separator data.</td></tr>';
}

async function loadChaseThreshold() {
  const r = await fetch('/api/chase-threshold');
  const d = await r.json();
  const note = document.getElementById('chase-threshold-note');
  if (note) note.textContent = d.question || 'Cumulative limit_chase_count thresholds.';
  document.getElementById('chase-threshold-body').innerHTML = (d.thresholds||[]).map(t => {
    const wr = t.wr_pct ?? t.wr ?? 'n/a';
    const ev = t.ev_usd ?? t.ev ?? 'n/a';
    const pnl = t.pnl_usd ?? t.pnl ?? 0;
    const cls = (Number(ev) >= 0.8) ? 'green' : '';
    return `<tr class="${cls}"><td>${t.threshold||''}</td><td>${t.trades||0}</td><td>${wr}%</td><td>$${fmtUsd(pnl)}</td><td>$${fmtUsd(ev)}</td></tr>`;
  }).join('') || '<tr><td colspan="5">No threshold data.</td></tr>';
}

async function loadChaseDelay() {
  const r = await fetch('/api/chase-delay');
  const d = await r.json();
  const note = document.getElementById('chase-delay-note');
  if (note) note.textContent = (d.question || '') + (d.verdict ? ` · Verdict: ${d.verdict}` : '');
  const delta = d.delta || {};
  document.getElementById('chase-delay-kpis').innerHTML = [
    ['Verdict', d.verdict || 'n/a'],
    ['Δ EV/appr', fmtUsd(delta.ev_per_approve)],
    ['Δ PnL', '$' + fmtUsd(delta.pnl_usd)],
    ['Δ fill%', (delta.fill_pct ?? 'n/a') + (delta.fill_pct != null ? '%' : '')],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('chase-delay-body').innerHTML = (d.lanes||[]).map(row => {
    const lane = row.lane || '';
    const cls = lane === (d.benchmark_lane || '') ? 'amber' : (lane === (d.direct_reference_lane || '') ? 'green' : '');
    const label = row.label ? `${lane} · ${row.label}` : lane;
    return `<tr class="${cls}"><td>${label}</td><td>${row.approves ?? 0}</td><td>${row.fills ?? 0}</td><td>${row.fill_pct ?? 'n/a'}%</td><td>${row.wr_pct ?? 'n/a'}%</td><td>$${fmtUsd(row.pnl_usd)}</td><td>$${fmtUsd(row.ev_per_approve)}</td><td>$${fmtUsd(row.ev_usd)}</td><td>${row.avg_signal_age_sec ?? 'n/a'}</td></tr>`;
  }).join('') || '<tr><td colspan="9">No delay report data.</td></tr>';
}

async function loadChaseIso() {
  const r = await fetch('/api/chase-iso');
  const d = await r.json();
  const note = document.getElementById('chase-iso-note');
  if (note) note.textContent = `Verdict: ${d.verdict || 'n/a'} — COMBO Direct vs Chase 3+ per tile pair.`;
  const cont = d.continuous || {};
  const urg = d.urgent || {};
  const directH = document.getElementById('chase-iso-direct-h');
  const chaseH = document.getElementById('chase-iso-chase-h');
  if (directH) directH.textContent = d.direct_label || d.direct_lane || 'Direct';
  if (chaseH) chaseH.textContent = d.chase_label || d.chase_lane || 'Chase 3+';
  document.getElementById('chase-iso-kpis').innerHTML = [
    ['Verdict', d.verdict || 'n/a'],
    ['Direct EV', '$' + fmtUsd(cont.ev_usd)],
    ['Chase EV', '$' + fmtUsd(urg.ev_usd)],
    ['Global fill model', JSON.stringify(d.global_fill_model || {})],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('chase-iso-notes').innerHTML = (d.notes||[]).map(n => `<li>${n}</li>`).join('') || '<li>No isolation notes.</li>';
  const metrics = [
    ['Trades', cont.trades, urg.trades],
    ['Win rate %', cont.wr_pct, urg.wr_pct],
    ['PnL', '$' + fmtUsd(cont.pnl_usd), '$' + fmtUsd(urg.pnl_usd)],
    ['EV/trade', '$' + fmtUsd(cont.ev_usd), '$' + fmtUsd(urg.ev_usd)],
    ['Avg chase count', cont.avg_chase_count, urg.avg_chase_count],
    ['Avg signal age (s)', cont.avg_signal_age_sec, urg.avg_signal_age_sec],
    ['Chase policy', cont.chase_policy || '', urg.chase_policy || ''],
  ];
  document.getElementById('chase-iso-body').innerHTML = metrics.map(([m, c, u]) =>
    `<tr><td>${m}</td><td>${c ?? 'n/a'}</td><td>${u ?? 'n/a'}</td></tr>`).join('');
}

async function loadLaneDefs() {
  const r = await fetch('/api/lanes-def');
  const d = await r.json();
  const roster = (d.active_roster||[]).join(', ') || 'n/a';
  const retired = (d.retired_lanes||[]).join(', ') || 'none';
  document.getElementById('lanes-def-note').textContent = `Active: ${roster} · Retired: ${retired}`;
  document.getElementById('lanes-def-kpis').innerHTML = [
    ['Active lanes', (d.active_roster||[]).length],
    ['Retired', (d.retired_lanes||[]).length],
    ['Defined', (d.lanes||[]).length],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('lanes-def-body').innerHTML = (d.lanes||[]).map(row => {
    const st = row.pathway_status || '';
    const cls = st.includes('RETIRED') ? 'red' : (st.includes('BENCHMARK') ? 'green' : '');
    const atF = row.all_time_fills ?? 0;
    const atP = row.all_time_pnl_usd ?? 0;
    return `<tr class="${cls}"><td>${row.lane||''}</td><td>${st}</td><td>${row.sample_size ?? 0}</td><td>${atF || '—'}</td><td>$${fmtUsd(row.pnl_usd)}</td><td>${atF ? '$'+fmtUsd(atP) : '—'}</td><td>$${fmtUsd(row.ev_per_approve)}</td><td>${row.role||''}</td></tr>`;
  }).join('') || '<tr><td colspan="8">No lane definitions.</td></tr>';
}

async function loadExitCombos() {
  const r = await fetch('/api/exit-combos');
  const d = await r.json();
  document.getElementById('exit-combos-kpis').innerHTML = [
    ['Total combos', d.total_combos ?? 0],
    ['Left on table', '$' + fmtUsd(d.overall_left_on_table_usd)],
    ['Benchmark', d.benchmark_lane || 'n/a'],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('exit-combos-body').innerHTML = (d.top||[]).map(c =>
    `<tr><td>${c.combo||''}</td><td>${c.exit_reason||''}</td><td>${c.ai_bucket||''}</td><td>${c.spread_bucket||''}</td><td>${c.peak_mfe_bucket||''}</td><td>${c.time_in_trade_bucket||''}</td><td>${c.type||''}</td><td>${c.lane||''}</td><td>${c.trades||0}</td><td>${c.wr_pct??'n/a'}%</td><td>$${fmtUsd(c.pnl_usd)}</td><td>$${fmtUsd(c.ev_usd)}</td><td class="red">$${fmtUsd(c.left_on_table_usd)}</td></tr>`).join('') || '<tr><td colspan="13">Run analyzer for exit combos.</td></tr>';
  document.getElementById('exit-leak-body').innerHTML = (d.worst_leakage||[]).map(c =>
    `<tr><td>${c.combo||''}</td><td>${c.exit_reason||''}</td><td>${c.trades||0}</td><td class="red">$${fmtUsd(c.left_on_table_usd)}</td><td>$${fmtUsd(c.avg_left_usd)}</td><td>$${fmtUsd(c.ev_usd)}</td></tr>`).join('') || '<tr><td colspan="6">No leakage data.</td></tr>';
}

async function loadExitReasonLeak() {
  const r = await fetch('/api/exit-reason-leak');
  const d = await r.json();
  document.getElementById('exit-reason-kpis').innerHTML = [
    ['Total left', '$' + fmtUsd(d.overall_left_usd)],
    ['Booked', '$' + fmtUsd(d.overall_booked_usd)],
    ['Peak', '$' + fmtUsd(d.overall_peak_usd)],
    ['Exit reasons', (d.reasons||[]).length],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('exit-reason-body').innerHTML = (d.reasons||[]).map(r =>
    `<tr><td>${r.exit_reason||''}</td><td>${r.trades||0}</td><td class="red">$${fmtUsd(r.left_on_table_usd)}</td><td>$${fmtUsd(r.avg_left_usd)}</td><td>${r.avg_mfe_margin_pct??'n/a'}%</td><td>${r.avg_realized_margin_pct??'n/a'}%</td><td class="red">${r.avg_leakage_margin_pct??'n/a'}%</td><td>${r.capture_ratio_pct??'n/a'}%</td></tr>`
  ).join('') || '<tr><td colspan="8">Run analyzer for exit reason leakage.</td></tr>';
}

async function loadLadderSim() {
  const r = await fetch('/api/ladder-sim');
  const d = await r.json();
  document.getElementById('ladder-sim-kpis').innerHTML = [
    ['Actual PnL', '$' + fmtUsd(d.actual_realized_usd)],
    ['Actual trades', d.actual_trades ?? 0],
    ['Replays', d.replays_available ?? 0],
    ['Best profile', d.best_profile_id || 'n/a'],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('ladder-sim-body').innerHTML = (d.profiles||[]).map(p => {
    const rungs = (p.ladder||[]).map(x => x[0] + '→' + x[1]).join(' · ');
    const best = p.profile_id === d.best_profile_id ? ' class="green"' : '';
    return `<tr${best}><td><strong>${p.profile_id||''}</strong><br><span style="font-size:0.85em;color:var(--muted)">${p.label||''}</span></td><td style="font-size:0.85em">${rungs}</td><td>${p.trades_simulated||0}</td><td>$${fmtUsd(p.sum_pnl_usd)}</td><td>$${fmtUsd(p.avg_pnl_usd)}</td><td>${p.wr_pct??'n/a'}%</td><td>${p.ladder_exit_pct??'n/a'}%</td><td>$${fmtUsd(p.delta_vs_actual_usd)}</td></tr>`;
  }).join('') || '<tr><td colspan="8">Run analyzer — needs signal_replay.jsonl tick data.</td></tr>';
}

async function loadPathwayAudit() {
  const r = await fetch('/api/pathway-audit');
  const d = await r.json();
  const ti = d.tile_independence || {};
  const tb = d.type_b_audit || {};
  const ai = d.ai_scan_independence || {};
  const air = d.ai_scan_role || {};
  const lm = d.lane_memory || {};
  const lmv = d.lane_memory_violation || {};
  const rpi = d.runtime_pathway_integrity || {};
  const ev = d.exit_reports_validation || {};
  const vs = d.version_sync || {};
  const bas = d.bot_analyzer_sync || {};
  document.getElementById('audit-kpis').innerHTML = [
    ['Dashboard', d.dashboard_version || 'n/a'],
    ['Bot expected', d.expected_bot_version || 'n/a'],
    ['Analyzer expected', d.expected_analyzer_sync_id || 'n/a'],
    ['Exchange', d.expected_exchange || 'bitfinex'],
    ['Bot↔Analyzer', bas.verdict || 'n/a'],
    ['Tile independence', ti.verdict || 'n/a'],
    ['AI scan path', ai.verdict || 'n/a'],
    ['AI scan role', air.verdict || 'n/a'],
    ['Runtime integrity', rpi.verdict || 'n/a'],
    ['TYPE_B audit', tb.verdict || 'n/a'],
    ['Exit reports', ev.verdict || 'n/a'],
    ['Lane memory', lmv.verdict || lm.verdict || 'n/a'],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  const passCell = ok => ok ? '<span class="green">PASS</span>' : '<span class="red">FAIL</span>';
  document.getElementById('audit-tile-body').innerHTML = (ti.tests||[]).map(t =>
    `<tr><td>${t.test||''}</td><td>${passCell(t.passed)}</td><td>${t.detail||''}</td></tr>`
  ).join('') || '<tr><td colspan="3">Run bot startup to generate tile_independence_report.json</td></tr>';
  document.getElementById('audit-aiscan-body').innerHTML = (ai.tests||[]).map(t =>
    `<tr><td>${t.test||''}</td><td>${passCell(t.passed)}</td><td>${t.detail||''}</td></tr>`
  ).join('') || '<tr><td colspan="3">Run bot startup to generate ai_scan_independence_report.json</td></tr>';
  document.getElementById('audit-aiscan-role-body').innerHTML = (air.checks||[]).map(c =>
    `<tr><td>${c.check||''}</td><td>${passCell(c.passed)}</td><td>${c.detail||''}</td></tr>`
  ).join('') || '<tr><td colspan="3">Run bot startup to generate ai_scan_role_validation.json</td></tr>';
  const runtimeRows = (rpi.critical_issues||[]).map(i => `<tr><td>${i}</td><td class="red">CRITICAL</td></tr>`)
    .concat((rpi.issues||[]).filter(i => !(rpi.critical_issues||[]).includes(i)).map(i => `<tr><td>${i}</td><td class="amber">WARN</td></tr>`));
  document.getElementById('audit-runtime-body').innerHTML = runtimeRows.join('')
    || `<tr><td>${rpi.verdict ? 'Last check: '+rpi.verdict : 'No runtime checks yet — bot runs validate_runtime_pathway_integrity every 10m'}</td><td>—</td></tr>`;
  document.getElementById('audit-typeb-body').innerHTML = (tb.checks||[]).map(c =>
    `<tr><td>${c.check||''}</td><td>${passCell(c.passed)}</td><td>${c.detail||''}</td></tr>`
  ).join('') || '<tr><td colspan="3">Run bot startup to generate type_b_execution_audit.json</td></tr>';
}

async function loadHorizon() {
  const r = await fetch('/api/horizon');
  const d = await r.json();
  const note = document.getElementById('horizon-note');
  if (note) {
    note.textContent = d.conclusions_allowed
      ? (d.note || 'Coverage sufficient for recovery conclusions.')
      : `⚠ Coverage ${d.max_horizon_coverage_pct ?? 0}% — recovery rates hidden until ≥${d.min_coverage_pct_for_conclusions ?? 80}%. ${d.note || ''}`;
    note.style.color = d.conclusions_allowed ? '' : 'var(--amber)';
  }
  const row = h => {
    const rate = h.conclusion_allowed === false || h.recovery_rate_pct == null ? 'n/a' : `${h.recovery_rate_pct}%`;
    return `<tr><td>${h.horizon}</td><td>${h.profitable||0}</td><td>${h.still_loss||0}</td><td>${h.unknown||0}</td><td>${h.coverage_pct ?? 'n/a'}%</td><td>${rate}</td></tr>`;
  };
  document.getElementById('horizon-body').innerHTML = (d.horizons||[]).map(row).join('') ||
    '<tr><td colspan="6">Run analyzer — needs losing trades + post-exit replay ticks</td></tr>';
  const fc = d.fast_cut_recovery_summary || [];
  document.getElementById('horizon-fc-body').innerHTML = fc.map(h => {
    const rate = h.conclusion_allowed === false || h.recovery_rate_pct == null ? 'n/a' : `${h.recovery_rate_pct}%`;
    return `<tr><td>${h.horizon}</td><td>${h.profitable||0}</td><td>${h.still_loss||0}</td><td>${h.coverage_pct ?? 'n/a'}%</td><td>${rate}</td></tr>`;
  }).join('') || '<tr><td colspan="5">No Fast Cut recovery data yet</td></tr>';
}

async function loadLeakage() {
  const r = await fetch('/api/leakage');
  const d = await r.json();
  document.getElementById('leak-kpis').innerHTML = [
    ['Left on table', '$' + fmtUsd(d.overall_left_usd)],
    ['Top trades shown', (d.trades||[]).length],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('leak-body').innerHTML = (d.trades||[]).slice(0,100).map(t =>
    `<tr><td>${(t.trade_id||'').slice(0,12)}</td><td>${t.lane||''}</td><td>${t.exit_reason||''}</td><td>${t.mfe_margin_pct??'n/a'}%</td><td>${t.realized_margin_pct??'n/a'}%</td><td class="red">${t.leakage_margin_pct??'n/a'}%</td><td>$${fmtUsd(t.realized_usd)}</td><td>$${fmtUsd(t.peak_profit_usd)}</td><td class="red">$${fmtUsd(t.left_on_table_usd)}</td></tr>`).join('');
}

async function loadRetirement() {
  const r = await fetch('/api/lane-retirement');
  const d = await r.json();
  document.getElementById('retire-body').innerHTML = (d.lanes||[]).map(row => {
    const cls = row.recommendation === 'RETIRE' ? 'red' : (row.recommendation.startsWith('KEEP') ? 'green' : 'amber');
    const atF = row.all_time_fills ?? 0;
    const atP = row.all_time_pnl_usd ?? 0;
    return `<tr><td>${row.lane}</td><td>${row.trades}</td><td>${atF || '—'}</td><td>$${fmtUsd(row.pnl_usd)}</td><td>${atF ? '$'+fmtUsd(atP) : '—'}</td><td>$${fmtUsd(row.ev_per_approve)}</td><td class="${cls}">${row.recommendation}</td><td>${row.reason||''}</td></tr>`;
  }).join('');
}

async function loadRegimeConf() {
  const r = await fetch('/api/report/regime_confidence_matrix.json');
  const d = await r.json();
  document.getElementById('regime-conf-note').textContent = d.usage_note || '';
  document.getElementById('regime-conf-kpis').innerHTML = [
    ['Total trades', d.total_trades ?? 'n/a'],
    ['Matrix cells', (d.cells||[]).length],
    ['Regimes', (d.regime_summaries||[]).length],
  ].map(([k,v]) => `<div class="kpi"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  document.getElementById('regime-conf-body').innerHTML = (d.cells||[]).filter(c => c.trades).map(c => {
    const ok = c.sample_ok ? '✓' : '—';
    const cls = c.sample_ok && (c.ev_usd||0) > 0 ? 'green' : (c.sample_ok ? 'red' : '');
    return `<tr><td>${c.regime}</td><td>${c.confidence_band}</td><td>${c.trades}</td><td>${c.win_rate_pct}%</td><td>$${fmtUsd(c.ev_usd)}</td><td>$${fmtUsd(c.sum_pnl_usd)}</td><td class="${cls}">${ok}</td></tr>`;
  }).join('') || '<tr><td colspan="7">No matrix data yet.</td></tr>';
}

async function loadRegime() {
  const [rr, rp] = await Promise.all([
    fetch('/api/report/regime_leaderboard.json'),
    fetch('/api/report/roster_policy.json'),
  ]);
  const d = rr.ok ? await rr.json() : {};
  const pol = rp.ok ? await rp.json() : {};
  document.getElementById('regime-note').textContent = d.usage_note || document.getElementById('regime-note').textContent;
  document.getElementById('regime-kpis').innerHTML = [
    ['Total trades tagged', d.total_trades ?? 'n/a'],
    ['Regime cells', (d.regimes||[]).length],
    ['Min trades/cell', d.min_trades_per_cell ?? 3],
    ['Policy action', pol.action || 'n/a'],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('regime-body').innerHTML = (d.regimes||[]).map(r => {
    const ok = r.conclusion_allowed ? 'yes' : 'no';
    const cls = r.conclusion_allowed ? 'green' : 'amber';
    return `<tr><td>${r.regime}</td><td>${r.total_trades}</td><td>${r.best_lane||'—'}</td><td>$${fmtUsd(r.best_ev_usd)}</td><td>${r.second_lane||'—'}</td><td class="${cls}">${ok}</td></tr>`;
  }).join('');
  document.getElementById('roster-policy-json').textContent = JSON.stringify(pol, null, 2);
  if (pol.collection_progress) {
    const cp = pol.collection_progress;
    document.getElementById('regime-kpis').innerHTML += [
      ['Collection progress', cp.progress_pct + '%'],
      ['Target trades', cp.target_trades],
    ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  }
}

async function loadFeatures() {
  const r = await fetch('/api/features');
  const d = await r.json();
  document.getElementById('feat-body').innerHTML = (d.features||[]).map(f =>
    `<tr><td>${f.feature}</td><td>${f.abs_correlation}</td><td>${f.correlation_with_pnl>=0?'+':''}${f.correlation_with_pnl}</td><td>${f.n}</td></tr>`).join('');
  document.getElementById('weak-signals').textContent = d.weak_signals?.length ?
    'Weak signals (|r|<0.05): ' + d.weak_signals.join(', ') : '';
}

async function loadAI() {
  const r = await fetch('/api/ai');
  const d = await r.json();
  const row = b => `<tr><td>${b.bucket}</td><td>${b.trades}</td><td>${b.win_rate_pct}%</td><td>$${fmtUsd(b.sum_pnl_usd)}</td></tr>`;
  document.getElementById('ai-cal-body').innerHTML = (d.calibration_buckets||[]).filter(b=>b.trades).map(row).join('');
  document.getElementById('ai-conf-body').innerHTML = (d.confidence_bands||[]).filter(b=>b.trades).map(row).join('');
}

async function loadExplorer() {
  const r = await fetch('/api/manifest');
  const d = await r.json();
  document.getElementById('sync').textContent = d.analyzer_sync_id || 'no manifest';
  const list = document.getElementById('report-list');
  list.innerHTML = '';
  (d.reports||[]).forEach((entry, i) => {
    const file = entry.file || entry;
    const title = entry.title || file;
    const li = document.createElement('li');
    li.textContent = title;
    li.onclick = async () => {
      list.querySelectorAll('li').forEach(x => x.classList.remove('sel'));
      li.classList.add('sel');
      const rr = await fetch('/api/report/' + encodeURIComponent(file));
      const j = await rr.json();
      document.getElementById('report-json').textContent = JSON.stringify(j, null, 2);
    };
    if (i === 0) li.click();
    list.appendChild(li);
  });
}

async function loadArchives() {
  const r = await fetch('/api/archives');
  const d = await r.json();
  document.getElementById('archive-body').innerHTML = (d.sessions||[]).map(s => {
    const sid = s.id || s.session_id || '';
    return `<tr><td>${sid}</td><td>${(s.generated_at||'').slice(0,19)}</td><td>${s.trades??'n/a'}</td><td>$${fmtUsd(s.net_pnl_usd)}</td><td><a href="/download/archive/${encodeURIComponent(sid)}">ZIP</a></td></tr>`;
  }).join('') || '<tr><td colspan="5">No archives yet — run analyzer once.</td></tr>';
}

async function loadStatus() {
  const r = await fetch('/api/status');
  const d = await r.json();
  const syncEl = document.getElementById('sync');
  if (syncEl && d.expected_analyzer_sync_id) {
    syncEl.textContent = d.expected_analyzer_sync_id + (d.analyzer_sync_match === true ? ' ✓' : (d.analyzer_sync_match === false ? ' ⚠' : ''));
  }
  const melbEl = document.getElementById('melb-clock');
  if (melbEl && d.melbourne_now) melbEl.textContent = d.melbourne_now;
  return d;
}

async function refreshAll() {
  await loadStatus();
  await loadSummary();
  await loadFindings();
  await loadLanes();
  await loadRetirement();
  await loadLaneDefs();
  await loadRegime();
  await loadRegimeConf();
  await loadChase();
  await loadChaseThreshold();
  await loadChaseDelay();
  await loadChaseIso();
  await loadCombos();
  await loadExitCombos();
  await loadExitReasonLeak();
  await loadLadderSim();
  await loadPathwayAudit();
  await loadTypeB();
  await loadLeakage();
  await loadHorizon();
  await loadFeatures();
  await loadAI();
  await loadExplorer();
  await loadArchives();
}
refreshAll();
setInterval(refreshAll, 60000);
</script></body></html>"""


@app.route("/")
def index():
    nav_json = json.dumps([[a, b] for a, b, _ in REPORT_NAV])
    return render_template_string(
        DASHBOARD_HTML,
        nav_json=nav_json,
        benchmark_lane=BENCHMARK_LANE,
        dashboard_version=RESEARCH_DASHBOARD_VERSION,
    )


def main():
    print("=" * 60)
    print(f"Research Dashboard {RESEARCH_DASHBOARD_VERSION} — READ-ONLY")
    print(f"  Bot sync:     {EXPECTED_BOT_VERSION}")
    print(f"  Analyzer sync: {EXPECTED_ANALYZER_SYNC_ID}")
    print(f"  Benchmark:    {BENCHMARK_LANE}")
    print(f"  Root:   {ROOT}")
    print(f"  Listen: http://{BIND_HOST}:{BIND_PORT}/")
    print(f"  LAN:    {PUBLIC_URL}/")
    print("  Download: /download/reports")
    print("=" * 60)
    app.run(host=BIND_HOST, port=BIND_PORT, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    print("Research dashboard is embedded in analyzer_research_engine_v62.py")
    print("Run:  python analyzer_research_engine_v62.py")
    print("Or:   .\\start_stack.ps1")
    if "--standalone" in sys.argv:
        main()

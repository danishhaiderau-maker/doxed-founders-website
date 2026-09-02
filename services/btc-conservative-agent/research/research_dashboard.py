from __future__ import annotations
import os as _os, sys as _sys
_AD_ = _os.path.abspath(_os.path.dirname(__file__))
_PARENT_ = _os.path.dirname(_AD_)
if _PARENT_ not in _sys.path:
    _sys.path.insert(0, _PARENT_)


import io
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

MELBOURNE_TZ = ZoneInfo("Australia/Melbourne")
from pathway_lane_roster import DASHBOARD_PRIMARY_LANES as _CANONICAL_TILE_LANES
from runtime_incident_history import build_runtime_incident_history

CURRENT_RESEARCH_LANES = frozenset(_CANONICAL_TILE_LANES)


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


def _parse_utc_dt(value):
    """Best-effort aware datetime used only for truthful receipt comparisons."""
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


from flask import Flask, jsonify, render_template_string, send_file, abort, request, make_response

try:
    from collector_v22_schema import RESEARCH_EVENTS_FILE
    from replay_eligibility import validate_replay_eligibility
    from research.best_policy_research import (
        QUALIFICATION_GATE_SCHEMA,
        candidate_contract_blockers,
        qualification_gate_details,
        qualification_gate_blockers,
    )
except ImportError:
    RESEARCH_EVENTS_FILE = "research_events_v22.jsonl"
    validate_replay_eligibility = None
    QUALIFICATION_GATE_SCHEMA = "best_policy_qualification_gates_v2"
    candidate_contract_blockers = lambda candidate: ["CANDIDATE_VALIDATOR_UNAVAILABLE"]
    qualification_gate_blockers = lambda gates: ["QUALIFICATION_GATE_VALIDATOR_UNAVAILABLE"]
    qualification_gate_details = lambda gates, evidence=None, current_generation_available=True: []

try:
    from combo_pathway_config import (
        ANALYZER_SYNC_ID as EXPECTED_ANALYZER_SYNC_ID,
        BENCHMARK_LANE,
        COMPARISON_BENCHMARK_LANE,
        EXECUTION_FIX_VERSION as EXPECTED_BOT_VERSION,
        RESEARCH_DASHBOARD_VERSION,
        ACTIVE_TILE_REGISTRY,
        active_tile_registry_signature,
    )
    from pathway_lane_roster import (
        ANALYZER_COMPARE_LANES,
        DASHBOARD_PATHWAY_LANES,
        DASHBOARD_PRIMARY_LANES,
        is_ai_focused_lane,
    )
    ALL_PATHWAY_LANES = ANALYZER_COMPARE_LANES
except ImportError:
    BENCHMARK_LANE = "CONTINUOUS"
    COMPARISON_BENCHMARK_LANE = "CONTINUOUS"
    EXPECTED_BOT_VERSION = "unknown"
    EXPECTED_ANALYZER_SYNC_ID = "unknown"
    RESEARCH_DASHBOARD_VERSION = "v9.83-quality-roster-4-tiles-2026-06-21"
    ALL_PATHWAY_LANES = tuple(sorted(CURRENT_RESEARCH_LANES))
    DASHBOARD_PATHWAY_LANES = ALL_PATHWAY_LANES
    DASHBOARD_PRIMARY_LANES = ALL_PATHWAY_LANES
    ACTIVE_TILE_REGISTRY = {}

    def active_tile_registry_signature() -> str:
        return "unknown"

    def is_ai_focused_lane(lane: str) -> bool:
        u = str(lane or "").upper().strip()
        if not u:
            return False
        return u in CURRENT_RESEARCH_LANES

def is_ai_focused_lane(lane: str) -> bool:
    return str(lane or "").upper().strip() in CURRENT_RESEARCH_LANES

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
_MODULE_ROOT = Path(os.path.abspath(os.path.dirname(__file__) or os.getcwd()))
_AGENT_ROOT = _MODULE_ROOT.parent
_CWD_ROOT = Path.cwd().resolve()


def _agent_source_root() -> Path:
    """Return the immutable service source root, independent of report cwd.

    Canonical analyzer reports live below ``canonical-research-data/analyzer``.
    Inferring source from ``ROOT`` therefore points bundle builders at derived
    data and makes source-bearing downloads fail even after a valid analyzer
    generation.  The dashboard module itself is installed below the service
    source root, so that location is the stable authority.
    """
    source_root = _AGENT_ROOT.resolve()
    required = (
        source_root / "bot.py",
        source_root / "analyzer_research_engine_v62.py",
        source_root / "research",
    )
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(
            "Analyzer service source root is incomplete: " + ", ".join(missing)
        )
    return source_root


def _approved_non_onedrive_path(value: str | os.PathLike[str] | None) -> Path | None:
    """Resolve a configured path, rejecting stale OneDrive authority."""
    if not value:
        return None
    candidate = Path(value).expanduser().resolve()
    if "onedrive" in {part.casefold() for part in candidate.parts}:
        return None
    return candidate


_configured_data_root = _approved_non_onedrive_path(os.getenv("BTC_AGENT_DATA_DIR"))
_canonical_mirror = _AGENT_ROOT / "canonical-research-data"
if _configured_data_root and _configured_data_root != _canonical_mirror.resolve():
    raise RuntimeError(
        "BTC_AGENT_DATA_DIR must select the repo-contained canonical-research-data store"
    )
DATA_ROOT = _canonical_mirror.resolve()
CURRENT_PATHWAY_RECEIPTS = (
    "tile_independence_report.json",
    "ai_scan_independence_report.json",
    "ai_scan_role_validation.json",
    "lane_memory_validation.json",
    "lane_memory_violation.json",
    "runtime_pathway_integrity.json",
    "exit_reports_validation.json",
)
REQUIRED_ANALYZER_RAW_INPUTS = (
    "research.db",
    "decisions_3factor.csv",
    "pipeline_events_3factor.csv",
    "ai_input_log.jsonl",
    "ai_reason_research.jsonl",
    "ai_edge_disagreement.jsonl",
    "ai_tranche_log.csv",
    "cycle_3m_universe.jsonl",
    "market_microstructure_1s.jsonl",
    "order_multiverse.jsonl",
    "research_events_v22.jsonl",
    "reversal_study.jsonl",
    "signal_replay.jsonl",
    "soft_reject_shadow.jsonl",
    "lane_opportunity_capture.jsonl",
    "execution_settings_history.jsonl",
    "trend_health.csv",
)
CONDITIONAL_ANALYZER_RAW_INPUTS = {
    # A fresh epoch may truthfully have no terminal trades yet.  Absence is
    # recorded explicitly in the bundle instead of being confused with zero
    # PnL or silently treated as a populated cohort.
    "trades_3factor.csv": "NO_TERMINAL_TRADES",
    "chase_offset_touch_grid.jsonl": "NO_COMPRESSED_SHADOW_SCHEDULE_EVENTS",
}
OPTIONAL_ANALYZER_RAW_INPUTS = (
    "blocked_signals_3factor.csv",
    "expired_orders_3factor.csv",
    "execution_funnel.jsonl",
    "trade_lifecycle.jsonl",
    "trade_outcome.jsonl",
    "shadow_outcome.jsonl",
    "shadow_lane_outcome.jsonl",
    "signal_snapshot.jsonl",
    "counterfactual.jsonl",
    "fill_quality.jsonl",
    "source_order_market_evidence.jsonl",
    "edge_census.jsonl",
    "duplicate_intent_audit.jsonl",
    "signal_persist.log",
    "near_edge.log",
)
_APPEND_PREFIX_SNAPSHOT_NAMES = frozenset({
    "research_events_v22.jsonl",
    "decisions_3factor.csv",
    "trades_3factor.csv",
    "expired_orders_3factor.csv",
    "blocked_signals_3factor.csv",
    "ai_tranche_log.csv",
    "setup_log_3factor.csv",
    "candles_3factor.csv",
    "pipeline_events_3factor.csv",
    "ai_errors_3factor.csv",
    "trend_health.csv",
})
# The canonical source lives in ``agent/research`` but the supported launcher
# runs ``agent/analyzer_research_engine_v62.py`` and writes its live reports in
# the agent root.  Resolve the report root from an explicit override first,
# then the launch cwd, and finally the data root.  This prevents :9001 from
# silently serving an older duplicate report set from ``agent/research``.
_REPORT_ROOT_ENV = _approved_non_onedrive_path(os.getenv("BTC_AGENT_REPORT_DIR", "").strip())
if _REPORT_ROOT_ENV:
    ROOT = _REPORT_ROOT_ENV
elif (_CWD_ROOT / "analyzer_research_engine_v62.py").is_file():
    ROOT = _CWD_ROOT
elif (DATA_ROOT / "analyzer_research_engine_v62.py").is_file():
    ROOT = DATA_ROOT
else:
    ROOT = _AGENT_ROOT
_parent = ROOT.parent
BIND_HOST = os.getenv("RESEARCH_DASHBOARD_BIND_HOST", "127.0.0.1")
BIND_PORT = int(os.getenv("RESEARCH_DASHBOARD_PORT", "9001"))
PUBLIC_URL = os.getenv("RESEARCH_DASHBOARD_PUBLIC_URL", f"http://127.0.0.1:{BIND_PORT}")

REPORT_MANIFEST_FILE = "report_manifest.json"
POLICY_EVIDENCE_LIBRARY_MANIFEST_FILE = "policy_evidence_library_manifest.json"
BEST_POLICY_RESEARCH_REPORT_FILE = "best_policy_research_report.json"
SAFE_POLICY_GENOME_V3_REPORT_FILE = "safe_policy_genome_v3_report.json"
CONSERVATIVE_FILL_DESCRIPTIVE_REPORT_FILE = "conservative_fill_descriptive_report.json"
EVIDENCE_COVERAGE_TRIAGE_REPORT_FILE = "evidence_coverage_triage_report.json"
LIFECYCLE_BUNDLE_INVENTORY_REPORT_FILE = "lifecycle_bundle_inventory.json"
DYNAMIC_POLICY_ANALYSIS_REPORT_FILE = "dynamic_policy_analysis_report.json"
COMPACT_SUMMARY_FILE = "research_compact_summary.json"
ANALYZER_INTEGRITY_FILE = "analyzer_integrity_report.json"
EXECUTIVE_SUMMARY_FILE = "executive_summary.txt"
HIGHLIGHTS_FILE = "research_highlights.txt"
FINDINGS_FILE = "research_findings.txt"
COVERAGE_FILE = "research_coverage.txt"
DEEP_DIVE_INDEX_FILE = "research_deep_dive_index.txt"
ANALYSIS_DASHBOARD_HTML = "analysis_dashboard.html"
ANALYZER_LOG_FILE = "analyzer_run.log"
REPORTS_DIR = "reports"
PUBLISHED_REPORTS_DIR = "published_reports"
ALL_DATA_REPORTS_DIR = os.path.join(REPORTS_DIR, "all_data")
HISTORICAL_COHORT_REPORT_FILE = "historical_trade_cohort_report.json"
RETENTION_STATUS_FILE = "research_retention_status.json"
MIRROR_SIZE_REPORT_FILE = "_size_report.json"
ARCHIVE_DIR = "research_session_archives"
ARCHIVE_INDEX_FILE = "research_session_index.json"
PAST_ANALYSIS_DIR = "past_analysis"
ZIP_BUNDLE_NAME = "reports_bundle.zip"
COMPLETE_BUNDLE_NAME = "trading_sessions_complete.zip"
COMPLETE_BUNDLE_FALLBACKS = (
    "trading_sessions_complete_v2.zip",
    "trading_sessions_complete_verified.zip",
)
_parent = ROOT.parent
HISTORY_ROOT = (
    _approved_non_onedrive_path(os.getenv("RESEARCH_HISTORY_ROOT"))
    or (ROOT if (ROOT / ARCHIVE_DIR).is_dir() else (_parent if (_parent / ARCHIVE_DIR).is_dir() else ROOT))
)

REPORT_NAV_GROUPS = (
    ("overview", "Overview", (
        ("summary", "Overview", None),
        ("findings", "Findings", None),
        ("regime", "Regime & ADX", "regime_leaderboard.json"),
    )),
    ("lanes-group", "Lanes & AI", (
        ("lanes", "Current Lanes", "benchmark_vs_lanes_report.json"),
        ("ai", "Direction & Gap", "ai_calibration_report.json"),
    )),
    ("trading-group", "Chase & Exits", (
        ("chase", "Attribution", "chase_attribution_report.json"),
        ("chase-policy-lab", "Chase Policy Lab", "chase_policy_lab_report.json"),
        ("chase-threshold", "Threshold", "chase_threshold_report.json"),
        ("chase-delay", "Delay", "chase_delay_report.json"),
        ("combos", "Top 100 Policy Combos", "top_combinations_report.json"),
        ("spread-perf", "Legacy Gap Performance", "top_combinations_report.json"),
        ("exit-combos", "Exit Combos", "exit_combinations_report.json"),
        ("exit-reason-leak", "Exit Reason Leak", "exit_leakage_by_reason_report.json"),
        ("ladder-sim", "Ladder Simulator", "exit_ladder_simulator_report.json"),
        ("exits", "Historical Exit Leakage", "top_leakage_report.json"),
    )),
    ("deep-group", "Genome & Reports", (
        ("genome", "Safe Policy Genome V3.1", "genome/genome_analysis_report.json"),
        ("research-design", "Entry & Regime Evidence", POLICY_EVIDENCE_LIBRARY_MANIFEST_FILE),
        ("evidence-coverage", "Evidence Coverage", EVIDENCE_COVERAGE_TRIAGE_REPORT_FILE),
        ("edge", "Edge & Features", "feature_importance_report.json"),
        ("explorer", "Report Explorer", None),
        ("archives", "Archives", None),
        ("download", "Downloads", None),
        ("runtime-incidents", "Runtime Incidents", None),
        ("pathway-audit", "Pathway Audit", "tile_independence_report.json"),
        ("horizon", "Historical Recovery", "horizon_profitability_report.json"),
    )),
)

# Flat list for any code that still iterates sections (id, label, report_file).
REPORT_NAV = tuple(item for _gid, _glabel, items in REPORT_NAV_GROUPS for item in items)

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

# Report JSON is immutable between analyzer writes, but several payload builders
# aggregate large historical ledgers. A short server-side cache keeps tab
# navigation responsive and prevents repeated browsers from rereading the same
# files while preserving the live /api/status heartbeat.
_API_CACHE_TTL_SEC = max(5.0, float(os.getenv("RESEARCH_API_CACHE_TTL_SEC", "60")))
_API_RESPONSE_CACHE: dict[str, tuple[float, int, str, bytes]] = {}
_API_CACHE_LOCK = threading.Lock()


def _report_cache_generation_token() -> str:
    """Invalidate cached APIs when reports or the sync/storage receipt changes."""
    parts = []
    for name in (REPORT_MANIFEST_FILE, SAFE_POLICY_GENOME_V3_REPORT_FILE):
        fingerprints = []
        for path in _data_file_candidates(name):
            try:
                stat = path.stat()
            except OSError:
                continue
            fingerprints.append(f"{path}:{stat.st_mtime_ns}:{stat.st_size}")
        parts.append(f"{name}:" + (";".join(fingerprints) if fingerprints else "missing"))
    size_receipt = DATA_ROOT / MIRROR_SIZE_REPORT_FILE
    try:
        size_stat = size_receipt.stat()
        parts.append(
            f"{MIRROR_SIZE_REPORT_FILE}:{size_stat.st_mtime_ns}:{size_stat.st_size}"
        )
    except OSError:
        parts.append(f"{MIRROR_SIZE_REPORT_FILE}:missing")
    return "|".join(parts)


def _read_api_cache_key() -> str:
    return f"{request.full_path}|reports={_report_cache_generation_token()}"


@app.before_request
def _serve_cached_read_api():
    if request.method != "GET" or not request.path.startswith("/api/"):
        return None
    if request.path in ("/api/health", "/api/status", "/api/integrity"):
        return None
    key = _read_api_cache_key()
    now = time.monotonic()
    with _API_CACHE_LOCK:
        item = _API_RESPONSE_CACHE.get(key)
        if item and item[0] > now:
            _expires, status, content_type, body = item
        else:
            if item:
                _API_RESPONSE_CACHE.pop(key, None)
            return None
    response = make_response(body, status)
    response.headers["Content-Type"] = content_type
    response.headers["X-Research-Cache"] = "HIT"
    return response


@app.after_request
def _cache_read_api_response(response):
    # A response returned by ``before_request`` is already a cache hit. Flask
    # still runs ``after_request`` for it, so do not re-store it or relabel the
    # truthful HIT evidence as a MISS.
    if response.headers.get("X-Research-Cache") == "HIT":
        return response
    if (
        request.method == "GET"
        and request.path.startswith("/api/")
        and request.path not in ("/api/health", "/api/status", "/api/integrity")
        and response.status_code == 200
        and response.mimetype == "application/json"
    ):
        body = response.get_data()
        if len(body) <= 5 * 1024 * 1024:
            with _API_CACHE_LOCK:
                _API_RESPONSE_CACHE[_read_api_cache_key()] = (
                    time.monotonic() + _API_CACHE_TTL_SEC,
                    response.status_code,
                    response.content_type,
                    body,
                )
        response.headers["X-Research-Cache"] = "MISS"
    return response
_DASHBOARD_STARTED_AT = datetime.now(timezone.utc)


def _analyzer_run_state() -> dict:
    """Describe the live analyzer pass independently of the last report.

    A fresh analyzer process starts the dashboard before a full report pass is
    complete.  During that window the previous manifest may legitimately have
    an older sync id.  Exposing the live run header lets stack health accept a
    correctly-versioned in-progress pass without accepting a permanently stale
    dashboard.
    """
    path = ROOT / ANALYZER_LOG_FILE
    if not path.is_file():
        return {
            "in_progress": False,
            "phase": "IDLE",
            "sync_id": None,
            "started_at": None,
            "updated_at": None,
            "last_completed_at": None,
            "age_seconds": None,
        }
    header = ""
    tail = ""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            header = handle.readline().strip()
    except Exception:
        pass
    try:
        tail = path.read_bytes()[-16_384:].decode("utf-8", errors="replace")
    except Exception:
        pass
    match = re.search(r"\bsync=([^|\s]+)", header)
    run_sync = match.group(1).strip() if match else None
    started_at = None
    match = re.search(r"# analyzer run ([^|]+)", header)
    if match:
        started_at = match.group(1).strip()
        try:
            started_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            if started_dt.tzinfo is None:
                # Legacy analyzer headers used local Melbourne wall time.
                started_dt = started_dt.replace(tzinfo=MELBOURNE_TZ)
            started_at = started_dt.astimezone(timezone.utc).isoformat()
        except Exception:
            pass
    try:
        updated_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        updated_at = updated_dt.isoformat()
        age_seconds = max(0, int((datetime.now(timezone.utc) - updated_dt).total_seconds()))
    except Exception:
        updated_at = None
        age_seconds = None
    completed = bool(re.search(r"Iteration\s+\d+\s+complete", tail, re.IGNORECASE))
    # A full pass can take several minutes on the home PC. The engine rewrites
    # this log at the start of every pass, so a completion footer means the
    # analyzer is idle between runs, not still analysing for another 45 min.
    in_progress = bool(
        run_sync == EXPECTED_ANALYZER_SYNC_ID
        and age_seconds is not None
        and age_seconds <= 45 * 60
        and not completed
    )
    return {
        "in_progress": in_progress,
        "phase": "RUNNING" if in_progress else ("IDLE_BETWEEN_RUNS" if completed else "IDLE"),
        "sync_id": run_sync,
        "started_at": started_at,
        "updated_at": updated_at,
        "last_completed_at": updated_at if completed else None,
        "age_seconds": age_seconds,
    }


def _load_bot_session():
    for base in (DATA_ROOT, ROOT):
        path = base / "research_session.json"
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return _read_json("research_session.json")


def _integrity_payload() -> dict:
    return _integrity_with_generation_freshness(
        _read_report(ANALYZER_INTEGRITY_FILE) or {}
    )


def _integrity_with_generation_freshness(receipt: dict | None) -> dict:
    rep = dict(receipt or {})
    if not rep:
        rep = {"valid": True, "report_status": "UNKNOWN", "checks": [], "banner": None}
    freshness = _generation_freshness_meta()
    rep["generation_freshness"] = freshness
    if not freshness["current"]:
        rep["valid"] = False
        rep["report_status"] = "STALE_GENERATION"
        rep["banner"] = "STALE ANALYZER GENERATION — QUALIFICATION BLOCKED"
        failed = list(rep.get("failed_checks") or [])
        failed.append({
            "check": "generation_and_mirror_sync_freshness",
            "expected": {
                "current": True,
                "revision_parity": "MATCH",
                "epoch_parity": "MATCH",
                "mirror_sync_in_progress": False,
            },
            "found": {
                "current": freshness.get("current"),
                "revision_parity": freshness.get("revision_parity"),
                "epoch_parity": freshness.get("epoch_parity"),
                "mirror_sync_in_progress": freshness.get("mirror_sync_in_progress"),
                "mirror_sync_revision_parity": freshness.get("mirror_sync_revision_parity"),
            },
            "reasons": freshness.get("reasons") or [],
        })
        rep["failed_checks"] = failed
    return rep


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

    freshness = _generation_freshness_meta()
    if not freshness["current"]:
        stale = True
        reasons.extend(freshness["reasons"])

    return {
        "stale": stale,
        "reasons": reasons,
        "fresh_collection_mode": bool(session.get("fresh_collection_mode")),
        "bot_start_iso": session.get("bot_start_iso"),
        "bot_version": session.get("bot_version"),
        "trades_csv_rows": trades_rows,
        "report_generated_at": gen_at,
        "generation_freshness": freshness,
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
    # Loading the session itself must not recurse through _load_bot_session on
    # a brand-new/empty installation where research_session.json is absent.
    session = {} if name == "research_session.json" else (_load_bot_session() or {})
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
    # A completed atomic generation is authoritative. If it intentionally did
    # not publish this report, do not resurrect a stale or in-progress working
    # copy from the analyzer directory.
    for base in (ROOT, DATA_ROOT):
        published_manifest = base / PUBLISHED_REPORTS_DIR / REPORT_MANIFEST_FILE
        if not published_manifest.is_file():
            continue
        try:
            manifest = json.loads(published_manifest.read_text(encoding="utf-8"))
            declared = {
                str(row.get("file"))
                for row in (manifest.get("reports") or [])
                if isinstance(row, dict) and row.get("file")
            }
            if name not in declared:
                return default
        except Exception:
            continue
        break
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


def _read_contract_receipt(name: str) -> tuple[dict, dict]:
    """Read a startup/runtime contract receipt without reviving it as analyzer evidence.

    Contract receipts are intentionally outside the atomic analyzer manifest.  The
    Pathway Audit page may display them, but must label their age and source so an
    old PASS can never be mistaken for current runtime/analyzer readiness.
    """
    candidates = []
    for base in (ROOT, _AGENT_ROOT, DATA_ROOT):
        path = (base / name).resolve()
        if path in candidates:
            continue
        candidates.append(path)
    newest = None
    newest_mtime = -1.0
    payload = {}
    for path in candidates:
        if not path.is_file():
            continue
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
            mtime = path.stat().st_mtime
        except Exception:
            continue
        if isinstance(candidate, dict) and mtime > newest_mtime:
            newest = path
            newest_mtime = mtime
            payload = candidate
    if newest is None:
        return {}, {
            "status": "NOT_PUBLISHED",
            "source": None,
            "generated_at": None,
            "age_seconds": None,
        }
    generated_at = payload.get("generated_at")
    try:
        generated_dt = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
        if generated_dt.tzinfo is None:
            generated_dt = generated_dt.replace(tzinfo=timezone.utc)
        age_seconds = max(0, int((datetime.now(timezone.utc) - generated_dt).total_seconds()))
    except Exception:
        age_seconds = max(0, int(time.time() - newest_mtime))
    return payload, {
        "status": "CURRENT" if age_seconds <= 3600 else "STALE_CONTRACT_RECEIPT",
        "source": str(newest),
        "generated_at": generated_at or datetime.fromtimestamp(newest_mtime, timezone.utc).isoformat(),
        "age_seconds": age_seconds,
    }


def _best_report_path(name: str) -> Path | None:
    """Return the same scope-aware file that ``_read_report`` would expose."""
    for base in (ROOT, DATA_ROOT):
        published_manifest = base / PUBLISHED_REPORTS_DIR / REPORT_MANIFEST_FILE
        if not published_manifest.is_file():
            continue
        try:
            manifest = json.loads(published_manifest.read_text(encoding="utf-8"))
            declared = {
                str(row.get("file"))
                for row in (manifest.get("reports") or [])
                if isinstance(row, dict) and row.get("file")
            }
            if name not in declared:
                return None
        except Exception:
            continue
        break
    session = _load_bot_session() or {}
    fresh = bool(session.get("fresh_collection_mode"))
    primary = None
    primary_payload = None
    primary_key = (-1, -1.0)
    fallback = None
    fallback_payload = None
    fallback_key = (-1, -1.0)
    for path, payload, mtime in _iter_data_payloads(name):
        key = (_scope_priority(payload, fresh_collection=fresh), mtime)
        is_all_data = str(path).endswith(
            os.path.join(ALL_DATA_REPORTS_DIR, name).replace("/", os.sep)
        )
        if is_all_data:
            if not _report_is_empty(name, payload) and key > fallback_key:
                fallback_key = key
                fallback = path
                fallback_payload = payload
        elif key > primary_key:
            primary_key = key
            primary = path
            primary_payload = payload
    if primary is not None and not _report_is_empty(name, primary_payload or {}):
        return primary
    if fallback is not None and fallback_payload is not None:
        return fallback
    return primary


def _data_file_candidates(name: str) -> list[Path]:
    """Analyzer writes to agent root (DATA_ROOT); legacy copies may sit under research/."""
    # Once an atomic generation exists, its manifest and declared artifacts are
    # the sole public report source. Working-directory files may belong to the
    # next analyzer pass and must not leak into API/dashboard responses.
    for base in (ROOT, DATA_ROOT):
        published_manifest = base / PUBLISHED_REPORTS_DIR / REPORT_MANIFEST_FILE
        if not published_manifest.is_file():
            continue
        try:
            manifest = json.loads(published_manifest.read_text(encoding="utf-8"))
            declared = {
                str(row.get("file"))
                for row in (manifest.get("reports") or [])
                if isinstance(row, dict) and row.get("file")
            }
            declared.update(str(item) for item in (manifest.get("text_artifacts") or []))
            declared.add(REPORT_MANIFEST_FILE)
            if name in declared:
                return [base / PUBLISHED_REPORTS_DIR / name]
        except Exception:
            # A malformed/incomplete publication is ignored; the last valid
            # directory exchange leaves no partial directory under this name.
            continue
    bases = [DATA_ROOT, ROOT]
    seen: set[Path] = set()
    out: list[Path] = []
    for base in bases:
        for candidate in (base / name, base / REPORTS_DIR / name, base / ALL_DATA_REPORTS_DIR / name):
            if candidate not in seen:
                seen.add(candidate)
                out.append(candidate)
    return out


def _public_policy_evidence_row(row: dict) -> dict:
    """Separate ideal-touch hypothesis metrics from terminal-fill evidence."""
    public = dict(row or {})
    policy_id = str(public.get("policy_id") or "").strip()
    # A complete policy identity is ENTRY|EXIT.  Older malformed reports could
    # concatenate a second exit profile; keep the raw value for audit, but never
    # present that ambiguous string as a selectable policy identity.
    if policy_id.count("|") > 1:
        public["raw_policy_id"] = policy_id
        public["policy_id"] = "INVALID_CONCATENATED_POLICY_ID"
        public["policy_identity_status"] = "AMBIGUOUS_MULTIPLE_EXIT_PROFILES"
        public["qualification"] = "INVALID_POLICY_IDENTITY"
    if isinstance(public.get("gates"), dict):
        public["gates"] = {
            str(name): _gate_passed(value)
            for name, value in public["gates"].items()
        }
    full_fills = int(public.get("full_fills") or 0)
    partial_fills = int(public.get("partial_fills") or 0)
    # Older pinned generations only exposed ``oos_fills``.  Prefer the
    # explicit execution classifications when present, but retain that field
    # as a compatibility fallback rather than turning old supported receipts
    # into false zeroes.
    explicit_fill_counts = "full_fills" in public or "partial_fills" in public
    fills = full_fills + partial_fills if explicit_fill_counts else int(public.get("oos_fills") or 0)
    supported = int(public.get("supported_conservative_episodes") or 0)
    no_fills = int(public.get("no_fills") or 0)
    if not supported and (fills or no_fills):
        supported = fills + no_fills
    public["supported_conservative_episodes"] = supported
    public["full_fills"] = full_fills if explicit_fill_counts else fills
    public["partial_fills"] = partial_fills
    public["no_fills"] = no_fills
    public["unsupported_episodes"] = int(public.get("unsupported_episodes") or 0)
    public["conservative_fill_rate"] = public.get("conservative_fill_rate")
    if public["conservative_fill_rate"] is None and supported > 0:
        public["conservative_fill_rate"] = round(fills / supported, 8)
    public["supported_terminal_fills"] = fills
    diagnostic = dict(public.get("ideal_touch_diagnostic") or {})
    public["diagnostic_replay_net_pnl_usd"] = diagnostic.get(
        "oos_net_usd", public.get("sealed_oos_net_usd")
    )
    public["diagnostic_replay_expectancy_lcb_usd"] = diagnostic.get(
        "expectancy_lcb_usd", public.get("expectancy_lcb_usd")
    )
    public["diagnostic_replay_max_drawdown_usd"] = diagnostic.get(
        "max_drawdown_usd", public.get("max_drawdown_usd")
    )
    public["diagnostic_replay_cvar95_usd"] = diagnostic.get(
        "cvar95_usd", public.get("cvar95_usd")
    )
    public["metric_evidence"] = "TERMINAL_OOS_FILLS" if fills > 0 else "IDEAL_TOUCH_DIAGNOSTIC_ONLY"
    if fills <= 0:
        public["sealed_oos_net_usd"] = None
        public["expectancy_lcb_usd"] = None
        public["max_drawdown_usd"] = None
        public["cvar95_usd"] = None
        public["oos_wins"] = None
        public["oos_losses"] = None
        public["execution_metric_status"] = "UNAVAILABLE_NO_SUPPORTED_TERMINAL_FILLS"
        public["qualification"] = "INSUFFICIENT_EXECUTION_EVIDENCE"
        public["execution_verification"] = "NOT EXECUTION VERIFIED"
        public["qualification_eligibility"] = "NOT QUALIFICATION ELIGIBLE"
        public["descriptive_blockers"] = sorted(set(
            [
                str(name) for name, passed in (public.get("gates") or {}).items()
                if passed is not True
            ]
            + [str(item) for item in (public.get("ranking_blockers") or [])]
            + [str(item) for item in (public.get("evidence_blockers") or [])]
        ))
    else:
        public["execution_metric_status"] = "SUPPORTED_TERMINAL_FILLS"
    return public


def _has_public_policy_execution_evidence(row: dict) -> bool:
    """Reject zero-information exhaustive-grid rows from leader APIs."""
    if not isinstance(row, dict):
        return False
    supported = int(row.get("supported_conservative_episodes") or 0)
    if supported <= 0:
        supported = (
            int(row.get("full_fills") or 0)
            + int(row.get("partial_fills") or 0)
            + int(row.get("no_fills") or 0)
        )
    return supported > 0


def _public_policy_diagnostic_row(row: dict) -> dict:
    """Expose a hypothesis without trusting any execution-shaped source fields."""
    public = _public_policy_evidence_row(row)
    diagnostic = dict((row or {}).get("ideal_touch_diagnostic") or {})
    public.update({
        "diagnostic_replay_net_pnl_usd": diagnostic.get("oos_net_usd"),
        "diagnostic_replay_expectancy_lcb_usd": diagnostic.get("expectancy_lcb_usd"),
        "diagnostic_replay_max_drawdown_usd": diagnostic.get("max_drawdown_usd"),
        "diagnostic_replay_cvar95_usd": diagnostic.get("cvar95_usd"),
        "supported_conservative_episodes": 0,
        "supported_terminal_fills": 0,
        "full_fills": 0,
        "partial_fills": 0,
        "no_fills": 0,
        "conservative_fill_rate": None,
        "sealed_oos_net_usd": None,
        "expectancy_lcb_usd": None,
        "max_drawdown_usd": None,
        "cvar95_usd": None,
        "oos_wins": None,
        "oos_losses": None,
        "metric_evidence": "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
        "execution_metric_status": "UNAVAILABLE_NO_SUPPORTED_TERMINAL_FILLS",
        "qualification": "INSUFFICIENT_EXECUTION_EVIDENCE",
        "execution_verification": "NOT EXECUTION VERIFIED",
        "qualification_eligibility": "NOT QUALIFICATION ELIGIBLE",
        "ranking_eligible": False,
    })
    blockers = sorted(set(
        [
            str(name) for name, passed in (public.get("gates") or {}).items()
            if passed is not True
        ]
        + [str(item) for item in (public.get("ranking_blockers") or [])]
        + [str(item) for item in (public.get("evidence_blockers") or [])]
        + ["NOT_EXECUTION_VERIFIED", "NOT_QUALIFICATION_ELIGIBLE"]
    ))
    public["descriptive_blockers"] = blockers
    # The existing descriptive table renders failed gate keys. Include every
    # explicit diagnostic blocker there so none remain hidden from the UI.
    public["gates"] = {
        **(public.get("gates") or {}),
        **{blocker: False for blocker in blockers},
    }
    return public


def _public_scenario_diagnostic_row(row: dict) -> dict:
    """Expose unsupported Scenario-C replay only as an explicit diagnostic."""
    public = _public_policy_evidence_row(row)
    diagnostic = dict((row or {}).get("ideal_touch_diagnostic") or {})
    return {
        "policy_id": public.get("policy_id"),
        "policy_family": public.get("policy_family"),
        "oos_episodes": int(public.get("oos_episodes") or 0),
        "supported_conservative_episodes": int(
            public.get("supported_conservative_episodes") or 0
        ),
        "diagnostic_touches": int(diagnostic.get("touches") or 0),
        "diagnostic_no_touches": int(diagnostic.get("no_touches") or 0),
        "diagnostic_wins": int(diagnostic.get("wins") or 0),
        "diagnostic_losses": int(diagnostic.get("losses") or 0),
        "diagnostic_net_pnl_usd": diagnostic.get("oos_net_usd"),
        "diagnostic_max_drawdown_usd": diagnostic.get("max_drawdown_usd"),
        "diagnostic_expectancy_lcb_usd": diagnostic.get("expectancy_lcb_usd"),
        "evidence_status": "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
        "execution_metric_status": "UNAVAILABLE_NO_SUPPORTED_EXECUTION_EVIDENCE",
        "qualification_eligibility": "NOT QUALIFICATION ELIGIBLE",
    }


def _gate_passed(value) -> bool:
    """Interpret report booleans without treating textual true as a failure."""
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "pass", "passed", "ok", "yes", "1"}
    if isinstance(value, (int, float)):
        return value == 1
    return False


def _failed_gate_names(gates: dict | None) -> list[str]:
    return [str(name) for name, value in (gates or {}).items() if not _gate_passed(value)]


def _mirror_sync_receipt() -> dict:
    """Return the canonical mirror-loop receipt, including in-flight state."""
    candidates = (DATA_ROOT / ".fly-data-sync-loop.heartbeat.json",)
    for path in candidates:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            if isinstance(payload, dict):
                return payload
        except (OSError, ValueError, TypeError):
            continue
    return {}


def _mirror_source_revision() -> str | None:
    """Return the revision actually promoted into the canonical mirror."""
    payload = _mirror_sync_receipt()
    revision = (
        payload.get("mirroredSourceRevision")
        or payload.get("sourceRevision")
        or payload.get("source_revision")
    )
    return str(revision) if revision else None


def _identity_matches(left, right) -> bool:
    """Compare full or intentionally abbreviated immutable identities."""
    left = str(left or "").strip()
    right = str(right or "").strip()
    return bool(left and right and (left.startswith(right) or right.startswith(left)))


def _generation_freshness_meta(manifest: dict | None = None) -> dict:
    """Fail closed unless the published generation matches mirror revision and epoch.

    Saved reports remain readable as historical evidence during synchronization,
    but this receipt prevents them from looking current or authorizing a policy.
    """
    manifest = manifest if isinstance(manifest, dict) else (
        _read_json(REPORT_MANIFEST_FILE, {}) or {}
    )
    session = _load_bot_session() or {}
    # generation_revision identifies analyzer code. Dataset freshness instead
    # compares the independently recorded canonical source revision.
    generation_revision = manifest.get("source_revision") or manifest.get("generation_revision")
    sync_receipt = _mirror_sync_receipt()
    mirror_revision = _mirror_source_revision()
    observed_revision = (
        sync_receipt.get("observedSourceRevision")
        or sync_receipt.get("observed_source_revision")
    )
    sync_in_progress = bool(
        sync_receipt.get("inProgress") or sync_receipt.get("in_progress")
    )
    sync_revision_parity = str(
        sync_receipt.get("revisionParity")
        or sync_receipt.get("revision_parity")
        or "UNAVAILABLE"
    ).upper()
    sync_receipt_ok = sync_receipt.get("ok") is True
    sync_poll_ok = sync_receipt.get("pollOk", sync_receipt.get("poll_ok"))
    generation_epoch = (manifest.get("fresh_epoch") or {}).get("epoch_id")
    mirror_epoch = (
        session.get("collector_v22_epoch_id")
        or session.get("fresh_epoch_id")
        or session.get("epoch_id")
    )
    revision_parity = (
        "MATCH" if _identity_matches(generation_revision, mirror_revision)
        else "MISMATCH" if generation_revision and mirror_revision
        else "UNAVAILABLE"
    )
    epoch_parity = (
        "MATCH" if _identity_matches(generation_epoch, mirror_epoch)
        else "MISMATCH" if generation_epoch and mirror_epoch
        else "UNAVAILABLE"
    )
    reasons = []
    if revision_parity != "MATCH":
        reasons.append(
            "Analyzer dataset source revision does not match the canonical Fly mirror"
            if revision_parity == "MISMATCH"
            else "Analyzer dataset or mirror revision identity is unavailable"
        )
    if epoch_parity != "MATCH":
        reasons.append(
            "Analyzer generation epoch does not match the canonical Fly mirror epoch"
            if epoch_parity == "MISMATCH"
            else "Analyzer or mirror epoch identity is unavailable"
        )
    if sync_in_progress:
        reasons.append("Canonical Fly mirror synchronization is in progress")
    if not sync_receipt_ok:
        reasons.append(
            "Canonical Fly mirror synchronization receipt is failed or unavailable"
        )
    if sync_poll_ok is False:
        reasons.append("Canonical Fly mirror synchronization poll failed")
    if sync_revision_parity != "MATCH":
        reasons.append(
            "Canonical Fly mirror synchronization revision parity is not confirmed"
        )
    if sync_revision_parity == "MISMATCH" or (
        observed_revision
        and mirror_revision
        and not _identity_matches(observed_revision, mirror_revision)
    ):
        reasons.append(
            "Observed Fly revision has not been promoted into the canonical mirror"
        )
    sync_current = bool(
        not sync_in_progress
        and sync_receipt_ok
        and sync_poll_ok is not False
        and sync_revision_parity == "MATCH"
        and (
            not observed_revision
            or not mirror_revision
            or _identity_matches(observed_revision, mirror_revision)
        )
    )
    current = revision_parity == "MATCH" and epoch_parity == "MATCH" and sync_current
    return {
        "current": current,
        "stale": not current,
        "revision_parity": revision_parity,
        "epoch_parity": epoch_parity,
        "mirror_sync_in_progress": sync_in_progress,
        "mirror_sync_receipt_ok": sync_receipt_ok,
        "mirror_sync_poll_ok": sync_poll_ok,
        "mirror_sync_revision_parity": sync_revision_parity,
        "observed_source_revision": observed_revision,
        "generation_revision": generation_revision,
        "mirror_source_revision": mirror_revision,
        "generation_epoch_id": generation_epoch,
        "mirror_epoch_id": mirror_epoch,
        "reasons": reasons,
        "qualification_allowed": current,
    }


def _bounded_safe_policy_payload(report: dict) -> dict:
    """Public Safe/Top APIs expose summaries; full artifact stays downloadable."""
    if not report:
        return {}
    out = {
        key: report.get(key)
        for key in (
            "schema", "extension", "generated_at", "status", "qualification",
            "note", "epoch_id", "epoch_scope", "integrity", "collection",
            "search", "search_progress", "blockers", "number_one_strategy",
            "live_policy_change_allowed", "real_bitfinex_trading_allowed",
            "analysis_provenance", "cohort_schema", "generation_revision",
            "source_data_revision", "policy_comparability_key", "cohorts",
            "report_eligibility", "generation_freshness",
        )
        if key in report
    }
    if "candidate_screen" in report:
        screen = report.get("candidate_screen") or {}
        out["candidate_screen"] = {
            key: value
            for key, value in screen.items()
            if not isinstance(value, (list, dict))
        }
        descriptive_rows = [
            _public_policy_evidence_row(row)
            for row in list(screen.get("descriptive_top_100") or [])
            if _has_public_policy_execution_evidence(row)
        ]
        if not descriptive_rows:
            descriptive_rows = [
                _public_policy_diagnostic_row(row)
                for row in list(
                    screen.get("profitable_ideal_touch_diagnostic_top_100") or []
                )
            ]
        out["candidate_screen"]["descriptive_top_100"] = descriptive_rows[:100]
        out["candidate_screen"]["drawdown_control_leaders"] = [
            _public_policy_evidence_row(row)
            for row in list(screen.get("drawdown_control_leaders") or [])
            if _has_public_policy_execution_evidence(row)
        ][:100]
        out["candidate_screen"]["profit_capture_leaders"] = {
            str(family): [
                _public_policy_evidence_row(row)
                for row in list(rows or [])
                if _has_public_policy_execution_evidence(row)
            ][:10]
            for family, rows in (screen.get("profit_capture_leaders") or {}).items()
            if any(_has_public_policy_execution_evidence(row) for row in list(rows or []))
        }
        sweep = screen.get("scenario_c_atr_stop_sweep") or {}
        conservative_by_stop = {
            str(stop): [
                _public_policy_evidence_row(row)
                for row in list(rows or [])
                if _has_public_policy_execution_evidence(row)
            ][:5]
            for stop, rows in (sweep.get("leaders_by_stop") or {}).items()
        }
        conservative_by_stop = {
            stop: rows for stop, rows in conservative_by_stop.items() if rows
        }
        conservative_by_chase_stop = {
            str(chase): {
                str(stop): _public_policy_evidence_row(row)
                for stop, row in (stops or {}).items()
                if _has_public_policy_execution_evidence(row)
            }
            for chase, stops in (sweep.get("best_by_chase_and_stop") or {}).items()
        }
        conservative_by_chase_stop = {
            chase: stops
            for chase, stops in conservative_by_chase_stop.items()
            if stops
        }
        out["candidate_screen"]["scenario_c_atr_stop_sweep"] = {
            "qualification": sweep.get("qualification"),
            "warning": sweep.get("warning"),
            "policies_tested": sweep.get("policies_tested", 0),
            "overall_leaders": [
                _public_policy_evidence_row(row)
                for row in list(sweep.get("overall_leaders") or [])
                if _has_public_policy_execution_evidence(row)
            ][:25],
            "leaders_by_stop": conservative_by_stop,
            "best_by_chase_and_stop": conservative_by_chase_stop,
            "diagnostic_hypotheses_by_stop": {
                str(stop): [
                    _public_scenario_diagnostic_row(row)
                    for row in list(rows or [])[:5]
                    if not _has_public_policy_execution_evidence(row)
                ]
                for stop, rows in (sweep.get("leaders_by_stop") or {}).items()
                if any(
                    not _has_public_policy_execution_evidence(row)
                    for row in list(rows or [])[:5]
                )
            },
            "diagnostic_hypotheses_by_chase_and_stop": {
                str(chase): {
                    str(stop): _public_scenario_diagnostic_row(row)
                    for stop, row in (stops or {}).items()
                    if not _has_public_policy_execution_evidence(row)
                }
                for chase, stops in (sweep.get("best_by_chase_and_stop") or {}).items()
                if any(
                    not _has_public_policy_execution_evidence(row)
                    for row in (stops or {}).values()
                )
            },
        }
    if "safe_policy_ranking" in report:
        ranking = report.get("safe_policy_ranking") or {}
        out["safe_policy_ranking"] = {
            key: value
            for key, value in ranking.items()
            if not isinstance(value, (list, dict))
        }
        for key in ("blockers", "warning", "number_one_strategy"):
            if key in ranking:
                out["safe_policy_ranking"][key] = ranking[key]
        ranked = [
            row for row in (ranking.get("ranked_policies") or ranking.get("ranked") or [])
            if _has_public_policy_execution_evidence(row)
        ]
        if ranked:
            out["safe_policy_ranking"]["ranked_policies"] = list(ranked)[:100]
    if "candidate_screen" in report or "safe_policy_ranking" in report:
        out["full_artifact"] = f"/api/report/{SAFE_POLICY_GENOME_V3_REPORT_FILE}"
    return out


def _read_json(name: str, default=None):
    return _pick_best_payload(name, default)


def _current_generation_report(name: str) -> dict:
    """Read a manifest-owned report without reviving undeclared stale files."""
    payload = _read_report(name, {}) or {}
    if payload:
        return payload
    manifest = _read_json(REPORT_MANIFEST_FILE, {}) or {}
    fresh_epoch = manifest.get("fresh_epoch") or {}
    return {
        "schema": "current_generation_report_unavailable_v1",
        "generated_at": manifest.get("generated_at"),
        "generation_revision": manifest.get("generation_revision"),
        "source_data_revision": manifest.get("source_data_revision"),
        "epoch_id": fresh_epoch.get("epoch_id"),
        "status": "REPORT_NOT_IN_CURRENT_GENERATION",
        "qualification": "NO_SAFE_QUALIFIED_POLICY",
        "live_policy_change_allowed": False,
        "real_bitfinex_trading_allowed": False,
        "blockers": ["REPORT_NOT_IN_CURRENT_GENERATION", name],
        "report_unavailable": True,
        "missing_report": name,
        "collection": {},
        "candidate_screen": {},
        "safe_policy_ranking": {},
    }


def _declared_atomic_generation_report(name: str) -> tuple[dict | None, dict]:
    """Read ``name`` only when the active atomic manifest declares it.

    Unlike the compatibility report resolver, this deliberately has no loose
    root/report fallback.  Coverage must never surface a file from an analyzer
    pass that has not completed the published-directory exchange.
    """
    for base in (ROOT, DATA_ROOT):
        published = base / PUBLISHED_REPORTS_DIR
        manifest_path = published / REPORT_MANIFEST_FILE
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            continue
        declared = {
            str(row.get("file")): row
            for row in (manifest.get("reports") or [])
            if isinstance(row, dict) and row.get("file")
        }
        if name not in declared:
            return None, {
                "reason": "REPORT_NOT_IN_CURRENT_GENERATION",
                "manifest": manifest,
                "manifest_path": str(manifest_path),
            }
        report_path = published / name
        try:
            report = json.loads(report_path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            return None, {
                "reason": "DECLARED_REPORT_FILE_MISSING",
                "manifest": manifest,
                "manifest_path": str(manifest_path),
            }
        except (OSError, ValueError, TypeError):
            return None, {
                "reason": "DECLARED_REPORT_INVALID_JSON",
                "manifest": manifest,
                "manifest_path": str(manifest_path),
            }
        return report, {
            "reason": None,
            "manifest": manifest,
            "manifest_path": str(manifest_path),
        }
    return None, {"reason": "ATOMIC_GENERATION_UNAVAILABLE", "manifest": {}}


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


def _bundle_members():
    """Canonical report ZIP members with one unambiguous file per report."""
    members: dict[str, Path] = {}
    for name in BUNDLE_FILES:
        p = ROOT / name
        if p.is_file():
            members.setdefault(name, p)
    manifest = _read_json(REPORT_MANIFEST_FILE)
    for entry in manifest.get("reports") or []:
        fname = entry.get("file") if isinstance(entry, dict) else entry
        if not fname or fname in BUNDLE_FILES:
            continue
        candidate = _best_report_path(fname)
        # ``_best_report_path`` intentionally parses JSON so it can apply
        # session-scope precedence.  Manifest-declared binary artifacts (for
        # example the exhaustive ``.jsonl.gz`` policy export) cannot be parsed
        # that way.  They are nevertheless generation-owned artifacts, so use
        # the same atomic publication candidates directly.  Keep JSON on the
        # existing scope-aware path and let the required-member gate below
        # continue to fail closed when a declared artifact is absent.
        if candidate is None and not str(fname).lower().endswith(".json"):
            candidate = next(
                (path for path in _data_file_candidates(fname) if path.is_file()),
                None,
            )
        if candidate is not None:
            members.setdefault(f"{REPORTS_DIR}/{fname}", candidate)
    return sorted(members.items())


def _bundle_paths():
    """Compatibility helper retained for focused source/tests."""
    return [path for _arcname, path in _bundle_members()]



_OPPORTUNITY_STATS_TTL_SEC = max(
    30.0, float(os.getenv("RESEARCH_OPPORTUNITY_CACHE_TTL_SEC", "180"))
)
_OPPORTUNITY_STATS_CACHE = {
    "expires_at": 0.0,
    "value": None,
    "refreshing": False,
}
_OPPORTUNITY_STATS_LOCK = threading.Lock()


def _compute_opportunity_lane_stats() -> dict:
    """Approve / order / spawn counts from lane_opportunity_capture.jsonl + signal_snapshot."""
    out: dict[str, dict] = {}
    snap_path = None
    for base in (DATA_ROOT, ROOT):
        cand = base / "signal_snapshot.jsonl"
        if cand.is_file():
            snap_path = cand
            break
    if snap_path is not None:
        try:
            with snap_path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    lane = str(row.get("research_lane") or row.get("lane") or "").strip()
                    if not lane:
                        continue
                    bucket = out.setdefault(lane, {
                        "approves": 0, "orders_submitted": 0, "spawn_lab": 0, "would_block": 0,
                    })
                    ai = row.get("ai") if isinstance(row.get("ai"), dict) else {}
                    approved = (
                        str(row.get("decision") or row.get("ai_decision") or "").upper() == "APPROVE"
                        or bool(row.get("approved"))
                        or bool(ai.get("approved"))
                        or str(ai.get("decision") or "").upper() == "APPROVE"
                        or row.get("approve_ts") is not None
                    )
                    if approved:
                        bucket["approves"] += 1
        except Exception:
            pass
    opp_path = None
    for base in (DATA_ROOT, ROOT):
        cand = base / "lane_opportunity_capture.jsonl"
        if cand.is_file():
            opp_path = cand
            break
    if opp_path is not None:
        try:
            with opp_path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    lane = str(row.get("lane") or row.get("research_lane") or "").strip()
                    if not lane:
                        continue
                    bucket = out.setdefault(lane, {
                        "approves": 0, "orders_submitted": 0, "spawn_lab": 0, "would_block": 0,
                    })
                    ev = str(row.get("event") or "").upper()
                    if ev == "APPROVE":
                        bucket["approves"] = max(bucket["approves"], 0)  # keep snap count; bump below
                        bucket["_opp_approves"] = int(bucket.get("_opp_approves") or 0) + 1
                    elif ev == "ORDER_SUBMITTED":
                        bucket["orders_submitted"] += 1
                    elif ev == "SPAWN_LAB":
                        bucket["spawn_lab"] += 1
                    elif ev == "WOULD_BLOCK":
                        bucket["would_block"] += 1
        except Exception:
            pass
    # Prefer max(snapshot, opportunity APPROVE) per lane
    for lane, bucket in out.items():
        opp_a = int(bucket.pop("_opp_approves", 0) or 0)
        if opp_a > int(bucket.get("approves") or 0):
            bucket["approves"] = opp_a
    # decisions_3factor.csv fallback
    dec_path = None
    for base in (DATA_ROOT, ROOT):
        cand = base / "decisions_3factor.csv"
        if cand.is_file():
            dec_path = cand
            break
    if dec_path is not None:
        try:
            import csv
            with dec_path.open(encoding="utf-8", errors="replace", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    reason = str(row.get("reason") or row.get("decision") or "").upper()
                    if reason != "APPROVE":
                        continue
                    lane = str(row.get("research_lane") or row.get("lane") or "").strip()
                    if not lane:
                        continue
                    bucket = out.setdefault(lane, {
                        "approves": 0, "orders_submitted": 0, "spawn_lab": 0, "would_block": 0,
                    })
                    bucket["_dec_approves"] = int(bucket.get("_dec_approves") or 0) + 1
            for lane, bucket in out.items():
                dec_a = int(bucket.pop("_dec_approves", 0) or 0)
                if dec_a > int(bucket.get("approves") or 0):
                    bucket["approves"] = dec_a
        except Exception:
            pass
    return out


def _refresh_opportunity_lane_stats() -> dict:
    """Refresh the expensive JSONL aggregation without holding cache locks."""
    try:
        value = _compute_opportunity_lane_stats()
        with _OPPORTUNITY_STATS_LOCK:
            _OPPORTUNITY_STATS_CACHE["value"] = value
            _OPPORTUNITY_STATS_CACHE["expires_at"] = (
                time.monotonic() + _OPPORTUNITY_STATS_TTL_SEC
            )
        return value
    finally:
        with _OPPORTUNITY_STATS_LOCK:
            _OPPORTUNITY_STATS_CACHE["refreshing"] = False


def _opportunity_lane_stats() -> dict:
    """Return lane counts with stale-while-refresh behavior.

    The underlying ledgers are tens of megabytes and the analyzer performs
    CPU-heavy replay grids in the same process.  Once primed, an expired cache
    is returned immediately while a daemon refreshes it, so opening the Lanes
    tab cannot stall for tens of seconds during an analysis pass.
    """
    now = time.monotonic()
    with _OPPORTUNITY_STATS_LOCK:
        value = _OPPORTUNITY_STATS_CACHE.get("value")
        fresh = value is not None and _OPPORTUNITY_STATS_CACHE["expires_at"] > now
        if fresh:
            return value
        if value is not None:
            if not _OPPORTUNITY_STATS_CACHE["refreshing"]:
                _OPPORTUNITY_STATS_CACHE["refreshing"] = True
                threading.Thread(
                    target=_refresh_opportunity_lane_stats,
                    name="research-opportunity-cache",
                    daemon=True,
                ).start()
            return value
        _OPPORTUNITY_STATS_CACHE["refreshing"] = True
    return _refresh_opportunity_lane_stats()


def prime_dashboard_caches() -> None:
    """Warm expensive dashboard aggregates before the analyzer starts work."""
    _opportunity_lane_stats()


def _lane_report_identity(payload: dict) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    provenance = payload.get("analysis_provenance") or {}
    epoch_scope = payload.get("epoch_scope") or {}
    revision = payload.get("generation_revision") or provenance.get("generation_revision")
    source_revision = payload.get("source_data_revision") or provenance.get("source_data_revision")
    epoch = (
        payload.get("epoch_id")
        or (payload.get("fresh_epoch") or {}).get("epoch_id")
        or provenance.get("fresh_epoch_id")
        or epoch_scope.get("selected_epoch_id")
    )
    raw_scope = str(
        payload.get("session_scope")
        or payload.get("data_scope")
        or payload.get("scope")
        or ""
    ).strip().upper()
    if "FRESH" in raw_scope or raw_scope == "SESSION":
        scope = "CURRENT_SESSION"
    elif raw_scope in {"ALL", "ALL-DATA", "ALL-TIME", "HISTORICAL"}:
        scope = "HISTORICAL_ALL_DATA"
    else:
        scope = raw_scope
    return {
        "generation_revision": str(revision or "").strip(),
        "source_data_revision": str(source_revision or "").strip(),
        "epoch_id": str(epoch or "").strip(),
        "scope": scope,
    }


def _current_lane_artifact(name: str) -> tuple[dict, dict]:
    """Return lane evidence only when it belongs to the atomic current generation.

    The Current Lanes page is a current-epoch surface.  Historical/all-data
    reports remain downloadable elsewhere, but they must never backfill current
    closes or PnL when a fresh generation intentionally has no executed rows.
    """
    manifest = _read_json(REPORT_MANIFEST_FILE, {}) or {}
    expected = _lane_report_identity(manifest)
    declared = {
        str(row.get("file"))
        for row in (manifest.get("reports") or [])
        if isinstance(row, dict) and row.get("file")
    }
    candidate = {}
    candidate_mtime = -1.0
    if name in declared:
        for path, payload, mtime in _iter_data_payloads(name):
            parts = tuple(part.casefold() for part in path.parts)
            # Current Lanes is deliberately not an all-data/historical view,
            # even if an old fallback happens to carry plausible identity.
            if len(parts) >= 2 and parts[-2:] == ("all_data", name.casefold()):
                continue
            if isinstance(payload, dict) and mtime > candidate_mtime:
                candidate = payload
                candidate_mtime = mtime
    observed = _lane_report_identity(candidate)
    mismatches = []
    required = (
        "generation_revision",
        "source_data_revision",
        "epoch_id",
        "scope",
    )
    for field in required:
        if not expected.get(field):
            mismatches.append(f"CURRENT_MANIFEST_{field.upper()}_MISSING")
        elif observed.get(field) != expected.get(field):
            mismatches.append(f"{field.upper()}_MISMATCH")
    if not candidate:
        mismatches.append(
            "CURRENT_REPORT_MISSING"
            if name in declared
            else "REPORT_NOT_IN_CURRENT_GENERATION"
        )
    if mismatches:
        return {}, {
            "status": "UNAVAILABLE_CURRENT_GENERATION",
            "report": name,
            "expected": expected,
            "observed": observed,
            "blockers": sorted(set(mismatches)),
        }
    return candidate, {
        "status": "CURRENT_GENERATION",
        "report": name,
        "expected": expected,
        "observed": observed,
        "blockers": [],
    }


def _lane_rows(*, include_evidence: bool = False):
    bench, bench_evidence = _current_lane_artifact("benchmark_vs_lanes_report.json")
    lanes = dict(bench.get("lanes") or {})
    ledger_file, ledger_evidence = _current_lane_artifact("lane_pnl_ledger.json")
    ledger = ledger_file.get("lanes") or {}
    lab_ledger_file, lab_evidence = _current_lane_artifact("lane_lab_pnl_ledger.json")
    lab_ledger = lab_ledger_file.get("lanes") or {}
    opp_stats = _opportunity_lane_stats()
    benchmark_lane = str(bench.get("benchmark_lane") or COMPARISON_BENCHMARK_LANE or BENCHMARK_LANE)
    bench_metrics = lanes.get(benchmark_lane) or {}
    benchmark_pnl = float(bench_metrics.get("net_pnl_real") or bench_metrics.get("net_pnl_usd") or 0)
    benchmark_ev = float(bench_metrics.get("per_approve_ev") or 0)

    all_keys = set(CURRENT_RESEARCH_LANES)
    all_keys.update(lanes.keys())
    all_keys.update(ledger.keys())
    all_keys.update(lab_ledger.keys())
    all_keys.update(opp_stats.keys())
    rows = []
    for lane in sorted(all_keys):
        m = lanes.get(lane) or {}
        lb = ledger.get(lane) or {}
        all_time = m.get("all_time") or {}
        lab = lab_ledger.get(lane) or {}
        opp = opp_stats.get(lane) or {}
        # Executed paper closes and lab/counterfactual terminals are different
        # evidence classes.  Never use the lab ledger as an executed-fill
        # fallback: doing so made an OFF benchmark appear to have paper fills.
        fills = int(m.get("real_fills") or m.get("fills") or lb.get("closes") or 0)
        counterfactual_closes = int(
            m.get("lab_closes") or m.get("lab_rows") or lab.get("closes") or 0
        )
        approves = int(m.get("approves") or 0)
        opp_appr = int(opp.get("approves") or 0)
        orders_submitted = int(opp.get("orders_submitted") or 0)
        spawn_lab = int(opp.get("spawn_lab") or 0)
        if opp_appr > approves:
            approves = opp_appr
        if not approves and orders_submitted:
            approves = orders_submitted
        if not approves and spawn_lab:
            approves = spawn_lab
        if not approves and fills:
            approves = fills
        shadow_filled = int(m.get("shadow_filled") or 0)
        shadow_pnl = float(m.get("net_pnl_shadow_blocked") or 0)
        shadow_fill_pct = float(m.get("shadow_fill_pct") or 0)
        costly_blocks = float(m.get("costly_blocks_usd") or 0)
        good_blocks_saved = float(m.get("good_blocks_saved_usd") or 0)
        pnl = float(m.get("net_pnl_real") or m.get("net_pnl_usd") or lb.get("net_pnl_usd") or 0)
        counterfactual_pnl = float(
            m.get("lab_net_pnl")
            if m.get("lab_net_pnl") is not None
            else lab.get("net_pnl_usd") or 0
        )
        ev = float(m.get("per_approve_ev") or 0)
        at_fills = int(all_time.get("real_fills") or 0)
        at_pnl = float(all_time.get("net_pnl_real") or 0)
        at_ev = float(all_time.get("ev_usd") or (at_pnl / at_fills if at_fills else 0))
        is_current = str(lane).upper().strip() in CURRENT_RESEARCH_LANES
        pathway_status = "BENCHMARK" if lane == benchmark_lane else ("ACTIVE" if is_current else "HISTORICAL")
        is_retired = not is_current
        is_shadow = False
        is_benchmark = lane == benchmark_lane
        has_any_data = (
            fills or approves or pnl or at_fills or at_pnl
            or shadow_filled or abs(shadow_pnl) > 0.01
            or orders_submitted or spawn_lab
        )
        if is_current:
            pass  # always show full catalog
        elif not has_any_data and lane != "AI_SCAN" and lane != benchmark_lane:
            continue
        compare_pnl = pnl if fills else at_pnl
        compare_ev = ev if approves else at_ev
        if is_benchmark:
            status = "BENCHMARK"
        elif is_retired and at_fills:
            status = "HISTORICAL"
        elif is_shadow:
            status = "SHADOW_COLLECTING"
        elif is_retired:
            status = "HISTORICAL"
        elif compare_ev >= benchmark_ev and compare_pnl > benchmark_pnl and lane != benchmark_lane:
            status = "BEATS BENCHMARK"
        elif compare_pnl < benchmark_pnl or (benchmark_ev and compare_ev < benchmark_ev * 0.85):
            status = "UNDERPERFORMING"
        else:
            status = "NEUTRAL"
        coord_note = m.get("coordinator_note") or ""
        if not coord_note and (orders_submitted or spawn_lab) and not fills:
            bits = []
            if orders_submitted:
                bits.append(f"{orders_submitted} orders")
            if spawn_lab:
                bits.append(f"{spawn_lab} spawn_lab")
            coord_note = "Opportunity: " + ", ".join(bits) + " (no closed fills)"
        rows.append({
            "lane": lane,
            "trades": fills,
            "executed_closes": fills,
            "counterfactual_closes": counterfactual_closes,
            "counterfactual_pnl": round(counterfactual_pnl, 2),
            "counterfactual_ev_per_close": round(
                counterfactual_pnl / counterfactual_closes, 2
            ) if counterfactual_closes else 0.0,
            "v2_checker_pass_sims": int(m.get("v2_checker_pass_sims") or 0),
            "v2_reject_counterfactual_sims": int(m.get("v2_reject_counterfactual_sims") or 0),
            "v2_metrics_note": m.get("v2_metrics_note") or "",
            "coordinator_note": coord_note,
            "coordinator_rejects": int(m.get("coordinator_rejects") or (m.get("ai_scan_coordinator") or {}).get("rejects") or 0),
            "coordinator_skipped": int(m.get("coordinator_skipped") or (m.get("ai_scan_coordinator") or {}).get("skipped") or 0),
            "coordinator_timeouts": int(m.get("coordinator_timeouts") or (m.get("ai_scan_coordinator") or {}).get("timeouts") or 0),
            "approves": approves,
            "orders_submitted": orders_submitted,
            "spawn_lab": spawn_lab,
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
    evidence = {
        "status": (
            "CURRENT_GENERATION"
            if (
                bench_evidence.get("status") == "CURRENT_GENERATION"
                and str(bench.get("status") or "").upper()
                == "CURRENT_SESSION_EVIDENCE"
            )
            else "UNAVAILABLE_CURRENT_GENERATION"
        ),
        "benchmark": bench_evidence,
        "executed_ledger": ledger_evidence,
        "counterfactual_ledger": lab_evidence,
        "historical_fallback_used": False,
    }
    if include_evidence:
        return rows, benchmark_pnl, evidence
    return rows, benchmark_pnl


def _normalize_chase_lane(lane: str) -> str:
    return str(lane or "").strip().upper()


def _filter_chase_attributions(rows, lane: str):
    lane = _normalize_chase_lane(lane)
    if not lane:
        return list(rows or [])
    out = []
    for row in rows or []:
        rl = _normalize_chase_lane((row or {}).get("lane") or (row or {}).get("research_lane"))
        if rl == lane:
            out.append(row)
    return out


def _nonqualifying_scope(scope: str, warning: str) -> dict:
    """Attach machine-readable provenance to historical analyzer surfaces."""
    return {
        "evidence_scope": scope,
        "qualified_v3_1": False,
        "ranking_eligible": False,
        "warning": warning,
    }


def _chase_payload(lane: str = ""):
    integrity = _integrity_payload()
    attr = _read_report("chase_attribution_report.json")
    eff = _read_report("chase_effectiveness_report.json")
    threshold = _read_report("chase_threshold_report.json")
    delay = _read_report("chase_delay_report.json")
    totals = attr.get("overnight_watch") or attr.get("totals") or {}
    buckets = eff.get("buckets") or {}
    trades_attr = _filter_chase_attributions(attr.get("trades") or [], lane)
    if lane and trades_attr:
        buckets = {}
        eff_rep = {"buckets": {}}
        for key, b in (_chase_bucket_stats_from_trades(trades_attr) or {}).items():
            buckets[key] = b
    bucket_rows = []
    if isinstance(buckets, dict):
        for key, b in buckets.items():
            if int((b or {}).get("trades") or 0):
                bucket_rows.append({"bucket": key, **(b or {})})
    threshold_rows = []
    for key, b in (threshold.get("thresholds") or {}).items():
        if int((b or {}).get("trades") or 0):
            threshold_rows.append({"threshold": key, **(b or {})})
    shadow_source = threshold.get("shadow_thresholds") or {}
    if lane:
        shadow_source = (threshold.get("shadow_thresholds_by_lane") or {}).get(str(lane).upper()) or {}
    shadow_bucket_rows = [
        {"bucket": key, **(block or {})}
        for key, block in shadow_source.items()
        if int((block or {}).get("trades") or 0)
    ]
    return {
        **_nonqualifying_scope(
            "SEPARATED_EXECUTED_AND_SHADOW",
            threshold.get("warning") or (
                "Executed paper and shadow/lab chase evidence are both analyzed, in separate evidence classes."
            ),
        ),
        "totals": totals,
        "buckets": bucket_rows,
        "executed_buckets": bucket_rows,
        "shadow_buckets": shadow_bucket_rows,
        "coverage": threshold.get("coverage") or {},
        "thresholds": threshold_rows,
        "threshold_question": threshold.get("question"),
        "delay_lanes": (delay.get("lanes") or {}),
        "delay_verdict": delay.get("verdict"),
        "delay_delta": delay.get("delta_chase_3plus_vs_continuous"),
        "question": eff.get("question"),
        "lane_filter": lane or "combined",
        "integrity": integrity,
    }


def _combo_token_known(val) -> bool:
    tok = str(val or "").strip().upper()
    if not tok or tok in {"UNKNOWN", "UNK", "NAN", "NONE", "NULL", "OTHER", "MIXED"}:
        return False
    return True


def _combo_row_known(row: dict) -> bool:
    if not row:
        return False
    lane = str(row.get("lane") or row.get("research_lane") or "").upper()
    return all(
        _combo_token_known(row.get(key))
        for key in ("ai_bucket", "spread_bucket", "entry_mode", "type", "lane")
        if row.get(key) not in (None, "")
    ) and (not row.get("combo") or _combo_token_known(row.get("combo")))


def _regime_key_known(regime) -> bool:
    r = str(regime or "").strip().upper()
    if not r or r in {"UNKNOWN", "UNK"}:
        return False
    for part in r.split("|"):
        p = part.strip()
        if not p or p in {"UNKNOWN", "UNK", "NAN", "NONE"} or p.startswith("UNK"):
            return False
    return True


def _wilson_interval_95(wins: int, total: int) -> tuple[float | None, float | None]:
    """Return a descriptive 95% Wilson interval for an observed OOS win rate."""
    if total <= 0:
        return None, None
    z = 1.959963984540054
    p = max(0.0, min(1.0, float(wins) / float(total)))
    denom = 1.0 + (z * z / total)
    center = (p + (z * z / (2.0 * total))) / denom
    margin = z * ((p * (1.0 - p) / total + z * z / (4.0 * total * total)) ** 0.5) / denom
    return round(max(0.0, center - margin) * 100.0, 2), round(min(1.0, center + margin) * 100.0, 2)


def _decode_counterfactual_policy_id(policy_id: str) -> dict:
    """Decode the versioned policy-grid ID without inventing unavailable fields."""
    text = str(policy_id or "")
    match = re.match(r"^OFFSET_([0-9.]+)_CHASE_([^|]+)\|(.+)$", text)
    if not match:
        return {
            "entry_offset_pct": None, "chase_policy": None, "exit_policy": None,
            "fill_model": "IDEAL_TOUCH_REPLAY", "protection_model": "UNKNOWN",
        }
    offset, chase, exit_policy = match.groups()
    chase_match = re.match(r"^(w234|all_on|w01_on|w5plus_on)_s([0-9]+)_i([0-9]+)$", chase)
    windows = {
        "w234": "2, 3, 4",
        "all_on": "all",
        "w01_on": "0, 1",
        "w5plus_on": "5+",
    }
    decoded = {
        "entry_offset_pct": float(offset),
        "chase_policy": chase,
        "exit_policy": exit_policy,
        "chase_windows": None,
        "chase_remaining_gap_step_pct": None,
        "reprice_interval_sec": None,
        "fill_model": "IDEAL_TOUCH_REPLAY",
        "protection_model": "POLICY_SPECIFIC",
        "exit_behavior": exit_policy,
    }
    if chase_match:
        window_key, step_bps, interval = chase_match.groups()
        window_ages = {
            "w234": "10–25 min",
            "all_on": "all enabled windows",
            "w01_on": "0–10 min",
            "w5plus_on": "25–30 min",
        }
        decoded.update({
            "chase_windows": windows.get(window_key, window_key),
            "chase_window_ages": window_ages.get(window_key),
            "chase_remaining_gap_step_pct": float(step_bps),
            "reprice_interval_sec": int(interval),
        })
    atr_tp = re.match(r"^atr_tp_k([0-9.]+)$", exit_policy)
    if atr_tp:
        k = float(atr_tp.group(1))
        decoded.update({
            "atr_take_profit_multiple": k,
            "exit_behavior": (
                f"Take profit at {k:g}x fill-time 3m ATR (leveraged margin); "
                "otherwise close at recorded path end"
            ),
            "protection_model": "NO_LADDER_NO_THESIS_NO_HARD_STOP",
        })
    return decoded


def _project_v31_policy_spec(policy_spec: dict) -> dict:
    """Flatten the signed V3.1 policy genome for the compact Top-100 table.

    The genome deliberately stores entry, fill, loss protection and profit
    protection as nested immutable components.  The dashboard's compact table
    predates that schema, so project only fields that are explicitly present;
    never infer a missing chase window, stop, or fill model.
    """
    spec = policy_spec if isinstance(policy_spec, dict) else {}
    entry = spec.get("entry") if isinstance(spec.get("entry"), dict) else {}
    fill = spec.get("fill") if isinstance(spec.get("fill"), dict) else {}
    loss = spec.get("loss_protection") if isinstance(spec.get("loss_protection"), dict) else {}
    profit = spec.get("profit_protection") if isinstance(spec.get("profit_protection"), dict) else {}

    chase_policy = entry.get("chase_id") or entry.get("entry_policy_id")
    fill_model = fill.get("execution_world") or fill.get("source_fill_model")
    profit_mode = profit.get("mode")
    exit_parts = []
    if profit_mode:
        exit_parts.append(str(profit_mode))
    if profit.get("atr_tp_k") is not None:
        exit_parts.append(f"TP {float(profit['atr_tp_k']):g}x ATR")
    if profit.get("atr_trail_k") is not None:
        exit_parts.append(f"trail {float(profit['atr_trail_k']):g}x ATR")
    if profit.get("chandelier_atr_k") is not None:
        exit_parts.append(f"chandelier {float(profit['chandelier_atr_k']):g}x ATR")
    if profit.get("break_even_arm_atr_k") is not None:
        exit_parts.append(f"BE at {float(profit['break_even_arm_atr_k']):g}x ATR")

    protection_parts = []
    if loss.get("atr_stop_k") is not None:
        protection_parts.append(f"ATR stop {float(loss['atr_stop_k']):g}x")
    if loss.get("thesis_cut_margin_pct") is not None:
        protection_parts.append(f"thesis {float(loss['thesis_cut_margin_pct']):g}%")
    if loss.get("hard_stop_margin_pct") is not None:
        protection_parts.append(f"hard {float(loss['hard_stop_margin_pct']):g}%")
    if loss.get("time_stop_min") is not None:
        protection_parts.append(f"time {float(loss['time_stop_min']):g}m")

    projected = {
        "entry_offset_pct": entry.get("offset_pct"),
        "chase_policy": chase_policy,
        "fill_model": fill_model,
        "exit_behavior": " + ".join(exit_parts) or None,
        "protection_model": " + ".join(protection_parts) or None,
    }
    return {key: value for key, value in projected.items() if value is not None}


def _current_policy_grid_rows(limit: int = 100) -> dict:
    """Expose canonical signed V3.1 candidates, never retired V2.2 leaders."""
    source = _safe_policy_v3_dashboard_source()
    report, screen = source["report"], source["screen"]
    rows = []
    all_candidates = list(screen.get("descriptive_top_100") or [])
    if "profitable_conservative_top_100" in screen:
        candidates = list(screen.get("profitable_conservative_top_100") or [])
    else:
        # Compatibility for an analyzer generation produced before the
        # explicit profitable-world lists were added. Never promote an
        # ideal-touch row into the conservative shortlist.
        candidates = [
            item for item in all_candidates
            if str((((item.get("policy_spec") or {}).get("fill") or {}).get("execution_world") or "")).startswith("CONSERVATIVE_")
            and int(item.get("oos_fills") or 0) > 0
            and isinstance(item.get("sealed_oos_net_usd"), (int, float))
            and float(item["sealed_oos_net_usd"]) > 0
        ]
    if "profitable_ideal_touch_diagnostic_top_100" in screen:
        diagnostic_candidates = list(screen.get("profitable_ideal_touch_diagnostic_top_100") or [])
    else:
        diagnostic_candidates = [
            item for item in all_candidates
            if isinstance(item.get("sealed_oos_net_usd"), (int, float))
            and float(item["sealed_oos_net_usd"]) > 0
            and str((((item.get("policy_spec") or {}).get("fill") or {}).get("execution_world") or "")).startswith("IDEAL_TOUCH")
        ]
    for rank, item in enumerate(candidates, start=1):
        policy_spec = item.get("policy_spec") or {}
        validation = item.get("validation") or {}
        ideal_touch = item.get("ideal_touch_diagnostic") or {}
        diagnostic_outcomes = ideal_touch.get("outcome_states") or {}
        diagnostic_risk = ideal_touch
        episodes = int(item.get("oos_episodes") or 0)
        full_fills = int(item.get("full_fills") or 0)
        partial_fills = int(item.get("partial_fills") or 0)
        no_fills = int(item.get("no_fills") or 0)
        unsupported_episodes = int(item.get("unsupported_episodes") or 0)
        supported_episodes = int(item.get("supported_conservative_episodes") or 0)
        fills = full_fills + partial_fills
        if not fills:
            fills = int(item.get("oos_fills") or 0)
            if "full_fills" not in item and "partial_fills" not in item:
                full_fills = fills
        if not supported_episodes and (fills or no_fills):
            supported_episodes = fills + no_fills
        wins = item.get("oos_wins")
        losses = item.get("oos_losses")
        if fills > 0:
            wins = wins if wins is not None else (validation.get("risk") or {}).get("wins")
            losses = losses if losses is not None else (validation.get("risk") or {}).get("losses")
        low, high = (None, None)
        if wins is not None and losses is not None:
            low, high = _wilson_interval_95(int(wins), max(episodes, int(wins) + int(losses)))
        rows.append({
            "rank": rank,
            "global_rank": item.get("global_rank"),
            "family_rank": item.get("family_rank"),
            "policy_id": item.get("policy_id"),
            **_decode_counterfactual_policy_id(item.get("policy_id")),
            **_project_v31_policy_spec(policy_spec),
            "policy_family": item.get("policy_family"),
            "comparison_cohort_key": item.get("comparison_cohort_key"),
            "comparison_cohort": item.get("comparison_cohort") or {},
            "cross_family_rank_eligible": item.get("cross_family_rank_eligible") is True,
            "policy_spec": policy_spec,
            "train_episodes": max(0, int(item.get("episodes_total") or 0) - episodes),
            "oos_episodes": episodes,
            "oos_fills": fills,
            "supported_conservative_episodes": supported_episodes,
            "full_fills": full_fills,
            "partial_fills": partial_fills,
            "no_fills": no_fills,
            "unsupported_episodes": unsupported_episodes,
            "conservative_fill_rate": (
                item.get("conservative_fill_rate")
                if item.get("conservative_fill_rate") is not None
                else round(fills / supported_episodes, 8) if supported_episodes else None
            ),
            "oos_wins": wins if fills > 0 else None,
            "oos_losses": losses if fills > 0 else None,
            "oos_win_probability_pct": round((int(wins) / episodes * 100.0), 2) if episodes and wins is not None else None,
            "oos_win_probability_ci95_low_pct": low,
            "oos_win_probability_ci95_high_pct": high,
            # Ideal-touch replay values rank hypotheses; they are not executed
            # OOS evidence. Never expose them as execution metrics without a
            # terminal OOS fill.
            "oos_net_pnl_usd": item.get("sealed_oos_net_usd") if fills > 0 else None,
            "oos_expectancy_usd": (
                round(float(item.get("sealed_oos_net_usd")) / episodes, 8)
                if fills > 0 and episodes > 0 and isinstance(item.get("sealed_oos_net_usd"), (int, float))
                else None
            ),
            "oos_expectancy_lcb_usd": item.get("expectancy_lcb_usd") if fills > 0 else None,
            "oos_max_drawdown_usd": item.get("max_drawdown_usd") if fills > 0 else None,
            "diagnostic_replay_net_pnl_usd": ideal_touch.get("oos_net_usd"),
            "diagnostic_replay_expectancy_lcb_usd": ideal_touch.get("expectancy_lcb_usd"),
            "diagnostic_replay_max_drawdown_usd": ideal_touch.get("max_drawdown_usd"),
            # The policy engine names ideal-touch outcomes FULL_FILL/NO_FILL
            # internally because it shares the replay evaluator with executable
            # worlds.  Publicly these are diagnostic touches, never execution
            # receipts or fills.
            "diagnostic_touch_episodes": int(diagnostic_outcomes.get("FULL_FILL") or 0)
            + int(diagnostic_outcomes.get("PARTIAL_FILL") or 0),
            "diagnostic_no_touch_episodes": int(diagnostic_outcomes.get("NO_FILL") or 0),
            "diagnostic_replay_wins": int(diagnostic_risk.get("wins") or 0),
            "diagnostic_replay_losses": int(diagnostic_risk.get("losses") or 0),
            "metric_evidence": "TERMINAL_OOS_FILLS" if fills > 0 else "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
            "execution_metric_status": (
                "SUPPORTED_TERMINAL_FILLS"
                if fills > 0
                else "UNKNOWN_UNVERIFIABLE_EXECUTION_EVIDENCE"
                if unsupported_episodes > 0 and supported_episodes == 0
                else "UNAVAILABLE_NO_SUPPORTED_TERMINAL_FILLS"
            ),
            "gates": item.get("gates") or {},
            "qualification": (
                "QUALIFIED" if item.get("ranking_eligible")
                else "DESCRIPTIVE_ONLY" if fills > 0
                else "INSUFFICIENT_EXECUTION_EVIDENCE"
            ),
        })
        if len(rows) >= max(1, limit):
            break
    diagnostic_rows = []
    for rank, item in enumerate(diagnostic_candidates[:max(1, limit)], start=1):
        policy_spec = item.get("policy_spec") or {}
        ideal_touch = item.get("ideal_touch_diagnostic") or {}
        # Older immutable generations stored the diagnostic result at the row
        # root. Current generations keep it in an explicitly named world.
        if not ideal_touch:
            validation = item.get("validation") or {}
            ideal_touch = {
                "oos_net_usd": item.get("sealed_oos_net_usd"),
                "max_drawdown_usd": item.get("max_drawdown_usd"),
                "expectancy_lcb_usd": item.get("expectancy_lcb_usd"),
                "outcome_states": validation.get("outcome_states") or {},
                "wins": (validation.get("risk") or {}).get("wins"),
                "losses": (validation.get("risk") or {}).get("losses"),
            }
        outcomes = ideal_touch.get("outcome_states") or {}
        diagnostic_rows.append({
            "rank": rank,
            "global_rank": item.get("global_rank"),
            "family_rank": item.get("family_rank"),
            "policy_id": item.get("policy_id"),
            "policy_family": item.get("policy_family"),
            **_decode_counterfactual_policy_id(item.get("policy_id")),
            **_project_v31_policy_spec(policy_spec),
            "oos_episodes": int(item.get("oos_episodes") or 0),
            "diagnostic_touch_episodes": (
                int(ideal_touch["touches"])
                if ideal_touch.get("touches") is not None
                else int(outcomes.get("FULL_FILL") or 0) + int(outcomes.get("PARTIAL_FILL") or 0)
            ),
            "diagnostic_no_touch_episodes": int(ideal_touch.get("no_touches") or outcomes.get("NO_FILL") or 0),
            "diagnostic_replay_wins": int(ideal_touch.get("wins") or 0),
            "diagnostic_replay_losses": int(ideal_touch.get("losses") or 0),
            "diagnostic_replay_net_pnl_usd": ideal_touch.get("oos_net_usd"),
            "diagnostic_replay_expectancy_lcb_usd": ideal_touch.get("expectancy_lcb_usd"),
            "diagnostic_replay_max_drawdown_usd": ideal_touch.get("max_drawdown_usd"),
            "metric_evidence": "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
            "execution_verification": "NOT EXECUTION VERIFIED",
            "qualification_eligibility": "NOT QUALIFICATION ELIGIBLE",
        })
    collection = report.get("collection") or {}
    search = report.get("search") or {}
    search_counts = {
        **(search.get("counts") or {}),
        **(report.get("search_progress") or {}),
    }
    terminal_oos_rows = [
        row for row in rows
        if int(row.get("oos_episodes") or 0) > 0
        and int(row.get("oos_fills") or 0) > 0
    ]
    profitable_terminal_oos_rows = [
        row for row in terminal_oos_rows
        if isinstance(row.get("oos_net_pnl_usd"), (int, float))
        and float(row["oos_net_pnl_usd"]) > 0
    ]
    split = screen.get("split") or {}
    training_episodes = int(
        split.get("training")
        or screen.get("training_episodes")
        or max(
            (
                max(0, int(item.get("episodes_total") or 0) - int(item.get("oos_episodes") or 0))
                for item in all_candidates
            ),
            default=0,
        )
    )
    oos_episodes = int(
        split.get("oos")
        or screen.get("oos_episodes")
        or max((int(item.get("oos_episodes") or 0) for item in all_candidates), default=0)
    )
    policy_search_statistics = {
        "descriptive_rows_available": len(all_candidates),
        "profitable_conservative_rows_displayed": len(rows),
        "positive_ideal_touch_hypotheses_displayed": len(diagnostic_rows),
        "policy_specs_enumerated": int(
            screen.get("unique_policies_evaluated") or len(all_candidates)
        ),
        "terminal_oos_policies_tested": len(terminal_oos_rows),
        "profitable_terminal_oos_policies": len(profitable_terminal_oos_rows),
        "metric_contract": (
            "Descriptive rows are enumerated counterfactual policy specs. A tested policy "
            "requires at least one terminal OOS fill; profitability requires positive terminal OOS net PnL."
        ),
    }
    return {
        "schema": "current_policy_grid_v3_1",
        "evidence_source": "safe_policy_genome_v3_report.json",
        "collector_generation": "V3.1",
        "status": "PROFITABLE_CONSERVATIVE_POLICIES_AVAILABLE" if rows else "NO_PROFITABLE_CONSERVATIVE_POLICIES",
        "rows": rows,
        "diagnostic_rows": diagnostic_rows,
        "diagnostic_evidence_warnings": [
            "IDEAL_TOUCH_DIAGNOSTIC_ONLY",
            "NOT EXECUTION VERIFIED",
            "NOT QUALIFICATION ELIGIBLE",
        ],
        "epoch_id": source["epoch_id"],
        "policy_epoch_id": None,
        "policy_signature": None,
        "cycle_snapshot": report.get("cycle_snapshot"),
        "evidence": collection,
        "search_counts": search_counts,
        "rows_available": len(all_candidates),
        "descriptive_selection": screen.get("descriptive_selection") or {},
        "comparison_cohort": screen.get("comparison_cohort") or {
            "status": "INSUFFICIENT_SHARED_COHORT",
            "canonical_comparison_cohort_key": None,
            "eligible_policy_count": 0,
            "cross_policy_pooling_allowed": False,
        },
        "policy_search_statistics": policy_search_statistics,
        "policy_episode_split": {
            "training_episodes": training_episodes,
            "oos_episodes": oos_episodes,
            "unit": "INDEPENDENT_MARKET_EPISODES_REUSED_ACROSS_POLICY_SPECS",
        },
        "rows_limit": max(1, int(limit)),
        "blockers": source["blockers"],
        "live_policy_change_allowed": source["qualified"],
        "warning": (
            "The Top Profitable table contains only positive conservative BBO/depth policies with supported fills. "
            "Negative policies are excluded rather than presented as leaders. Positive ideal-touch hypotheses are "
            "shown separately and remain diagnostic only. The chronological 70/30 view is rolling, not a sealed "
            "holdout; no row can authorize live trading until a fixed holdout and every safety gate pass."
        ),
    }


def _combos_payload():
    rep = _read_report("top_combinations_report.json")
    legacy_top = [c for c in (rep.get("top") or []) if _combo_row_known(c)]
    legacy_top.sort(key=lambda x: (x.get("ev_usd") or 0, x.get("pnl_usd") or 0), reverse=True)
    policy_grid = _current_policy_grid_rows(limit=100)
    current_top = list(policy_grid.get("rows") or [])
    return {
        "schema": "top_combinations_dashboard_v3_1",
        "current_evidence_source": "safe_policy_genome_v3_report.json",
        "generated_at": (_safe_policy_v3_dashboard_source()["report"] or {}).get("generated_at"),
        "total_combos": len(current_top),
        "min_trades": None,
        "dimensions": ["entry", "chase", "fill", "exit", "protection", "regime"],
        "filter_note": (
            "Canonical signed V3.1 complete-policy Top 100. The observed executed-lane "
            "combos below are retained only as excluded legacy description."
        ),
        "top": current_top,
        "policy_grid": policy_grid,
        "legacy_executed_combos": {
            "status": "DESCRIPTIVE_LEGACY_EXCLUDED_FROM_V3_1_QUALIFICATION",
            "generated_at": rep.get("generated_at"),
            "min_trades": rep.get("min_trades_per_combo"),
            "dimensions": rep.get("dimensions") or [],
            "rows": legacy_top[:100],
        },
    }


def _current_generation_identity():
    """Return the immutable generation identity shared by lightweight APIs."""
    manifest = _read_json(REPORT_MANIFEST_FILE, {}) or {}
    fresh_epoch = manifest.get("fresh_epoch") or {}
    return {
        "generation_id": manifest.get("generation_id"),
        "generated_at": manifest.get("generated_at"),
        "generation_revision": manifest.get("generation_revision"),
        "source_data_revision": manifest.get("source_data_revision"),
        "epoch_id": fresh_epoch.get("epoch_id"),
    }


def _spread_performance_payload():
    """Aggregate Top Combos by normalized score-gap bucket -> P&L / WR / EV.

    ``spread_bucket`` is retained as the report-schema field name for backward
    compatibility. It is the normalized LONG-vs-SHORT score gap (raw gap / 10),
    not the exchange bid/ask spread and not an AI-confidence band.

    Reuses the same top_combinations_report.json the Top Combos tab reads, grouped by
    spread_bucket so the user can see whether wider directional separation books more profit.
    """
    rep = _read_report("top_combinations_report.json")
    rows = [c for c in (rep.get("top") or []) if _combo_row_known(c)]
    buckets = {}
    for c in rows:
        b = str(c.get("spread_bucket") or "unknown")
        acc = buckets.setdefault(b, {"spread_bucket": b, "trades": 0, "pnl_usd": 0.0, "wins": 0.0})
        n = int(c.get("trades") or 0)
        acc["trades"] += n
        acc["pnl_usd"] += float(c.get("pnl_usd") or 0.0)
        acc["wins"] += n * (float(c.get("wr_pct") or 0.0) / 100.0)
    out = []
    for acc in buckets.values():
        n = acc["trades"]
        pnl = acc["pnl_usd"]
        wr = (acc["wins"] / n * 100.0) if n else 0.0
        ev = (pnl / n) if n else 0.0
        out.append({
            "spread_bucket": acc["spread_bucket"],
            "trades": n,
            "wr_pct": round(wr, 1),
            "pnl_usd": round(pnl, 2),
            "ev_usd": round(ev, 2),
        })
    order = {"0-1": 0, "2": 1, "3": 2, "4": 3, "5+": 4}
    out.sort(key=lambda x: (order.get(x["spread_bucket"], 99), x["spread_bucket"]))
    payload = {
        "total_combos": len(rows),
        "filter_note": (
            "Normalized score gap = abs(LONG score - SHORT score) / 10. "
            "Example: raw gap 30 is bucket 3. This is not exchange bid/ask spread."
        ),
        "buckets": out,
    }
    payload.update(_current_generation_identity())
    if not out:
        payload["empty_reason"] = (
            "INSUFFICIENT_EXECUTED_SCORE_GAP_EVIDENCE: no eligible terminal "
            "executed combinations exist in the current generation"
        )
    return payload


def _chase_bucket_stats_from_trades(rows):
    order = ["0", "1", "2", "3", "4", "5+"]
    buckets = {k: {"trades": 0, "wins": 0, "sum_pnl_usd": 0.0, "win_rate_pct": 0.0, "ev_usd": 0.0, "avg_hold_min": None} for k in order}
    for row in rows or []:
        if row.get("net_pnl_usd") is None and row.get("win") is None:
            continue
        try:
            cc = int(row.get("chase_count") or 0)
        except (TypeError, ValueError):
            cc = 0
        key = "5+" if cc >= 5 else str(cc)
        b = buckets[key]
        b["trades"] += 1
        pnl = float(row.get("net_pnl_usd") or 0)
        b["sum_pnl_usd"] = round(b["sum_pnl_usd"] + pnl, 2)
        if row.get("win") or pnl > 0:
            b["wins"] += 1
    for key, b in buckets.items():
        n = b["trades"]
        if n:
            b["win_rate_pct"] = round(100.0 * b["wins"] / n, 1)
            b["ev_usd"] = round(b["sum_pnl_usd"] / n, 2)
    return buckets


def _chase_threshold_payload(lane: str = ""):
    rep = _read_report("chase_threshold_report.json")
    attr = _read_report("chase_attribution_report.json")
    rows = []
    if lane:
        trades_attr = _filter_chase_attributions(attr.get("trades") or [], lane)
        buckets = _chase_bucket_stats_from_trades(trades_attr)
        for key, block in buckets.items():
            if int((block or {}).get("trades") or 0):
                rows.append({"threshold": key, **(block or {})})
    else:
        for key, block in (rep.get("executed_thresholds") or rep.get("thresholds") or {}).items():
            if int((block or {}).get("trades") or 0):
                rows.append({"threshold": key, **(block or {})})
    shadow_source = rep.get("shadow_thresholds") or {}
    if lane:
        shadow_source = (rep.get("shadow_thresholds_by_lane") or {}).get(str(lane).upper()) or {}
    shadow_rows = [
        {"threshold": key, **(block or {})}
        for key, block in shadow_source.items()
        if int((block or {}).get("trades") or 0)
    ]
    return {
        **_nonqualifying_scope(
            "SEPARATED_EXECUTED_AND_SHADOW",
            rep.get("warning") or (
                "Executed paper and shadow/lab outcomes are both included but remain separate evidence classes."
            ),
        ),
        "generated_at": rep.get("generated_at"),
        "question": rep.get("question") or "Per exact limit_chase_count bucket (0, 1, 2, 3, 4, 5+)",
        "thresholds": rows,
        "executed_thresholds": rows,
        "shadow_thresholds": shadow_rows,
        "coverage": rep.get("coverage") or {},
        "evidence_contract": rep.get("evidence_contract"),
        "lane_filter": lane or "combined",
        "integrity": _integrity_payload(),
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
        **_nonqualifying_scope(
            "LEGACY_EXECUTED",
            "Historical pathway-lab chase delay evidence; excluded from active V3.1 rankings.",
        ),
        "generated_at": rep.get("generated_at"),
        "question": rep.get("question"),
        "verdict": rep.get("verdict"),
        "benchmark_lane": rep.get("benchmark_lane") or BENCHMARK_LANE,
        "direct_reference_lane": rep.get("direct_reference_lane"),
        "delta": delta,
        "lanes": lane_rows,
    }


def _exit_combos_payload():
    rep = _read_report("exit_combinations_report.json")
    classes = rep.get("evidence_worlds") or rep.get("evidence_classes") or {}
    executed = classes.get("executed_paper") or {"top": rep.get("top") or [], "worst_leakage": rep.get("worst_leakage") or []}
    shadow = classes.get("shadow_lab") or {"top": [], "worst_leakage": []}
    top = list(executed.get("top_family_balanced") or executed.get("top") or [])
    worst = list(executed.get("worst_leakage") or [])
    return {
        **_nonqualifying_scope(
            "CURRENT EXECUTED PAPER + SHADOW/LAB — SEPARATED",
            "Terminal exit evidence is descriptive only. Executed-paper and shadow/lab rows are never merged or qualification eligible.",
        ),
        "generated_at": rep.get("generated_at"),
        "benchmark_lane": rep.get("benchmark_lane"),
        "overall_left_on_table_usd": rep.get("overall_left_on_table_usd"),
        "total_combos": executed.get("total_combos", len(top)),
        "family_balance": executed.get("family_balance") or {},
        "filter_note": rep.get("filter_note") or "Generic historical exit combinations.",
        "qualification_eligible": False,
        "exit_family_scorecard": list(executed.get("exit_family_scorecard") or []),
        "stop_effectiveness_matrix": list(executed.get("stop_effectiveness_matrix") or []),
        "top": top[:100],
        "worst_leakage": worst[:100],
        "evidence_classes": {
            "executed_paper": {**executed, "top": top[:100], "worst_leakage": worst[:100]},
            "shadow_lab": {**shadow, "top": list(shadow.get("top") or [])[:100], "worst_leakage": list(shadow.get("worst_leakage") or [])[:100]},
            "conservative_bbo_depth": classes.get("conservative_bbo_depth") or {},
            "ideal_touch_diagnostic": classes.get("ideal_touch_diagnostic") or {},
        },
        "evidence_worlds": classes,
    }


def _exit_reason_leak_payload():
    rep = _read_report("exit_leakage_by_reason_report.json")
    classes = rep.get("evidence_worlds") or rep.get("evidence_classes") or {}
    executed = classes.get("executed_paper") or {"reasons": rep.get("reasons") or [], "recommendations": rep.get("recommendations") or []}
    shadow = classes.get("shadow_lab") or {"reasons": [], "recommendations": []}
    return {
        **_nonqualifying_scope(
            "CURRENT EXECUTED PAPER + SHADOW/LAB — SEPARATED",
            "Peak-to-close hindsight is descriptive only; executed-paper and shadow/lab rows are shown separately.",
        ),
        "generated_at": rep.get("generated_at"),
        "overall_left_usd": rep.get("overall_left_usd"),
        "overall_booked_usd": rep.get("overall_booked_usd"),
        "overall_peak_usd": rep.get("overall_peak_usd"),
        "metric_label": rep.get("metric_label"),
        "metric_warning": rep.get("metric_warning"),
        "qualification_eligible": False,
        "reasons": executed.get("reasons") or [],
        "recommendations": executed.get("recommendations") or [],
        "evidence_classes": {
            "executed_paper": executed,
            "shadow_lab": shadow,
            "conservative_bbo_depth": classes.get("conservative_bbo_depth") or {},
            "ideal_touch_diagnostic": classes.get("ideal_touch_diagnostic") or {},
        },
        "evidence_worlds": classes,
    }


def _ladder_sim_payload():
    rep = _read_report("exit_ladder_simulator_report.json")
    return {
        **_nonqualifying_scope(
            "LEGACY_COUNTERFACTUAL",
            "Historical matched-trade ladder replay; excluded from active V3.1 rankings.",
        ),
        "generated_at": rep.get("generated_at"),
        "actual_realized_usd": rep.get("actual_realized_usd"),
        "actual_trades": rep.get("actual_trades"),
        "matched_actual_realized_usd": rep.get("matched_actual_realized_usd"),
        "matched_actual_trades": rep.get("matched_actual_trades"),
        "comparison_scope": rep.get("comparison_scope"),
        "raw_replays_available": rep.get("raw_replays_available"),
        "eligible_replays_available": rep.get("eligible_replays_available"),
        "replays_available": rep.get("replays_available"),
        "replays_matched_executed": rep.get("replays_matched_executed"),
        "disclaimer": rep.get("disclaimer"),
        "data_status": rep.get("data_status"),
        "empty_reason": rep.get("empty_reason"),
        "best_profile_id": rep.get("best_profile_id"),
        "profiles": rep.get("profiles") or [],
        "integrity": _integrity_payload(),
    }


def _pathway_audit_payload():
    receipt_names = (
        ANALYZER_INTEGRITY_FILE,
        "tile_independence_report.json",
        "ai_scan_independence_report.json",
        "ai_scan_role_validation.json",
        "lane_memory_validation.json",
        "lane_memory_violation.json",
        "runtime_pathway_integrity.json",
        "exit_reports_validation.json",
    )
    receipts = {name: _read_contract_receipt(name) for name in receipt_names}
    # Contract receipts are diagnostic artifacts, not current runtime state.
    # Do not render stale receipt bodies: retired lane names in an old receipt
    # previously made the clean registry look as though obsolete pathways were
    # still active. Keep the metadata so the UI can say exactly why it is hidden.
    def current_receipt(name):
        payload, meta = receipts[name]
        return payload if meta.get("status") != "STALE_CONTRACT_RECEIPT" else {}

    tiles = current_receipt("tile_independence_report.json")
    ai_scan = current_receipt("ai_scan_independence_report.json")
    ai_scan_role = current_receipt("ai_scan_role_validation.json")
    lane_mem = current_receipt("lane_memory_validation.json")
    lane_mem_violation = current_receipt("lane_memory_violation.json")
    runtime_integrity = current_receipt("runtime_pathway_integrity.json")
    exit_val = current_receipt("exit_reports_validation.json")
    sync = _read_report("repo_version_sync.json")
    bot_sync = _read_report("bot_analyzer_sync.json")
    manifest = _read_json(REPORT_MANIFEST_FILE) or {}
    manifest_sync = manifest.get("analyzer_sync_id")
    manifest_registry = manifest.get("tile_registry_signature")
    expected_registry = active_tile_registry_signature()
    current_sync = {
        "status": "CURRENT_MATCH" if (
            manifest_sync == EXPECTED_ANALYZER_SYNC_ID
            and manifest_registry == expected_registry
        ) else "CURRENT_MISMATCH",
        "matches": bool(
            manifest_sync == EXPECTED_ANALYZER_SYNC_ID
            and manifest_registry == expected_registry
        ),
        "generated_at": manifest.get("generated_at"),
        "generation_revision": manifest.get("generation_revision"),
        "analyzer_source_revision": manifest.get("generation_revision"),
        "mirror_source_revision": _mirror_source_revision(),
        "epoch_id": (manifest.get("fresh_epoch") or {}).get("epoch_id"),
        "analyzer_sync_id": manifest_sync,
        "expected_analyzer_sync_id": EXPECTED_ANALYZER_SYNC_ID,
        "tile_registry_signature": manifest_registry,
        "expected_tile_registry_signature": expected_registry,
    }
    return {
        "tile_independence": tiles,
        "ai_scan_independence": ai_scan,
        "ai_scan_role": ai_scan_role,
        "lane_memory": lane_mem,
        "lane_memory_violation": lane_mem_violation,
        "runtime_pathway_integrity": runtime_integrity,
        "exit_reports_validation": exit_val,
        "version_sync": sync,
        "bot_analyzer_sync": bot_sync,
        "current_sync": current_sync,
        "analyzer_integrity": receipts[ANALYZER_INTEGRITY_FILE][0],
        "receipt_status": {name: meta for name, (_payload, meta) in receipts.items()},
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
    losing_trades = int(rep.get("losing_trades") or 0)
    normalized_recovery = []
    for row in recovery:
        item = dict(row)
        if losing_trades == 0 and item.get("coverage_pct") is None:
            item["coverage_pct"] = 0.0
        normalized_recovery.append(item)
    payload = {
        "horizons": normalized_recovery,
        "losing_trades": losing_trades,
        "fast_cut_recovery": rep.get("fast_cut_recovery"),
        "fast_cut_recovery_summary": rep.get("fast_cut_recovery_summary") or [],
        "conclusions_allowed": rep.get("conclusions_allowed", False),
        "max_horizon_coverage_pct": rep.get("max_horizon_coverage_pct"),
        "min_coverage_pct_for_conclusions": rep.get("min_coverage_pct_for_conclusions", 80),
        "note": rep.get("note"),
        "coverage_reason": rep.get("coverage_reason"),
    }
    payload.update(_current_generation_identity())
    if losing_trades == 0:
        payload["empty_reason"] = (
            "INSUFFICIENT_POST_EXIT_HORIZON_EVIDENCE: no eligible losing terminal "
            "trades exist in the current generation"
        )
        payload["coverage_reason"] = payload["empty_reason"]
        payload["note"] = payload["note"] or payload["empty_reason"]
        payload["max_horizon_coverage_pct"] = 0.0
    return payload


def _leakage_payload():
    rep = _read_json("top_leakage_report.json") or _read_json(str(Path(REPORTS_DIR) / "top_leakage_report.json"))
    leak = _read_json("scenario_c_leakage_report.json") or _read_json(str(Path(REPORTS_DIR) / "scenario_c_leakage_report.json"))
    payload = {
        "overall_left_usd": rep.get("overall_left_usd") or (leak.get("overall") or {}).get("left_on_table_usd"),
        "by_exit_reason": rep.get("by_exit_reason") or {},
        "trades": rep.get("trades") or [],
    }
    payload.update(_current_generation_identity())
    if not payload["trades"] and not payload["by_exit_reason"]:
        payload["empty_reason"] = (
            "INSUFFICIENT_TERMINAL_EXIT_EVIDENCE: no eligible terminal exits with "
            "peak-to-close observations exist in the current generation"
        )
    return payload


def _feature_payload():
    rep = _read_json("feature_importance_report.json") or _read_json(str(Path(REPORTS_DIR) / "feature_importance_report.json"))
    payload = {
        "features": rep.get("features") or [],
        "weak_signals": rep.get("weak_signals") or [],
    }
    payload.update(_current_generation_identity())
    if not payload["features"] and not payload["weak_signals"]:
        payload["empty_reason"] = (
            "INSUFFICIENT_OUTCOME_FEATURE_EVIDENCE: no eligible terminal outcomes "
            "exist for feature attribution in the current generation"
        )
    return payload


def _ai_payload():
    cal = _read_json("ai_calibration_report.json")
    funnel = _read_json("ai_funnel_report.json")
    fp = _read_json("ai_decision_fingerprint_report.json")
    conf = _read_json("confidence_band_report.json")
    calibration_status = str(cal.get("calibration_status") or "NO_DATA").upper()
    probability_mode = calibration_status == "AVAILABLE"
    gap = _spread_performance_payload()
    return {
        "calibration_status": calibration_status,
        "direction_only": not probability_mode,
        "mode_note": (
            "Direction-only AI: no confidence probability is requested or used. "
            "Showing normalized LONG-vs-SHORT score-gap performance instead."
            if not probability_mode
            else "Probability calibration is available for this historical cohort."
        ),
        "calibration_buckets": (cal.get("confidence_buckets") or []) if probability_mode else [],
        "expected_vs_actual": cal.get("expected_vs_actual") or {},
        "feature_attribution": cal.get("feature_attribution") or {},
        "funnel": funnel,
        "fingerprints": fp.get("clusters") or fp.get("fingerprints") or fp,
        "confidence_bands": (conf.get("filled_trades_by_band") or []) if probability_mode else [],
        "normalized_gap_buckets": gap.get("buckets") or [],
        "normalized_gap_note": gap.get("filter_note"),
        "normalized_gap_total_combos": gap.get("total_combos") or 0,
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
    archive_path = Path(
        entry.get("path") or (ROOT / ARCHIVE_DIR / str(sid))
    )
    archived_manifest = _read_json(str(archive_path / REPORT_MANIFEST_FILE), {}) or {}
    summary_generated_at = entry.get("generated_at")
    return {
        "id": str(sid),
        "session_id": str(sid),
        # Archive rows identify an immutable analyzer generation.  Its manifest
        # timestamp is authoritative; the earlier summary timestamp is exposed
        # separately rather than silently standing in for generation time.
        "generated_at": archived_manifest.get("generated_at") or summary_generated_at,
        "manifest_generated_at": archived_manifest.get("generated_at"),
        "summary_generated_at": summary_generated_at,
        "generation_id": archived_manifest.get("generation_id"),
        "generation_revision": archived_manifest.get("generation_revision"),
        "source_data_revision": archived_manifest.get("source_data_revision"),
        "epoch_id": (archived_manifest.get("fresh_epoch") or {}).get("epoch_id"),
        "report_count": archived_manifest.get("report_count"),
        "trades": entry.get("trades"),
        "net_pnl_usd": entry.get("net_pnl_usd"),
        "win_rate_pct": entry.get("win_rate_pct"),
        "path": str(archive_path),
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


def _past_analysis_index():
    try:
        from research.past_analysis import list_past_analyses

        return {"analyses": list_past_analyses(ROOT)}
    except Exception:
        return {"analyses": []}


# ---------------------------------------------------------------------------
# Routes — API
# ---------------------------------------------------------------------------
@app.route("/api/health")
def api_health():
    """Cheap readiness probe; process liveness remains explicit and separate."""
    runtime_sync_ok = RESEARCH_DASHBOARD_VERSION == EXPECTED_ANALYZER_SYNC_ID
    freshness = _generation_freshness_meta()
    return jsonify({
        "ok": bool(runtime_sync_ok and freshness["current"]),
        "alive": True,
        "ready": bool(runtime_sync_ok and freshness["current"]),
        "read_only": True,
        "dashboard_version": RESEARCH_DASHBOARD_VERSION,
        "runtime_analyzer_sync_id": EXPECTED_ANALYZER_SYNC_ID,
        "runtime_sync_match": runtime_sync_ok,
        "dashboard_started_at": _DASHBOARD_STARTED_AT.isoformat(),
        "pid": os.getpid(),
        "data_root": str(DATA_ROOT),
        "report_root": str(ROOT),
        "generation_freshness": freshness,
        "source_revision_parity": freshness["revision_parity"],
        "epoch_parity": freshness["epoch_parity"],
    })


@app.route("/api/runtime-incidents")
def api_runtime_incidents():
    candidates = (Path(DATA_ROOT) / "crash_dump.json", Path(ROOT) / "crash_dump.json")
    crash_path = next((path for path in candidates if path.is_file()), candidates[0])
    return jsonify(build_runtime_incident_history(
        crash_path,
        current_started_at=None,
        current_instance_id=None,
        current_revision=None,
    ))


@app.route("/api/status")
def api_status():
    manifest = _read_json(REPORT_MANIFEST_FILE)
    compact = _read_json(COMPACT_SUMMARY_FILE)
    safe_genome = _current_generation_report(SAFE_POLICY_GENOME_V3_REPORT_FILE)
    manifest_sync = manifest.get("analyzer_sync_id") or compact.get("analyzer_sync_id")
    report_sync_ok = manifest_sync == EXPECTED_ANALYZER_SYNC_ID if manifest_sync else None
    run_state = _analyzer_run_state()
    previous_report_at = manifest.get("generated_at") or compact.get("generated_at")
    if not run_state.get("last_completed_at") and previous_report_at:
        run_state["last_completed_at"] = previous_report_at
    runtime_sync_ok = RESEARCH_DASHBOARD_VERSION == EXPECTED_ANALYZER_SYNC_ID
    report_pending = bool(
        report_sync_ok is not True
        and run_state.get("in_progress")
        and run_state.get("sync_id") == EXPECTED_ANALYZER_SYNC_ID
    )
    fresh_epoch = manifest.get("fresh_epoch") or {}
    fresh_epoch_id = (
        fresh_epoch.get("epoch_id")
        or safe_genome.get("epoch_id")
        or (safe_genome.get("epoch_scope") or {}).get("selected_epoch_id")
    )
    observed_execution_policy_signatures = sorted({
        str(identity.get("policy_signature") or "").strip()
        for identity in (
            (safe_genome.get("collection") or {}).get(
                "effective_paper_execution_identities"
            )
            or []
        )
        if isinstance(identity, dict)
        and str(identity.get("policy_signature") or "").strip()
    })
    legacy_policy_signature = safe_genome.get("policy_signature") or (
        safe_genome.get("epoch_scope") or {}
    ).get("policy_signature")
    if legacy_policy_signature and not observed_execution_policy_signatures:
        observed_execution_policy_signatures = [str(legacy_policy_signature)]
    active_tile_policy_signatures = sorted({
        str(tile.get("policy_signature") or "").strip()
        for tile in (manifest.get("active_tiles") or [])
        if isinstance(tile, dict)
        and str(tile.get("policy_signature") or "").strip()
    })
    analyzer_source_revision = manifest.get("generation_revision")
    mirror_source_revision = _mirror_source_revision()
    freshness = _generation_freshness_meta(manifest)
    source_revision_parity = freshness["revision_parity"]
    required_report_status = manifest.get("required_report_status") or {}
    required_report_failures = sorted(
        str(name)
        for name, status in required_report_status.items()
        if not isinstance(status, dict)
        or status.get("available_in_generation") is not True
        or bool(status.get("generation_error"))
        or status.get("current_generation_valid") is False
    )
    required_reports_ok = bool(required_report_status) and not required_report_failures
    dashboard_started_dt = _parse_utc_dt(_DASHBOARD_STARTED_AT.isoformat())
    report_generated_dt = _parse_utc_dt(previous_report_at)
    restart_observed = bool(
        dashboard_started_dt
        and report_generated_dt
        and dashboard_started_dt > report_generated_dt
    )
    return jsonify({
        "ok": bool(
            runtime_sync_ok
            and (report_sync_ok is True or report_pending)
            and freshness["current"]
            and required_reports_ok
        ),
        "alive": True,
        "ready": bool(
            runtime_sync_ok
            and report_sync_ok is True
            and freshness["current"]
            and required_reports_ok
        ),
        "read_only": True,
        "dashboard_version": RESEARCH_DASHBOARD_VERSION,
        "runtime_analyzer_sync_id": EXPECTED_ANALYZER_SYNC_ID,
        "runtime_sync_match": runtime_sync_ok,
        "expected_bot_version": EXPECTED_BOT_VERSION,
        "expected_analyzer_sync_id": EXPECTED_ANALYZER_SYNC_ID,
        "benchmark_lane": BENCHMARK_LANE,
        "analyzer_sync_match": report_sync_ok,
        "report_sync_match": report_sync_ok,
        "report_sync_pending": report_pending,
        "required_reports_ok": required_reports_ok,
        "required_report_status": required_report_status,
        "required_report_failures": required_report_failures,
        "analysis_in_progress": run_state.get("in_progress"),
        "analysis_run": run_state,
        "dashboard_started_at": _DASHBOARD_STARTED_AT.isoformat(),
        "cwd": str(ROOT),
        "data_root": str(DATA_ROOT),
        "report_root": str(ROOT),
        "public_url": PUBLIC_URL,
        "analyzer_sync_id": EXPECTED_ANALYZER_SYNC_ID,
        "report_analyzer_sync_id": manifest_sync,
        "generation_revision": manifest.get("generation_revision"),
        "generation_revision_label": "ANALYZER_SOURCE_REVISION",
        "analyzer_source_revision": analyzer_source_revision,
        "mirror_source_revision": mirror_source_revision,
        "fly_mirror_source_revision": mirror_source_revision,
        "source_revision_parity": source_revision_parity,
        "epoch_parity": freshness["epoch_parity"],
        "generation_freshness": freshness,
        "stale": freshness["stale"],
        "stale_reasons": freshness["reasons"],
        "source_data_revision": manifest.get("source_data_revision"),
        "fresh_epoch_id": fresh_epoch_id,
        "tile_registry_signature": manifest.get("tile_registry_signature"),
        # Compatibility alias retained for older clients.  Its evidence scope
        # is now explicit so observed execution identities cannot be confused
        # with the complete five-family tile registry.
        "policy_signatures": observed_execution_policy_signatures,
        "policy_signatures_scope": "OBSERVED_EXECUTION_IDENTITIES",
        "observed_execution_policy_signatures": observed_execution_policy_signatures,
        "active_tile_policy_signatures": active_tile_policy_signatures,
        "policy_signature_counts": {
            "observed_execution": len(observed_execution_policy_signatures),
            "active_tile_registry": len(active_tile_policy_signatures),
        },
        "active_tile_policy_signatures_match_manifest": bool(
            active_tile_policy_signatures
            and active_tile_policy_signatures == sorted({
                str(tile.get("policy_signature") or "").strip()
                for tile in (manifest.get("active_tiles") or [])
                if isinstance(tile, dict)
                and str(tile.get("policy_signature") or "").strip()
            })
        ),
        "generated_at": previous_report_at,
        "generated_at_melbourne": format_melbourne_dt(previous_report_at),
        "melbourne_now": format_melbourne_dt(datetime.now(timezone.utc).isoformat()),
        "timezone": "Australia/Melbourne",
        "report_count": len(_manifest_reports()),
        "last_files": {
            "manifest": _file_mtime(REPORT_MANIFEST_FILE),
            "compact": _file_mtime(COMPACT_SUMMARY_FILE),
        },
        "availability_receipt": {
            "schema": "analyzer_dashboard_availability_v1",
            "dashboard_started_at": _DASHBOARD_STARTED_AT.isoformat(),
            "dashboard_pid": os.getpid(),
            "report_generated_at": previous_report_at,
            "analysis_last_completed_at": run_state.get("last_completed_at"),
            "restart_observed_with_preserved_reports": bool(
                restart_observed
            ),
            "restart_classification": (
                "EXPECTED_CONTROLLED_RESTART"
                if str(os.getenv("ANALYZER_EXPECTED_CONTROLLED_RESTART") or "").strip().lower()
                in {"1", "true", "yes"}
                else "RESTART_OBSERVED_UNCLASSIFIED"
                if restart_observed
                else "NO_RESTART_SINCE_CURRENT_REPORT"
            ),
            "note": (
                "A dashboard restart never rewrites or silently advances the immutable report generation."
            ),
        },
    })


def _read_research_events_v22() -> list[dict]:
    """Read the freshest mirror without locking it during JSON parsing."""
    candidates = [path for path in _data_file_candidates(RESEARCH_EVENTS_FILE) if path.is_file()]
    if not candidates:
        return []
    path = max(candidates, key=lambda item: item.stat().st_mtime)
    rows = []
    try:
        # Close the Windows source handle before parsing a large ledger. The
        # dashboard may receive overlapping refresh requests; retaining the
        # handle during JSON decoding can otherwise starve atomic mirror sync.
        payload = path.read_bytes()
    except OSError:
        return []
    for raw_line in payload.splitlines():
        try:
            row = json.loads(raw_line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _best_policy_research_payload():
    """One fail-closed answer based only on the newest qualified V3.1 epoch."""
    manifest = _read_json(REPORT_MANIFEST_FILE)
    policy_report = _read_json(BEST_POLICY_RESEARCH_REPORT_FILE)
    events = _read_research_events_v22()

    def signal_ts(row):
        envelope = row.get("envelope") or {}
        try:
            return float(envelope.get("signal_ts") or row.get("signal_ts") or 0)
        except (TypeError, ValueError):
            return 0.0

    newest = max(events, key=signal_ts, default={})
    current_epoch = str(newest.get("epoch_id") or (newest.get("envelope") or {}).get("epoch_id") or "")
    current_policy_epoch = str(
        newest.get("policy_epoch_id") or (newest.get("envelope") or {}).get("policy_epoch_id") or ""
    )
    current_policy_signature = str(
        newest.get("policy_signature") or (newest.get("envelope") or {}).get("policy_signature") or ""
    )
    deployed_policies = [
        {
            "lane": lane,
            "policy_id": spec.get("raw_policy_id"),
            "policy_signature": spec.get("policy_signature"),
            "collection_status": "COLLECTING_NO_CURRENT_EPOCH_EVIDENCE",
            "qualification_status": "NOT_QUALIFIED",
        }
        for lane, spec in ACTIVE_TILE_REGISTRY.items()
    ]
    deployed_epochs = {
        str(spec.get("policy_epoch") or "")
        for spec in ACTIVE_TILE_REGISTRY.values()
        if spec.get("policy_epoch")
    }
    deployed_policy_epoch = next(iter(deployed_epochs)) if len(deployed_epochs) == 1 else None
    current = [row for row in events if str(
        row.get("epoch_id") or (row.get("envelope") or {}).get("epoch_id") or ""
    ) == current_epoch and str(
        row.get("policy_epoch_id") or (row.get("envelope") or {}).get("policy_epoch_id") or ""
    ) == current_policy_epoch] if current_epoch and current_policy_epoch else []
    outcomes = {"ACCEPTED_FILLED": 0, "ACCEPTED_UNFILLED": 0, "REJECTED": 0}
    eligible = []
    ineligible = []
    explicit_episodes = set()
    missing_episode_ids = 0
    for row in current:
        outcome = str(row.get("primary_outcome") or (row.get("envelope") or {}).get("primary_outcome") or "")
        if outcome in outcomes:
            outcomes[outcome] += 1
        episode_id = row.get("event_episode_id") or (row.get("envelope") or {}).get("event_episode_id")
        if episode_id:
            explicit_episodes.add(str(episode_id))
        else:
            missing_episode_ids += 1
        receipt = validate_replay_eligibility(row) if validate_replay_eligibility else {
            "eligible": False, "reasons": ["REPLAY_ELIGIBILITY_VALIDATOR_UNAVAILABLE"]
        }
        (eligible if receipt.get("eligible") else ineligible).append((row, receipt))

    report_epoch = str(policy_report.get("epoch_id") or "")
    evidence = policy_report.get("evidence") or {}
    candidate = policy_report.get("current_candidate") or policy_report.get("candidate")
    blockers = list(policy_report.get("blockers") or [])
    gate_values = policy_report.get("qualification_gates") or {}
    gate_blockers = qualification_gate_blockers(gate_values)
    independent_oos = bool(
        policy_report.get("independent_oos_qualified") or evidence.get("independent_oos_qualified")
    )
    if not current_epoch:
        blockers.append("NO_CURRENT_V22_EPOCH")
    if not current:
        blockers.append("NO_CURRENT_EPOCH_EVENTS")
    if ineligible:
        blockers.append("REPLAY_INELIGIBLE_PATHS_PRESENT")
    if missing_episode_ids:
        blockers.append("EVENT_EPISODE_ID_MISSING")
    for name, count in outcomes.items():
        if count == 0:
            blockers.append(f"{name}_COVERAGE_MISSING")
    if not policy_report:
        blockers.append("BEST_POLICY_REPORT_MISSING")
    elif not report_epoch or report_epoch != current_epoch:
        blockers.append("BEST_POLICY_REPORT_EPOCH_MISMATCH")
    if str(policy_report.get("policy_epoch_id") or "") != current_policy_epoch:
        blockers.append("BEST_POLICY_REPORT_POLICY_EPOCH_MISMATCH")
    if str(policy_report.get("evidence_policy_signature") or "") != current_policy_signature:
        blockers.append("BEST_POLICY_REPORT_POLICY_SIGNATURE_MISMATCH")
    if not independent_oos:
        blockers.append("INDEPENDENT_OOS_EVIDENCE_MISSING")
    if policy_report.get("qualification_gate_schema") != QUALIFICATION_GATE_SCHEMA:
        blockers.append("QUALIFICATION_GATE_SCHEMA_MISMATCH")
    blockers.extend(gate_blockers)
    blockers.extend(candidate_contract_blockers(candidate))

    blockers = sorted(set(blockers))
    qualified = bool(
        str(policy_report.get("status") or "").upper() == "QUALIFIED"
        and candidate and current_epoch and report_epoch == current_epoch
        and independent_oos and not gate_blockers and not blockers
    )
    last_analysis = policy_report.get("generated_at") or manifest.get("generated_at")
    live_evidence = {
        "current_epoch_events": len(current),
        "replay_eligible_events": len(eligible),
        "completed_paths": len(eligible),
        "replay_ineligible_events": len(ineligible),
        "independent_episode_count": len(explicit_episodes),
        "events_missing_episode_id": missing_episode_ids,
        "qualified_oos_episodes": int(evidence.get("qualified_oos_episodes") or 0),
        "outcome_coverage": outcomes,
    }
    cycle_snapshot = policy_report.get("cycle_snapshot")
    analyzed_evidence = dict(evidence) if cycle_snapshot and evidence else live_evidence
    payload = {
        "schema": "best_policy_research_v1",
        "cycle_snapshot": cycle_snapshot,
        "status": "QUALIFIED" if qualified else "NO QUALIFIED POLICY",
        "live_policy_change_allowed": qualified,
        "current_candidate": candidate if qualified else None,
        "descriptive_challenger": policy_report.get("descriptive_challenger"),
        "epoch_id": current_epoch or None,
        "policy_epoch_id": current_policy_epoch or deployed_policy_epoch,
        "evidence_policy_signature": current_policy_signature or None,
        "deployed_policy_collection": {
            "policy_epoch": deployed_policy_epoch,
            "policies": deployed_policies,
            "policy_count": len(deployed_policies),
            "qualification_allowed": False,
        },
        "last_analysis": last_analysis,
        "last_analysis_melbourne": format_melbourne_dt(last_analysis),
        "evidence": analyzed_evidence,
        "live_observed_evidence": live_evidence,
        "blockers": blockers,
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "qualification_gate_details": qualification_gate_details(
            gate_values,
            policy_report.get("qualification_gate_evidence"),
            current_generation_available=bool(
                current_epoch and report_epoch == current_epoch
                and str(policy_report.get("policy_epoch_id") or "") == current_policy_epoch
                and str(policy_report.get("evidence_policy_signature") or "") == current_policy_signature
            ),
        ),
        "note": (
            "A candidate appears only after replay-eligible, independent, untouched out-of-sample "
            "evidence passes every declared qualification gate for this exact epoch."
        ),
        "legacy_historical": {
            "status": "EXCLUDED_FROM_QUALIFICATION",
            "event_count_outside_current_epoch": max(0, len(events) - len(current)),
        },
        "research_design": policy_report.get("research_design") or {},
    }
    return payload


def _decision_readiness_payload():
    """Compatibility wrapper for clients of the retired five-card endpoint."""
    payload = _best_policy_research_v31_payload()
    evidence = payload["evidence"]
    payload["questions"] = [{
        "key": "best_policy_research",
        "question": "Best Policy Research",
        "status": payload["status"],
        "live_policy_change_allowed": payload["live_policy_change_allowed"],
        "current_epoch_qualified_rows": int(
            evidence.get("replay_eligible_events")
            or evidence.get("independent_opportunities")
            or 0
        ),
        "historical_showcase_rows": 0,
        "blockers": payload["blockers"],
        "detail": payload["note"],
    }]
    return payload


@app.route("/api/decision-readiness")
def api_decision_readiness():
    return jsonify(_decision_readiness_payload())


@app.route("/api/best-policy-research")
def api_best_policy_research():
    return jsonify(_best_policy_research_v31_payload())


@app.route("/api/safe-policy-genome-v3")
@app.route("/api/safe-policy-genome-v3.1")
def api_safe_policy_genome_v3():
    source = _safe_policy_v3_dashboard_source()
    payload = dict(source["report"])
    report_was_missing = not payload
    if not payload:
        payload = {
            "schema": "safe_policy_genome_v3_1_report_v1",
            "extension": "ADAPTIVE_EXIT_AND_DRAWDOWN_LAB_V3_1",
            "status": "V3_REPORT_NOT_GENERATED",
            "qualification": "NO_SAFE_QUALIFIED_POLICY",
            "number_one_strategy": None,
            "live_policy_change_allowed": False,
            "real_bitfinex_trading_allowed": False,
            "collection": {},
            "blockers": ["V3_REPORT_NOT_GENERATED"],
        }
    freshness = source.get("generation_freshness") or {
        "current": True, "stale": False, "revision_parity": "MATCH",
        "epoch_parity": "MATCH", "reasons": [],
    }
    if (
        not report_was_missing
        and payload.get("status") != "REPORT_NOT_IN_CURRENT_GENERATION"
        and not freshness["current"]
    ):
        payload["status"] = "STALE_GENERATION"
        payload["qualification"] = "STALE_GENERATION_NOT_QUALIFICATION_ELIGIBLE"
        payload["number_one_strategy"] = None
        payload["live_policy_change_allowed"] = False
        payload["real_bitfinex_trading_allowed"] = False
        payload["blockers"] = source["blockers"]
        payload["generation_freshness"] = freshness
    return jsonify(_bounded_safe_policy_payload(payload))


@app.route("/safe-policy-genome-v3")
@app.route("/safe-policy-genome-v3.1")
def safe_policy_genome_v3_page():
    return render_template_string("""
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Safe Policy Genome V3.1</title>
<style>body{font-family:system-ui;background:#0d1117;color:#e6edf3;padding:24px}a{color:#58a6ff}.wrap{width:100%;max-width:1500px;min-width:0;margin:auto;box-sizing:border-box}.banner,.card{min-width:0;max-width:100%;box-sizing:border-box;border:1px solid #30363d;background:#161b22;border-radius:9px;padding:14px;margin:12px 0}.bad{border-color:#d29922}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}.value{font-size:24px;font-weight:700}pre,li{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}table{width:100%;border-collapse:collapse;font-size:13px}th,td{border-bottom:1px solid #30363d;padding:8px;text-align:right}th:first-child,td:first-child{text-align:left}.scroll{display:block;width:100%;max-width:100%;min-width:0;overflow-x:auto}.muted{color:#8b949e}@media(max-width:600px){body{padding:12px}.banner,.card{padding:12px}.grid{grid-template-columns:1fr}}</style></head><body><div class="wrap"><a href="/">← Research Dashboard</a><h1>Research Collector V3.1 — Adaptive Exit and Drawdown Lab</h1><div id="banner" class="banner bad">Loading signed V3.1 report…</div><div class="banner bad"><strong>IDEAL_TOUCH_DIAGNOSTIC_ONLY</strong> · <strong>NOT EXECUTION VERIFIED</strong> · <strong>NOT QUALIFICATION ELIGIBLE</strong><br><span class="muted">Any ideal-touch values below are diagnostic replay values, never conservative execution results.</span></div><div id="grid" class="grid"></div><div class="card"><h2>Number one complete safe strategy</h2><pre id="winner"></pre></div><div class="card"><h2>Scenario C × ATR initial-stop sweep</h2><p id="scenario-warning" class="muted">Loading stop comparison…</p><h3>Conservative execution leaders by stop</h3><div class="scroll"><table><thead><tr><th>ATR stop</th><th>Policy</th><th>Supported OOS</th><th>Net USD</th><th>Max DD</th><th>CVaR95</th><th>LCB/episode</th></tr></thead><tbody id="scenario-stops"></tbody></table></div><h3>Conservative execution leaders by chase × stop</h3><div class="scroll"><table><thead><tr><th>Chase policy</th><th>ATR stop</th><th>Supported OOS</th><th>Net USD</th><th>Max DD</th><th>LCB/episode</th></tr></thead><tbody id="scenario-chase-stops"></tbody></table></div><h3>Ideal-touch diagnostic hypotheses</h3><p class="muted">Supplied OOS paths without supported execution are hypotheses only; their diagnostic PnL cannot qualify a policy.</p><div class="scroll"><table><thead><tr><th>ATR stop</th><th>Policy</th><th>Supplied OOS</th><th>Supported execution</th><th>Touches</th><th>Diagnostic PnL</th><th>Diagnostic DD</th><th>Status</th></tr></thead><tbody id="scenario-diagnostics"></tbody></table></div></div><div class="card"><h2>Profit-capture leaders by family</h2><p class="muted">Fixed target, ATR trail, chandelier, MFE giveback and hybrid runner policies are evaluated as complete entry-to-terminal paths.</p><div id="families"></div></div><div class="card"><h2>Drawdown-control leaders</h2><div class="scroll"><table><thead><tr><th>Policy</th><th>Family</th><th>OOS net</th><th>Max DD</th><th>Retention</th><th>Underwater</th></tr></thead><tbody id="drawdown"></tbody></table></div></div><div class="card"><h2>Descriptive complete-policy screen (top 100)</h2><p class="muted">Visible for transparency only. These rows cannot authorize live trading until every safety gate passes.</p><div class="scroll"><table><thead><tr><th>Policy</th><th>Family</th><th>Episodes</th><th>OOS</th><th>Diagnostic net USD</th><th>Diagnostic max DD</th><th>Evidence</th><th>Eligibility</th><th>Blockers</th></tr></thead><tbody id="descriptive"></tbody></table></div></div><div class="card"><h2>Search and blockers</h2><pre id="detail"></pre></div></div>
<script>fetch('/api/safe-policy-genome-v3.1').then(r=>r.json()).then(d=>{const c=d.collection||{},s=d.search_progress||{},cs=d.candidate_screen||{},rows=cs.descriptive_top_100||[],dd=cs.drawdown_control_leaders||[],families=cs.profit_capture_leaders||{};document.getElementById('banner').textContent=(d.status||'—')+' · '+(d.qualification||'—')+' · Real Bitfinex allowed: '+(d.real_bitfinex_trading_allowed?'YES':'NO')+' · '+(d.note||'');const cards=[['Independent episodes',c.independent_opportunities||0],['Decision branches',c.decision_branches||0],['Terminal lifecycles',c.terminal_lifecycles||0],['Market segments',c.market_segments||0],['Complete policies evaluated',cs.unique_policies_evaluated||s.unique_policies_evaluated||0],['Nominal search space',s.nominal_full_cartesian||0]];document.getElementById('grid').innerHTML=cards.map(x=>'<div class="card"><small>'+x[0]+'</small><div class="value">'+x[1]+'</div></div>').join('');document.getElementById('winner').textContent=JSON.stringify(d.number_one_strategy||{status:'NO SAFE QUALIFIED POLICY'},null,2);document.getElementById('families').innerHTML=Object.entries(families).map(([name,items])=>'<h3>'+name+'</h3><ol>'+items.slice(0,10).map(r=>'<li>'+r.policy_id+' · OOS $'+String(r.sealed_oos_net_usd??'—')+' · DD $'+String(r.max_drawdown_usd??'—')+'</li>').join('')+'</ol>').join('')||'<p>Insufficient execution evidence for family leaders.</p>';document.getElementById('drawdown').innerHTML=dd.length?dd.map(r=>'<tr><td>'+r.policy_id+'</td><td>'+r.policy_family+'</td><td>'+String(r.sealed_oos_net_usd??'—')+'</td><td>'+String(r.max_drawdown_usd??'—')+'</td><td>'+String(r.mean_profit_retention_ratio??'—')+'</td><td>'+String(r.mean_underwater_observation_ratio??'—')+'</td></tr>').join(''):'<tr><td colspan="6">Insufficient execution evidence for drawdown leaders.</td></tr>';document.getElementById('descriptive').innerHTML=rows.length?rows.map(r=>'<tr><td>'+r.policy_id+'</td><td>'+r.policy_family+'</td><td>'+r.episodes_total+'</td><td>'+r.oos_episodes+'</td><td>'+String(r.diagnostic_replay_net_pnl_usd??'—')+'</td><td>'+String(r.diagnostic_replay_max_drawdown_usd??'—')+'</td><td>'+String(r.metric_evidence||'IDEAL_TOUCH_DIAGNOSTIC_ONLY')+'</td><td>'+String(r.qualification_eligibility||'NOT QUALIFICATION ELIGIBLE')+'</td><td>'+(r.descriptive_blockers||[]).join(', ')+'</td></tr>').join(''):'<tr><td colspan="9">Insufficient execution evidence; exhaustive zero-information hypotheses remain internal.</td></tr>';document.getElementById('detail').textContent=JSON.stringify({blockers:d.blockers,epoch_scope:d.epoch_scope,integrity:d.integrity,ranking:d.safe_policy_ranking,search:d.search,candidate_warning:cs.warning},null,2);});</script>
<script>fetch('/api/safe-policy-genome-v3.1').then(r=>r.json()).then(d=>{const sweep=((d.candidate_screen||{}).scenario_c_atr_stop_sweep)||{},byStop=sweep.leaders_by_stop||{},byChase=sweep.best_by_chase_and_stop||{},diag=sweep.diagnostic_hypotheses_by_stop||{},sortStops=entries=>Object.entries(entries).sort(([a],[b])=>{if(a==='CONTROL_NO_ATR_STOP')return 1;if(b==='CONTROL_NO_ATR_STOP')return -1;return Number(a)-Number(b)});document.getElementById('scenario-warning').textContent=(sweep.qualification||'INSUFFICIENT')+' · '+(sweep.warning||'No Scenario C stop evidence yet.');document.getElementById('scenario-stops').innerHTML=sortStops(byStop).map(([stop,items])=>{const row=(items||[])[0]||{};return '<tr><td>'+stop+'</td><td>'+String(row.policy_id||'—')+'</td><td>'+String(row.supported_conservative_episodes??'—')+'</td><td>'+String(row.sealed_oos_net_usd??'—')+'</td><td>'+String(row.max_drawdown_usd??'—')+'</td><td>'+String(row.cvar95_usd??'—')+'</td><td>'+String(row.expectancy_lcb_usd??'—')+'</td></tr>'}).join('')||'<tr><td colspan="7">INSUFFICIENT EXECUTION EVIDENCE — no supported conservative Scenario C leader.</td></tr>';const chaseRows=[];Object.entries(byChase).forEach(([chase,stops])=>Object.entries(stops||{}).forEach(([stop,row])=>chaseRows.push({chase,stop,row:row||{}})));document.getElementById('scenario-chase-stops').innerHTML=chaseRows.map(x=>'<tr><td>'+x.chase+'</td><td>'+x.stop+'</td><td>'+String(x.row.supported_conservative_episodes??'—')+'</td><td>'+String(x.row.sealed_oos_net_usd??'—')+'</td><td>'+String(x.row.max_drawdown_usd??'—')+'</td><td>'+String(x.row.expectancy_lcb_usd??'—')+'</td></tr>').join('')||'<tr><td colspan="6">INSUFFICIENT EXECUTION EVIDENCE — no supported chase × stop leader.</td></tr>';document.getElementById('scenario-diagnostics').innerHTML=sortStops(diag).flatMap(([stop,items])=>(items||[]).map(row=>'<tr><td>'+stop+'</td><td>'+String(row.policy_id||'—')+'</td><td>'+String(row.oos_episodes??0)+'</td><td>'+String(row.supported_conservative_episodes??0)+'</td><td>'+String(row.diagnostic_touches??0)+'</td><td>'+String(row.diagnostic_net_pnl_usd??'—')+'</td><td>'+String(row.diagnostic_max_drawdown_usd??'—')+'</td><td>IDEAL_TOUCH_DIAGNOSTIC_ONLY · NOT QUALIFICATION ELIGIBLE</td></tr>')).join('')||'<tr><td colspan="8">No Scenario C ideal-touch diagnostic hypotheses.</td></tr>';});</script></body></html>
""")


@app.route("/api/conservative-fill-research")
def api_conservative_fill_research():
    """Read-only descriptive receipts; never a policy qualification endpoint."""
    payload = _read_json(CONSERVATIVE_FILL_DESCRIPTIVE_REPORT_FILE, {}) or {}
    if not payload:
        payload = {
            "schema": "conservative_fill_descriptive_cohort_v1",
            "qualification": "DESCRIPTIVE_ONLY",
            "qualification_effect": "NONE",
            "qualification_promotion_allowed": False,
            "counts": {"events": 0, "fill": 0, "partial_fill": 0, "no_fill": 0, "unsupported": 0},
            "receipts": [],
        }
    return jsonify(payload)


@app.route("/api/cross-world-evidence")
def api_cross_world_evidence():
    """Expose only the manifest-owned current analyzer generation."""
    payload = _current_generation_report("cross_world_evidence_report.json")
    if not payload:
        payload = {
            "schema": "cross_world_evidence_v1",
            "status": "NOT_COMPUTABLE",
            "qualification": "DESCRIPTIVE_ONLY",
            "join_summary": {"status": "NOT_COMPUTABLE"},
            "blockers": ["CURRENT_MANIFEST_REPORT_NOT_AVAILABLE"],
            "worlds": {},
        }
    return jsonify(payload)


@app.route("/cross-world-evidence")
def cross_world_evidence_page():
    return render_template_string("""
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Cross-World Evidence</title>
<style>body{font-family:system-ui;background:#0d1117;color:#e6edf3;padding:24px}a{color:#58a6ff}.wrap{max-width:1400px;margin:auto}.banner,.card{border:1px solid #30363d;background:#161b22;border-radius:9px;padding:14px;margin:12px 0}.bad{border-color:#d29922}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.value{font-size:22px;font-weight:700}pre{white-space:pre-wrap;overflow-wrap:anywhere}@media(max-width:600px){body{padding:12px}.grid{grid-template-columns:1fr}}</style></head><body><div class="wrap"><a href="/">← Research Dashboard</a><h1>Cross-World Evidence</h1><div id="banner" class="banner bad">Loading current manifest report…</div><div class="card"><strong>Strict causal join:</strong> epoch + opportunity + policy + schedule + tape + fill. Missing or duplicate identity remains NOT_COMPUTABLE; diagnostic touch is never relabelled as a fill.</div><div id="worlds" class="grid"></div><div class="card"><h2>Join summary and blockers</h2><pre id="detail"></pre></div></div>
<script>fetch('/api/cross-world-evidence').then(r=>r.json()).then(d=>{const j=d.join_summary||{},worlds=d.worlds||{};document.getElementById('banner').textContent=(j.status||d.status||'NOT_COMPUTABLE')+' · epoch '+String(d.epoch_id||'UNKNOWN')+' · revision '+String(d.source_revision||'UNKNOWN')+' · comparisons '+String(j.pairwise_computable_comparisons??0)+' · disagreements '+String(j.pairwise_disagreements??0);document.getElementById('worlds').innerHTML=Object.entries(worlds).map(([name,w])=>'<div class="card"><small>'+name+'</small><div class="value">'+String(w.status||'NOT_COMPUTABLE')+'</div><div>current observed '+String(w.rows_observed??0)+' · complete identity '+String(w.rows_with_complete_explicit_identity??0)+' · unique joinable '+String(w.unique_joinable_rows??0)+'</div><div>excluded legacy/missing epoch '+String((w.rows_excluded_missing_epoch??0)+(w.rows_excluded_other_epoch??0))+'</div></div>').join('')||'<div class="card">No current world inventory is available.</div>';document.getElementById('detail').textContent=JSON.stringify({epoch_id:d.epoch_id,source_revision:d.source_revision,join_summary:j,worlds:worlds,source_inventory:d.source_inventory},null,2)}).catch(e=>{document.getElementById('banner').textContent='FAILED TO LOAD · '+e});</script></body></html>
""")


def _policy_detail_is_current(detail: dict, best: dict) -> bool:
    return bool(
        detail
        and detail.get("epoch_id") == best.get("epoch_id")
        and detail.get("policy_epoch_id") == best.get("policy_epoch_id")
        and detail.get("evidence_policy_signature")
        == best.get("evidence_policy_signature")
    )


def _safe_policy_v3_dashboard_source() -> dict:
    """Return the canonical signed V3.1 policy-research surface.

    Static/dynamic pages previously inherited the retired V2.2 best-policy
    adapter.  That made a healthy V3.1 collection look empty for V2.2 reasons.
    These pages now consume the same artifact as the Safe Policy Genome page
    and remain fail-closed when its evidence is immature or invalid.
    """
    report = dict(_current_generation_report(SAFE_POLICY_GENOME_V3_REPORT_FILE))
    epoch_id = report.get("epoch_id") or (report.get("epoch_scope") or {}).get("selected_epoch_id")
    # The compatibility report evaluates explicit cohort-level maturity gates
    # (minimum independent episodes, execution paths, market segments and OOS).
    # Surface those exact blockers on every V3.1 dashboard instead of reducing
    # an empty Top 100 table to the unhelpful NO_SAFE_QUALIFIED_POLICY label.
    compatibility = _read_report(BEST_POLICY_RESEARCH_REPORT_FILE, {}) or {}
    compatibility_schema = str(compatibility.get("schema") or "")
    if (
        compatibility_schema.startswith("best_policy_research_v3_1_adapter")
        and compatibility.get("epoch_id") == epoch_id
    ):
        report["blockers"] = sorted(set(
            list(report.get("blockers") or []) + list(compatibility.get("blockers") or [])
        ))
    screen = report.get("candidate_screen") or {}
    ranking = report.get("safe_policy_ranking") or {}
    freshness = _generation_freshness_meta()
    if not report:
        freshness = dict(freshness)
        freshness.update({"current": False, "stale": True})
        freshness["reasons"] = sorted(set(
            list(freshness.get("reasons") or []) + ["CURRENT_POLICY_REPORT_MISSING"]
        ))
    blockers = list(report.get("blockers") or (['V3_REPORT_NOT_GENERATED'] if not report else []))
    if not freshness["current"]:
        blockers.append("STALE_ANALYZER_GENERATION")
        if freshness["revision_parity"] != "MATCH":
            blockers.append(f"SOURCE_REVISION_PARITY_{freshness['revision_parity']}")
        if freshness["epoch_parity"] != "MATCH":
            blockers.append(f"EPOCH_PARITY_{freshness['epoch_parity']}")
    return {
        "report": report,
        "screen": screen,
        "ranking": ranking,
        "epoch_id": epoch_id,
        "qualified": bool(
            freshness["current"]
            and
            report.get("number_one_strategy")
            and ranking.get("qualification") == "QUALIFIED"
            and report.get("live_policy_change_allowed") is True
        ),
        "blockers": sorted(set(blockers)),
        "generation_freshness": freshness,
    }


def _best_policy_research_v31_payload() -> dict:
    """Compatibility answer backed exclusively by canonical signed V3.1 data."""
    source = _safe_policy_v3_dashboard_source()
    report, screen, ranking = source["report"], source["screen"], source["ranking"]
    collection = report.get("collection") or {}
    compatibility = _read_report(BEST_POLICY_RESEARCH_REPORT_FILE, {}) or {}
    compatibility_evidence = compatibility.get("evidence") or {}
    compatibility_matches = bool(
        str(compatibility.get("schema") or "").startswith("best_policy_research_v3_1_adapter")
        and compatibility.get("epoch_id") == source["epoch_id"]
    )
    # A terminal lifecycle may truthfully be NO_ORDER (for example, a disabled
    # benchmark decision).  It is not a completed replay/execution path.  Use
    # the canonical cohort report when identities match and otherwise fail
    # closed to the explicit execution-path count.
    completed_paths = int(
        (compatibility_evidence.get("completed_paths") or 0)
        if compatibility_matches
        else (collection.get("execution_rows") or 0)
    )
    # Keep the compatibility endpoint truthful for clients which still render
    # the retired V2.2 field names.  The values are projections of canonical
    # V3.1 counts, not a second evidence source.
    evidence = dict(collection)
    evidence.update({
        "current_epoch_events": int(collection.get("independent_opportunities") or 0),
        "completed_paths": completed_paths,
        "replay_eligible_execution_rows": int(
            compatibility_evidence.get("replay_eligible_execution_rows")
            or completed_paths
        ),
        "independent_episode_count": int(collection.get("independent_opportunities") or 0),
        "qualified_oos_episodes": int(
            (screen.get("split") or {}).get("oos") or 0
        ),
        "outcome_coverage": dict(collection.get("decision_outcomes") or {}),
    })
    search = dict(report.get("search") or report.get("search_progress") or {})
    search_counts = dict(search.get("counts") or {})
    search_counts.update({
        "entry_policy_cartesian": int(search_counts.get("entry_cartesian") or 0),
        "naive_full_cartesian": int(search_counts.get("nominal_full_cartesian") or 0),
    })
    search["counts"] = search_counts
    search["static_vs_dynamic"] = {
        "required": True,
        "source": "V3.1_HIERARCHICAL_POLICY_GENOME",
    }
    execution_identities = collection.get("effective_paper_execution_identities") or []
    execution_identity = execution_identities[0] if len(execution_identities) == 1 else {}
    deployed_policy_collection = report.get("deployed_policy_collection") or {
        "policy_epoch": next(iter({
            str(spec.get("policy_epoch")) for spec in ACTIVE_TILE_REGISTRY.values()
            if spec.get("policy_epoch")
        }), None),
        "policies": [
            {
                "lane": lane,
                "policy_id": spec.get("raw_policy_id"),
                "policy_signature": spec.get("policy_signature"),
                "collection_status": "COLLECTING_NO_CURRENT_EPOCH_EVIDENCE",
                "qualification_status": "NOT_QUALIFIED",
            }
            for lane, spec in ACTIVE_TILE_REGISTRY.items()
        ],
        "policy_count": len(ACTIVE_TILE_REGISTRY),
        "qualification_allowed": False,
    }
    descriptive = screen.get("descriptive_top_100") or []
    generated_at = report.get("generated_at") or (_read_json(REPORT_MANIFEST_FILE) or {}).get("generated_at")
    qualified = source["qualified"]
    # Real sources always include this receipt. The fallback keeps isolated
    # test/extension stubs compatible without weakening production behavior.
    freshness = source.get("generation_freshness") or {
        "current": True, "stale": False, "revision_parity": "MATCH",
        "epoch_parity": "MATCH", "reasons": [],
    }
    report_gate_values = compatibility.get("qualification_gates") or {}
    report_gate_evidence = compatibility.get("qualification_gate_evidence") or {}
    gate_details = qualification_gate_details(
        report_gate_values,
        report_gate_evidence,
        current_generation_available=bool(compatibility_matches and freshness["current"]),
    )
    return {
        "schema": "best_policy_research_v3_1",
        "evidence_source": "safe_policy_genome_v3_report.json",
        "collector_generation": "V3.1",
        "status": (
            "QUALIFIED" if qualified else
            "STALE GENERATION — QUALIFICATION BLOCKED" if not freshness["current"] else
            "NO QUALIFIED POLICY"
        ),
        "qualification": (
            report.get("qualification") or "NO_SAFE_QUALIFIED_POLICY"
        ) if freshness["current"] else "STALE_GENERATION_NOT_QUALIFICATION_ELIGIBLE",
        "live_policy_change_allowed": qualified,
        "real_bitfinex_trading_allowed": bool(qualified and report.get("real_bitfinex_trading_allowed")),
        "current_candidate": ranking.get("number_one") if qualified else None,
        "descriptive_challenger": descriptive[0] if descriptive else None,
        "strategy_leaders": report.get("strategy_leaders") or {
            "schema": "three_tier_strategy_leaders_v1",
            "currency": {
                "generated_at": generated_at,
                "source_revision": freshness.get("source_revision"),
                "analyzer_revision": freshness.get("analyzer_revision"),
                "dataset_epoch_id": source["epoch_id"],
                "tile_config_signature": report.get("tile_config_signature"),
            },
            "unknown_evidence": {"episode_count": 0, "blocker_counts": {}},
            "descriptive_ideal_touch": {
                "status": "AVAILABLE" if descriptive else "NO_EVALUATED_DIAGNOSTIC_POLICY",
                "claim_label": "IDEAL_TOUCH_DIAGNOSTIC_ONLY · NOT EXECUTION VERIFIED · DOES NOT SHOW THAT IT WORKS",
                "leader": descriptive[0] if descriptive else None,
                "blockers": source["blockers"],
            },
            "execution_supported": {
                "status": "NO_EXECUTION_SUPPORTED_POLICY", "leader": None,
                "claim_label": "EXECUTION-SUPPORTED OBSERVATION · NOT FULLY QUALIFIED · NOT LIVE READY",
                "blockers": sorted(set(source["blockers"] + ["NO_SUPPORTED_CONSERVATIVE_FILL_POLICY"])),
            },
            "fully_qualified": {
                "status": "AVAILABLE" if qualified else "NO_FULLY_QUALIFIED_POLICY",
                "leader": ranking.get("number_one") if qualified else None,
                "claim_label": "FULLY QUALIFIED RESEARCH POLICY · LIVE ARM STILL REQUIRES EXPLICIT AUTHORIZATION",
                "blockers": [] if qualified else source["blockers"],
            },
        },
        "epoch_id": source["epoch_id"],
        "policy_epoch_id": (
            execution_identity.get("policy_epoch_id")
            or deployed_policy_collection.get("policy_epoch")
        ),
        "evidence_policy_signature": execution_identity.get("policy_signature"),
        "deployed_policy_collection": deployed_policy_collection,
        "last_analysis": generated_at,
        "last_analysis_melbourne": format_melbourne_dt(generated_at),
        "evidence": evidence,
        "live_observed_evidence": collection,
        "blockers": source["blockers"],
        "qualification_gate_schema": QUALIFICATION_GATE_SCHEMA,
        "qualification_gate_details": gate_details,
        "generation_freshness": freshness,
        "stale": freshness["stale"],
        "note": (
            "This endpoint is a V3.1 compatibility projection. A winner appears only after complete "
            "terminal paths pass chronological OOS, conservative execution, drawdown, tail-risk, "
            "multiple-testing, regime and sealed-holdout gates."
        ),
        "legacy_historical": {
            "status": "RETIRED_V2_2_EXCLUDED_FROM_CURRENT_QUALIFICATION",
            "source": "best_policy_research_report.json",
        },
        "research_design": search,
    }


@app.route("/api/static-policy-research")
def api_static_policy_research():
    source = _safe_policy_v3_dashboard_source()
    report, screen = source["report"], source["screen"]
    all_rows = screen.get("descriptive_top_100") or []
    rows = [
        _public_policy_evidence_row(row)
        for row in all_rows
        if float(row.get("sealed_oos_net_usd") or 0) > 0
    ]
    collection = report.get("collection") or {}
    return jsonify({
        "schema": "static_policy_dashboard_v3_1",
        "evidence_source": "safe_policy_genome_v3_report.json",
        "collector_generation": "V3.1",
        "status": "DESCRIPTIVE" if rows else "WAITING_FOR_EVIDENCE",
        "qualification": "QUALIFIED" if source["qualified"] else "DESCRIPTIVE_ONLY",
        "live_policy_change_allowed": source["qualified"],
        "epoch_id": source["epoch_id"],
        "policy_epoch_id": None,
        "independent_episodes": int(collection.get("independent_opportunities") or 0),
        "training_episodes": int(screen.get("training_episodes") or 0),
        "oos_episodes": int(screen.get("oos_episodes") or 0),
        "profitable_policies": rows,
        "policy_search_statistics": {
            "unique_policies_evaluated": int(screen.get("unique_policies_evaluated") or 0),
            "rows_shown": len(rows),
            "nominal_search_space": (report.get("search") or {}).get("nominal_full_cartesian"),
        },
        "warning": screen.get("warning") or (
            "V3.1 evidence is descriptive until conservative execution and sealed chronological OOS gates pass."
        ),
        "blockers": source["blockers"],
    })


@app.route("/api/integrity")
def api_integrity():
    """Expose the analyzer's canonical fail-closed integrity receipt."""
    raw = _read_json(ANALYZER_INTEGRITY_FILE) or {}
    payload = _integrity_with_generation_freshness(raw)
    if not raw and payload.get("generation_freshness", {}).get("current"):
        payload.update({
            "schema": "analyzer_integrity_v1",
            "valid": False,
            "report_status": "MISSING",
            "failed_checks": ["ANALYZER_INTEGRITY_REPORT_MISSING"],
        })
    payload["ok"] = payload.get("valid") is True
    return jsonify(payload), (200 if payload["ok"] else 503)


@app.route("/api/dynamic-policy-research")
def api_dynamic_policy_research():
    report = _read_report(DYNAMIC_POLICY_ANALYSIS_REPORT_FILE, {}) or {}
    current_manifest = _read_json(REPORT_MANIFEST_FILE) or {}
    legacy_rows = []
    legacy_source = None
    # Development/backward-compatible view only when no completed generation
    # exists. Once a manifest exists, an absent dynamic artifact is UNKNOWN and
    # an older safe-policy file must never be revived.
    if not report:
        legacy_source = _safe_policy_v3_dashboard_source()
        for regime, policies in sorted(
            ((legacy_source.get("screen") or {}).get("dynamic_regime_leaders") or {}).items()
        ):
            supported = [
                policy for policy in (policies or [])
                if policy.get("expectancy_lcb_usd") is not None
                or policy.get("sealed_oos_net_usd") is not None
            ]
            if supported:
                legacy_rows.append({
                    "regime": regime,
                    "policies": [_public_policy_evidence_row(policy) for policy in supported],
                })
    current_epoch = (
        report.get("dataset_epoch")
        or ((legacy_source or {}).get("epoch_id"))
        or (current_manifest.get("fresh_epoch") or {}).get("epoch_id")
        or current_manifest.get("dataset_epoch")
    )
    blockers = list(report.get("blockers") or [])
    status = str(report.get("status") or "UNKNOWN")
    sealed = report.get("sealed_holdout") or {}
    protocol = report.get("nested_protocol") or {}
    qualified = bool(
        status == "PASS"
        and sealed.get("qualification_eligible") is True
        and sealed.get("sealed_holdout_evaluation_verified") is True
    )
    return jsonify({
        "schema": "dynamic_policy_dashboard_v3_1",
        "evidence_source": (
            DYNAMIC_POLICY_ANALYSIS_REPORT_FILE
            if report or current_manifest else SAFE_POLICY_GENOME_V3_REPORT_FILE
        ),
        "collector_generation": "V3.1",
        "status": status,
        "qualification": "RESEARCH_PASS" if qualified else "UNKNOWN",
        "purpose": "RESEARCH_ONLY_NOT_RELAY_ELIGIBLE",
        "relay_eligible": False,
        "live_policy_change_allowed": False,
        "epoch_id": current_epoch,
        "policy_epoch_id": None,
        "winner_kind": "DYNAMIC_RESEARCH" if qualified else "NONE",
        "winner_status": "SEALED_RESEARCH_PASS" if qualified else "NO_QUALIFIED_OOS_WINNER",
        "relative_leader_kind": "DYNAMIC" if legacy_rows else "DYNAMIC_RESEARCH" if qualified else "NONE",
        "relative_leader_status": "DESCRIPTIVE_ONLY" if legacy_rows else "SEALED_EVALUATION_AVAILABLE" if qualified else "UNKNOWN",
        "comparison_delta": {},
        "static_oos": None,
        "dynamic_oos": {
            "nested_protocol_passed": protocol.get("passed"),
            "outer_fold_count": len(protocol.get("folds") or []),
            "sealed_holdout_evaluation_verified": sealed.get("sealed_holdout_evaluation_verified"),
            "qualification_eligible": sealed.get("qualification_eligible"),
        },
        "regimes": legacy_rows,
        "required_runtime_regimes": [
            "WEAKENING", "TRANSITION", "RANGE", "COMPRESSION", "EXPANSION", "TRENDING",
        ],
        "bull_bear_range_projection": None,
        "fallback": "CONTROL_OR_NO_TRADE",
        "unseen_cell_fallback": "NO_TRADE",
        "warning": (
            "No qualified dynamic OOS winner. Dynamic policy evidence is UNKNOWN until the checksum-bound canonical input, "
            "nested purged folds, and exact sealed holdout all verify. It remains research-only."
        ) if not qualified else (
            "Sealed research evaluation passed; this artifact is still relay-ineligible and cannot change live policy."
        ),
        "blockers": blockers or (
            list((legacy_source or {}).get("blockers") or [])
            if legacy_source else ([] if qualified else ["REPORT_NOT_IN_CURRENT_GENERATION"])
        ),
        "generation_revision": report.get("generation_revision"),
        "input_receipt": report.get("input_receipt") or {},
        "full_artifact": f"/api/report/{DYNAMIC_POLICY_ANALYSIS_REPORT_FILE}",
    })


@app.route("/api/shadow-policy-research")
def api_shadow_policy_research():
    source = _safe_policy_v3_dashboard_source()
    report = source["report"]
    collection = report.get("collection") or {}
    paused = _read_report("paused_shadow_research_report.json", {})
    real_edge = _read_report("real_edge_summary.json", {})
    comprehensive = _read_report("shadow_lane_comprehensive_report.json", {}) or {}
    chase_threshold = _read_report("chase_threshold_report.json", {}) or {}
    expected_epoch = str(source["epoch_id"] or "")
    comprehensive_epoch = str(
        (comprehensive.get("epoch_scope") or {}).get("selected_epoch_id")
        or comprehensive.get("epoch_id")
        or ""
    )
    expected_revision = str(report.get("generation_revision") or "")
    comprehensive_revision = str(comprehensive.get("generation_revision") or "")
    shadow_mismatch_reasons = []
    if not comprehensive:
        shadow_mismatch_reasons.append("REPORT_MISSING")
    if comprehensive and comprehensive_epoch != expected_epoch:
        shadow_mismatch_reasons.append("EPOCH_MISMATCH")
    if (
        comprehensive and expected_revision and comprehensive_revision
        and comprehensive_revision != expected_revision
    ):
        shadow_mismatch_reasons.append("GENERATION_REVISION_MISMATCH")
    comprehensive_envelope = {
        "available": not shadow_mismatch_reasons,
        "status": "CURRENT" if not shadow_mismatch_reasons else "UNAVAILABLE_STALE_OR_MISSING",
        "reason": ",".join(shadow_mismatch_reasons) if shadow_mismatch_reasons else None,
        "expected_epoch_id": expected_epoch or None,
        "report_epoch_id": comprehensive_epoch or None,
        "expected_generation_revision": expected_revision or None,
        "report_generation_revision": comprehensive_revision or None,
        "coverage": (comprehensive.get("coverage") or {}) if not shadow_mismatch_reasons else {},
        "epoch_scope": (comprehensive.get("epoch_scope") or {}) if not shadow_mismatch_reasons else {},
        "cohorts": list(comprehensive.get("cohorts") or []) if not shadow_mismatch_reasons else [],
        "legacy_unscoped_cohorts": list(
            comprehensive.get("legacy_unscoped_cohorts") or []
        ) if not shadow_mismatch_reasons else [],
    }
    chase_coverage = chase_threshold.get("coverage") or {}
    generic_shadow = {
        "status": "SEPARATE_GENERIC_COUNTERFACTUAL_COHORT",
        "terminal_outcomes": int(chase_coverage.get("shadow_terminal_outcomes") or 0),
        "generic_terminal_outcomes": int(
            chase_coverage.get("generic_shadow_counterfactuals") or 0
        ),
        "tile_lab_terminal_outcomes": int(
            chase_coverage.get("tile_lab_shadow_outcomes") or 0
        ),
        "source": "chase_threshold_report.json",
    }
    legacy_detail = _read_report("policy_candidate_oos_report.json", {})
    legacy_shadow = legacy_detail.get("shadow_research") or {}
    return jsonify({
        "schema": "shadow_policy_dashboard_v3_1",
        "evidence_source": "safe_policy_genome_v3_report.json",
        "collector_generation": "V3.1",
        "status": "DESCRIPTIVE_ONLY",
        "live_policy_change_allowed": False,
        "epoch_id": source["epoch_id"],
        "current_epoch_rejected": int((collection.get("decision_outcomes") or {}).get("REJECTED") or 0),
        "current_v3_1_collection": collection,
        "evidence_classes": {
            "shadow_counterfactual": {
                "status": "DESCRIPTIVE_ONLY",
                "pnl_kind": "SIMULATED_COUNTERFACTUAL",
                "merged_with_executed": False,
            },
            "executed_paper": {
                "status": "SEPARATE_COHORT",
                "pnl_kind": "EXECUTED_PAPER",
                "merged_with_shadow": False,
            },
        },
        "v22_shadow": {},
        "paused_shadow": paused,
        "comprehensive_shadow_lanes": comprehensive_envelope,
        "generic_shadow_terminals": generic_shadow,
        "real_edge": real_edge,
        "legacy_v22_excluded": {
            "status": "RETIRED_V2_2_EXCLUDED_FROM_CURRENT_QUALIFICATION",
            "shadow_research": legacy_shadow,
        },
        "blockers": source["blockers"],
        "warning": (
            "Current signed V3.1 shadow and rejected paths are shown separately from retired V2.2. "
            "Counterfactual PnL is never merged with executed PnL and cannot authorize a live policy."
        ),
    })


def _research_page(title: str, endpoint: str, mode: str):
    return render_template_string("""
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title><style>
*{box-sizing:border-box}html,body{width:100%;max-width:100%;overflow-x:hidden}body{font-family:system-ui;background:#0d1117;color:#e6edf3;margin:0;padding:24px}a{color:#58a6ff}
.wrap{width:100%;max-width:1500px;min-width:0;margin:auto;overflow:hidden}.note{max-width:100%;overflow-wrap:anywhere;padding:14px;border:1px solid #8b6f19;background:#2d260f;border-radius:8px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(170px,100%),1fr));gap:12px;margin:16px 0}.kpi{min-width:0;overflow-wrap:anywhere;background:#161b22;border:1px solid #30363d;padding:12px;border-radius:8px}
table{display:block;width:100%;max-width:100%;overflow-x:auto;border-collapse:collapse;background:#161b22}th,td{padding:9px;border:1px solid #30363d;text-align:left;font-size:13px;white-space:nowrap}th{background:#21262d}.bad{color:#f2cc60}.good{color:#3fb950}
@media(max-width:600px){body{padding:12px}.kpis{grid-template-columns:minmax(0,1fr)}h1{font-size:1.45rem}}
</style></head><body><div class="wrap"><p><a href="/">← Research Dashboard</a></p><h1>{{ title }}</h1>
<div id="note" class="note">Loading current-epoch evidence…</div><div id="kpis" class="kpis"></div><table><thead id="head"></thead><tbody id="body"></tbody></table></div>
<script>
const mode={{ mode|tojson }}; const endpoint={{ endpoint|tojson }};
const money=v=>v==null?'—':'$'+Number(v).toFixed(4); const pct=(w,n)=>n?((100*w/n).toFixed(1)+'%'):'—';
fetch(endpoint).then(r=>r.json()).then(d=>{
 document.getElementById('note').textContent=(d.warning||'')+' Status: '+(d.status||'—')+' · Live policy changes: '+(d.live_policy_change_allowed?'YES':'NO');
 let cards=[]; let rows=[];
 if(mode==='static'){
  cards=[['Epoch',d.epoch_id],['Independent episodes',d.independent_episodes],['Train / OOS',d.training_episodes+' / '+d.oos_episodes],['Profitable descriptive policies',(d.profitable_policies||[]).length]];
  document.getElementById('head').innerHTML='<tr><th>Policy</th><th>Train N</th><th>Train WR</th><th>Train PnL</th><th>OOS N</th><th>OOS WR</th><th>OOS PnL</th><th>OOS EV</th><th>Drawdown</th><th>Status</th></tr>';
  rows=(d.profitable_policies||[]).map(x=>`<tr><td>${x.policy_id}</td><td>${x.training_episodes??'—'}</td><td>—</td><td>—</td><td>${x.oos_episodes??0}</td><td>—</td><td>${money(x.sealed_oos_net_usd)}</td><td>${money(x.expectancy_lcb_usd)}</td><td>${money(x.max_drawdown_usd)}</td><td class="bad">${x.qualification||'DESCRIPTIVE_ONLY'}</td></tr>`);
 } else if(mode==='dynamic'){
  cards=[['Epoch',d.epoch_id],['Qualified OOS winner',d.winner_kind==='NONE'?'NONE — qualification incomplete':(d.winner_kind||'NONE')],['Descriptive regime leader',d.relative_leader_kind||'NONE'],['Static comparison EV',money((d.static_oos||{}).expectancy_usd)],['Dynamic comparison EV',money((d.dynamic_oos||{}).expectancy_usd)],['Required markets',(d.required_market_families||[]).join(' / ')]];
  document.getElementById('head').innerHTML='<tr><th>Market regime</th><th>Selected policy</th><th>Train N</th><th>Train PnL</th><th>OOS N</th><th>OOS PnL</th><th>OOS EV</th><th>Fallback</th><th>Status</th></tr>';
  rows=(d.regimes||[]).flatMap(group=>(group.policies||[]).map(x=>`<tr><td>${group.regime}</td><td>${x.policy_id}</td><td>${x.training_episodes??'—'}</td><td>—</td><td>${x.oos_episodes??0}</td><td>${money(x.sealed_oos_net_usd)}</td><td>${money(x.expectancy_lcb_usd)}</td><td>NO</td><td class="bad">${x.qualification||'DESCRIPTIVE_ONLY'}</td></tr>`));
 } else {
  const s=d.v22_shadow||{}, c=d.comprehensive_shadow_lanes||{}, cov=c.coverage||{}, scope=c.epoch_scope||{}, g=d.generic_shadow_terminals||{}, p=d.paused_shadow||{}, o=p.overall||{}, re=d.real_edge||{}, signedAvailable=c.available===true;
  cards=[['Current rejected paths',d.current_epoch_rejected??'UNAVAILABLE'],['Signed per-lane shadow episodes',signedAvailable?(cov.independent_shared_ai_episodes??0):('UNAVAILABLE · '+(c.reason||'report missing'))],['Signed per-lane records',signedAvailable?(cov.deduped_lane_records??0):'UNAVAILABLE'],['Generic shadow terminal outcomes',g.terminal_outcomes??'UNAVAILABLE'],['Generic counterfactual terminals',g.generic_terminal_outcomes??'UNAVAILABLE'],['Tile LAB shadow terminals',g.tile_lab_terminal_outcomes??'UNAVAILABLE'],['Preserved legacy/unscoped',signedAvailable?(scope.legacy_unscoped_rows??0):'UNAVAILABLE'],['Foreign / malformed',signedAvailable?(Number(scope.foreign_epoch_rows||0)+Number(scope.malformed_current_identity_rows||0)):'UNAVAILABLE'],['Paired signed episodes',signedAvailable?(cov.paired_multi_lane_episodes??0):'UNAVAILABLE'],['Provisional exclusions',signedAvailable?(c.cohorts||[]).reduce((n,x)=>n+(x.provisional_excluded||0),0):'UNAVAILABLE'],['Executed PnL (separate)',money(re.executed_pnl_usd)]];
  document.getElementById('head').innerHTML='<tr><th>Policy / lane</th><th>Episodes</th><th>Fills</th><th>Wins</th><th>Losses</th><th>Net PnL</th><th>EV</th><th>Status</th></tr>';
  rows=signedAvailable?(c.cohorts||[]).map(x=>`<tr><td>${x.research_lane}<br><small>${x.classification}</small></td><td>${x.independent_shared_ai_episodes}</td><td>${x.completed_terminal_fills}/${x.fills}</td><td>${x.wins}</td><td>${x.losses}</td><td>${money(x.net_pnl_usd)}</td><td>${money(x.ev_per_completed_fill_usd)}</td><td class="bad">${x.qualification}; provisional excluded ${x.provisional_excluded}</td></tr>`):[`<tr><td colspan="8">Signed per-lane shadow cohort unavailable: ${c.reason||'report missing'}. Generic terminal counts remain separate above.</td></tr>`];
  rows=rows.concat((c.legacy_unscoped_cohorts||[]).map(x=>`<tr><td>${x.research_lane}<br><small>${x.classification}</small></td><td>${x.independent_shared_ai_episodes}</td><td>${x.completed_terminal_fills}/${x.fills}</td><td>${x.wins}</td><td>${x.losses}</td><td>${money(x.net_pnl_usd)}</td><td>${money(x.ev_per_completed_fill_usd)}</td><td class="bad">LEGACY UNSCOPED — preserved, excluded from current signed cohort</td></tr>`));
  rows=rows.concat((s.profitable_policies||[]).map(x=>`<tr><td>${x.policy_id}<br><small>REJECTED V2.2 POLICY REPLAY</small></td><td>${x.independent_episodes}</td><td>${x.fills}</td><td>${x.wins}</td><td>${x.losses}</td><td>${money(x.net_pnl_usd)}</td><td>${money(x.expectancy_usd)}</td><td class="bad">${x.qualification}</td></tr>`));
 }
 document.getElementById('kpis').innerHTML=cards.map(x=>`<div class="kpi"><small>${x[0]}</small><div>${x[1]??'—'}</div></div>`).join('');
 document.getElementById('body').innerHTML=rows.join('')||'<tr><td colspan="10">Waiting for sufficient current-epoch evidence.</td></tr>';
});
</script></body></html>
""", title=title, endpoint=endpoint, mode=mode)


@app.route("/static-policies")
def static_policy_page():
    return _research_page("Static Profitable Policy Research", "/api/static-policy-research", "static")


@app.route("/dynamic-policies")
def dynamic_policy_page():
    return _research_page("Dynamic Market-Regime Policy Research", "/api/dynamic-policy-research", "dynamic")


@app.route("/shadow-research")
def shadow_policy_page():
    return _research_page("Shadow and Rejected-Opportunity Research", "/api/shadow-policy-research", "shadow")


def _v31_evidence_payload(kind: str) -> dict:
    report = dict(_current_generation_report(SAFE_POLICY_GENOME_V3_REPORT_FILE))
    screen = report.get("candidate_screen") or {}
    collection = report.get("collection") or {}
    base = {
        "schema": f"v31_{kind}_dashboard_v1",
        "status": "DESCRIPTIVE_ONLY",
        "qualification": report.get("qualification") or "NO_SAFE_QUALIFIED_POLICY",
        "live_policy_change_allowed": False,
        "epoch_id": report.get("epoch_id") or (report.get("epoch_scope") or {}).get("selected_epoch_id"),
        "generated_at": report.get("generated_at"),
        "blockers": report.get("blockers") or ["CURRENT_V3_1_EVIDENCE_IMMATURE"],
    }
    if kind == "risk_drawdown":
        rows = screen.get("drawdown_control_leaders") or screen.get("descriptive_top_100") or []
        return {**base, "rows": [_public_policy_evidence_row(row) for row in rows[:100]], "warning": "Risk metrics are descriptive current-epoch evidence; no row authorizes live trading."}
    if kind == "chronological_oos":
        return {
            **base,
            "rows": (screen.get("descriptive_top_100") or [])[:100],
            "warning": "Chronological train/OOS rows remain blocked until sealed holdout, regime coverage, independence and minimum-episode gates pass.",
        }
    if kind == "maturity":
        return {
            **base,
            "collection": collection,
            "unique_policies_evaluated": screen.get("unique_policies_evaluated") or 0,
            "qualified_policies": len(report.get("qualified_policies") or []),
            "warning": "Opportunity counts, execution rows and terminal lifecycles are separate denominators; they must not be presented as interchangeable trades.",
        }
    _old_exit, old_meta = _read_contract_receipt("exit_reports_validation.json")
    return {
        **base,
        "status": "NOT_PROVEN",
        "relay_eligible": False,
        "tiles": {
            lane: (spec.get("relay_capability") or "FAIL_CLOSED")
            for lane, spec in ACTIVE_TILE_REGISTRY.items()
        },
        "gates": {
            "idempotent_reduce_only_partial_exits": False,
            "correct_remaining_quantity": False,
            "retry_duplicate_prevention": False,
            "exchange_reconciliation": False,
            "restart_recovery": False,
            "terminal_pnl_reconciliation": False,
        },
        "legacy_receipt": old_meta,
        "warning": "No current signed V3.1 partial-reduction lifecycle proves relay safety. Historical receipts are retained only as stale contract evidence.",
    }


def _v31_evidence_page(title: str, endpoint: str, kind: str):
    return render_template_string("""
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{ title }}</title>
<style>*{box-sizing:border-box}html,body{width:100%;max-width:100%;overflow-x:hidden}body{font-family:system-ui;background:#0d1117;color:#e6edf3;margin:0;padding:24px}a{color:#58a6ff}.wrap{width:100%;max-width:1500px;min-width:0;margin:auto;overflow:hidden}.banner,.card{min-width:0;max-width:100%;overflow-wrap:anywhere;border:1px solid #30363d;background:#161b22;border-radius:9px;padding:14px;margin:12px 0}.banner{border-color:#d29922}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(190px,100%),1fr));gap:10px}.scroll{display:block;width:100%;max-width:100%;overflow-x:auto}table{width:100%;border-collapse:collapse}th,td{padding:8px;border:1px solid #30363d;text-align:left;white-space:nowrap;font-size:13px}th{background:#21262d}pre{white-space:pre-wrap;overflow-wrap:anywhere}@media(max-width:600px){body{padding:12px}.grid{grid-template-columns:minmax(0,1fr)}h1{font-size:1.45rem}}</style></head>
<body><div class="wrap"><p><a href="/">← Research Dashboard</a></p><h1>{{ title }}</h1><div id="banner" class="banner">Loading current signed V3.1 evidence…</div><div id="grid" class="grid"></div><div class="card"><div class="scroll"><table><thead id="head"></thead><tbody id="rows"></tbody></table></div><pre id="detail"></pre></div></div>
<script>const kind={{ kind|tojson }};fetch({{ endpoint|tojson }}).then(r=>r.json()).then(d=>{document.getElementById('banner').textContent=(d.status||'—')+' · '+(d.qualification||'—')+' · '+(d.warning||'');const c=d.collection||{};let cards=[['Epoch',d.epoch_id||'—'],['Generated',d.generated_at||'—'],['Live policy changes',d.live_policy_change_allowed?'YES':'NO']];let rows=[];if(kind==='maturity'){cards=cards.concat([['Independent opportunities',c.independent_opportunities||0],['Decision branches',c.decision_branches||0],['Execution rows',c.execution_rows||0],['Provisional lifecycles',c.provisional_lifecycles||0],['Terminal lifecycles',c.terminal_lifecycles||0],['Market segments',c.market_segments||0],['Policies evaluated',d.unique_policies_evaluated||0],['Qualified policies',d.qualified_policies||0]]);document.getElementById('head').innerHTML='<tr><th>Evidence blocker</th></tr>';rows=(d.blockers||[]).map(x=>'<tr><td>'+x+'</td></tr>');}else if(kind==='partial_reduction'){cards=cards.concat([['Relay eligible',d.relay_eligible?'YES':'NO']]);document.getElementById('head').innerHTML='<tr><th>Safety gate</th><th>Passed</th></tr>';rows=Object.entries(d.gates||{}).map(([k,v])=>'<tr><td>'+k+'</td><td>'+ (v?'PASS':'NOT PROVEN')+'</td></tr>');}else{document.getElementById('head').innerHTML='<tr><th>Policy</th><th>Family</th><th>Total episodes</th><th>OOS episodes</th><th>OOS net</th><th>Max drawdown</th><th>CVaR95</th><th>Failed gates</th></tr>';rows=(d.rows||[]).map(x=>'<tr><td>'+String(x.policy_id||'—')+'</td><td>'+String(x.policy_family||'—')+'</td><td>'+String(x.episodes_total??'—')+'</td><td>'+String(x.oos_episodes??'—')+'</td><td>'+String(x.sealed_oos_net_usd??'—')+'</td><td>'+String(x.max_drawdown_usd??'—')+'</td><td>'+String(x.cvar95_usd??'—')+'</td><td>'+Object.entries(x.gates||{}).filter(y=>y[1]!==true).map(y=>y[0]).join(', ')+'</td></tr>');}document.getElementById('grid').innerHTML=cards.map(x=>'<div class="card"><small>'+x[0]+'</small><div>'+x[1]+'</div></div>').join('');document.getElementById('rows').innerHTML=rows.join('')||'<tr><td colspan="8">No current qualified evidence yet.</td></tr>';document.getElementById('detail').textContent=JSON.stringify({blockers:d.blockers,legacy_receipt:d.legacy_receipt,tiles:d.tiles},null,2);}).catch(e=>{document.getElementById('banner').textContent='LOAD FAILED · '+e;});</script></body></html>
""", title=title, endpoint=endpoint, kind=kind)


@app.route("/api/risk-drawdown")
def api_risk_drawdown(): return jsonify(_v31_evidence_payload("risk_drawdown"))

@app.route("/risk-drawdown")
def risk_drawdown_page(): return _v31_evidence_page("V3.1 Risk and Drawdown", "/api/risk-drawdown", "risk_drawdown")

@app.route("/api/chronological-oos")
def api_chronological_oos(): return jsonify(_v31_evidence_payload("chronological_oos"))

@app.route("/chronological-oos")
def chronological_oos_page(): return _v31_evidence_page("V3.1 Chronological OOS", "/api/chronological-oos", "chronological_oos")

@app.route("/api/evidence-maturity")
def api_evidence_maturity(): return jsonify(_v31_evidence_payload("maturity"))

@app.route("/evidence-maturity")
def evidence_maturity_page(): return _v31_evidence_page("V3.1 Evidence Maturity", "/api/evidence-maturity", "maturity")

@app.route("/api/partial-reduction")
def api_partial_reduction(): return jsonify(_v31_evidence_payload("partial_reduction"))

@app.route("/partial-reduction")
def partial_reduction_page(): return _v31_evidence_page("V3.1 Partial-Reduction Reconciliation", "/api/partial-reduction", "partial_reduction")


@app.route("/api/summary")
def api_summary():
    compact = _read_json(COMPACT_SUMMARY_FILE)
    manifest = _read_json(REPORT_MANIFEST_FILE, {}) or {}
    real = _read_json("real_edge_summary.json")
    historical = _read_json(HISTORICAL_COHORT_REPORT_FILE)
    retention = _read_json(RETENTION_STATUS_FILE)
    mirror_size = {}
    mirror_size_path = DATA_ROOT / MIRROR_SIZE_REPORT_FILE
    if mirror_size_path.is_file():
        try:
            # Windows PowerShell 5.1's ``-Encoding UTF8`` emits a BOM.  Accept
            # existing receipts while the sync writer now emits BOM-free UTF-8.
            mirror_size = json.loads(mirror_size_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, TypeError):
            mirror_size = {}
    stale_meta = _summary_stale_meta(compact)
    # The atomic manifest is the authority for the current generation.  An old
    # real_edge artifact used to repopulate an intentionally empty fresh epoch
    # (for example 0 current trades became 44 historical trades).  Keep such an
    # artifact on disk, but never expose its metrics as current dashboard data.
    manifest_performance = manifest.get("performance")
    p = dict(
        manifest_performance
        if isinstance(manifest_performance, dict)
        else (compact.get("performance") or {})
    )
    current_revision = str(manifest.get("generation_revision") or "").strip()
    current_epoch = str((manifest.get("fresh_epoch") or {}).get("epoch_id") or "").strip()
    lifecycle_inventory = _read_report(LIFECYCLE_BUNDLE_INVENTORY_REPORT_FILE)
    lifecycle_provenance = (
        lifecycle_inventory.get("analysis_provenance")
        if isinstance(lifecycle_inventory, dict) else {}
    ) or {}
    lifecycle_revision = str(
        lifecycle_provenance.get("generation_revision") or ""
    ).strip()
    lifecycle_epoch = str(
        lifecycle_provenance.get("fresh_epoch_id")
        or lifecycle_provenance.get("dataset_epoch")
        or ""
    ).strip()
    transfer_inventory = (
        lifecycle_inventory.get("transfer")
        if isinstance(lifecycle_inventory, dict) else {}
    ) or {}
    lifecycle_current = bool(
        lifecycle_inventory.get("schema") == "lifecycle_bundle_inventory_v1"
        and lifecycle_inventory.get("inventory_scope") == "MANIFEST_ONLY"
        and lifecycle_inventory.get("complete") is True
        and lifecycle_inventory.get("complete_scope") == "MANIFEST_INVENTORY"
        and lifecycle_inventory.get("payload_verification_status") == "UNKNOWN_NOT_SCANNED"
        and lifecycle_inventory.get("payload_files_read") == 0
        and (lifecycle_inventory.get("scan") or {}).get("truncated") is False
        and current_revision and lifecycle_revision == current_revision
        and current_epoch and lifecycle_epoch == current_epoch
        and transfer_inventory.get("audit_only") is True
        and transfer_inventory.get("ranking_eligible") is False
        and transfer_inventory.get("profitability_supported") is False
        and transfer_inventory.get("source_cleanup_authorized") is False
    )
    def _bundle_count(value):
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if number >= 0 else None

    qualification_count = _bundle_count(
        (lifecycle_inventory.get("qualification") or {}).get("unique_lifecycle_count")
    )
    transfer_count = _bundle_count(transfer_inventory.get("unique_lifecycle_count"))
    invalid_count = _bundle_count(lifecycle_inventory.get("invalid_manifest_count"))
    raw_parity = lifecycle_inventory.get("parity") or {}
    safe_parity = {
        key: _bundle_count(raw_parity.get(key, 0))
        for key in (
            "intersection_count",
            "qualification_only_count",
            "transfer_only_count",
            "provenance_mismatch_count",
        )
    }
    payload_verification_status = str(
        lifecycle_inventory.get("payload_verification_status")
        or "UNKNOWN_NOT_SCANNED"
    )
    lifecycle_current = bool(
        lifecycle_current
        and qualification_count is not None
        and transfer_count is not None
        and invalid_count is not None
        and all(value is not None for value in safe_parity.values())
    )
    if lifecycle_current:
        lifecycle_bundle_summary = {
            "status": "AVAILABLE_CURRENT_GENERATION",
            "qualification_count": qualification_count,
            "transfer_audit_count": transfer_count,
            "parity": safe_parity,
            "invalid_count": invalid_count,
            "qualification_label": "manifest-verified qualification bundles",
            "transfer_label": "transfer-ready audit copies",
            "payload_verification_status": payload_verification_status,
            "transfer_audit_only": True,
            "transfer_ranking_eligible": False,
            "transfer_profitability_supported": False,
            "transfer_source_cleanup_authorized": False,
        }
    else:
        lifecycle_bundle_summary = {
            "status": "UNAVAILABLE_CURRENT_GENERATION",
            "qualification_count": None,
            "transfer_audit_count": None,
            "parity": {},
            "invalid_count": None,
            "qualification_label": "manifest-verified qualification bundles",
            "transfer_label": "transfer-ready audit copies",
            "payload_verification_status": "UNKNOWN_NOT_SCANNED",
            "transfer_audit_only": True,
            "transfer_ranking_eligible": False,
            "transfer_profitability_supported": False,
            "transfer_source_cleanup_authorized": False,
            "reason": "REPORT_NOT_IN_CURRENT_GENERATION",
        }

    def _real_edge_identity(payload):
        payload = payload if isinstance(payload, dict) else {}
        provenance = payload.get("analysis_provenance") or {}
        revision = str(
            payload.get("generation_revision")
            or provenance.get("generation_revision")
            or ""
        ).strip()
        epoch = str(
            payload.get("epoch_id")
            or (payload.get("fresh_epoch") or {}).get("epoch_id")
            or provenance.get("fresh_epoch_id")
            or ""
        ).strip()
        return revision, epoch

    rejected_real_edge = []
    re = None
    for source, candidate in (
        ("research_compact_summary.json.real_edge", compact.get("real_edge")),
        ("real_edge_summary.json", real),
    ):
        if not isinstance(candidate, dict) or not candidate:
            continue
        revision, epoch = _real_edge_identity(candidate)
        revision_matches = bool(current_revision and revision and revision == current_revision)
        epoch_matches = bool(current_epoch and epoch and epoch == current_epoch)
        if revision_matches and epoch_matches:
            re = candidate
            break
        rejected_real_edge.append({
            "source": source,
            "generation_revision": revision or None,
            "epoch_id": epoch or None,
            "reason": "CROSS_GENERATION_IDENTITY_MISMATCH",
        })
    if re is None:
        re = {
            "schema": "current_real_edge_unavailable_v1",
            "status": "UNAVAILABLE_CURRENT_GENERATION",
            "qualification_eligible": False,
            "generation_revision": current_revision or None,
            "epoch_id": current_epoch or None,
            "excluded_candidates": rejected_real_edge,
        }
    if (
        not isinstance(manifest_performance, dict)
        and not p.get("trades")
        and int(re.get("executed") or 0)
    ):
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
        "generated_at": manifest.get("generated_at") or compact.get("generated_at"),
        "performance": p,
        "performance_source": (
            "CURRENT_ATOMIC_MANIFEST"
            if isinstance(manifest_performance, dict)
            else "CURRENT_COMPACT_SUMMARY"
        ),
        "real_edge": re,
        "approve_to_fill_pct": fill_pct,
        "executive_text": _read_text(EXECUTIVE_SUMMARY_FILE),
        "coverage_status": (compact.get("coverage") or {}).get("confidence_status"),
        "stale": stale_meta,
        "integrity": _integrity_payload(),
        "all_data_fallback_active": all_data_active,
        "historical_cohort": historical,
        "retention": retention,
        "lifecycle_bundles": lifecycle_bundle_summary,
        "storage": {
            "mirror_identity": "LOCAL_CACHED_COPY_OF_FLY_RUNTIME_DATA",
            "mirror_path": str(DATA_ROOT),
            "report_path": str(ROOT),
            "local_size_mb": mirror_size.get("local_size_mb"),
            "local_file_count": mirror_size.get("local_file_count"),
            "local_limit_mb": int(retention.get("raw_mirror_cap_gib") or 25) * 1024,
            "local_limit_pct": round(
                100.0 * float(mirror_size.get("local_size_mb") or 0)
                / (int(retention.get("raw_mirror_cap_gib") or 25) * 1024), 2
            ),
            "fly_size_mb": mirror_size.get("fly_size_mb"),
            "fly_volume_total_mb": mirror_size.get("fly_volume_total_mb"),
            "fly_volume_pct": mirror_size.get("fly_volume_pct"),
            "sync_computed_at": mirror_size.get("computed_at"),
            "fly_computed_at": mirror_size.get("fly_computed_at"),
            "sync_interval_seconds": mirror_size.get("sync_interval_seconds"),
            "sync_threshold_mb": mirror_size.get("sync_threshold_mb"),
            "categories": {
                label: (
                    {
                        "status": "OBSERVED",
                        "bytes": int(mirror_size.get(field)),
                        "mb": round(int(mirror_size.get(field)) / 1048576, 3),
                    }
                    if isinstance(mirror_size.get(field), (int, float))
                    and not isinstance(mirror_size.get(field), bool)
                    and float(mirror_size.get(field)) >= 0
                    else {
                        "status": "UNKNOWN",
                        "bytes": None,
                        "mb": None,
                        "reason": "LIFECYCLE_STORAGE_CLASSIFICATION_NOT_IN_SYNC_RECEIPT",
                    }
                )
                for label, field in (
                    ("active_lifecycle", "active_lifecycle_bytes"),
                    ("completed_unsynchronized", "completed_unsynchronized_bytes"),
                    ("downloaded_unacknowledged", "downloaded_unacknowledged_bytes"),
                    ("acknowledged_cleanup_eligible", "acknowledged_cleanup_eligible_bytes"),
                    ("protected_recovery", "protected_recovery_bytes"),
                    ("unknown_unclassified", "unknown_unclassified_bytes"),
                )
            },
        },
    })


@app.route("/api/findings")
def api_findings():
    return jsonify(_findings_payload())



def _wants_all_lanes() -> bool:
    """Power-user override: ?all=1 or ?show_all=1 shows non-AI historical lanes."""
    try:
        flag = (request.args.get("all") or request.args.get("show_all") or "").strip().lower()
    except Exception:
        flag = ""
    return flag in ("1", "true", "yes", "all")


def _filter_lane_rows(rows, *, all_lanes: bool = False):
    """Default to the canonical active tile roster; history remains opt-in."""
    if all_lanes:
        return list(rows or [])
    out = []
    for row in rows or []:
        lane = ""
        if isinstance(row, dict):
            lane = row.get("lane") or row.get("research_lane") or ""
        else:
            lane = str(row)
        if str(lane).upper().strip() in CURRENT_RESEARCH_LANES:
            out.append(row)
    return out


@app.route("/api/lanes")
def api_lanes():
    rows, bench_pnl, evidence = _lane_rows(include_evidence=True)
    freshness = _generation_freshness_meta()
    if evidence.get("status") == "CURRENT_GENERATION" and not freshness["current"]:
        evidence = dict(evidence or {})
        artifact_status = evidence.get("status")
        blockers = list(evidence.get("blockers") or [])
        blockers.extend(freshness.get("reasons") or [])
        evidence.update({
            "status": "STALE_GENERATION",
            "artifact_status": artifact_status,
            "generation_freshness": freshness,
            "blockers": list(dict.fromkeys(str(item) for item in blockers if item)),
        })
    all_lanes = _wants_all_lanes()
    filtered = _filter_lane_rows(rows, all_lanes=all_lanes)
    return jsonify({
        "lanes": filtered,
        "benchmark_pnl": bench_pnl,
        "lane_filter": "all" if all_lanes else "active_tile_registry",
        "lane_filter_note": (
            "Showing all historical lanes"
            if all_lanes
            else "Current active research stack is derived from the canonical tile registry."
        ),
        "primary_lanes": list(DASHBOARD_PRIMARY_LANES),
        "evidence_status": evidence["status"],
        "evidence": evidence,
    })


@app.route("/api/chase")
def api_chase():
    lane = request.args.get("lane") or ""
    return jsonify(_chase_payload(lane=lane))


@app.route("/api/combos")
def api_combos():
    return jsonify(_combos_payload())


@app.route("/api/spread-performance")
def api_spread_performance():
    return jsonify(_spread_performance_payload())


@app.route("/api/chase-threshold")
def api_chase_threshold():
    lane = request.args.get("lane") or ""
    return jsonify(_chase_threshold_payload(lane=lane))


@app.route("/api/chase-delay")
def api_chase_delay():
    return jsonify(_chase_delay_payload())


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


def _genome_payload():
    # V3.1 Safe Policy Genome is the canonical current collector/analyzer
    # surface. The older research.db DNA engine is a legacy fallback only.
    safe_v31 = _read_json(SAFE_POLICY_GENOME_V3_REPORT_FILE) or {}
    if safe_v31.get("schema"):
        # The dashboard overview needs a bounded summary, not the complete
        # chase x stop grid. Shipping the full candidate screen made a simple
        # tab click parse/render more than a megabyte of nested policy cells.
        # The dedicated Safe Policy Genome API/page and downloadable artifact
        # remain the detailed evidence surfaces.
        bounded = _bounded_safe_policy_payload(safe_v31)
        candidate_screen = dict(bounded.get("candidate_screen") or {})
        overview_fields = (
            "policy_id", "policy_family", "episodes_total", "oos_episodes",
            "sealed_oos_net_usd", "max_drawdown_usd",
            "diagnostic_replay_net_pnl_usd", "diagnostic_replay_max_drawdown_usd",
            "metric_evidence", "qualification_eligibility", "descriptive_blockers",
        )
        candidate_screen["descriptive_top_100"] = [
            {key: row.get(key) for key in overview_fields}
            for row in list(candidate_screen.get("descriptive_top_100") or [])[:20]
        ]
        candidate_screen.pop("drawdown_control_leaders", None)
        candidate_screen.pop("profit_capture_leaders", None)
        scenario_sweep = dict(candidate_screen.get("scenario_c_atr_stop_sweep") or {})
        scenario_sweep.pop("best_by_chase_and_stop", None)
        scenario_sweep.pop("overall_leaders", None)
        scenario_sweep.pop("leaders_by_stop", None)
        candidate_screen["scenario_c_atr_stop_sweep"] = scenario_sweep
        return {
            "schema": "genome_dashboard_v3_1_compat_v1",
            "available": True,
            "collector_generation": "V3.1",
            "evidence_source": SAFE_POLICY_GENOME_V3_REPORT_FILE,
            "status": safe_v31.get("status") or "COLLECTING",
            "qualification": safe_v31.get("qualification") or "NO_SAFE_QUALIFIED_POLICY",
            "generated_at": safe_v31.get("generated_at"),
            "epoch_id": safe_v31.get("epoch_id")
            or (safe_v31.get("epoch_scope") or {}).get("selected_epoch_id"),
            "policy_signature": safe_v31.get("policy_signature")
            or (safe_v31.get("epoch_scope") or {}).get("policy_signature"),
            "collection": safe_v31.get("collection") or {},
            "search_progress": safe_v31.get("search_progress") or {},
            "candidate_screen": candidate_screen,
            "safe_policy_ranking": bounded.get("safe_policy_ranking") or {},
            "integrity": safe_v31.get("integrity") or {},
            "blockers": list(safe_v31.get("blockers") or []),
            "number_one_strategy": safe_v31.get("number_one_strategy"),
            "live_policy_change_allowed": safe_v31.get("live_policy_change_allowed") is True,
            "legacy_genome": {
                "status": "RETIRED_RESEARCH_DB_DNA_EXCLUDED_FROM_V3_1_QUALIFICATION",
            },
            "warning": (
                "Current V3.1 Safe Policy Genome evidence. Descriptive rows do not "
                "authorize live trading until every chronological OOS and risk gate passes."
            ),
        }
    # Standalone mode sets ROOT/DATA_ROOT to the agent root, while the Genome
    # writer publishes under agent/research/genome. Embedded/legacy mode may
    # instead set ROOT directly to agent/research. Support both layouts so a
    # fresh Genome report cannot be hidden by the dashboard launch mode.
    source_status = _read_json(str(Path("research") / "genome" / "genome_source_status.json"))
    if not source_status:
        source_status = _read_json(str(Path("genome") / "genome_source_status.json"))
    unavailable = source_status.get("status") == "GENOME_SOURCE_UNAVAILABLE"
    candidates = (
        Path("research") / "genome" / "genome_analysis_report.json",
        Path("genome") / "genome_analysis_report.json",
        Path("research") / "genome_analysis_report.json",
        Path("genome_analysis_report.json"),
    )
    preserved = {}
    for candidate in candidates:
        rep = _read_json(str(candidate))
        if rep and rep.get("schema"):
            preserved = rep
            break
    if unavailable:
        return {
            "schema": "genome_dashboard_status_v1",
            "available": False,
            "status": "GENOME_SOURCE_UNAVAILABLE",
            "source_status": source_status,
            "preserved_report_available": bool(preserved),
            "preserved_report_generated_at": preserved.get("generated_at") if preserved else None,
            "warning": (
                "Current Genome conclusions are blocked because the required raw source tables are missing. "
                "Any preserved prior report is historical and is not rendered as current evidence."
            ),
        }
    if preserved:
        preserved = dict(preserved)
        preserved["available"] = True
        preserved["source_status"] = source_status or {"status": "SOURCE_STATUS_NOT_EMITTED"}
        return preserved
    return {
        "schema": "genome_dashboard_status_v1",
        "available": False,
        "status": "GENOME_SOURCE_UNAVAILABLE",
        "source_status": source_status or {"status": "SOURCE_STATUS_MISSING"},
        "preserved_report_available": False,
    }


@app.route("/api/genome")
def api_genome():
    return jsonify(_genome_payload())


@app.route("/api/features")
def api_features():
    return jsonify(_feature_payload())


@app.route("/api/ai")
def api_ai():
    return jsonify(_ai_payload())


@app.route("/api/manifest")
def api_manifest():
    return jsonify(_read_json(REPORT_MANIFEST_FILE))


@app.route("/api/research-design")
def api_research_design():
    """Expose signed research baselines and observed Phase-7 feature coverage."""
    from research_entry_baselines import ENTRY_BASELINE_REGISTRY

    report, source = _declared_atomic_generation_report(
        POLICY_EVIDENCE_LIBRARY_MANIFEST_FILE
    )
    baseline_replay, baseline_source = _declared_atomic_generation_report(
        "entry_baseline_replay_report.json"
    )
    manifest = source.get("manifest") or {}
    freshness = _generation_freshness_meta(manifest)
    evaluator = report.get("conservative_evaluator") if isinstance(report, dict) else {}
    evaluator = evaluator if isinstance(evaluator, dict) else {}
    coverage = evaluator.get("regime_feature_coverage")
    coverage_available = isinstance(coverage, dict)
    coverage = coverage if coverage_available else {
        "schema": "phase7_regime_feature_coverage_v1",
        "row_count": 0,
        "dimensions": [],
        "status": "UNKNOWN_CURRENT_GENERATION",
        "qualification_allowed": False,
        "profitability_calculated": False,
    }
    baselines = []
    replay_summaries = (
        baseline_replay.get("summaries")
        if isinstance(baseline_replay, dict) else {}
    )
    replay_summaries = replay_summaries if isinstance(replay_summaries, dict) else {}
    for definition in ENTRY_BASELINE_REGISTRY["baselines"]:
        row = {
            key: definition.get(key) for key in (
                "baseline_id", "entry_type", "timing", "policy_signature",
                "execution_class", "relay_eligible", "places_order",
                "missing_evidence_outcome", "required_evidence",
            )
        }
        row["replay_summary"] = replay_summaries.get(definition["baseline_id"]) or {
            "opportunities": 0, "full_fills": 0, "partial_fills": 0,
            "no_fills": 0, "unknown": 0,
        }
        baselines.append(row)
    available = (
        bool(report) and coverage_available
        and coverage.get("schema") == "phase7_regime_feature_coverage_v1"
    )
    return jsonify({
        "schema": "research_design_dashboard_v1",
        "available": available,
        "status": (
            "CURRENT" if available and freshness.get("current")
            else "STALE_CURRENT_GENERATION" if available
            else "UNAVAILABLE_CURRENT_GENERATION"
        ),
        "reason": None if report else source.get("reason") or "REPORT_UNAVAILABLE",
        "generation_id": manifest.get("generation_id"),
        "generation_revision": manifest.get("generation_revision"),
        "generation_freshness": freshness,
        "entry_baseline_registry_signature": ENTRY_BASELINE_REGISTRY["registry_signature"],
        "entry_baselines": baselines,
        "entry_baseline_replay": baseline_replay or {
            "schema": "entry_baseline_same_opportunity_replay_v1",
            "same_opportunity_count": 0,
            "summaries": {},
            "status": "UNKNOWN_CURRENT_GENERATION",
            "reason": baseline_source.get("reason") or "REPORT_UNAVAILABLE",
        },
        "regime_feature_coverage": coverage,
        "qualification_allowed": False,
        "profitability_calculated": False,
        "profitability_status": "NOT_CALCULATED_FROM_DEFINITIONS_OR_COVERAGE",
    })


@app.route("/api/evidence-coverage")
def api_evidence_coverage():
    """Bounded, read-only coverage summary from the declared atomic generation."""
    report, source = _declared_atomic_generation_report(
        EVIDENCE_COVERAGE_TRIAGE_REPORT_FILE
    )
    manifest = source.get("manifest") or {}
    freshness = _generation_freshness_meta(manifest)
    if not report:
        return jsonify({
            "schema": "evidence_coverage_dashboard_v1",
            "available": False,
            "status": "UNAVAILABLE_CURRENT_GENERATION",
            "reason": source.get("reason") or "REPORT_UNAVAILABLE",
            "generated_at": manifest.get("generated_at"),
            "generation_id": manifest.get("generation_id"),
            "generation_revision": manifest.get("generation_revision"),
            "generation_freshness": freshness,
            "qualification_allowed": False,
        })

    checksum_valid = False
    try:
        from research.evidence_coverage_triage import verify_report_checksum
        checksum_valid = verify_report_checksum(report)
    except Exception:
        checksum_valid = False
    totals = report.get("totals") if isinstance(report.get("totals"), dict) else {}
    source_counts = (
        report.get("authoritative_source_record_counts")
        if isinstance(report.get("authoritative_source_record_counts"), dict)
        else {}
    )
    outcomes = report.get("terminal_outcome_counts") or {}
    reasons = report.get("missing_evidence_reason_counts") or {}
    archive = report.get("archive_recovery_retention") or {}
    orphan = report.get("unresolved_episode") or {}
    valid = (
        report.get("schema") == "evidence_coverage_triage_report_v1"
        and checksum_valid
        and report.get("outcome_inference_performed") is False
        and report.get("missing_evidence_defaults_to") == "UNKNOWN"
    )
    if not valid:
        return jsonify({
            "schema": "evidence_coverage_dashboard_v1",
            "available": False,
            "status": "INVALID_CURRENT_GENERATION",
            "reason": "DECLARED_REPORT_INTEGRITY_INVALID",
            "generated_at": manifest.get("generated_at"),
            "generation_id": manifest.get("generation_id"),
            "generation_revision": manifest.get("generation_revision"),
            "generation_freshness": freshness,
            "checksum_valid": checksum_valid,
            "qualification_allowed": False,
        })
    status = (
        "CURRENT" if freshness.get("current")
        else "STALE_CURRENT_GENERATION"
    )
    return jsonify({
        "schema": "evidence_coverage_dashboard_v1",
        "available": valid,
        "status": status,
        "generated_at": manifest.get("generated_at"),
        "generation_id": manifest.get("generation_id"),
        "generation_revision": manifest.get("generation_revision"),
        "generation_freshness": freshness,
        "checksum_valid": checksum_valid,
        # A current coverage receipt proves only what evidence exists. It does
        # not, by itself, qualify any policy or authorize a live-policy change.
        "qualification_allowed": False,
        "authoritative_source_record_counts": {
            key: source_counts.get(key, "UNKNOWN")
            for key in ("opportunities", "decisions", "order_intents", "executions", "lifecycles", "market_segments")
        },
        "episode_coverage": {
            "exact": totals.get("exact_episodes", "UNKNOWN"),
            "reconstructed": totals.get("reconstructed_episodes", "UNKNOWN"),
            "unknown": totals.get("unknown_episodes", "UNKNOWN"),
            "total": totals.get("episodes", "UNKNOWN"),
        },
        "terminal_outcome_counts": {
            key: int(outcomes.get(key) or 0)
            for key in ("FULL_FILL", "PARTIAL_FILL", "NO_FILL", "UNKNOWN")
        },
        "complete_schedules": totals.get("complete_schedules", "UNKNOWN"),
        "market_paths": totals.get("market_paths", "UNKNOWN"),
        "terminal_outcomes": totals.get("terminal_outcomes", "UNKNOWN"),
        "top_missing_evidence_reasons": [
            {"reason": str(reason), "count": int(count)}
            for reason, count in sorted(
                reasons.items(), key=lambda item: (-int(item[1]), str(item[0]))
            )[:12]
        ],
        "archive_recovery_retention": {
            key: archive.get(key, "UNKNOWN")
            for key in (
                "archive_session_count", "verified_session_count",
                "unverifiable_session_count", "invalid_session_count",
                "retained_file_count", "retained_unique_checksum_count",
            )
        },
        "quarantined_orphan": {
            "episode_id": orphan.get("episode_id"),
            "status": orphan.get("status") or "UNKNOWN",
            "present_in_inputs": orphan.get("present_in_inputs") is True,
            "separate_from_general_triage": orphan.get("separate_from_general_triage") is True,
        },
        "full_artifact": f"/api/report/{EVIDENCE_COVERAGE_TRIAGE_REPORT_FILE}",
    })


@app.route("/api/policy-evidence-library")
def api_policy_evidence_library():
    """Read-only status only; evaluation and arbitrary SQL are never exposed."""
    payload = _read_report(POLICY_EVIDENCE_LIBRARY_MANIFEST_FILE, {})
    if not payload:
        return jsonify({
            "schema": "policy_evidence_library_v1",
            "cache_status": "NOT_PUBLISHED_IN_CURRENT_GENERATION",
            "evaluation_triggered": False,
            "qualification_allowed": False,
        })
    return jsonify(payload)


@app.route("/api/chase-policy-lab")
def api_chase_policy_lab():
    path = _best_report_path("chase_policy_lab_report.json")
    if path is None:
        return jsonify({
            "schema": "chase_policy_lab_v1",
            "qualification_eligible": False,
            "leader_label": "INSUFFICIENT_EVIDENCE",
            "empty_reason": "SOURCE_EMPTY_OR_UNAVAILABLE",
            "ranked_schedules": [],
        })
    return jsonify(_read_json(path))


@app.route("/api/missed-opportunity-proof")
def api_missed_opportunity_proof():
    path = _best_report_path("missed_opportunity_proof_report.json")
    if path is None:
        return jsonify({
            "schema": "missed_opportunity_proof_v1",
            "qualification_eligible": False,
            "empty_reason": "SOURCE_EMPTY_OR_UNAVAILABLE",
            "proof_count": 0,
            "proofs": [],
        })
    return jsonify(_read_json(path))


@app.route("/api/report/<path:filename>")
def api_report(filename):
    safe = os.path.basename(filename)
    if Path(safe).suffix.lower() != ".json":
        abort(404)
    path = _best_report_path(safe)
    if path is None:
        abort(404)
    try:
        with open(path, encoding="utf-8") as f:
            return jsonify(json.load(f))
    except Exception:
        abort(500)


@app.route("/api/archives")
def api_archives():
    return jsonify(_archives_index())


@app.route("/api/past-analysis")
def api_past_analysis():
    return jsonify(_past_analysis_index())


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


@app.route("/download/research-pack")
def download_research_pack():
    """Merge the 6 core research artifacts into one self-contained downloadable HTML file.
    Files: research_highlights.txt, research_findings.txt, research_coverage.txt,
    research_deep_dive_index.txt, analysis_dashboard.html, analyzer_run.log.
    No secrets are included — these are analyzer-generated observation artifacts only."""
    import html as _html
    import datetime as _dt
    stamp = _dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    parts = []
    parts.append("<!DOCTYPE html><html><head><meta charset='utf-8'>")
    parts.append(f"<title>Doxxed Research Pack {stamp}</title>")
    parts.append(
        "<style>body{font-family:system-ui,'Segoe UI',Arial;background:#0e1116;color:#d4d7dd;margin:0;padding:24px}"
        "h1{color:#9ad8ea}h2{color:#7bc6e6;border-bottom:1px solid #333;padding-bottom:6px;margin-top:36px}"
        "pre{background:#06080c;border:1px solid #222;border-radius:8px;padding:14px;overflow:auto;"
        "white-space:pre-wrap;word-break:break-word;font-size:12px;line-height:1.5}"
        "iframe{width:100%;height:900px;border:1px solid #222;border-radius:8px;background:#fff}"
        ".note{color:#889}</style></head><body>"
    )
    parts.append("<h1>Doxxed Crypto - Research Pack (all-in-one)</h1>")
    parts.append(
        f"<p class='note'>Merged {stamp} UTC. Single-file bundle of the 6 core research artifacts. "
        "No secrets included - analyzer-generated observation data only.</p>"
    )

    for fn in (HIGHLIGHTS_FILE, FINDINGS_FILE, COVERAGE_FILE, DEEP_DIVE_INDEX_FILE):
        p = ROOT / fn
        parts.append(f"<h2>{fn}</h2>")
        if p.is_file():
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                txt = f"(failed to read {fn}: {exc})"
            parts.append(f"<pre>{_html.escape(txt)}</pre>")
        else:
            parts.append(f"<p class='note'>(missing: {fn})</p>")

    dash = ROOT / ANALYSIS_DASHBOARD_HTML
    parts.append(f"<h2>{ANALYSIS_DASHBOARD_HTML}</h2>")
    if dash.is_file():
        try:
            dash_html = dash.read_text(encoding="utf-8", errors="replace")
            parts.append(f"<iframe srcdoc=\"{_html.escape(dash_html, quote=True)}\"></iframe>")
        except Exception as exc:
            parts.append(f"<p class='note'>(failed to embed dashboard: {exc})</p>")
    else:
        parts.append(f"<p class='note'>(missing: {ANALYSIS_DASHBOARD_HTML})</p>")

    logp = ROOT / ANALYZER_LOG_FILE
    parts.append(f"<h2>{ANALYZER_LOG_FILE}</h2>")
    if logp.is_file():
        try:
            raw = logp.read_text(encoding="utf-8", errors="replace")
            lines = raw.splitlines()
            if len(lines) > 4000:
                shown = "\n".join(lines[-4000:])
                parts.append(f"<p class='note'>(showing last 4000 of {len(lines)} lines)</p>")
            else:
                shown = raw
            parts.append(f"<pre>{_html.escape(shown)}</pre>")
        except Exception as exc:
            parts.append(f"<p class='note'>(failed to read log: {exc})</p>")
    else:
        parts.append(f"<p class='note'>(missing: {ANALYZER_LOG_FILE})</p>")

    parts.append("</body></html>")
    buf = io.BytesIO("".join(parts).encode("utf-8"))
    return send_file(
        buf,
        mimetype="text/html",
        as_attachment=True,
        download_name=f"doxxed_research_pack_{stamp}.html",
    )


@app.route("/download/reports")
def download_reports():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, path in _bundle_members():
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


def _snapshot_generation(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return (
        int(getattr(stat, "st_dev", 0) or 0),
        int(getattr(stat, "st_ino", 0) or 0),
        int(stat.st_size),
        int(stat.st_mtime_ns),
    )


def _read_generation_fenced(path: Path, attempts: int = 3) -> tuple[bytes, dict]:
    """Capture one stable file generation without stopping its producer."""
    append_prefix = (
        path.name in _APPEND_PREFIX_SNAPSHOT_NAMES
        or path.suffix.lower() == ".jsonl"
    )
    for _attempt in range(attempts):
        before = _snapshot_generation(path)
        with path.open("rb") as handle:
            payload = handle.read(before[2])
        after = _snapshot_generation(path)
        same_identity = before[:2] == after[:2]
        stable = (
            same_identity
            and len(payload) == before[2]
            and (
                (append_prefix and after[2] >= before[2])
                or (not append_prefix and after == before)
            )
        )
        if not stable:
            continue
        if append_prefix and payload and path.suffix.lower() in {".csv", ".jsonl"}:
            boundary = payload.rfind(b"\n")
            payload = payload[: boundary + 1] if boundary >= 0 else b""
        return payload, {
            "capture_mode": (
                "append_prefix_generation_fence_v1"
                if append_prefix
                else "strict_generation_fence_v1"
            ),
            "source_size": before[2],
            "source_mtime_ns": before[3],
            "captured_bytes": len(payload),
        }
    raise RuntimeError(f"could not capture stable generation: {path}")


def _sqlite_online_snapshot(path: Path) -> tuple[bytes, dict]:
    """Use SQLite's online backup API so a live WAL database is consistent."""
    fd, temp_name = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    snapshot = Path(temp_name)
    try:
        source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        destination = sqlite3.connect(str(snapshot))
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        payload = snapshot.read_bytes()
        check = sqlite3.connect(f"file:{snapshot.as_posix()}?mode=ro", uri=True)
        try:
            verdict = check.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            check.close()
        if str(verdict).lower() != "ok":
            raise RuntimeError(f"SQLite snapshot integrity check failed: {path}")
        return payload, {
            "capture_mode": "sqlite_online_backup_v1",
            "source_size": int(path.stat().st_size),
            "captured_bytes": len(payload),
            "integrity_check": "ok",
        }
    finally:
        snapshot.unlink(missing_ok=True)


def _capture_bundle_member(path: Path) -> tuple[bytes, dict]:
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return _sqlite_online_snapshot(path)
    return _read_generation_fenced(path)


_BUNDLE_COPY_CHUNK_BYTES = 1024 * 1024


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_BUNDLE_COPY_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _truncate_to_last_newline(path: Path) -> int:
    """Trim a staged append-only CSV/JSONL snapshot to a complete row."""
    size = path.stat().st_size
    if size <= 0:
        return 0
    with path.open("r+b") as handle:
        cursor = size
        while cursor > 0:
            width = min(_BUNDLE_COPY_CHUNK_BYTES, cursor)
            cursor -= width
            handle.seek(cursor)
            block = handle.read(width)
            boundary = block.rfind(b"\n")
            if boundary >= 0:
                captured = cursor + boundary + 1
                handle.truncate(captured)
                return captured
        handle.truncate(0)
    return 0


def _stage_generation_fenced(path: Path, destination: Path, attempts: int = 3) -> dict:
    """Capture a stable member on disk using bounded memory."""
    append_prefix = (
        path.name in _APPEND_PREFIX_SNAPSHOT_NAMES
        or path.suffix.lower() == ".jsonl"
    )
    last_error = None
    for _attempt in range(attempts):
        destination.unlink(missing_ok=True)
        try:
            before = _snapshot_generation(path)
            remaining = before[2]
            with path.open("rb") as source, destination.open("xb") as staged:
                while remaining:
                    chunk = source.read(min(_BUNDLE_COPY_CHUNK_BYTES, remaining))
                    if not chunk:
                        break
                    staged.write(chunk)
                    remaining -= len(chunk)
            after = _snapshot_generation(path)
            captured = destination.stat().st_size
            stable = (
                before[:2] == after[:2]
                and remaining == 0
                and captured == before[2]
                and (
                    (append_prefix and after[2] >= before[2])
                    or (not append_prefix and after == before)
                )
            )
            if not stable:
                last_error = "source generation changed during capture"
                continue
            if append_prefix and path.suffix.lower() in {".csv", ".jsonl"}:
                captured = _truncate_to_last_newline(destination)
            return {
                "capture_mode": (
                    "append_prefix_generation_fence_v1"
                    if append_prefix
                    else "strict_generation_fence_v1"
                ),
                "staging_mode": "bounded_memory_disk_v1",
                "source_size": before[2],
                "source_mtime_ns": before[3],
                "captured_bytes": captured,
                "sha256": _sha256_file(destination),
            }
        except OSError as exc:
            last_error = str(exc)
    destination.unlink(missing_ok=True)
    raise RuntimeError(
        f"could not capture stable generation: {path}"
        + (f" ({last_error})" if last_error else "")
    )


def _stage_sqlite_snapshot(path: Path, destination: Path) -> dict:
    destination.unlink(missing_ok=True)
    source = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    snapshot = sqlite3.connect(str(destination))
    try:
        source.backup(snapshot)
    finally:
        snapshot.close()
        source.close()
    check = sqlite3.connect(f"file:{destination.as_posix()}?mode=ro", uri=True)
    try:
        verdict = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()
    if str(verdict).lower() != "ok":
        destination.unlink(missing_ok=True)
        raise RuntimeError(f"SQLite snapshot integrity check failed: {path}")
    captured = destination.stat().st_size
    return {
        "capture_mode": "sqlite_online_backup_v1",
        "staging_mode": "bounded_memory_disk_v1",
        "source_size": int(path.stat().st_size),
        "captured_bytes": captured,
        "integrity_check": "ok",
        "sha256": _sha256_file(destination),
    }


def _stage_bundle_member(path: Path, destination: Path) -> dict:
    if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return _stage_sqlite_snapshot(path, destination)
    return _stage_generation_fenced(path, destination)


def _count_valid_compressed_shadow_rows_path(path: Path) -> int:
    count = 0
    with path.open("r", encoding="utf-8-sig", errors="strict") as handle:
        for raw_line in handle:
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict) or row.get("schema") != "compressed_chase_shadow_v1":
                continue
            if (
                row.get("execution_class") != "SHADOW_ONLY"
                or row.get("places_order") is not False
                or row.get("relay_eligible") is not False
                or row.get("event") not in {"STAGE", "EXPIRED"}
            ):
                continue
            if all(
                str(row.get(name) or "").strip()
                for name in (
                    "trade_id", "shared_ai_call_id", "opportunity_id",
                    "episode_id", "epoch_id", "policy_id", "policy_signature",
                )
            ):
                count += 1
    return count


def _count_valid_compressed_shadow_rows(payload: bytes) -> int:
    """Count signed shadow schedule rows, never legacy touch-grid rows."""
    count = 0
    for raw_line in payload.decode("utf-8-sig", errors="strict").splitlines():
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(row, dict):
            continue
        if row.get("schema") != "compressed_chase_shadow_v1":
            continue
        if (
            row.get("execution_class") != "SHADOW_ONLY"
            or row.get("places_order") is not False
            or row.get("relay_eligible") is not False
            or row.get("event") not in {"STAGE", "EXPIRED"}
        ):
            continue
        if not all(
            str(row.get(name) or "").strip()
            for name in (
                "trade_id",
                "shared_ai_call_id",
                "opportunity_id",
                "episode_id",
                "epoch_id",
                "policy_id",
                "policy_signature",
            )
        ):
            continue
        count += 1
    return count


@app.route("/download/everything")
def download_everything():
    """One verified ZIP containing raw research data, reports, sessions, genome,
    accumulator exports, audit source bundle, and any preserved analysis."""
    freshness = _generation_freshness_meta()
    generation_current = freshness.get("current") is True
    agent_root = _agent_source_root()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    candidates = []

    def is_transient_sync_member(path: Path) -> bool:
        """Exclude atomic mirror-sync working files from evidence bundles."""
        name = path.name.lower()
        return (
            ".download" in name
            or ".replace-backup" in name
            or name.endswith(".tmp")
            or ".tmp-" in name
        )

    def add_tree(base: Path, prefix: str, patterns=("*",)):
        if not base.is_dir():
            return
        seen = set()
        for pattern in patterns:
            for path in sorted(base.rglob(pattern)):
                if (
                    not path.is_file()
                    or path in seen
                    or is_transient_sync_member(path)
                ):
                    continue
                rel = path.relative_to(base)
                if (
                    "__pycache__" in rel.parts
                    or path.suffix.lower() in (".pyc", ".pyo")
                    or path.name == ".gitkeep"
                ):
                    continue
                seen.add(path)
                candidates.append((path, f"{prefix}/{rel.as_posix()}"))

    # Keep the synchronized Fly data and any longer desktop research history
    # explicitly separated. The configured data root is the current source.
    for pattern in ("*.csv", "*.jsonl", "*.db", "*.log"):
        for path in sorted(DATA_ROOT.glob(pattern)):
            if is_transient_sync_member(path):
                continue
            candidates.append((path, f"raw/current_fly_mirror/{path.name}"))
        if agent_root.resolve() != DATA_ROOT.resolve():
            for path in sorted(agent_root.glob(pattern)):
                candidates.append((path, f"raw/research_history/{path.name}"))
    # Reproducibility receipts are explicitly allowlisted. They are raw source
    # inputs, not analyzer reports, and therefore must travel with the mirror.
    current_receipts = (
        "relay_lifecycle_evidence_v1.json",
        "research_session.json",
        "pathway_lane_specs.json",
        "research_events_v22.index.json",
        "paper_lifecycle_v1.json",
        "lane_lab_pnl_ledger.json",
        ".fly-sync-state.json",
        ".fly-sync-growth-state.json",
        "_size_report.json",
    )
    for name in current_receipts:
        path = DATA_ROOT / name
        if path.is_file():
            candidates.append((path, f"raw/current_fly_mirror/{name}"))
    for pattern in ("paper_lifecycle_emergency_*.json", "config-*.json"):
        for path in sorted(DATA_ROOT.glob(pattern)):
            candidates.append((path, f"raw/current_fly_mirror/{path.name}"))
    for path in sorted(DATA_ROOT.glob("signal_replay.jsonl.*")):
        if path.is_file():
            candidates.append((path, f"raw/current_fly_mirror/{path.name}"))
    # Preserve rotations for every analyzer input, not only signal replay.
    for pattern in ("*.jsonl.*", "*.csv.*", "*.log.*"):
        for path in sorted(DATA_ROOT.glob(pattern)):
            if path.is_file() and not is_transient_sync_member(path):
                candidates.append((path, f"raw/current_fly_mirror/{path.name}"))

    # Pathway receipts are a required, named evidence component. Runtime
    # contracts come from the synchronized Fly mirror. Exit validation is an
    # analyzer-generation report and must resolve through the current report
    # manifest rather than a potentially older mirror copy.
    for name in CURRENT_PATHWAY_RECEIPTS:
        path = (
            _best_report_path(name)
            if name == "exit_reports_validation.json"
            else DATA_ROOT / name
        )
        if path is not None and path.is_file():
            candidates.append((path, f"current_receipts/{name}"))

    # Canonical V3 evidence is recursive by design: ledgers are append-only and
    # market segments are content-addressed below a nested directory tree.
    v3_ledgers = (
        "decision.jsonl",
        "lifecycle.jsonl",
        "market_segment.jsonl",
        "opportunity.jsonl",
        "order_intent.jsonl",
    )
    conditional_v3_ledgers = {
        # The execution ledger is first created by an observed fill/close.
        # Its absence before any execution is a truthful empty cohort, not a
        # transport failure.  Once present it is always exported and hashed.
        "execution.jsonl": "NO_EXECUTION_EVENTS",
    }
    for name in (*v3_ledgers, *conditional_v3_ledgers):
        path = DATA_ROOT / "v3" / "ledgers" / name
        if path.is_file():
            candidates.append((
                path,
                f"raw/current_fly_mirror/v3/ledgers/{name}",
            ))
    add_tree(
        DATA_ROOT / "v3" / "market_segments",
        "raw/current_fly_mirror/v3/market_segments",
        patterns=("*.json", "*.jsonl"),
    )

    # Exactly one scope-aware copy of each current report. Historical/all-data
    # reports remain available under an explicitly named directory.
    for arcname, path in _bundle_members():
        candidates.append((path, f"current_reports/{arcname}"))
    add_tree(ROOT / ALL_DATA_REPORTS_DIR, "all_data_reports")
    add_tree(ROOT / ARCHIVE_DIR, "sessions")
    add_tree(agent_root / "research" / "genome", "genome")
    research_db = agent_root / "research.db"
    if research_db.is_file():
        candidates.append((research_db, "genome/research.db"))
    add_tree(ROOT / "research_accumulator", "accumulator")
    add_tree(ROOT / PAST_ANALYSIS_DIR, "past_analysis")

    # Include the cached full-stack source audit as a named component, without
    # recursively embedding prior all-in-one bundles.
    audit_zip = _ensure_current_gpt_audit_bundle(agent_root)
    candidates.append((audit_zip, "audit/gpt_audit_bundle.zip"))

    unique = {}
    for path, arcname in candidates:
        unique.setdefault(arcname, path)
    member_names = set(unique)
    required_raw_members = {
        "raw/current_fly_mirror/relay_lifecycle_evidence_v1.json",
        "raw/current_fly_mirror/research_session.json",
        "raw/current_fly_mirror/pathway_lane_specs.json",
        "raw/current_fly_mirror/research_events_v22.index.json",
        "raw/current_fly_mirror/paper_lifecycle_v1.json",
        "raw/current_fly_mirror/.fly-sync-state.json",
        *{
            f"raw/current_fly_mirror/v3/ledgers/{name}"
            for name in v3_ledgers
        },
        *{
            f"raw/current_fly_mirror/{name}"
            for name in REQUIRED_ANALYZER_RAW_INPUTS
        },
    }
    missing_required_raw = sorted(required_raw_members - member_names)
    if missing_required_raw:
        abort(
            500,
            description=(
                "complete research download refused: required current mirror "
                "artifacts are missing: " + ", ".join(missing_required_raw)
            ),
        )
    conditional_raw_status = {
        name: {
            "present": f"raw/current_fly_mirror/{name}" in member_names,
            "absence_status": (
                None
                if f"raw/current_fly_mirror/{name}" in member_names
                else absence_status
            ),
        }
        for name, absence_status in CONDITIONAL_ANALYZER_RAW_INPUTS.items()
    }
    conditional_v3_status = {
        name: {
            "present": (
                f"raw/current_fly_mirror/v3/ledgers/{name}" in member_names
            ),
            "absence_status": (
                None
                if f"raw/current_fly_mirror/v3/ledgers/{name}" in member_names
                else absence_status
            ),
        }
        for name, absence_status in conditional_v3_ledgers.items()
    }
    accumulator_db_members = sorted(
        name for name in member_names
        if name.startswith("accumulator/")
        and Path(name).suffix.lower() in {".db", ".sqlite", ".sqlite3"}
    )
    if not accumulator_db_members and generation_current:
        abort(
            500,
            description=(
                "complete research download refused: required accumulator "
                "SQLite database is missing"
            ),
        )
    required_current_receipts = {
        f"current_receipts/{name}" for name in CURRENT_PATHWAY_RECEIPTS
    }
    missing_current_receipts = sorted(required_current_receipts - member_names)
    if missing_current_receipts and generation_current:
        abort(
            500,
            description=(
                "complete research download refused: required current pathway "
                "receipts are missing: " + ", ".join(missing_current_receipts)
            ),
        )
    current_report_manifest = _read_json(REPORT_MANIFEST_FILE) or {}
    current_safe_genome = _read_json(SAFE_POLICY_GENOME_V3_REPORT_FILE) or {}
    current_epoch_id = current_safe_genome.get("epoch_id") or (
        current_safe_genome.get("epoch_scope") or {}
    ).get("selected_epoch_id")
    current_policy_signatures = sorted({
        str(identity.get("policy_signature") or "").strip()
        for identity in (
            (current_safe_genome.get("collection") or {}).get(
                "effective_paper_execution_identities"
            )
            or []
        )
        if isinstance(identity, dict)
        and str(identity.get("policy_signature") or "").strip()
    })
    legacy_single_policy_signature = current_safe_genome.get("policy_signature") or (
        current_safe_genome.get("epoch_scope") or {}
    ).get("policy_signature")
    if legacy_single_policy_signature and not current_policy_signatures:
        current_policy_signatures = [str(legacy_single_policy_signature)]
    current_generation_revision = (
        current_report_manifest.get("generation_revision")
        or current_safe_genome.get("generation_revision")
        or os.getenv("SOURCE_GIT_REV")
        or "UNKNOWN"
    )
    declared_report_files = sorted({
        str(entry.get("file") if isinstance(entry, dict) else entry).strip()
        for entry in (current_report_manifest.get("reports") or [])
        if str(entry.get("file") if isinstance(entry, dict) else entry).strip()
    })
    required_current_reports = {
        f"current_reports/{name}" for name in BUNDLE_FILES
    } | {
        f"current_reports/{REPORTS_DIR}/{name}" for name in declared_report_files
        if name not in BUNDLE_FILES
    }
    missing_current_reports = sorted(required_current_reports - member_names)
    if missing_current_reports and generation_current:
        abort(
            500,
            description=(
                "complete research download refused: required current analyzer "
                "reports are missing: " + ", ".join(missing_current_reports)
            ),
        )

    sync_identity = _read_json(DATA_ROOT / ".fly-sync-state.json") or {}
    session_identity = _read_json(DATA_ROOT / "research_session.json") or {}
    identity_sources = {
        "report_manifest": {
            "revision": current_generation_revision,
            "source_data_revision": current_report_manifest.get("source_data_revision"),
            "epoch_id": current_report_manifest.get("epoch_id"),
        },
        "safe_policy_genome": {
            "revision": current_safe_genome.get("generation_revision"),
            "epoch_id": current_epoch_id,
        },
        "mirror_sync": {
            "revision": sync_identity.get("source_git_rev") or sync_identity.get("revision"),
            "epoch_id": sync_identity.get("epoch_id"),
        },
        "research_session": {
            "revision": session_identity.get("source_git_rev") or session_identity.get("revision"),
            "epoch_id": (
                session_identity.get("epoch_id")
                or session_identity.get("collection_epoch")
                or session_identity.get("collector_v22_epoch_id")
            ),
        },
    }
    provenance_conflicts = []
    for field in ("revision", "epoch_id"):
        values = {
            str(identity.get(field)).strip()
            for identity in identity_sources.values()
            if identity.get(field) not in (None, "", "UNKNOWN", "UNAVAILABLE")
        }
        if len(values) > 1:
            provenance_conflicts.append({"field": field, "values": sorted(values)})
            if generation_current:
                abort(
                    500,
                    description=(
                        "complete research download refused: incoherent "
                        f"{field} provenance across sources: {sorted(values)}"
                    ),
                )
    optional_presence = {
        name: f"raw/current_fly_mirror/{name}" in member_names
        for name in OPTIONAL_ANALYZER_RAW_INPUTS
    }
    capture_started_at = datetime.now(timezone.utc).isoformat()
    coherence_anchor_paths = {
        "report_manifest": ROOT / REPORT_MANIFEST_FILE,
        "safe_policy_genome": ROOT / SAFE_POLICY_GENOME_V3_REPORT_FILE,
        "mirror_sync": DATA_ROOT / ".fly-sync-state.json",
        "research_session": DATA_ROOT / "research_session.json",
    }
    coherence_before = {
        name: _snapshot_generation(path)
        for name, path in coherence_anchor_paths.items()
        if path.is_file()
    }
    manifest = {
        "schema": "doxxed_everything_bundle_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generation_revision": current_generation_revision,
        "source_data_revision": current_report_manifest.get("source_data_revision"),
        "epoch_id": current_epoch_id,
        # A V3.1 epoch can intentionally contain multiple independently signed
        # tile policies.  Export the complete sorted set rather than implying a
        # single identity or emitting a misleading null value.
        "policy_signatures": current_policy_signatures,
        "generation_current": generation_current,
        "generation_freshness": freshness,
        "files": [],
        "capture_contract": {
            "schema": "generation_fenced_bundle_capture_v2",
            "archive_build_mode": "atomic_on_disk_bounded_memory_v1",
            "copy_chunk_bytes": _BUNDLE_COPY_CHUNK_BYTES,
            "sqlite": "sqlite_online_backup_v1",
            "hot_files": "append_prefix_or_strict_generation_fence_v1",
            "required_analyzer_raw_inputs": list(REQUIRED_ANALYZER_RAW_INPUTS),
            "conditional_analyzer_raw_inputs": CONDITIONAL_ANALYZER_RAW_INPUTS,
            "conditional_v3_ledgers": conditional_v3_ledgers,
            "optional_analyzer_raw_inputs": list(OPTIONAL_ANALYZER_RAW_INPUTS),
            "capture_started_at": capture_started_at,
            "coherence_anchors": sorted(coherence_before),
        },
        "provenance": identity_sources,
        "provenance_coherent": not provenance_conflicts,
        "provenance_conflicts": provenance_conflicts,
        "notes": {
            "past_analysis_available": (ROOT / PAST_ANALYSIS_DIR).is_dir()
            and any((ROOT / PAST_ANALYSIS_DIR).iterdir()),
            "configured_data_root_included": DATA_ROOT.is_dir(),
            "current_report_scope": (
                "FRESH-COLLECTION" if generation_current
                else "STALE_ANALYZER_SNAPSHOT_WITH_CURRENT_RAW_EVIDENCE"
            ),
            "live_trading_data": False,
            "purpose": "research audit and offline analysis",
            "source_revision": current_generation_revision,
            "required_raw_members": sorted(required_raw_members),
            "missing_required_raw_members": missing_required_raw,
            "required_current_receipts": sorted(required_current_receipts),
            "missing_current_receipts": missing_current_receipts,
            "required_current_reports": sorted(required_current_reports),
            "missing_current_reports": missing_current_reports,
            "accumulator_db_members": accumulator_db_members,
            "optional_analyzer_raw_presence": optional_presence,
            "conditional_analyzer_raw_status": conditional_raw_status,
            "conditional_v3_ledger_status": conditional_v3_status,
            "missing_optional_analyzer_raw_inputs": sorted(
                name for name, present in optional_presence.items() if not present
            ),
            "component_coverage": {
                "relay_lifecycle_evidence_v1": any(
                    name.endswith("/relay_lifecycle_evidence_v1.json")
                    for name in member_names
                ),
                "counterfactual_evidence": any(
                    "counterfactual" in name.lower() for name in member_names
                ),
                "cohort_reports": any("cohort" in name.lower() for name in member_names),
                "genome_and_dna": any(name.startswith("genome/") for name in member_names),
                "report_manifest": any(
                    name.endswith("/report_manifest.json") for name in member_names
                ),
                "canonical_v3_ledgers": all(
                    f"raw/current_fly_mirror/v3/ledgers/{name}" in member_names
                    for name in v3_ledgers
                ),
                "canonical_v3_execution_ledger": conditional_v3_status[
                    "execution.jsonl"
                ],
                "canonical_v3_market_segments": any(
                    name.startswith("raw/current_fly_mirror/v3/market_segments/")
                    for name in member_names
                ),
                "signed_compressed_shadow_schedule": {
                    "present": "raw/current_fly_mirror/chase_offset_touch_grid.jsonl" in member_names,
                    "absence_status": (
                        None if "raw/current_fly_mirror/chase_offset_touch_grid.jsonl" in member_names
                        else "NO_COMPRESSED_SHADOW_SCHEDULE_EVENTS"
                    ),
                },
                "missed_opportunity_proof_report": any(
                    name.endswith("/missed_opportunity_proof_report.json") for name in member_names
                ),
                "chase_policy_lab_report": any(
                    name.endswith("/chase_policy_lab_report.json") for name in member_names
                ),
                "replay_rotations": any(
                    name.startswith("raw/current_fly_mirror/signal_replay.jsonl.")
                    for name in member_names
                ),
                "session_spec_index_receipts": all(
                    f"raw/current_fly_mirror/{name}" in member_names
                    for name in (
                        "research_session.json",
                        "pathway_lane_specs.json",
                        "research_events_v22.index.json",
                    )
                ),
                "paper_lifecycle_receipt": (
                    "raw/current_fly_mirror/paper_lifecycle_v1.json" in member_names
                ),
                "mirror_sync_receipt": (
                    "raw/current_fly_mirror/.fly-sync-state.json" in member_names
                ),
                "gpt_audit_source_bundle": (
                    "audit/gpt_audit_bundle.zip" in member_names
                ),
                "current_pathway_receipts": {
                    name: f"current_receipts/{name}" in member_names
                    for name in CURRENT_PATHWAY_RECEIPTS
                },
                "current_pathway_receipts_complete": all(
                    f"current_receipts/{name}" in member_names
                    for name in CURRENT_PATHWAY_RECEIPTS
                ),
            },
        },
    }
    compressed_shadow_row_count = 0
    staging_root = Path(tempfile.mkdtemp(prefix="doxxed-evidence-bundle-"))
    building_path = staging_root / "complete-research-evidence.zip.building"
    final_path = staging_root / "complete-research-evidence.zip"
    try:
        with zipfile.ZipFile(
            building_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True
        ) as zf:
            for index, (arcname, path) in enumerate(sorted(unique.items())):
                staged = staging_root / f"member-{index:08d}.capture"
                try:
                    capture = _stage_bundle_member(path, staged)
                    manifest["files"].append({
                        "path": arcname,
                        "bytes": capture["captured_bytes"],
                        **capture,
                    })
                    zf.write(staged, arcname)
                    if arcname == "raw/current_fly_mirror/chase_offset_touch_grid.jsonl":
                        compressed_shadow_row_count = (
                            _count_valid_compressed_shadow_rows_path(staged)
                        )
                except (OSError, RuntimeError, sqlite3.Error) as exc:
                    abort(
                        500,
                        description=(
                            "complete research download refused: unable to capture "
                            f"stable source {arcname}: {exc}"
                        ),
                    )
                finally:
                    staged.unlink(missing_ok=True)
            compressed_present = compressed_shadow_row_count > 0
            compressed_status = manifest["notes"]["conditional_analyzer_raw_status"][
                "chase_offset_touch_grid.jsonl"
            ]
            compressed_status["present"] = compressed_present
            compressed_status["absence_status"] = (
                None if compressed_present else "NO_COMPRESSED_SHADOW_SCHEDULE_EVENTS"
            )
            compressed_status["valid_compressed_shadow_rows"] = compressed_shadow_row_count
            compressed_coverage = manifest["notes"]["component_coverage"][
                "signed_compressed_shadow_schedule"
            ]
            compressed_coverage["present"] = compressed_present
            compressed_coverage["absence_status"] = compressed_status["absence_status"]
            compressed_coverage["valid_compressed_shadow_rows"] = compressed_shadow_row_count
            coherence_after = {
                name: _snapshot_generation(path)
                for name, path in coherence_anchor_paths.items()
                if path.is_file()
            }
            if coherence_after != coherence_before:
                abort(
                    500,
                    description=(
                        "complete research download refused: source/report identity "
                        "anchors changed during capture"
                    ),
                )
            manifest["capture_contract"]["capture_completed_at"] = (
                datetime.now(timezone.utc).isoformat()
            )
            manifest["capture_contract"]["coherence_verified"] = True
            readme = (
                "Doxxed Crypto all-in-one research bundle.\n"
                "MANIFEST.json lists every included payload file except itself, "
                "with size and SHA-256 checksum.\n"
                "raw/current_fly_mirror is the configured current data source; "
                "raw/research_history is retained separately.\n"
                "Analyzer reports may be stale; generation_current and "
                "generation_freshness record their exact status.\n"
                "Past Analysis is included only after a preserved analysis exists.\n"
            ).encode("utf-8")
            manifest["files"].append({
                "path": "README.txt",
                "bytes": len(readme),
                "sha256": hashlib.sha256(readme).hexdigest(),
                "capture_mode": "generated_bundle_member_v1",
                "captured_bytes": len(readme),
            })
            zf.writestr("MANIFEST.json", json.dumps(manifest, indent=2))
            zf.writestr("README.txt", readme)
        with zipfile.ZipFile(building_path, "r") as verified:
            corrupt_member = verified.testzip()
            if corrupt_member is not None:
                raise RuntimeError(f"corrupt archive member: {corrupt_member}")
        os.replace(building_path, final_path)
        response = send_file(
            final_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"complete_research_evidence_bundle_{stamp}.zip",
        )

        def _cleanup_complete_bundle() -> None:
            final_path.unlink(missing_ok=True)
            try:
                staging_root.rmdir()
            except OSError:
                pass

        response.call_on_close(_cleanup_complete_bundle)
        return response
    except Exception:
        building_path.unlink(missing_ok=True)
        final_path.unlink(missing_ok=True)
        for staged in staging_root.glob("member-*.capture"):
            staged.unlink(missing_ok=True)
        try:
            staging_root.rmdir()
        except OSError:
            pass
        raise


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


@app.route("/download/past-analysis")
@app.route("/download/past-analysis/<archive_id>")
def download_past_analysis(archive_id=None):
    """Download preserved derived analysis without bulky raw ledgers."""
    try:
        from research.past_analysis import latest_past_analysis
    except ImportError:
        abort(503, description="Past Analysis support is unavailable")
    if archive_id:
        safe = os.path.basename(archive_id)
        archive = ROOT / PAST_ANALYSIS_DIR / safe
    else:
        archive = latest_past_analysis(ROOT)
        safe = archive.name if archive else ""
    if not archive or not archive.is_dir() or not (archive / "past_analysis_manifest.json").is_file():
        abort(404, description="No preserved Past Analysis is available yet")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(archive.rglob("*")):
            if path.is_file():
                zf.write(path, arcname=str(path.relative_to(archive)))
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"past_analysis_{safe}.zip",
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_json(path: Path, member: str) -> dict | None:
    try:
        with zipfile.ZipFile(path) as zf:
            if zf.testzip() is not None or member not in zf.namelist():
                return None
            return json.loads(zf.read(member))
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError):
        return None


def _source_record_matches(record: dict | None, path: Path) -> bool:
    if not record or not path.is_file():
        return False
    try:
        return (
            int(record.get("bytes") or -1) == path.stat().st_size
            and str(record.get("sha256") or "") == _sha256_file(path)
        )
    except (OSError, TypeError, ValueError):
        return False


def _current_archive_ids() -> list[str]:
    index = _archives_index()
    return sorted(
        str(item.get("id") or item.get("session_id"))
        for item in (index.get("sessions") or [])
        if item.get("id") or item.get("session_id")
    )


def _complete_bundle_candidates(agent_root: Path):
    bases = (
        agent_root / "research" / "downloads",
        agent_root / "downloads",
        Path(HISTORY_ROOT) / "downloads",
        ROOT / "downloads",
        ROOT,
    )
    seen = set()
    for base in bases:
        for name in (COMPLETE_BUNDLE_NAME,) + COMPLETE_BUNDLE_FALLBACKS:
            candidate = Path(base) / name
            if candidate in seen:
                continue
            seen.add(candidate)
            if candidate.is_file():
                yield candidate


def _complete_bundle_is_current(candidate: Path) -> bool:
    meta = _zip_json(candidate, "BUNDLE_MANIFEST.json")
    if not meta or meta.get("schema") != "trading_sessions_complete_manifest_v2":
        return False
    report_manifest_path = ROOT / REPORT_MANIFEST_FILE
    if not _source_record_matches(meta.get("report_manifest"), report_manifest_path):
        return False
    if sorted(meta.get("session_ids") or []) != _current_archive_ids():
        return False
    for record in meta.get("raw_sources") or []:
        name = os.path.basename(str(record.get("name") or ""))
        if not name:
            return False
        if record.get("role") == "configured_data_root":
            source = DATA_ROOT / name
        else:
            source = next(
                (
                    path
                    for path in (DATA_ROOT / name, ROOT / name, Path(HISTORY_ROOT) / name)
                    if path.is_file()
                ),
                DATA_ROOT / name,
            )
        if not _source_record_matches(record, source):
            return False
    return True


def _build_complete_bundle(agent_root: Path) -> Path:
    try:
        import importlib.util

        builder_path = agent_root / "build_complete_session_bundle.py"
        spec = importlib.util.spec_from_file_location(
            "_canonical_complete_session_bundle", builder_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {builder_path}")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        build_bundle = builder.build_bundle
    except (ImportError, OSError) as exc:
        abort(503, description=f"build_complete_session_bundle.py not found: {exc}")
    out_dir = agent_root / "research" / "downloads"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / COMPLETE_BUNDLE_NAME
    try:
        built = build_bundle(
            Path(HISTORY_ROOT),
            ROOT,
            out_path,
            data_root=DATA_ROOT,
        )
    except Exception as exc:
        abort(500, description=f"Bundle build failed: {exc}")
    if not built.is_file() or not _complete_bundle_is_current(built):
        abort(500, description="Complete bundle failed freshness verification")
    return built


def _chatgpt_bundle_is_current(candidate: Path) -> bool:
    meta = _zip_json(candidate, "BUNDLE_MANIFEST.json")
    if not meta:
        return False
    contract = meta.get("source_contract") or {}
    if contract.get("schema") != "split_report_and_data_roots_v1":
        return False
    report_path = _best_report_path(REPORT_MANIFEST_FILE)
    if report_path is None or not _source_record_matches(
        contract.get("report_manifest"), report_path
    ):
        return False
    for record in contract.get("raw_sources") or []:
        name = os.path.basename(str(record.get("name") or ""))
        source = (
            DATA_ROOT / name
            if (DATA_ROOT / name).is_file()
            else ROOT / name
        )
        if not _source_record_matches(record, source):
            return False
    return True


def _gpt_audit_bundle_is_current(
    candidate: Path,
    manifest_path: Path,
    agent_root: Path,
) -> bool:
    try:
        meta = json.loads(manifest_path.read_text(encoding="utf-8"))
        with zipfile.ZipFile(candidate) as zf:
            if zf.testzip() is not None:
                return False
            source_records = [
                record
                for record in (meta.get("file_index") or [])
                if str(record.get("path") or "").startswith("source/")
            ]
            source_members = set(zf.namelist())
            required_members = set(meta.get("required_members") or [])
            promised_members = set(meta.get("start_here") or [])
            mandatory_source_members = {
                "source/bot.py",
                "source/analyzer_research_engine_v62.py",
                "source/research/research_dashboard.py",
            }
            if (
                not source_records
                or not mandatory_source_members.issubset(source_members)
                or not required_members.issubset(source_members)
                or not promised_members.issubset(source_members)
            ):
                return False
            for record in source_records:
                rel = str(record.get("path") or "")
                if rel not in source_members:
                    return False
                bundled = zf.read(rel)
                if (
                    len(bundled) != int(record.get("bytes") or -1)
                    or hashlib.sha256(bundled).hexdigest()[:16]
                    != str(record.get("sha256_prefix") or "")
                ):
                    return False
    except (OSError, zipfile.BadZipFile, json.JSONDecodeError):
        return False
    for record in source_records:
        rel = str(record.get("path") or "")
        source = agent_root / rel[len("source/") :]
        if (
            not source.is_file()
            or source.stat().st_size != int(record.get("bytes") or -1)
            or _sha256_file(source)[:16] != str(record.get("sha256_prefix") or "")
        ):
            return False
    report = agent_root / "research" / "genome" / "genome_analysis_report.json"
    if report.is_file():
        try:
            report_generated = json.loads(
                report.read_text(encoding="utf-8")
            ).get("generated_at")
            if (
                report_generated
                and meta.get("generated_at")
                and report_generated > meta["generated_at"]
            ):
                return False
        except (OSError, json.JSONDecodeError):
            return False
    return True


def _ensure_current_gpt_audit_bundle(agent_root: Path) -> Path:
    if str(agent_root) not in sys.path:
        sys.path.insert(0, str(agent_root))
    try:
        from build_gpt_audit_bundle import build, OUT_DIR, ZIP_NAME, MANIFEST_NAME
    except ImportError as exc:
        abort(503, description=f"build_gpt_audit_bundle.py not found: {exc}")
    out_dir = (
        agent_root / "research" / "downloads"
        if (agent_root / "research" / "downloads").is_dir()
        else Path(OUT_DIR)
    )
    candidate = out_dir / ZIP_NAME
    manifest_path = out_dir / MANIFEST_NAME
    if (
        candidate.is_file()
        and candidate.stat().st_size > 50_000
        and manifest_path.is_file()
        and _gpt_audit_bundle_is_current(candidate, manifest_path, agent_root)
    ):
        return candidate
    try:
        out_zip, _ = build(agent_root=agent_root)
    except Exception as exc:
        abort(500, description=f"GPT audit bundle build failed: {exc}")
    out_zip = Path(out_zip)
    if not _gpt_audit_bundle_is_current(out_zip, manifest_path, agent_root):
        abort(500, description="GPT audit bundle failed source freshness verification")
    return out_zip


@app.route("/download/all-sessions")
def download_all_sessions():
    """One ZIP: every session archive + live reports + CSVs. Uses cache unless ?rebuild=1."""
    force = request.args.get("rebuild") in ("1", "true", "yes")
    agent_root = _agent_source_root()
    out_path = None
    if not force:
        out_path = next(
            (
                candidate
                for candidate in _complete_bundle_candidates(agent_root)
                if _complete_bundle_is_current(candidate)
            ),
            None,
        )
    if out_path is None:
        out_path = _build_complete_bundle(agent_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return send_file(
        out_path,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"trading_sessions_complete_{stamp}.zip",
    )


@app.route("/download/complete")
def download_complete_cached():
    """Serve a source-current complete bundle, rebuilding stale caches."""
    agent_root = _agent_source_root()
    candidate = next(
        (
            path
            for path in _complete_bundle_candidates(agent_root)
            if _complete_bundle_is_current(path)
        ),
        None,
    )
    if candidate is None:
        candidate = _build_complete_bundle(agent_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return send_file(
        candidate,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"trading_sessions_complete_{stamp}.zip",
    )


@app.route("/download/chatgpt")
def download_chatgpt_bundle():
    """ChatGPT-safe bundle: CSV + key reports + manifest (atomic ZIP, verified)."""
    agent_root = _agent_source_root()
    try:
        # Load the canonical agent-root builder explicitly.  A retired copy in
        # research/ has an incompatible build() signature and previously made
        # this dashboard button return HTTP 500.
        import importlib.util
        builder_path = agent_root / "build_chatgpt_research_bundle.py"
        spec = importlib.util.spec_from_file_location(
            "_canonical_chatgpt_research_bundle", builder_path
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {builder_path}")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        build, OUT_DIR, ZIP_NAME = builder.build, builder.OUT_DIR, builder.ZIP_NAME
    except (ImportError, OSError) as exc:
        abort(503, description=f"build_chatgpt_research_bundle.py not found: {exc}")
    for base in (RESEARCH_ROOT if (RESEARCH_ROOT := agent_root / "research").is_dir() else ROOT, ROOT, agent_root):
        candidate = base / "downloads" / ZIP_NAME
        if (
            candidate.is_file()
            and candidate.stat().st_size > 10_000
            and _chatgpt_bundle_is_current(candidate)
        ):
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            return send_file(
                candidate,
                mimetype="application/zip",
                as_attachment=True,
                download_name=f"chatgpt_research_bundle_{stamp}.zip",
            )
    try:
        out_zip, _ = build(
            agent_root=agent_root,
            data_root=DATA_ROOT,
            report_root=ROOT,
        )
    except Exception as exc:
        abort(500, description=f"ChatGPT bundle build failed: {exc}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return send_file(
        out_zip,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"chatgpt_research_bundle_{stamp}.zip",
    )


@app.route("/download/gpt-audit")
def download_gpt_audit_bundle():
    """Full-stack GPT audit: bot.py + analyzers + genome modules + implementation checklist."""
    agent_root = _agent_source_root()
    out_zip = _ensure_current_gpt_audit_bundle(agent_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return send_file(
        out_zip,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"gpt_audit_bundle_{stamp}.zip",
    )


@app.route("/api/gpt-audit")
def api_gpt_audit_status():
    agent_root = _agent_source_root()
    manifest_path = agent_root / "research" / "downloads" / "GPT_AUDIT_MANIFEST.json"
    if not manifest_path.is_file():
        return jsonify({"ready": False, "message": "Run analyzer once to generate GPT audit bundle."})
    try:
        meta = json.loads(manifest_path.read_text(encoding="utf-8"))
        return jsonify({"ready": True, **meta})
    except (json.JSONDecodeError, OSError) as exc:
        return jsonify({"ready": False, "error": str(exc)})


@app.route("/download/genome")
def download_genome_bundle():
    """Genome + DNA fingerprints + integrity audit — one ZIP for ChatGPT/review."""
    agent_root = _agent_source_root()
    genome_dir = agent_root / "research" / "genome"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    buf = io.BytesIO()
    names = (
        "genome_analysis_report.json",
        "genome_library.json",
        "genome_discoveries.json",
        "genome_cluster_library.json",
        "data_integrity_audit.json",
    )
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        added = 0
        for name in names:
            p = genome_dir / name
            if p.is_file():
                zf.write(p, arcname=f"genome/{name}")
                added += 1
        db = agent_root / "research.db"
        if db.is_file():
            zf.write(db, arcname="research.db")
            added += 1
        zf.writestr(
            "README.txt",
            "Genome bundle — analysis report, library, discoveries, DNA fingerprints.\n"
            "Upload to ChatGPT with GPT Audit Bundle for full stack review.\n",
        )
        if added == 0:
            abort(404, description="No genome reports yet — run analyzer once.")
    buf.seek(0)
    return send_file(
        buf,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"genome_dna_bundle_{stamp}.zip",
    )


@app.route("/api/accumulator")
def api_accumulator():
    """Return accumulator status as JSON; never masquerade as a ZIP."""
    try:
        from research_trade_accumulator import (
            ACCUMULATOR_DIR,
            STATUS_FILE,
            sync_accumulator,
        )
    except ImportError:
        abort(503)
    status = sync_accumulator(root=ROOT)
    status_path = ROOT / ACCUMULATOR_DIR / STATUS_FILE
    if status_path.is_file():
        try:
            return jsonify(json.loads(status_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return jsonify(status)


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
  html, body { width: 100%; max-width: 100%; overflow-x: hidden; }
  body { margin: 0; font-family: system-ui, sans-serif; background: var(--bg); color: var(--text); }
  header { width: 100%; max-width: 100%; overflow: hidden; padding: 16px 24px; border-bottom: 1px solid var(--border); display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
  header > div { min-width: 0; max-width: 100%; }
  header > div:last-child { display: flex; flex-wrap: wrap; gap: 6px; }
  header h1 { margin: 0; font-size: 1.25rem; }
  .meta { color: var(--muted); font-size: 0.85rem; }
  .badge { display: inline-block; flex: 0 1 auto; min-width: 0; max-width: 100%; white-space: normal; overflow-wrap: anywhere; background: var(--panel); border: 1px solid var(--border); padding: 4px 10px; border-radius: 999px; font-size: 0.75rem; }
  .badge.ok { border-color: var(--green); color: var(--green); }
  nav { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 24px; border-bottom: 1px solid var(--border); background: #0e1319; }
  nav button { flex: 0 0 auto; max-width: 100%; white-space: normal; overflow-wrap: anywhere; background: var(--panel); color: var(--text); border: 1px solid var(--border); padding: 8px 14px; border-radius: 8px; cursor: pointer; font-size: 0.85rem; }
  nav button.active { border-color: var(--accent); color: var(--accent); }
  nav.subnav { padding-top: 0; border-bottom-color: #1a2430; background: #0b1016; }
  nav.subnav button { font-size: 0.8rem; padding: 6px 12px; opacity: 0.9; }
  nav.subnav button.active { opacity: 1; }
  .lane-toggle { display: inline-flex; align-items: center; gap: 8px; margin: 8px 0 4px; color: var(--muted); font-size: 0.85rem; cursor: pointer; }
  .lane-toggle input { accent-color: var(--accent); }
  main { padding: 20px 24px; width: 100%; max-width: 1200px; min-width: 0; overflow: hidden; }
  section { display: none; min-width: 0; max-width: 100%; }
  section.active { display: block; }
  .kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 12px; margin-bottom: 20px; }
  #decision-readiness { grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr)); }
  #decision-readiness .val { font-size: 1rem; overflow-wrap: break-word; word-break: normal; }
  #genome-kpis { grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr)); }
  #genome-kpis .val { font-size: 1rem; overflow-wrap: break-word; word-break: normal; }
  .kpi { min-width: 0; overflow-wrap: anywhere; background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 14px; }
  .kpi .lbl { font-size: 0.7rem; color: var(--muted); text-transform: uppercase; }
  .kpi .val { font-size: 1.4rem; font-weight: 700; margin-top: 4px; }
  #lifecycle-bundle-kpis { grid-template-columns: repeat(auto-fit, minmax(min(230px, 100%), 1fr)); }
  #lifecycle-bundle-kpis .val { overflow-wrap: normal; word-break: normal; }
  .table-scroll { width: 100%; max-width: 100%; min-width: 0; overflow-x: auto; overscroll-behavior-inline: contain; -webkit-overflow-scrolling: touch; margin-top: 12px; }
  .table-scroll:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .table-scroll table { display: table; width: max-content; min-width: 100%; max-width: none; overflow: visible; border-collapse: collapse; font-size: 0.9rem; margin-top: 0; }
  th, td { border: 1px solid var(--border); padding: 8px 10px; text-align: left; }
  th { background: var(--panel); }
  tr:nth-child(even) { background: #101820; }
  .green { color: var(--green); font-weight: 600; }
  .red { color: var(--red); font-weight: 600; }
  .amber { color: var(--amber); }
  pre { min-width: 0; max-width: 100%; background: var(--panel); border: 1px solid var(--border); padding: 12px; border-radius: 8px; overflow-x: auto; font-size: 0.8rem; white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; }
  .btn { display: inline-block; background: var(--accent); color: #001018; padding: 12px 20px; border-radius: 8px; text-decoration: none; font-weight: 700; margin: 8px 8px 8px 0; }
  .btn.secondary { background: var(--panel); color: var(--text); border: 1px solid var(--border); }
  .grid2 { display: grid; grid-template-columns: 280px 1fr; gap: 16px; }
  @media (max-width: 800px) {
    header, nav, main { padding-left: 12px; padding-right: 12px; }
    .grid2 { grid-template-columns: 1fr; }
    .kpis { grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); }
    pre { max-width: 100%; }
  }
  @media (max-width: 600px) {
    header > div:last-child { width: 100%; }
    .kpis { grid-template-columns: minmax(0, 1fr); }
  }
  ul.findings li { margin-bottom: 8px; line-height: 1.4; }
  .explorer-list { list-style: none; padding: 0; margin: 0; max-height: 70vh; overflow: auto; }
  .explorer-list li { border-bottom: 1px solid var(--border); }
  .explorer-list button { width: 100%; padding: 8px 10px; border: 0; border-radius: 0; background: transparent; color: inherit; text-align: left; cursor: pointer; font: inherit; font-size: 0.85rem; }
  .explorer-list button:hover { background: var(--panel); }
  .explorer-list button:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .explorer-list button.sel { background: #1a2838; color: var(--accent); }
  h2 { font-size: 1rem; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
  .note { max-width: 100%; overflow-wrap: anywhere; color: var(--muted); font-size: 0.8rem; }
  .empty-state { min-width: 0; max-width: 100%; overflow-wrap: anywhere; border: 1px solid var(--border); border-radius: 8px; padding: 16px; color: var(--muted); background: var(--panel); }
  .stale-banner { background: #3d1f1f; border: 1px solid #f85149; color: #ffb4b4; padding: 12px 16px; border-radius: 8px; margin-bottom: 16px; font-size: 0.9rem; overflow-wrap: anywhere; word-break: break-word; }
</style></head><body>
<div id="integrity-banner" class="stale-banner" style="display:none;background:#3d2a1f;border-color:#d29922;color:#f8e3a1;"></div>
<div id="stale-banner" class="stale-banner" style="display:none;"></div>
<header>
  <div>
    <h1>Research Dashboard</h1>
    <div class="meta">Read-only · analyzer outputs · <span id="scope">loading…</span></div>
  </div>
  <div>
    <span class="badge ok" id="health">READ-ONLY</span>
    <span class="badge" id="sync">—</span>
    <span class="badge" id="revision" title="Analyzer generation revision">rev —</span>
    <span class="badge" id="epoch" title="Signed collection epoch">epoch —</span>
    <span class="badge" id="melb-clock" title="Australia/Melbourne">Melbourne —</span>
    <span class="badge" id="updated">—</span>
  </div>
</header>
<nav id="nav"></nav>
<nav id="subnav" class="subnav" style="display:none"></nav>
<main>
  <section id="sec-summary" class="active">
    <h2>Executive Summary</h2>
    <div class="empty-state" id="collection-status">
      <b>Collection ON:</b> raw signal, feature, order, fill, lifecycle,
      MFE/MAE and generic shadow evidence continue independently of analysis.
      Dashboard reports are cached and deterministic. AI egress is reserved for
      the trading-direction pipeline only.
    </div>
    <div class="kpis" id="kpis"></div>
    <h3>Lifecycle evidence bundles</h3>
    <div class="kpis" id="lifecycle-bundle-kpis"></div>
    <p class="note" id="lifecycle-bundle-note">Transfer-ready audit copies are audit-only and cannot rank, profitability-qualify, or authorize source cleanup.</p>
    <p class="note" id="cohort-note"></p>
    <h2>Best Policy Research</h2>
    <p class="note">Only complete paths from the current epoch count. A policy is shown only after independent untouched out-of-sample evidence passes every qualification gate.</p>
    <p class="note"><strong>V3.1 evidence:</strong> <a href="/safe-policy-genome-v3.1">Safe Policy Genome</a> · <a href="/cross-world-evidence">Cross-world evidence</a> · <a href="/static-policies">Static policies</a> · <a href="/dynamic-policies">Dynamic/regime</a> · <a href="/shadow-research">Shadow paths</a> · <a href="/risk-drawdown">Risk/drawdown</a> · <a href="/chronological-oos">Chronological OOS</a> · <a href="/evidence-maturity">Evidence maturity</a> · <a href="/partial-reduction">Partial-reduction reconciliation</a></p>
    <div class="kpis" id="decision-readiness"></div>
    <h3>Three strategy truth tiers</h3>
    <p class="note">The descriptive tier remains visible when integrity or qualification fails. It is an ideal-touch diagnostic, not evidence that a strategy works.</p>
    <div class="table-scroll" tabindex="0"><table><thead><tr><th>Tier</th><th>Status</th><th>Leader</th><th>Evidence label</th><th>UNKNOWN</th><th>Blockers</th></tr></thead><tbody id="strategy-leader-tiers"><tr><td colspan="6">Loading current atomic generation…</td></tr></tbody></table></div>
    <h3>Mandatory Bitfinex qualification gates</h3>
    <p class="note">PASS requires current-generation evidence. FAIL is a measured failure, UNKNOWN means the evidence has not been supplied, and UNAVAILABLE means no exact current analyzer generation can be evaluated.</p>
    <div class="table-scroll" tabindex="0"><table><thead><tr><th>Gate</th><th>Status</th><th>Evidence / receipt</th><th>Precise blocker</th></tr></thead><tbody id="qualification-gate-body"><tr><td colspan="4">Loading qualification gates…</td></tr></tbody></table></div>
    <p class="note" id="decision-readiness-provenance"></p>
    <pre id="exec-text"></pre>
    <p class="note">Active tab refreshes every 3 minutes. Analyzer loop: <code>analyzer_research_engine_v62.py</code> + <code>research/genome/run_analyzer.py</code>. Genome engine schema v11 is independent of the active bot release shown in the header.</p>
  </section>
  <section id="sec-findings">
    <h2>Research Findings</h2>
    <div class="kpis" id="hl-kpis"></div>
    <ol class="findings" id="findings-list"></ol>
  </section>
    <section id="sec-lanes">
    <h2>Current Lane Analysis</h2>
    <p class="note" id="lanes-filter-note">Current lanes: {{ tile_lane_names }}. Archived lane names remain available only in quarantine artifacts.</p>
    <p class="note" id="lanes-evidence-note"></p>
    <p class="note">Executed paper closes and counterfactual/lab terminals are separate evidence classes. Counterfactual results never count as fills, executed PnL, or strategy qualification.</p>
    <table><thead><tr><th>Lane</th><th>Status</th><th>Approvals</th><th>Executed closes</th><th>Executed net PnL</th><th>Executed EV / approval</th><th>Counterfactual terminals</th><th>Counterfactual PnL</th></tr></thead><tbody id="lane-body"></tbody></table>
  </section>
  <section id="sec-regime">
    <h2>Regime Leaderboard</h2>
    <p class="note" id="regime-note">Recommend-only — best lane per weekend/weekday × ADX × spread bucket.</p>
    <div class="kpis" id="regime-kpis"></div>
    <table><thead><tr><th>Regime</th><th>Trades</th><th>Best lane</th><th>Best EV</th><th>2nd lane</th><th>OK?</th></tr></thead><tbody id="regime-body"></tbody></table>
    <h3>Roster policy (recommend only)</h3>
    <pre id="roster-policy-json">Loading…</pre>
  </section>
  <section id="sec-chase">
    <h2>Chase Analytics</h2>
    <label class="lane-toggle">Lane: <select id="chase-lane-filter"><option value="">Combined</option>{% for lane in tile_lanes %}<option value="{{ lane }}">{{ lane }}</option>{% endfor %}</select></label>
    <div class="kpis" id="chase-kpis"></div>
    <h3>Executed paper outcomes</h3>
    <table><thead><tr><th>Bucket</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Avg hold (min)</th></tr></thead><tbody id="chase-body"></tbody></table>
    <h3>Shadow and counterfactual outcomes</h3>
    <p class="note">All current generic shadow and per-tile LAB terminal outcomes, kept separate from executed fills.</p>
    <table><thead><tr><th>Bucket</th><th>N</th><th>WR%</th><th>Shadow PnL</th><th>Shadow EV</th><th>Avg hold (min)</th></tr></thead><tbody id="chase-shadow-body"></tbody></table>
  </section>
  <section id="sec-chase-threshold">
    <label class="lane-toggle chase-lane-filter-wrap">Lane: <select class="chase-lane-filter"><option value="">Combined</option>{% for lane in tile_lanes %}<option value="{{ lane }}">{{ lane }}</option>{% endfor %}</select></label>
    <h2>Chase Threshold Analysis</h2>
    <p class="note" id="chase-threshold-note">Executed paper and shadow/lab outcomes are analyzed separately by exact chase count.</p>
    <div class="kpis" id="chase-threshold-kpis"></div>
    <h3>Executed paper outcomes</h3>
    <table><thead><tr><th>Threshold</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Avg hold (min)</th></tr></thead><tbody id="chase-threshold-body"></tbody></table>
    <h3>Shadow and counterfactual outcomes</h3>
    <p class="note">Includes generic shadows and per-tile LAB paths. These are simulated outcomes, not fills or realized profit.</p>
    <table><thead><tr><th>Threshold</th><th>N</th><th>WR%</th><th>Shadow PnL</th><th>Shadow EV</th><th>Avg hold (min)</th></tr></thead><tbody id="chase-threshold-shadow-body"></tbody></table>
  </section>
  <section id="sec-chase-policy-lab">
    <h2>Chase Policy Lab</h2>
    <div class="stale-banner" style="display:block;background:#3d2a1f;border-color:#d29922;color:#f8e3a1;"><strong>SHADOW-ONLY DESCRIPTIVE EVIDENCE</strong> · Never mixed with executed PnL or qualification.</div>
    <p class="note" id="chase-policy-lab-note">Signed compressed schedules are joined to causal identity and checkpoint tape evidence.</p>
    <h3 id="chase-policy-leader-label">INSUFFICIENT EVIDENCE</h3>
    <pre id="chase-policy-top">No signed schedule evidence.</pre>
    <div class="kpis" id="chase-policy-kpis"></div>
    <table><thead><tr><th>Rank</th><th>Schedule</th><th>Independent opportunities</th><th>Supported</th><th>Full / partial / no-fill / unsupported</th><th>Fill rate</th><th>Shadow PnL / EV</th><th>Max DD / tail</th><th>MFE / MAE</th><th>Coverage / confidence / lower bound</th><th>Regimes</th><th>Evidence / qualification</th></tr></thead><tbody id="chase-policy-body"></tbody></table>
    <h3>Missed Opportunity Proof</h3>
    <p class="note">Only four classifications are permitted: PROVEN_MISSED_PROFIT, PROVEN_AVOIDED_LOSS, AMBIGUOUS, or INSUFFICIENT_EVIDENCE.</p>
    <div class="kpis" id="missed-proof-kpis"></div>
    <table><thead><tr><th>Classification</th><th>Episode / policy</th><th>Direction</th><th>Touch / terminal return</th><th>MFE / MAE</th><th>Coverage</th><th>Regime / ADX</th><th>Contraindications</th></tr></thead><tbody id="missed-proof-body"></tbody></table>
  </section>
  <section id="sec-chase-delay">
    <label class="lane-toggle chase-lane-filter-wrap">Lane: <select class="chase-lane-filter"><option value="">Combined</option>{% for lane in tile_lanes %}<option value="{{ lane }}">{{ lane }}</option>{% endfor %}</select></label>
    <h2>Chase Delay (Pathway Lab)</h2>
    <p class="note" id="chase-delay-note">COMBO Direct vs Chase 3+ — delayed virtual-chase entry within each AI/spread tier.</p>
    <div class="kpis" id="chase-delay-kpis"></div>
    <table><thead><tr><th>Lane</th><th>Approves</th><th>Fills</th><th>Fill%</th><th>WR%</th><th>PnL</th><th>EV/appr</th><th>EV/trade</th><th>Avg age(s)</th></tr></thead><tbody id="chase-delay-body"></tbody></table>
  </section>
  <section id="sec-combos">
    <h2>Top Profitable Conservative Policy Combos</h2>
    <p class="note" id="policy-grid-note">Only positive policies with supported conservative BBO/depth fills appear here. Execution fills are split into full and partial receipts. A blank table means this generation has no profitable conservative policy; negative policies are not presented as leaders.</p>
    <div class="kpis" id="policy-grid-kpis"></div>
    <p class="muted">OOS episodes are candidate opportunities in the chronological holdout. They are not fills or complete trade lifecycles. Unsupported means execution is unknown/unverifiable; it must not be read as NO_FILL.</p>
    <table><thead><tr><th>#</th><th>Family</th><th>Family rank</th><th>Policy / parameters</th><th>OOS candidate opportunities</th><th>Supported episodes</th><th>Full fills</th><th>Partial fills</th><th>No fills</th><th>Unknown / unverifiable</th><th>Fill rate</th><th>Execution wins / losses</th><th>Execution OOS PnL</th><th>Execution EV / episode</th><th>Execution max drawdown</th><th>Evidence status</th></tr></thead><tbody id="policy-grid-body"></tbody></table>
    <h2>Positive Ideal-Touch Diagnostic Hypotheses</h2>
    <div class="stale-banner" style="display:block;background:#3d2a1f;border-color:#d29922;color:#f8e3a1;"><strong>IDEAL_TOUCH_DIAGNOSTIC_ONLY</strong> · <strong>NOT EXECUTION VERIFIED</strong> · <strong>NOT QUALIFICATION ELIGIBLE</strong></div>
    <p class="note">Separate research screen: Diagnostic touches are price-path touches only. Diagnostic replay PnL is not conservative execution PnL. These rows can prioritize further testing but cannot qualify a strategy.</p>
    <table><thead><tr><th>#</th><th>Family</th><th>Policy / parameters</th><th>Rolling OOS candidate opportunities</th><th>Ideal touches</th><th>No touches</th><th>Diagnostic wins / losses after touch</th><th>Diagnostic PnL</th><th>Diagnostic drawdown</th><th>Evidence status</th></tr></thead><tbody id="diagnostic-policy-grid-body"></tbody></table>
    <h3>Observed executed-lane combinations</h3>
    <p class="note" id="combos-note">Separate legacy direction-only cohort: ADX × normalized score gap × entry path × lane — sorted by EV.</p>
    <div class="kpis" id="combos-kpis"></div>
    <table><thead><tr><th>Combo</th><th>ADX</th><th>Score gap</th><th>Entry</th><th>Lane</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th></tr></thead><tbody id="combos-body"></tbody></table>
  </section>
  <section id="sec-spread-perf">
    <h2>Normalized Gap Performance</h2>
    <p class="note" id="spread-perf-note">Normalized score gap = abs(LONG score - SHORT score) / 10. This is not exchange bid/ask spread.</p>
    <div class="kpis" id="spread-perf-kpis"></div>
    <table><thead><tr><th>Gap bucket</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th></tr></thead><tbody id="spread-perf-body"></tbody></table>
  </section>
  <section id="sec-pathway-audit">
    <h2>Pathway Audit</h2>
    <p class="note">Startup validation — tile independence, version sync, exit reports.</p>
    <div class="kpis" id="audit-kpis"></div>
    <h3>Tile independence tests</h3>
    <table><thead><tr><th>Test</th><th>Pass</th><th>Detail</th></tr></thead><tbody id="audit-tile-body"></tbody></table>
    <h3>AI scan pipeline independence</h3>
    <table><thead><tr><th>Test</th><th>Pass</th><th>Detail</th></tr></thead><tbody id="audit-aiscan-body"></tbody></table>
    <h3>AI scan role (coordinator-only)</h3>
    <table><thead><tr><th>Check</th><th>Pass</th><th>Detail</th></tr></thead><tbody id="audit-aiscan-role-body"></tbody></table>
    <h3>Runtime pathway integrity</h3>
    <table><thead><tr><th>Issue</th><th>Severity</th></tr></thead><tbody id="audit-runtime-body"></tbody></table>
  </section>
  <section id="sec-exit-combos">
    <h2>Exit Combinations</h2>
    <p class="note" id="exit-combos-note">Exit reason × AI × spread × peak MFE × time-in-trade × lane.</p>
    <div class="kpis" id="exit-combos-kpis"></div>
    <h3>Executed-paper exit-family scorecard</h3>
    <p class="note">Low-dimensional family comparison. EV is divided by independent shared opportunities, not correlated family children. Missing values remain missing.</p>
    <table><thead><tr><th>Exit family</th><th>Terminals</th><th>Independent N</th><th>Wins / losses</th><th>Net PnL</th><th>EV / independent</th><th>Max DD</th><th>Missing identity / PnL / costs / slip</th><th>Evidence</th></tr></thead><tbody id="exit-family-scorecard-body"></tbody></table>
    <h3>Shadow/lab exit-family scorecard — separate descriptive evidence</h3>
    <table><thead><tr><th>Exit family</th><th>Terminals</th><th>Independent N</th><th>Wins / losses</th><th>Net PnL</th><th>EV / independent</th><th>Max DD</th><th>Missing identity / PnL / costs / slip</th><th>Evidence</th></tr></thead><tbody id="exit-family-scorecard-shadow-body"></tbody></table>
    <h3>Executed-paper stop-effectiveness matrix</h3>
    <p class="note">Exit reason × configured stop × chase bucket. This is descriptive attribution, not evidence that a stop caused or prevented profit.</p>
    <table><thead><tr><th>Stop</th><th>ATR distance</th><th>Hard %</th><th>Exit reason</th><th>Chase</th><th>Terminals</th><th>Independent N</th><th>Wins / losses</th><th>Net PnL</th><th>Avg MAE%</th><th>Avg stop slip</th><th>Missing identity / PnL / MAE / slip</th><th>Evidence</th></tr></thead><tbody id="stop-effectiveness-body"></tbody></table>
    <h3>Shadow/lab stop-effectiveness matrix — separate descriptive evidence</h3>
    <table><thead><tr><th>Stop</th><th>ATR distance</th><th>Hard %</th><th>Exit reason</th><th>Chase</th><th>Terminals</th><th>Independent N</th><th>Wins / losses</th><th>Net PnL</th><th>Avg MAE%</th><th>Avg stop slip</th><th>Missing identity / PnL / MAE / slip</th><th>Evidence</th></tr></thead><tbody id="stop-effectiveness-shadow-body"></tbody></table>
    <h3>Causal exit-policy combinations</h3>
    <p class="note">Family × exit profile × terminal reason. Paper and shadow/lab rows remain separate; missing dimensions are not guessed.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-policy-body"></tbody></table>
    <h3>Risk and chase combinations</h3>
    <p class="note">Initial ATR stop × hard-stop percentage × exact chase bucket × terminal reason.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-risk-body"></tbody></table>
    <h3>Market-context exit combinations</h3>
    <p class="note">Regime × direction × family × terminal reason.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-market-body"></tbody></table>
    <h3>Entry/execution exit combinations</h3>
    <p class="note">Offset × exact chase bucket × entry-delay band × fill status × terminal reason.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-entry-body"></tbody></table>
    <h3>Market microstructure exit combinations</h3>
    <p class="note">Regime × volatility band × session × support/resistance state × direction × terminal reason. Only explicitly recorded fields are grouped.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-microstructure-body"></tbody></table>
    <h3>Profit-path exit combinations</h3>
    <p class="note">Exit profile × MAE band × entry-delay band × terminal reason. Missing excursion data remains unavailable.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-profit-path-body"></tbody></table>
    <h3>Cost and fill exit combinations</h3>
    <p class="note">Fill status × slippage band × fee band × terminal reason. Diagnostic and execution costs remain in separate evidence worlds.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-cost-body"></tbody></table>
    <h3>Direction-quality exit combinations</h3>
    <p class="note">Entry ADX × multi-timeframe agreement × recorded structure bias × direction × terminal reason. Missing entry-state fields remain explicit.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-direction-quality-body"></tbody></table>
    <h3>Support/resistance geometry exit combinations</h3>
    <p class="note">Support/resistance state and recorded distances × direction × family × terminal reason.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-sr-geometry-body"></tbody></table>
    <h3>Execution-quality exit combinations</h3>
    <p class="note">Maker/taker entry and exit × partial/full entry × fill model × slippage × terminal reason.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-execution-quality-body"></tbody></table>
    <h3>Partial-profit path combinations</h3>
    <p class="note">Normalized partial-exit receipt count × remaining-position band × family × terminal reason.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-partial-profit-body"></tbody></table>
    <h3>Exact chase-detail exit combinations</h3>
    <p class="note">Exact chase bucket × urgent tier × delay × offset × family/profile × terminal reason. Global submission permission and tile-local repricing remain distinct.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-chase-detail-body"></tbody></table>
    <h3>Observed excursion-timing combinations</h3>
    <p class="note">One-second path MAE/MFE bands × time-to-MAE/time-to-MFE × family × terminal reason. Unavailable tape paths are excluded and reported.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-excursion-timing-body"></tbody></table>
    <h3>Regime-transition exit combinations</h3>
    <p class="note">Entry regime/ADX × exit regime/ADX × direction × terminal reason. This describes observed transitions and does not use hindsight to switch policies.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-regime-transition-body"></tbody></table>
    <h3>Fill-time direction revalidation outcomes</h3>
    <p class="note">Revalidation result/reason × signal-age band × exact chase × terminal reason. Blocked fills remain terminal no-fill evidence, not losing trades.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-fill-revalidation-body"></tbody></table>
    <h3>Terminal order outcomes</h3>
    <p class="note">No-fill and TTL classification × terminal reason × exact chase. Missing results are unavailable rather than zero PnL.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-terminal-order-body"></tbody></table>
    <h3>Path-sequence combinations</h3>
    <p class="note">Whether adverse or favorable excursion occurred first, combined with excursion bands, family and exit reason. Only timestamped tape paths are included.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-path-sequence-body"></tbody></table>
    <h3>Protection-activation combinations</h3>
    <p class="note">Partial-profit count and terminal remaining fraction by exit profile and terminal reason.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-protection-body"></tbody></table>
    <h3>Stop execution quality</h3>
    <p class="note">Configured ATR/hard stop versus observed slippage and partial/full entry. Missing execution receipts remain unavailable.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-stop-quality-body"></tbody></table>
    <h3>Liquidity at exit</h3>
    <p class="note">Side-correct exit-book basis, visible executable depth, levels consumed and slippage by terminal reason.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-liquidity-body"></tbody></table>
    <h3>Cost-drag combinations</h3>
    <p class="note">Observed fees, funding and slippage are combined only when at least one cost receipt exists; missing cost data is never treated as zero.</p>
    <table><thead><tr><th>Evidence world</th><th>Combination</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Identity status</th></tr></thead><tbody id="exit-causal-cost-drag-body"></tbody></table>
    <h3>Family-balanced descriptive exit-combo EV — unqualified</h3>
    <p class="note">Observed cohorts only. Small or unmatched samples are not evidence that an exit caused the result and cannot qualify a policy.</p>
    <table><thead><tr><th>Combo</th><th>Exit</th><th>AI</th><th>Spread</th><th>MFE</th><th>Time</th><th>Type</th><th>Lane</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th><th>Left</th></tr></thead><tbody id="exit-combos-body"></tbody></table>
    <h3>Worst leakage cohorts</h3>
    <table><thead><tr><th>Combo</th><th>Exit</th><th>N</th><th>Left on table</th><th>Avg left</th><th>EV</th></tr></thead><tbody id="exit-leak-body"></tbody></table>
    <h3>Shadow/lab exit combos — separate descriptive evidence</h3>
    <table><thead><tr><th>Combo</th><th>Exit</th><th>N</th><th>Sample</th><th>PnL</th><th>EV</th></tr></thead><tbody id="exit-shadow-combos-body"></tbody></table>
    <h3>Conservative BBO/depth replay exits — separate evidence</h3>
    <table><thead><tr><th>Combo</th><th>Exit</th><th>N</th><th>Sample</th><th>Conservative PnL</th><th>EV</th></tr></thead><tbody id="exit-conservative-combos-body"></tbody></table>
    <h3>Ideal-touch diagnostic exits — not fill evidence</h3>
    <table><thead><tr><th>Combo</th><th>Exit</th><th>N</th><th>Sample</th><th>Diagnostic PnL</th><th>EV</th></tr></thead><tbody id="exit-ideal-touch-combos-body"></tbody></table>
  </section>
  <section id="sec-exit-reason-leak">
    <h2>Exit Peak-to-Close Gap (Combined Lanes)</h2>
    <p class="note" id="exit-reason-note">Hindsight MFE minus realized close. This is not directly capturable profit and cannot prescribe an exit change without tick replay validation.</p>
    <div class="kpis" id="exit-reason-kpis"></div>
    <table><thead><tr><th>Exit reason</th><th>N</th><th>Hindsight gap $</th><th>Avg gap $</th><th>Avg MFE%</th><th>Realized%</th><th>Peak-close gap%</th><th>Peak capture%</th></tr></thead><tbody id="exit-reason-body"></tbody></table>
    <h3>Shadow/lab peak-to-close gap — separate descriptive evidence</h3>
    <table><thead><tr><th>Exit reason</th><th>N</th><th>Sample</th><th>Hindsight gap $</th><th>Booked $</th><th>Peak $</th></tr></thead><tbody id="exit-reason-shadow-body"></tbody></table>
    <h3>Conservative BBO/depth replay gap — separate evidence</h3>
    <table><thead><tr><th>Exit reason</th><th>N</th><th>Sample</th><th>Gap $</th><th>Booked $</th><th>Peak $</th></tr></thead><tbody id="exit-reason-conservative-body"></tbody></table>
    <h3>Ideal-touch diagnostic gap — not execution evidence</h3>
    <table><thead><tr><th>Exit reason</th><th>N</th><th>Sample</th><th>Gap $</th><th>Diagnostic booked $</th><th>Peak $</th></tr></thead><tbody id="exit-reason-ideal-touch-body"></tbody></table>
    <h3>Validation required</h3>
    <ul id="exit-reason-recs"></ul>
  </section>
  <section id="sec-ladder-sim">
    <h2>Ladder Replay Simulator (Combined Lanes)</h2>
    <p class="note" id="ladder-sim-note">Counterfactual replay on the subset with tick data. Each profile is compared only with the booked PnL of those same trades.</p>
    <p class="note amber" id="ladder-sim-disclaimer"></p>
    <div class="kpis" id="ladder-sim-kpis"></div>
    <table><thead><tr><th>Profile</th><th>Ladder rungs</th><th>N sim</th><th>Sim PnL</th><th>Avg PnL</th><th>WR%</th><th>Ladder exit%</th><th>Delta vs matched actual</th></tr></thead><tbody id="ladder-sim-body"></tbody></table>
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
    <h2>AI Direction &amp; Gap Laboratory</h2>
    <p class="note" id="ai-mode-note">Loading the current AI evidence mode…</p>
    <div id="ai-gap-view">
      <h3>Normalized score-gap performance</h3>
      <p class="note" id="ai-gap-note">Raw LONG-vs-SHORT score difference divided by 10. Example: raw gap 30 is bucket 3.</p>
      <table><thead><tr><th>Gap bucket</th><th>N</th><th>WR%</th><th>PnL</th><th>EV</th></tr></thead><tbody id="ai-gap-body"></tbody></table>
    </div>
    <div id="ai-confidence-view" style="display:none">
      <h3>Historical probability calibration</h3>
      <table><thead><tr><th>Band</th><th>N</th><th>WR%</th><th>PnL</th></tr></thead><tbody id="ai-cal-body"></tbody></table>
      <h3>Historical executed probability bands</h3>
      <table><thead><tr><th>Band</th><th>N</th><th>WR%</th><th>PnL</th></tr></thead><tbody id="ai-conf-body"></tbody></table>
    </div>
  </section>
  <section id="sec-genome">
    <h2>Trading Genome research</h2>
    <p class="note" id="genome-note">DNA-first analysis from research.db — discoveries, cluster match, decision &amp; lifecycle DNA. Advisory only.</p>
    <div class="empty-state" id="genome-empty">Loading the current Genome report…</div>
    <div id="genome-content" style="display:none">
    <div class="kpis" id="genome-kpis"></div>
    <p class="note" id="genome-taxonomy-note"></p>
    <h2>Current market cluster</h2>
    <pre id="genome-cluster"></pre>
    <h2>Discoveries</h2>
    <div id="genome-discoveries"></div>
    <h2>Decision DNA</h2>
    <pre id="genome-decision"></pre>
    <h2>Lifecycle DNA</h2>
    <pre id="genome-lifecycle"></pre>
    <h2>Hypotheses</h2>
    <pre id="genome-hypotheses"></pre>
    <h2>Replay capabilities</h2>
    <pre id="genome-replay"></pre>
    </div>
  </section>
  <section id="sec-research-design">
    <h2>Entry baselines &amp; Phase-7 regime evidence</h2>
    <div class="stale-banner" id="research-design-banner" style="display:block"></div>
    <p class="note">Signed comparison definitions are research-only and place no orders. Coverage reports only fields explicitly captured before entry. Definitions and coverage never create fills, PnL, profitability, qualification, or live authorization.</p>
    <div class="kpis" id="research-design-kpis"></div>
    <h2>Signed entry baseline registry</h2>
    <table><thead><tr><th>Baseline</th><th>Entry type</th><th>Timing</th><th>Required evidence</th><th>Execution status</th><th>Missing evidence</th></tr></thead><tbody id="research-baseline-body"></tbody></table>
    <h2>Observed / unknown regime dimensions</h2>
    <table><thead><tr><th>Dimension</th><th>Observed rows</th><th>Unknown rows</th><th>Status</th></tr></thead><tbody id="research-regime-coverage-body"></tbody></table>
  </section>
  <section id="sec-evidence-coverage">
    <h2>Evidence Coverage</h2>
    <div class="stale-banner" id="evidence-coverage-banner" style="display:block"></div>
    <p class="note">Read-only counts from the checksum-verified report declared by the active atomic analyzer generation. Missing evidence remains UNKNOWN and never becomes NO_FILL.</p>
    <div class="kpis" id="evidence-coverage-source-kpis"></div>
    <h2>Episode and terminal outcome coverage</h2>
    <div class="kpis" id="evidence-coverage-outcome-kpis"></div>
    <h2>Top missing-evidence reasons</h2>
    <table><thead><tr><th>Reason</th><th>Episodes / rows</th></tr></thead><tbody id="evidence-coverage-reasons"></tbody></table>
    <h2>Archive recovery and quarantined orphan</h2>
    <div class="kpis" id="evidence-coverage-archive-kpis"></div>
    <pre id="evidence-coverage-orphan">Loading…</pre>
    <p><a class="btn secondary" id="evidence-coverage-full" href="#">Inspect full declared report</a></p>
  </section>
  <section id="sec-edge">
    <h2>Edge &amp; Feature Importance</h2>
    <p class="note">Pearson correlation with PnL — validation only, not for auto-tuning.</p>
    <table><thead><tr><th>Feature</th><th>|r|</th><th>Correlation</th><th>N</th></tr></thead><tbody id="feat-body"></tbody></table>
    <p class="note" id="weak-signals"></p>
  </section>
  <section id="sec-explorer">
    <h2>Report Explorer</h2>
    <p class="note">Reports are loaded on demand. Select one report to inspect it; opening this tab never renders a large report automatically.</p>
    <div class="grid2">
      <ul class="explorer-list" id="report-list"></ul>
      <pre id="report-json">Select a report…</pre>
    </div>
  </section>
  <section id="sec-archives">
    <h2>Session Archive</h2>
    <p class="note">One archive folder per analyzer run (when enabled).</p>
    <table><thead><tr><th>Session</th><th>Time</th><th>Trades</th><th>PnL</th><th>Download</th></tr></thead><tbody id="archive-body"></tbody></table>
    <h2>Preserved Past Analysis</h2>
    <p class="note">Final derived findings preserved before Fresh Collection. Raw CSV/JSONL/database payloads are fingerprinted but excluded.</p>
    <table><thead><tr><th>Analysis</th><th>Sealed</th><th>Trades</th><th>PnL</th><th>Download</th></tr></thead><tbody id="past-analysis-body"></tbody></table>
  </section>
  <section id="sec-download">
    <h2>Download Center</h2>
    <div class="empty-state" id="collection-contract">
      <b>Collection boundary:</b> raw trading, order, fill, lifecycle, MFE/MAE,
      generic shadow evidence is collected continuously. This dashboard and
      analyzer are deterministic and out-of-process; they never call an AI
      provider. DeepSeek is reserved for the bot's trading-direction decision.
    </div>
    <p><b>Complete Research Evidence Bundle</b> — one verified ZIP containing the current Fly mirror, immutable relay lifecycle evidence, counterfactual and cohort data, current and historical reports, Genome/DNA artifacts, preserved sessions, source audit, and a SHA-256 manifest.</p>
    <a class="btn" href="/download/everything" id="dl-everything" style="background:#7b4cc9">⬇ Download Complete Research Evidence Bundle</a>
    <p class="note">Older specialized download routes remain available for compatibility, but are intentionally hidden here so there is one authoritative export.</p>
    <pre id="bundle-list"></pre>
  </section>
  <section id="sec-runtime-incidents">
    <h2>Runtime incident &amp; restart history</h2>
    <p class="note" id="runtime-incidents-note">Loading retained application crash receipts…</p>
    <table><thead><tr><th>Time (UTC)</th><th>Classification</th><th>Reason</th><th>Restart requested</th><th>Exit code</th><th>Evidence</th></tr></thead><tbody id="runtime-incidents-body"></tbody></table>
  </section>
</main>
<script>
const NAV_GROUPS = {{ nav_groups_json|safe }};
function ensureScrollableTables(root = document) {
  root.querySelectorAll('main table').forEach(table => {
    if (table.parentElement && table.parentElement.classList.contains('table-scroll')) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'table-scroll';
    wrapper.setAttribute('role', 'region');
    wrapper.setAttribute('tabindex', '0');
    const heading = table.closest('section')?.querySelector('h2');
    wrapper.setAttribute('aria-label', `${heading?.textContent?.trim() || 'Research'} table — scroll horizontally for more columns`);
    table.parentNode.insertBefore(wrapper, table);
    wrapper.appendChild(table);
  });
}
ensureScrollableTables();
const EVIDENCE_SCOPES = {
  summary: ['MIXED — CURRENT POLICY + LEGACY EXECUTED', 'Best-policy evidence is current/pinned; compact executed results and preserved history use separate older cohorts.'],
  findings: ['LEGACY EXECUTED', 'Derived from historical executed-lane reports, not the current signed V3.1 counterfactual policy grid.'],
  regime: ['LEGACY EXECUTED', 'Historical executed-lane regime/ADX aggregation; not a qualified dynamic policy.'],
  lanes: ['CURRENT CANONICAL TILE EVIDENCE', 'One causal opportunity is counted once; tile and child-mode evidence remains separated and does not imply live execution.'],
  ai: ['LEGACY EXECUTED', 'Historical AI direction/gap calibration; current policy-grid evidence is shown under Policy Grid & Legacy.'],
  chase: ['EXECUTED + SHADOW, SEPARATED', 'All available terminal chase outcomes are shown with paper execution and shadow/lab evidence kept distinct.'],
  'chase-policy-lab': ['SIGNED COMPRESSED SHADOW — NOT QUALIFICATION ELIGIBLE', 'Descriptive schedule and missed-opportunity proof evidence; executed outcomes remain separate and unavailable unless explicitly matched.'],
  'chase-threshold': ['EXECUTED + SHADOW, SEPARATED', 'Exact chase-count outcomes include paper and shadow/lab cohorts without mixing their PnL.'],
  'chase-delay': ['LEGACY EXECUTED', 'Historical pathway-lab chase delay comparison.'],
  combos: ['CURRENT V3.1 POLICY GRID + LEGACY EXECUTED — SEPARATED', 'The first table is signed current-epoch V3.1 counterfactual OOS research; the second is a separate legacy executed-lane cohort.'],
  'spread-perf': ['LEGACY EXECUTED', 'Historical executed-lane normalized score-gap aggregation.'],
  'exit-combos': ['CURRENT EXECUTED PAPER + SHADOW/LAB — SEPARATED', 'Current terminal exit combinations; observed paper and shadow/lab evidence are displayed separately and remain descriptive.'],
  'exit-reason-leak': ['CURRENT EXECUTED PAPER + SHADOW/LAB — SEPARATED', 'Current peak-to-close hindsight gaps; paper and shadow/lab evidence remain separate and are not directly capturable profit.'],
  'ladder-sim': ['LEGACY COUNTERFACTUAL', 'Older matched-trade ladder replay; separate from the current signed V3.1 Safe Policy Genome.'],
  exits: ['LEGACY HINDSIGHT', 'Historical peak-to-close leakage, not a current-policy result.'],
  genome: ['CURRENT V3.1 SAFE POLICY GENOME', 'Signed current-epoch policy replay. Descriptive rows remain blocked from live use until chronological OOS and risk gates pass.'],
  'evidence-coverage': ['CURRENT DECLARED ATOMIC GENERATION ONLY', 'Checksum-verified canonical counts and triage. Stale generations remain visible but are explicitly blocked from qualification.'],
  'research-design': ['RESEARCH DEFINITIONS + PRE-ENTRY COVERAGE ONLY', 'Signed baselines place no orders. OBSERVED/UNKNOWN feature coverage is not profitability or qualification evidence.'],
  edge: ['LEGACY EXECUTED', 'Historical feature correlation; validation only and never an automatic trading rule.'],
  explorer: ['MIXED ARTIFACT EXPLORER', 'Contains current, legacy, shadow, conservative, and unavailable artifacts; inspect each report provenance.'],
  archives: ['PRESERVED HISTORY', 'Sealed prior reports and sessions; not current-epoch policy evidence.'],
  download: ['MIXED EVIDENCE BUNDLE', 'Bundle may contain current and historical artifacts; manifest timestamps and per-report provenance remain authoritative.'],
  'runtime-incidents': ['RETAINED APPLICATION RECEIPTS', 'Application watchdog/crash receipts are shown separately. Fly platform and deployment causes remain unavailable unless an authoritative platform receipt exists.'],
  'pathway-audit': ['MIXED INTEGRITY REPORTS', 'Combines current runtime checks with historical lane/report contracts.'],
  horizon: ['LEGACY POST-EXIT REPLAY', 'Historical recovery/horizon evidence; not the current pinned policy grid.'],
};
const navEl = document.getElementById('nav');
const subnavEl = document.getElementById('subnav');
let _rdPrefs = {};
try { _rdPrefs = JSON.parse(localStorage.getItem('research_dashboard_prefs_v1') || '{}'); } catch (e) {}
let showAllLanes = !!_rdPrefs.showAllLanes;
let activeGroup = _rdPrefs.activeGroup || 'overview';
let activeSection = _rdPrefs.activeSection || 'summary';

function sectionGroup(secId) {
  for (const g of NAV_GROUPS) {
    if ((g.items || []).some(it => it[0] === secId)) return g.id;
  }
  return NAV_GROUPS[0] ? NAV_GROUPS[0].id : 'overview';
}
function savePrefs() {
  try {
    localStorage.setItem('research_dashboard_prefs_v1', JSON.stringify({
      activeSection, activeGroup, showAllLanes
    }));
  } catch (e) {}
}
function laneQuery() { return showAllLanes ? '?all=1' : ''; }
function chaseLaneQuery() {
  const sel = document.getElementById('chase-lane-filter');
  const lane = sel ? sel.value : '';
  return lane ? '?lane=' + encodeURIComponent(lane) : '';
}
function renderNav() {
  navEl.innerHTML = '';
  NAV_GROUPS.forEach(g => {
    const b = document.createElement('button');
    b.textContent = g.label;
    b.dataset.group = g.id;
    b.classList.toggle('active', g.id === activeGroup);
    b.onclick = () => showGroup(g.id);
    navEl.appendChild(b);
  });
  const g = NAV_GROUPS.find(x => x.id === activeGroup);
  const items = (g && g.items) || [];
  if (!g || items.length <= 1) {
    subnavEl.style.display = 'none';
    subnavEl.innerHTML = '';
    return;
  }
  subnavEl.style.display = 'flex';
  subnavEl.innerHTML = '';
  items.forEach(([id, label]) => {
    const b = document.createElement('button');
    b.textContent = label;
    b.dataset.sec = id;
    b.classList.toggle('active', id === activeSection);
    b.onclick = () => show(id);
    subnavEl.appendChild(b);
  });
}
function showGroup(gid) {
  activeGroup = gid;
  const g = NAV_GROUPS.find(x => x.id === gid);
  const first = g && g.items && g.items[0] ? g.items[0][0] : 'summary';
  show(first);
}
function show(id) {
  activeSection = id;
  activeGroup = sectionGroup(id);
  document.querySelectorAll('main section').forEach(s => s.classList.remove('active'));
  const sec = document.getElementById('sec-' + id);
  if (sec) {
    sec.classList.add('active');
    const scope = EVIDENCE_SCOPES[id] || ['SCOPE UNCLASSIFIED', 'This panel has not yet been mapped to an authoritative evidence cohort.'];
    let banner = sec.querySelector(':scope > .evidence-scope-banner');
    if (!banner) {
      banner = document.createElement('div');
      banner.className = 'empty-state evidence-scope-banner';
      sec.insertBefore(banner, sec.firstChild);
    }
    banner.innerHTML = `<b>Evidence scope: ${scope[0]}</b><br>${scope[1]}`;
  }
  renderNav();
  savePrefs();
  void refreshActiveSection();
}
function setEvidenceScope(id, title, note) {
  const sec = document.getElementById('sec-' + id);
  if (!sec) return;
  const banner = sec.querySelector(':scope > .evidence-scope-banner');
  if (banner) banner.innerHTML = `<b>Evidence scope: ${title}</b><br>${note}`;
}
const showAllEl = document.getElementById('show-all-lanes');
if (showAllEl) {
  showAllEl.checked = showAllLanes;
  showAllEl.addEventListener('change', () => {
    showAllLanes = !!showAllEl.checked;
    savePrefs();
    loadLanes();
  });
}
function fmtUsd(v) {
  if (v == null) return 'n/a';
  const value = Number(v);
  if (!Number.isFinite(value)) return 'n/a';
  if (Math.abs(value) < 0.005) return '0.00';
  return (value > 0 ? '+' : '') + value.toFixed(2);
}
function fmtExecutionUsd(v) { return v == null ? 'UNAVAILABLE' : '$' + fmtUsd(v); }
function fmtAdxBucket(v) {
  const key = String(v || '').toLowerCase();
  if (['adx_low', 'adx<18', 'adx_lt_18'].includes(key)) return 'ADX <18';
  if (['adx_mid', 'adx18-30', 'adx_18_to_30'].includes(key)) return 'ADX 18–<30';
  if (['adx_high', 'adx30+', 'adx_30_plus'].includes(key)) return 'ADX ≥30';
  return v || 'ADX unknown';
}
function fmtResearchBucket(dimension, bucket) {
  return String(dimension || '').toLowerCase() === 'adx' ? fmtAdxBucket(bucket) : (bucket || '');
}
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
  const hist = d.historical_cohort || {};
  const histPerf = hist.performance || {};
  const retention = d.retention || {};
  const storage = d.storage || {};
  const integrity = d.integrity || {};
  const lifecycleBundles = d.lifecycle_bundles || {};
  const iBanner = document.getElementById('integrity-banner');
  if (iBanner) {
    if (integrity.valid === false || integrity.report_status === 'INVALID') {
      iBanner.style.display = 'block';
      const integrityValue = value => (
        value && typeof value === 'object' ? JSON.stringify(value) : String(value)
      );
      const fails = (integrity.failed_checks || []).map(c => (
        `${c.check}: expected ${integrityValue(c.expected)}, found ${integrityValue(c.found)}`
      )).join(' · ');
      iBanner.innerHTML = '<strong>' + (integrity.banner || '⚠ REPORT INVALID') + '</strong> ' + fails;
    } else {
      iBanner.style.display = 'none';
    }
  }
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
        + '(full CSV since last Fresh Collection ON). Empty sections may truthfully mean no eligible evidence in the current cohort; check each section\'s evidence status.';
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
    ['Fresh executed', p.trades ?? 0],
    ['Historical dedup', hist.unique_trades ?? histPerf.trades ?? 'not imported'],
    ['Storage cleanup', retention.status === 'COMPLETED'
      ? ((retention.rotated_raw_deleted ?? 0) + ' rotations · '
        + (retention.raw_db_rows_deleted ?? 0) + ' raw rows · '
        + (Number(retention.deleted_bytes || 0) / 1048576).toFixed(1) + ' MB')
      : 'Pending first run'],
    ['Raw mirror cap', retention.raw_mirror_cap_status
      ? ((Number(retention.raw_mirror_bytes || 0) / 1073741824).toFixed(2) + ' / '
        + Number(retention.raw_mirror_cap_gib || 25).toFixed(0) + ' GiB · '
        + Number(retention.raw_mirror_usage_pct || 0).toFixed(1) + '% · '
        + retention.raw_mirror_cap_status)
      : 'Pending first retention measurement'],
    ['Local Fly mirror cache', storage.local_size_mb == null
      ? 'No size report'
      : (Number(storage.local_size_mb).toFixed(1) + ' MB / 30 GB · '
        + Number(storage.local_limit_pct || 0).toFixed(2) + '% · '
        + (storage.local_file_count ?? 0) + ' files')],
    ['Fly runtime data', storage.fly_size_mb == null
      ? 'No Fly size report'
      : (Number(storage.fly_size_mb).toFixed(1) + ' MB / '
        + Number(storage.fly_volume_total_mb || 1024).toFixed(0) + ' MB · '
        + Number(storage.fly_volume_pct || 0).toFixed(1) + '%')],
    ['Mirror sync receipt', storage.sync_computed_at
      ? ('local cache refreshed ' + fmtMelb(storage.sync_computed_at))
      : 'No local mirror receipt'],
    ...Object.entries(storage.categories || {}).map(([name, category]) => [
      'Storage · ' + name.replaceAll('_', ' '),
      category && category.status === 'OBSERVED'
        ? (Number(category.mb || 0).toFixed(3) + ' MB · OBSERVED')
        : 'UNKNOWN · lifecycle classification unavailable'
    ]),
    ['EV/trade', '$' + (p.expectancy_usd ?? 'n/a')],
    ['MFE Capture', (p.mfe_capture_pct ?? 'n/a') + '%'],
    ['APPROVE→Fill', (d.approve_to_fill_pct ?? 'n/a') + '%'],
    ['Gate Damage', '$' + fmtUsd(re.gate_damage_usd)],
    ['Sample', d.coverage_status || 'n/a'],
  ];
  document.getElementById('kpis').innerHTML = kpis.map(([l,v]) =>
    `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  const bundleKpis = document.getElementById('lifecycle-bundle-kpis');
  if (bundleKpis) {
    const unavailable = lifecycleBundles.status !== 'AVAILABLE_CURRENT_GENERATION';
    const parity = lifecycleBundles.parity || {};
    const rows = [
      ['manifest-verified qualification bundles', unavailable ? 'UNAVAILABLE' : (lifecycleBundles.qualification_count ?? 0)],
      ['transfer-ready audit copies', unavailable ? 'UNAVAILABLE' : (lifecycleBundles.transfer_audit_count ?? 0)],
      ['Bundle parity', unavailable ? 'UNAVAILABLE' : (
        (parity.intersection_count ?? 0) + ' matched · '
        + (parity.qualification_only_count ?? 0) + ' qualification only · '
        + (parity.transfer_only_count ?? 0) + ' transfer only'
      )],
      ['Invalid bundle manifests', unavailable ? 'UNAVAILABLE' : (lifecycleBundles.invalid_count ?? 0)],
    ];
    bundleKpis.innerHTML = rows.map(([label, value]) =>
      `<div class="kpi"><div class="lbl">${label}</div><div class="val">${value}</div></div>`
    ).join('');
  }
  const bundleNote = document.getElementById('lifecycle-bundle-note');
  if (bundleNote) {
    bundleNote.textContent = lifecycleBundles.status === 'AVAILABLE_CURRENT_GENERATION'
      ? 'Manifest inventory only · payload verification=' + (lifecycleBundles.payload_verification_status || 'UNKNOWN_NOT_SCANNED') + '. Transfer-ready audit copies: audit-only=true · ranking eligible=false · profitability supported=false · source cleanup authorized=false.'
      : 'Lifecycle bundle inventory is unavailable for this exact analyzer generation. Transfer-ready audit copies remain audit-only and cannot rank, profitability-qualify, or authorize source cleanup.';
  }
  const cohortNote = document.getElementById('cohort-note');
  if (cohortNote) {
    const dupes = hist.duplicates_removed ?? 0;
    const raw = hist.raw_rows ?? 0;
    cohortNote.textContent = hist.unique_trades != null
      ? `Current V3.1 counts each shared market opportunity once across paper and shadow evidence. Historical executed research remains separate: ${hist.unique_trades} unique trades from ${raw} exported rows (${dupes} duplicates removed).`
      : 'Current V3.1 counts each shared market opportunity once across paper and shadow evidence. Historical archives have not been imported on this machine.';
  }
  await loadDecisionReadiness();
}

async function loadDecisionReadiness() {
  const r = await fetch('/api/best-policy-research');
  const d = await r.json();
  const e = d.evidence || {};
  const coverage = e.outcome_coverage || {};
  const candidate = d.current_candidate || {};
  const challenger = d.descriptive_challenger || {};
  const design = d.research_design || {};
  const searchCounts = design.counts || {};
  const deployed = d.deployed_policy_collection || {};
  const deployedPolicies = deployed.policies || [];
  const tiers = d.strategy_leaders || {};
  const candidateName = candidate.policy_id || candidate.name || 'Hidden until qualified';
  const candidateKind = candidate.kind || '—';
  const dynamicSummary = candidate.kind === 'DYNAMIC'
    ? `${Object.keys(candidate.regime_policy_map || {}).length} regimes · fallback ${candidate.fallback || 'missing'} · drift ${candidate.drift_action || 'missing'}`
    : (candidate.kind === 'STATIC' ? (candidate.policy_signature || 'signature missing') : '—');
  const cards = [
    ['Research result', d.status || 'NO QUALIFIED POLICY', d.status === 'QUALIFIED' ? 'green' : 'amber'],
    ['Deployed policies collecting', deployed.policy_count || deployedPolicies.length || 0, 'amber'],
    ['Current candidate', candidateName, d.status === 'QUALIFIED' ? 'green' : 'amber'],
    ['Candidate type', candidateKind, d.status === 'QUALIFIED' ? 'green' : ''],
    ['Policy design', dynamicSummary, ''],
    ['Independent opportunities', e.current_epoch_events || e.independent_episode_count || 0, ''],
    ['Replay-eligible execution rows', e.replay_eligible_execution_rows ?? e.completed_paths ?? 0, ''],
    ['Filled / Unfilled / Rejected', `${coverage.ACCEPTED_FILLED || 0} / ${coverage.ACCEPTED_UNFILLED || 0} / ${coverage.REJECTED || 0}`, ''],
    ['Qualified OOS episodes', e.qualified_oos_episodes || 0, ''],
    ['Entry policies', Number(searchCounts.entry_policy_cartesian || 0).toLocaleString(), ''],
    ['Hierarchical search space', Number(searchCounts.naive_full_cartesian || 0).toLocaleString(), ''],
    ['Static vs dynamic', (design.static_vs_dynamic || {}).required ? 'Required · OOS decides' : 'Manifest unavailable', ''],
    ['Profitable OOS winner', challenger.winner_kind === 'NONE' ? 'NONE — no profitable OOS candidate' : (challenger.winner_kind || 'Waiting for matured OOS'), ''],
    ['Relative leader only', challenger.relative_leader_kind || 'Unavailable', ''],
    ['Static OOS expectancy', challenger.static_oos && challenger.static_oos.expectancy_usd != null ? '$' + Number(challenger.static_oos.expectancy_usd).toFixed(4) : 'Unavailable', ''],
    ['Dynamic OOS expectancy', challenger.dynamic_oos && challenger.dynamic_oos.expectancy_usd != null ? '$' + Number(challenger.dynamic_oos.expectancy_usd).toFixed(4) : 'Unavailable', ''],
  ];
  document.getElementById('decision-readiness').innerHTML = cards.map(([label, value, cls]) =>
    `<div class="kpi"><div class="lbl">${label}</div><div class="val ${cls}">${value}</div></div>`
  ).join('');
  const unknownEvidence = tiers.unknown_evidence || {};
  const tierRows = [
    ['Best descriptive / ideal-touch', tiers.descriptive_ideal_touch || {}],
    ['Best execution-supported', tiers.execution_supported || {}],
    ['Best fully qualified', tiers.fully_qualified || {}],
  ];
  document.getElementById('strategy-leader-tiers').innerHTML = tierRows.map(([label, tier]) => {
    const leader = tier.leader || {};
    const unknown = leader.unknown_evidence_count ?? unknownEvidence.episode_count ?? 0;
    return `<tr><td><strong>${label}</strong></td><td>${tier.status || 'UNAVAILABLE'}</td>`
      + `<td>${leader.policy_id || 'NONE'}</td><td>${tier.claim_label || 'UNAVAILABLE'}</td>`
      + `<td>${unknown}</td><td>${(tier.blockers || []).join(', ') || 'none'}</td></tr>`;
  }).join('');
  const gateRows = d.qualification_gate_details || [];
  document.getElementById('qualification-gate-body').innerHTML = gateRows.length
    ? gateRows.map(row => {
        const cls = row.status === 'PASS' ? 'green' : (row.status === 'FAIL' ? 'red' : 'amber');
        const evidence = row.evidence || row.receipt_id || row.source || 'No current evidence receipt';
        return `<tr><td>${row.label || row.gate}</td><td class="${cls}">${row.status}</td><td>${evidence}</td><td>${row.blocker || '—'}</td></tr>`;
      }).join('')
    : '<tr><td colspan="4">UNAVAILABLE — qualification gate projection was not published.</td></tr>';
  document.getElementById('decision-readiness-provenance').textContent =
    `Collection epoch: ${d.epoch_id || 'UNAVAILABLE'} · Policy epoch: ${d.policy_epoch_id || 'UNAVAILABLE'} · `
    + `Evidence policy: ${d.evidence_policy_signature || 'UNAVAILABLE'} · Last analysis: ${d.last_analysis_melbourne || '—'} · `
    + `Collecting deployed identities (not qualified): ${deployedPolicies.map(x => `${x.policy_id} [${x.policy_signature}]`).join(' · ') || 'UNAVAILABLE'} · `
    + `Generation: ${((tiers.currency || {}).generated_at) || d.last_analysis || 'UNAVAILABLE'} · `
    + `Revision: ${((tiers.currency || {}).source_revision) || 'UNAVAILABLE'} / analyzer ${((tiers.currency || {}).analyzer_revision) || 'UNAVAILABLE'} · `
    + `Config: ${((tiers.currency || {}).tile_config_signature) || 'UNAVAILABLE'} · `
    + `UNKNOWN blockers: ${JSON.stringify(unknownEvidence.blocker_counts || {})} · `
    + `Blockers: ${(d.blockers || []).join(', ') || 'none'} · ${d.note || ''}`;
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
  const rCurrent = await fetch('/api/lanes');
  const current = await rCurrent.json();
  const noteCurrent = document.getElementById('lanes-filter-note');
  if (noteCurrent) noteCurrent.textContent = current.lane_filter_note || noteCurrent.textContent;
  const evidenceNote = document.getElementById('lanes-evidence-note');
  const currentAvailable = current.evidence_status === 'CURRENT_GENERATION';
  if (evidenceNote) {
    const evidence = current.evidence || {};
    const blockers = Array.from(new Set([
      ...(evidence.blockers || []),
      ...(((evidence.benchmark || {}).blockers) || []),
    ])).join(', ');
    evidenceNote.textContent = currentAvailable
      ? 'Evidence status: CURRENT GENERATION — revision, source data, epoch and session scope match the atomic report manifest.'
      : current.evidence_status === 'STALE_GENERATION'
        ? `Evidence status: STALE ANALYZER GENERATION — current qualification and performance claims are blocked${blockers ? ` — ${blockers}` : ''}. Historical results are not substituted.`
        : `Evidence status: INSUFFICIENT CURRENT-GENERATION EXECUTION EVIDENCE${blockers ? ` — ${blockers}` : ''}. Historical results are not substituted.`;
  }
  document.getElementById('lane-body').innerHTML = (current.lanes || []).map(row =>
    `<tr><td>${row.lane || row.research_lane || ''}</td><td>${currentAvailable ? (row.status || row.pathway_status || 'COLLECTING') : 'STALE / UNAVAILABLE'}</td>`
    + `<td>${row.approves || 0}</td><td>${currentAvailable ? (row.executed_closes || 0) : '—'}</td>`
    + `<td>${currentAvailable ? `$${fmtUsd(row.pnl || 0)}` : '—'}</td><td>${currentAvailable ? `$${fmtUsd(row.ev || 0)}` : '—'}</td>`
    + `<td>${currentAvailable ? (row.counterfactual_closes || 0) : '—'}</td><td>${currentAvailable ? `$${fmtUsd(row.counterfactual_pnl || 0)}` : '—'}</td></tr>`
  ).join('') || '<tr><td colspan="8">No current-lane evidence yet.</td></tr>';
  return;
}async function loadChase() {
  const r = await fetch('/api/chase' + chaseLaneQuery());
  const d = await r.json();
  const t = d.totals || {};
  const ck = [
    ['Assisted', (t.chase_assisted_fills||0) + '/' + (t.total_fills||0)],
    ['Saved', t.saved_fills_heuristic||0],
    ['TTL expired', t.ttl_expired||0],
    ['Executed terminal', (d.coverage||{}).executed_terminal_outcomes ?? 0],
    ['Shadow terminal', (d.coverage||{}).shadow_terminal_outcomes ?? 0],
  ];
  document.getElementById('chase-kpis').innerHTML = ck.map(([l,v]) =>
    `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  const renderChaseBuckets = rows => {
    const byBucket = new Map((rows || []).map(row => [String(row.bucket || row.threshold), row]));
    return ['0', '1', '2', '3', '4', '5+'].map(bucket => {
      const b = byBucket.get(bucket) || {bucket, trades: 0};
      const hasEvidence = Number(b.trades || 0) > 0;
      return `<tr><td>${bucket}</td><td>${b.trades||0}</td><td>${hasEvidence ? `${b.win_rate_pct??'n/a'}%` : '—'}</td><td>${hasEvidence ? `$${fmtUsd(b.sum_pnl_usd??b.pnl_usd??0)}` : '—'}</td><td>${hasEvidence ? `$${fmtUsd(b.ev_usd??b.ev??0)}` : '—'}</td><td>${hasEvidence ? (b.avg_hold_min??'—') : 'NO TERMINAL EVIDENCE'}</td></tr>`;
    }).join('');
  };
  document.getElementById('chase-body').innerHTML = renderChaseBuckets(d.executed_buckets || d.buckets || []);
  document.getElementById('chase-shadow-body').innerHTML = renderChaseBuckets(d.shadow_buckets || []);
}

async function loadCombos() {
  const r = await fetch('/api/combos');
  const d = await r.json();
  const legacy = d.legacy_executed_combos || {};
  const legacyRows = legacy.rows || [];
  const note = document.getElementById('combos-note');
  if (note) note.textContent = `Separate excluded legacy direction-only cohort: ${legacyRows.length} observed combination(s).`;
  document.getElementById('combos-kpis').innerHTML = [
    ['Known legacy combos', legacyRows.length],
    ['Shown', legacyRows.length],
    ['Dimensions', (legacy.dimensions||[]).join(', ') || 'n/a'],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('combos-body').innerHTML = legacyRows.map(c => {
    const cls = (c.ev_usd ?? 0) >= 2 ? 'green' : '';
    const combo = `${fmtAdxBucket(c.adx_bucket)} + gap ${c.spread_bucket||''} + ${c.entry_mode||''} + ${c.lane||''}`;
    return `<tr class="${cls}"><td>${combo}</td><td>${fmtAdxBucket(c.adx_bucket)}</td><td>${c.spread_bucket||''}</td><td>${c.entry_mode||''}</td><td>${c.lane||''}</td><td>${c.trades||0}</td><td>${c.wr_pct ?? 'n/a'}%</td><td>$${fmtUsd(c.pnl_usd)}</td><td>$${fmtUsd(c.ev_usd)}</td></tr>`;
  }).join('') || '<tr><td colspan="9">No eligible legacy executed-lane combinations exist in the current cohort.</td></tr>';
  const pg = d.policy_grid || {};
  const pe = pg.evidence || {};
  const searchCounts = pg.search_counts || {};
  const policyStats = pg.policy_search_statistics || {};
  const policySplit = pg.policy_episode_split || {};
  const policyRows = pg.policy_rows || pg.rows || [];
  const diagnosticRows = pg.diagnostic_rows || [];
  const selection = pg.descriptive_selection || {};
  const comparison = pg.comparison_cohort || {};
  const pgNote = document.getElementById('policy-grid-note');
  if (pgNote) pgNote.textContent = pg.warning || 'Current-epoch policy grid is waiting for a pinned analyzer report.';
  document.getElementById('policy-grid-kpis').innerHTML = [
    ['Profitable conservative rows', Number(policyStats.profitable_conservative_rows_displayed ?? policyRows.length).toLocaleString()],
    ['Positive ideal-touch hypotheses', Number(policyStats.positive_ideal_touch_hypotheses_displayed ?? diagnosticRows.length).toLocaleString()],
    ['Policy-grid families materialized', Number(selection.families_evaluated ?? 0).toLocaleString()],
    ['Conservative shortlist families', Number(selection.families_represented ?? 0).toLocaleString()],
    ['Diagnostic families represented', new Set(diagnosticRows.map(row => row.policy_family || 'UNKNOWN')).size.toLocaleString()],
    ['Maximum rows per family', Number(selection.per_family_cap ?? 0).toLocaleString()],
    ['Configured family-balanced capacity', (Number(selection.families_evaluated ?? 0) * Number(selection.per_family_cap ?? 0)).toLocaleString()],
    ['Policy specs enumerated', Number(policyStats.policy_specs_enumerated ?? pg.rows_available ?? 0).toLocaleString()],
    ['Policies with terminal OOS fills', Number(policyStats.terminal_oos_policies_tested || 0).toLocaleString()],
    ['Profitable terminal OOS policies', Number(policyStats.profitable_terminal_oos_policies || 0).toLocaleString()],
    ['Entry configurations', Number(searchCounts.entry_cartesian ?? searchCounts.entry_policy_cartesian ?? 0).toLocaleString()],
    ['Theoretical search space', Number(searchCounts.nominal_full_cartesian ?? searchCounts.naive_full_cartesian ?? 0).toLocaleString()],
    ['Independent opportunities (shared episodes)', pe.independent_opportunities ?? pe.independent_episodes ?? searchCounts.independent_episodes ?? 0],
    ['Policy episode split (train / OOS)', `${policySplit.training_episodes ?? 0} / ${policySplit.oos_episodes ?? 0}`],
    ['Cross-family comparison', comparison.status || 'INSUFFICIENT_SHARED_COHORT'],
    ['Same-cohort policies', Number(comparison.eligible_policy_count ?? 0).toLocaleString()],
    ['Qualification', pg.live_policy_change_allowed ? 'QUALIFIED' : 'DESCRIPTIVE ONLY'],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('policy-grid-body').innerHTML = policyRows.map(p => {
    const params = `offset ${p.entry_offset_pct ?? '—'}% · chase ${p.chase_windows ?? p.chase_policy ?? '—'} (${p.chase_window_ages ?? 'age unavailable'}) · move ${p.chase_remaining_gap_step_pct ?? '—'}% of remaining gap · reprice ${p.reprice_interval_sec ?? '—'}s · exit ${p.exit_behavior ?? p.exit_policy ?? '—'} · fill ${p.fill_model ?? '—'} · protection ${p.protection_model ?? '—'}`;
    const fillRate = p.conservative_fill_rate == null ? 'UNAVAILABLE' : `${(Number(p.conservative_fill_rate) * 100).toFixed(2)}%`;
    const executionWinsLosses = p.oos_wins == null ? 'UNAVAILABLE' : `${p.oos_wins} / ${p.oos_losses}`;
    return `<tr><td>${p.rank}</td><td><strong>${p.policy_family||'UNKNOWN'}</strong></td><td>${p.family_rank||'—'}</td><td><strong>${p.policy_id||'—'}</strong><br><small>global rank ${p.global_rank||'—'} · ${params}</small></td>`
      + `<td>${p.oos_episodes||0}</td><td>${p.supported_conservative_episodes ?? 0}</td>`
      + `<td>${p.full_fills ?? 0}</td><td>${p.partial_fills ?? 0}</td><td>${p.no_fills ?? 0}</td><td>${p.unsupported_episodes ?? 0}</td>`
      + `<td>${fillRate}</td><td>${executionWinsLosses}</td>`
      + `<td>${fmtExecutionUsd(p.oos_net_pnl_usd)}</td><td>${fmtExecutionUsd(p.oos_expectancy_usd)}</td><td>${fmtExecutionUsd(p.oos_max_drawdown_usd)}</td>`
      + `<td class="bad">${p.execution_metric_status||p.metric_evidence||p.qualification||'DESCRIPTIVE_ONLY'}</td></tr>`;
  }).join('') || '<tr><td colspan="16">No profitable conservative policy with supported terminal fills exists in the current analyzer generation. Execution PnL, EV, wins/losses, and drawdown are UNAVAILABLE.</td></tr>';
  document.getElementById('diagnostic-policy-grid-body').innerHTML = diagnosticRows.map(p => {
    const params = `offset ${p.entry_offset_pct ?? '—'}% · chase ${p.chase_windows ?? p.chase_policy ?? '—'} · exit ${p.exit_behavior ?? p.exit_policy ?? '—'} · protection ${p.protection_model ?? '—'}`;
    return `<tr><td>${p.rank}</td><td><strong>${p.policy_family||'UNKNOWN'}</strong></td>`
      + `<td><strong>${p.policy_id||'—'}</strong><br><small>${params}</small></td>`
      + `<td>${p.oos_episodes||0}</td><td>${p.diagnostic_touch_episodes||0}</td><td>${p.diagnostic_no_touch_episodes||0}</td>`
      + `<td>${p.diagnostic_replay_wins||0} / ${p.diagnostic_replay_losses||0}</td>`
      + `<td>${fmtExecutionUsd(p.diagnostic_replay_net_pnl_usd)}</td><td>${fmtExecutionUsd(p.diagnostic_replay_max_drawdown_usd)}</td>`
      + `<td class="bad">IDEAL_TOUCH_DIAGNOSTIC_ONLY · NOT EXECUTION VERIFIED · NOT QUALIFICATION ELIGIBLE</td></tr>`;
  }).join('') || '<tr><td colspan="10">No positive ideal-touch diagnostic policy exists in the current rolling generation.</td></tr>';
}

async function loadSpreadPerf() {
  const r = await fetch('/api/spread-performance');
  const d = await r.json();
  const note = document.getElementById('spread-perf-note');
  if (note) note.textContent = d.filter_note || 'P&L by directional spread bucket.';
  const totalTrades = (d.buckets||[]).reduce((s,b)=>s+(b.trades||0),0);
  document.getElementById('spread-perf-kpis').innerHTML = [
    ['Buckets', (d.buckets||[]).length],
    ['Combos aggregated', d.total_combos ?? 0],
    ['Total trades', totalTrades],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('spread-perf-body').innerHTML = (d.buckets||[]).map(b => {
    const cls = (b.pnl_usd ?? 0) >= 0 ? 'green' : 'red';
    return `<tr class="${cls}"><td>${b.spread_bucket||''}</td><td>${b.trades||0}</td><td>${b.wr_pct ?? 'n/a'}%</td><td>$${fmtUsd(b.pnl_usd)}</td><td>$${fmtUsd(b.ev_usd)}</td></tr>`;
  }).join('') || '<tr><td colspan="5">No legacy spread-performance evidence exists in the current cohort.</td></tr>';
}

async function loadChaseThreshold() {
  const r = await fetch('/api/chase-threshold' + chaseLaneQuery());
  const d = await r.json();
  const note = document.getElementById('chase-threshold-note');
  if (note) note.textContent = [d.warning, d.question, d.evidence_contract].filter(Boolean).join(' · ') || 'Executed and shadow chase evidence.';
  const coverage = d.coverage || {};
  document.getElementById('chase-threshold-kpis').innerHTML = [
    ['Executed terminal outcomes', coverage.executed_terminal_outcomes ?? 0],
    ['Shadow terminal outcomes', coverage.shadow_terminal_outcomes ?? 0],
    ['Generic shadows', coverage.generic_shadow_counterfactuals ?? 0],
    ['Tile LAB shadows', coverage.tile_lab_shadow_outcomes ?? 0],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  const completeThresholdBuckets = rows => {
    const byBucket = new Map((rows || []).map(row => [String(row.threshold), row]));
    return ['0', '1', '2', '3', '4', '5+'].map(bucket => byBucket.get(bucket) || ({threshold: bucket, trades: 0, evidence_available: false}));
  };
  const renderThresholdRows = rows => completeThresholdBuckets(rows).map(t => {
    const hasEvidence = Number(t.trades || 0) > 0;
    const wr = t.win_rate_pct ?? t.wr_pct ?? t.wr ?? 'n/a';
    const ev = t.ev_usd ?? t.ev ?? 'n/a';
    const pnl = t.sum_pnl_usd ?? t.pnl_usd ?? t.pnl ?? 0;
    const cls = (Number(ev) >= 0.8) ? 'green' : '';
    return `<tr class="${cls}"><td>${t.threshold||''}</td><td>${t.trades||0}</td><td>${hasEvidence ? `${wr}%` : '—'}</td><td>${hasEvidence ? `$${fmtUsd(pnl)}` : '—'}</td><td>${hasEvidence ? `$${fmtUsd(ev)}` : '—'}</td><td>${hasEvidence ? (t.avg_hold_min??'—') : 'NO TERMINAL EVIDENCE'}</td></tr>`;
  }).join('');
  document.getElementById('chase-threshold-body').innerHTML = renderThresholdRows(d.executed_thresholds || d.thresholds || []);
  document.getElementById('chase-threshold-shadow-body').innerHTML = renderThresholdRows(d.shadow_thresholds || []);
}

async function loadChasePolicyLab() {
  const [labResponse, proofResponse] = await Promise.all([
    fetch('/api/chase-policy-lab'),
    fetch('/api/missed-opportunity-proof'),
  ]);
  const lab = await labResponse.json();
  const proof = await proofResponse.json();
  const rows = lab.ranked_schedules || [];
  const top = lab.top_schedule || null;
  const note = document.getElementById('chase-policy-lab-note');
  if (note) note.textContent = lab.empty_reason || lab.full_artifact_note || 'All schedule permutations are retained in the downloadable JSON artifact.';
  document.getElementById('chase-policy-leader-label').textContent = top
    ? `${lab.leader_label || 'INSUFFICIENT_EVIDENCE'} — ${top.policy_id || 'UNKNOWN'}`
    : 'INSUFFICIENT EVIDENCE — NO SIGNED SCHEDULE ROWS';
  document.getElementById('chase-policy-top').textContent = top
    ? `Checkpoints: ${(top.checkpoint_seconds||[]).map(x=>x+'s').join(' → ')} · Expiry: ${top.terminal_expiry_sec ?? '—'}s\nExecuted evidence: ${(top.executed||{}).status || 'UNAVAILABLE'}\nQualification: ${top.qualification_status || 'NOT ELIGIBLE'}`
    : (lab.empty_reason || 'SOURCE_EMPTY_OR_UNAVAILABLE');
  document.getElementById('chase-policy-kpis').innerHTML = [
    ['Schedules retained', lab.all_schedule_count ?? rows.length],
    ['Evidence world', 'SHADOW ONLY'],
    ['Leader label', lab.leader_label || 'INSUFFICIENT_EVIDENCE'],
    ['Qualification', 'NOT ELIGIBLE'],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('chase-policy-body').innerHTML = rows.map((row, index) => {
    const shadow = row.shadow || {};
    const confidence = row.confidence || {};
    const fillRate = row.fill_rate_pct == null ? 'UNAVAILABLE' : `${Number(row.fill_rate_pct).toFixed(2)}%`;
    return `<tr><td>${index+1}</td><td><strong>${row.policy_id||'UNKNOWN'}</strong><br><small>${(row.checkpoint_seconds||[]).join('/')}s · expire ${row.terminal_expiry_sec ?? '—'}s</small></td>`
      + `<td>${row.independent_opportunities||0}</td><td>${row.supported||0}</td><td>${row.full_fills||0} / ${row.partial_fills||0} / ${row.no_fills||0} / ${row.unsupported||0}</td>`
      + `<td>${fillRate}</td><td>shadow net ${shadow.net_pnl_usd == null ? 'UNAVAILABLE USD' : '$'+fmtUsd(shadow.net_pnl_usd)} / EV ${shadow.ev_usd == null ? 'UNAVAILABLE USD' : '$'+fmtUsd(shadow.ev_usd)}<br><small>return ${shadow.net_return_pct ?? 'UNAVAILABLE'}% / ${shadow.ev_return_pct ?? 'UNAVAILABLE'}% · executed ${(row.executed||{}).pnl_usd ?? 'UNAVAILABLE'} / ${(row.executed||{}).ev_usd ?? 'UNAVAILABLE'}</small></td>`
      + `<td>${shadow.max_drawdown_usd == null ? 'UNAVAILABLE USD' : '$'+fmtUsd(shadow.max_drawdown_usd)} / ${shadow.tail_loss_usd == null ? 'UNAVAILABLE USD' : '$'+fmtUsd(shadow.tail_loss_usd)}<br><small>${shadow.max_drawdown_pct ?? 'UNAVAILABLE'}% / ${shadow.tail_loss_pct ?? 'UNAVAILABLE'}%</small></td><td>${shadow.avg_mfe_pct ?? 'UNAVAILABLE'}% / ${shadow.avg_mae_pct ?? 'UNAVAILABLE'}%</td>`
      + `<td>${row.coverage_pct ?? 'UNAVAILABLE'}% / ${confidence.label||'INSUFFICIENT'} / ${confidence.fill_rate_wilson_lower_95_pct ?? 'UNAVAILABLE'}%</td>`
      + `<td>${(row.regimes||[]).join(', ')||'UNAVAILABLE'}</td><td class="bad">${row.evidence_status||'INSUFFICIENT_EVIDENCE'}<br>${row.qualification_status||'NOT ELIGIBLE'}</td></tr>`;
  }).join('') || '<tr><td colspan="12">No signed compressed shadow schedule evidence is available in this generation.</td></tr>';
  const proofs = proof.proofs || [];
  const counts = proof.classification_counts || {};
  document.getElementById('missed-proof-kpis').innerHTML = [
    ['Proof rows', proof.proof_count ?? proofs.length],
    ['Proven missed profit', counts.PROVEN_MISSED_PROFIT || 0],
    ['Proven avoided loss', counts.PROVEN_AVOIDED_LOSS || 0],
    ['Ambiguous', counts.AMBIGUOUS || 0],
    ['Insufficient evidence', counts.INSUFFICIENT_EVIDENCE || 0],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('missed-proof-body').innerHTML = proofs.slice(0, 100).map(row => {
    const coverage = row.coverage || {};
    return `<tr><td><strong>${row.classification}</strong></td><td>${row.episode_id||'—'}<br><small>${row.policy_id||'—'}</small></td><td>${row.direction||'—'}</td>`
      + `<td>${row.conservative_touch ? 'TOUCHED' : 'NO TOUCH'} / net ${row.net_terminal_return_pct == null ? 'UNAVAILABLE' : row.net_terminal_return_pct+'%'} / USD ${row.net_pnl_usd == null ? 'UNAVAILABLE' : '$'+fmtUsd(row.net_pnl_usd)}</td>`
      + `<td>${row.mfe_pct ?? 'UNAVAILABLE'}% / ${row.mae_pct ?? 'UNAVAILABLE'}%</td><td>${coverage.status||'INSUFFICIENT'} (stages ${coverage.stage_ratio ?? 0}; tape ${coverage.tape_status||'UNAVAILABLE'}; missing seconds ${coverage.missing_seconds ?? 'UNAVAILABLE'})</td>`
      + `<td>${row.regime||'UNAVAILABLE'} / ${row.adx ?? 'UNAVAILABLE'}</td><td>${(row.contraindications||[]).join('; ')||'none recorded'}</td></tr>`;
  }).join('') || `<tr><td colspan="8">${proof.empty_reason || 'No proof rows exist.'}</td></tr>`;
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

async function loadExitCombos() {
  const r = await fetch('/api/exit-combos');
  const d = await r.json();
  const money = value => value == null ? 'n/a' : '$' + fmtUsd(value);
  document.getElementById('exit-combos-kpis').innerHTML = [
    ['Total combos', d.total_combos ?? 0],
    ['Left on table', money(d.overall_left_on_table_usd)],
    ['Benchmark', d.benchmark_lane || 'n/a'],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  const executed = ((d.evidence_classes||{}).executed_paper||{});
  const shadow = ((d.evidence_classes||{}).shadow_lab||{});
  const conservative = ((d.evidence_classes||{}).conservative_bbo_depth||{});
  const idealTouch = ((d.evidence_classes||{}).ideal_touch_diagnostic||{});
  const renderFamilies = rows => (rows||[]).map(row =>
    `<tr><td>${row.exit_family||'UNKNOWN'}</td><td>${row.terminal_rows??0}</td><td>${row.independent_episodes??0}</td><td>${row.wins??0} / ${row.losses??0}</td><td>${money(row.net_pnl_usd)}</td><td>${money(row.ev_per_independent_episode_usd)}</td><td>${money(row.max_drawdown_usd)}</td><td>${row.missing_identity_rows??0} / ${row.missing_pnl_rows??0} / ${row.missing_cost_rows??0} / ${row.missing_slippage_rows??0}</td><td>${row.evidence_status||'DESCRIPTIVE'} · NOT QUALIFIED</td></tr>`
  ).join('') || '<tr><td colspan="9">No terminal evidence for this evidence world.</td></tr>';
  const renderStops = rows => (rows||[]).map(row =>
    `<tr><td>${row.stop_type||'UNSPECIFIED'}</td><td>${row.stop_distance_atr??'n/a'}</td><td>${row.hard_stop_margin_pct??'n/a'}</td><td>${row.exit_reason||'UNKNOWN'}</td><td>${row.chase_bucket??'n/a'}</td><td>${row.terminal_rows??0}</td><td>${row.independent_episodes??0}</td><td>${row.wins??0} / ${row.losses??0}</td><td>${money(row.net_pnl_usd)}</td><td>${row.avg_mae_margin_pct??'n/a'}</td><td>${row.avg_stop_slippage??'n/a'}</td><td>${row.missing_identity_rows??0} / ${row.missing_pnl_rows??0} / ${row.missing_mae_rows??0} / ${row.missing_stop_slippage_rows??0}</td><td>${row.evidence_status||'DESCRIPTIVE'} · NOT QUALIFIED</td></tr>`
  ).join('') || '<tr><td colspan="13">No terminal stop evidence for this evidence world.</td></tr>';
  document.getElementById('exit-family-scorecard-body').innerHTML = renderFamilies(executed.exit_family_scorecard);
  document.getElementById('exit-family-scorecard-shadow-body').innerHTML = renderFamilies(shadow.exit_family_scorecard);
  document.getElementById('stop-effectiveness-body').innerHTML = renderStops(executed.stop_effectiveness_matrix);
  document.getElementById('stop-effectiveness-shadow-body').innerHTML = renderStops(shadow.stop_effectiveness_matrix);
  const causalWorlds = [
    ['PAPER', executed],
    ['SHADOW/LAB', shadow],
    ['CONSERVATIVE BBO/DEPTH', conservative],
    ['IDEAL TOUCH DIAGNOSTIC', idealTouch],
  ];
  const renderCausalView = key => {
    const rows = [];
    const reasons = [];
    causalWorlds.forEach(([label, world]) => {
      const view = ((world.causal_combination_views||{})[key]||{});
      (view.rows||[]).forEach(row => rows.push(
        `<tr><td>${label}</td><td>${row.combination||'—'}</td><td>${row.trades??0}</td><td>${row.wr_pct??'n/a'}%</td><td>${money(row.pnl_usd)}</td><td>${money(row.ev_usd)}</td><td>${row.identity_status||row.evidence_status||'DESCRIPTIVE'} · NOT QUALIFIED</td></tr>`
      ));
      if (!(view.rows||[]).length) {
        const sourceRows = view.source_terminal_rows ?? 0;
        const eligibleRows = view.eligible_rows ?? 0;
        const coverage = view.coverage_pct ?? 0;
        const missing = (view.missing_dimensions||[]).join(', ')
          || (sourceRows === 0 ? 'not applicable (source empty)' : 'none recorded');
        reasons.push(`${label}: ${view.empty_reason||world.empty_reason||'NO TERMINAL EVIDENCE'} · source ${sourceRows}, eligible ${eligibleRows}, coverage ${coverage}% · missing ${missing}`);
      }
    });
    return rows.join('') || `<tr><td colspan="7">${reasons.join(' · ')}</td></tr>`;
  };
  document.getElementById('exit-causal-policy-body').innerHTML = renderCausalView('exit_policy');
  document.getElementById('exit-causal-risk-body').innerHTML = renderCausalView('risk_and_chase');
  document.getElementById('exit-causal-market-body').innerHTML = renderCausalView('market_context');
  document.getElementById('exit-causal-entry-body').innerHTML = renderCausalView('entry_execution');
  document.getElementById('exit-causal-microstructure-body').innerHTML = renderCausalView('market_microstructure');
  document.getElementById('exit-causal-profit-path-body').innerHTML = renderCausalView('profit_path');
  document.getElementById('exit-causal-cost-body').innerHTML = renderCausalView('cost_and_fill');
  document.getElementById('exit-causal-direction-quality-body').innerHTML = renderCausalView('direction_quality');
  document.getElementById('exit-causal-sr-geometry-body').innerHTML = renderCausalView('sr_geometry');
  document.getElementById('exit-causal-execution-quality-body').innerHTML = renderCausalView('execution_quality');
  document.getElementById('exit-causal-partial-profit-body').innerHTML = renderCausalView('partial_profit_path');
  document.getElementById('exit-causal-chase-detail-body').innerHTML = renderCausalView('chase_detail');
  document.getElementById('exit-causal-excursion-timing-body').innerHTML = renderCausalView('excursion_timing');
  document.getElementById('exit-causal-regime-transition-body').innerHTML = renderCausalView('regime_transition');
  document.getElementById('exit-causal-fill-revalidation-body').innerHTML = renderCausalView('fill_revalidation');
  document.getElementById('exit-causal-terminal-order-body').innerHTML = renderCausalView('terminal_order_outcome');
  document.getElementById('exit-causal-path-sequence-body').innerHTML = renderCausalView('path_sequence');
  document.getElementById('exit-causal-protection-body').innerHTML = renderCausalView('protection_activation');
  document.getElementById('exit-causal-stop-quality-body').innerHTML = renderCausalView('stop_execution_quality');
  document.getElementById('exit-causal-liquidity-body').innerHTML = renderCausalView('liquidity_at_exit');
  document.getElementById('exit-causal-cost-drag-body').innerHTML = renderCausalView('cost_drag');
  document.getElementById('exit-combos-body').innerHTML = (d.top||[]).map(c =>
    `<tr><td>${c.combo||''}</td><td>${c.exit_reason||''}</td><td>${c.ai_bucket||''}</td><td>${c.spread_bucket||''}</td><td>${c.peak_mfe_bucket||''}</td><td>${c.time_in_trade_bucket||''}</td><td>${c.sample_status||'DESCRIPTIVE'}</td><td>${c.lane||''}</td><td>${c.trades||0}</td><td>${c.wr_pct??'n/a'}%</td><td>$${fmtUsd(c.pnl_usd)}</td><td>$${fmtUsd(c.ev_usd)}</td><td class="red">$${fmtUsd(c.left_on_table_usd)}</td></tr>`).join('') || '<tr><td colspan="13">Analyzer completed: no current-epoch terminal exit paths exist yet, so exit-combo EV is unavailable.</td></tr>';
  document.getElementById('exit-leak-body').innerHTML = (d.worst_leakage||[]).map(c =>
    `<tr><td>${c.combo||''}</td><td>${c.exit_reason||''}</td><td>${c.trades||0}</td><td class="red">$${fmtUsd(c.left_on_table_usd)}</td><td>$${fmtUsd(c.avg_left_usd)}</td><td>$${fmtUsd(c.ev_usd)}</td></tr>`).join('') || '<tr><td colspan="6">No current-epoch terminal exits exist yet; peak-to-close leakage is unavailable.</td></tr>';
  document.getElementById('exit-shadow-combos-body').innerHTML = (shadow.top||[]).map(c =>
    `<tr><td>${c.combo||''}</td><td>${c.exit_reason||''}</td><td>${c.trades||0}</td><td>${c.sample_status||'DESCRIPTIVE'}</td><td>${money(c.pnl_usd)}</td><td>${money(c.ev_usd)}</td></tr>`).join('') || `<tr><td colspan="6">${shadow.empty_reason||'No explicit shadow/lab terminal exit evidence in this epoch.'}</td></tr>`;
  const renderReplayCombos = (world, fallback) => (world.top||[]).map(c =>
    `<tr><td>${c.combo||''}</td><td>${c.exit_reason||''}</td><td>${c.trades||0}</td><td>${c.sample_status||'DESCRIPTIVE'}</td><td>${money(c.pnl_usd)}</td><td>${money(c.ev_usd)}</td></tr>`).join('') || `<tr><td colspan="6">${world.empty_reason||fallback}</td></tr>`;
  document.getElementById('exit-conservative-combos-body').innerHTML = renderReplayCombos(conservative, 'No conservative BBO/depth terminal exit evidence in this epoch.');
  document.getElementById('exit-ideal-touch-combos-body').innerHTML = renderReplayCombos(idealTouch, 'No ideal-touch diagnostic terminal exit evidence in this epoch.');
}

async function loadExitReasonLeak() {
  const r = await fetch('/api/exit-reason-leak');
  const d = await r.json();
  const money = value => value == null ? 'n/a' : '$' + fmtUsd(value);
  document.getElementById('exit-reason-kpis').innerHTML = [
    ['Hindsight gap', money(d.overall_left_usd)],
    ['Booked', money(d.overall_booked_usd)],
    ['Peak', money(d.overall_peak_usd)],
    ['Exit reasons', (d.reasons||[]).length],
    ['Replay reviews', (d.recommendations||[]).length],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  document.getElementById('exit-reason-body').innerHTML = (d.reasons||[]).map(r =>
    `<tr><td>${r.exit_reason||''}</td><td>${r.trades||0}</td><td class="red">$${fmtUsd(r.left_on_table_usd)}</td><td>$${fmtUsd(r.avg_left_usd)}</td><td>${r.avg_mfe_margin_pct??'n/a'}%</td><td>${r.avg_realized_margin_pct??'n/a'}%</td><td class="red">${r.avg_leakage_margin_pct??'n/a'}%</td><td>${r.capture_ratio_pct??'n/a'}%</td></tr>`
  ).join('') || '<tr><td colspan="8">Analyzer completed: no current-epoch terminal exits exist yet, so exit-reason leakage is unavailable.</td></tr>';
  const shadow = ((d.evidence_classes||{}).shadow_lab||{});
  const conservative = ((d.evidence_classes||{}).conservative_bbo_depth||{});
  const idealTouch = ((d.evidence_classes||{}).ideal_touch_diagnostic||{});
  const renderReasonWorld = (world, fallback) => (world.reasons||[]).map(r =>
    `<tr><td>${r.exit_reason||''}</td><td>${r.trades||0}</td><td>${r.sample_status||'DESCRIPTIVE'}</td><td>${money(r.left_on_table_usd)}</td><td>${money(r.booked_profit_usd)}</td><td>${money(r.peak_profit_usd)}</td></tr>`
  ).join('') || `<tr><td colspan="6">${world.empty_reason||fallback}</td></tr>`;
  document.getElementById('exit-reason-shadow-body').innerHTML = renderReasonWorld(shadow, 'No explicit shadow/lab terminal leakage evidence in this epoch.');
  document.getElementById('exit-reason-conservative-body').innerHTML = renderReasonWorld(conservative, 'No conservative BBO/depth terminal leakage evidence in this epoch.');
  document.getElementById('exit-reason-ideal-touch-body').innerHTML = renderReasonWorld(idealTouch, 'No ideal-touch diagnostic terminal leakage evidence in this epoch.');
  const recEl = document.getElementById('exit-reason-recs');
  if (recEl) {
    recEl.innerHTML = (d.recommendations||[]).map(rec =>
      `<li><b>${rec.exit_reason}</b> (${rec.priority})<br/><em>Observation:</em> ${rec.finding||rec.action}<br/><em>QA rule:</em> ${rec.recommendation||rec.action}</li>`
    ).join('') || '<li>No current-epoch terminal exits exist yet; validation action items are unavailable.</li>';
  }
}

async function loadLadderSim() {
  const r = await fetch('/api/ladder-sim');
  const d = await r.json();
  const disc = document.getElementById('ladder-sim-disclaimer');
  const noSim = !((d.profiles||[]).some(p => (p.trades_simulated||0) > 0));
  const noReplayEvidence = ['NO_REPLAYS','NO_ELIGIBLE_REPLAYS'].includes(d.data_status);
  const overlapZero = d.data_status === 'NO_EXECUTED_REPLAY_OVERLAP' || ((d.replays_matched_executed ?? 0) === 0 && (d.actual_trades ?? 0) > 0);
  const noComparableProfiles = noReplayEvidence || overlapZero || noSim;
  if (disc) {
    disc.textContent = d.empty_reason || d.disclaimer || '';
    disc.style.display = (noComparableProfiles || d.disclaimer) ? '' : 'none';
  }
  document.getElementById('ladder-sim-kpis').innerHTML = [
    ['Full-session actual', '$' + fmtUsd(d.actual_realized_usd)],
    ['Full-session trades', d.actual_trades ?? 0],
    ['Matched-cohort actual', '$' + fmtUsd(d.matched_actual_realized_usd)],
    ['Matched replays', d.replays_matched_executed ?? 0],
    ['Replays on disk', d.raw_replays_available ?? d.replays_available ?? 0],
    ['Best profile', noComparableProfiles ? 'n/a' : (d.best_profile_id || 'n/a')],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  if (noComparableProfiles) {
    const reason = d.empty_reason || 'No profile has a non-zero matched replay sample.';
    document.getElementById('ladder-sim-body').innerHTML =
      `<tr class="amber"><td colspan="8">No comparable ladder profiles — ${reason} Profiles with zero simulated trades are not ranked or displayed as results.</td></tr>`;
    return;
  }
  document.getElementById('ladder-sim-body').innerHTML = (d.profiles||[]).map(p => {
    const delta = p.delta_vs_matched_actual_usd ?? p.delta_vs_actual_usd;
    const cls = p.unrealistic_vs_actual ? 'red' : (delta != null && delta > 50 ? 'amber' : '');
    const unreal = p.unrealistic_vs_actual ? ' UNREALISTIC' : '';
    return `<tr class="${cls}"><td>${p.profile_id||''}${unreal}</td><td>${(p.ladder||[]).map(r=>r.join('\u2192')).join(' · ')||p.label||''}</td><td>${p.trades_simulated||0}</td><td>$${fmtUsd(p.sum_pnl_usd)}</td><td>$${fmtUsd(p.avg_pnl_usd)}</td><td>${p.wr_pct??'n/a'}%</td><td>${p.ladder_exit_pct??'n/a'}%</td><td>${delta!=null?'$'+fmtUsd(delta):'n/a'}</td></tr>`;
  }).join('') || '<tr><td colspan="8">No ladder sim data — need executed-trade tick replays.</td></tr>';
}

async function loadPathwayAudit() {
  const r = await fetch('/api/pathway-audit');
  const d = await r.json();
  const ti = d.tile_independence || {};
  const ai = d.ai_scan_independence || {};
  const air = d.ai_scan_role || {};
  const lm = d.lane_memory || {};
  const lmv = d.lane_memory_violation || {};
  const rpi = d.runtime_pathway_integrity || {};
  const ev = d.exit_reports_validation || {};
  const vs = d.version_sync || {};
  const bas = d.bot_analyzer_sync || {};
  const currentSync = d.current_sync || {};
  const ais = d.analyzer_integrity || {};
  const receiptStatus = d.receipt_status || {};
  const receiptLabel = (name, payload) => {
    const meta = receiptStatus[name] || {};
    if (meta.status === 'STALE_CONTRACT_RECEIPT') return 'STALE — BODY HIDDEN';
    if (meta.status === 'NOT_PUBLISHED') return 'NOT PUBLISHED';
    return payload.verdict || meta.status || 'n/a';
  };
  const laneMemoryName = lmv.verdict ? 'lane_memory_violation.json' : 'lane_memory_validation.json';
  const laneMemoryPayload = lmv.verdict ? lmv : lm;
  const isStale = name => (receiptStatus[name] || {}).status === 'STALE_CONTRACT_RECEIPT';
  document.getElementById('audit-kpis').innerHTML = [
    ['Dashboard', d.dashboard_version || 'n/a'],
    ['Bot expected', d.expected_bot_version || 'n/a'],
    ['Analyzer expected', d.expected_analyzer_sync_id || 'n/a'],
    ['Exchange', d.expected_exchange || 'bitfinex'],
    ['Current analyzer↔registry', currentSync.status || 'CURRENT STATUS UNAVAILABLE'],
    ['Analyzer source revision', currentSync.analyzer_source_revision || currentSync.generation_revision || 'n/a'],
    ['Fly/mirror source revision', currentSync.mirror_source_revision || 'n/a'],
    ['Current epoch', currentSync.epoch_id || 'n/a'],
    ['Analyzer integrity', ais.report_status || (ais.valid === true ? 'VALID' : 'n/a')],
    ['Tile independence', receiptLabel('tile_independence_report.json', ti)],
    ['AI scan path', receiptLabel('ai_scan_independence_report.json', ai)],
    ['AI scan role', receiptLabel('ai_scan_role_validation.json', air)],
    ['Runtime integrity', receiptLabel('runtime_pathway_integrity.json', rpi)],
    ['Exit reports', receiptLabel('exit_reports_validation.json', ev)],
    ['Lane memory', receiptLabel(laneMemoryName, laneMemoryPayload)],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  const passCell = (ok, stale=false) => stale
    ? '<span class="amber">STALE RECEIPT</span>'
    : (ok ? '<span class="green">PASS</span>' : '<span class="red">FAIL</span>');
  document.getElementById('audit-tile-body').innerHTML = (ti.tests||[]).map(t =>
    `<tr><td>${t.test||''}</td><td>${passCell(t.passed, isStale('tile_independence_report.json'))}</td><td>${t.detail||''}</td></tr>`
  ).join('') || '<tr><td colspan="3">No current tile-independence receipt is published. Registry contract tests are required before deployment.</td></tr>';
  document.getElementById('audit-aiscan-body').innerHTML = (ai.tests||[]).map(t =>
    `<tr><td>${t.test||''}</td><td>${passCell(t.passed, isStale('ai_scan_independence_report.json'))}</td><td>${t.detail||''}</td></tr>`
  ).join('') || '<tr><td colspan="3">No current AI-scan independence receipt is published.</td></tr>';
  document.getElementById('audit-aiscan-role-body').innerHTML = (air.checks||[]).map(c =>
    `<tr><td>${c.check||''}</td><td>${passCell(c.passed, isStale('ai_scan_role_validation.json'))}</td><td>${c.detail||''}</td></tr>`
  ).join('') || '<tr><td colspan="3">No current AI-scan role receipt is published.</td></tr>';
  const runtimeRows = (rpi.critical_issues||[]).map(i => `<tr><td>${i}</td><td class="red">CRITICAL</td></tr>`)
    .concat((rpi.issues||[]).filter(i => !(rpi.critical_issues||[]).includes(i)).map(i => `<tr><td>${i}</td><td class="amber">WARN</td></tr>`));
  document.getElementById('audit-runtime-body').innerHTML = runtimeRows.join('')
    || `<tr><td>${rpi.verdict ? 'Recorded check: '+rpi.verdict : 'Runtime pathway receipt is not published; use authenticated /ready and the stability supervisor for current runtime truth.'}</td><td>—</td></tr>`;
}

async function loadHorizon() {
  const r = await fetch('/api/horizon');
  const d = await r.json();
  const note = document.getElementById('horizon-note');
  if (note) {
    const reason = d.coverage_reason ? ` ${d.coverage_reason}` : '';
    note.textContent = d.conclusions_allowed
      ? (d.note || 'Coverage sufficient for recovery conclusions.')
      : `⚠ Coverage ${d.max_horizon_coverage_pct ?? 0}% — recovery rates hidden until ≥${d.min_coverage_pct_for_conclusions ?? 80}%. ${d.note || ''}${reason}`;
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
    `<tr><td>${(t.trade_id||'').slice(0,12)}</td><td>${t.lane||''}</td><td>${t.exit_reason||''}</td><td>${t.mfe_margin_pct??'n/a'}%</td><td>${t.realized_margin_pct??'n/a'}%</td><td class="red">${t.leakage_margin_pct??'n/a'}%</td><td>$${fmtUsd(t.realized_usd)}</td><td>$${fmtUsd(t.peak_profit_usd)}</td><td class="red">$${fmtUsd(t.left_on_table_usd)}</td></tr>`).join('')
    || '<tr><td colspan="9">No legacy hindsight exit-leakage evidence exists in the current cohort.</td></tr>';
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

async function loadResearchDesign() {
  const r = await fetch('/api/research-design');
  const d = await r.json();
  const escape = value => String(value == null ? '' : value)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
  const banner = document.getElementById('research-design-banner');
  const current = d.status === 'CURRENT';
  banner.style.background = current ? '#153526' : '#3d2a1f';
  banner.style.borderColor = current ? '#3dd68c' : '#d29922';
  banner.style.color = current ? '#9df0c8' : '#f8e3a1';
  banner.textContent = current
    ? 'Current declared generation · qualification disabled · profitability not calculated'
    : `${d.status || 'UNAVAILABLE'} · ${d.reason || 'current evaluator coverage unavailable'} · qualification disabled`;
  const coverage = d.regime_feature_coverage || {};
  document.getElementById('research-design-kpis').innerHTML = cards([
    ['Signed baselines', (d.entry_baselines || []).length],
    ['Same-opportunity replay N', (d.entry_baseline_replay || {}).same_opportunity_count ?? 0],
    ['Evaluator rows', coverage.row_count ?? 0],
    ['Qualification', d.qualification_allowed ? 'ALLOWED' : 'DISABLED'],
    ['Profitability', d.profitability_calculated ? 'CALCULATED' : 'NOT CALCULATED'],
  ]);
  document.getElementById('research-baseline-body').innerHTML = (d.entry_baselines || []).map(row =>
    `<tr><td>${escape(row.baseline_id)}<br><small>${escape(row.policy_signature)}</small></td>` +
    `<td>${escape(row.entry_type)}</td><td>${escape(row.timing)}</td>` +
    `<td>${escape((row.required_evidence || []).join(', '))}</td>` +
    `<td>RESEARCH ONLY · relay ${row.relay_eligible ? 'eligible' : 'disabled'} · places order ${row.places_order ? 'yes' : 'no'}</td>` +
    `<td>${escape(row.missing_evidence_outcome || 'UNKNOWN')}<br><small>` +
    `N ${row.replay_summary?.opportunities ?? 0} · full ${row.replay_summary?.full_fills ?? 0} · partial ${row.replay_summary?.partial_fills ?? 0} · no-fill ${row.replay_summary?.no_fills ?? 0} · UNKNOWN ${row.replay_summary?.unknown ?? 0}</small></td></tr>`
  ).join('') || '<tr><td colspan="6">No signed baseline definitions are available.</td></tr>';
  document.getElementById('research-regime-coverage-body').innerHTML = (coverage.dimensions || []).map(row =>
    `<tr><td>${escape(row.name)}</td><td>${row.observed_rows ?? 0}</td><td>${row.unknown_rows ?? 0}</td><td>${escape(row.status || 'UNKNOWN')}</td></tr>`
  ).join('') || '<tr><td colspan="4">Current generation has no published evaluator feature coverage; every regime dimension remains UNKNOWN.</td></tr>';
}

async function loadEvidenceCoverage() {
  const r = await fetch('/api/evidence-coverage');
  const d = await r.json();
  const banner = document.getElementById('evidence-coverage-banner');
  const unavailable = !d.available;
  const stale = d.status === 'STALE_CURRENT_GENERATION';
  banner.style.background = unavailable ? '#3d1f1f' : stale ? '#3d2a1f' : '#153526';
  banner.style.borderColor = unavailable ? '#f85149' : stale ? '#d29922' : '#3dd68c';
  banner.style.color = unavailable ? '#ffb4b4' : stale ? '#f8e3a1' : '#9df0c8';
  const freshnessReasons = ((d.generation_freshness || {}).reasons || []).join(' · ');
  banner.textContent = unavailable
    ? `UNAVAILABLE · ${d.reason || d.status || 'current generation report is invalid or missing'}`
    : `${d.status} · checksum ${d.checksum_valid ? 'VERIFIED' : 'INVALID'} · generation ${d.generation_id || 'UNKNOWN'} · revision ${d.generation_revision || 'UNKNOWN'}${freshnessReasons ? ' · ' + freshnessReasons : ''}`;
  const value = v => v === undefined || v === null ? 'UNKNOWN' : String(v);
  const html = v => value(v).replace(/[&<>"']/g, c => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
  })[c]);
  const cards = rows => rows.map(([label, val]) =>
    `<div class="kpi"><div class="lbl">${html(label)}</div><div class="val">${html(val)}</div></div>`
  ).join('');
  const counts = d.authoritative_source_record_counts || {};
  document.getElementById('evidence-coverage-source-kpis').innerHTML = cards([
    ['Opportunities', counts.opportunities], ['Decisions', counts.decisions],
    ['Order intents', counts.order_intents], ['Executions', counts.executions],
    ['Lifecycles', counts.lifecycles], ['Market segments', counts.market_segments],
  ]);
  const episodes = d.episode_coverage || {};
  const outcomes = d.terminal_outcome_counts || {};
  document.getElementById('evidence-coverage-outcome-kpis').innerHTML = cards([
    ['Exact episodes', episodes.exact], ['Reconstructed episodes', episodes.reconstructed],
    ['Unknown episodes', episodes.unknown], ['Complete schedules', d.complete_schedules],
    ['FULL_FILL', outcomes.FULL_FILL], ['PARTIAL_FILL', outcomes.PARTIAL_FILL],
    ['NO_FILL', outcomes.NO_FILL], ['UNKNOWN outcomes', outcomes.UNKNOWN],
  ]);
  document.getElementById('evidence-coverage-reasons').innerHTML =
    (d.top_missing_evidence_reasons || []).map(row =>
      `<tr><td>${html(row.reason)}</td><td>${html(row.count)}</td></tr>`
    ).join('') || '<tr><td colspan="2">No declared, checksum-valid missing-reason report is available.</td></tr>';
  const archive = d.archive_recovery_retention || {};
  document.getElementById('evidence-coverage-archive-kpis').innerHTML = cards([
    ['Archive sessions', archive.archive_session_count],
    ['Verified', archive.verified_session_count],
    ['Unverifiable', archive.unverifiable_session_count],
    ['Invalid', archive.invalid_session_count],
  ]);
  document.getElementById('evidence-coverage-orphan').textContent = JSON.stringify(
    d.quarantined_orphan || {status: 'UNKNOWN', reason: d.reason || 'REPORT_UNAVAILABLE'}, null, 2
  );
  const full = document.getElementById('evidence-coverage-full');
  full.style.display = d.full_artifact ? 'inline-block' : 'none';
  if (d.full_artifact) full.href = d.full_artifact;
}

async function loadGenome() {
  const r = await fetch('/api/genome');
  const d = await r.json();
  const empty = document.getElementById('genome-empty');
  const content = document.getElementById('genome-content');
  if (!d || !d.schema || d.available === false) {
    setEvidenceScope('genome', 'SOURCE UNAVAILABLE', 'The required current Genome evidence is unavailable. Prior artifacts are preserved but current conclusions are blocked.');
    content.style.display = 'none';
    empty.style.display = 'block';
    const src = (d && d.source_status) || {};
    const missing = (src.missing_tables || []).join(', ');
    empty.textContent = (d && d.warning) || `Genome source unavailable: ${src.reason || src.status || 'unknown reason'}.`;
    document.getElementById('genome-note').textContent = `SOURCE UNAVAILABLE · missing tables: ${missing || 'not reported'} · prior artifacts preserved but blocked · execution unaffected.`;
    return;
  }
  setEvidenceScope('genome', 'CURRENT V3.1 SAFE POLICY GENOME', 'Signed current-epoch policy replay. Descriptive rows remain blocked from live use until chronological OOS and risk gates pass.');
  empty.style.display = 'none';
  content.style.display = 'block';
  if (d.collector_generation === 'V3.1') {
    const c = d.collection || {}, s = d.search_progress || {}, cs = d.candidate_screen || {};
    const rows = cs.descriptive_top_100 || [];
    document.getElementById('genome-kpis').innerHTML = [
      ['Collector generation', 'V3.1'],
      ['Generated', d.generated_at ? new Date(d.generated_at).toLocaleString('en-AU', {timeZone:'Australia/Melbourne'}) : 'n/a'],
      ['Independent opportunities', c.independent_opportunities ?? 0],
      ['Decision branches', c.decision_branches ?? 0],
      ['Terminal lifecycles', c.terminal_lifecycles ?? 0],
      ['Market segments', c.market_segments ?? 0],
      ['Policies evaluated', cs.unique_policies_evaluated ?? s.unique_policies_evaluated ?? 0],
      ['Qualification', d.qualification || 'NO SAFE QUALIFIED POLICY'],
    ].map(([l,v]) => {
      const visibleValue = l === 'Qualification' ? String(v).replaceAll('_', ' ') : v;
      return `<div class="kpi"><div class="lbl">${l}</div><div class="val">${visibleValue}</div></div>`;
    }).join('');
    document.getElementById('genome-note').textContent = d.warning || 'Current signed V3.1 Safe Policy Genome evidence.';
    document.getElementById('genome-taxonomy-note').textContent = `Signed epoch ${d.epoch_id || 'not reported'} · source ${d.evidence_source || 'n/a'} · live policy changes ${d.live_policy_change_allowed ? 'allowed' : 'blocked'}.`;
    // Keep the overview bounded. The collection object contains identity arrays
    // and orphan rows large enough to make one panel dominate the whole page.
    // Full evidence remains available through the declared report route.
    const resolution = c.entry_resolution_integrity || {};
    const compactCollection = {
      independent_opportunities: c.independent_opportunities ?? 0,
      decision_branches: c.decision_branches ?? 0,
      terminal_lifecycles: c.terminal_lifecycles ?? 0,
      market_segments: c.market_segments ?? 0,
      decision_dispositions: c.decision_dispositions || {},
      decision_outcomes: c.decision_outcomes || {},
      effective_paper_execution_identity_count: Array.isArray(c.effective_paper_execution_identities)
        ? c.effective_paper_execution_identities.length : 0,
      entry_resolution_integrity: {
        expected: resolution.expected ?? 0,
        awaiting_within_deadline: resolution.awaiting_within_deadline ?? 0,
        orphan_expected_order_count: Array.isArray(resolution.orphan_expected_orders)
          ? resolution.orphan_expected_orders.length : (resolution.orphan_expected_order_count ?? 0),
        status: resolution.status || 'UNKNOWN',
      },
    };
    document.getElementById('genome-cluster').textContent = JSON.stringify({
      epoch_id: d.epoch_id,
      collection: compactCollection,
      qualification: d.qualification,
      blocker_count: Array.isArray(d.blockers) ? d.blockers.length : 0,
      full_report: '/safe-policy-genome-v3.1',
    }, null, 2);
    document.getElementById('genome-decision').textContent = JSON.stringify({search_progress:s, qualification:d.qualification}, null, 2);
    document.getElementById('genome-lifecycle').textContent = JSON.stringify({
      collection: compactCollection,
      blockers: d.blockers,
      full_report: '/safe-policy-genome-v3.1',
    }, null, 2);
    // Do not stringify the complete candidate screen into the overview DOM.
    // The V3.1 screen contains thousands of nested policy/stop cells and made
    // the navigation button block the rendered page while duplicating the
    // entire API payload as text.  Keep this panel useful and bounded; the
    // dedicated Safe Policy Genome route exposes the detailed tables.
    const scenarioSweep = cs.scenario_c_atr_stop_sweep || {};
    document.getElementById('genome-hypotheses').textContent = JSON.stringify({
      number_one_strategy: d.number_one_strategy,
      candidate_warning: cs.warning,
      unique_policies_evaluated: cs.unique_policies_evaluated,
      descriptive_rows_available: rows.length,
      scenario_c_atr_stop_sweep: {
        qualification: scenarioSweep.qualification,
        warning: scenarioSweep.warning,
        policies_tested: scenarioSweep.policies_tested,
        chase_families_materialized: Object.keys(scenarioSweep.best_by_chase_and_stop || {}).length,
        stop_settings_materialized: Object.keys(scenarioSweep.leaders_by_stop || {}).length,
      },
      detailed_route: '/safe-policy-genome-v3.1',
    }, null, 2);
    document.getElementById('genome-replay').textContent = JSON.stringify({integrity:d.integrity, safe_policy_ranking:d.safe_policy_ranking}, null, 2);
    document.getElementById('genome-discoveries').innerHTML = rows.length ? rows.slice(0, 20).map(row =>
      `<div class="kpi" style="margin-bottom:12px;text-align:left;padding:10px"><div class="lbl"><strong>${row.policy_id || 'policy'}</strong> · ${row.policy_family || ''}</div><div class="note">episodes=${row.episodes_total ?? 0} · OOS=${row.oos_episodes ?? 0} · diagnostic net $${fmtUsd(row.diagnostic_replay_net_pnl_usd)} · diagnostic max DD $${fmtUsd(row.diagnostic_replay_max_drawdown_usd)} · ${row.metric_evidence || 'IDEAL_TOUCH_DIAGNOSTIC_ONLY'} · ${row.qualification_eligibility || 'NOT QUALIFICATION ELIGIBLE'} · blockers=${(row.descriptive_blockers || []).join(', ') || 'none reported'}</div></div>`
    ).join('') : '<p class="note">No matured V3.1 policy rows yet. See the blockers above.</p>';
    return;
  }
  const dq = (d.dna_quality || {}).overall || {};
  const tax = d.genome_taxonomy || {};
  document.getElementById('genome-kpis').innerHTML = [
    ['Genome schema (not release)', d.architecture_frozen || d.schema_version || 'n/a'],
    ['Generated', d.generated_at ? new Date(d.generated_at).toLocaleString('en-AU', {timeZone:'Australia/Melbourne'}) : 'n/a'],
    ['DNA Quality', dq.dna_quality ?? 'n/a'],
    ['Sample', dq.sample_size ?? 0],
    ['EV/trade', '$' + fmtUsd(dq.ev)],
    ['Confidence', dq.research_confidence || 'LOW'],
    ['Genomes (persistent)', tax.persistent_genomes ?? (d.genome_memory || {}).persistent_genomes ?? 0],
    ['Validated clusters', tax.validated_clusters ?? 0],
    ['Discoveries', (d.discoveries || []).length],
    ['Validation', (d.validation || {}).verdict || 'n/a'],
  ].map(([l,v]) => `<div class="kpi"><div class="lbl">${l}</div><div class="val">${v}</div></div>`).join('');
  const taxNote = document.getElementById('genome-taxonomy-note');
  if (taxNote && tax.definitions) {
    taxNote.innerHTML = `<strong>Running release</strong> = {{ dashboard_version }}. `
      + `<strong>Genome schema</strong> = ${d.architecture_frozen || d.schema_version || 'n/a'} `
      + `(independent frozen research-data contract, not the bot release). `
      + `<strong>Genome</strong> = persistent fingerprint (${tax.persistent_genomes ?? 0} collecting). `
      + `<strong>Cluster</strong> = validated identity (${tax.validated_clusters ?? 0} validated, `
      + `${tax.candidate_genomes ?? 0} candidates). `
      + `UNKNOWN at high similarity is correct when validated_clusters=0.`;
  }
  const rec = d.recommendation || {};
  const expl = rec.explanation || {};
  const sim = rec.similarity_pct ?? (d.current_market_cluster || {}).similarity_pct;
  let note = (rec.action || 'COLLECT') + ': ' + (rec.detail || d.disclaimer || '');
  if (expl.why) note += ' — ' + expl.why;
  if (rec.action === 'UNKNOWN_MARKET' && sim != null) note += ` (similarity ${Number(sim).toFixed(1)}%)`;
  note += ' — advisory only; never changes execution.';
  document.getElementById('genome-note').textContent = note;
  document.getElementById('genome-cluster').textContent = JSON.stringify(d.current_market_cluster || {}, null, 2);
  document.getElementById('genome-decision').textContent = JSON.stringify(d.decision_dna || {}, null, 2);
  document.getElementById('genome-lifecycle').textContent = JSON.stringify(d.lifecycle_dna || {}, null, 2);
  document.getElementById('genome-hypotheses').textContent = JSON.stringify(d.hypotheses || {}, null, 2);
  document.getElementById('genome-replay').textContent = JSON.stringify(d.replay_capabilities || {}, null, 2);
  document.getElementById('genome-discoveries').innerHTML = (d.discoveries || []).map(disc => {
    const fp = disc.fingerprint || {};
    const m = disc.metrics || {};
    const se = disc.statistical_evidence || {};
    const ci = se.confidence_interval_95 || m.confidence_interval_95 || {};
    const stab = disc.stability || {};
    const ledger = (disc.evidence_ledger || []).slice(-4).map(h =>
      `${h.period_key || (h.ts || '').slice(0,10)}: WR ${((h.win_rate||0)*100).toFixed(0)}% EV $${fmtUsd(h.ev_usd)}`
    ).join(' → ');
    const explDisc = disc.explanation || {};
    return `<div class="kpi" style="margin-bottom:12px;text-align:left;padding:10px">`
      + `<div class="lbl"><strong>${disc.identity || disc.discovery_id || ''}</strong> · ${disc.status || ''} · ${disc.research_confidence || ''}</div>`
      + `<div class="note">${fp.session || ''} · ADX ${fp.adx_bucket || ''} · spread ${fp.spread_bucket || ''} · ${fp.direction || ''}</div>`
      + `<div class="note">n=${se.sample_size ?? disc.observed_trades ?? 0} · EV $${fmtUsd(se.expected_value_usd ?? m.ev_usd)} · CI [$${fmtUsd(ci.low)}–$${fmtUsd(ci.high)}] · DNA ${se.dna_quality ?? m.dna_quality ?? 'n/a'}%</div>`
      + `<div class="note">p=${se.p_value_ev_gt_zero ?? 'n/a'} · sig=${se.statistically_significant ? 'yes' : 'no'} · trend ${stab.trend || 'n/a'} · stable=${stab.stable ? 'yes' : 'no'}</div>`
      + (ledger ? `<div class="note">Ledger: ${ledger}</div>` : '')
      + (explDisc.why ? `<div class="note">${explDisc.why}</div>` : '')
      + `<div class="note"><em>${disc.recommendation || ''}</em></div></div>`;
  }).join('') || '<p class="note">No discoveries yet — need ≥10 trades per DNA fingerprint bucket.</p>';
}

async function loadAI() {
  const r = await fetch('/api/ai');
  const d = await r.json();
  const status = String(d.calibration_status || 'NO_DATA').toUpperCase();
  const showConfidence = status === 'AVAILABLE';
  const confidenceView = document.getElementById('ai-confidence-view');
  const gapView = document.getElementById('ai-gap-view');
  if (confidenceView) confidenceView.style.display = showConfidence ? '' : 'none';
  if (gapView) gapView.style.display = showConfidence ? 'none' : '';
  const modeNote = document.getElementById('ai-mode-note');
  if (modeNote) modeNote.textContent = d.mode_note || `AI evidence mode: ${status}`;

  if (showConfidence) {
    const row = b => `<tr><td>${b.bucket}</td><td>${b.trades}</td><td>${b.win_rate_pct}%</td><td>$${fmtUsd(b.sum_pnl_usd)}</td></tr>`;
    document.getElementById('ai-cal-body').innerHTML = (d.calibration_buckets||[]).filter(b=>b.trades).map(row).join('')
      || '<tr><td colspan="4">No probability-calibration outcomes yet.</td></tr>';
    document.getElementById('ai-conf-body').innerHTML = (d.confidence_bands||[]).filter(b=>b.trades).map(row).join('')
      || '<tr><td colspan="4">No executed probability-band outcomes yet.</td></tr>';
    return;
  }

  const gapNote = document.getElementById('ai-gap-note');
  if (gapNote) gapNote.textContent = d.normalized_gap_note
    || 'Normalized score gap = abs(LONG score - SHORT score) / 10.';
  document.getElementById('ai-gap-body').innerHTML = (d.normalized_gap_buckets||[]).map(b => {
    const cls = Number(b.pnl_usd || 0) >= 0 ? 'green' : 'red';
    return `<tr class="${cls}"><td>${b.spread_bucket||''}</td><td>${b.trades||0}</td>`
      + `<td>${b.wr_pct ?? 'n/a'}%</td><td>$${fmtUsd(b.pnl_usd)}</td><td>$${fmtUsd(b.ev_usd)}</td></tr>`;
  }).join('') || '<tr><td colspan="5">No normalized score-gap outcomes yet.</td></tr>';
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
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = title;
    button.onclick = async () => {
      list.querySelectorAll('button').forEach(x => {
        x.classList.remove('sel');
        x.removeAttribute('aria-current');
      });
      button.classList.add('sel');
      button.setAttribute('aria-current', 'true');
      const output = document.getElementById('report-json');
      output.textContent = `Loading ${title}…`;
      try {
        const rr = await fetch('/api/report/' + encodeURIComponent(file));
        if (!rr.ok) throw new Error(`HTTP ${rr.status}`);
        const j = await rr.json();
        output.textContent = JSON.stringify(j, null, 2);
      } catch (error) {
        output.textContent = `Unable to load ${title}: ${error.message || error}`;
      }
    };
    li.appendChild(button);
    list.appendChild(li);
  });
}

async function loadArchives() {
  const [archiveResponse, pastResponse] = await Promise.all([
    fetch('/api/archives'),
    fetch('/api/past-analysis'),
  ]);
  const d = await archiveResponse.json();
  const past = await pastResponse.json();
  document.getElementById('archive-body').innerHTML = (d.sessions||[]).map(s => {
    const sid = s.id || s.session_id || '';
    return `<tr><td>${sid}</td><td>${(s.generated_at||'').slice(0,19)}</td><td>${s.trades??'n/a'}</td><td>$${fmtUsd(s.net_pnl_usd)}</td><td><a href="/download/archive/${encodeURIComponent(sid)}">ZIP</a></td></tr>`;
  }).join('') || '<tr><td colspan="5">No archives yet — run analyzer once.</td></tr>';
  document.getElementById('past-analysis-body').innerHTML = (past.analyses||[]).map(a => {
    const id = a.archive_id || '';
    const perf = a.performance || {};
    return `<tr><td>${id}</td><td>${(a.created_at||'').slice(0,19)}</td><td>${perf.trades??'n/a'}</td><td>$${fmtUsd(perf.net_pnl_usd)}</td><td><a href="/download/past-analysis/${encodeURIComponent(id)}">ZIP</a></td></tr>`;
  }).join('') || '<tr><td colspan="5">No preserved analysis yet. Fresh Collection creates one only after a completed analyzer run.</td></tr>';
  const pastButton = document.getElementById('dl-past-analysis');
  if (pastButton && !(past.analyses||[]).length) {
    pastButton.removeAttribute('href');
    pastButton.setAttribute('aria-disabled', 'true');
    pastButton.title = 'No preserved Past Analysis is available yet';
    pastButton.style.opacity = '0.45';
    pastButton.style.pointerEvents = 'none';
    pastButton.textContent = 'Past Analysis — not available yet';
  }
}

async function loadStatus() {
  const r = await fetch('/api/status');
  const d = await r.json();
  const syncEl = document.getElementById('sync');
  if (syncEl && d.expected_analyzer_sync_id) {
    syncEl.textContent = d.expected_analyzer_sync_id + (d.analyzer_sync_match === true ? ' ✓' : (d.analyzer_sync_match === false ? ' ⚠' : ''));
  }
  const revisionEl = document.getElementById('revision');
  if (revisionEl) {
    const revision = d.generation_revision || 'UNKNOWN';
    revisionEl.textContent = `analyzer rev ${revision.slice(0, 12)}`;
    revisionEl.title = `Analyzer source revision: ${revision} · Fly/mirror source revision: ${d.mirror_source_revision || 'UNAVAILABLE'} · parity: ${d.source_revision_parity || 'UNAVAILABLE'}`;
  }
  const epochEl = document.getElementById('epoch');
  if (epochEl) {
    const epoch = d.fresh_epoch_id || 'UNBOUND';
    epochEl.textContent = epoch === 'UNBOUND' ? 'epoch UNBOUND' : `epoch ${epoch.replace(/^epoch-/, '').slice(0, 8)}`;
    epochEl.title = `Signed collection epoch: ${epoch}`;
  }
  const melbEl = document.getElementById('melb-clock');
  if (melbEl && d.melbourne_now) melbEl.textContent = d.melbourne_now;
  return d;
}

async function loadGptAuditNote() {
  try {
    const r = await fetch('/api/gpt-audit');
    const d = await r.json();
    const el = document.getElementById('gpt-audit-note');
    if (!el || !d.ready) return;
    const v = d.architecture_version || 'v11';
    const mb = d.zip_size_mb != null ? d.zip_size_mb + ' MB' : '';
    el.textContent = `GPT audit ready — ${v} — ${mb} — updated ${(d.generated_at||'').slice(0,19)}Z`;
  } catch (_) {}
}
async function loadRuntimeIncidents() {
  const r = await fetch('/api/runtime-incidents');
  const d = await r.json();
  const rows = Array.isArray(d.application_incidents) ? d.application_incidents : [];
  const text = value => String(value == null ? '' : value)
    .replaceAll('&', '&amp;').replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;').replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
  document.getElementById('runtime-incidents-note').textContent =
    (d.platform_history_status || 'PLATFORM HISTORY STATUS UNKNOWN') + ' — ' +
    (d.platform_history_note || 'No platform history note.');
  document.getElementById('runtime-incidents-body').innerHTML = rows.map(row =>
    `<tr><td>${text(row.time||'UNKNOWN')}</td><td>${text(row.classification||'UNKNOWN')}</td><td>${text(row.reason||'-')}</td><td>${row.restart_requested?'YES':'NO'}</td><td>${text(row.exit_code==null?'-':row.exit_code)}</td><td>${text(row.evidence_source||'-')}</td></tr>`
  ).join('') || '<tr><td colspan="6">No retained application incident receipts in the bounded crash-dump tail.</td></tr>';
}

const SECTION_LOADERS = {
  summary: [loadSummary], findings: [loadFindings], regime: [loadRegime],
  lanes: [loadLanes],
  ai: [loadAI], chase: [loadChase],
  'chase-policy-lab': [loadChasePolicyLab],
  'chase-threshold': [loadChaseThreshold], 'chase-delay': [loadChaseDelay],
  combos: [loadCombos], 'spread-perf': [loadSpreadPerf],
  'exit-combos': [loadExitCombos], 'exit-reason-leak': [loadExitReasonLeak],
  'ladder-sim': [loadLadderSim], exits: [loadLeakage], genome: [loadGenome],
  'research-design': [loadResearchDesign], 'evidence-coverage': [loadEvidenceCoverage],
  edge: [loadFeatures], explorer: [loadExplorer], archives: [loadArchives],
  download: [loadArchives, loadGptAuditNote], 'runtime-incidents': [loadRuntimeIncidents], 'pathway-audit': [loadPathwayAudit], horizon: [loadHorizon],
};
const SECTION_REFRESHES = new Map();

async function refreshActiveSection() {
  const sectionId = activeSection;
  if (SECTION_REFRESHES.has(sectionId)) return SECTION_REFRESHES.get(sectionId);
  const steps = [loadStatus, ...(SECTION_LOADERS[sectionId] || [])];
  const job = Promise.allSettled(steps.map(step => step())).then(results => {
    results.forEach((result, index) => {
      if (result.status === 'rejected') console.warn('active tab refresh failed', steps[index].name, result.reason);
    });
  }).finally(() => SECTION_REFRESHES.delete(sectionId));
  SECTION_REFRESHES.set(sectionId, job);
  return job;
}

// Header provenance is global, so populate it even when the browser restores a
// non-summary tab.  Previously the header remained stuck on `loading...` until
// Overview was opened, despite current reports being available.
if (activeSection !== 'summary') void loadSummary();
show(activeSection);
setInterval(refreshActiveSection, 180000);
</script></body></html>"""


@app.route("/")
def index():
    nav_groups_json = json.dumps([
        {"id": gid, "label": glabel, "items": [[a, b] for a, b, _ in items]}
        for gid, glabel, items in REPORT_NAV_GROUPS
    ])
    html = render_template_string(
        DASHBOARD_HTML,
        nav_groups_json=nav_groups_json,
        benchmark_lane=BENCHMARK_LANE,
        dashboard_version=RESEARCH_DASHBOARD_VERSION,
        tile_lanes=tuple(DASHBOARD_PRIMARY_LANES),
        tile_lane_names=", ".join(DASHBOARD_PRIMARY_LANES),
    )
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@app.route("/favicon.ico")
def favicon():
    return "", 204


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

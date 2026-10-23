"""full-reset-no-archive: delete EVERYTHING collected since the 13 Oct fresh start, keep nothing as an archive.

Danish's decision (17 Oct 2026 18:09): no archive, no export; wipe the collected data and start clean from now.
This is a separate, explicit mode of ``clean_epoch_wipe.py`` (flag ``--full-reset-no-archive``); no existing
mode, KEEP rule or workflow changes.

Safety model
------------
* KEEP is an explicit ALLOWLIST (``KEEP_EXACT_NAMES`` / ``KEEP_PREFIXES``) plus code, secrets and config by name.
  It covers the live-off flags, arming state, force_paper, settings, the spread gate, tile ON/OFF and the tile 6/7
  switches, boot config, the restart-recovery state and every wipe receipt.
* DELETE is an explicit set of data classes (``DELETE_EXACT_NAMES``, ``DELETE_TAPE_DATASETS`` and top-level
  ``.jsonl`` / ``.csv`` row streams with every rotated ``.N`` / ``.N.gz`` copy). Paper rows (including the
  PAPER_FILL_FORCED deploy-flatten rows, which live in those CSVs), the paper IOC no-fill shadow file, tape,
  book, deriv, (retired) DISLOC ledgers and shadow streams are all in it.
  Analyzer-derived reports (``ANALYZER_DERIVED_REPORT_NAMES``) and ``*episode_receipts.jsonl.gz`` shards in an
  ``analyzer/`` directory at any depth are DELETE too (R5 fresh-only, no archive); they are checked last, so every
  KEEP / CONFIG / RELAY / deny-list rule wins over them.
* Anything that is neither allowlisted nor an explicit data class is UNCLASSIFIED: it is RETAINED and listed in
  the plan and receipt. Nothing is ever deleted by default.
* The plan pins the sha256 of every allowlisted file and a parsed "flag view" (every live / arm / force_paper /
  paper_only / tile / enabled / switch / eligible key) of the state and config files. The tool never opens an
  allowlisted file for writing and only ever unlinks DELETE-class paths. After the last unlink the flag views are
  compared and the run FAILS (receipt completed=false) if a single live flag differs; whole-file byte changes are
  reported too (the paused bot may legitimately rewrite its own heartbeat fields while the tool runs).
* The plan identity is the set of DELETE paths (not sizes or mtimes): the collectors keep appending while the bot is
  paused, so sizes are never stable. Deleting a file that is still being appended is intended.
* Collector v22 events / seals, the V3 store, WAL, SQLite and the research alias directories are NOT touched here:
  they are reset by the existing in-process guarded reset (``POST /api/wipe_fly_only`` with the freeze override),
  which the workflow runs first. Deleting them under a running bot would break seal and index consistency.
* Execution aborts BEFORE deleting anything unless: force_paper, disarmed, live off, execution paused by
  DEPLOY_MAINTENANCE, no open or pending paper position, the expected git rev (the one guarded deploy),
  ``RESEARCH_SEGMENTS_PREFIX_PER_WIPE=1`` and a valid freeze override (or an unguarded freeze).
* The intent receipt (what will be deleted, counts, git_rev, reason, freeze override text) is fsynced BEFORE the
  first unlink; the completion receipt and deleted listing follow. Receipts live under ``full-reset-receipts/``
  which is allowlisted, so they survive the wipe. A completed plan sha makes a re-run a no-op.
* This module is only ever run by hand / by the dispatch-only workflow job. It has no import-time side effects,
  and nothing in the bot or the entrypoint references it, so it can never re-run at boot.

    python clean_epoch_wipe.py plan --scope fly --data-root /app/data --full-reset-no-archive
    python clean_epoch_wipe.py execute --scope fly --data-root /app/data --full-reset-no-archive \
        --confirm DELETE-FULL-RESET:<plan sha256[:12]> --expect-plan-sha256 <sha> --expect-git-rev <rev12> \
        --reason "<why>" --freeze-override-confirmation BREAK_21_DAY_RESEARCH_FREEZE \
        --freeze-override-reason "<>= 10 chars>"
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import clean_epoch_wipe as cew
import data_epoch
import research_reset_inventory as rri

MODE = "full-reset-no-archive"
PLAN_SCHEMA = "full_reset_no_archive_plan_v1"
RECEIPT_SCHEMA = "full_reset_no_archive_receipt_v1"
CONFIRM_PREFIX = "DELETE-FULL-RESET"
RECEIPTS_DIRNAME = "full-reset-receipts"
INTENT_NAME = "receipt-intent.json"
COMPLETE_NAME = "receipt-complete.json"
LISTING_NAME = "deleted.jsonl.gz"

# ------------------------------------------------------------------ KEEP allowlist (explicit)
KEEP_EXACT_NAMES = frozenset({
    # live-off flags, arming state, force_paper, settings, tile ON/OFF and the tile 6/7 switches, boot config
    "config-7002.json", "state.json", "policy.json", "spread-gate.json", "pathway_lane_specs.json",
    "execution_settings_history.jsonl", "data_epoch.json", "fresh_start_epoch.json", "research_session.json",
    # open / paper lifecycle state (must be flat before a wipe) and relay evidence
    "open_positions.json", "positions.json", "orders.json", "pending_orders.json", "paper_state.json",
    "paper_lifecycle_v1.json", "relay_lifecycle_evidence_v1.json", "laptop-reset-required.json",
    # operational boot / audit ledgers that are not collected data
    "retired_tile_boundary_receipts.jsonl", "pre_entry_evidence_handoffs.jsonl",
    # receipts that describe things this reset does not touch (R2 backups, live drills, epoch receipts)
    "archive_receipts.jsonl", "live_drills.jsonl", "lane_pnl_ledger_epoch_receipts.jsonl",
})
KEEP_PREFIXES = (
    "v3/", "runtime/v3/", "recovery/", "recovery_receipts/", "data_epoch_boundary/", "research_reset_receipts/",
    "clean-epoch-receipts/", f"{RECEIPTS_DIRNAME}/", "segment-store/", "segment-shipper",
)
# Handled by the in-process reset (seal receipts and the event index must stay consistent with them).
INPROCESS_RESET_BASES = ("research_events_v22", "collector_v22", "research_events_v22.seals")
# Irreplaceable research that predates the fresh start (hash chains, pre-registrations, signed receipts).
PRESERVED_RESEARCH_TOKENS = cew.PRESERVED_RESEARCH_TOKENS

# ------------------------------------------------------------------ DELETE classes (explicit)
DELETE_EXACT_NAMES = frozenset({
    # paper rows and accounting (PAPER_FILL_FORCED deploy-flatten rows are rows of these files)
    "trades_3factor.csv", "trades_3factor.csv.malformed_rows.jsonl",
    "csv_write_fallback.jsonl", "csv_write_fallback_replay.json",
    "lane_pnl_ledger.json", "lane_lab_pnl_ledger.json", "paper_ioc_no_fill_shadow.jsonl",
    # Retired DISLOC / DISLOC-FOI ledgers (Tiles 6/7 removed 23 Oct 2026): no writer; still classified so a
    # left-over file is handled like any paper ledger, and a missing one is simply absent (never a crash).
    "disloc_trades.jsonl", "disloc_foi_trades.jsonl", "disloc_paused_skips.jsonl",
    # shadow / counterfactual streams
    "shadow_exit_paths.jsonl", "xvp_shadow_signals.jsonl", "ai_shadow_challengers.jsonl",
    # DISK-CAP rotation-cap receipts (self-capped; never shipped)
    "cap_receipts.jsonl", "v22_discard_receipts.jsonl",
    # market streams (tape, book, deriv, trades, bbo, cross venue, context, liquidations)
    "market_microstructure_1s.jsonl", "bfx_book_10l_1s.jsonl", "bfx_trades.jsonl", "bfx_deriv_status_1s.jsonl",
    "venue_bbo_1m.jsonl", "cross_venue_tape_1m.jsonl", "market_context_1m.jsonl", "liquidations.jsonl",
    "runtime_telemetry_1m.jsonl",
    # the other *_3factor.csv ledgers (named so the plan table documents them; the guarded expired-orders CSV is covered by
    # the top-level row-stream rule because the epoch guard test pins who may name it)
    "decisions_3factor.csv", "blocked_signals_3factor.csv", "pipeline_events_3factor.csv", "setup_log_3factor.csv",
    "candles_3factor.csv", "ai_errors_3factor.csv", "blocked_3factor.csv", "trades_3factor.csv.validation.json",
    # Tile 6/7 and lane evidence (they write through the lane ledgers and the shadow lane streams)
    "shadow_lane_outcome.jsonl", "lane_opportunity_capture.jsonl", "lane_opportunity_capture.json",
    "ledger_reconciliation.json",
    # analysis outputs
    "pathway_scorecard.json", "fill_quality_report.json", "shadow_fill_outcome_report.json",
    "benchmark_vs_lanes_report.json", "analyzer_integrity_report.json",
})
DELETE_TAPE_DATASETS = cew.TAPE_DATASETS | cew.MARKET_DATASETS
# The ONLY names allowed to override the deny-list (top level, plus that stream's own rotations). Owner decision
# 2026-10-17 21:15 AEDT: tile_integrity.jsonl is an append-only data stream (tile integrity rows), not a switch.
DENY_OVERRIDE_DELETE_NAMES = frozenset({"tile_integrity.jsonl"})
# Analyzer-derived reports (R5 fresh-only, no archive): every report the analyzer regenerates from collected rows is
# DELETE when it sits in an ``analyzer/`` directory at ANY depth. The declared set is the shared inventory
# (research_reset_inventory, parity-tested against the analyzer's own report list, R4: one list) plus the derived
# reports the analyzer / past_analysis write outside that list. Manifests, pointers, snapshots and the startup sync gate
# are NOT here (they stay UNCLASSIFIED and retained); KEEP, CONFIG, RELAY and the deny-list are evaluated first and win.
ANALYZER_DIR_NAME = "analyzer"
ANALYZER_EXTRA_DERIVED_REPORT_NAMES = frozenset({
    "ai_confidence_expectancy.json", "ai_scan_independence_report.json", "all_epochs_bp.json",
    "analyzer_input_blockers.json", "conservative_shadow_terminal_report.json", "counterfactual_coverage_report.json",
    "discovery_cohort_scorecard_report.json", "dynamic_policy_analysis_report.json",
    "evidence_coverage_triage_report.json", "execution_funnel_summary.json", "exit_reports_validation.json",
    "historical_trade_cohort_report.json", "lane_definition_report.json", "lane_memory_validation.json",
    "lane_retirement_report.json", "lifecycle_bundle_inventory.json", "policy_evidence_binding_report.json",
    "policy_research_reports.json",
})
ANALYZER_DERIVED_REPORT_NAMES = frozenset(
    rri.ANALYZER_REPORT_FILES | rri.ANALYZER_SIDECAR_REPORT_FILES | ANALYZER_EXTRA_DERIVED_REPORT_NAMES)
ANALYZER_EPISODE_RECEIPTS_SUFFIX = "episode_receipts.jsonl.gz"
DENY_NAME_TOKENS = ("live", "arm", "flag", "switch", "tile", "settings")
DELETE_STREAM_SUFFIXES = (".jsonl", ".csv", ".jsonl.gz", ".csv.gz")
ROTATION_RE = cew.ROTATION_RE
_ANY_ROTATION_RE = re.compile(r"^(?P<base>.+\.(?:jsonl|csv))\.(?P<n>[1-9][0-9]*)(?:\.gz)?$")

KEEP, DELETE, UNCLASSIFIED = "KEEP", "DELETE", "UNCLASSIFIED"


class FullResetRefused(RuntimeError):
    """The reset must not run (nothing was deleted)."""


def _base_name(name: str) -> str:
    m = _ANY_ROTATION_RE.match(name)
    return m.group("base") if m else name


def classify(relpath: str) -> tuple[str, str]:
    """(KEEP|DELETE|UNCLASSIFIED, reason) for one runtime-relative POSIX path. Order matters and is pinned by tests."""
    rel = relpath.replace("\\", "/").lstrip("/")
    lower = rel.lower()
    parts = lower.split("/")
    name = parts[-1]
    base = _base_name(name)
    top_level = len(parts) == 1
    # 1. explicit allowlist
    if base in KEEP_EXACT_NAMES:
        return KEEP, "ALLOWLIST_NAME"
    if any(lower.startswith(p) for p in KEEP_PREFIXES):
        return KEEP, "ALLOWLIST_PREFIX"
    # 2. code, secrets, config: never touched
    if ".git" in parts or lower.endswith(cew.CODE_SUFFIXES) or name in ("dockerfile", "makefile"):
        return KEEP, "CODE"
    if any(t in lower for t in cew.SECRET_TOKENS):
        return KEEP, "SECRETS"
    if any(t in lower for t in cew.CONFIG_TOKENS):
        return KEEP, "CONFIG"
    # 3. handled by the in-process reset
    if any(base == b or base.startswith(b + ".") or parts[0] == b for b in INPROCESS_RESET_BASES):
        return KEEP, "INPROCESS_RESET_OWNS_IT"
    # 4. pre-fresh-start irreplaceable research and signed receipts
    if any(t in lower for t in PRESERVED_RESEARCH_TOKENS):
        return KEEP, "PRESERVED_RESEARCH"
    # 4b. DENY-LIST: anything that smells like a live / arming / flag / switch / tile / settings file is NEVER deleted by
    # any rule below (explicit, dataset or catch-all). Not on KEEP means UNCLASSIFIED, so execute aborts for data files.
    if top_level and base in DENY_OVERRIDE_DELETE_NAMES:  # owner decision 17 Oct 21:15: a data stream, not a switch
        return DELETE, "DENY_OVERRIDE_DATA_STREAM" + ("_ROTATED_COPY" if base != name else "")
    if any(t in name for t in DENY_NAME_TOKENS):
        return UNCLASSIFIED, "DENYLIST_NAME_NEVER_DELETE"
    # 5. explicit DELETE classes
    if base in DELETE_EXACT_NAMES and (top_level or base == name or _ANY_ROTATION_RE.match(name)):
        return DELETE, "DATA_STREAM_EXACT" + ("_ROTATED_COPY" if base != name else "")
    if any(d in parts[:-1] for d in DELETE_TAPE_DATASETS):
        return DELETE, "TAPE_DATASET"
    # 6. relay / exchange evidence, SQLite, restart-recovery state stay
    if any(t in lower for t in cew.RELAY_TOKENS):
        return KEEP, "RELAY_BITFINEX_EVIDENCE"
    if lower.endswith(cew.SQLITE_SUFFIXES):
        return KEEP, "RESTART_STATE_SQLITE"
    if any(t in lower for t in cew.RECOVERY_TOKENS):
        return KEEP, "RESTART_RECOVERY_STATE"
    # 7. top-level row streams (and every rotated copy) are collected data
    if top_level and (lower.endswith(DELETE_STREAM_SUFFIXES) or _ANY_ROTATION_RE.match(name)):
        return DELETE, "ROW_STREAM_ROTATED_COPY" if _ANY_ROTATION_RE.match(name) else "ROW_STREAM"
    # 8. analyzer-derived reports and episode-receipt shards in an analyzer/ directory at any depth (R5 fresh-only)
    if ANALYZER_DIR_NAME in parts[:-1]:
        if name.endswith(ANALYZER_EPISODE_RECEIPTS_SUFFIX):
            return DELETE, "ANALYZER_EPISODE_RECEIPTS"
        if base in ANALYZER_DERIVED_REPORT_NAMES:
            return DELETE, "ANALYZER_DERIVED_REPORT"
    return UNCLASSIFIED, "NOT_ALLOWLISTED_NOT_A_DATA_CLASS"


# ------------------------------------------------------------------ planning

def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _walk(root: Path) -> Iterable[tuple[str, os.stat_result]]:
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if not os.path.islink(os.path.join(directory, d)) and d != ".git")
        for fname in sorted(filenames):
            path = os.path.join(directory, fname)
            try:
                st = os.lstat(path)
            except OSError:
                continue
            if os.path.islink(path):
                continue
            yield os.path.relpath(path, root).replace("\\", "/"), st


FLAG_FILES = ("config-7002.json", "state.json", "policy.json", "spread-gate.json", "paper_lifecycle_v1.json")
_FLAG_KEY_RE = re.compile(r"live|arm|force_paper|paper_only|tile|enabled|switch|eligible|pause|gate", re.I)


def _flag_view(doc: Any, prefix: str = "") -> dict:
    """Flatten the parsed JSON to {dotted.path: value} for every flag-looking key (scalars only)."""
    out: dict = {}
    if isinstance(doc, Mapping):
        for key, value in doc.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, (Mapping, list)):
                inner = _flag_view(value, path)
                out.update(inner if _FLAG_KEY_RE.search(path) or inner else {})
            elif _FLAG_KEY_RE.search(path) and isinstance(value, (bool, int, float, str, type(None))):
                out[path] = value
    elif isinstance(doc, list) and len(doc) <= 64:
        for i, value in enumerate(doc):
            out.update(_flag_view(value, f"{prefix}[{i}]") if isinstance(value, (Mapping, list)) else
                       ({f"{prefix}[{i}]": value} if _FLAG_KEY_RE.search(prefix) and isinstance(value, (bool, int, float, str, type(None))) else {}))
    return out


def flag_views(runtime: Path) -> dict:
    views = {}
    for name in FLAG_FILES:
        doc = _read_json(runtime / name)
        if doc is not None:
            views[name] = _flag_view(doc)
    return views


RULES_VERSION = "full-reset-rules-v5-analyzer-derived-reports"


def rules_identity() -> bytes:
    """The plan sha binds the delete CLASSES and RULES (never exact paths), so log rotation, collectors appending and
    the in-process reset between plan and execute cannot cause a PLAN_SHA256_MISMATCH."""
    doc = {"version": RULES_VERSION, "delete_exact": sorted(DELETE_EXACT_NAMES), "delete_datasets": sorted(DELETE_TAPE_DATASETS),
           "delete_suffixes": list(DELETE_STREAM_SUFFIXES), "keep_exact": sorted(KEEP_EXACT_NAMES),
           "keep_prefixes": list(KEEP_PREFIXES), "inprocess": list(INPROCESS_RESET_BASES),
           "preserved": list(PRESERVED_RESEARCH_TOKENS), "deny": list(DENY_NAME_TOKENS), "deny_override": sorted(DENY_OVERRIDE_DELETE_NAMES), "analyzer_dir": ANALYZER_DIR_NAME, "analyzer_reports": sorted(ANALYZER_DERIVED_REPORT_NAMES), "analyzer_receipts_suffix": ANALYZER_EPISODE_RECEIPTS_SUFFIX, "legacy": ["orders.json", "positions.json", "pending_orders.json", "paper_state.json"]}
    return json.dumps(doc, sort_keys=True, separators=(",", ":")).encode()


def is_data_file(rel: str) -> bool:
    low = rel.lower()
    return low.endswith((".csv", ".jsonl", ".gz")) or bool(_ANY_ROTATION_RE.match(low.rsplit("/", 1)[-1]))


def top_level_rows(candidates: list) -> list:
    """Top-level files to delete with rotated copies folded in: [{name, files, bytes}] (what HM reads in the plan file)."""
    agg: dict[str, dict] = {}
    for c in candidates:
        if "/" in c["rel"]:
            continue
        row = agg.setdefault(_base_name(c["rel"]), {"name": _base_name(c["rel"]), "files": 0, "bytes": 0})
        row["files"] += 1
        row["bytes"] += int(c["bytes"])
    return sorted(agg.values(), key=lambda r: -r["bytes"])


def top_level_unclassified(unclassified: list) -> list:
    return [{"name": u["rel"], "bytes": u["bytes"]} for u in unclassified if "/" not in u["rel"]][:200]


def build_plan(data_root: Path | str) -> dict:
    """Read-only plan over ``<data_root>/runtime``. Only the runtime directory itself is scanned, never alias roots."""
    runtime = Path(data_root) / "runtime"
    candidates, kept, unclassified = [], {}, []
    keep_digests: dict[str, str] = {}
    by_class: dict[str, dict[str, int]] = {}
    for rel, st in _walk(runtime) if runtime.is_dir() else ():
        action, reason = classify(rel)
        if action == DELETE:
            candidates.append({"path": str(runtime / rel), "rel": rel, "bytes": int(st.st_size),
                               "mtime_ns": int(st.st_mtime_ns), "reason": reason})
            row = by_class.setdefault(reason, {"files": 0, "bytes": 0})
            row["files"] += 1
            row["bytes"] += int(st.st_size)
        elif action == KEEP:
            row = kept.setdefault(reason, {"files": 0, "bytes": 0})
            row["files"] += 1
            row["bytes"] += int(st.st_size)
            if reason in ("ALLOWLIST_NAME", "CONFIG") and st.st_size <= 64 * 1024 * 1024:
                keep_digests[rel] = _sha256_file(str(runtime / rel))
        else:
            unclassified.append({"rel": rel, "bytes": int(st.st_size)})
    candidates.sort(key=lambda c: c["path"])
    identity = rules_identity()
    blocking = [u for u in unclassified if is_data_file(u["rel"])]
    total = sum(c["bytes"] for c in candidates)
    return {"schema": PLAN_SCHEMA, "mode": MODE, "data_root": str(data_root), "runtime": str(runtime),
            "total_delete_files": len(candidates), "total_delete_bytes": total, "total_delete_gb": round(total / 1e9, 3),
            "delete_by_class": by_class, "kept_by_reason": kept,
            "unclassified_retained_count": len(unclassified), "unclassified_retained": unclassified[:500],
            "unclassified_data_files_count": len(blocking), "unclassified_data_files": blocking[:200],
            "keep_digests": keep_digests, "keep_flag_views": flag_views(runtime), "plan_sha256": hashlib.sha256(identity).hexdigest(),
            "notes": ["unclassified files are retained and listed, never deleted",
                      "V3 store, WAL, SQLite, collector v22 and research alias dirs are reset by the in-process "
                      "guarded reset (POST /api/wipe_fly_only), not by this tool"],
            "top_level_delete": top_level_rows(candidates), "top_level_denied_or_unclassified": top_level_unclassified(unclassified),
            "candidates": candidates}


def confirm_token(plan_sha256: str) -> str:
    return f"{CONFIRM_PREFIX}:{plan_sha256[:12]}"


def summary(plan: dict, examples: int = 15) -> dict:
    out = {k: v for k, v in plan.items() if k not in ("candidates", "keep_digests", "keep_flag_views")}
    out["top_level_delete_count"] = len(plan.get("top_level_delete") or [])
    out["keep_digest_count"] = len(plan.get("keep_digests") or {})
    out["largest_candidates"] = sorted(plan["candidates"], key=lambda c: -c["bytes"])[:examples]
    return out


# ------------------------------------------------------------------ preflight

_TRUE_SAFE = {"force_paper_mode": True}
_FALSE_SAFE = ("live_armed", "bitfinex_live_enabled")


def _truthy_count(value: Any) -> int | None:
    """0 only when provably empty/zero; a fractional exposure (0.0003 BTC) or an unreadable value is NOT flat."""
    if value is None:
        return None
    if isinstance(value, (list, tuple, dict, set)):
        return len(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 1
    return 0 if abs(number) < 1e-12 else max(1, int(abs(number)))


_ABSENT = object()
EXCHANGE_PROOF_MAX_AGE_SEC = 900


def _is_real_zero(value: Any) -> bool:
    """FAIL CLOSED: True only for an empty list/dict or a numeric zero. None (key absent / file unreadable), strings,
    booleans and any non-zero number are NOT zero."""
    if value is None or value is _ABSENT or isinstance(value, (bool, str, bytes)):
        return False
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    if isinstance(value, (int, float)):
        return abs(float(value)) < 1e-12
    return False


def _describe(value: Any) -> str:
    if value is None:
        return "missing"
    if isinstance(value, (list, tuple, dict, set)):
        return f"{len(value)} rows"
    return repr(value)[:40]


def preflight_violations(health: Mapping[str, Any], *, lifecycle: Mapping[str, Any] | None,
                         config: Mapping[str, Any] | None, open_positions: Any, expect_git_rev: str,
                         env: Mapping[str, str], legacy_state: Mapping[str, Any] | None = None,
                         exchange: Mapping[str, Any] | None = None, now: float | None = None) -> list[str]:
    """Every reason the reset must not start (empty list = all pre-checks pass)."""
    bad: list[str] = []
    for key, want in _TRUE_SAFE.items():
        if health.get(key) is not want:
            bad.append(f"{key}={health.get(key)!r} (want {want!r})")
    for key in _FALSE_SAFE:
        if health.get(key) is not False:
            bad.append(f"{key}={health.get(key)!r} (want False)")
    if health.get("execution_paused") is not True or health.get("pause_owner") != "DEPLOY_MAINTENANCE":
        bad.append(f"relay/bot not paused by DEPLOY_MAINTENANCE: execution_paused={health.get('execution_paused')!r} "
                   f"pause_owner={health.get('pause_owner')!r}")
    rev = str(health.get("source_git_rev") or health.get("git_rev") or "").lower()
    want_rev = str(expect_git_rev or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{7,40}", want_rev):
        bad.append("expect_git_rev missing or malformed")
    elif not (rev.startswith(want_rev) or want_rev.startswith(rev)) or not rev:
        bad.append(f"git_rev {rev[:12]!r} != expected {want_rev[:12]!r} (one guarded deploy only)")
    if lifecycle is None:
        bad.append("paper_lifecycle_v1.json unreadable (cannot prove flat)")
    else:
        if lifecycle.get("paper_only") is not True or lifecycle.get("live_armed") is not False:
            bad.append("paper_lifecycle_v1.json is not paper_only/disarmed")
        for key in ("positions", "pending_orders"):
            if _truthy_count(lifecycle.get(key)) != 0:
                bad.append(f"paper_lifecycle_v1.json {key} not empty")
    if config is None:
        bad.append("config-7002.json unreadable or missing (cannot prove live is off)")
    else:
        for key in ("live_armed", "bitfinex_live_enabled"):
            if config.get(key) is True:
                bad.append(f"config-7002.json {key} is true")
    if not _is_real_zero(open_positions):
        bad.append(f"open_positions.json missing/unreadable or not empty ({_describe(open_positions)})")
    # /health is a liveness probe and carries NO exchange facts: the exchange proof (workflow GET of the private
    # exposure audit) is merged over it. Every key must be PRESENT and a real zero; a missing key is a violation.
    facts = {**health, **dict(exchange or {})}
    for key in ("exchange_positions", "exchange_active_orders", "exchange_delta_btc"):
        if key not in facts:
            bad.append(f"{key} absent (cannot prove the exchange is flat)")
        elif not _is_real_zero(facts.get(key)):
            bad.append(f"{key}={facts.get(key)!r} (exchange not flat)")
        if key in health and not _is_real_zero(health.get(key)):  # a non-zero in EITHER source is a violation
            bad.append(f"/health {key}={health.get(key)!r} (exchange not flat)")
    if exchange is not None:
        for key in ("authoritative", "fresh", "flat"):
            if facts.get(key) is not True:
                bad.append(f"exchange proof {key}={facts.get(key)!r} (want True)")
        if "error" not in facts or facts.get("error") is not None:
            bad.append(f"exchange proof error={facts.get('error')!r} (must be present and null)")
        try:
            age = float(now if now is not None else time.time()) - float(facts.get("proof_ts"))
        except (TypeError, ValueError):
            age = None
        if age is None or age < -60 or age > EXCHANGE_PROOF_MAX_AGE_SEC:
            bad.append(f"exchange proof is missing a recent proof_ts (age {age!r}s, max {EXCHANGE_PROOF_MAX_AGE_SEC}s)")
    for name, doc in (legacy_state or {}).items():
        if doc is not _ABSENT and not _is_real_zero(doc):
            bad.append(f"{name} is present and not empty (could hold paper rows)")
    if str(env.get("RESEARCH_SEGMENTS_PREFIX_PER_WIPE", "")).strip() != "1":  # same strictness as research_segment_epoch
        bad.append("RESEARCH_SEGMENTS_PREFIX_PER_WIPE=1 required (fly.toml) so numbering cannot reuse an old prefix")
    return bad


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text("utf-8-sig"))
    except (OSError, ValueError):
        return None


LEGACY_STATE_FILES = ("orders.json", "positions.json", "pending_orders.json", "paper_state.json")


def local_state_for_preflight(runtime: Path) -> dict:
    legacy = {}
    for name in LEGACY_STATE_FILES:
        legacy[name] = _read_json(runtime / name) if (runtime / name).exists() else _ABSENT
        if legacy[name] is None:  # exists but unreadable
            legacy[name] = "UNREADABLE"
    return {"lifecycle": _read_json(runtime / "paper_lifecycle_v1.json"), "config": _read_json(runtime / "config-7002.json"),
            "open_positions": _read_json(runtime / "open_positions.json"), "legacy_state": legacy}


def freeze_refusal(data_root: Path, now: float, override: Mapping[str, str] | None) -> dict | None:
    """The freeze is lifted ONLY by BREAK_21_DAY_RESEARCH_FREEZE plus a reason (>= 10 chars), for this reset only."""
    import research_freeze
    manifest = None
    try:
        manifest = data_epoch.load_manifest(Path(data_root) / "runtime")
    except (OSError, ValueError, KeyError, TypeError):
        manifest = None
    verdict = research_freeze.check(research_freeze.ACTION_PRE_START_WIPE, manifest, now, override=override,
                                    env={})
    if verdict["allowed"]:
        return None
    return {"error": verdict["error"], "summary": verdict["summary"], "research_freeze": verdict["freeze"]}


# ------------------------------------------------------------------ execution

def _fsync_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def completed_receipt(receipts_dir: Path, plan_sha256: str) -> dict | None:
    doc = _read_json(Path(receipts_dir) / plan_sha256[:12] / COMPLETE_NAME)
    return doc if isinstance(doc, dict) and doc.get("plan_sha256") == plan_sha256 and doc.get("completed") is True else None


def execute(plan: dict, *, receipts_dir: Path | str, git_rev: str, reason: str, freeze_override: Mapping[str, str] | None,
            unlink: Callable[[str], None] = os.unlink, now: Callable[[], float] = time.time) -> dict:
    """Intent receipt first, then delete, then verify the allowlist is byte-identical and write the completion."""
    receipts_dir = Path(receipts_dir)
    sha = plan["plan_sha256"]
    done = completed_receipt(receipts_dir, sha)
    if done:
        return {**done, "already_done": True}
    if len(str(reason or "").strip()) < 10:
        raise FullResetRefused("REASON_REQUIRED: a written reason of at least 10 characters")
    if int(plan.get("unclassified_data_files_count") or 0) > 0:
        names = [u["rel"] for u in plan.get("unclassified_data_files", [])][:20]
        raise FullResetRefused(f"UNCLASSIFIED_DATA_FILES: {plan['unclassified_data_files_count']} .csv/.jsonl/.gz files are neither "
                               f"KEEP nor DELETE (classify them, then re-plan): {names}")
    runtime = Path(plan["runtime"])
    rdir = receipts_dir / sha[:12]
    started = now()
    by_class = plan["delete_by_class"]
    intent = {"schema": RECEIPT_SCHEMA, "phase": "INTENT", "mode": MODE, "plan_sha256": sha, "git_rev": git_rev,
              "reason": str(reason).strip(), "started_at_utc": data_epoch.utc_iso(started),
              "freeze_override": ({"confirmation": freeze_override.get("confirmation"),
                                   "reason": str(freeze_override.get("reason") or "").strip()}
                                  if freeze_override else None),
              "will_delete_files": plan["total_delete_files"], "will_delete_bytes": plan["total_delete_bytes"],
              "delete_by_class": by_class, "kept_by_reason": plan["kept_by_reason"],
              "unclassified_retained_count": plan["unclassified_retained_count"],
              "keep_digests": plan["keep_digests"], "keep_flag_views": plan.get("keep_flag_views", {}), "archive": "NONE (Danish 17 Oct 2026 18:09: no archive)"}
    _fsync_json(rdir / INTENT_NAME, intent)  # durable BEFORE the first unlink
    deleted, freed, skipped = [], 0, []
    for row in plan["candidates"]:
        path, rel = row["path"], row["rel"]
        action, why = classify(rel)
        if action != DELETE:
            skipped.append({"path": path, "why": f"CLASSIFICATION_CHANGED:{action}:{why}"})
            continue
        try:
            size = int(os.lstat(path).st_size)
        except FileNotFoundError:
            skipped.append({"path": path, "why": "ALREADY_ABSENT"})
            continue
        unlink(path)  # a file still being appended by a collector is deleted on purpose; the restart follows
        deleted.append(path)
        freed += size
    bytes_changed = []
    for rel, digest in plan["keep_digests"].items():
        try:
            now_digest = _sha256_file(str(runtime / rel))
        except OSError:
            now_digest = None
        if now_digest != digest:
            bytes_changed.append(rel)
    after_flags = flag_views(runtime)
    changed = sorted(name for name, view in plan.get("keep_flag_views", {}).items()
                     if after_flags.get(name) != view)
    with gzip.open(rdir / LISTING_NAME, "wt", encoding="utf-8") as fh:
        for path in deleted:
            fh.write(json.dumps({"path": path}) + "\n")
    complete = {**intent, "phase": "COMPLETE", "completed": not changed, "finished_at_utc": data_epoch.utc_iso(now()),
                "deleted_files": len(deleted), "freed_bytes": freed, "freed_gb": round(freed / 1e9, 3),
                "skipped": skipped[:200], "skipped_count": len(skipped), "keep_flags_changed": changed,
                "keep_bytes_changed_informational": bytes_changed,
                "deleted_listing": str(rdir / LISTING_NAME)}
    _fsync_json(rdir / COMPLETE_NAME, complete)
    with open(receipts_dir / "receipts.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({k: v for k, v in complete.items() if k not in ("skipped", "keep_digests", "kept_by_reason")},
                            sort_keys=True, default=str) + "\n")
    if changed:
        raise FullResetRefused(f"LIVE_FLAGS_CHANGED: {changed} (receipt {rdir / COMPLETE_NAME} records completed=false)")
    return complete


def verify_after(plan: dict) -> dict:
    """Post-wipe self check: no DELETE-class file remains (a collector may have re-created a tiny new file, which the
    restart follows), every allowlisted file still exists and no live flag differs from the plan."""
    runtime = Path(plan["runtime"])
    leftovers = [rel for rel, _ in (_walk(runtime) if runtime.is_dir() else ()) if classify(rel)[0] == DELETE]
    after = flag_views(runtime)
    changed = sorted(name for name, view in plan.get("keep_flag_views", {}).items() if after.get(name) != view)
    missing = sorted(rel for rel in plan["keep_digests"] if not (runtime / rel).exists())
    return {"ok": not changed and not missing, "delete_class_leftovers": leftovers[:50],
            "delete_class_leftover_count": len(leftovers),
            "keep_flags_changed": changed, "keep_missing": missing}


# ------------------------------------------------------------------ CLI (called by clean_epoch_wipe.py)

DEFAULT_HEALTH_URL = "http://127.0.0.1:7002/health"


def _fetch_health(url: str) -> dict:
    import urllib.request  # noqa: PLC0415
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def _exchange_arg(args) -> dict | None:
    raw = getattr(args, "exchange_proof_json", None)
    if not raw:
        return None
    try:
        doc = json.loads(raw)
    except ValueError:
        return {"unparseable": True}
    return doc if isinstance(doc, dict) else {"unparseable": True}


def run_cli(args, *, fetch: Callable[[str], dict] | None = None, env: Mapping[str, str] | None = None,
            clock: Callable[[], float] = time.time, unlink: Callable[[str], None] = os.unlink) -> int:
    """plan (dry run, default) or execute. Every gate fails closed BEFORE the first deletion. Exit codes:
    0 ok / already done, 2 bad usage, 5 confirmation, 6 plan sha mismatch, 7 pre-check failed, 8 freeze, 9 refused."""
    env = os.environ if env is None else env
    if args.scope != "fly":
        print(json.dumps({"error": "FULL_RESET_IS_FLY_SCOPE_ONLY",
                          "hint": "laptop steps are run by hand with Danish's approval, see the runbook"}))
        return 2
    data_root = Path(args.data_root or "/app/data")
    receipts = Path(args.receipts_dir) if args.receipts_dir else data_root / "runtime" / RECEIPTS_DIRNAME
    if args.command == "execute" and args.expect_plan_sha256:
        done = completed_receipt(receipts, args.expect_plan_sha256)
        if done:
            print(json.dumps({"executed": False, "already_done": True, "plan_sha256": done["plan_sha256"],
                              "deleted_files": done.get("deleted_files")}, sort_keys=True))
            return 0
    plan = build_plan(data_root)
    if args.out:
        data_epoch.write_json_atomic(args.out, plan)
    if args.command == "plan":
        print(json.dumps({"dry_run": True, "confirm_token": confirm_token(plan["plan_sha256"]), **summary(plan)},
                         indent=1, sort_keys=True, default=str))
        return 0
    if args.confirm != confirm_token(plan["plan_sha256"]):
        print(json.dumps({"error": "CONFIRMATION_REQUIRED", "expected_form": f"{CONFIRM_PREFIX}:<plan sha256[:12]>"}))
        return 5
    if args.expect_plan_sha256 != plan["plan_sha256"]:
        print(json.dumps({"error": "PLAN_SHA256_MISMATCH", "plan_sha256": plan["plan_sha256"]}))
        return 6
    override = None
    if getattr(args, "freeze_override_confirmation", None) or getattr(args, "freeze_override_reason", None):
        override = {"confirmation": args.freeze_override_confirmation, "reason": args.freeze_override_reason}
    refusal = freeze_refusal(data_root, clock(), override)
    if refusal:
        print(json.dumps(refusal, sort_keys=True, default=str))
        return 8
    try:
        health = (fetch or _fetch_health)(args.health_url or DEFAULT_HEALTH_URL)
    except Exception as exc:  # noqa: BLE001 - any doubt fails closed
        print(json.dumps({"error": "PRECHECK_FAILED", "violations": [f"health unavailable: {type(exc).__name__}"]}))
        return 7
    local = local_state_for_preflight(data_root / "runtime")
    bad = preflight_violations(health, lifecycle=local["lifecycle"], config=local["config"],
                               open_positions=local["open_positions"], expect_git_rev=args.expect_git_rev or "", env=env,
                               legacy_state=local["legacy_state"], exchange=_exchange_arg(args), now=clock())
    if bad:
        print(json.dumps({"error": "PRECHECK_FAILED", "violations": bad, "nothing_deleted": True}))
        return 7
    try:
        receipt = execute(plan, receipts_dir=receipts, git_rev=str(health.get("source_git_rev") or ""),
                          reason=args.reason or "", freeze_override=override, unlink=unlink, now=clock)
    except FullResetRefused as exc:
        print(json.dumps({"error": "FULL_RESET_REFUSED", "detail": str(exc)}))
        return 9
    post = verify_after(plan)
    print(json.dumps({"executed": True, "verify": post, **{k: v for k, v in receipt.items() if k not in ("keep_digests", "skipped")}},
                     indent=1, sort_keys=True, default=str))
    return 0 if post["ok"] else 9

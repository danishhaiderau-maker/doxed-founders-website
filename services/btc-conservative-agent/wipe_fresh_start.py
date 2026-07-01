#!/usr/bin/env python3
"""
Wipe all legacy research/trading runtime data — fresh v9.83 week collection.

Preserves: source .py, git repos, secrets vault, deploy monorepo code.
Removes: CSVs, JSONL, reports, session archives, old downloads, accumulator DB.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENT = ROOT / "doxedcryptofounder" / "services" / "btc-conservative-agent"
VERSION = "v9.83-quality-roster-4-tiles-2026-06-21"

# Directories to remove entirely (runtime data only)
WIPE_DIRS = (
    "research_session_archives",
    "research_accumulator",
    "research_archive",
    "reports",
    "downloads",
    "overnight_analysis",
    "_reports_bundle_temp",
    "_dash_debug_reports",
    "_analysis_debug",
    "_analysis_logs",
    "_analysis_zip84",
    "_analyze_debug15",
    "_analyze_latest",
    "_analyze_zip70",
    "_audit_temp",
    "_debug_export_82",
    "_debug_review_8",
    "_extracted_debug",
    "_extracted_3factor",
    "_import_debug27",
    "_import_3factor83",
    "_log_parse_23",
    "_log_parse_78",
    "_log64",
    "_log_review_63",
    "_zip90_import",
    "_dash_debug_logs89",
    "debug_session_60",
    "debug_session_61",
    "debug_session_61_run",
    "debug_session_62",
    "debug_session_62_run",
    "debug_session_63",
)

# research/ holds the bulk accumulated raw data (~27GB / 478k files) AND the Genome
# analysis store. Wipe research/ contents completely BUT preserve the Genome subdirs
# (the accumulated analysis that must survive a fresh collection).
PRESERVE_INSIDE_RESEARCH = frozenset({"genome"})

# Root-level runtime globs (not directories)
ROOT_GLOBS = (
    "*.csv",
    "*.jsonl",
    "*.log",
    "*.db",
    "*_report.json",
    "*_scorecard.json",
    "research_compact_summary.json",
    "report_manifest.json",
    "real_edge_summary.json",
    "pathway_scorecard.json",
    "pathway_lane_specs.json",
    "analysis_dashboard.html",
    "executive_summary.txt",
    "research_findings.txt",
    "research_highlights.txt",
    "research_coverage.txt",
    "research_deep_dive_index.txt",
    "crash_dump.json",
    "open_positions.json",
    "repo_version_sync.json",
    "bot_analyzer_sync.json",
    "*.zip",
)

KEEP_ROOT_FILES = frozenset({
    "package.json",
    "vercel.json",
    "railway.toml",
})


def _rm_dir(path: Path) -> tuple[bool, str]:
    if not path.exists():
        return True, "absent"
    try:
        shutil.rmtree(path)
        return True, "removed"
    except Exception as e:
        return False, str(e)


def _rm_file(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return True, "absent"
    try:
        path.unlink()
        return True, "removed"
    except Exception as e:
        return False, str(e)


def wipe_location(base: Path, label: str) -> dict:
    removed_dirs = []
    removed_files = []
    errors = []

    for name in WIPE_DIRS:
        ok, msg = _rm_dir(base / name)
        if ok and msg == "removed":
            removed_dirs.append(name)
        elif not ok:
            errors.append(f"{label}/{name}: {msg}")

    # research/: wipe EVERYTHING inside except preserved subdirs (genome = Genome store).
    # This is the bulk (~27GB / 478k files). Without this, fresh-collection never cleared
    # the accumulated raw data — which was the root cause of "fresh collection not working".
    research_dir = base / "research"
    if research_dir.is_dir():
        for child in research_dir.iterdir():
            if child.name in PRESERVE_INSIDE_RESEARCH:
                continue
            # Preserve source .py files at research/ root — research/ holds the CANONICAL
            # source (research_dashboard.py, analyzer_research_engine_v62.py,
            # research_trade_accumulator.py, etc.) that the bot + analyzer import via root
            # shims. Wiping them breaks analyzer dashboard startup (FileNotFoundError on
            # research\research_dashboard.py). Only DATA files (csv/jsonl/db/json/txt/html)
            # and DATA subdirs are wiped; source + genome/ survive.
            if child.is_file() and child.suffix == ".py":
                continue
            if child.is_dir():
                ok, msg = _rm_dir(child)
            else:
                ok, msg = _rm_file(child)
            if ok and msg == "removed":
                removed_dirs.append(f"research/{child.name}")
            elif not ok:
                errors.append(f"{label}/research/{child.name}: {msg}")

    for pattern in ROOT_GLOBS:
        for p in base.glob(pattern):
            if p.name in KEEP_ROOT_FILES:
                continue
            if p.is_dir():
                ok, msg = _rm_dir(p)
            else:
                ok, msg = _rm_file(p)
            if ok and msg == "removed":
                removed_files.append(str(p.relative_to(base)))
            elif not ok:
                errors.append(f"{label}/{p.name}: {msg}")

    return {"dirs": removed_dirs, "files": len(removed_files), "errors": errors}


def write_fresh_session(base: Path) -> None:
    now = time.time()
    iso_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    payload = {
        "bot_version": VERSION,
        "analyzer_sync_id": VERSION,
        "bot_start_time": now,
        "bot_start_iso_utc": iso_utc,
        "fresh_collection_mode": True,
        "fresh_collection_start_time": now,
        "fresh_collection_start_iso_utc": iso_utc,
        "fresh_collection_start_iso": iso_utc,
        "display_timezone": "Australia/Melbourne",
        "cwd": str(base.resolve()),
        "wipe_reason": "manual_fresh_start_v983_week_collection",
        "wipe_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    (base / "research_session.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def init_accumulator(base: Path) -> dict:
    os.chdir(base)
    try:
        from research_trade_accumulator import init_db, sync_accumulator, _status_path

        acc_dir = base / "research_accumulator"
        if acc_dir.exists():
            shutil.rmtree(acc_dir, ignore_errors=True)
        init_db(base)
        return sync_accumulator(session=json.loads((base / "research_session.json").read_text()), root=base)
    except Exception as e:
        return {"error": str(e)}


def _has_open_live_positions(base: Path) -> bool:
    """Refuse a fresh wipe if live Bitfinex trading is armed AND there are open positions
    (wiping would orphan real exchange positions)."""
    if (os.environ.get("BITFINEX_LIVE_ENABLED") or "").strip().lower() != "true":
        return False
    op = base / "open_positions.json"
    if not op.is_file():
        return False
    try:
        data = json.loads(op.read_text(encoding="utf-8"))
        positions = data.get("positions") if isinstance(data, dict) else data
        return bool(positions)
    except Exception:
        return False


def main():
    print("=== FRESH START WIPE — v9.83 week collection ===\n")
    # Safety gate: never wipe while live Bitfinex trades are open (would orphan real positions).
    for base, label in ((ROOT, "Final Bots"),):
        if base.is_dir() and _has_open_live_positions(base):
            print(f"ABORT: BITFINEX_LIVE_ENABLED=true and open positions exist in {label}.")
            print("       Close all live positions / disarm live trading before fresh collection.")
            return
    results = {}
    for base, label in ((ROOT, "Final Bots"), (AGENT, "agent")):
        if base.is_dir():
            results[label] = wipe_location(base, label)
            write_fresh_session(base)
            print(f"{label}: removed {len(results[label]['dirs'])} dirs, {results[label]['files']} files")
            for err in results[label]["errors"][:10]:
                print(f"  WARN {err}")
            if len(results[label]["errors"]) > 10:
                print(f"  ⚠ ... and {len(results[label]['errors']) - 10} more errors")

    os.chdir(ROOT)
    acc = init_accumulator(ROOT)
    if AGENT.is_dir():
        init_accumulator(AGENT)
    print(f"\nAccumulator (root): {acc}")
    print("\nDone. Restart bot + analyzer from Final Bots root:")
    print("  python 15minu_bot.py")
    print("  python analyzer_research_engine_v62.py")
    summary_path = ROOT / "FRESH_START_WIPE.json"
    summary_path.write_text(
        json.dumps({"wiped_at": datetime.now(timezone.utc).isoformat(), "results": results, "accumulator": acc}, indent=2),
        encoding="utf-8",
    )
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()

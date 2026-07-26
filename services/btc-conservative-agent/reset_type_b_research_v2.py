#!/usr/bin/env python3
"""Destructively reset obsolete research data and initialize Type-B Research V2.

This tool deliberately does not touch exchange/relay state, open positions,
credentials, or source files. It is dry-run unless all explicit safety flags
are supplied.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from research_opportunity_v2 import (
    COLLECTION_ID,
    EVENT_FILE,
    REPORT_FILE,
    SCHEMA_VERSION,
    summarize,
)


CONFIRMATION = "DESTROY_OLD_RESEARCH_V1"
RUNTIME_DIRS = (
    "research_session_archives",
    "research_accumulator",
    "research_archive",
    "reports",
    "downloads",
    "overnight_analysis",
    "local-collection-data",
    "research_retention",
    "past_analysis",
)
RESEARCH_SUBDIRS = (
    "reports",
    "research_accumulator",
    "research_session_archives",
    "downloads",
    "backups",
)
ROOT_DATA_SUFFIXES = (
    ".csv", ".jsonl", ".db", ".db-journal", ".db-shm", ".db-wal", ".log", ".zip",
)
ROOT_REPORT_SUFFIXES = (
    "_report.json",
    "_scorecard.json",
    "_summary.json",
    "_validation.json",
    "_heatmap.json",
    "_capture.json",
    "_funnel.json",
    "_leaderboard.json",
    "_policy.json",
    "_integrity.json",
    "_audit.json",
)
ROOT_RESEARCH_FILES = {
    ".research_retention_last_run.json",
    "ai_confidence_expectancy.json",
    "research_session.json",
    "research_session_index.json",
    "research_retention_status.json",
    "report_manifest.json",
    "bot_analyzer_sync.json",
    "approve_outcome_confidence_direction.json",
    "scenario_c_capture_ratio.json",
    "tile2_counters.json",
    "pathway_lane_specs.json",
    "crash_dump.json",
    "lane_pnl_ledger.json",
    "lane_lab_pnl_ledger.json",
    "analysis_dashboard.html",
    "executive_summary.txt",
    "research_findings.txt",
    "research_highlights.txt",
    "research_coverage.txt",
    "research_deep_dive_index.txt",
    "repo_version_sync.json",
}
GENOME_RUNTIME_FILES = {
    "data_integrity_audit.json",
    "genome_analysis_report.json",
    "genome_discoveries.json",
    "genome_library.json",
}
PRESERVE_EXACT = {
    ".env",
    "bitfinex_live_state.json",
    "open_positions.json",
    "persistent_config.json",
    "manifest.json",
}
PRESERVE_PREFIXES = (
    "config-",
    "relay_",
    "bitfinex_",
    "exchange_",
    "webhook_",
    "virtual_",
)


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_agent_root(agent_root: Path) -> Path:
    root = agent_root.resolve()
    required = (root / "bot.py", root / "research_opportunity_v2.py", root / "research")
    if not all(path.exists() for path in required):
        raise RuntimeError(f"refusing reset: not a btc-conservative-agent root: {root}")
    return root


def _is_research_data_file(name: str) -> bool:
    lower = name.lower()
    return (
        lower.endswith(ROOT_DATA_SUFFIXES)
        or bool(re.fullmatch(r".+\.(?:jsonl|csv)\.\d+", lower))
    )


def _runtime_targets(root: Path) -> list[Path]:
    targets: list[Path] = []
    for name in RUNTIME_DIRS:
        path = root / name
        if path.exists():
            targets.append(path)
    research = root / "research"
    for name in RESEARCH_SUBDIRS:
        path = research / name
        if path.exists():
            targets.append(path)
    if research.is_dir():
        for child in research.iterdir():
            if child.is_file() and (
                child.suffix.lower() in {".json", ".jsonl", ".txt", ".html", ".csv", ".db"}
                or _is_research_data_file(child.name)
            ):
                if child.name != "README.md":
                    targets.append(child)
    genome = research / "genome"
    for name in GENOME_RUNTIME_FILES:
        path = genome / name
        if path.exists():
            targets.append(path)
    if genome.is_dir():
        targets.extend(
            path for path in genome.iterdir()
            if path.is_file() and _is_research_data_file(path.name)
        )
    for child in root.iterdir():
        if not child.is_file():
            continue
        name = child.name
        lower = name.lower()
        if name in PRESERVE_EXACT or lower.startswith(PRESERVE_PREFIXES):
            continue
        if (
            _is_research_data_file(name)
            or lower.endswith(ROOT_REPORT_SUFFIXES)
            or name == EVENT_FILE
            or name.startswith(f"{EVENT_FILE}.")
            or name in ROOT_RESEARCH_FILES
        ):
            targets.append(child)
    # Stable order, no duplicate paths, and never permit a target outside the agent.
    unique = []
    seen = set()
    for path in targets:
        resolved = path.resolve()
        if not _inside(resolved, root):
            raise RuntimeError(f"refusing out-of-root reset target: {resolved}")
        key = str(resolved).lower()
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return sorted(unique, key=lambda path: (len(path.parts), str(path).lower()), reverse=True)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def assert_writers_stopped(repo_root: Path) -> None:
    repo = repo_root.resolve()
    if not repo.is_dir():
        raise RuntimeError(f"refusing reset: invalid --repo-root: {repo}")
    if not (repo / ".home-stack-user-stopped").is_file():
        raise RuntimeError(
            "refusing reset: .home-stack-user-stopped is missing; stop the home stack first"
        )
    # Include every process that can write or auto-relaunch a writer. Checking
    # only the bot/analyzer PIDs leaves a race where the supervisor or crash
    # monitor recreates them while the destructive reset is in progress.
    for name in (
        ".home-bot.pid",
        ".home-bot-starter.pid",
        ".home-bot-crash-monitor.pid",
        ".home-analyzer.pid",
        ".home-analyzer-starter.pid",
        ".home-analyzer-dashboard.pid",
        ".home-analyzer-crash-monitor.pid",
        ".home-stack-supervisor.pid",
    ):
        pid_file = repo / name
        if not pid_file.is_file():
            continue
        try:
            pid = int(pid_file.read_text(encoding="utf-8-sig").strip())
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"refusing reset: ambiguous writer PID file {pid_file}: {exc}") from exc
        if _pid_alive(pid):
            raise RuntimeError(f"refusing reset: writer PID {pid} from {pid_file} is still alive")
    # Check twice so a monitor racing the first check cannot relaunch a writer
    # unnoticed. These are the only two destructive-reset writer endpoints.
    for attempt in range(2):
        open_ports = [port for port in (7002, 9001) if _port_open(port)]
        if open_ports:
            raise RuntimeError(f"refusing reset: writer port(s) still open: {open_ports}")
        if attempt == 0:
            time.sleep(0.5)


def assert_source_flat(root: Path) -> None:
    """Do not let a pre-reset source position close into the new cohort."""
    path = root / "open_positions.json"
    if not path.is_file():
        raise RuntimeError("refusing reset: open_positions.json is missing; source flatness is unproven")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"refusing reset: cannot verify open_positions.json: {exc}") from exc
    if isinstance(payload, list):
        positions = payload
    elif isinstance(payload, dict):
        if "positions" in payload:
            positions = payload.get("positions")
        elif not payload:
            positions = []
        else:
            raise RuntimeError(
                "refusing reset: ambiguous nonempty open_positions.json object"
            )
    else:
        raise RuntimeError("refusing reset: unsupported open_positions.json shape")
    if not isinstance(positions, list):
        raise RuntimeError("refusing reset: ambiguous open_positions.json positions payload")
    open_rows = [
        row for row in positions
        if not isinstance(row, dict)
        or str(row.get("status") or "OPEN").upper() not in {"CLOSED", "EXPIRED", "CANCELLED"}
    ]
    if open_rows:
        raise RuntimeError(
            f"refusing reset: source has {len(open_rows)} open/carryover position(s)"
        )


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def initialize_v2(root: Path) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    session = {
        "collection_id": COLLECTION_ID,
        "research_schema": SCHEMA_VERSION,
        "fresh_collection_mode": True,
        "fresh_collection_start_iso": now,
        "wipe_reason": "approved_type_b_research_v2_clean_reset",
        "legacy_data_retained": False,
    }
    (root / "research_session.json").write_text(
        json.dumps(session, indent=2) + "\n", encoding="utf-8"
    )
    (root / EVENT_FILE).write_text("", encoding="utf-8")
    report = summarize([])
    (root / REPORT_FILE).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return session


def reset(
    agent_root: Path,
    *,
    execute: bool,
    confirmation: str,
    writers_stopped: bool,
    repo_root: Path | None = None,
) -> dict:
    root = validate_agent_root(agent_root)
    targets = _runtime_targets(root)
    result = {
        "agent_root": str(root),
        "execute": bool(execute),
        "targets": [str(path) for path in targets],
        "removed": [],
        "initialized": False,
    }
    if not execute:
        return result
    if confirmation != CONFIRMATION:
        raise RuntimeError("refusing reset: exact confirmation phrase was not supplied")
    if not writers_stopped:
        raise RuntimeError("refusing reset: --writers-stopped is required")
    if repo_root is None:
        raise RuntimeError("refusing reset: --repo-root is required for writer verification")
    assert_writers_stopped(repo_root)
    assert_source_flat(root)
    for path in targets:
        _remove(path)
        result["removed"].append(str(path))
    initialize_v2(root)
    result["initialized"] = True
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--repo-root", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--confirm", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = reset(
        args.agent_root,
        execute=args.execute,
        confirmation=args.confirm,
        writers_stopped=args.writers_stopped,
        repo_root=args.repo_root,
    )
    print(json.dumps(result, indent=2))
    if not args.execute:
        print(f"DRY RUN. Execute only after stopping both writers: --execute --writers-stopped --confirm {CONFIRMATION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

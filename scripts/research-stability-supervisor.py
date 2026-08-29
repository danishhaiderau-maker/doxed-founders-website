#!/usr/bin/env python3
"""Fail-closed supervisor for the desktop BTC research pipeline.

This process has deliberately narrow authority.  It may start a missing Fly
mirror loop, start a missing desktop analyzer, or refresh one revision-stale
analyzer after exact mirror parity through their existing launchers.  It cannot
stop/restart trading, call Fly mutation endpoints, wipe data, or change policy.
Every observation is written atomically for the dashboard/operator.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import re
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPORT_MAX_AGE_SECONDS = 45 * 60
SYNC_MAX_AGE_SECONDS = 10 * 60
FLY_MANIFEST_TIMEOUT_SECONDS = 60


class SupervisorLockUnavailable(RuntimeError):
    """Raised only when another supervisor already owns the process lock."""


@contextmanager
def exclusive_process_lock(path: Path):
    """Hold one cross-platform byte lock for the supervisor lifetime."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            raise SupervisorLockUnavailable(str(path)) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()).encode("ascii"))
        handle.flush()
        yield handle
    finally:
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
        handle.close()
PARTIAL_ARTIFACT_STALE_SECONDS = SYNC_MAX_AGE_SECONDS
MAX_PENDING_EVENT_DELTA = 100
READINESS_STARVATION_THRESHOLD_SECONDS = 15 * 60
OPPORTUNITY_STALL_THRESHOLD_SECONDS = 12 * 60
LOCAL_STORAGE_GREEN_FREE_BYTES = 150 * 1024**3
LOCAL_STORAGE_AMBER_FREE_BYTES = 100 * 1024**3
LOCAL_TEMP_ABNORMAL_GROWTH_BYTES = 1024**3
LOCAL_QUARANTINE_MAX_PERCENT = 10.0
LOCAL_MIRROR_MAX_BYTES = 25 * 1024**3
LOCAL_QUARANTINE_MAX_BYTES = 25 * 1024**3
REQUIRED_SCHEMA = "research_event_v2.2"
REQUIRED_COLLECTOR = "collector_v2.2"


def local_tile_registry_contract(repo: Path) -> tuple[list[str], str]:
    """Load the canonical registry without maintaining a monitor-side roster."""
    values = runpy.run_path(
        str(repo / "services" / "btc-conservative-agent" / "combo_pathway_config.py")
    )
    lanes = list(values["ACTIVE_TILE_ORDER"])
    signature = str(values["active_tile_registry_signature"]())
    return lanes, signature


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} is not a JSON object")
    return value


def directory_size(path: Path) -> tuple[int, int]:
    """Return best-effort file count and bytes without following directory links."""
    files = 0
    total = 0
    if not path.is_dir():
        return files, total
    for candidate in path.rglob("*"):
        try:
            if candidate.is_file() and not candidate.is_symlink():
                files += 1
                total += candidate.stat().st_size
        except OSError:
            # Atomic mirror replacement can remove a file between enumeration
            # and stat. The next five-minute check will observe its replacement.
            continue
    return files, total


def local_storage_snapshot(
    mirror: Path,
    *,
    report_dir: Path | None = None,
    temp_dir: Path | None = None,
    previous_snapshot: dict[str, Any] | None = None,
    disk_usage: Callable[[str | os.PathLike[str]], Any] = shutil.disk_usage,
) -> tuple[bool, dict[str, Any]]:
    """Measure known generated-data roots without deleting or modifying them.

    The boolean is deliberately GREEN-only because storage health participates
    in technical readiness.  AMBER remains usable for bounded repair and
    collection, but it must be visible to the operator rather than passing as
    fully healthy.
    """
    quarantine = mirror.parent / "fly-data-quarantine"
    reports = report_dir or mirror.parent / "analyzer-reports"
    processing_temp = temp_dir or mirror.parent / "temp"
    sync_staging = mirror.parent / "fly-data-staging"
    mirror_files, mirror_bytes = directory_size(mirror)
    quarantine_files, quarantine_bytes = directory_size(quarantine)
    report_files, report_bytes = directory_size(reports)
    temp_files, temp_bytes = directory_size(processing_temp)
    staging_files, staging_bytes = directory_size(sync_staging)
    total, _used, free = disk_usage(mirror.parent)
    free_pct = (float(free) / float(total) * 100.0) if total else 0.0
    quarantine_pct = (float(quarantine_bytes) / float(total) * 100.0) if total else 100.0
    if free >= LOCAL_STORAGE_GREEN_FREE_BYTES:
        rag = "GREEN"
    elif free >= LOCAL_STORAGE_AMBER_FREE_BYTES:
        rag = "AMBER"
    else:
        rag = "RED"

    previous_temp_bytes = int((previous_snapshot or {}).get("temporary_bytes") or 0)
    temp_growth_bytes = temp_bytes - previous_temp_bytes if previous_snapshot else 0
    abnormal_temp_growth = bool(previous_snapshot) and temp_growth_bytes >= LOCAL_TEMP_ABNORMAL_GROWTH_BYTES
    consumers = [
        {"name": "active_fly_mirror", "path": str(mirror), "files": mirror_files, "bytes": mirror_bytes},
        {"name": "fly_data_quarantine", "path": str(quarantine), "files": quarantine_files, "bytes": quarantine_bytes},
        {"name": "analyzer_reports", "path": str(reports), "files": report_files, "bytes": report_bytes},
        {"name": "temporary_processing", "path": str(processing_temp), "files": temp_files, "bytes": temp_bytes},
        {"name": "mirror_sync_staging", "path": str(sync_staging), "files": staging_files, "bytes": staging_bytes},
    ]
    consumers.sort(key=lambda row: (-int(row["bytes"]), str(row["name"])))
    generated_roots_within_caps = (
        mirror_bytes <= LOCAL_MIRROR_MAX_BYTES
        and quarantine_bytes <= LOCAL_QUARANTINE_MAX_BYTES
        and quarantine_pct <= LOCAL_QUARANTINE_MAX_PERCENT
    )
    ok = rag == "GREEN" and generated_roots_within_caps and not abnormal_temp_growth
    return ok, {
        "rag": rag,
        "mirror_files": mirror_files,
        "mirror_bytes": mirror_bytes,
        "quarantine_files": quarantine_files,
        "quarantine_bytes": quarantine_bytes,
        "analyzer_report_files": report_files,
        "analyzer_report_bytes": report_bytes,
        "temporary_files": temp_files,
        "temporary_bytes": temp_bytes,
        "temporary_growth_bytes": temp_growth_bytes,
        "temporary_growth_rag": "AMBER" if abnormal_temp_growth else "GREEN",
        "temporary_growth_alert": abnormal_temp_growth,
        "temporary_growth_alert_bytes": LOCAL_TEMP_ABNORMAL_GROWTH_BYTES,
        "five_largest_known_generated_data_consumers": consumers[:5],
        "disk_total_bytes": int(total),
        "disk_free_bytes": int(free),
        "disk_free_gib": round(float(free) / 1024**3, 2),
        "disk_free_percent": round(free_pct, 2),
        "quarantine_disk_percent": round(quarantine_pct, 3),
        "green_minimum_free_bytes": LOCAL_STORAGE_GREEN_FREE_BYTES,
        "amber_minimum_free_bytes": LOCAL_STORAGE_AMBER_FREE_BYTES,
        "rag_rule": "GREEN >=150 GiB; AMBER 100-149 GiB; RED <100 GiB",
        "generated_roots_within_caps": generated_roots_within_caps,
        "maximum_mirror_bytes": LOCAL_MIRROR_MAX_BYTES,
        "maximum_quarantine_bytes": LOCAL_QUARANTINE_MAX_BYTES,
        "maximum_quarantine_percent": LOCAL_QUARANTINE_MAX_PERCENT,
        "retention_action": "QUARANTINE_AND_REVIEW; NEVER_SILENTLY_DELETE",
        "automatic_delete": False,
    }


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def evaluate_opportunity_progress(
    *,
    count: int,
    epoch_ids: list[str],
    source_revision: str | None,
    prior: dict[str, Any],
    now: datetime,
    progress_expected: bool,
) -> tuple[bool, dict[str, Any], dict[str, Any]]:
    """Persistently prove that independent research opportunities advance.

    Mirror freshness alone is insufficient: unrelated files can keep syncing
    while the three-minute AI/opportunity pipeline is stalled.  A new epoch or
    deployed revision establishes a new baseline.  Paused or otherwise
    non-expected collection is reported explicitly and never accumulates a
    false stall duration.
    """
    epoch_key = ",".join(sorted(str(value) for value in epoch_ids if value))
    revision = str(source_revision or "")
    prior_count = prior.get("independent_opportunities")
    same_identity = (
        prior.get("epoch_key") == epoch_key
        and prior.get("source_revision") == revision
    )
    previous_observed = parse_time(prior.get("observed_at"))
    advanced = bool(
        same_identity
        and isinstance(prior_count, int)
        and count > prior_count
    )

    if not progress_expected:
        first_stalled = None
        state = "PROGRESS_NOT_EXPECTED"
    elif not same_identity or not isinstance(prior_count, int):
        first_stalled = None
        state = "BASELINE_ESTABLISHED"
    elif advanced:
        first_stalled = None
        state = "ADVANCING"
    else:
        first_stalled = parse_time(prior.get("first_stalled_at"))
        if first_stalled is None:
            first_stalled = previous_observed or now
        state = "STALLED"

    stalled_for = max(0.0, (now - first_stalled).total_seconds()) if first_stalled else 0.0
    failed = bool(progress_expected and state == "STALLED" and stalled_for >= OPPORTUNITY_STALL_THRESHOLD_SECONDS)
    if failed:
        state = "OPPORTUNITY_PROGRESS_STALLED"

    next_state = {
        "schema": "research_opportunity_progress_state_v1",
        "observed_at": now.isoformat(),
        "epoch_key": epoch_key,
        "source_revision": revision,
        "independent_opportunities": int(count),
        "first_stalled_at": first_stalled.isoformat() if first_stalled else None,
        "stalled_duration_seconds": round(stalled_for, 1),
        "state": state,
        "progress_expected": bool(progress_expected),
    }
    detail = {
        **next_state,
        "previous_independent_opportunities": prior_count if same_identity else None,
        "advanced": advanced,
        "threshold_seconds": OPPORTUNITY_STALL_THRESHOLD_SECONDS,
    }
    return not failed, detail, next_state


def fetch_json(url: str, token: str, timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"X-Bot-Admin-Token": token})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise ValueError("remote response is not a JSON object")
    return value


@contextmanager
def open_replace_safe(path: Path):
    """Open evidence for reading without blocking an atomic mirror replace.

    Python's normal Windows file open does not grant FILE_SHARE_DELETE.  A
    supervisor scan of a large JSONL could therefore make the sync publisher's
    File.Replace fail even though the supervisor is read-only.  The explicit
    Windows handle keeps read/write/delete sharing enabled; other platforms use
    the ordinary context-managed reader.
    """
    if os.name != "nt":
        with path.open("rb") as handle:
            yield handle
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    create_file = ctypes.WinDLL("kernel32", use_last_error=True).CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = ctypes.WinDLL("kernel32", use_last_error=True).CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    raw_handle = create_file(
        str(path),
        0x80000000,              # GENERIC_READ
        0x00000001 | 0x00000002 | 0x00000004,  # READ | WRITE | DELETE sharing
        None,
        3,                       # OPEN_EXISTING
        0x00000080,              # FILE_ATTRIBUTE_NORMAL
        None,
    )
    invalid_handle = wintypes.HANDLE(-1).value
    if raw_handle == invalid_handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(int(raw_handle), os.O_RDONLY | os.O_BINARY)
    except Exception:
        close_handle(raw_handle)
        raise
    with os.fdopen(fd, "rb", closefd=True) as handle:
        yield handle


def read_replace_safe_bytes(path: Path) -> bytes:
    with open_replace_safe(path) as handle:
        return handle.read()


def process_inventory() -> list[dict[str, Any]]:
    command = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    raw = json.loads(result.stdout or "[]")
    return raw if isinstance(raw, list) else [raw]


def classify_processes(rows: list[dict[str, Any]]) -> dict[str, list[int]]:
    groups = {"sync": [], "analyzer": [], "dashboard": [], "supervisor": []}
    parent_by_pid: dict[int, int] = {}
    for row in rows:
        cmd = str(row.get("CommandLine") or "").lower()
        name = str(row.get("Name") or "").lower()
        pid = int(row.get("ProcessId") or 0)
        if pid <= 0:
            continue
        parent_by_pid[pid] = int(row.get("ParentProcessId") or 0)
        is_python = not name or "python" in name
        is_powershell = not name or "powershell" in name or name.startswith("pwsh")
        if is_powershell and "sync-fly-bot-data-loop.ps1" in cmd:
            groups["sync"].append(pid)
        if is_python and "analyzer_research_engine_v62.py" in cmd:
            groups["analyzer"].append(pid)
        if is_python and "research_dashboard.py" in cmd and "--standalone" in cmd:
            groups["dashboard"].append(pid)
        # Only the long-running --loop instance owns continuous supervision.
        # One-shot audits intentionally run alongside it and must not create a
        # false duplicate-supervisor alert.
        if (
            is_python
            and "research-stability-supervisor.py" in cmd
            and "--loop" in cmd.split()
        ):
            groups["supervisor"].append(pid)
    logical_groups: dict[str, list[int]] = {}
    for key, values in groups.items():
        members = set(values)
        # A pwsh launcher commonly starts powershell.exe with the same sync
        # script. They are one worker tree, not two independent sync loops.
        # Count only roots whose matched parent is not another member.
        def has_member_ancestor(pid: int) -> bool:
            seen: set[int] = set()
            parent = parent_by_pid.get(pid, 0)
            while parent > 0 and parent not in seen:
                if parent in members:
                    return True
                seen.add(parent)
                parent = parent_by_pid.get(parent, 0)
            return False

        roots = [pid for pid in members if not has_member_ancestor(pid)]
        logical_groups[key] = sorted(roots)
    return logical_groups


def expected_process_count(kind: str, count: int, require_loop_owner: bool) -> tuple[bool, str]:
    if kind == "supervisor" and not require_loop_owner:
        return count <= 1, "zero_or_one_loop_owner"
    return count == 1, "exactly_one"


def read_current_events(path: Path) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    # Release the Windows mirror handle before schema validation/counting. A
    # supervisor pass must never block publication of the next generation.
    payload = read_replace_safe_bytes(path)
    for line_number, raw_line in enumerate(payload.splitlines(), 1):
        line = raw_line.decode("utf-8-sig", errors="strict")
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"event line {line_number} is not an object")
        if value.get("schema") != REQUIRED_SCHEMA or value.get("collector_version") != REQUIRED_COLLECTOR:
            raise ValueError(f"event line {line_number} is not qualified v2.2")
        events.append(value)
    if not events:
        raise ValueError("no v2.2 events")
    epoch = str(events[-1].get("epoch_id") or "")
    current = [row for row in events if str(row.get("epoch_id") or "") == epoch]
    completed = [
        row for row in current
        if str(row.get("observation_status") or "").upper() in {"COMPLETE", "FUNNEL_COMPLETE"}
    ]
    all_episode_ids = {
        str(row.get("event_episode_id") or row.get("envelope", {}).get("event_episode_id") or "")
        for row in current
    }
    all_episode_ids.discard("")
    eligible_episode_ids = {
        str(row.get("event_episode_id") or row.get("envelope", {}).get("event_episode_id") or "")
        for row in completed
    }
    eligible_episode_ids.discard("")
    policy_epochs = {str(row.get("policy_epoch_id") or "") for row in current}
    signatures = {str(row.get("policy_signature") or "") for row in current}
    policy_epochs.discard("")
    signatures.discard("")
    return {
        "epoch_id": epoch,
        "current_events": len(current),
        "eligible_events": len(completed),
        "ineligible_events": len(current) - len(completed),
        "all_independent_episodes": len(all_episode_ids),
        "eligible_independent_episodes": len(eligible_episode_ids),
        "policy_epoch_ids": sorted(policy_epochs),
        "policy_signatures": sorted(signatures),
        "_all_event_ids": [str(row.get("event_id") or "") for row in events],
    }


def read_v3_evidence(mirror: Path, *, now_ts: float | None = None) -> dict[str, Any]:
    """Read normalized V3 ledgers without holding mirror-replace handles."""
    ledger_dir = mirror / "v3" / "ledgers"
    counts: dict[str, int] = {}
    episodes: set[str] = set()
    epochs: set[str] = set()
    terminal = provisional = 0
    opportunity_rows: list[dict[str, Any]] = []
    rows_by_ledger: dict[str, list[dict[str, Any]]] = {}
    for name in ("opportunity", "decision", "order_intent", "execution", "market_segment", "lifecycle"):
        path = ledger_dir / f"{name}.jsonl"
        rows = []
        if path.is_file():
            payload = read_replace_safe_bytes(path)
            if payload and not payload.endswith(b"\n"):
                raise ValueError(f"V3_TRUNCATED_LEDGER:{name}")
            for line_number, raw in enumerate(payload.splitlines(), 1):
                row = json.loads(raw.decode("utf-8-sig", errors="strict"))
                if row.get("schema") != "research_evidence_v3" or row.get("ledger") != name:
                    raise ValueError(f"V3_SCHEMA_OR_LEDGER_MISMATCH:{name}:{line_number}")
                rows.append(row)
                if name == "opportunity":
                    opportunity_rows.append(row)
                if row.get("episode_id"):
                    episodes.add(str(row["episode_id"]))
                if row.get("epoch_id"):
                    epochs.add(str(row["epoch_id"]))
                if name == "lifecycle":
                    if row.get("terminal") is True:
                        terminal += 1
                    else:
                        provisional += 1
        counts[name] = len(rows)
        rows_by_ledger[name] = rows
    parents = list(range(len(opportunity_rows)))
    def find(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    def union(left: int, right: int) -> None:
        left, right = find(left), find(right)
        if left != right:
            parents[right] = left
    first_by_shared: dict[str, int] = {}
    first_by_fingerprint: dict[tuple[float, str, str], int] = {}
    for index, row in enumerate(opportunity_rows):
        shared = str(row.get("shared_ai_call_id") or "").strip()
        if shared:
            if shared in first_by_shared:
                union(index, first_by_shared[shared])
            else:
                first_by_shared[shared] = index
        try:
            signal_ts = float(row.get("signal_ts"))
        except (TypeError, ValueError):
            signal_ts = -1.0
        fingerprint = (
            signal_ts,
            str(row.get("symbol") or "").upper(),
            str(row.get("raw_direction") or "").upper(),
        )
        if fingerprint in first_by_fingerprint:
            union(index, first_by_fingerprint[fingerprint])
        else:
            first_by_fingerprint[fingerprint] = index
    causal_groups: dict[int, list[dict[str, Any]]] = {}
    for index, row in enumerate(opportunity_rows):
        causal_groups.setdefault(find(index), []).append(row)
    identity_aliases = []
    for rows in causal_groups.values():
        if len(rows) <= 1:
            continue
        ordered = sorted(rows, key=lambda row: (
            0 if str(row.get("grouping_basis") or "") == "SHARED_AI_CALL" else 1,
            str(row.get("episode_id") or ""),
        ))
        identity_aliases.extend(ordered[1:])
    segment_root = mirror / "v3" / "market_segments"
    segment_files = list(segment_root.glob("*/*.json")) if segment_root.is_dir() else []
    def resolution_key(row: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(row.get("episode_id") or ""),
            str(row.get("policy_signature") or ""),
            str(row.get("research_lane") or "").upper(),
        )
    expected = [
        row for row in rows_by_ledger["decision"]
        if row.get("decision_stage") == "LANE_POLICY_VERDICT"
        and row.get("order_intent_expected") is True
    ]
    intent_keys = {resolution_key(row) for row in rows_by_ledger["order_intent"]}
    resolution_rows: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows_by_ledger["lifecycle"]:
        if row.get("resolution_scope") == "LANE_ENTRY":
            resolution_rows.setdefault(resolution_key(row), []).append(row)
    resolution_counts = {"submitted": 0, "terminal_no_order": 0,
                         "awaiting_within_deadline": 0, "overdue_orphan": 0}
    orphan_expected_orders = []
    observed_now = float(now_ts if now_ts is not None else time.time())
    for decision in expected:
        key = resolution_key(decision)
        rows = resolution_rows.get(key, [])
        states = {str(row.get("entry_resolution") or "") for row in rows}
        if key in intent_keys or "ORDER_SUBMITTED" in states:
            resolution_counts["submitted"] += 1
        elif "NO_ORDER" in states:
            resolution_counts["terminal_no_order"] += 1
        else:
            deadlines = [float(decision.get("resolution_deadline_ts") or 0)] + [
                float(row.get("resolution_deadline_ts") or 0)
                for row in rows if row.get("entry_resolution") == "AWAITING"
            ]
            deadline = max(deadlines)
            if deadline > observed_now:
                resolution_counts["awaiting_within_deadline"] += 1
            else:
                resolution_counts["overdue_orphan"] += 1
                orphan_expected_orders.append({
                    "episode_id": key[0], "policy_signature": key[1],
                    "research_lane": key[2], "resolution_deadline_ts": deadline or None,
                })
    policy_provenance_defects = []
    attributable_rows = [
        *(('execution', row) for row in rows_by_ledger["execution"]),
        *(("lifecycle", row) for row in rows_by_ledger["lifecycle"]
          if str(row.get("observation_status") or "") in {
              "PAPER_POSITION_OPEN", "PAPER_POSITION_CLOSED",
          }),
    ]
    for ledger, row in attributable_rows:
        missing = [field for field in (
            "policy_id", "policy_signature", "policy_epoch_id",
            "research_lane", "shared_ai_call_id",
        ) if not str(row.get(field) or "").strip()]
        if missing:
            policy_provenance_defects.append({
                "ledger": ledger,
                "record_id": str(row.get("record_id") or ""),
                "missing_fields": missing,
            })
    paper_scope_rows = [
        *(("decision", row) for row in rows_by_ledger["decision"]
          if str(row.get("decision_stage") or "") == "LANE_POLICY_VERDICT"),
        *(("order_intent", row) for row in rows_by_ledger["order_intent"]),
        *(("execution", row) for row in rows_by_ledger["execution"]),
        *(("lifecycle", row) for row in rows_by_ledger["lifecycle"]
          if str(row.get("observation_status") or "") in {
              "PAPER_POSITION_OPEN", "PAPER_POSITION_CLOSED",
          }),
    ]
    for ledger, row in paper_scope_rows:
        if str(row.get("policy_execution_scope") or "") != "PAPER_RESEARCH_ONLY":
            continue
        spec = row.get("paper_policy_spec")
        spec_paper_only = spec.get("paper_only") if isinstance(spec, dict) else None
        if row.get("paper_only") is not False and spec_paper_only is not False:
            continue
        policy_provenance_defects.append({
            "ledger": ledger,
            "record_id": str(row.get("record_id") or ""),
            "contradiction": "PAPER_SCOPE_WITH_FALSE_PAPER_ONLY",
            "top_level_paper_only": row.get("paper_only"),
            "spec_paper_only": spec_paper_only,
        })
    return {
        "ledger_counts": counts,
        "independent_opportunities": counts["opportunity"] - len(identity_aliases),
        "raw_opportunity_rows": counts["opportunity"],
        "identity_alias_count": len(identity_aliases),
        "identity_alias_episode_ids": sorted(str(row.get("episode_id") or "") for row in identity_aliases),
        "decision_branches": counts["decision"],
        "terminal_lifecycles": terminal,
        "provisional_lifecycles": provisional,
        "market_segments": len(segment_files),
        "episode_ids_seen_across_ledgers": len(episodes),
        "epoch_ids": sorted(epochs),
        "entry_resolution_integrity": {
            "expected": len(expected), **resolution_counts,
            "orphan_expected_orders": orphan_expected_orders,
            "passed": resolution_counts["overdue_orphan"] == 0,
        },
        "policy_provenance_integrity": {
            "checked_rows": len(paper_scope_rows),
            "defect_count": len(policy_provenance_defects),
            "defects": policy_provenance_defects,
            "passed": not policy_provenance_defects,
        },
    }


def shared_cycle_snapshot_prefix_ok(
    reports: dict[str, dict[str, Any]], event_summary: dict[str, Any],
) -> tuple[bool, dict[str, Any]]:
    """Prove that both reports used one immutable prefix of the live mirror."""
    snapshots = [report.get("cycle_snapshot") or {} for report in reports.values()]
    shared = bool(snapshots) and all(snapshot == snapshots[0] for snapshot in snapshots[1:])
    receipt = snapshots[0] if snapshots else {}
    row_count = int(receipt.get("row_count") or 0)
    event_ids = event_summary.get("_all_event_ids") or []
    prefix_terminal = event_ids[row_count - 1] if 0 < row_count <= len(event_ids) else None
    identity_ok = bool(
        receipt.get("schema") == "policy_cycle_snapshot_v1"
        and receipt.get("snapshot_id")
        and receipt.get("epoch_id") == event_summary.get("epoch_id")
        and receipt.get("policy_epoch_id") in event_summary.get("policy_epoch_ids", [])
        and receipt.get("policy_signature") in event_summary.get("policy_signatures", [])
    )
    ok = bool(
        shared and identity_ok and row_count > 0
        and receipt.get("last_event_id") == prefix_terminal
    )
    return ok, {
        "shared": shared,
        "identity_ok": identity_ok,
        "snapshot_id": receipt.get("snapshot_id"),
        "row_count": row_count,
        "mirror_row_count": len(event_ids),
        "last_event_id": receipt.get("last_event_id"),
        "prefix_terminal_event_id": prefix_terminal,
    }


def report_count_contract(filename: str, report: dict[str, Any]) -> dict[str, int]:
    """Return counts with the semantics declared by each report schema.

    Candidate selection operates only on replay-eligible paths, so its episode
    count is eligible-only.  Best-policy coverage deliberately includes
    negative/ineligible paths, so its episode count covers the whole epoch.
    Silently substituting one meaning for the other would create false health
    or false failure and is therefore rejected.
    """
    evidence = report.get("evidence") or {}
    if filename == "policy_candidate_oos_report.json":
        return {
            "current_events": int(evidence.get("current_events") or 0),
            "eligible_events": int(evidence.get("eligible_events") or 0),
            "eligible_independent_episodes": int(evidence.get("independent_episodes") or 0),
        }
    if filename == "best_policy_research_report.json":
        return {
            "current_events": int(evidence.get("current_epoch_events") or 0),
            "eligible_events": int(evidence.get("replay_eligible_events") or 0),
            "ineligible_events": int(evidence.get("replay_ineligible_events") or 0),
            "all_independent_episodes": int(evidence.get("independent_episode_count") or 0),
        }
    raise ValueError(f"unsupported report count contract: {filename}")


def bounded_pending_parity(
    expected: dict[str, dict[str, int]],
    observed: dict[str, dict[str, int]],
    *,
    reports_fresh: bool,
    mirror_after_reports: bool,
    snapshot_prefix_ok: bool,
    identity_ok: bool,
) -> tuple[bool, dict[str, Any]]:
    """Accept only append-only growth awaiting the next fresh analyzer cycle."""
    candidate_name = "policy_candidate_oos_report.json"
    best_name = "best_policy_research_report.json"
    candidate = observed[candidate_name]
    best = observed[best_name]
    candidate_expected = expected[candidate_name]
    best_expected = expected[best_name]

    baseline_consistent = (
        candidate["current_events"] == best["current_events"]
        and candidate["eligible_events"] == best["eligible_events"]
        and best["current_events"] == best["eligible_events"] + best["ineligible_events"]
        and candidate["eligible_events"] <= candidate["current_events"]
    )
    deltas = {
        "current_events": best_expected["current_events"] - best["current_events"],
        "eligible_events": best_expected["eligible_events"] - best["eligible_events"],
        "ineligible_events": best_expected["ineligible_events"] - best["ineligible_events"],
        "all_independent_episodes": (
            best_expected["all_independent_episodes"] - best["all_independent_episodes"]
        ),
        "eligible_independent_episodes": (
            candidate_expected["eligible_independent_episodes"]
            - candidate["eligible_independent_episodes"]
        ),
    }
    nonnegative = all(value >= 0 for value in deltas.values())
    bounded = (
        deltas["current_events"] <= MAX_PENDING_EVENT_DELTA
        and deltas["eligible_events"] <= deltas["current_events"]
        and deltas["ineligible_events"] <= deltas["current_events"]
        and deltas["eligible_events"] + deltas["ineligible_events"] == deltas["current_events"]
        and deltas["all_independent_episodes"] <= deltas["current_events"]
        and deltas["eligible_independent_episodes"] <= deltas["eligible_events"]
    )
    pending = (
        reports_fresh and (mirror_after_reports or snapshot_prefix_ok) and identity_ok
        and baseline_consistent and nonnegative and bounded
        and deltas["current_events"] > 0
    )
    return pending, {
        "status": "PENDING_NEXT_ANALYZER_CYCLE" if pending else "MISMATCH",
        "deltas": deltas,
        "reports_fresh": reports_fresh,
        "mirror_after_reports": mirror_after_reports,
        "snapshot_prefix_ok": snapshot_prefix_ok,
        "identity_ok": identity_ok,
        "baseline_consistent": baseline_consistent,
        "nonnegative": nonnegative,
        "bounded": bounded,
        "max_pending_event_delta": MAX_PENDING_EVENT_DELTA,
    }


def runtime_counts(payload: dict[str, Any]) -> dict[str, int | None]:
    def count_from_payload(
        source: dict[str, Any],
        explicit: tuple[str, ...],
        collections: tuple[str, ...],
    ) -> int | None:
        for key in explicit:
            if source.get(key) is not None:
                try:
                    return int(source[key])
                except (TypeError, ValueError):
                    return None
        for key in collections:
            if isinstance(source.get(key), list):
                return len(source[key])
        return None

    def count(explicit: tuple[str, ...], collections: tuple[str, ...]) -> int | None:
        return count_from_payload(payload, explicit, collections)

    strategy_progress = (
        payload.get("strategy_progress")
        if isinstance(payload.get("strategy_progress"), dict)
        else {}
    )
    top_level = {
        "virtual_count": count(
            ("virtual_count", "virtual_candidate_count", "active_signal_count"),
            ("virtual_chase_candidates", "active_signals"),
        ),
        "pending_count": count(
            ("pending_count", "pending_order_count"), ("pending_orders", "orders"),
        ),
        "position_count": count(
            ("position_count", "open_position_count"), ("positions",),
        ),
    }
    nested = {
        "virtual_count": count_from_payload(
            strategy_progress,
            ("virtual_count", "virtual_candidate_count", "active_signal_count"),
            ("virtual_chase_candidates", "active_signals"),
        ),
        "pending_count": count_from_payload(
            strategy_progress,
            ("pending_count", "pending_order_count", "pending_orders"),
            (),
        ),
        "position_count": count_from_payload(
            strategy_progress,
            ("position_count", "open_position_count", "open_positions"),
            (),
        ),
    }
    return {
        key: top_level[key] if top_level[key] is not None else nested[key]
        for key in top_level
    }


def mirror_partial_artifacts(
    mirror: Path, *, now_ts: float | None = None,
    stale_after_seconds: float = PARTIAL_ARTIFACT_STALE_SECONDS,
) -> list[str]:
    """Return abandoned atomic-sync candidates, not active transfer staging.

    The sync worker downloads into a unique ``.download`` path before one
    atomic replace.  Observing that fresh staging file mid-transfer is normal;
    only a candidate surviving beyond a full sync window is an integrity
    failure.  Analyzer discovery never includes either form.
    """
    if not mirror.is_dir():
        return []
    observed_now = float(now_ts if now_ts is not None else time.time())
    artifacts: list[str] = []
    for candidate in mirror.rglob("*"):
        try:
            if not candidate.is_file() or candidate.is_symlink():
                continue
        except OSError:
            continue
        name = candidate.name.lower()
        if name.endswith(".download") or name.endswith(".download.replace-backup"):
            try:
                age = max(0.0, observed_now - candidate.stat().st_mtime)
            except OSError:
                continue
            if age >= stale_after_seconds:
                artifacts.append(candidate.relative_to(mirror).as_posix())
    return sorted(artifacts)


def evaluate_runtime_readiness(
    status: dict[str, Any],
    prior: dict[str, Any],
    *,
    now: datetime,
    counts: dict[str, int | None],
) -> tuple[bool, dict[str, Any], dict[str, Any]]:
    runtime = status.get("runtime_readiness") or {}
    strategy_progress = (
        status.get("strategy_progress")
        if isinstance(status.get("strategy_progress"), dict)
        else {}
    )
    strategy_progress_failed = strategy_progress.get("ok") is False
    strategy_progress_reasons = [
        str(value) for value in (strategy_progress.get("reasons") or [])
    ]
    reasons = [str(value) for value in (runtime.get("readiness_reasons") or [])]
    signal_ready = bool(status.get("signal_generation_ready", runtime.get("signal_generation_ready")))
    process_alive = bool(status.get("process_alive"))
    data_reasons = {
        "NO_PRICE", "WS_NOT_READY", "REST_ENTRY_QUOTE_NOT_READY", "OHLCV_NOT_READY",
        "EMA_NOT_READY", "CANDLE_STALE", "BUFFERS_NOT_READY",
    }
    starvation_reasons = sorted(set(reasons) & data_reasons)
    paused_only = bool(set(reasons) & {"ADMIN_MANUAL_PAUSE", "EXECUTION_PAUSED"}) and not starvation_reasons
    stabilizing = (
        not signal_ready
        and bool(runtime.get("prerequisites_ready"))
        and "READINESS_STABILIZING" in reasons
        and not starvation_reasons
    )
    identity = str(status.get("git_rev") or status.get("bot_version") or "UNKNOWN")
    prior_identity = str(prior.get("runtime_identity") or "")
    prior_first = parse_time(prior.get("first_starved_at")) if prior_identity == identity else None

    if signal_ready or paused_only or stabilizing or not process_alive or not starvation_reasons:
        first = None
        duration = 0.0
    else:
        first = prior_first or now
        duration = max(0.0, (now - first).total_seconds())

    persistent = bool(first and duration >= READINESS_STARVATION_THRESHOLD_SECONDS)
    if not process_alive:
        state = "PROCESS_NOT_ALIVE"
    elif strategy_progress_failed:
        state = "STRATEGY_PROGRESS_FAILED"
    elif signal_ready:
        state = "READY"
    elif stabilizing:
        state = "STABILIZING"
    elif paused_only:
        state = "PAUSED_NOT_STARVATION"
    elif persistent:
        state = "PERSISTENT_COLLECTION_STARVATION"
    elif starvation_reasons:
        state = "TRANSIENT_NOT_READY"
    else:
        state = "NOT_READY_NON_DATA_REASON"

    next_state = {
        "schema": "research_runtime_readiness_state_v1",
        "runtime_identity": identity,
        "observed_at": now.isoformat(),
        "first_starved_at": first.isoformat() if first else None,
        "starved_duration_seconds": round(duration, 1),
        "state": state,
        "reasons": reasons,
    }
    detail = {
        **next_state,
        "threshold_seconds": READINESS_STARVATION_THRESHOLD_SECONDS,
        "process_alive": process_alive,
        "signal_generation_ready": signal_ready,
        "system_ready": bool(status.get("system_ready", runtime.get("system_ready"))),
        "ws_ready": bool(status.get("ws_ready", runtime.get("ws_transport_ready"))),
        "rest_entry_quote_ready": runtime.get("rest_entry_quote_ready"),
        "ohlcv_ready": status.get("ohlcv_ready", runtime.get("ohlcv_ready")),
        "ohlcv_age_sec": runtime.get("ohlcv_age_sec"),
        "readiness_reasons": reasons,
        "strategy_progress_ok": strategy_progress.get("ok"),
        "strategy_progress_reasons": strategy_progress_reasons,
        "trade_lock_available": strategy_progress.get("trade_lock_available"),
        "ws_age_sec": strategy_progress.get("ws_age_sec"),
        "ai_age_sec": strategy_progress.get("ai_age_sec"),
        **counts,
    }
    return process_alive and not persistent and not strategy_progress_failed, detail, next_state


@dataclass
class Supervisor:
    repo: Path
    mirror: Path
    report_dir: Path
    fly_url: str
    token: str
    repair: bool = False
    now: Callable[[], datetime] = utc_now
    fetcher: Callable[[str, str, int], dict[str, Any]] = fetch_json
    process_reader: Callable[[], list[dict[str, Any]]] = process_inventory
    launcher: Callable[..., subprocess.Popen[Any]] = subprocess.Popen
    runtime_repo: Path | None = None
    readiness_state_file: Path | None = None
    progress_state_file: Path | None = None
    storage_state_file: Path | None = None
    require_loop_owner: bool = True

    def launch_missing(self, kind: str) -> bool:
        if not self.repair:
            return False
        if kind == "sync":
            owner_repo = self.runtime_repo or self.repo
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(owner_repo / "scripts" / "sync-fly-bot-data-loop.ps1")]
        elif kind == "analyzer":
            args = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(self.repo / "scripts" / "start-home-analyzer.ps1"), "-NoWait", "-Port", "9001"]
        else:
            return False
        self.launcher(args, cwd=self.repo, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True

    def check(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        repairs: list[str] = []

        def add(name: str, ok: bool, detail: Any) -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        source_revision = None
        manifest_registry_signature = None
        manifest_tile_lanes: list[str] = []
        manifest: dict[str, Any] = {}
        try:
            manifest = self.fetcher(
                self.fly_url.rstrip("/") + "/api/data-sync/manifest",
                self.token,
                FLY_MANIFEST_TIMEOUT_SECONDS,
            )
            source_revision = manifest.get("source_git_rev") or manifest.get("source_revision")
            manifest_registry_signature = manifest.get("tile_registry_signature")
            manifest_tile_lanes = [
                str(row.get("lane") or "")
                for row in (manifest.get("active_tiles") or [])
                if isinstance(row, dict)
            ]
            add("fly_collector_manifest", bool(manifest.get("files")) and int(manifest.get("total_bytes") or 0) > 0, {
                "total_bytes": manifest.get("total_bytes"), "source_revision": source_revision,
                "fresh_collection_signal_ts": manifest.get("fresh_collection_signal_ts"),
                "tile_registry_signature": manifest_registry_signature,
                "active_tile_lanes": manifest_tile_lanes})
        except Exception as exc:
            add("fly_collector_manifest", False, type(exc).__name__)
        try:
            size = self.fetcher(self.fly_url.rstrip("/") + "/api/data_size", self.token, 20)
            pct = float(size.get("volume_pct") or 0)
            add("fly_storage", pct < 85.0, {"volume_pct": pct, "cleanup_status": size.get("cleanup_status")})
        except Exception as exc:
            add("fly_storage", False, type(exc).__name__)
        try:
            storage_path = self.storage_state_file or self.repo / ".research-storage-state.json"
            previous_storage: dict[str, Any] = {}
            if storage_path.is_file():
                previous_storage = read_json(storage_path)
            local_ok, local_detail = local_storage_snapshot(
                self.mirror,
                report_dir=self.report_dir,
                previous_snapshot=previous_storage,
            )
            atomic_json(storage_path, local_detail)
            add("local_storage", local_ok, local_detail)
        except Exception as exc:
            add("local_storage", False, f"{type(exc).__name__}: {exc}")
        try:
            partials = mirror_partial_artifacts(self.mirror)
            add("mirror_partial_artifacts", not partials, {
                "count": len(partials),
                "paths": partials[:20],
            })
        except Exception as exc:
            add("mirror_partial_artifacts", False, f"{type(exc).__name__}: {exc}")

        readiness_path = self.readiness_state_file or self.repo / ".research-runtime-readiness-state.json"
        status: dict[str, Any] = {}
        try:
            status = self.fetcher(self.fly_url.rstrip("/") + "/api/status", self.token, 20)
            count_payload = status
            counts = runtime_counts(count_payload)
            if any(value is None for value in counts.values()):
                try:
                    state_payload = self.fetcher(self.fly_url.rstrip("/") + "/api/state", self.token, 20)
                    state_counts = runtime_counts(state_payload)
                    counts = {key: counts[key] if counts[key] is not None else state_counts[key] for key in counts}
                except Exception:
                    pass
            try:
                prior_readiness = read_json(readiness_path)
            except (FileNotFoundError, ValueError, json.JSONDecodeError):
                prior_readiness = {}
            readiness_ok, readiness_detail, next_readiness = evaluate_runtime_readiness(
                status, prior_readiness, now=self.now(), counts=counts,
            )
            atomic_json(readiness_path, next_readiness)
            add("persistent_runtime_readiness", readiness_ok, readiness_detail)
        except Exception as exc:
            add("persistent_runtime_readiness", False, type(exc).__name__)

        heartbeat_path = self.mirror / ".fly-data-sync-loop.heartbeat.json"
        sync_revision = None
        sync_registry_signature = None
        try:
            heartbeat = read_json(heartbeat_path)
            # Progress heartbeats emitted during a long atomic download use
            # updatedAt.  Accept it as a backwards-compatible freshness
            # fallback so active sync work is not reported as infinitely
            # stale between completed-cycle heartbeats.
            stamp = parse_time(heartbeat.get("syncedAt") or heartbeat.get("updatedAt"))
            age = (self.now() - stamp).total_seconds() if stamp else float("inf")
            sync_revision = heartbeat.get("sourceRevision") or heartbeat.get("source_revision")
            sync_registry_signature = heartbeat.get("tileRegistrySignature") or heartbeat.get("tile_registry_signature")
            add("atomic_sync_heartbeat", heartbeat.get("ok") is True and age <= SYNC_MAX_AGE_SECONDS,
                {"age_seconds": round(age, 1), "ok": heartbeat.get("ok"), "sourceRevision": sync_revision,
                 "tileRegistrySignature": sync_registry_signature})
        except Exception as exc:
            add("atomic_sync_heartbeat", False, type(exc).__name__)
        revision_match = bool(source_revision and sync_revision and (
            source_revision == sync_revision
            or source_revision.startswith(sync_revision)
            or sync_revision.startswith(source_revision)
        ))
        add("fly_sync_revision_parity", revision_match, {
            "fly_source_revision": source_revision,
            "sync_source_revision": sync_revision,
        })
        try:
            expected_lanes, expected_registry_signature = local_tile_registry_contract(self.repo)
        except Exception as exc:
            expected_lanes, expected_registry_signature = [], ""
            add("local_tile_registry_contract", False, f"{type(exc).__name__}: {exc}")
        else:
            add("local_tile_registry_contract", bool(expected_lanes and expected_registry_signature), {
                "lanes": expected_lanes,
                "signature": expected_registry_signature,
            })
        status_registry_signature = status.get("tile_registry_signature")
        status_tile_lanes = [
            str(row.get("lane") or "")
            for row in (status.get("active_tiles") or [])
            if isinstance(row, dict)
        ]
        registry_parity = bool(
            manifest_registry_signature
            and manifest_registry_signature == expected_registry_signature
            and status_registry_signature == manifest_registry_signature
            and sync_registry_signature == manifest_registry_signature
            and manifest_tile_lanes == expected_lanes
            and status_tile_lanes == expected_lanes
        )
        add("tile_registry_cross_layer_parity", registry_parity, {
            "expected_lanes": expected_lanes,
            "manifest_lanes": manifest_tile_lanes,
            "status_lanes": status_tile_lanes,
            "manifest_signature": manifest_registry_signature,
            "status_signature": status_registry_signature,
            "sync_signature": sync_registry_signature,
        })

        process_rows: list[dict[str, Any]] = []
        try:
            process_rows = self.process_reader()
            inventory = classify_processes(process_rows)
        except Exception as exc:
            inventory = {"sync": [], "analyzer": [], "dashboard": [], "supervisor": []}
            add("process_inventory", False, type(exc).__name__)
        else:
            add("process_inventory", True, inventory)
        for kind in ("sync", "analyzer", "dashboard", "supervisor"):
            count = len(inventory[kind])
            # A Task Scheduler invocation is intentionally one-shot. It must
            # reject duplicate long-running owners, but absence of a --loop
            # owner is the expected topology rather than a fault.
            process_ok, expected = expected_process_count(
                kind, count, self.require_loop_owner
            )
            add(
                f"unique_{kind}_process",
                process_ok,
                {"count": count, "pids": inventory[kind], "expected": expected},
            )
            if count == 0 and kind in {"sync", "analyzer"} and self.launch_missing(kind):
                repairs.append(f"started_missing_{kind}_through_safe_launcher")

        analyzer_process_revisions = []
        analyzer_pids = set(inventory["analyzer"])
        for row in process_rows:
            row_pid = int(row.get("ProcessId") or row.get("process_id") or 0)
            if row_pid not in analyzer_pids:
                continue
            command_line = str(row.get("CommandLine") or row.get("command_line") or "")
            match = re.search(r"--source-revision=([0-9a-fA-F]{7,40})", command_line)
            analyzer_process_revisions.append(match.group(1).lower() if match else None)
        analyzer_revision_match = bool(
            source_revision
            and len(analyzer_process_revisions) == 1
            and analyzer_process_revisions[0]
            and (
                str(source_revision).lower().startswith(analyzer_process_revisions[0])
                or analyzer_process_revisions[0].startswith(str(source_revision).lower())
            )
        )
        add("analyzer_process_revision_parity", analyzer_revision_match, {
            "fly_source_revision": source_revision,
            "analyzer_process_revisions": analyzer_process_revisions,
        })
        # A deployed revision can leave a healthy-looking long-lived analyzer
        # pinned to the previous source marker.  Refresh it only after the
        # completed mirror proves exact Fly parity and process ownership is
        # singular.  The existing launcher performs the final owner/port
        # validation and preserves the independent read-only dashboard.
        if (
            self.repair
            and revision_match
            and len(inventory["sync"]) == 1
            and len(inventory["analyzer"]) == 1
            and len(inventory["dashboard"]) <= 1
            and not analyzer_revision_match
            and self.launch_missing("analyzer")
        ):
            repairs.append("refreshed_stale_analyzer_through_safe_launcher")

        events_path = self.mirror / "research_events_v22.jsonl"
        event_summary: dict[str, Any] | None = None
        current_evidence_summary: dict[str, Any] | None = None
        v3_ledger_dir = self.mirror / "v3" / "ledgers"
        v3_ledger_paths = list(v3_ledger_dir.glob("*.jsonl")) if v3_ledger_dir.is_dir() else []
        try:
            # V3.1 is the canonical collector whenever normalized ledgers are
            # present.  The compatibility v2.2 writer can remain on disk for
            # old consumers, but it is intentionally append-frozen and must
            # never drive current progress or report-parity health.  Preferring
            # it here produced false OPPORTUNITY_PROGRESS_STALLED and stale
            # legacy identity alarms while the V3 ledgers were advancing.
            if v3_ledger_paths:
                v3_summary = read_v3_evidence(self.mirror)
                row_count = sum(v3_summary["ledger_counts"].values())
                if row_count <= 0 or not v3_summary["epoch_ids"]:
                    raise ValueError("V3_EVIDENCE_EMPTY_OR_MISSING_EPOCH")
                mirror_age = self.now().timestamp() - max(
                    path.stat().st_mtime for path in v3_ledger_paths
                )
                public_summary = v3_summary
                schema_source = "research_evidence_v3"
            elif events_path.is_file():
                event_summary = read_current_events(events_path)
                mirror_age = self.now().timestamp() - events_path.stat().st_mtime
                public_summary = {
                    key: value for key, value in event_summary.items()
                    if not key.startswith("_")
                }
                schema_source = "research_event_v2.2"
            else:
                raise FileNotFoundError(
                    "neither research_events_v22.jsonl nor V3 normalized ledgers exist"
                )
            # A finalized append-only event file legitimately remains unchanged
            # when no lifecycle matures. Transport freshness is independently
            # enforced by the atomic sync heartbeat above.
            add("mirror_schema_and_freshness", True,
                {**public_summary, "schema_source": schema_source,
                 "age_seconds": round(mirror_age, 1)})
            current_evidence_summary = public_summary
        except Exception as exc:
            add("mirror_schema_and_freshness", False, f"{type(exc).__name__}: {exc}")

        progress_path = self.progress_state_file or self.repo / ".research-opportunity-progress-state.json"
        if current_evidence_summary is not None:
            try:
                prior_progress = read_json(progress_path)
            except (FileNotFoundError, ValueError, json.JSONDecodeError):
                prior_progress = {}
            if schema_source == "research_evidence_v3":
                opportunity_count = int(current_evidence_summary.get("independent_opportunities") or 0)
                epoch_ids = list(current_evidence_summary.get("epoch_ids") or [])
            else:
                opportunity_count = int(current_evidence_summary.get("all_independent_episodes") or 0)
                epoch_ids = [str(current_evidence_summary.get("epoch_id") or "")]
            strategy_progress = status.get("strategy_progress") if isinstance(status.get("strategy_progress"), dict) else {}
            ai_expected = strategy_progress.get("ai_expected")
            if not isinstance(ai_expected, bool):
                ai_expected = bool(
                    status.get("signal_generation_ready")
                    or (status.get("runtime_readiness") or {}).get("signal_generation_ready")
                )
            progress_expected = bool(ai_expected and not status.get("execution_paused", False))
            progress_ok, progress_detail, next_progress = evaluate_opportunity_progress(
                count=opportunity_count,
                epoch_ids=epoch_ids,
                source_revision=source_revision,
                prior=prior_progress,
                now=self.now(),
                progress_expected=progress_expected,
            )
            atomic_json(progress_path, next_progress)
            add("independent_opportunity_progress", progress_ok, progress_detail)

        reports: dict[str, dict[str, Any]] = {}
        report_times: dict[str, datetime] = {}
        report_freshness: dict[str, bool] = {}
        for filename in ("policy_candidate_oos_report.json", "best_policy_research_report.json"):
            path = self.report_dir / filename
            try:
                report = read_json(path)
                generated = parse_time(report.get("generated_at"))
                age = (self.now() - generated).total_seconds() if generated else float("inf")
                fresh = age <= REPORT_MAX_AGE_SECONDS
                add(f"report_fresh:{filename}", fresh, {"age_seconds": round(age, 1)})
                reports[filename] = report
                if generated:
                    report_times[filename] = generated
                report_freshness[filename] = fresh
            except Exception as exc:
                add(f"report_fresh:{filename}", False, type(exc).__name__)

        if event_summary and len(reports) == 2:
            expected = {
                "policy_candidate_oos_report.json": {
                    "current_events": event_summary["current_events"],
                    "eligible_events": event_summary["eligible_events"],
                    "eligible_independent_episodes": event_summary["eligible_independent_episodes"],
                },
                "best_policy_research_report.json": {
                    "current_events": event_summary["current_events"],
                    "eligible_events": event_summary["eligible_events"],
                    "ineligible_events": event_summary["ineligible_events"],
                    "all_independent_episodes": event_summary["all_independent_episodes"],
                },
            }
            expected_epoch = event_summary["epoch_id"]
            expected_policy_epochs = event_summary["policy_epoch_ids"]
            expected_signatures = event_summary["policy_signatures"]
            identity_ok = all(
                report.get("epoch_id") == expected_epoch
                and report.get("policy_epoch_id") in expected_policy_epochs
                and report.get("evidence_policy_signature") in expected_signatures
                for report in reports.values()
            )
            observed = {name: report_count_contract(name, report) for name, report in reports.items()}
            exact = all(observed[name] == expected[name] for name in expected)
            mirror_time = datetime.fromtimestamp(events_path.stat().st_mtime, tz=timezone.utc)
            mirror_after_reports = (
                len(report_times) == 2
                and all(mirror_time > generated for generated in report_times.values())
            )
            snapshot_prefix_ok, snapshot_prefix_detail = shared_cycle_snapshot_prefix_ok(
                reports, event_summary,
            )
            pending, pending_detail = bounded_pending_parity(
                expected, observed,
                reports_fresh=len(report_freshness) == 2 and all(report_freshness.values()),
                mirror_after_reports=mirror_after_reports,
                snapshot_prefix_ok=snapshot_prefix_ok,
                identity_ok=identity_ok,
            )
            add("report_count_parity", exact or pending, {
                "status": "EXACT" if exact else pending_detail["status"],
                "expected": expected,
                "reports": observed,
                "pending": pending_detail,
                "cycle_snapshot_prefix": snapshot_prefix_detail,
            })
            add("report_epoch_policy_signature_parity", identity_ok, {
                "expected_epoch": expected_epoch, "expected_policy_epochs": expected_policy_epochs,
                "expected_policy_signatures": expected_signatures,
                "reports": {name: {key: report.get(key) for key in ("epoch_id", "policy_epoch_id", "evidence_policy_signature")}
                            for name, report in reports.items()}})

        v3_report_path = self.report_dir / "safe_policy_genome_v3_report.json"
        manifest_has_v3 = any(str(row.get("path") or "").replace("\\", "/").startswith("v3/") for row in (manifest.get("files") or []))
        if v3_report_path.is_file() or manifest_has_v3:
            try:
                v3 = read_v3_evidence(self.mirror, now_ts=self.now().timestamp())
                entry_integrity = v3.get("entry_resolution_integrity") or {}
                provenance_integrity = v3.get("policy_provenance_integrity") or {}
                add("v3_normalized_evidence_integrity", (
                    v3.get("identity_alias_count", 0) == 0
                    and entry_integrity.get("passed") is True
                    and provenance_integrity.get("passed") is True
                ), v3)
                report = read_json(v3_report_path)
                generated = parse_time(report.get("generated_at"))
                age = (self.now() - generated).total_seconds() if generated else float("inf")
                collection = report.get("collection") or {}
                expected = {
                    "independent_opportunities": v3["independent_opportunities"],
                    "decision_branches": v3["decision_branches"],
                    "terminal_lifecycles": v3["terminal_lifecycles"],
                    "provisional_lifecycles": v3["provisional_lifecycles"],
                    "market_segments": v3["market_segments"],
                }
                observed = {key: int(collection.get(key) or 0) for key in expected}
                # ``market_segments`` is intentionally the terminal-path
                # subset.  Compare the ledger total with the analyzer's
                # explicit ledger-row total so pre-signal-only context is not
                # misreported as a pending analyzer deficit.
                observed["market_segments"] = int(
                    collection.get("market_segment_ledger_rows")
                    if collection.get("market_segment_ledger_rows") is not None
                    else collection.get("market_segments") or 0
                )
                deltas = {key: expected[key] - observed[key] for key in expected}
                exact = expected == observed
                pending = (
                    age <= REPORT_MAX_AGE_SECONDS
                    and all(delta >= 0 for delta in deltas.values())
                    # Each ledger counts a different projection of the same
                    # newly collected opportunities. Summing the deltas
                    # double-counts one collection interval and falsely marks
                    # a healthy 30-minute analyzer cadence as mismatched.
                    and all(delta <= MAX_PENDING_EVENT_DELTA for delta in deltas.values())
                )
                add("v3_report_fresh_and_count_parity", age <= REPORT_MAX_AGE_SECONDS and (exact or pending), {
                    "status": "EXACT" if exact else "PENDING_NEXT_ANALYZER_CYCLE" if pending else "MISMATCH",
                    "age_seconds": round(age, 1), "expected": expected, "observed": observed, "deltas": deltas,
                    "report_status": report.get("status"), "qualification": report.get("qualification"),
                    "real_bitfinex_trading_allowed": report.get("real_bitfinex_trading_allowed"),
                })
                add("v3_real_money_fail_closed", report.get("real_bitfinex_trading_allowed") is False, {
                    "real_bitfinex_trading_allowed": report.get("real_bitfinex_trading_allowed"),
                    "number_one_strategy": report.get("number_one_strategy"),
                })
            except Exception as exc:
                add("v3_normalized_evidence_integrity", False, f"{type(exc).__name__}: {exc}")

        return {
            "schema": "research_stability_supervisor_v1",
            "generated_at": self.now().isoformat(),
            "healthy": all(row["ok"] for row in checks),
            "repair_authority": "LOCAL_SYNC_OR_MISSING_OR_REVISION_STALE_ANALYZER_ONLY",
            "forbidden_actions": ["TRADING_RESTART", "FLY_RESTART", "DATA_WIPE", "POLICY_CHANGE", "LIVE_TRADE_ARM"],
            "repairs": repairs,
            "checks": checks,
        }


def default_paths(repo: Path) -> tuple[Path, Path]:
    agent = repo / "services" / "btc-conservative-agent"
    return agent / "canonical-research-data", agent


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-repo", type=Path)
    parser.add_argument("--mirror", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--fly-url", default="https://doxed-btc-bot.fly.dev")
    parser.add_argument("--repair-missing-local", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--status-file", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    mirror_default, report_default = default_paths(repo)
    status_file = args.status_file or repo / ".research-stability-supervisor.json"
    process_lock = repo / ".research-stability-supervisor.lock"
    token = os.environ.get("BOT_ADMIN_TOKEN", "")
    if not token:
        raise SystemExit("BOT_ADMIN_TOKEN is required; load it through the existing vault launcher")
    supervisor = Supervisor(
        repo, args.mirror or mirror_default, args.report_dir or report_default,
        args.fly_url, token, repair=args.repair_missing_local,
        runtime_repo=args.runtime_repo.resolve() if args.runtime_repo else None,
        require_loop_owner=args.loop,
    )
    try:
        with exclusive_process_lock(process_lock):
            while True:
                payload = supervisor.check()
                atomic_json(status_file, payload)
                if not args.loop:
                    return 0 if payload["healthy"] else 2
                time.sleep(max(60, args.interval_seconds))
    except SupervisorLockUnavailable:
        # Another verified owner already holds the lifetime lock.  Duplicate
        # scheduled invocations are an expected no-op, not a repair failure.
        return 0


if __name__ == "__main__":
    sys.exit(main())

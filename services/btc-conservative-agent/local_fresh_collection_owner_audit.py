"""Bounded Windows owner evidence for the laptop-only research reset."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import time


class LocalOwnerAuditUnavailable(RuntimeError):
    pass


OWNER_COMMAND_PATTERNS = (
    "sync-fly-bot-data",
    "fly-sync-bundle-client",
    "fly-sync-generation-resume",
    "start-fly-batch-sync",
    "analyzer_research_engine_v62",
    "research_dashboard.py",
    "migrate_canonical_research_store",
    "archive-research-data",
    "fly-mirror-quarantine",
    "raw_generation_cleanup_owner",
    "canonical_generation_retirement",
)


def audit_local_research_owners(_canonical_root, _archive_root) -> dict:
    if os.name != "nt":
        raise LocalOwnerAuditUnavailable("LOCAL_RESET_WINDOWS_OWNER_AUDIT_REQUIRED")
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise LocalOwnerAuditUnavailable("LOCAL_RESET_POWERSHELL_UNAVAILABLE")
    # The bridge itself is intentionally not a blocker.  Only processes which
    # can read/write/promote the eligible research roots are included.
    pattern_literals = ",".join(json.dumps(value) for value in OWNER_COMMAND_PATTERNS)
    command = r'''
$ErrorActionPreference='Stop'
$patterns=@(__OWNER_COMMAND_PATTERNS__)
$owners=@(Get-CimInstance Win32_Process -OperationTimeoutSec 8 | Where-Object {
  $process=$_
  $process.ProcessId -ne $PID -and $process.CommandLine -and ($patterns | Where-Object {$process.CommandLine -like ('*'+$_+'*')}).Count -gt 0
} | ForEach-Object {@{pid=[int]$_.ProcessId;name=[string]$_.Name}})
$taskNames=@('DoxxedFlyMirrorOneshot','DoxxedFlyMirrorSync','DoxxedFlySyncDurable','DoxxedResearchStabilitySupervisor')
$tasks=@(Get-ScheduledTask -ErrorAction Stop | Where-Object {$_.TaskName -in $taskNames -and [string]$_.State -eq 'Running'} | ForEach-Object {@{name=$_.TaskName;state=[string]$_.State}})
@{schema='local_research_owner_audit_v1';safe=($owners.Count -eq 0 -and $tasks.Count -eq 0);owners=$owners;running_tasks=$tasks;checked_at=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000} | ConvertTo-Json -Depth 5 -Compress
'''
    command = command.replace("__OWNER_COMMAND_PATTERNS__", pattern_literals)
    encoded = base64.b64encode(command.encode("utf-16le")).decode("ascii")
    completed = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if completed.returncode != 0:
        raise LocalOwnerAuditUnavailable("LOCAL_RESET_OWNER_AUDIT_FAILED")
    try:
        result = json.loads(completed.stdout.strip())
    except (ValueError, TypeError) as exc:
        raise LocalOwnerAuditUnavailable("LOCAL_RESET_OWNER_AUDIT_INVALID") from exc
    if not isinstance(result, dict):
        raise LocalOwnerAuditUnavailable("LOCAL_RESET_OWNER_AUDIT_INVALID")
    age = time.time() - float(result.get("checked_at", 0))
    if (
        result.get("schema") != "local_research_owner_audit_v1"
        or type(result.get("safe")) is not bool
        or not 0 <= age <= 15
    ):
        raise LocalOwnerAuditUnavailable("LOCAL_RESET_OWNER_AUDIT_INVALID")
    return result

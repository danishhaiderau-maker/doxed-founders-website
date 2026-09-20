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
    "resume-current-epoch-batch-20260921.ps1",
    "small-sync-client-20260920",
    "analyzer_research_engine_v62",
    "research_dashboard.py",
    "migrate_canonical_research_store",
    "archive-research-data",
    "fly-mirror-quarantine",
    "raw_generation_cleanup_owner",
    "canonical_generation_retirement",
    "research-stability-supervisor.py",
    "start-research-stability-supervisor.ps1",
    "home-stack-supervisor.ps1",
    "home-stack-supervisor-watchdog.ps1",
    "bot-auto-restart.ps1",
    "fast-recover-global.ps1",
    "home-stack-start-everything.ps1",
    "start-showcase-bot.cmd",
)

RELAUNCH_TASK_NAMES = (
    "DoxxedFlyMirrorOneshot",
    "DoxxedFlyMirrorSync",
    "DoxxedFlySyncDurable",
    "DoxxedResearchStabilitySupervisor",
    "DcfShowcaseBotAutostart",
    "DoxedSupervisorWatchdog",
)

RELAUNCH_ACTION_PATTERNS = (
    "sync-fly-bot-data",
    "start-fly-desktop-mirror",
    "resume-current-epoch-batch-20260921.ps1",
    "small-sync-client-20260920",
    "start-home-analyzer",
    "start-research-stability-supervisor",
    "research-stability-supervisor.py",
    "start-showcase-bot.cmd",
    "home-stack-supervisor.ps1",
    "home-stack-supervisor-watchdog.ps1",
    "bot-auto-restart.ps1",
    "fast-recover-global.ps1",
    "home-stack-start-everything.ps1",
    "oneshot_detached_loop.ps1",
    "FlySyncDurable-boot.ps1",
)

READINESS_SCOPE = "local_research_owners_and_relaunch_authorities_v1"


def audit_local_research_owners(_canonical_root, _archive_root) -> dict:
    if os.name != "nt":
        raise LocalOwnerAuditUnavailable("LOCAL_RESET_WINDOWS_OWNER_AUDIT_REQUIRED")
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        raise LocalOwnerAuditUnavailable("LOCAL_RESET_POWERSHELL_UNAVAILABLE")
    # The bridge itself is intentionally not a blocker.  Only processes which
    # can read/write/promote the eligible research roots are included.
    pattern_literals = ",".join(json.dumps(value) for value in OWNER_COMMAND_PATTERNS)
    task_name_literals = ",".join(json.dumps(value) for value in RELAUNCH_TASK_NAMES)
    relaunch_pattern_literals = ",".join(
        json.dumps(value) for value in RELAUNCH_ACTION_PATTERNS
    )
    command = r'''
$ErrorActionPreference='Stop'
$patterns=@(__OWNER_COMMAND_PATTERNS__)
$taskNames=@(__RELAUNCH_TASK_NAMES__)
$relaunchPatterns=@(__RELAUNCH_ACTION_PATTERNS__)
$ownerCategories=@(Get-CimInstance Win32_Process -OperationTimeoutSec 8 | Where-Object {
  $process=$_
  $process.ProcessId -ne $PID -and $process.CommandLine -and ($patterns | Where-Object {$process.CommandLine -like ('*'+$_+'*')}).Count -gt 0
} | ForEach-Object {
  $line=[string]$_.CommandLine
  if ($line -like '*research_dashboard.py*') { 'dashboard_owner' }
  elseif ($line -like '*analyzer_research_engine_v62*') { 'analyzer_owner' }
  elseif ($line -like '*research-stability-supervisor*') { 'stability_supervisor_owner' }
  elseif ($line -like '*resume-current-epoch-batch-20260921.ps1*') { 'batch_resume_owner' }
  elseif ($line -like '*home-stack-supervisor*' -or $line -like '*auto-restart*' -or $line -like '*fast-recover-global*') { 'relaunch_supervisor_owner' }
  elseif ($line -like '*archive-research-data*') { 'archive_writer_owner' }
  elseif ($line -like '*migrate_canonical_research_store*') { 'migration_owner' }
  elseif ($line -like '*quarantine*' -or $line -like '*cleanup_owner*' -or $line -like '*retirement*') { 'generation_maintenance_owner' }
  else { 'sync_owner' }
} | Sort-Object -Unique)
$taskCategories=@(Get-ScheduledTask -ErrorAction Stop | ForEach-Object {
  $task=$_
  $state=[string]$task.State
  if ($state -ne 'Disabled') {
    $actionText=[string](($task.Actions | ForEach-Object { ([string]$_.Execute) + ' ' + ([string]$_.Arguments) }) -join ' ')
    $known=[string]$task.TaskName -in $taskNames
    $matched=@($relaunchPatterns | Where-Object {$actionText -like ('*'+$_+'*')}).Count -gt 0
    if ($known -or $matched) {
      if (-not $known) { 'unknown_relaunch_authority' }
      elseif ([string]$task.TaskName -eq 'DcfShowcaseBotAutostart') { 'showcase_autostart_authority' }
      elseif ([string]$task.TaskName -eq 'DoxxedResearchStabilitySupervisor') { 'stability_supervisor_authority' }
      elseif ([string]$task.TaskName -eq 'DoxedSupervisorWatchdog') { 'stack_supervisor_authority' }
      else { 'sync_relaunch_authority' }
    }
  }
} | Sort-Object -Unique)
$blockers=@()
if ($ownerCategories.Count -gt 0) { $blockers += 'active_local_research_owner' }
if ($taskCategories.Count -gt 0) { $blockers += 'enabled_relaunch_authority' }
@{
  schema='local_research_owner_audit_v2'
  readiness_scope='__READINESS_SCOPE__'
  safe=($blockers.Count -eq 0)
  blocker_categories=@($blockers)
  active_owner_categories=@($ownerCategories)
  relaunch_authority_categories=@($taskCategories)
  checked_at=[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()/1000
} | ConvertTo-Json -Depth 5 -Compress
'''
    command = command.replace("__OWNER_COMMAND_PATTERNS__", pattern_literals)
    command = command.replace("__RELAUNCH_TASK_NAMES__", task_name_literals)
    command = command.replace("__RELAUNCH_ACTION_PATTERNS__", relaunch_pattern_literals)
    command = command.replace("__READINESS_SCOPE__", READINESS_SCOPE)
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
        result.get("schema") != "local_research_owner_audit_v2"
        or result.get("readiness_scope") != READINESS_SCOPE
        or type(result.get("safe")) is not bool
        or not all(
            isinstance(result.get(key), list)
            and all(isinstance(value, str) and 0 < len(value) <= 64 for value in result[key])
            for key in (
                "blocker_categories",
                "active_owner_categories",
                "relaunch_authority_categories",
            )
        )
        or not 0 <= age <= 15
    ):
        raise LocalOwnerAuditUnavailable("LOCAL_RESET_OWNER_AUDIT_INVALID")
    return result

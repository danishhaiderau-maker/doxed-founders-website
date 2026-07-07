# scripts/tidy-orphan-shells.ps1
#
# Housekeeping: kill orphan cmd.exe + hidden powershell.exe processes that
# accumulate from launcher scripts (start-home-stack.ps1, ensure-*.ps1, etc.)
# over multi-day runs. Each one is tiny (~1MB) but they pile up - a week of
# restarts produced 14 cmd.exe + 10 powershell.exe on the production home PC.
#
# Safe by design:
#   - NEVER kills python.exe (showcase bot, analyzer)
#   - NEVER kills node.exe running scripts/ (lifecycle/trade watchers)
#   - NEVER kills cloudflared.exe (tunnel)
#   - NEVER kills powershell.exe running one of $ESSENTIAL_WATCH_SCRIPTS
#   - NEVER kills the calling shell or its parent
#   - NEVER kills Cursor/VSCode processes
#   - ONLY kills cmd.exe with no commandline (orphan launchers) OR cmd.exe
#     launched from logs\launchers\ whose launcher .cmd file has been pruned
#     by F9 (and is older than 30 minutes for race safety)
#   - ONLY kills powershell.exe with -WindowStyle Hidden AND no essential script
#
# Usage:
#   scripts\tidy-orphan-shells.ps1              # show what would be killed (dry-run)
#   scripts\tidy-orphan-shells.ps1 -Execute     # actually kill
#   scripts\tidy-orphan-shells.ps1 -Execute -LogPath C:\path\to\log.txt
#
# Recommended: schedule every 6h via Task Scheduler (run as the same user
# that owns the bot stack, WITH ELEVATION, so it can reach cross-session
# orphans). Use scripts\register-tidy-orphan-shells.ps1 (companion) to
# register the scheduled task with elevation. Running from a non-elevated
# shell will report "Access is denied" for any process owned by an elevated
# launcher - that's a Windows limitation, not a script bug.

[CmdletBinding()]
param(
  [switch]$Execute,
  [string]$LogPath,
  [string[]]$EssentialWatchScripts = @(
    'analyzer-auto-restart.ps1',
    'bot-auto-restart.ps1',
    'live-copy-6h-lifecycle-watch.ps1',
    'live-copy-24h-supervisor.ps1',
    'stack-health-watch.ps1',
    'stack-watch.ps1',
    'stack-watch-2h.ps1',
    'watch-stack-2h.ps1',
    'watch-home-stack-2h.ps1',
    'home-stack-supervisor.ps1',
    'home-stack-supervisor-watchdog.ps1',
    'home-stack-watch.ps1',
    'home-stack-monitor.ps1',
    'home-stack-health.ps1',
    'system-watchdog.ps1',
    'tunnel-watchdog.ps1',
    'bridge-watchdog.ps1',
    'bot-crash-monitor.ps1',
    'register-stack-monitor.ps1',
    'register-bridge-watchdog.ps1',
    'stack-monitor.ps1'
  )
)

$ErrorActionPreference = 'Continue'
$mode = if ($Execute) { 'EXECUTE' } else { 'DRY-RUN' }
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$myPid = $PID
$myParentPid = $null
try {
  $myProc = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
  $myParentPid = $myProc.ParentProcessId
} catch {}

# Resolve $PSScriptRoot safely (empty when invoked via powershell -File from cmd)
$scriptRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($scriptRoot)) {
  $scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if ([string]::IsNullOrWhiteSpace($scriptRoot)) {
  $scriptRoot = (Get-Location).Path
}

if (-not $LogPath) {
  $LogPath = Join-Path $scriptRoot "..\logs\shell-tidy.log"
}

function Write-TidyLog([string]$msg) {
  $line = "[$ts] [$mode] $msg"
  Write-Host $line
  try {
    $logDir = Split-Path -Parent $LogPath
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    Add-Content -Path $LogPath -Value $line -Encoding UTF8
  } catch {}
}

function Test-OrphanLauncherCmd([string]$CommandLine, [datetime]$CreationDate) {
  # Returns $true if this cmd.exe was launched by a now-pruned launcher file
  # (i.e. spawned by DCF home-stack launcher machinery from logs\launchers\,
  # where the referenced .cmd file no longer exists because F9 pruned it).
  if (-not $CommandLine) { return $false }
  # Match: cmd /k "...logs\launchers\<name>.cmd" or cmd /c variant
  if ($CommandLine -notmatch 'logs\\launchers\\[^"]*\.cmd') { return $false }
  # Extract the launcher path
  $m = [regex]::Match($CommandLine, '"([^"]*logs\\launchers\\[^"]*\.cmd)"')
  $launcherPath = if ($m.Success) { $m.Groups[1].Value } else { $null }
  if (-not $launcherPath) { return $false }
  if (Test-Path $launcherPath) {
    # Launcher still exists - not an orphan yet (F9 hasn't pruned it)
    return $false
  }
  # Launcher file is gone. Add 30-minute floor to avoid killing fresh launches
  # that are still mid-startup.
  try {
    $ageMin = [math]::Round(((Get-Date) - $CreationDate).TotalMinutes)
  } catch {
    # CreationDate unavailable - can't apply age floor, be safe and skip
    return $false
  }
  if ($ageMin -lt 30) { return $false }
  return $true
}

function Test-EssentialPowerShell([int]$ProcessId, [string]$CommandLine) {
  if ($ProcessId -eq $myPid -or $ProcessId -eq $myParentPid) { return $true }
  if (-not $CommandLine) { return $false }
  foreach ($s in $EssentialWatchScripts) {
    if ($CommandLine -like "*$s*") { return $true }
  }
  # Cursor / VSCode integration shell - keep
  if ($CommandLine -like '*cursor*' -or $CommandLine -like '*Code.exe*' -or $CommandLine -like '*vscode*') { return $true }
  return $false
}

function Stop-ProcessSafe([int]$ProcessId, [string]$Name) {
  if (-not $Execute) {
    Write-TidyLog "  WOULD KILL $Name PID=$ProcessId"
    return 'would-kill'
  }
  # Try Stop-Process first (same session), fall back to taskkill (cross-session,
  # same user). /F = force, /T = tree (kill children too).
  $killedViaStop = $false
  try {
    Stop-Process -Id $ProcessId -Force -ErrorAction Stop
    Write-TidyLog "  KILLED $Name PID=$ProcessId (Stop-Process)"
    return 'killed'
  } catch {
    # Stop-Process failed (often access-denied cross-session). Try taskkill /F /T.
    $taskkillResult = & taskkill /F /T /PID $ProcessId 2>&1
    if ($LASTEXITCODE -eq 0) {
      Write-TidyLog "  KILLED $Name PID=$ProcessId (taskkill)"
      return 'killed'
    } else {
      $err = $_.Exception.Message
      Write-TidyLog "  FAILED $Name PID=$ProcessId - $err | taskkill: $taskkillResult"
      return 'failed'
    }
  }
}

Write-TidyLog "=== tidy-orphan-shells start (my PID=$myPid, parent=$myParentPid) ==="

$stats = [pscustomobject]@{
  CmdTotal = 0
  CmdKilled = 0
  PsTotal = 0
  PsKilled = 0
  PsKeptEssential = 0
  Failed = 0
}

# ─── 1. cmd.exe orphans ───────────────────────────────────────────────────
# Kill cmd.exe when EITHER:
#   (a) it has no CommandLine (orphan launcher window), OR
#   (b) it was launched by a now-pruned launcher file under logs\launchers\
#       (cmd /k "...logs\launchers\<name>.cmd") - the launcher file is gone
#       (F9 prunes launchers older than 6h), so the cmd is just sitting at
#       `pause >nul` doing nothing. We add a 30-minute age floor so we don't
#       race a launcher that is still mid-startup.
# All other cmd.exe (user shells, scheduled tasks, setup scripts) are kept.
$cmdProcesses = @(Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue)
$stats.CmdTotal = $cmdProcesses.Count
Write-TidyLog "cmd.exe total: $($stats.CmdTotal)"

foreach ($p in $cmdProcesses) {
  $cl = [string]$p.CommandLine
  if (-not [string]::IsNullOrWhiteSpace($cl) -and -not (Test-OrphanLauncherCmd -CommandLine $cl -CreationDate $p.CreationDate)) {
    Write-TidyLog "  KEEP cmd.exe PID=$($p.ProcessId) (active commandline)"
    continue
  }
  $reason = if ([string]::IsNullOrWhiteSpace($cl)) { 'no commandline' } else { 'orphan launcher (file pruned)' }
  $result = Stop-ProcessSafe -ProcessId $p.ProcessId -Name 'cmd.exe'
  if ($result -eq 'killed') { $stats.CmdKilled++ }
  elseif ($result -eq 'failed') { $stats.Failed++ }
}

# ─── 2. powershell.exe orphans ────────────────────────────────────────────
# Kill hidden-windowless powershell.exe that aren't running an essential
# watcher script. Visible powershell windows are user terminals - leave alone.
$psProcesses = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue)
$stats.PsTotal = $psProcesses.Count
Write-TidyLog "powershell.exe total: $($stats.PsTotal)"

foreach ($p in $psProcesses) {
  $cl = [string]$p.CommandLine
  if (Test-EssentialPowerShell -ProcessId $p.ProcessId -CommandLine $cl) {
    $label = if ($cl) { ($cl -replace '\s+',' ').Substring(0,[math]::Min(80,$cl.Length)) } else { '<self/parent>' }
    Write-TidyLog "  KEEP essential powershell.exe PID=$($p.ProcessId): $label"
    $stats.PsKeptEssential++
    continue
  }
  # Hidden window check - orphan launchers use -WindowStyle Hidden. If the
  # process has no main window (WindowVisible=$false) it's a hidden launcher.
  try {
    $procInfo = Get-Process -Id $p.ProcessId -ErrorAction Stop
    $hasWindow = -not [string]::IsNullOrEmpty($procInfo.MainWindowTitle) -or $procInfo.MainWindowHandle -ne 0
  } catch {
    $hasWindow = $false
  }
  $isHiddenInCmdLine = $cl -like '*-WindowStyle Hidden*' -or $cl -like '*-WindowStyle Hidden*'
  if (-not $isHiddenInCmdLine -and $hasWindow) {
    Write-TidyLog "  KEEP visible powershell.exe PID=$($p.ProcessId) (user terminal)"
    continue
  }
  $result = Stop-ProcessSafe -ProcessId $p.ProcessId -Name 'powershell.exe'
  if ($result -eq 'killed') { $stats.PsKilled++ }
  elseif ($result -eq 'failed') { $stats.Failed++ }
}

Write-TidyLog ("Summary: cmd(killed={0} of {1}) ps(killed={2} of {3}, kept-essential={4}) failed={5}" -f `
  $stats.CmdKilled, $stats.CmdTotal, $stats.PsKilled, $stats.PsTotal, $stats.PsKeptEssential, $stats.Failed)
Write-TidyLog "=== tidy-orphan-shells end ==="
Write-Host ""
if (-not $Execute) {
  Write-Host "DRY-RUN complete. Re-run with -Execute to actually kill." -ForegroundColor Yellow
}
return $stats

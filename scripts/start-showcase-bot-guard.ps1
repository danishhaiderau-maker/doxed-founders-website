# Fail-safe autostart guard for DcfShowcaseBotAutostart.
# Returns exit code 99 when the bot is already bound to :7002 by a python
# process -> start-showcase-bot.cmd then skips the orchestrator entirely.
# Returns 0 otherwise (caller proceeds with normal startup).

$ErrorActionPreference = "Continue"
$port = 7002

$repoRoot = Split-Path -Parent $PSScriptRoot
$logPath = Join-Path $repoRoot "logs\bot_restarts.log"

function Write-GuardLog([string]$msg) {
  $ts = (Get-Date).ToString("yyyy-MM-ddTHH:mm:sszzz")
  $line = "$ts`t$msg"
  try {
    $logDir = Split-Path $logPath -Parent
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    Add-Content -Path $logPath -Value $line -ErrorAction SilentlyContinue
  } catch { }
  Write-Host $line
}

$conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1

# Do not let an old but healthy process mask a newly installed bot revision.
# The recovery helper performs all destructive checks itself: exact listener
# PID, exact source path and source-newer-than-process timestamp.
if ($conn) {
  try {
    $state = Invoke-RestMethod -Uri "http://127.0.0.1:$port/api/state" -TimeoutSec 8
    $canonicalBot = Join-Path $repoRoot 'services\btc-conservative-agent\bot.py'
    $reportedBot = [System.IO.Path]::GetFullPath([string]$state.bot_script)
    $expectedBot = [System.IO.Path]::GetFullPath($canonicalBot)
    $started = [DateTimeOffset]::FromUnixTimeSeconds([long][math]::Floor([double]$state.bot_start_time)).UtcDateTime
    $sourceIsNewer = (Get-Item -LiteralPath $canonicalBot -ErrorAction Stop).LastWriteTimeUtc -gt $started
    if ($reportedBot.Equals($expectedBot, [System.StringComparison]::OrdinalIgnoreCase) -and $sourceIsNewer) {
      $helper = Join-Path $PSScriptRoot 'replace-stale-home-bot.ps1'
      Write-GuardLog "[autostart-guard] stale bot revision detected on :$port; running guarded replacement"
      & $helper -Port $port | ForEach-Object { Write-GuardLog "[autostart-guard] stale recovery: $_" }
      $conn = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    }
  } catch {
    Write-GuardLog "[autostart-guard] stale-revision check skipped: $($_.Exception.Message)"
  }
}

if (-not $conn) {
  Write-GuardLog "[autostart-guard] no listener on :$port, proceeding with startup"
  exit 0
}

$proc = $null
try { $proc = Get-Process -Id $conn.OwningProcess -ErrorAction Stop } catch { }

if ($proc -and $proc.Name -match "python") {
  Write-GuardLog "[autostart-guard] bot already running on :$port (pid=$($proc.Id) uptime=$([int]((Get-Date) - $proc.StartTime).TotalMinutes)m), skipping launch"
  exit 99
}

Write-GuardLog "[autostart-guard] :$port bound by non-python pid=$($proc.Id) name=$($proc.Name), proceeding with startup"
exit 0

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

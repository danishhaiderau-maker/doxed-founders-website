# Scheduled-task watchdog: relaunches home-stack-supervisor.ps1 if it has died.
# Registered by register-supervisor-watchdog.ps1 as the DoxedSupervisorWatchdog task.
# This is the missing link that was letting the bot stay dead for hours after a crash.
param([switch]$Quiet)
$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

# Fly is the sole AI/strategy/trading owner. Do not let an old scheduled task
# resurrect the obsolete home supervisor merely because a lock file moved.
$obsoleteOwnerOptIn = "I_UNDERSTAND_THIS_STARTS_A_SECOND_AI_TRADING_OWNER"
$obsoleteOwnerEnabled = (
  [Environment]::GetEnvironmentVariable("DCF_ENABLE_OBSOLETE_WINDOWS_TRADING_OWNER", "Process") -ceq
  $obsoleteOwnerOptIn
)
if (-not $obsoleteOwnerEnabled) {
  if (
    [Environment]::GetEnvironmentVariable("DCF_LEGACY_WINDOWS_LAUNCH_CONTRACT_TEST", "Process") -ceq
    "NO_SIDE_EFFECTS"
  ) {
    Write-Error "Obsolete Windows supervisor watchdog is quarantined; no process was started."
    exit 78
  }

  $mirrorScript = Join-Path $scriptDir "start-fly-desktop-mirror.ps1"
  if (Test-Path -LiteralPath $mirrorScript) {
    if (-not $Quiet) {
      Write-Warning "Obsolete supervisor watchdog is quarantined. Starting the safe Fly desktop mirror/analyzer instead."
    }
    & $mirrorScript -NoWait
    exit 0
  }

  Write-Error "Obsolete supervisor watchdog is quarantined and the safe Fly desktop mirror launcher is missing."
  exit 78
}

Write-Warning "DISASTER-RECOVERY OPT-IN ACTIVE: obsolete Windows supervisor watchdog is permitted."
if (Test-Path -LiteralPath (Join-Path $repoRoot "config\fly-canonical.lock.json")) {
  Write-Error "Fly canonical lock is present; refusing to restore a second AI/trading owner."
  exit 78
}
$logFile = Join-Path $repoRoot ".home-stack-watchdog.log"
$supervisorHeartbeatFile = Join-Path $repoRoot ".home-stack-supervisor.heartbeat"
$supervisorScript = Join-Path $scriptDir "home-stack-supervisor.ps1"
$userStoppedFile = Join-Path $repoRoot ".home-stack-user-stopped"

function Wd-Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
  if (-not $Quiet) { Write-Host $line }
}

function Test-SupervisorAlive {
  # The heartbeat is written only after the supervisor holds its exclusive
  # lock and at the start of every progress loop.  It is sufficient proof of
  # useful liveness and keeps this scheduled task independent of every Windows
  # process provider.  If it is stale, launching another supervisor is safe:
  # the exclusive lock makes a healthy incumbent reject the duplicate.
  if (-not (Test-Path -LiteralPath $supervisorHeartbeatFile)) { return $false }
  try {
    $raw = Get-Content -LiteralPath $supervisorHeartbeatFile -Raw -ErrorAction Stop
    $heartbeat = [datetime]::Parse(
      $raw.Trim(),
      [System.Globalization.CultureInfo]::InvariantCulture,
      [System.Globalization.DateTimeStyles]::RoundtripKind
    )
    $ageSeconds = ((Get-Date).ToUniversalTime() - $heartbeat.ToUniversalTime()).TotalSeconds
    return ($ageSeconds -ge 0 -and $ageSeconds -le 300)
  } catch {
    return $false
  }
}

# A scheduled watchdog must never undo an explicit operator Stop. The normal
# Start path clears this sentinel before launching the supervisor again.
if (Test-Path -LiteralPath $userStoppedFile) {
  Wd-Log "supervisor restart skipped - user stopped stack"
  exit 0
}

if (Test-SupervisorAlive) {
  Wd-Log "supervisor alive - nothing to do"
  exit 0
}

Wd-Log "supervisor NOT running - relaunching"
# Read home stack mode so we pass the correct ports.
$modeScript = Join-Path $scriptDir "home-stack-mode.ps1"
$botPort = 7002; $analyzerPort = 9500; $bridgePort = 7810
if (Test-Path $modeScript) {
  try {
    . $modeScript
    $m = Get-HomeStackMode
    $botPort = [int]$m.BotPort
    $analyzerPort = [int]$m.AnalyzerPort
    $bridgePort = [int]$m.BridgePort
  } catch { }
}

try {
  # Build arg list as a single quoted string - repo path has a space ("Final
  # Bots") and Start-Process -ArgumentList @("-File", $path) splits the path
  # on the space, producing "Processing -File 'C:\...\Final' failed because
  # the file does not have a '.ps1' extension" and the supervisor never starts
  # (so the bot has NO 24/7 health watcher). Same fix start-home-bot.ps1 uses
  # for the monitor spawn. Quoting the -File value inside one argument string
  # is the reliable solution.
  $supArgString = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$supervisorScript`" -BotPort $botPort -AnalyzerPort $analyzerPort -BridgePort $bridgePort"
  Start-Process -FilePath "powershell" -ArgumentList $supArgString -WindowStyle Hidden | Out-Null
  Wd-Log "supervisor relaunch issued bot=:$botPort analyzer=:$analyzerPort"
} catch {
  Wd-Log "supervisor relaunch FAILED: $($_.Exception.Message)"
}

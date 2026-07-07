# Scheduled-task watchdog: relaunches home-stack-supervisor.ps1 if it has died.
# Registered by register-supervisor-watchdog.ps1 as the DoxedSupervisorWatchdog task.
# This is the missing link that was letting the bot stay dead for hours after a crash.
param([switch]$Quiet)
$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logFile = Join-Path $repoRoot ".home-stack-watchdog.log"
$supervisorPidFile = Join-Path $repoRoot ".home-stack-supervisor.pid"
$supervisorScript = Join-Path $scriptDir "home-stack-supervisor.ps1"

function Wd-Log([string]$msg) {
  $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
  if (-not $Quiet) { Write-Host $line }
}

function Test-SupervisorAlive {
  $pidVal = $null
  if (Test-Path $supervisorPidFile) {
    $pidVal = (Get-Content $supervisorPidFile -Raw -ErrorAction SilentlyContinue).Trim()
  }
  if (-not $pidVal -or $pidVal -notmatch '^\d+$') { return $false }
  $proc = Get-Process -Id ([int]$pidVal) -ErrorAction SilentlyContinue
  if (-not $proc) { return $false }
  # Confirm it is actually the supervisor, not a recycled PID.
  $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId=$pidVal" -ErrorAction SilentlyContinue).CommandLine
  if ($cmd -and $cmd -like "*home-stack-supervisor.ps1*") { return $true }
  return $false
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
  $supArgString = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$supervisorScript`" -BotPort $botPort -AnalyzerPort $analyzerPort -BridgePort $bridgePort -Quiet"
  Start-Process -FilePath "powershell" -ArgumentList $supArgString -WindowStyle Hidden | Out-Null
  Wd-Log "supervisor relaunch issued bot=:$botPort analyzer=:$analyzerPort"
} catch {
  Wd-Log "supervisor relaunch FAILED: $($_.Exception.Message)"
}

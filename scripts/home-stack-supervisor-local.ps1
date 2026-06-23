# 24/7 supervisor for local collection stack (7002/9500) - no Cloudflare tunnel.
param(
  [int]$BotPort = 7002,
  [int]$AnalyzerPort = 9500,
  [int]$BridgePort = 7810,
  [int]$IntervalSec = 90,
  [int]$FailThreshold = 3,
  [int]$BotCooldownSec = 480,
  [int]$AnalyzerCooldownSec = 480,
  [int]$BridgeCooldownSec = 300
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort -Port $BridgePort
. (Join-Path $scriptDir "home-stack-health.ps1")

$logFile = Join-Path $repoRoot ".home-stack-supervisor.log"
$lockFile = Join-Path $repoRoot ".home-stack-supervisor-local.lock"

function Log([string]$msg) {
  $line = "{0} [local] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue
}

function Test-SupervisorLock {
  try {
    $script:LockHandle = [System.IO.File]::Open($lockFile, [System.IO.FileMode]::OpenOrCreate, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    return $true
  } catch { return $false }
}

function Restart-BotLocal {
  Log "RECOVER local bot :$BotPort"
  Stop-ListenPortFast $BotPort | Out-Null
  Stop-PythonMatching "btc_conservative_agent" | Out-Null
  Stop-PythonMatching "bot.py" | Out-Null
  Start-Sleep -Seconds 2
  $botScript = Join-Path $scriptDir "start-local-collection-bot.ps1"
  cmd /c start "Local Collection Bot :$BotPort" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "$botScript" -NoWait
}

function Restart-AnalyzerLocal {
  Log "RECOVER local analyzer :$AnalyzerPort"
  Stop-PythonMatching "analyzer_research_engine" | Out-Null
  Stop-ListenPortFast $AnalyzerPort | Out-Null
  Remove-Item (Join-Path $repoRoot ".local-collection-analyzer.lock") -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2
  $analyzerScript = Join-Path $scriptDir "start-local-collection-analyzer.ps1"
  cmd /c start "Local Collection Analyzer :$AnalyzerPort" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "$analyzerScript" -NoWait
}

function Restart-BridgeLocal {
  Log "RECOVER bridge :$BridgePort"
  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*home-stack-launcher.ps1*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 2
  cmd /c start "Doxed Home Bridge :7810" powershell -NoExit -NoProfile -ExecutionPolicy Bypass -File "$scriptDir\home-stack-launcher.ps1"
}

if (-not (Test-SupervisorLock)) {
  Log "Another local supervisor already running - exit"
  exit 0
}

Log "Local collection supervisor started bot=:$BotPort analyzer=:$AnalyzerPort interval=${IntervalSec}s"

$botFails = 0
$analyzerFails = 0
$bridgeFails = 0
$lastBotRecover = [datetime]::MinValue
$lastAnalyzerRecover = [datetime]::MinValue
$lastBridgeRecover = [datetime]::MinValue

while ($true) {
  try {
    if (-not (Test-BotHealthy)) {
      $botFails++
      if ($botFails -ge $FailThreshold) {
        $since = ((Get-Date) - $lastBotRecover).TotalSeconds
        if ($since -ge $BotCooldownSec) {
          Restart-BotLocal
          $lastBotRecover = Get-Date
          $botFails = 0
        }
      }
    } else { $botFails = 0 }

    if (-not (Test-AnalyzerHealthy)) {
      $analyzerFails++
      if ($analyzerFails -ge $FailThreshold) {
        $since = ((Get-Date) - $lastAnalyzerRecover).TotalSeconds
        if ($since -ge $AnalyzerCooldownSec) {
          Restart-AnalyzerLocal
          $lastAnalyzerRecover = Get-Date
          $analyzerFails = 0
        }
      }
    } else { $analyzerFails = 0 }

    if (-not (Test-BridgeHealthy)) {
      $bridgeFails++
      if ($bridgeFails -ge $FailThreshold) {
        $since = ((Get-Date) - $lastBridgeRecover).TotalSeconds
        if ($since -ge $BridgeCooldownSec) {
          Restart-BridgeLocal
          $lastBridgeRecover = Get-Date
          $bridgeFails = 0
        }
      }
    } else { $bridgeFails = 0 }
  } catch {
    Log ("Loop error: " + $_.Exception.Message)
  }
  Start-Sleep -Seconds $IntervalSec
}

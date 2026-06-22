# Legacy alias — long-run recovery is handled by home-stack-supervisor.ps1
param(
  [int]$BotPort = 7800,
  [int]$IntervalSec = 120,
  [int]$RestartCooldownSec = 900,
  [string]$BridgeUrl = "http://127.0.0.1:7810"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$logFile = Join-Path $repoRoot ".home-tunnel-watchdog.log"
$line = "{0} tunnel-watchdog deprecated — starting home-stack-supervisor.ps1 instead" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Add-Content -Path $logFile -Value $line -ErrorAction SilentlyContinue

. (Join-Path $scriptDir "home-stack-common.ps1")
if (-not (Test-HomeScriptRunning "home-stack-supervisor.ps1")) {
  Start-HiddenPs1 (Join-Path $scriptDir "home-stack-supervisor.ps1") @("-BotPort", "$BotPort", "-IntervalSec", "$IntervalSec", "-TunnelCooldownSec", "$RestartCooldownSec")
}

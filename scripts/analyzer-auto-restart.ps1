# Retired compatibility entry point. Analyzer restarts are owned by the explicit
# home-stack launcher/supervisor; this script must never create a second owner.
param(
  [int]$AnalyzerPid = 0,
  [int]$Port = 9001,
  [string]$VaultEnv = ""
)

$ErrorActionPreference = "Stop"
Write-Output (
  "analyzer-auto-restart.ps1 is disabled fail-closed; " +
  "no process, lock, heartbeat, credential, or PID file was changed. " +
  "Use start-home-analyzer.ps1 for an explicit start."
)
exit 0

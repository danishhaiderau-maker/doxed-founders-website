# Frozen local collection ports (7002 / 9500) — dot-source to load $LocalCollection.*
$ErrorActionPreference = "Stop"
if (-not $scriptDir) {
  $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if (-not $repoRoot) {
  $repoRoot = Split-Path -Parent $scriptDir
}

$lockPath = Join-Path $repoRoot "config\local-collection.lock.json"
if (-not (Test-Path $lockPath)) {
  throw "Missing $lockPath"
}

$lock = Get-Content $lockPath -Raw | ConvertFrom-Json
if (-not $lock.frozen) {
  throw "local-collection.lock.json must have frozen=true"
}

$LocalCollection = @{
  BotPort       = [int]$lock.botPort
  AnalyzerPort  = [int]$lock.analyzerPort
  DataDir       = Join-Path $repoRoot ($lock.dataDirRelative -replace '/', '\')
  AgentDir      = Join-Path $repoRoot "services\btc-conservative-agent"
  LockPath      = $lockPath
  DisableRelay  = [bool]$lock.disableRelayWebhook
}

if ($LocalCollection.BotPort -ne 7002 -or $LocalCollection.AnalyzerPort -ne 9500) {
  throw "Local collection ports are frozen at bot=7002 analyzer=9500 (edit lock file only if intentional)."
}

if (-not (Test-Path $LocalCollection.DataDir)) {
  New-Item -ItemType Directory -Path $LocalCollection.DataDir -Force | Out-Null
}

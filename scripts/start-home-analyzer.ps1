# Load home-bot.env and run the research analyzer.
param([switch]$Once)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
$vaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"

Set-Location $agentDir
Get-Content $vaultEnv | ForEach-Object {
  if ($_ -match '^\s*([^#=]+)=(.*)$') {
    Set-Item -Path ("env:" + $matches[1].Trim()) -Value $matches[2].Trim()
  }
}

Write-Host "IMPORTANT: Analyzer reads CSV/JSONL from THIS folder only:"
Write-Host "  $agentDir"
Write-Host "Do NOT run analyzer from Final Bots root (stale/missing trades_3factor.csv)."
Write-Host ""

$args = @("research\analyzer_research_engine_v62.py")
if ($Once) { $args += "--once" }
Write-Host "Starting analyzer in $agentDir ..."
Write-Host "Dashboard: http://127.0.0.1:9001/ (embedded in analyzer when enabled)"
python @args

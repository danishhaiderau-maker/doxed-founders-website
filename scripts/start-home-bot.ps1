# Start BTC bot on home PC using vault/home-bot.env
param(
  [int]$Port = 7800,
  [string]$VaultEnv = ""
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir

if (-not $VaultEnv) {
  $VaultEnv = Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets\vault\home-bot.env"
}

if (-not (Test-Path $VaultEnv)) {
  Write-Host "Missing $VaultEnv - run: npm run print:home-bot-env"
  exit 1
}

Get-Content $VaultEnv | ForEach-Object {
  if ($_ -match '^\s*([^#=]+)=(.*)$') {
    $name = $matches[1].Trim()
    $val = $matches[2].Trim()
    Set-Item -Path "env:$name" -Value $val
  }
}

$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
if (-not (Test-Path $agentDir)) {
  Write-Host "Monorepo agent dir not found: $agentDir"
  exit 1
}

Set-Location $agentDir
Write-Host "Starting bot on port $Port from $agentDir ..."
Write-Host ('Dashboard: http://127.0.0.1:' + $Port)
python btc_conservative_agent.py

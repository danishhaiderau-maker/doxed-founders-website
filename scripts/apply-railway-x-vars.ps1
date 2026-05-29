# Writes Railway variable commands from .env.x.secrets (run after: railway login)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$vaultRoot = Join-Path (Split-Path $root -Parent) "doxedcryptofounder-secrets"
$secretsFile = Join-Path $vaultRoot "vault" ".env.x.secrets"
if (-not (Test-Path $secretsFile)) {
  $secretsFile = Join-Path $root ".env.x.secrets"
}
if (-not (Test-Path $secretsFile)) {
  Write-Host "Missing secrets. Run: npm run secrets:link" -ForegroundColor Red
  exit 1
}

function Read-DotEnv($path) {
  $map = @{}
  Get-Content $path | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $k = $line.Substring(0, $idx).Trim()
    $v = $line.Substring($idx + 1).Trim().Trim('"')
    if ($v) { $map[$k] = $v }
  }
  return $map
}

$env = Read-DotEnv $secretsFile
$keys = @(
  "TWITTER_BEARER_TOKEN",
  "TWITTER_API_KEY",
  "TWITTER_API_SECRET",
  "TWITTER_ACCESS_TOKEN",
  "TWITTER_ACCESS_TOKEN_SECRET",
  "X_BRAND_HANDLE",
  "PUBLIC_SITE_URL",
  "ADMIN_SYNC_JWT",
  "TRENDING_BUY_MIN_TRADERS",
  "TRADER_WIN_MIN_PNL_PERCENT"
)
if (-not $env["TRENDING_BUY_MIN_TRADERS"]) { $env["TRENDING_BUY_MIN_TRADERS"] = "5" }
if (-not $env["TRADER_WIN_MIN_PNL_PERCENT"]) { $env["TRADER_WIN_MIN_PNL_PERCENT"] = "50" }

Set-Location $root
foreach ($key in $keys) {
  $val = $env[$key]
  if (-not $val) { continue }
  Write-Host "Setting Railway: $key"
  railway variables set "${key}=$val" 2>&1
}

Write-Host "Done. Redeploy API on Railway." -ForegroundColor Green

# Applies SOLANA_RPC_URL + PLATFORM_SOLANA_TREASURY to Railway (run after: railway login)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$vaultRoot = Join-Path (Split-Path $root -Parent) "doxedcryptofounder-secrets"
$pasteFile = Join-Path (Join-Path $vaultRoot "vault") "railway-crypto-paste.env"
if (-not (Test-Path $pasteFile)) {
  Write-Host "Missing railway-crypto-paste.env - run: npm run bootstrap:crypto-topups" -ForegroundColor Red
  exit 1
}

function Read-DotEnv($path) {
  $map = @{}
  Get-Content $path | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $idx = $line.IndexOf("=")
    if ($idx -lt 1) { return }
    $map[$line.Substring(0, $idx).Trim()] = $line.Substring($idx + 1).Trim()
  }
  return $map
}

$envMap = Read-DotEnv $pasteFile
$keys = @("HELIUS_RPC_URL", "SOLANA_RPC_URL", "PLATFORM_SOLANA_TREASURY")

Set-Location $root
foreach ($key in $keys) {
  $val = $envMap[$key]
  if (-not $val) { continue }
  Write-Host "Setting Railway: $key"
  railway variables set "${key}=$val" 2>&1
}

Write-Host "Done. Redeploy API on Railway and verify reset-info." -ForegroundColor Green

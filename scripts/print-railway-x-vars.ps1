# Writes Railway Raw Editor block from .env.x.secrets -> railway-x-paste.env (gitignored)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$secretsFile = Join-Path $root ".env.x.secrets"
$outFile = Join-Path $root "railway-x-paste.env"
if (-not (Test-Path $secretsFile)) { throw "Missing .env.x.secrets" }

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

$lines = @()
foreach ($key in $keys) {
  $val = $env[$key]
  if ($val) { $lines += "$key=$val" }
}
$lines -join "`n" | Set-Content $outFile -NoNewline
Write-Host "Wrote $($lines.Count) vars to railway-x-paste.env (paste into Railway Raw Editor)"

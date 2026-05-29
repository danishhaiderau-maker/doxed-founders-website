# Removes secret copies from repo after vault migration (keeps canonical copies in vault only)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$vault = Join-Path (Split-Path $root -Parent) "doxedcryptofounder-secrets\vault"

$repoFiles = @(
  ".env",
  ".env.local",
  ".env.x.secrets",
  ".env.vercel.check",
  ".env.admin-rotate",
  ".env.neon",
  ".env.self-host",
  ".env.tunnel.local",
  ".env.vercel.prod",
  "railway-x-paste.env",
  "google-keys.txt",
  "apps\web\.env.local",
  "apps\web\.env.vercel.prod",
  "apps\.env.vercel.prod"
)

foreach ($rel in $repoFiles) {
  $repoPath = Join-Path $root $rel
  $vaultName = switch -Wildcard ($rel) {
    "apps\web\.env.local" { "apps-web.env.local" }
    "apps\web\.env.vercel.prod" { "apps-web.env.vercel.prod" }
    "apps\.env.vercel.prod" { "apps.env.vercel.prod" }
    default { Split-Path $rel -Leaf }
  }
  $vaultPath = Join-Path $vault $vaultName
  if ((Test-Path $repoPath) -and (Test-Path $vaultPath)) {
    Remove-Item -LiteralPath $repoPath -Force
    Write-Host "removed repo copy: $rel (vault is canonical)"
  }
}

Write-Host "`nRepo secret files removed. Dev loads from vault via scripts/load-vault-env.mjs"

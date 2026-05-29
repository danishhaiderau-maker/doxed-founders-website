# Moves sensitive env files into ../doxedcryptofounder-secrets/vault (outside git)
# and creates symlinks back so local dev still works.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$vaultRoot = Join-Path (Split-Path $root -Parent) "doxedcryptofounder-secrets"
$vault = Join-Path $vaultRoot "vault"

New-Item -ItemType Directory -Force -Path $vault | Out-Null

$mapping = @(
  @{ Repo = ".env"; Vault = ".env" },
  @{ Repo = ".env.local"; Vault = ".env.local" },
  @{ Repo = ".env.x.secrets"; Vault = ".env.x.secrets" },
  @{ Repo = ".env.vercel.check"; Vault = ".env.vercel.check" },
  @{ Repo = ".env.admin-rotate"; Vault = ".env.admin-rotate" },
  @{ Repo = ".env.neon"; Vault = ".env.neon" },
  @{ Repo = ".env.self-host"; Vault = ".env.self-host" },
  @{ Repo = ".env.tunnel.local"; Vault = ".env.tunnel.local" },
  @{ Repo = ".env.vercel.prod"; Vault = ".env.vercel.prod" },
  @{ Repo = "railway-x-paste.env"; Vault = "railway-x-paste.env" },
  @{ Repo = "google-keys.txt"; Vault = "google-keys.txt" },
  @{ Repo = "apps\web\.env.local"; Vault = "apps-web.env.local" },
  @{ Repo = "apps\web\.env.vercel.prod"; Vault = "apps-web.env.vercel.prod" },
  @{ Repo = "apps\.env.vercel.prod"; Vault = "apps.env.vercel.prod" }
)

function Link-Or-Copy($repoRel, $vaultName) {
  $repoPath = Join-Path $root $repoRel
  $vaultPath = Join-Path $vault $vaultName
  $repoDir = Split-Path $repoPath -Parent
  if ($repoDir -and -not (Test-Path $repoDir)) {
    New-Item -ItemType Directory -Force -Path $repoDir | Out-Null
  }

  if (Test-Path $repoPath) {
    $item = Get-Item $repoPath -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
      Write-Host "  skip (already linked): $repoRel"
      return
    }
    if (-not (Test-Path $vaultPath)) {
      Move-Item -LiteralPath $repoPath -Destination $vaultPath -Force
      Write-Host "  locked: $repoRel -> vault\$vaultName"
    }
  }

  if (Test-Path $vaultPath) {
    if (-not (Test-Path $repoPath)) {
      try {
        New-Item -ItemType SymbolicLink -Path $repoPath -Target $vaultPath -Force | Out-Null
        Write-Host "  linked: $repoRel"
      } catch {
        Write-Host "  vault only: $repoRel (use npm run secrets:link as Admin for symlinks)"
      }
    }
  }
}

Write-Host "Secrets vault: $vaultRoot"
Write-Host "Never commit or share vault contents with ChatGPT.`n"

foreach ($m in $mapping) {
  Link-Or-Copy $m.Repo $m.Vault
}

$readme = @"
DOXXED CRYPTO - SECRETS VAULT (LOCAL ONLY)
==========================================
This folder is OUTSIDE the git repository.
- Do NOT commit to GitHub
- Do NOT paste into ChatGPT or public audits
- Do NOT upload to cloud drives shared publicly

Contains: API keys, DB URLs, JWT secrets, OAuth tokens, Railway/Vercel env.

To relink after clone: npm run secrets:link
To export code-only audit bundle: npm run audit:export
"@
Set-Content -Path (Join-Path $vaultRoot "README.txt") -Value $readme -Encoding UTF8

Write-Host "`nDone. Vault secured at:`n  $vaultRoot"

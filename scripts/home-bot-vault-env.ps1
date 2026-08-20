# Canonical home-bot.env resolution for Fly admin calls.
# Vault home-bot.env wins over a stale shell/user BOT_ADMIN_TOKEN (matches Fly secrets).

$HomeBotVaultEnvScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }

function Get-DcfSecretsVaultRoot {
  if ($env:DCF_SECRETS_VAULT) {
    return [System.IO.Path]::GetFullPath($env:DCF_SECRETS_VAULT)
  }
  $repoRoot = if ($script:repoRoot) { $script:repoRoot } else { Split-Path -Parent $HomeBotVaultEnvScriptDir }
  return [System.IO.Path]::GetFullPath((Join-Path (Split-Path -Parent $repoRoot) "doxedcryptofounder-secrets"))
}

function Get-HomeBotVaultEnvPath {
  Join-Path (Join-Path (Get-DcfSecretsVaultRoot) "vault") "home-bot.env"
}

function Read-HomeBotVaultEnvValue {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [string]$VaultEnvPath = ""
  )
  if (-not $VaultEnvPath) { $VaultEnvPath = Get-HomeBotVaultEnvPath }
  if (-not (Test-Path -LiteralPath $VaultEnvPath)) { return "" }
  $pattern = '^\s*' + [regex]::Escape($Name) + '=(.*)$'
  $line = Get-Content -LiteralPath $VaultEnvPath -ErrorAction SilentlyContinue |
    Where-Object { $_ -match $pattern } |
    Select-Object -Last 1
  if (-not $line) { return "" }
  if ($line -match $pattern) {
    return [string]$matches[1].Trim().Trim('"').Trim("'")
  }
  return ""
}

function Resolve-CanonicalBotAdminToken {
  param([string]$VaultEnvPath = "")
  $fromVault = Read-HomeBotVaultEnvValue -Name "BOT_ADMIN_TOKEN" -VaultEnvPath $VaultEnvPath
  if ($fromVault) { return $fromVault }
  $fromProcess = [Environment]::GetEnvironmentVariable("BOT_ADMIN_TOKEN", "Process")
  if ($fromProcess) { return [string]$fromProcess }
  return ""
}

function Import-CanonicalBotAdminToken {
  param([string]$VaultEnvPath = "")
  $token = Resolve-CanonicalBotAdminToken -VaultEnvPath $VaultEnvPath
  if ($token) {
    [Environment]::SetEnvironmentVariable("BOT_ADMIN_TOKEN", $token, "Process")
  }
  return $token
}

function Import-HomeBotVaultConfig {
  param(
    [string[]]$Names = @("BOT_ADMIN_TOKEN", "PLATFORM_API_BASE_URL", "PLATFORM_RELAY_AGENT_SLUG", "PLATFORM_RELAY_USER_ID"),
    [string]$VaultEnvPath = ""
  )
  if (-not $VaultEnvPath) { $VaultEnvPath = Get-HomeBotVaultEnvPath }
  if (-not (Test-Path -LiteralPath $VaultEnvPath)) { return }
  foreach ($configName in $Names) {
    $value = Read-HomeBotVaultEnvValue -Name $configName -VaultEnvPath $VaultEnvPath
    if (-not $value) { continue }
    $shouldLoad = $configName -eq "BOT_ADMIN_TOKEN" -or
      -not [Environment]::GetEnvironmentVariable($configName, "Process")
    if ($shouldLoad) {
      [Environment]::SetEnvironmentVariable($configName, $value, "Process")
    }
  }
}
# Resolves home stack ports: production showcase (7002/9500) vs legacy local collection.
param(
  [string]$RepoRoot = ""
)

if (-not $RepoRoot) {
  if (-not $repoRoot) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RepoRoot = Split-Path -Parent $scriptDir
  } else {
    $RepoRoot = $repoRoot
  }
}

function Get-HomeStackMode {
  param([string]$Root = $RepoRoot)

  $showcaseLock = Join-Path $Root "config\home-showcase.lock.json"
  $legacyLock = Join-Path $Root "config\local-collection.lock.json"
  $prodFlag = Join-Path $Root ".home-production-mode"
  $localFlag = Join-Path $Root ".local-collection-mode"

  if (Test-Path $showcaseLock) {
    $lock = Get-Content $showcaseLock -Raw | ConvertFrom-Json
    if ($lock.frozen) {
      $isProduction = "$($lock.mode)" -eq "production" -or -not [bool]$lock.disableTunnel
      return @{
        Mode          = if ($isProduction) { "production" } else { "local-collection" }
        BotPort       = [int]$lock.botPort
        AnalyzerPort  = [int]$lock.analyzerPort
        TunnelEnabled = -not [bool]$lock.disableTunnel
        RelayEnabled  = -not [bool]$lock.disableRelayWebhook
        DataDir       = Join-Path $Root ($lock.dataDirRelative -replace '/', '\')
        Label         = if ($isProduction) {
          "Global showcase :$($lock.botPort)/:$($lock.analyzerPort) (doxxedcrypto + tunnel)"
        } else {
          "Local collection (frozen :$($lock.botPort)/:$($lock.analyzerPort))"
        }
      }
    }
  }

  if ((Test-Path $localFlag) -and -not (Test-Path $prodFlag) -and (Test-Path $legacyLock)) {
    $lock = Get-Content $legacyLock -Raw | ConvertFrom-Json
    if ($lock.frozen) {
      return @{
        Mode          = "local-collection"
        BotPort       = [int]$lock.botPort
        AnalyzerPort  = [int]$lock.analyzerPort
        TunnelEnabled = $false
        RelayEnabled  = $false
        DataDir       = Join-Path $Root ($lock.dataDirRelative -replace '/', '\')
        Label         = "Local collection only (no tunnel / no relay)"
      }
    }
  }

  return @{
    Mode          = "production"
    BotPort       = 7800
    AnalyzerPort  = 9001
    TunnelEnabled = $true
    RelayEnabled  = $true
    DataDir       = Join-Path $Root "services\btc-conservative-agent"
    Label         = "Production mirror (legacy :7800/:9001)"
  }
}

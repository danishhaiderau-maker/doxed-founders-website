# Resolves active home stack ports (local collection 7002/9500 vs production 7800/9001).
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

$lockPath = Join-Path $RepoRoot "config\local-collection.lock.json"
$modeFlag = Join-Path $RepoRoot ".local-collection-mode"
$prodFlag = Join-Path $RepoRoot ".home-production-mode"

function Get-HomeStackMode {
  param([string]$Root = $RepoRoot)

  $lockPath = Join-Path $Root "config\local-collection.lock.json"
  $modeFlag = Join-Path $Root ".local-collection-mode"
  $prodFlag = Join-Path $Root ".home-production-mode"

  if ((Test-Path $prodFlag) -and -not (Test-Path $modeFlag)) {
    return @{
      Mode          = "production"
      BotPort       = 7800
      AnalyzerPort  = 9001
      TunnelEnabled = $true
      DataDir       = Join-Path $Root "services\btc-conservative-agent"
      Label         = "Production mirror (doxxedcrypto)"
    }
  }

  if (Test-Path $lockPath) {
    $lock = Get-Content $lockPath -Raw | ConvertFrom-Json
    if ($lock.frozen) {
      return @{
        Mode          = "local-collection"
        BotPort       = [int]$lock.botPort
        AnalyzerPort  = [int]$lock.analyzerPort
        TunnelEnabled = -not [bool]$lock.disableTunnel
        DataDir       = Join-Path $Root ($lock.dataDirRelative -replace '/', '\')
        Label         = "Local collection (frozen :$($lock.botPort)/:$($lock.analyzerPort))"
      }
    }
  }

  return @{
    Mode          = "production"
    BotPort       = 7800
    AnalyzerPort  = 9001
    TunnelEnabled = $true
    DataDir       = Join-Path $Root "services\btc-conservative-agent"
    Label         = "Production mirror (doxxedcrypto)"
  }
}

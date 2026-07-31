# Resolve the only supported desktop role: Fly dashboard/data/analyzer mirror.
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

  $flyLockPath = Join-Path $Root "config\fly-canonical.lock.json"
  $mirrorLockPath = Join-Path $Root "config\home-showcase.lock.json"
  if (-not (Test-Path $flyLockPath) -or -not (Test-Path $mirrorLockPath)) {
    throw "Canonical Fly/mirror locks are missing; refusing legacy home-stack fallback."
  }

  $fly = Get-Content $flyLockPath -Raw | ConvertFrom-Json
  $mirror = Get-Content $mirrorLockPath -Raw | ConvertFrom-Json
  if (
    -not [bool]$fly.frozen -or
    [bool]$fly.desktopBotEnabled -or
    "$($mirror.mode)" -ne "fly-mirror" -or
    -not [bool]$mirror.disableLocalStrategy
  ) {
    throw "Canonical Fly/mirror locks are inconsistent; refusing startup."
  }

  return @{
    Mode = "fly-mirror"
    BotPort = [int]$mirror.botPort
    AnalyzerPort = [int]$mirror.analyzerPort
    TunnelEnabled = $false
    RelayEnabled = $false
    LocalStrategyEnabled = $false
    SourceUrl = [string]$fly.sourceUrl
    DataDir = Join-Path $Root ($mirror.dataDirRelative -replace '/', '\')
    Label = "Fly dashboard proxy :$($mirror.botPort) + analyzer mirror :$($mirror.analyzerPort)"
  }
}

$script:FlyCanonicalRepoRoot = Split-Path -Parent $PSScriptRoot
$script:FlyCanonicalLockPath = Join-Path $script:FlyCanonicalRepoRoot "config\fly-canonical.lock.json"

function Get-CanonicalFlyBotUrl {
  param([string]$RequestedUrl = "")

  if (-not (Test-Path -LiteralPath $script:FlyCanonicalLockPath)) {
    throw "Canonical Fly lock is missing: $script:FlyCanonicalLockPath"
  }
  $lock = Get-Content -LiteralPath $script:FlyCanonicalLockPath -Raw | ConvertFrom-Json
  $expected = [string]$lock.sourceUrl
  if (
    $lock.frozen -ne $true -or
    $lock.desktopBotEnabled -ne $false -or
    -not $expected
  ) {
    throw "Canonical Fly lock is not frozen in desktop-mirror mode."
  }
  $expected = $expected.TrimEnd("/")
  $uri = [uri]$expected
  if (
    $uri.Scheme -ne "https" -or
    $uri.Host -ne "doxed-btc-bot.fly.dev" -or
    $uri.AbsolutePath -ne "/"
  ) {
    throw "Canonical Fly lock contains an unexpected source URL."
  }
  if ($RequestedUrl -and $RequestedUrl.TrimEnd("/") -ne $expected) {
    throw "REFUSED_NON_CANONICAL_UPSTREAM: expected $expected"
  }
  return $expected
}

param(
  [switch]$LocalBuild
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$serviceRoot = Join-Path $repoRoot "services\btc-conservative-agent"

if (-not (Test-Path -LiteralPath (Join-Path $serviceRoot "Dockerfile"))) {
  throw "BTC Fly Dockerfile is missing from the canonical service context."
}
if (-not (Test-Path -LiteralPath (Join-Path $serviceRoot ".dockerignore"))) {
  throw "BTC Fly whitelist .dockerignore is missing from the canonical service context."
}

$revision = (& git -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $revision -notmatch '^[0-9a-f]{40}$') {
  throw "Unable to resolve the exact source revision."
}

# A Fly replacement removes the Showcase owner briefly. Require the complete
# source + Neon + authenticated Bitfinex boundary to be paused, disarmed, flat,
# and reconciled before starting the deployment.
$priorCanonicalOwner = $env:REQUIRE_CANONICAL_FLY_OWNER
$priorAdminProof = $env:REQUIRE_BOT_ADMIN_TOKEN
$priorOwnerUrl = $env:SHOWCASE_OWNER_URL
try {
  $env:REQUIRE_CANONICAL_FLY_OWNER = "YES"
  $env:REQUIRE_BOT_ADMIN_TOKEN = "YES"
  $env:SHOWCASE_OWNER_URL = "https://doxed-btc-bot.fly.dev"
  & node (Join-Path $PSScriptRoot "check-relay-flat.mjs")
  if ($LASTEXITCODE -ne 0) {
    throw "Flat-boundary proof failed; Fly deployment refused."
  }
} finally {
  $env:REQUIRE_CANONICAL_FLY_OWNER = $priorCanonicalOwner
  $env:REQUIRE_BOT_ADMIN_TOKEN = $priorAdminProof
  $env:SHOWCASE_OWNER_URL = $priorOwnerUrl
}

$deployArgs = @(
  "deploy",
  "--app", "doxed-btc-bot",
  "--config", "fly.toml",
  "--strategy", "immediate",
  "--build-arg", "SOURCE_GIT_REV=$revision"
)
if (-not $LocalBuild) {
  $deployArgs += "--remote-only"
}

# The working directory is part of the safety contract: it selects the BTC
# Dockerfile and its whitelist context instead of the monorepo API Dockerfile.
Push-Location $serviceRoot
try {
  & flyctl.exe @deployArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Fly deployment failed."
  }
} finally {
  Pop-Location
}

$expected = $revision.Substring(0, 12)
$lastHealth = $null
for ($attempt = 0; $attempt -lt 60; $attempt++) {
  try {
    $lastHealth = Invoke-RestMethod `
      -Uri "https://doxed-btc-bot.fly.dev/health" `
      -TimeoutSec 8
    if (
      $lastHealth.process_alive -eq $true -and
      [string]$lastHealth.source_git_rev -like "$expected*" -and
      $lastHealth.live_armed -eq $false -and
      $lastHealth.bitfinex_live_enabled -eq $false -and
      $lastHealth.force_paper_mode -eq $true
    ) {
      [pscustomobject]@{
        ok = $true
        app = "doxed-btc-bot"
        sourceRevision = [string]$lastHealth.source_git_rev
        buildContext = $serviceRoot
        liveArmed = $lastHealth.live_armed
        forcePaperMode = $lastHealth.force_paper_mode
      } | ConvertTo-Json
      exit 0
    }
  } catch {
    # The immediate deployment has a bounded period without a healthy route.
  }
  Start-Sleep -Seconds 5
}

throw "Fly health did not prove exact disarmed revision $expected after deployment."

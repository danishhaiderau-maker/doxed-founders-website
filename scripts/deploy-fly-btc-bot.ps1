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
$priorPlatformApiUrl = $env:PLATFORM_API_URL
try {
  $env:REQUIRE_CANONICAL_FLY_OWNER = "YES"
  $env:REQUIRE_BOT_ADMIN_TOKEN = "YES"
  $env:SHOWCASE_OWNER_URL = "https://doxed-btc-bot.fly.dev"
  if (-not $env:PLATFORM_API_URL) {
    $env:PLATFORM_API_URL = "https://doxed-founders-website-production.up.railway.app/api"
  }
  & node (Join-Path $PSScriptRoot "check-relay-flat.mjs")
  if ($LASTEXITCODE -ne 0) {
    throw "Flat-boundary proof failed; Fly deployment refused."
  }
} finally {
  $env:REQUIRE_CANONICAL_FLY_OWNER = $priorCanonicalOwner
  $env:REQUIRE_BOT_ADMIN_TOKEN = $priorAdminProof
  $env:SHOWCASE_OWNER_URL = $priorOwnerUrl
  $env:PLATFORM_API_URL = $priorPlatformApiUrl
}
# Resolve a local interpreter before the guarded deployment starts.  Some
# managed Windows hosts intentionally have no global ``python`` command even
# though Codex supplies a bundled stdlib runtime.  The registry read is local
# and deterministic; failing closed here is safer than bypassing the registry
# parity fence or silently deploying a different service context.
$pythonCandidates = @()
if ($env:PYTHON_EXE) {
  $pythonCandidates += [string]$env:PYTHON_EXE
}
$systemPython = Get-Command python -ErrorAction SilentlyContinue
if ($systemPython -and $systemPython.Source) {
  $pythonCandidates += [string]$systemPython.Source
}
if ($env:LOCALAPPDATA) {
  $pythonCandidates += (Join-Path $env:LOCALAPPDATA "codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
}
if ($env:USERPROFILE) {
  $pythonCandidates += (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
}
$pythonExe = @($pythonCandidates | Where-Object {
  $_ -and (Test-Path -LiteralPath $_)
} | Select-Object -First 1)[0]
if (-not $pythonExe) {
  throw "No local Python interpreter is available for the tile-registry deploy fence. Set PYTHON_EXE or install Python."
}
$registryJson = & $pythonExe -c "import json,sys;sys.path.insert(0,sys.argv[1]);import combo_pathway_config as c;print(json.dumps({'version':c.EXECUTION_FIX_VERSION,'signature':c.active_tile_registry_signature(),'lanes':list(c.ACTIVE_TILE_ORDER)}))" $serviceRoot
if ($LASTEXITCODE -ne 0) {
  throw "Unable to resolve the canonical tile registry contract."
}
$registry = $registryJson | ConvertFrom-Json

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
$lastReady = $null
# Strict flatness is proved immediately before Fly replacement above. After
# startup, paper-only research is expected to resume and may legitimately open
# paper positions or orders before this readiness loop observes the runtime.
# Post-deploy acceptance therefore verifies the live boundary and progressing
# paper runtime, not a second flat snapshot.
for ($attempt = 0; $attempt -lt 60; $attempt++) {
  try {
    $lastHealth = Invoke-RestMethod `
      -Uri "https://doxed-btc-bot.fly.dev/api/status" `
      -TimeoutSec 8
    $lastReady = Invoke-RestMethod `
      -Uri "https://doxed-btc-bot.fly.dev/ready" `
      -TimeoutSec 8
    $runtimeLanes = @($lastHealth.active_tiles | ForEach-Object { [string]$_.lane })
    $laneParity = ($runtimeLanes.Count -eq @($registry.lanes).Count) -and `
      ((Compare-Object -ReferenceObject @($registry.lanes) -DifferenceObject $runtimeLanes -SyncWindow 0).Count -eq 0)
    if (
      $lastHealth.process_alive -eq $true -and
      $lastReady.ok -eq $true -and
      [string]$lastHealth.source_git_rev -like "$expected*" -and
      [string]$lastHealth.bot_version -eq [string]$registry.version -and
      [string]$lastHealth.tile_registry_signature -eq [string]$registry.signature -and
      [string]$lastReady.tile_registry_signature -eq [string]$registry.signature -and
      $laneParity -and
      $lastHealth.strategy_progress.ok -eq $true -and
      $lastHealth.strategy_progress.trade_lock_available -eq $true -and
      $lastHealth.live_armed -eq $false -and
      $lastHealth.bitfinex_live_enabled -eq $false -and
      $lastHealth.force_paper_mode -eq $true
    ) {
      [pscustomobject]@{
        ok = $true
        app = "doxed-btc-bot"
        sourceRevision = [string]$lastHealth.source_git_rev
        botVersion = [string]$lastHealth.bot_version
        tileRegistrySignature = [string]$lastHealth.tile_registry_signature
        activeTileLanes = $runtimeLanes
        buildContext = $serviceRoot
        liveArmed = $lastHealth.live_armed
        forcePaperMode = $lastHealth.force_paper_mode
        paperOpenPositions = [int]$lastHealth.strategy_progress.open_positions
        paperPendingOrders = [int]$lastHealth.strategy_progress.pending_orders
      } | ConvertTo-Json
      exit 0
    }
  } catch {
    # The immediate deployment has a bounded period without a healthy route.
  }
  Start-Sleep -Seconds 5
}

throw "Fly health did not prove exact disarmed revision $expected after deployment."

# Local command bridge â€” Agent Hub admin panel â†’ home PC (bot, analyzer, tunnel, wire).
# Listen: http://127.0.0.1:7810
# Run once: RESTART-LAUNCHER.cmd

param(
  [int]$Port = 7810,
  [int]$BotPort = 0,
  [int]$AnalyzerPort = 0,
  [switch]$Force
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:LegacyOwnerOptInName = "DCF_ENABLE_OBSOLETE_WINDOWS_TRADING_OWNER"
$script:LegacyOwnerOptInPhrase = "I_UNDERSTAND_THIS_STARTS_A_SECOND_AI_TRADING_OWNER"
. (Join-Path $scriptDir "home-stack-mode.ps1")
$stackMode = Get-HomeStackMode
if ($BotPort -le 0) { $BotPort = $stackMode.BotPort }
if ($AnalyzerPort -le 0) { $AnalyzerPort = $stackMode.AnalyzerPort }
. (Join-Path $scriptDir "home-stack-common.ps1") -BridgePort $Port -BotPort $BotPort -AnalyzerPort $AnalyzerPort
. (Join-Path $scriptDir "home-stack-health.ps1")
# Load HttpClient before the single-threaded listener accepts requests. On this
# Windows host the first Add-Type can take several seconds under disk pressure;
# paying that startup cost here keeps the first /status request cancellation-
# bounded by the explicit probe deadlines below.
try { Add-Type -AssemblyName System.Net.Http -ErrorAction Stop } catch {
  Write-Warning "System.Net.Http preload failed: $($_.Exception.Message)"
}
$prefix = "http://127.0.0.1:$Port/"

function Test-LegacyWindowsOwnerOptIn {
  $actual = (Get-Item -Path "env:$($script:LegacyOwnerOptInName)" -ErrorAction SilentlyContinue).Value
  return ($actual -ceq $script:LegacyOwnerOptInPhrase)
}

function Get-LegacyStartRefusal([string]$RequestedAction) {
  return @{
    ok = $false
    error = (
      "REFUSED '$RequestedAction': this obsolete Windows path can start a second AI/strategy owner " +
      "or Cloudflare tunnel. Fly.io remains the sole production owner. Use start-mirror, " +
      "start-analyzer, or start-analyzer-once. Disaster recovery requires the exact audited " +
      "$($script:LegacyOwnerOptInName) phrase in this bridge process."
    )
    architecture_owner = "fly.io"
    legacy_opt_in_required = $true
  }
}

function Invoke-FlyDesktopMirror {
  try {
    & (Join-Path $scriptDir "start-fly-desktop-mirror.ps1") -NoWait
    return @{
      ok = $true
      message = "Fly desktop mirror started: :7002 proxy, data sync, and :9001 analyzer."
      architecture_owner = "fly.io"
    }
  } catch {
    return @{ ok = $false; error = "Fly desktop mirror failed to start: $($_.Exception.Message)" }
  }
}

function Stop-RecordedMirrorProcess(
  [string]$MarkerName,
  [string[]]$ExpectedCommandFragments
) {
  $markerPath = Join-Path $repoRoot $MarkerName
  if (-not (Test-Path -LiteralPath $markerPath)) { return }
  try {
    $recordedPid = [int](Get-Content -LiteralPath $markerPath -Raw)
    if ($recordedPid -gt 0) {
      $processAlive = Test-ProcessIdAliveFast $recordedPid
      $commandLine = [string](Get-ProcessCommandLineFast $recordedPid)
      $matchesExpectedProcess = @(
        $ExpectedCommandFragments |
          Where-Object { $commandLine -like "*$_*" }
      ).Count -gt 0
      if ($processAlive -and $matchesExpectedProcess) {
        Stop-Process -Id $recordedPid -Force -ErrorAction SilentlyContinue
      }
    }
  } catch { }
  Remove-Item -LiteralPath $markerPath -Force -ErrorAction SilentlyContinue
}

function Invoke-ResetFlyDesktopMirror {
  # Stop only explicitly recorded desktop mirror/analyzer processes. Never
  # enumerate or stop the canonical Fly machine, a local strategy process, or
  # the :7810 bridge serving this request.
  Stop-RecordedMirrorProcess ".fly-dashboard-proxy.pid" @("fly-dashboard-proxy.py")
  Stop-RecordedMirrorProcess ".fly-data-sync-loop.lock" @("sync-fly-bot-data-loop.ps1")
  Stop-RecordedMirrorProcess ".home-analyzer-crash-monitor.pid" @("analyzer-auto-restart.ps1")
  Stop-RecordedMirrorProcess ".home-analyzer-starter.pid" @("start-home-analyzer.ps1")
  Stop-RecordedMirrorProcess ".home-analyzer-dashboard.pid" @("research_dashboard.py")
  Stop-RecordedMirrorProcess ".home-analyzer.pid" @("analyzer_research_engine_v62.py")
  foreach ($marker in @(
    ".fly-data-sync-loop.heartbeat.json",
    ".home-analyzer-start.lock",
    ".home-analyzer-auto-restart.lock",
    ".home-analyzer-auto-restart.heartbeat"
  )) {
    Remove-Item -LiteralPath (Join-Path $repoRoot $marker) -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Milliseconds 500
  return (Invoke-FlyDesktopMirror)
}

function Import-BotAdminToken {
  . (Join-Path $scriptDir "home-bot-vault-env.ps1")
  return (Resolve-CanonicalBotAdminToken)
}

$script:BotAdminToken = Import-BotAdminToken

# Defense-in-depth: if another launcher instance already has :$Port bound and
# healthy, skip the bind instead of crashing on http.sys prefix conflict. This
# handles duplicate launches (e.g. user runs Start Everything again, or opens a
# second bridge window) without needing admin urlacl fixes. ensure-home-bridge.ps1
# passes -Force after killing the old bridge, so genuine restarts still bind.
function Test-BridgeAlreadyBound([int]$ProbePort) {
  try {
    $req = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$ProbePort/health")
    $req.Method = "GET"
    # /status is serialized and can hold the listener for up to ~5.5s while
    # bounded bot/analyzer/tunnel probes finish. Wait long enough to avoid
    # misclassifying that bounded work as a dead bridge.
    $req.Timeout = 7000
    $req.ReadWriteTimeout = 7000
    $resp = $req.GetResponse()
    $ok = ($resp.StatusCode -eq 200)
    $resp.Close()
    return $ok
  } catch {
    return $false
  }
}

if (Test-BridgeAlreadyBound $Port) {
  $Host.UI.RawUI.WindowTitle = "Doxed Home Bridge :$Port (already running)"
  Write-Host "Bridge already OK on :$Port (skipping second listener) - auto-closing duplicate window." -ForegroundColor Green
  # The launcher is invoked through cmd /c, so exiting here also closes its
  # short-lived parent. Avoid Win32_Process: that provider can stall the bridge.
  exit 0
}

function Write-Cors {
  param(
    [System.Net.HttpListenerRequest]$Request,
    [System.Net.HttpListenerResponse]$Response
  )
  $allowed = @(
    "https://doxxedcrypto.digital",
    "https://www.doxxedcrypto.digital",
    "http://localhost:3000",
    "http://127.0.0.1:3000"
  )
  $origin = $Request.Headers["Origin"]
  if ($origin -and ($allowed -contains $origin)) {
    $Response.Headers.Add("Access-Control-Allow-Origin", $origin)
  } else {
    $Response.Headers.Add("Access-Control-Allow-Origin", "https://doxxedcrypto.digital")
  }
  $Response.Headers.Add("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
  $Response.Headers.Add("Access-Control-Allow-Headers", "Content-Type")
  $Response.Headers.Add("Access-Control-Allow-Private-Network", "true")
}

function Send-Json {
  param(
    [System.Net.HttpListenerResponse]$Response,
    [object]$Payload,
    [int]$Status = 200
  )
  $json = ($Payload | ConvertTo-Json -Compress -Depth 8)
  $bytes = [System.Text.Encoding]::UTF8.GetBytes($json)
  $Response.ContentType = "application/json; charset=utf-8"
  $Response.StatusCode = $Status
  $Response.OutputStream.Write($bytes, 0, $bytes.Length)
  $Response.Close()
}

function Get-FullStatus {
  # HTTP liveness for bot/analyzer (was 400ms TCP-only, which produced false "offline"
  # flicker when the listening socket was momentarily slow to handshake under load).
  # HTTP /api/ping confirms the server actually answers, not just that the port is bound.
  # Timeouts are tight (1.5s) so the single-threaded listener never blocks long; the
  # 4s status cache means this runs at most every 4s.
  $now = Get-Date
  if ($script:StatusCache.payload -and ($now - $script:StatusCache.at).TotalSeconds -lt 4) {
    return $script:StatusCache.payload
  }

  $botUrl = "http://127.0.0.1:$BotPort/api/ping"
  $anUrl = "http://127.0.0.1:$AnalyzerPort/"
  # Parallel HTTP probes (async) so the two local pings complete in ~1.5s instead of ~3s
  # â€” halves the window during which the single-threaded listener could delay a /health.
  $live = Test-HttpAliveParallel @($botUrl, $anUrl) 1500
  $botOnline = $live[$botUrl]
  $analyzerRunning = $live[$anUrl]
  $tunnelUrl = Get-TunnelUrl
  if (-not $tunnelUrl) { $tunnelUrl = "https://bot.doxxedcrypto.digital" }
  # Never enumerate Windows processes here. On this host the process provider can
  # stall for minutes, and the bridge deliberately serializes requests; one stuck
  # /status would therefore block /health and every recovery command behind it.
  # Probe the public route at most every 120s and distinguish an answering
  # connector with a temporarily unavailable bot origin (502/504) from no route.
  $tunnelLive = $false
  $cloudflaredRunning = $false
  if ($tunnelUrl) {
    if ($script:BridgeTunnelCache.url -eq $tunnelUrl -and ($now - $script:BridgeTunnelCache.at).TotalSeconds -lt 120) {
      $tunnelLive = [bool]$script:BridgeTunnelCache.live
      $cloudflaredRunning = [bool]$script:BridgeTunnelCache.connector
    } else {
      $probe = Test-TunnelHttpSmart -Url $tunnelUrl -TimeoutSec 4 -UserAgent "dcf-home-bridge/1.0"
      $tunnelLive = [bool]$probe.Healthy
      $cloudflaredRunning = Test-TunnelConnectorPresent $probe
      $script:BridgeTunnelCache = @{
        url = $tunnelUrl
        live = $tunnelLive
        connector = $cloudflaredRunning
        at = $now
      }
    }
  }
  $payload = @{
    ok = $true
    launcher = "running"
    mode = "production"
    stackLabel = "Doxxedcrypto global showcase :$BotPort / :$AnalyzerPort"
    ports = @{
      bot = $BotPort
      analyzer = $AnalyzerPort
      launcher = $Port
    }
    bot = @{
      online = $botOnline
      dashboard = "http://127.0.0.1:$BotPort"
      lan = "http://10.0.0.102:$BotPort"
      dataDir = $agentDir
    }
    analyzer = @{
      online = $analyzerRunning
      dashboard = "http://127.0.0.1:$AnalyzerPort/"
      lan = "http://10.0.0.102:$AnalyzerPort/"
      note = "Showcase stack for doxxedcrypto.digital - bot :$BotPort feeds site + Bitfinex relay via tunnel."
    }
    tunnel = @{
      url = $tunnelUrl
      live = $tunnelLive
      cloudflaredRunning = $cloudflaredRunning
      enabled = $stackMode.TunnelEnabled
    }
  }
  $script:StatusCache = @{ at = $now; payload = $payload }
  return $payload
}

$script:StatusCache = @{ at = [datetime]::MinValue; payload = $null }
$script:BridgeTunnelCache = @{
  url = ""
  live = $false
  connector = $false
  at = [datetime]::MinValue
}

function Invoke-HomeCommandBackground([string]$Action) {
  $modeArg = if ($stackMode.Mode -eq "local-collection") { "local-collection" } else { "production" }
  Start-HiddenPs1 (Join-Path $scriptDir "home-stack-cmd-worker.ps1") @(
    "-Action", $Action,
    "-BotPort", "$BotPort",
    "-AnalyzerPort", "$AnalyzerPort",
    "-StackMode", $modeArg
  )
}

function Invoke-TradingControl([ValidateSet("pause", "resume")][string]$Action) {
  if (-not (Test-PortOpen $BotPort)) {
    return @{ ok = $false; error = "Bot not running on :$BotPort - click Start showcase first" }
  }
  try {
    $headers = @{}
    if ($script:BotAdminToken) {
      $headers["X-Bot-Admin-Token"] = $script:BotAdminToken
    }
    $result = Invoke-RestMethod `
      -Uri "http://127.0.0.1:$BotPort/api/$Action" `
      -Method POST `
      -Headers $headers `
      -TimeoutSec 12
    $status = Invoke-RestMethod `
      -Uri "http://127.0.0.1:$BotPort/api/status" `
      -TimeoutSec 12
    $wantPaused = $Action -eq "pause"
    $confirmed = [bool]$status.execution_paused -eq $wantPaused
    if (-not $confirmed) {
      return @{
        ok = $false
        error = "Bot answered, but $Action was not confirmed by /api/status. No state assumption was made."
        execution_paused = [bool]$status.execution_paused
        execution_reason = [string]$status.execution_reason
      }
    }
    return @{
      ok = $true
      message = if ($wantPaused) { "Trading paused and confirmed." } else { "Trading resumed and confirmed." }
      execution_paused = [bool]$status.execution_paused
      execution_reason = [string]$status.execution_reason
      bot_response = $result
    }
  } catch {
    return @{ ok = $false; error = "Could not confirm $Action`: $($_.Exception.Message)" }
  }
}

function Invoke-StartAllGlobal {
  if (-not (Test-LegacyWindowsOwnerOptIn)) {
    return $false
  }
  Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
  # Keep the single-threaded bridge listener responsive. The worker owns the
  # visible orchestration window; the HTTP request only queues that work.
  Invoke-HomeCommandBackground "start-all-global"
}

function Invoke-StopAllGlobal {
  # Stop can enumerate/terminate several process trees. Never hold the bridge
  # request channel across that work or later Start/health requests will appear
  # dead until the stop finishes.
  Invoke-HomeCommandBackground "stop-all-global"
}

function Invoke-RestartBridge {
  Start-VisibleConsole (Join-Path $scriptDir "ensure-home-bridge.ps1") @("-Force") -Title "Doxed Home Bridge :7810"
}

function Invoke-HomeCommand([string]$Action, [string]$QueryUrl) {
  switch ($Action) {
    "start-mirror" {
      return (Invoke-FlyDesktopMirror)
    }
    "reset-mirror" {
      return (Invoke-ResetFlyDesktopMirror)
    }
    "start-all-local" {
      if (-not (Test-LegacyWindowsOwnerOptIn)) {
        return (Get-LegacyStartRefusal $Action)
      }
      Invoke-HomeCommandBackground "start-all-local"
      return @{
        ok = $true
        message = "Local lab start queued (:7002 bot + :9500 analyzer in Final Bots folder)."
      }
    }
    "start-all-global" {
      if (-not (Test-LegacyWindowsOwnerOptIn)) {
        return (Get-LegacyStartRefusal $Action)
      }
      Invoke-StartAllGlobal
      return @{
        ok = $true
        message = @(
          "Start everything launched (visible consoles)."
          "Step 0: bridge reload  |  Step 1: bot :$BotPort  |  Step 2: analyzer :$AnalyzerPort  |  Step 3: tunnel"
          "Four windows should stay open. Refresh status in 30-60 seconds."
        ) -join "`n"
      }
    }
    "start-all" {
      if (-not (Test-LegacyWindowsOwnerOptIn)) {
        return (Get-LegacyStartRefusal $Action)
      }
      if ($stackMode.Mode -eq "local-collection") {
        Start-Process -FilePath "powershell.exe" -ArgumentList @(
          "-NoProfile", "-ExecutionPolicy", "Bypass",
          "-File", (Join-Path $scriptDir "start-local-collection.ps1")
        ) -WorkingDirectory $repoRoot -WindowStyle Normal
        Start-HiddenPs1 (Join-Path $scriptDir "home-stack-supervisor-local.ps1") @(
          "-BotPort", "$BotPort",
          "-AnalyzerPort", "$AnalyzerPort"
        )
        return @{
          ok = $true
          message = @(
            "Local collection start queued."
            "Bot :$BotPort + Analyzer :$AnalyzerPort (visible console windows)."
            "Data: $($stackMode.DataDir)"
            "No tunnel - isolated from doxxedcrypto production stack."
            "Supervisor runs hidden for 24/7 recovery (.home-stack-supervisor.log)."
          ) -join "`n"
        }
      }
      Invoke-StartAllGlobal
      return @{
        ok = $true
        message = @(
          "Start everything launched (visible consoles)."
          "Bridge reload + bot :$BotPort + analyzer :$AnalyzerPort + tunnel."
          "Refresh status in 30-60 seconds."
        ) -join "`n"
      }
    }
    "restart-bridge" {
      Invoke-RestartBridge
      # Ensure the bridge auto-respawn watchdog is armed so closing the bridge console
      # no longer leaves the command center without a command path.
      try { & (Join-Path $scriptDir "register-bridge-watchdog.ps1") -Quiet } catch { }
      return @{
        ok = $true
        message = @(
          "Bridge restart window opened (Doxed Home Bridge :7810)."
          "Bridge watchdog armed - bridge auto-respawns if the console is closed."
          "Keep it open - Agent Hub buttons need it."
          "Hard-refresh Agent Hub after bridge shows OK."
        ) -join "`n"
      }
    }
    "start-bot" {
      if (-not (Test-LegacyWindowsOwnerOptIn)) {
        return (Get-LegacyStartRefusal $Action)
      }
      # Starting/repairing a process may inspect listeners and stale owners.
      # Never hold the bridge's single request thread across that work.
      Invoke-HomeCommandBackground "start-bot"
      return @{
        ok = $true
        message = "Bot start queued on :$BotPort - refresh status in 15-30 seconds."
      }
    }
    "start-analyzer" {
      Invoke-HomeCommandBackground "start-analyzer"
      return @{
        ok = $true
        message = "Analyzer start queued on :$AnalyzerPort - refresh status in 15-30 seconds."
      }
    }
    "start-analyzer-once" {
      if ($stackMode.Mode -eq "local-collection") {
        Start-VisibleConsole (Join-Path $scriptDir "start-local-collection-analyzer.ps1") @("-Once") -Title "Local Collection Analyzer (once)"
      } else {
        Start-VisibleConsole (Join-Path $scriptDir "start-home-analyzer.ps1") @("-Port", "$AnalyzerPort", "-Once") -Title "Doxed Analyzer (once)"
      }
      return @{ ok = $true; message = "Analyzer single pass started on :$AnalyzerPort." }
    }
    "start-tunnel" {
      if (-not (Test-LegacyWindowsOwnerOptIn)) {
        return (Get-LegacyStartRefusal $Action)
      }
      if (-not (Test-PortOpen $BotPort)) {
        return @{
          ok = $false
          error = "Bot not running on :$BotPort - click Start bot first, wait for /api/ping, then Start tunnel."
        }
      }
      try {
        if (Use-NamedTunnel) {
          Start-HomeTunnel -Port $BotPort -Force
          $stableUrl = "https://bot.doxxedcrypto.digital"
          return @{
            ok = $true
            message = "Named tunnel starting hidden at $stableUrl - refresh status in 30-60s."
          }
        }
        Start-HomeTunnel -Port $BotPort -Force -PreferVisible
        return @{ ok = $true; message = "Quick tunnel console opened - copy URL from the window when ready." }
      } catch {
        return @{ ok = $false; error = "Tunnel start failed: $($_.Exception.Message)" }
      }
    }
    "enable-named-tunnel" {
      if (-not (Test-LegacyWindowsOwnerOptIn)) {
        return (Get-LegacyStartRefusal $Action)
      }
      Set-Content -Path (Join-Path $repoRoot ".home-use-named-tunnel") -Value "enabled" -NoNewline
      Set-Content -Path $tunnelUrlFile -Value "https://bot.doxxedcrypto.digital" -NoNewline
      return @{
        ok = $true
        message = "Named tunnel mode ON. Run SETUP-NAMED-TUNNEL.cmd once, then Start everything."
      }
    }
    "wire" {
      if (-not (Test-LegacyWindowsOwnerOptIn)) {
        return (Get-LegacyStartRefusal $Action)
      }
      $url = $QueryUrl
      if (-not $url) { $url = Get-TunnelUrl }
      if (-not $url) {
        return @{ ok = $false; error = "No tunnel URL yet. Click Start everything or Start tunnel first." }
      }
      Start-HiddenPs1 (Join-Path $scriptDir "wire-home-bot-background.ps1") @("-Url", $url)
      return @{ ok = $true; message = "Wiring $url to Neon + Railway (see wire window / .home-wire.log)" }
    }
    "resume-trading" {
      return (Invoke-TradingControl "resume")
    }
    "pause-trading" {
      return (Invoke-TradingControl "pause")
    }
    "wipe-research" {
      if (-not (Test-PortOpen $BotPort)) {
        return @{ ok = $false; error = "Bot not running on :$BotPort - start bot first" }
      }
      Invoke-HomeCommandBackground "wipe-research"
      return @{ ok = $true; message = "Fresh collection wipe queued (runs in background, up to 3 min)." }
    }
    "stop-bot" {
      Invoke-HomeCommandBackground "stop-bot"
      return @{ ok = $true; message = "Stop bot queued (background)." }
    }
    "stop-analyzer" {
      Invoke-HomeCommandBackground "stop-analyzer"
      return @{ ok = $true; message = "Stop analyzer queued (background)." }
    }
    "stop-all" {
      Invoke-StopAllGlobal
      return @{
        ok = $true
        message = @(
          "Stop everything window opened."
          "Stopping :$BotPort bot, :$AnalyzerPort analyzer, tunnel."
          "Bridge :7810 stays running. Local lab :7002/:9500 untouched."
        ) -join "`n"
      }
    }
    "stop-all-global" {
      Invoke-StopAllGlobal
      return @{
        ok = $true
        message = "Stop everything window opened (:$BotPort + :$AnalyzerPort + tunnel). Bridge stays running."
      }
    }
    "stop-all-local" {
      Invoke-HomeCommandBackground "stop-all-local"
      return @{
        ok = $true
        message = "Local lab stop queued (:7002 + :9500 only). Global showcase untouched."
      }
    }
    "reset-home-stack" {
      if (-not (Test-LegacyWindowsOwnerOptIn)) {
        return (Get-LegacyStartRefusal $Action)
      }
      Invoke-HomeCommandBackground "reset-home-stack"
      return @{
        ok = $true
        message = @(
          "Clean reset queued - stop everything, wait 8s, start fresh."
          "Step 1: /api/ping on :$BotPort within ~2s (boot probe)."
          "Step 2: full dashboard in 60-90s on home PC."
          "Refresh status at 30s, 60s, and 90s."
        ) -join "`n"
      }
    }
    default {
      return @{ ok = $false; error = "Unknown action: $Action" }
    }
  }
}

function Serve-Request([System.Net.HttpListenerContext]$Context) {
  $request = $Context.Request
  $response = $Context.Response
  Write-Cors $request $response

  if ($request.HttpMethod -eq "OPTIONS") {
    $response.Headers.Add("Access-Control-Allow-Private-Network", "true")
    $response.StatusCode = 204
    $response.Close()
    return
  }

  $path = ($request.Url.AbsolutePath.TrimEnd("/"))
  if (-not $path) { $path = "/" }
  $tunnelParam = $request.QueryString["url"]

  try {
    $payload = switch -Regex ($path) {
      "^/status$" { Get-FullStatus }
      "^/health$" { @{ ok = $true; launcher = "running"; architecture_owner = "fly.io"; legacy_starts_quarantined = $true } }
      "^/start$" { Invoke-HomeCommand "start-mirror" $tunnelParam }
      "^/cmd/start-mirror$" { Invoke-HomeCommand "start-mirror" $tunnelParam }
      "^/cmd/reset-mirror$" { Invoke-HomeCommand "reset-mirror" $tunnelParam }
      "^/cmd/start-all$" { Invoke-HomeCommand "start-all" $tunnelParam }
      "^/cmd/restart-bridge$" { Invoke-HomeCommand "restart-bridge" $tunnelParam }
      "^/cmd/start-all-global$" { Invoke-HomeCommand "start-all-global" $tunnelParam }
      "^/cmd/start-all-local$" { Invoke-HomeCommand "start-all-local" $tunnelParam }
      "^/cmd/start-bot$" { Invoke-HomeCommand "start-bot" $tunnelParam }
      "^/cmd/start-analyzer$" { Invoke-HomeCommand "start-analyzer" $tunnelParam }
      "^/cmd/start-analyzer-once$" { Invoke-HomeCommand "start-analyzer-once" $tunnelParam }
      "^/cmd/start-tunnel$" { Invoke-HomeCommand "start-tunnel" $tunnelParam }
      "^/cmd/enable-named-tunnel$" { Invoke-HomeCommand "enable-named-tunnel" $tunnelParam }
      "^/cmd/wire$" { Invoke-HomeCommand "wire" $tunnelParam }
      "^/cmd/wipe-research$" { Invoke-HomeCommand "wipe-research" $tunnelParam }
      "^/cmd/resume-trading$" { Invoke-HomeCommand "resume-trading" $tunnelParam }
      "^/cmd/pause-trading$" { Invoke-HomeCommand "pause-trading" $tunnelParam }
      "^/cmd/stop-bot$" { Invoke-HomeCommand "stop-bot" $tunnelParam }
      "^/cmd/stop-analyzer$" { Invoke-HomeCommand "stop-analyzer" $tunnelParam }
      "^/cmd/stop-all$" { Invoke-HomeCommand "stop-all-global" $tunnelParam }
      "^/cmd/stop-all-global$" { Invoke-HomeCommand "stop-all-global" $tunnelParam }
      "^/cmd/stop-all-local$" { Invoke-HomeCommand "stop-all-local" $tunnelParam }
      "^/cmd/reset-home-stack$" { Invoke-HomeCommand "reset-home-stack" $tunnelParam }
      default {
        @{ ok = $false; error = "Unknown path: $path - run RESTART-LAUNCHER.cmd" }
      }
    }
    Send-Json -Response $response -Payload $payload
  } catch {
    Send-Json -Response $response -Payload @{ ok = $false; error = $_.Exception.Message } -Status 500
  }
}

# Scheduled-task/watchdog recovery runs under a hidden noninteractive host.
# That host legitimately has no RawUI; attempting to set WindowTitle there
# aborts the bridge before HttpListener binds. The title is diagnostic only.
try {
  if ($Host -and $Host.UI -and $Host.UI.RawUI) {
    $Host.UI.RawUI.WindowTitle = "Doxed Home Bridge :$Port"
  }
} catch {
  # Keep the command bridge available even when the host has no console UI.
}
Write-Host ""
Write-Host "=== Doxed home command bridge (:$Port) ===" -ForegroundColor Cyan
Write-Host "Repo: $repoRoot"
Write-Host "Stack: $($stackMode.Label)"
Write-Host "Agent Hub buttons need this window open on the same PC."
Write-Host ""

$bridgeErrLog = Join-Path $repoRoot ".home-bridge.err.log"
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($prefix)
try {
  $listener.Start()
} catch {
  $msg = $_.Exception.Message
  Set-Content -Path $bridgeErrLog -Value $msg -Encoding UTF8
  Write-Host "Bridge failed to bind :$Port - $msg" -ForegroundColor Red
  Write-Host "Run RESTART-LAUNCHER.cmd as admin if http.sys URL reservation conflict."
  Read-Host "Press Enter to close"
  exit 1
}

$bridgePidFile = Join-Path $repoRoot ".home-bridge.pid"
Set-Content -LiteralPath $bridgePidFile -Value "$PID" -NoNewline -Encoding UTF8

try {
  # Synchronous request handling in the main runspace. The earlier runspace-pool +
  # Task.Run approach had two PowerShell-specific bugs: Task.Run(ScriptBlock) is an
  # ambiguous overload (kills the listener on the first request), and functions
  # defined in the main runspace (Serve-Request, Get-FullStatus, etc.) are NOT
  # visible inside pool runspaces, so no response was ever sent. Get-FullStatus is
  # port-only (<500ms), so serialized handling is fine for an admin command bridge.
  while ($listener.IsListening) {
    $context = $listener.GetContext()
    try {
      Serve-Request $context
    } catch {
      # A single bad request must never take the listener down.
      try {
        $context.Response.StatusCode = 500
        $context.Response.Close()
      } catch { }
    }
  }
} finally {
  $listener.Stop()
  try {
    if (Test-Path -LiteralPath $bridgePidFile) {
      $recordedPid = [int](Get-Content -LiteralPath $bridgePidFile -ErrorAction SilentlyContinue)
      if ($recordedPid -eq $PID) {
        Remove-Item -LiteralPath $bridgePidFile -Force -ErrorAction SilentlyContinue
      }
    }
  } catch { }
}

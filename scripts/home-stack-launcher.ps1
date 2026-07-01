# Local command bridge — Agent Hub admin panel → home PC (bot, analyzer, tunnel, wire).
# Listen: http://127.0.0.1:7810
# Run once: RESTART-LAUNCHER.cmd

param(
  [int]$Port = 7810,
  [int]$BotPort = 0,
  [int]$AnalyzerPort = 0
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $scriptDir "home-stack-mode.ps1")
$stackMode = Get-HomeStackMode
if ($BotPort -le 0) { $BotPort = $stackMode.BotPort }
if ($AnalyzerPort -le 0) { $AnalyzerPort = $stackMode.AnalyzerPort }
. (Join-Path $scriptDir "home-stack-common.ps1") -BridgePort $Port -BotPort $BotPort -AnalyzerPort $AnalyzerPort
. (Join-Path $scriptDir "home-stack-health.ps1")
$prefix = "http://127.0.0.1:$Port/"

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
  # — halves the window during which the single-threaded listener could delay a /health.
  $live = Test-HttpAliveParallel @($botUrl, $anUrl) 1500
  $botOnline = $live[$botUrl]
  $analyzerRunning = $live[$anUrl]
  $tunnelUrl = Get-TunnelUrl
  if (-not $tunnelUrl) { $tunnelUrl = "https://bot.doxxedcrypto.digital" }
  $cloudflaredRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  # Tunnel is "live" when cloudflared is running AND the public URL answers /api/ping.
  # Probe at most every 120s so /status stays fast (the bridge listener must not block).
  $tunnelLive = $false
  if ($tunnelUrl -and $cloudflaredRunning) {
    if ($script:TunnelLiveCache.url -eq $tunnelUrl -and ($now - $script:TunnelLiveCache.at).TotalSeconds -lt 120) {
      $tunnelLive = $script:TunnelLiveCache.live
    } else {
      $tunnelLive = (Test-TunnelLive $tunnelUrl)
      $script:TunnelLiveCache = @{ url = $tunnelUrl; live = $tunnelLive; at = $now }
    }
  } elseif (-not $cloudflaredRunning) {
    # cloudflared gone -> invalidate cache so a restart re-probes immediately.
    $script:TunnelLiveCache = @{ url = ""; live = $false; at = [datetime]::MinValue }
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

function Invoke-HomeCommandBackground([string]$Action) {
  $modeArg = if ($stackMode.Mode -eq "local-collection") { "local-collection" } else { "production" }
  Start-HiddenPs1 (Join-Path $scriptDir "home-stack-cmd-worker.ps1") @(
    "-Action", $Action,
    "-BotPort", "$BotPort",
    "-AnalyzerPort", "$AnalyzerPort",
    "-StackMode", $modeArg
  )
}

function Invoke-StartAllGlobal {
  Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
  Start-VisibleConsole (Join-Path $scriptDir "home-stack-start-everything.ps1") @(
    "-BotPort", "$BotPort",
    "-AnalyzerPort", "$AnalyzerPort"
  ) -Title "Doxed Start Everything"
}

function Invoke-StopAllGlobal {
  Start-VisibleConsole (Join-Path $scriptDir "home-stack-stop-everything.ps1") @(
    "-BotPort", "$BotPort",
    "-AnalyzerPort", "$AnalyzerPort"
  ) -Title "Doxed Stop Everything"
}

function Invoke-RestartBridge {
  Start-VisibleConsole (Join-Path $scriptDir "ensure-home-bridge.ps1") @("-Force") -Title "Doxed Home Bridge :7810"
}

function Invoke-HomeCommand([string]$Action, [string]$QueryUrl) {
  switch ($Action) {
    "start-all-local" {
      Invoke-HomeCommandBackground "start-all-local"
      return @{
        ok = $true
        message = "Local lab start queued (:7800 bot + :9500 analyzer in Final Bots folder)."
      }
    }
    "start-all-global" {
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
      Start-VisibleConsole (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort") -Title "Doxed Bot :$BotPort"
      return @{
        ok = $true
        message = "Bot console opened on :$BotPort - keep the window open."
      }
    }
    "start-analyzer" {
      Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
      Start-VisibleConsole (Join-Path $scriptDir "start-home-analyzer.ps1") @("-Port", "$AnalyzerPort", "-NoWait") -Title "Doxed Analyzer :$AnalyzerPort"
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
      if (-not (Test-PortOpen $BotPort)) {
        return @{
          ok = $false
          error = "Bot not running on :$BotPort - click Start bot first, wait for /api/ping, then Start tunnel."
        }
      }
      if (-not ((Use-NamedTunnel) -and (Test-Path (Join-Path $repoRoot ".home-use-named-tunnel")))) {
        Start-VisibleConsole (Join-Path $scriptDir "restart-home-tunnel.ps1") @("-Port", "$BotPort", "-Force") -Title "Doxed Cloudflare Tunnel"
        return @{ ok = $true; message = "Quick tunnel window opened - watch Doxed Cloudflare Tunnel for the URL" }
      }
      try {
        Start-CloudflaredNamedHidden -Port $BotPort
      } catch {
        return @{ ok = $false; error = "Tunnel start failed: $($_.Exception.Message)" }
      }
      $stableUrl = "https://bot.doxxedcrypto.digital"
      return @{
        ok = $true
        message = "Named tunnel starting at $stableUrl - refresh status in 30-60s (bridge stays responsive)."
      }
    }
    "enable-named-tunnel" {
      Set-Content -Path (Join-Path $repoRoot ".home-use-named-tunnel") -Value "enabled" -NoNewline
      Set-Content -Path $tunnelUrlFile -Value "https://bot.doxxedcrypto.digital" -NoNewline
      return @{
        ok = $true
        message = "Named tunnel mode ON. Run SETUP-NAMED-TUNNEL.cmd once, then Start everything."
      }
    }
    "wire" {
      $url = $QueryUrl
      if (-not $url) { $url = Get-TunnelUrl }
      if (-not $url) {
        return @{ ok = $false; error = "No tunnel URL yet. Click Start everything or Start tunnel first." }
      }
      Start-HiddenPs1 (Join-Path $scriptDir "wire-home-bot-background.ps1") @("-Url", $url)
      return @{ ok = $true; message = "Wiring $url to Neon + Railway (see wire window / .home-wire.log)" }
    }
    "resume-trading" {
      if (-not (Test-PortOpen $BotPort)) {
        return @{ ok = $false; error = "Bot not running on :$BotPort - click Start bot first" }
      }
      Invoke-HomeCommandBackground "resume-trading"
      return @{ ok = $true; message = "Resume trading queued." }
    }
    "pause-trading" {
      if (-not (Test-PortOpen $BotPort)) {
        return @{ ok = $false; error = "Bot not running on :$BotPort - click Start bot first" }
      }
      Invoke-HomeCommandBackground "pause-trading"
      return @{ ok = $true; message = "Pause trading queued." }
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
          "Bridge :7810 stays running. Local lab :7800/:9500 untouched."
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
        message = "Local lab stop queued (:7800 + :9001 only). Global showcase untouched."
      }
    }
    "reset-home-stack" {
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
      "^/health$" { @{ ok = $true; launcher = "running" } }
      "^/start$" { Invoke-HomeCommand "start-all" $tunnelParam }
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

$Host.UI.RawUI.WindowTitle = "Doxed Home Bridge :$Port"
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
}

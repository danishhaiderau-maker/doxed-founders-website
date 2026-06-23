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
. (Join-Path $scriptDir "home-stack-common.ps1") -Port $Port -BotPort $BotPort -AnalyzerPort $AnalyzerPort
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
  # Port-only checks — WMI + outbound HTTP probes block the single listener thread and freeze buttons.
  $botPortOpen = Test-PortOpen $BotPort
  $analyzerRunning = Test-PortOpen $AnalyzerPort
  $localBotOpen = Test-PortOpen 7800
  $localAnalyzerOpen = Test-PortOpen 9001
  $tunnelUrl = Get-TunnelUrl
  $cloudflaredRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  $tunnelLive = $false
  if ($tunnelUrl) {
    $now = Get-Date
    if ($script:TunnelLiveCache.url -eq $tunnelUrl -and ($now - $script:TunnelLiveCache.at).TotalSeconds -lt 45) {
      $tunnelLive = $script:TunnelLiveCache.live
    } elseif ($cloudflaredRunning -and $botPortOpen) {
      $tunnelLive = $true
    }
  }
  $labRoot = Split-Path -Parent $repoRoot
  return @{
    ok = $true
    launcher = "running"
    mode = $stackMode.Mode
    stackLabel = $stackMode.Label
    ports = @{
      bot = $BotPort
      analyzer = $AnalyzerPort
      launcher = $Port
      localBot = 7800
      localAnalyzer = 9001
    }
    bot = @{
      online = $botPortOpen
      dashboard = "http://127.0.0.1:$BotPort"
      lan = "http://10.0.0.102:$BotPort"
      dataDir = if ($stackMode.Mode -eq "local-collection") { $stackMode.DataDir } else { $agentDir }
    }
    localLab = @{
      online = ($localBotOpen -and $localAnalyzerOpen)
      botOnline = $localBotOpen
      analyzerOnline = $localAnalyzerOpen
      botDashboard = "http://127.0.0.1:7800"
      analyzerDashboard = "http://127.0.0.1:9001/"
      labRoot = $labRoot
      note = "Source-of-truth research lab (Final Bots) - independent from global :$BotPort showcase."
    }
    analyzer = @{
      online = $analyzerRunning
      dashboard = "http://127.0.0.1:$AnalyzerPort/"
      lan = "http://10.0.0.102:$AnalyzerPort/"
      note = if ($stackMode.Mode -eq "local-collection") {
        "Isolated collection on :$AnalyzerPort - no tunnel or relay."
      } else {
        "Global showcase :$BotPort + analyzer :$AnalyzerPort. Site mirror + relay need tunnel (bot.doxxedcrypto.digital)."
      }
    }
    tunnel = @{
      url = $tunnelUrl
      live = $tunnelLive
      cloudflaredRunning = $cloudflaredRunning
      enabled = $stackMode.TunnelEnabled
    }
  }
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

function Invoke-HomeCommand([string]$Action, [string]$QueryUrl) {
  switch ($Action) {
    "start-all-local" {
      Invoke-HomeCommandBackground "start-all-local"
      return @{
        ok = $true
        message = "Local lab start queued (:7800 bot + :9001 analyzer in Final Bots folder)."
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
      Start-DetachedPs1 (Join-Path $scriptDir "home-stack-start-all.ps1") @("-BotPort", "$BotPort", "-AnalyzerPort", "$AnalyzerPort") -NoExit -WindowTitle "Doxed Start Everything" -Show Normal
      return @{
        ok = $true
        message = @(
          "Production start queued."
          "Bot :$BotPort + Analyzer :$AnalyzerPort + Cloudflare tunnel."
          "Site mirror + Bitfinex relay need tunnel live (bot.doxxedcrypto.digital)."
          "Leave console windows open. Supervisor runs hidden for 24/7 recovery."
        ) -join "`n"
      }
    }
    "start-bot" {
      Invoke-HomeCommandBackground "start-bot"
      return @{
        ok = $true
        message = "Bot start queued - window opens on :$BotPort in a few seconds."
      }
    }
    "start-analyzer" {
      Invoke-HomeCommandBackground "start-analyzer"
      return @{
        ok = $true
        message = "Analyzer start queued - console + :$AnalyzerPort dashboard when ready."
      }
    }
    "start-analyzer-once" {
      if ($stackMode.Mode -eq "local-collection") {
        Start-DetachedPs1 (Join-Path $scriptDir "start-local-collection-analyzer.ps1") @("-Once") -NoExit -WindowTitle "Local Collection Analyzer (once)" -Show Normal
      } else {
        Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @("-Port", "$AnalyzerPort", "-Once") -NoExit -WindowTitle "Doxed Analyzer (once)" -Show Normal
      }
      return @{ ok = $true; message = "Analyzer single pass started on :$AnalyzerPort." }
    }
    "start-tunnel" {
      Start-DetachedPs1 (Join-Path $scriptDir "restart-home-tunnel.ps1") @("-Port", "$BotPort", "-Force") -NoExit -WindowTitle "Doxed Cloudflare Tunnel" -Show Normal
      if (Use-NamedTunnel) {
        return @{ ok = $true; message = "Named tunnel window opened - keep it open. URL: https://bot.doxxedcrypto.digital" }
      }
      return @{ ok = $true; message = "Quick tunnel window opened - watch Doxed Cloudflare Tunnel for the URL" }
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
      Invoke-HomeCommandBackground "stop-all-global"
      return @{
        ok = $true
        message = @(
          "Global showcase stop queued (:$BotPort bot, :$AnalyzerPort analyzer, tunnel)."
          "Local lab :7800/:9001 is NOT stopped - use Stop all local for that."
        ) -join "`n"
      }
    }
    "stop-all-global" {
      Invoke-HomeCommandBackground "stop-all-global"
      return @{
        ok = $true
        message = "Global showcase stop queued (:$BotPort + :$AnalyzerPort + tunnel). Local lab untouched."
      }
    }
    "stop-all-local" {
      Invoke-HomeCommandBackground "stop-all-local"
      return @{
        ok = $true
        message = "Local lab stop queued (:7800 + :9001 only). Global showcase untouched."
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
      default {
        @{ ok = $false; error = "Unknown path: $path - run RESTART-LAUNCHER.cmd" }
      }
    }
    Send-Json -Response $response -Payload $payload
  } catch {
    Send-Json -Response $response -Payload @{ ok = $false; error = $_.Exception.Message } -Status 500
  }
}

Write-Host ""
Write-Host "=== Doxed home command bridge (:$Port) ===" -ForegroundColor Cyan
Write-Host "Repo: $repoRoot"
Write-Host "Agent Hub buttons need this window open on the same PC."
Write-Host ""

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($prefix)
$listener.Start()

try {
  while ($listener.IsListening) {
    Serve-Request $listener.GetContext()
  }
} finally {
  $listener.Stop()
}

# Local command bridge — Agent Hub admin panel → home PC (bot, analyzer, tunnel, wire).
# Listen: http://127.0.0.1:7810
# Run once: START-LAUNCHER.cmd

param(
  [int]$Port = 7810,
  [int]$BotPort = 7800,
  [int]$AnalyzerPort = 9001
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
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
  # Chrome Private Network Access (HTTPS site → http://127.0.0.1)
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

function Test-PortOpen([int]$P) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $async = $c.ConnectAsync("127.0.0.1", $P)
    if (-not $async.Wait(1500)) { return $false }
    $c.Close()
    return $true
  } catch {
    return $false
  }
}

function Test-TunnelLive([string]$Url) {
  if (-not $Url) { return $false }
  try {
    $r = Invoke-WebRequest -Uri "$Url/health" -UseBasicParsing -TimeoutSec 8
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

function Stop-ListenPort([int]$ListenPort) {
  $killed = @()
  Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object {
      $procId = [int]$_.OwningProcess
      if ($procId -gt 0 -and $killed -notcontains $procId) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        $killed += $procId
      }
    }
  return $killed
}

function Stop-PythonMatching([string]$Pattern) {
  $killed = @()
  Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Pattern*" } |
    ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $killed += $_.ProcessId
    }
  return $killed
}

function Stop-Cloudflared {
  $killed = @()
  Get-Process cloudflared -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    $killed += $_.Id
  }
  return $killed
}

function Close-HomeStackWindows {
  $closed = @()
  $windowTitles = @(
    "Doxed Bot :7800",
    "Doxed Analyzer",
    "Doxed Analyzer (once)",
    "Doxed Cloudflare Tunnel",
    "Doxed Stack Control Panel",
    "Doxed Auto-Wire",
    "Doxed Wire to Site"
  )
  foreach ($title in $windowTitles) {
    & taskkill.exe /F /FI "WINDOWTITLE eq $title" 2>$null | Out-Null
    $closed += $title
  }
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object {
      $_.Name -in @("powershell.exe", "cmd.exe") -and $_.CommandLine -and (
        $_.CommandLine -like "*start-home-bot.ps1*" -or
        $_.CommandLine -like "*start-home-analyzer.ps1*" -or
        $_.CommandLine -like "*setup-home-bot-tunnel.ps1*" -or
        $_.CommandLine -like "*home-stack-control-panel.ps1*" -or
        $_.CommandLine -like "*auto-wire-after-tunnel.ps1*" -or
        $_.CommandLine -like "*wire-home-bot-background.ps1*"
      )
    } | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $closed += "pid:$($_.ProcessId)"
    }
  return $closed
}

function Clear-TunnelUrlFile {
  if (Test-Path $tunnelUrlFile) {
    Set-Content -Path $tunnelUrlFile -Value "" -NoNewline
  }
}

function Stop-AllHomeStack {
  $botPort = @(Stop-ListenPort $BotPort)
  $botPy = @(Stop-PythonMatching "btc_conservative_agent")
  $analyzerPy = @(Stop-PythonMatching "analyzer_research_engine")
  $tunnel = @(Stop-Cloudflared)
  $windows = @(Close-HomeStackWindows)
  Clear-TunnelUrlFile
  return @{
    botPort = $botPort
    botPy = $botPy
    analyzerPy = $analyzerPy
    tunnel = $tunnel
    windows = $windows
  }
}

function Test-AnalyzerRunning {
  $hit = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*analyzer_research_engine*" } |
    Select-Object -First 1
  return [bool]$hit
}

function Test-BotRunning {
  if (Test-PortOpen $BotPort) { return $true }
  $hit = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*btc_conservative_agent*" } |
    Select-Object -First 1
  return [bool]$hit
}

function Start-DetachedPs1 {
  param(
    [string]$ScriptPath,
    [string[]]$ExtraArgs = @(),
    [switch]$NoExit,
    [string]$WindowTitle = "Doxed Home Stack"
  )
  if (-not (Test-Path $ScriptPath)) { throw "Missing script: $ScriptPath" }
  $psLine = "-NoExit -NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`""
  foreach ($a in $ExtraArgs) { $psLine += " `"$a`"" }
  # cmd /c start opens a dedicated visible console (works from HttpListener / Agent Hub clicks).
  $cmdArgs = @(
    "/c",
    "start",
    "`"$WindowTitle`"",
    "powershell.exe",
    $psLine
  )
  Start-Process -FilePath "cmd.exe" `
    -ArgumentList $cmdArgs `
    -WorkingDirectory $repoRoot `
    -WindowStyle Normal
}

function Get-TunnelUrl {
  if (Test-Path $tunnelUrlFile) {
    return (Get-Content $tunnelUrlFile -Raw -ErrorAction SilentlyContinue).Trim()
  }
  return $null
}

function Get-FullStatus {
  $botPortOpen = Test-BotRunning
  $analyzerRunning = Test-AnalyzerRunning
  $tunnelUrl = Get-TunnelUrl
  $cloudflaredRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  $tunnelLive = if ($tunnelUrl) { Test-TunnelLive $tunnelUrl } else { $false }
  return @{
    ok = $true
    launcher = "running"
    ports = @{
      bot = $BotPort
      analyzer = $AnalyzerPort
      launcher = $Port
    }
    bot = @{
      online = $botPortOpen
      dashboard = "http://127.0.0.1:$BotPort"
      lan = "http://10.0.0.102:$BotPort"
      dataDir = $agentDir
    }
    analyzer = @{
      online = $analyzerRunning
      dashboard = "console window (research loop)"
      lan = $agentDir
      note = "Runs from $agentDir - console shows iteration logs every 30 min (not an HTTP server on :9001)"
    }
    tunnel = @{
      url = $tunnelUrl
      live = $tunnelLive
      cloudflaredRunning = $cloudflaredRunning
    }
  }
}

function Invoke-HomeCommand([string]$Action, [string]$QueryUrl) {
  switch ($Action) {
    "start-all" {
      $messages = [System.Collections.Generic.List[string]]::new()

      if (-not (Test-BotRunning)) {
        Start-DetachedPs1 (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort") -NoExit -WindowTitle "Doxed Bot :7800"
        $messages.Add("[1/4] Bot window opened on :$BotPort")
        Start-Sleep -Seconds 4
      } else {
        $messages.Add("[1/4] Bot already online on :$BotPort")
      }

      if (-not (Test-AnalyzerRunning)) {
        Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @() -NoExit -WindowTitle "Doxed Analyzer"
        $messages.Add("[2/4] Analyzer console opened (30-min research loop)")
      } else {
        $messages.Add("[2/4] Analyzer already running")
      }

      $tunnelUrl = Get-TunnelUrl
      $tunnelOk = (Test-TunnelLive $tunnelUrl)
      if (-not $tunnelOk) {
        if (@(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0) {
          Stop-Cloudflared | Out-Null
          Start-Sleep -Seconds 2
        }
        Clear-TunnelUrlFile
        Start-DetachedPs1 (Join-Path $scriptDir "setup-home-bot-tunnel.ps1") @("-Quick", "-Port", "$BotPort") -NoExit -WindowTitle "Doxed Cloudflare Tunnel"
        $messages.Add("[3/4] Tunnel window opened (watch for trycloudflare URL)")
      } else {
        $messages.Add("[3/4] Tunnel already live: $tunnelUrl")
      }

      Start-DetachedPs1 (Join-Path $scriptDir "home-stack-control-panel.ps1") @("-BotPort", "$BotPort", "-AnalyzerPort", "$AnalyzerPort") -NoExit -WindowTitle "Doxed Stack Control Panel"
      $messages.Add("[4/4] Control panel opened (live status every 5s)")

      $autoWire = Join-Path $scriptDir "auto-wire-after-tunnel.ps1"
      Start-DetachedPs1 $autoWire @() -WindowTitle "Doxed Auto-Wire"
      $messages.Add("Auto-wire window opened - wires tunnel URL to Neon + Railway when ready")

      return @{ ok = $true; message = ($messages -join "`n") }
    }
    "start-bot" {
      if (Test-BotRunning) {
        return @{ ok = $true; message = "Bot already listening on :$BotPort" }
      }
      Start-DetachedPs1 (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort") -NoExit -WindowTitle "Doxed Bot :7800"
      return @{ ok = $true; message = "Bot window opened on :$BotPort" }
    }
    "start-analyzer" {
      if (Test-AnalyzerRunning) {
        return @{ ok = $true; message = "Analyzer already running (python research loop)." }
      }
      Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @() -NoExit -WindowTitle "Doxed Analyzer"
      return @{
        ok = $true
        message = "Analyzer console opened from $agentDir (reads bot CSVs there)."
      }
    }
    "start-analyzer-once" {
      Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @("-Once") -NoExit -WindowTitle "Doxed Analyzer (once)"
      return @{ ok = $true; message = "Analyzer single pass started." }
    }
    "start-tunnel" {
      Stop-Cloudflared | Out-Null
      Clear-TunnelUrlFile
      Start-Sleep -Seconds 1
      Start-DetachedPs1 (Join-Path $scriptDir "setup-home-bot-tunnel.ps1") @("-Quick", "-Port", "$BotPort") -NoExit -WindowTitle "Doxed Cloudflare Tunnel"
      return @{ ok = $true; message = "Fresh tunnel window opened - new URL saves to .home-tunnel-url automatically" }
    }
    "wire" {
      $url = $QueryUrl
      if (-not $url) { $url = Get-TunnelUrl }
      if (-not $url) {
        return @{
          ok = $false
          error = "No tunnel URL yet. Click Start everything or Start tunnel first."
        }
      }
      Start-DetachedPs1 (Join-Path $scriptDir "wire-home-bot-background.ps1") @("-Url", $url) -WindowTitle "Doxed Wire to Site"
      return @{ ok = $true; message = "Wiring $url to Neon + Railway (see wire window / .home-wire.log)" }
    }
    "wipe-research" {
      if (-not (Test-PortOpen $BotPort)) {
        return @{ ok = $false; error = "Bot not running on :$BotPort - start bot first" }
      }
      try {
        $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$BotPort/api/reset" -Method POST -UseBasicParsing -TimeoutSec 180
        return @{ ok = $true; message = "Fresh collection wipe + `$500 reset sent to bot.`n$($resp.Content)" }
      } catch {
        return @{ ok = $false; error = "Wipe failed: $($_.Exception.Message)" }
      }
    }
    "stop-bot" {
      $portKilled = @(Stop-ListenPort $BotPort)
      $pyKilled = @(Stop-PythonMatching "btc_conservative_agent")
      Close-HomeStackWindows | Out-Null
      return @{
        ok = $true
        message = "Stopped bot (port:$($portKilled.Count) python:$($pyKilled.Count)). Closed bot console windows."
      }
    }
    "stop-analyzer" {
      $pyKilled = @(Stop-PythonMatching "analyzer_research_engine")
      Close-HomeStackWindows | Out-Null
      return @{
        ok = $true
        message = "Stopped analyzer (python:$($pyKilled.Count)). Closed analyzer console windows."
      }
    }
    "stop-all" {
      $result = Stop-AllHomeStack
      return @{
        ok = $true
        message = @(
          "Full local wipe complete (bridge :7810 still running)."
          "Bot port: $($result.botPort.Count) | bot python: $($result.botPy.Count)"
          "Analyzer python: $($result.analyzerPy.Count) | tunnel: $($result.tunnel.Count)"
          "Closed stack windows. Cleared stale .home-tunnel-url."
          "Click Start everything for a clean run, then Wire to site when URL appears."
        ) -join "`n"
        pids = $result
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
      "^/cmd/start-bot$" { Invoke-HomeCommand "start-bot" $tunnelParam }
      "^/cmd/start-analyzer$" { Invoke-HomeCommand "start-analyzer" $tunnelParam }
      "^/cmd/start-analyzer-once$" { Invoke-HomeCommand "start-analyzer-once" $tunnelParam }
      "^/cmd/start-tunnel$" { Invoke-HomeCommand "start-tunnel" $tunnelParam }
      "^/cmd/wire$" { Invoke-HomeCommand "wire" $tunnelParam }
      "^/cmd/wipe-research$" { Invoke-HomeCommand "wipe-research" $tunnelParam }
      "^/cmd/stop-bot$" { Invoke-HomeCommand "stop-bot" $tunnelParam }
      "^/cmd/stop-analyzer$" { Invoke-HomeCommand "stop-analyzer" $tunnelParam }
      "^/cmd/stop-all$" { Invoke-HomeCommand "stop-all" $tunnelParam }
      default {
        @{
          ok = $false
          error = "Unknown path: $path - run RESTART-LAUNCHER.cmd"
        }
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
Write-Host "Agent Hub on THIS PC can start bot / analyzer / tunnel."
Write-Host "Keep this window open. Ctrl+C stops bridge only."
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

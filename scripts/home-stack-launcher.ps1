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
  $allowed = @("https://doxxedcrypto.digital", "http://localhost:3000", "http://127.0.0.1:3000")
  $origin = $Request.Headers["Origin"]
  if ($origin -and ($allowed -contains $origin)) {
    $Response.Headers.Add("Access-Control-Allow-Origin", $origin)
  } else {
    $Response.Headers.Add("Access-Control-Allow-Origin", "https://doxxedcrypto.digital")
  }
  $Response.Headers.Add("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
  $Response.Headers.Add("Access-Control-Allow-Headers", "Content-Type")
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

function Get-HttpJson([string]$Url, [int]$TimeoutSec = 6) {
  try {
    $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSec
    return @{ ok = $true; status = $r.StatusCode; body = $r.Content }
  } catch {
    return @{ ok = $false; error = $_.Exception.Message }
  }
}

function Test-ProcessPattern([string]$Pattern) {
  return @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'cloudflared.exe' OR Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Pattern*" })
}

function Start-DetachedPs1([string]$ScriptPath, [string[]]$ExtraArgs = @(), [switch]$NoExit) {
  if (-not (Test-Path $ScriptPath)) { throw "Missing $ScriptPath" }
  $args = @()
  if ($NoExit) { $args += "-NoExit" }
  $args += @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath)
  $args += $ExtraArgs
  Start-Process powershell -ArgumentList $args
}

function Get-FullStatus {
  $botHealth = Get-HttpJson "http://127.0.0.1:$BotPort/health"
  $analyzerHealth = Get-HttpJson "http://127.0.0.1:$AnalyzerPort/" 4
  $tunnelUrl = $null
  if (Test-Path $tunnelUrlFile) {
    $tunnelUrl = (Get-Content $tunnelUrlFile -Raw -ErrorAction SilentlyContinue).Trim()
  }
  $tunnelLive = $false
  if ($tunnelUrl) {
    $th = Get-HttpJson "$tunnelUrl/health" 12
    $tunnelLive = [bool]$th.ok
  }
  return @{
    ok = $true
    launcher = "running"
    ports = @{
      bot = $BotPort
      analyzer = $AnalyzerPort
      launcher = $Port
    }
    bot = @{
      online = [bool]$botHealth.ok
      health = $botHealth
      dashboard = "http://127.0.0.1:$BotPort"
      lan = "http://10.0.0.102:$BotPort"
      dataDir = $agentDir
    }
    analyzer = @{
      online = [bool]$analyzerHealth.ok
      dashboard = "http://127.0.0.1:$AnalyzerPort"
      lan = "http://10.0.0.102:$AnalyzerPort"
      note = "Must run from $agentDir — not Final Bots root"
    }
    tunnel = @{
      url = $tunnelUrl
      live = $tunnelLive
      cloudflaredRunning = (Test-ProcessPattern "cloudflared tunnel").Count -gt 0
    }
    processes = @{
      botPython = (Test-ProcessPattern "btc_conservative_agent").Count
      analyzerPython = (Test-ProcessPattern "analyzer_research_engine").Count
    }
  }
}

function Invoke-HomeCommand([string]$Action, [string]$QueryUrl) {
  switch ($Action) {
    "start-all" {
      Start-DetachedPs1 (Join-Path $scriptDir "start-home-stack.ps1") @() 
      return @{ ok = $true; message = "Started bot + analyzer + tunnel (3 windows)." }
    }
    "start-bot" {
      if (Test-PortOpen $BotPort) {
        return @{ ok = $true; message = "Bot already listening on :$BotPort" }
      }
      Start-DetachedPs1 (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", $BotPort) -NoExit
      return @{ ok = $true; message = "Bot window opened on :$BotPort" }
    }
    "start-analyzer" {
      Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @() -NoExit
      return @{
        ok = $true
        message = "Analyzer started from $agentDir (reads bot CSVs there)."
      }
    }
    "start-analyzer-once" {
      Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @("-Once") -NoExit
      return @{ ok = $true; message = "Analyzer single pass started." }
    }
    "start-tunnel" {
      Start-DetachedPs1 (Join-Path $scriptDir "setup-home-bot-tunnel.ps1") @("-Quick", "-Port", $BotPort) -NoExit
      return @{ ok = $true; message = "Tunnel window opened — copy trycloudflare URL then Wire." }
    }
    "wire" {
      $url = $QueryUrl
      if (-not $url -and (Test-Path $tunnelUrlFile)) {
        $url = (Get-Content $tunnelUrlFile -Raw).Trim()
      }
      if (-not $url) {
        return @{
          ok = $false
          error = "No tunnel URL. Paste URL from tunnel window or save to .home-tunnel-url"
        }
      }
      Push-Location $repoRoot
      $out = & npm.cmd run wire:home-bot -- $url --skip-health-check --keep-railway-bot 2>&1 | Out-String
      Pop-Location
      return @{ ok = $true; message = "Wired Neon + Railway to $url"; log = $out }
    }
    "stop-bot" {
      $killed = @()
      Get-NetTCPConnection -LocalPort $BotPort -ErrorAction SilentlyContinue |
        ForEach-Object {
          $pid = $_.OwningProcess
          if ($pid -gt 0) {
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            $killed += $pid
          }
        }
      return @{
        ok = $true
        message = if ($killed.Count) { "Stopped process(es) on :$BotPort" } else { "Nothing listening on :$BotPort" }
      }
    }
    default {
      return @{ ok = $false; error = "Unknown action: $Action" }
    }
  }
}

Write-Host ""
Write-Host "=== Doxed home command bridge (:$Port) ===" -ForegroundColor Cyan
Write-Host "Agent Hub admin panel on THIS PC can start bot / analyzer / tunnel."
Write-Host "Keep this window open. Press Ctrl+C to stop bridge (services keep running)."
Write-Host ""

$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add($prefix)
$listener.Start()

try {
  while ($listener.IsListening) {
    $context = $listener.GetContext()
    $request = $context.Request
    $response = $context.Response
    Write-Cors $request $response

    if ($request.HttpMethod -eq "OPTIONS") {
      $response.StatusCode = 204
      $response.Close()
      continue
    }

    $path = ($request.Url.AbsolutePath.TrimEnd("/"))
    if (-not $path) { $path = "/" }
    $tunnelParam = $request.QueryString["url"]

    $payload = switch -Regex ($path) {
      "^/status$" { Get-FullStatus }
      "^/start$" { Invoke-HomeCommand "start-all" $tunnelParam }
      "^/cmd/start-all$" { Invoke-HomeCommand "start-all" $tunnelParam }
      "^/cmd/start-bot$" { Invoke-HomeCommand "start-bot" $tunnelParam }
      "^/cmd/start-analyzer$" { Invoke-HomeCommand "start-analyzer" $tunnelParam }
      "^/cmd/start-analyzer-once$" { Invoke-HomeCommand "start-analyzer-once" $tunnelParam }
      "^/cmd/start-tunnel$" { Invoke-HomeCommand "start-tunnel" $tunnelParam }
      "^/cmd/wire$" { Invoke-HomeCommand "wire" $tunnelParam }
      "^/cmd/stop-bot$" { Invoke-HomeCommand "stop-bot" $tunnelParam }
      default {
        @{
          ok = $true
          endpoints = @(
            "/status",
            "/cmd/start-all",
            "/cmd/start-bot",
            "/cmd/start-analyzer",
            "/cmd/start-analyzer-once",
            "/cmd/start-tunnel",
            "/cmd/wire?url=https://....trycloudflare.com",
            "/cmd/stop-bot"
          )
        }
      }
    }

    Send-Json -Response $response -Payload $payload
  }
} finally {
  $listener.Stop()
}

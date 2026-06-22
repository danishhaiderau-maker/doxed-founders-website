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

function Stop-ListenPort([int]$Port) {
  $killed = @()
  $listen = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($listen) {
    $procId = [int]$listen.OwningProcess
    if ($procId -gt 0) {
      Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
      $killed += $procId
    }
  }
  return $killed
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
      "^/cmd/stop-bot$" { Invoke-HomeCommand "stop-bot" $tunnelParam }
      "^/cmd/stop-analyzer$" { Invoke-HomeCommand "stop-analyzer" $tunnelParam }
      "^/cmd/stop-all$" { Invoke-HomeCommand "stop-all" $tunnelParam }
      default {
        @{
          ok = $false
          error = "Unknown path: $path - close launcher window and re-run START-LAUNCHER.cmd"
        }
      }
    }
    Send-Json -Response $response -Payload $payload
  } catch {
    Send-Json -Response $response -Payload @{ ok = $false; error = $_.Exception.Message } -Status 500
  }
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
  $botPortOpen = Test-PortOpen $BotPort
  $analyzerPortOpen = Test-PortOpen $AnalyzerPort
  $tunnelUrl = $null
  if (Test-Path $tunnelUrlFile) {
    $tunnelUrl = (Get-Content $tunnelUrlFile -Raw -ErrorAction SilentlyContinue).Trim()
  }
  $cloudflaredRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
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
      online = $analyzerPortOpen
      dashboard = "http://127.0.0.1:$AnalyzerPort"
      lan = "http://10.0.0.102:$AnalyzerPort"
      note = "Must run from $agentDir - not Final Bots root"
    }
    tunnel = @{
      url = $tunnelUrl
      live = $false
      cloudflaredRunning = $cloudflaredRunning
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
      return @{ ok = $true; message = "Tunnel window opened - copy trycloudflare URL then Wire." }
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
      $wireScript = Join-Path $scriptDir "wire-home-bot-background.ps1"
      Start-Process powershell -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $wireScript, "-Url", $url
      ) | Out-Null
      return @{ ok = $true; message = "Wiring $url to Neon + Railway in background (30-60s)." }
    }
    "stop-bot" {
      $killed = @(Stop-ListenPort $BotPort)
      return @{
        ok = $true
        message = if ($killed.Count) { "Stopped PID(s) on :$BotPort - $($killed -join ', ')" } else { "Nothing listening on :$BotPort" }
      }
    }
    "stop-analyzer" {
      $killed = @(Stop-ListenPort $AnalyzerPort)
      return @{
        ok = $true
        message = if ($killed.Count) { "Stopped analyzer on :$AnalyzerPort - $($killed -join ', ')" } else { "Nothing listening on :$AnalyzerPort" }
      }
    }
    "stop-all" {
      $botKilled = @(Stop-ListenPort $BotPort)
      $analyzerKilled = @(Stop-ListenPort $AnalyzerPort)
      $tunnelKilled = @()
      Get-Process cloudflared -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
        $tunnelKilled += $_.Id
      }
      return @{
        ok = $true
        message = "Stopped bot:$($botKilled.Count) analyzer:$($analyzerKilled.Count) tunnel:$($tunnelKilled.Count)"
        pids = @{ bot = $botKilled; analyzer = $analyzerKilled; tunnel = $tunnelKilled }
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
    Serve-Request $listener.GetContext()
  }
} finally {
  $listener.Stop()
}

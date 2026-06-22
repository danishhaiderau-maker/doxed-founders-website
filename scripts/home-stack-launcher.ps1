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
  $listen = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($listen) {
    $procId = [int]$listen.OwningProcess
    if ($procId -gt 0) {
      Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
      $killed += $procId
    }
  }
  return $killed
}

function Start-DetachedPs1 {
  param(
    [string]$ScriptPath,
    [string[]]$ExtraArgs = @(),
    [switch]$NoExit,
    [string]$WindowTitle = "Doxed Home Stack"
  )
  if (-not (Test-Path $ScriptPath)) { throw "Missing script: $ScriptPath" }
  $argList = New-Object System.Collections.Generic.List[string]
  if ($NoExit) { $argList.Add("-NoExit") }
  $argList.Add("-NoProfile")
  $argList.Add("-ExecutionPolicy")
  $argList.Add("Bypass")
  $argList.Add("-File")
  $argList.Add($ScriptPath)
  foreach ($a in $ExtraArgs) { $argList.Add([string]$a) }
  Start-Process -FilePath "powershell.exe" `
    -ArgumentList $argList.ToArray() `
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
  $botPortOpen = Test-PortOpen $BotPort
  $analyzerPortOpen = Test-PortOpen $AnalyzerPort
  $tunnelUrl = Get-TunnelUrl
  $cloudflaredRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  $tunnelLive = $cloudflaredRunning -and (Test-TunnelLive $tunnelUrl)
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
      live = $tunnelLive
      cloudflaredRunning = $cloudflaredRunning
    }
  }
}

function Invoke-HomeCommand([string]$Action, [string]$QueryUrl) {
  switch ($Action) {
    "start-all" {
      $messages = [System.Collections.Generic.List[string]]::new()

      if (-not (Test-PortOpen $BotPort)) {
        Start-DetachedPs1 (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort") -NoExit -WindowTitle "Doxed Bot :7800"
        $messages.Add("[1/4] Bot window opened on :$BotPort")
        Start-Sleep -Seconds 4
      } else {
        $messages.Add("[1/4] Bot already online on :$BotPort")
      }

      if (-not (Test-PortOpen $AnalyzerPort)) {
        Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @() -NoExit -WindowTitle "Doxed Analyzer :9001"
        $messages.Add("[2/4] Analyzer window opened on :$AnalyzerPort")
      } else {
        $messages.Add("[2/4] Analyzer already online on :$AnalyzerPort")
      }

      $cfRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
      if (-not $cfRunning) {
        Start-DetachedPs1 (Join-Path $scriptDir "setup-home-bot-tunnel.ps1") @("-Quick", "-Port", "$BotPort") -NoExit -WindowTitle "Doxed Cloudflare Tunnel"
        $messages.Add("[3/4] Tunnel window opened (watch for trycloudflare URL)")
      } else {
        $messages.Add("[3/4] cloudflared already running")
      }

      Start-DetachedPs1 (Join-Path $scriptDir "home-stack-control-panel.ps1") @("-BotPort", "$BotPort", "-AnalyzerPort", "$AnalyzerPort") -NoExit -WindowTitle "Doxed Stack Control Panel"
      $messages.Add("[4/4] Control panel opened (live status every 5s)")

      $autoWire = Join-Path $scriptDir "auto-wire-after-tunnel.ps1"
      Start-Process powershell.exe -ArgumentList @(
        "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $autoWire
      ) -WorkingDirectory $repoRoot -WindowStyle Normal
      $messages.Add("Auto-wire started in background when tunnel URL appears")

      return @{ ok = $true; message = ($messages -join "`n") }
    }
    "start-bot" {
      if (Test-PortOpen $BotPort) {
        return @{ ok = $true; message = "Bot already listening on :$BotPort" }
      }
      Start-DetachedPs1 (Join-Path $scriptDir "start-home-bot.ps1") @("-Port", "$BotPort") -NoExit -WindowTitle "Doxed Bot :7800"
      return @{ ok = $true; message = "Bot window opened on :$BotPort" }
    }
    "start-analyzer" {
      Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @() -NoExit -WindowTitle "Doxed Analyzer :9001"
      return @{
        ok = $true
        message = "Analyzer started from $agentDir (reads bot CSVs there)."
      }
    }
    "start-analyzer-once" {
      Start-DetachedPs1 (Join-Path $scriptDir "start-home-analyzer.ps1") @("-Once") -NoExit -WindowTitle "Doxed Analyzer (once)"
      return @{ ok = $true; message = "Analyzer single pass started." }
    }
    "start-tunnel" {
      Start-DetachedPs1 (Join-Path $scriptDir "setup-home-bot-tunnel.ps1") @("-Quick", "-Port", "$BotPort") -NoExit -WindowTitle "Doxed Cloudflare Tunnel"
      return @{ ok = $true; message = "Tunnel window opened - URL saves to .home-tunnel-url automatically" }
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
      Start-Process powershell.exe -ArgumentList @(
        "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", (Join-Path $scriptDir "wire-home-bot-background.ps1"),
        "-Url", $url
      ) -WorkingDirectory $repoRoot -WindowStyle Normal
      return @{ ok = $true; message = "Wiring $url to Neon + Railway (see window / .home-wire.log)" }
    }
    "stop-bot" {
      $killed = @(Stop-ListenPort $BotPort)
      return @{
        ok = $true
        message = if ($killed.Count) { "Stopped bot on :$BotPort (PID $($killed -join ', '))" } else { "Nothing listening on :$BotPort" }
      }
    }
    "stop-analyzer" {
      $killed = @(Stop-ListenPort $AnalyzerPort)
      return @{
        ok = $true
        message = if ($killed.Count) { "Stopped analyzer on :$AnalyzerPort (PID $($killed -join ', '))" } else { "Nothing listening on :$AnalyzerPort" }
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
      Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
          $_.CommandLine -and (
            $_.CommandLine -like "*home-stack-control-panel*" -or
            $_.CommandLine -like "*auto-wire-after-tunnel*"
          )
        } | ForEach-Object {
          Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
      return @{
        ok = $true
        message = "Stopped bot:$($botKilled.Count) analyzer:$($analyzerKilled.Count) tunnel:$($tunnelKilled.Count). Bridge :7810 still running."
        pids = @{ bot = $botKilled; analyzer = $analyzerKilled; tunnel = $tunnelKilled }
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

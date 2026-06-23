# Shared helpers for home-stack-launcher.ps1 and home-stack-start-all.ps1
param(
  [int]$Port = 7810,
  [int]$BotPort = 7800,
  [int]$AnalyzerPort = 9001
)

if (-not $scriptDir) {
  $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if (-not $repoRoot) {
  $repoRoot = Split-Path -Parent $scriptDir
}
$agentDir = Join-Path $repoRoot "services\btc-conservative-agent"
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"

function Test-PortOpen([int]$P) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $iar = $c.BeginConnect("127.0.0.1", $P, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(400)) {
      $c.Close()
      return $false
    }
    $c.EndConnect($iar)
    $c.Close()
    return $true
  } catch {
    return $false
  }
}

function Test-MultiPortOpen([int[]]$Ports, [int]$TimeoutMs = 400) {
  $pending = @{}
  foreach ($p in $Ports) {
    try {
      $c = New-Object System.Net.Sockets.TcpClient
      $iar = $c.BeginConnect("127.0.0.1", $p, $null, $null)
      $pending[$p] = @{ Client = $c; Ar = $iar }
    } catch { }
  }
  Start-Sleep -Milliseconds $TimeoutMs
  $out = @{}
  foreach ($p in $Ports) {
    $out[$p] = $false
    if (-not $pending.ContainsKey($p)) { continue }
    $entry = $pending[$p]
    if ($entry.Ar.IsCompleted) {
      try {
        $entry.Client.EndConnect($entry.Ar)
        $out[$p] = $true
      } catch { }
    }
    $entry.Client.Close()
  }
  return $out
}

function Test-TunnelLive([string]$Url) {
  if (-not $Url) { return $false }
  try {
    $r = Invoke-WebRequest -Uri "$Url/api/ping" -UseBasicParsing -TimeoutSec 4
    return $r.StatusCode -eq 200
  } catch {
    return $false
  }
}

$script:TunnelLiveCache = @{ url = ""; live = $false; at = [datetime]::MinValue }
function Test-TunnelLiveCached([string]$Url) {
  if (-not $Url) { return $false }
  $now = Get-Date
  if ($script:TunnelLiveCache.url -eq $Url -and ($now - $script:TunnelLiveCache.at).TotalSeconds -lt 12) {
    return $script:TunnelLiveCache.live
  }
  $live = Test-TunnelLive $Url
  $script:TunnelLiveCache = @{ url = $Url; live = $live; at = $now }
  return $live
}

function Stop-ListenPortFast([int]$ListenPort) {
  $killed = @()
  try {
    $pattern = ":$ListenPort\s"
    $job = Start-Job -ScriptBlock {
      param($Pat)
      netstat -ano | Select-String $Pat
    } -ArgumentList $pattern
    if (-not (Wait-Job $job -Timeout 5)) {
      Stop-Job $job -Force | Out-Null
      Remove-Job $job -Force | Out-Null
      return $killed
    }
    Receive-Job $job | ForEach-Object {
      $line = "$_".Trim()
      if ($line -notmatch 'LISTENING') { return }
      if ($line -match '\s(\d+)\s*$') {
        $procId = [int]$matches[1]
        if ($procId -gt 0 -and $procId -ne 4 -and $killed -notcontains $procId) {
          Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
          $killed += $procId
        }
      }
    }
    Remove-Job $job -Force | Out-Null
  } catch { }
  return $killed
}

function Stop-ListenPort([int]$ListenPort) {
  return @(Stop-ListenPortFast $ListenPort)
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

function Test-HomeScriptRunning([string]$ScriptName) {
  $hit = Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*$ScriptName*" } |
    Select-Object -First 1
  return [bool]$hit
}

function Close-HomeStackWindows {
  $closed = @()
  $windowTitles = @(
    "Doxed Bot :7800",
    "Doxed Analyzer",
    "Doxed Analyzer (once)",
    "Doxed Cloudflare Tunnel",
    "Doxed Cloudflare Tunnel (stable)",
    "Doxed Stack Control Panel",
    "Doxed Auto-Wire",
    "Doxed Wire to Site",
    "Doxed Tunnel Watchdog",
    "Doxed Tunnel Restart",
    "Doxed Stack Start"
  )
  foreach ($title in $windowTitles) {
    & taskkill.exe /F /FI "WINDOWTITLE eq $title" 2>$null | Out-Null
    $closed += $title
  }
  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and (
        $_.CommandLine -like "*start-home-bot.ps1*" -or
        $_.CommandLine -like "*start-home-analyzer.ps1*" -or
        $_.CommandLine -like "*setup-home-bot-tunnel.ps1*" -or
        $_.CommandLine -like "*run-named-bot-tunnel.ps1*" -or
        $_.CommandLine -like "*restart-home-tunnel.ps1*" -or
        $_.CommandLine -like "*home-stack-control-panel.ps1*" -or
        $_.CommandLine -like "*auto-wire-after-tunnel.ps1*" -or
        $_.CommandLine -like "*wire-home-bot-background.ps1*" -or
        $_.CommandLine -like "*tunnel-watchdog.ps1*" -or
        $_.CommandLine -like "*home-stack-start-all.ps1*"
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

function Close-HomeStackWindowTitles {
  $closed = @()
  $windowTitles = @(
    "Doxed Bot :7800",
    "Doxed Analyzer",
    "Doxed Analyzer (once)",
    "Doxed Cloudflare Tunnel",
    "Doxed Cloudflare Tunnel (stable)",
    "Doxed Stack Control Panel",
    "Doxed Auto-Wire",
    "Doxed Wire to Site",
    "Doxed Tunnel Watchdog",
    "Doxed Tunnel Restart",
    "Doxed Stack Start",
    "Doxed Start Everything",
    "Local Collection Bot :7002",
    "Local Collection Analyzer :9500",
    "Local Collection Analyzer (once)"
  )
  foreach ($title in $windowTitles) {
    & taskkill.exe /F /FI "WINDOWTITLE eq $title" 2>$null | Out-Null
    $closed += $title
  }
  return $closed
}

function Stop-GlobalStackFast {
  param(
    [int]$GlobalBotPort = 7002,
    [int]$GlobalAnalyzerPort = 9500
  )
  $botPort = @(Stop-ListenPortFast $GlobalBotPort)
  $analyzerPort = @(Stop-ListenPortFast $GlobalAnalyzerPort)
  $tunnel = @(Stop-Cloudflared)
  $titles = @(
    "Doxed Bot :$GlobalBotPort",
    "Doxed Analyzer :$GlobalAnalyzerPort",
    "Doxed Analyzer (once)",
    "Doxed Cloudflare Tunnel",
    "Doxed Cloudflare Tunnel (stable)",
    "Doxed Start Everything",
    "Doxed Stack Start"
  )
  foreach ($title in $titles) {
    & taskkill.exe /F /FI "WINDOWTITLE eq $title" 2>$null | Out-Null
  }
  Clear-TunnelUrlFile
  return @{
    botPort = $botPort
    analyzerPort = $analyzerPort
    tunnel = $tunnel
  }
}

function Stop-LocalLabFast {
  $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
  $repoRoot = Split-Path -Parent $scriptDir
  $stopScript = Join-Path (Split-Path -Parent $repoRoot) "stop_stack.ps1"
  if (Test-Path $stopScript) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript 2>$null | Out-Null
  } else {
    Stop-ListenPortFast 7800 | Out-Null
    Stop-ListenPortFast 9001 | Out-Null
    Stop-PythonMatching "15minu_bot.py" | Out-Null
  }
  return @{ stopped = $true; ports = @(7800, 9001) }
}

function Stop-AllHomeStackFast {
  # Legacy: stop global showcase ports only (does not touch local lab :7800/:9001).
  return Stop-GlobalStackFast -GlobalBotPort $BotPort -GlobalAnalyzerPort $AnalyzerPort
}

function Stop-AllHomeStack {
  $result = Stop-AllHomeStackFast
  $scriptHits = @()
  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and (
        $_.CommandLine -like "*start-home-bot.ps1*" -or
        $_.CommandLine -like "*start-home-analyzer.ps1*" -or
        $_.CommandLine -like "*setup-home-bot-tunnel.ps1*" -or
        $_.CommandLine -like "*run-named-bot-tunnel.ps1*" -or
        $_.CommandLine -like "*restart-home-tunnel.ps1*" -or
        $_.CommandLine -like "*home-stack-control-panel.ps1*" -or
        $_.CommandLine -like "*auto-wire-after-tunnel.ps1*" -or
        $_.CommandLine -like "*wire-home-bot-background.ps1*" -or
        $_.CommandLine -like "*tunnel-watchdog.ps1*" -or
        $_.CommandLine -like "*home-stack-start-all.ps1*"
      )
    } | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $scriptHits += "pid:$($_.ProcessId)"
    }
  $result.scriptProcesses = $scriptHits
  return $result
}

function Test-AnalyzerRunning {
  if (Test-PortOpen $AnalyzerPort) { return $true }
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

function Start-HiddenPs1 {
  param(
    [string]$ScriptPath,
    [string[]]$ExtraArgs = @()
  )
  if (-not (Test-Path $ScriptPath)) { throw "Missing script: $ScriptPath" }
  $errLog = Join-Path $repoRoot ".home-cmd-worker.err.log"
  $args = @("-WindowStyle", "Hidden", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath) + $ExtraArgs
  Start-Process -FilePath "powershell.exe" -ArgumentList $args -WorkingDirectory $repoRoot -WindowStyle Hidden `
    -RedirectStandardError $errLog
}

function Start-VisibleConsole {
  param(
    [string]$ScriptPath,
    [string[]]$ExtraArgs = @(),
    [string]$Title = "Doxed Home Stack"
  )
  if (-not (Test-Path $ScriptPath)) { throw "Missing script: $ScriptPath" }
  $scriptEsc = ($ScriptPath -replace '"', '""')
  $psLine = "powershell.exe -NoExit -NoProfile -ExecutionPolicy Bypass -File `"$scriptEsc`""
  foreach ($a in $ExtraArgs) {
    if ($null -eq $a -or "$a" -eq "") { continue }
    $aEsc = ("$a" -replace '"', '""')
    if ($aEsc -match '\s') { $psLine += " `"$aEsc`"" } else { $psLine += " $aEsc" }
  }
  $titleEsc = ($Title -replace '"', '')
  # cmd /k keeps the window open even if PowerShell exits unexpectedly.
  Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", "title $titleEsc & $psLine") `
    -WorkingDirectory $repoRoot -WindowStyle Normal
}

function Start-DetachedPs1 {
  param(
    [string]$ScriptPath,
    [string[]]$ExtraArgs = @(),
    [switch]$NoExit,
    [string]$WindowTitle = "Doxed Home Stack",
    [ValidateSet("Minimized", "Normal")]
    [string]$Show = "Normal"
  )
  if (-not (Test-Path $ScriptPath)) { throw "Missing script: $ScriptPath" }
  if ($Show -eq "Normal") {
    Start-VisibleConsole -ScriptPath $ScriptPath -ExtraArgs $ExtraArgs -Title $WindowTitle
    return
  }
  $argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ScriptPath) + $ExtraArgs
  if ($NoExit) { $argList = @("-NoExit") + $argList }
  Start-Process -FilePath "powershell.exe" -ArgumentList $argList -WorkingDirectory $repoRoot -WindowStyle Minimized
}

function Get-TunnelUrl {
  if (Test-Path $tunnelUrlFile) {
    $raw = Get-Content $tunnelUrlFile -Raw -ErrorAction SilentlyContinue
    if ($null -ne $raw -and "$raw".Trim()) {
      $t = "$raw".Trim()
      if ($t -match 'bot\.doxxedcrypto\.digital' -and -not (Use-NamedTunnel)) {
        return $null
      }
      return $t
    }
  }
  return $null
}

function Use-NamedTunnel {
  $flag = Join-Path $repoRoot ".home-use-named-tunnel"
  if (-not (Test-Path $flag)) { return $false }
  $configDir = Join-Path $env:USERPROFILE ".cloudflared"
  $cred = Get-ChildItem -Path (Join-Path $configDir "doxed-btc-bot*.json") -ErrorAction SilentlyContinue | Select-Object -First 1
  $token = Join-Path $configDir "doxed-btc-bot.token"
  return ($null -ne $cred) -or (Test-Path $token)
}

function Start-AnalyzerDashboard {
  return (Test-PortOpen $AnalyzerPort)
}

function Start-CloudflaredNamedHidden {
  param([int]$Port = 7800)
  $configDir = Join-Path $env:USERPROFILE ".cloudflared"
  $tunnelName = "doxed-btc-bot"
  $tokenFile = Join-Path $configDir "$tunnelName.token"
  $logDir = Join-Path $repoRoot "logs"
  if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
  $outLog = Join-Path $logDir "cloudflared-named.log"
  $errLog = Join-Path $logDir "cloudflared-named.err.log"

  if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    throw "cloudflared not installed"
  }

  $args = @()
  if (Test-Path $tokenFile) {
    $token = (Get-Content $tokenFile -Raw).Trim()
    $args = @("tunnel", "run", "--token", $token)
  } else {
    $cred = Get-ChildItem -Path (Join-Path $configDir "$tunnelName*.json") -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $cred) { throw "Named tunnel not configured" }
    $configPath = Join-Path $configDir "config.yml"
    @(
      "tunnel: $tunnelName"
      "credentials-file: $($cred.FullName)"
      "ingress:"
      "  - hostname: bot.doxxedcrypto.digital"
      "    service: http://127.0.0.1:$Port"
      "  - service: http_status:404"
    ) | Set-Content -Path $configPath -Encoding UTF8
    $args = @("tunnel", "run", $tunnelName)
  }

  Set-Content -Path $tunnelUrlFile -Value "https://bot.doxxedcrypto.digital" -NoNewline
  Start-Process -FilePath "cloudflared" -ArgumentList $args -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog -WorkingDirectory $repoRoot
}

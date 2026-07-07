# Shared helpers for home-stack-launcher.ps1 and home-stack-start-all.ps1
param(
  [int]$BridgePort = 7810,
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
$userStoppedFile = Join-Path $repoRoot ".home-stack-user-stopped"

function Set-HomeStackUserStopped {
  Set-Content -Path $userStoppedFile -Value (Get-Date -Format o) -NoNewline
}

function Clear-HomeStackUserStopped {
  Remove-Item $userStoppedFile -Force -ErrorAction SilentlyContinue
}

function Test-HomeStackUserStopped {
  return Test-Path $userStoppedFile
}

function Test-PortOpen([int]$P) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $iar = $c.BeginConnect("127.0.0.1", $P, $null, $null)
    if (-not $iar.AsyncWaitHandle.WaitOne(1200)) {
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

# HTTP liveness check — confirms the server actually answers (not just that the port
# is bound). Replaces the 400ms TCP-only check that produced false "offline" flicker
# on the Agent Hub when the listening socket was momentarily slow under load.
function Test-HttpAlive([string]$Url, [int]$TimeoutMs = 1500) {
  try {
    $code = curl.exe -s --max-time ([math]::Max(1, [int]($TimeoutMs / 1000))) -o NUL -w "%{http_code}" $Url 2>$null
    return ([int]$code -ge 200 -and [int]$code -lt 500)
  } catch {
    return $false
  }
}

<# Probe several URLs in parallel with async HttpWebRequest so the bridge listener never
blocks for sum(url timeouts) — runs in max(timeoutMs). Returns hashtable url -> bool. #>
function Test-HttpAliveParallel([string[]]$Urls, [int]$TimeoutMs = 1500) {
  $out = @{}
  $pending = @()
  foreach ($u in $Urls) {
    $out[$u] = $false
    try {
      $req = [System.Net.HttpWebRequest]::Create($u)
      $req.Method = "GET"
      $req.Timeout = 5000
      $req.ReadWriteTimeout = $TimeoutMs
      $iar = $req.BeginGetResponse($null, $req)
      $pending += @{ Url = $u; Req = $req; Ar = $iar }
    } catch {
      # leave false
    }
  }
  if ($pending.Count -gt 0) {
    # Poll each AsyncWaitHandle with WaitOne (STA-safe; WaitAll for multiple handles
    # throws on a single-threaded apartment thread, which is the PowerShell default).
    $deadline = [datetime]::Now.AddMilliseconds($TimeoutMs)
    $remaining = [System.Collections.ArrayList]@($pending)
    while ($remaining.Count -gt 0 -and [datetime]::Now -lt $deadline) {
      $stillPending = [System.Collections.ArrayList]@()
      foreach ($p in $remaining) {
        if ($p.Ar.AsyncWaitHandle.WaitOne(50)) {
          try {
            $resp = $p.Req.EndGetResponse($p.Ar)
            $code = [int]$resp.StatusCode
            $resp.Close()
            $out[$p.Url] = ($code -ge 200 -and $code -lt 500)
          } catch {
            # false (timeout / connection refused / >=500)
          }
        } else {
          [void]$stillPending.Add($p)
        }
      }
      $remaining = $stillPending
    }
    # Anything still pending after the deadline: leave false (EndGetResponse would throw).
  }
  return $out
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
    Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty OwningProcess -Unique |
      ForEach-Object {
        $procId = [int]$_
        if ($procId -gt 0 -and $procId -ne 4 -and $killed -notcontains $procId) {
          Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
          $killed += $procId
        }
      }
  } catch { }
  return $killed
}

function Stop-RelayStatePusher {
  $killed = @()
  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and $_.CommandLine -like "*relay-state-pusher.ps1*"
    } | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $killed += $_.ProcessId
    }
  return $killed
}

function Stop-HomeStackSupervisor {
  $killed = @()
  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.CommandLine -and (
        $_.CommandLine -like "*home-stack-supervisor.ps1*" -or
        $_.CommandLine -like "*auto-wire-after-tunnel.ps1*"
      )
    } | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $killed += $_.ProcessId
    }
  Remove-Item (Join-Path $repoRoot ".home-stack-supervisor.pid") -Force -ErrorAction SilentlyContinue
  try {
    if (Test-Path (Join-Path $repoRoot ".home-stack-supervisor.lock")) {
      Remove-Item (Join-Path $repoRoot ".home-stack-supervisor.lock") -Force -ErrorAction SilentlyContinue
    }
  } catch { }
  return $killed
}

function Stop-ProcessTree {
  param([int]$ProcessId)
  if ($ProcessId -le 0) { return }
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.ParentProcessId -eq $ProcessId } |
    ForEach-Object { Stop-ProcessTree $_.ProcessId }
  Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

function Close-WindowsByTitlePrefix {
  param(
    [string[]]$Prefixes,
    [int[]]$ExcludeProcessIds = @()
  )
  $closed = @()
  $exclude = @{}
  foreach ($id in $ExcludeProcessIds) {
    if ($id -gt 0) { $exclude[$id] = $true }
  }
  Get-Process cmd, powershell, pwsh -ErrorAction SilentlyContinue | ForEach-Object {
    if ($exclude.ContainsKey($_.Id)) { return }
    $t = $_.MainWindowTitle
    if (-not $t) { return }
    foreach ($prefix in $Prefixes) {
      if ($t -like "$prefix*") {
        Stop-ProcessTree $_.Id
        $closed += "title:$t"
        return
      }
    }
  }
  return $closed
}

function Close-StaleOrchestratorConsoles {
  param([int[]]$ExcludeProcessIds = @())
  return @(Close-WindowsByTitlePrefix @(
    "Doxed Start Everything",
    "Doxed Stop Everything"
  ) -ExcludeProcessIds $ExcludeProcessIds)
}

function Invoke-HomeTerminalHygiene {
  param(
    [int]$BotPort = 7002,
    [int]$AnalyzerPort = 9500
  )
  $closed = [System.Collections.Generic.List[string]]::new()
  foreach ($t in (Close-StaleOrchestratorConsoles)) { $closed.Add($t) }

  $dupPushers = @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*relay-state-pusher.ps1*" })
  if ($dupPushers.Count -gt 1) {
    $dupPushers | Sort-Object CreationDate -Descending | Select-Object -Skip 1 | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $closed.Add("relay-pusher-dup:$($_.ProcessId)")
    }
  }

  $dupSupervisors = @(Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like "*home-stack-supervisor.ps1*" })
  if ($dupSupervisors.Count -gt 1) {
    $dupSupervisors | Sort-Object CreationDate -Descending | Select-Object -Skip 1 | ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      $closed.Add("supervisor-dup:$($_.ProcessId)")
    }
  }

  foreach ($t in (Close-WindowsByTitlePrefix @(
    "Doxed Start Everything",
    "Doxed Stop Everything",
    "Doxed Wire to Site",
    "Doxed Auto-Wire"
  ))) {
    if ($closed -notcontains $t) { $closed.Add($t) }
  }

  return @($closed)
}

function Close-ShowcaseStackConsoles {
  param(
    [int]$GlobalBotPort = 7002,
    [int]$GlobalAnalyzerPort = 9500,
    [switch]$KeepBridge,
    [int[]]$ExcludeProcessIds = @()
  )
  $closed = @()
  $exclude = @{}
  foreach ($id in $ExcludeProcessIds) {
    if ($id -gt 0) { $exclude[$id] = $true }
  }

  $titlePrefixes = @(
    "Doxed Bot :$GlobalBotPort",
    "Doxed Analyzer :$GlobalAnalyzerPort",
    "Doxed Analyzer (once)",
    "Doxed Cloudflare Tunnel",
    "Doxed Start Everything",
    "Doxed Stack Start",
    "Doxed Stop Everything"
  )
  if (-not $KeepBridge) {
    $titlePrefixes += "Doxed Home Bridge"
    $titlePrefixes += "TEST Bridge"
  }

  Get-Process cmd, powershell, pwsh -ErrorAction SilentlyContinue | ForEach-Object {
    if ($exclude.ContainsKey($_.Id)) { return }
    $t = $_.MainWindowTitle
    if (-not $t) { return }
    foreach ($prefix in $titlePrefixes) {
      if ($t -like "$prefix*") {
        Stop-ProcessTree $_.Id
        $closed += "title:$t"
        return
      }
    }
  }

  $scriptNeedles = @(
    "start-home-bot.ps1",
    "start-home-analyzer.ps1",
    "restart-home-tunnel.ps1",
    "home-stack-start-everything.ps1",
    "home-stack-start-all.ps1"
  )
  if (-not $KeepBridge) {
    $scriptNeedles += @("ensure-home-bridge.ps1", "home-stack-launcher.ps1")
  }

  Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe' OR Name = 'cmd.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      if ($exclude.ContainsKey($_.ProcessId)) { return $false }
      if (-not $_.CommandLine) { return $false }
      foreach ($needle in $scriptNeedles) {
        if ($_.CommandLine -like "*$needle*") { return $true }
      }
      return $false
    } | ForEach-Object {
      Stop-ProcessTree $_.ProcessId
      $closed += "pid:$($_.ProcessId)"
    }

  return $closed
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
        $_.CommandLine -like "*home-stack-start-all.ps1*" -or
        $_.CommandLine -like "*relay-state-pusher.ps1*"
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

function Stop-BotPidFile {
  $killed = @()
  $pidFile = Join-Path $repoRoot ".home-bot.pid"
  if (-not (Test-Path $pidFile)) { return $killed }
  try {
    $raw = Get-Content $pidFile -Raw -ErrorAction SilentlyContinue
    $botPid = [int]"$raw".Trim()
    if ($botPid -gt 0) {
      Stop-Process -Id $botPid -Force -ErrorAction SilentlyContinue
      $killed += $botPid
    }
  } catch { }
  Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
  return $killed
}

function Stop-GlobalStackFast {
  param(
    [int]$GlobalBotPort = 7002,
    [int]$GlobalAnalyzerPort = 9500,
    [int[]]$ExcludeProcessIds = @()
  )
  # Stop supervisor first so it cannot restart bot/analyzer/tunnel during shutdown.
  Set-HomeStackUserStopped
  $supervisor = @(Stop-HomeStackSupervisor)
  $relayPusher = @(Stop-RelayStatePusher)
  # Kill port listeners + hidden detached bot before tunnel/other cleanup (NoWait uses Hidden python).
  $botPidFile = @(Stop-BotPidFile)
  $botPort = @(Stop-ListenPortFast $GlobalBotPort)
  $botPy = @(Stop-PythonMatching "btc_conservative_agent")
  $analyzerPort = @(Stop-ListenPortFast $GlobalAnalyzerPort)
  $analyzerPy = @(Stop-PythonMatching "analyzer_research_engine")
  $tunnel = @(Stop-Cloudflared)
  Start-Sleep -Seconds 1
  $botPort += @(Stop-ListenPortFast $GlobalBotPort)
  $botPy += @(Stop-PythonMatching "btc_conservative_agent")
  $analyzerPort += @(Stop-ListenPortFast $GlobalAnalyzerPort)
  $analyzerPy += @(Stop-PythonMatching "analyzer_research_engine")
  $consoles = @(Close-ShowcaseStackConsoles -GlobalBotPort $GlobalBotPort -GlobalAnalyzerPort $GlobalAnalyzerPort -KeepBridge -ExcludeProcessIds $ExcludeProcessIds)
  Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
  Clear-TunnelUrlFile
  return @{
    botPort = @($botPort | Select-Object -Unique)
    analyzerPort = @($analyzerPort | Select-Object -Unique)
    tunnel = $tunnel
    relayPusher = $relayPusher
    supervisor = $supervisor
    botPidFile = $botPidFile
    pythonBot = @($botPy | Select-Object -Unique)
    pythonAnalyzer = @($analyzerPy | Select-Object -Unique)
    consoles = $consoles
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
    Stop-ListenPortFast 9500 | Out-Null
    Stop-PythonMatching "15minu_bot.py" | Out-Null
  }
  return @{ stopped = $true; ports = @(7800, 9500) }
}

function Stop-AllHomeStackFast {
  # Legacy: stop global showcase ports only (does not touch local lab :7800/:9500).
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
        $_.CommandLine -like "*home-stack-start-all.ps1*" -or
        $_.CommandLine -like "*relay-state-pusher.ps1*"
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
  $scriptEsc = ($ScriptPath -replace '"', '""')
  $psLine = "powershell.exe -WindowStyle Hidden -NoProfile -ExecutionPolicy Bypass -File `"$scriptEsc`""
  foreach ($a in $ExtraArgs) {
    if ($null -eq $a -or "$a" -eq "") { continue }
    $aEsc = ("$a" -replace '"', '""')
    if ($aEsc -match '\s') { $psLine += " `"$aEsc`"" } else { $psLine += " $aEsc" }
  }
  # Quoted command line required when repo path contains spaces (Final Bots).
  Start-Process -FilePath "cmd.exe" -ArgumentList @("/c", $psLine) `
    -WorkingDirectory $repoRoot -WindowStyle Hidden -RedirectStandardError $errLog
}

function Start-VisibleConsole {
  param(
    [string]$ScriptPath,
    [string[]]$ExtraArgs = @(),
    [string]$Title = "Doxed Home Stack",
    [switch]$NoPause
  )
  if (-not (Test-Path $ScriptPath)) { throw "Missing script: $ScriptPath" }
  $launcherDir = Join-Path $repoRoot "logs\launchers"
  if (-not (Test-Path $launcherDir)) {
    New-Item -ItemType Directory -Path $launcherDir -Force | Out-Null
  }
  $argLine = ""
  foreach ($a in $ExtraArgs) {
    if ($null -eq $a -or "$a" -eq "") { continue }
    if ("$a" -match '\s') { $argLine += " `"$a`"" } else { $argLine += " $a" }
  }
  $launcher = Join-Path $launcherDir ("run-" + [guid]::NewGuid().ToString("n") + ".cmd")
  $titleSafe = ($Title -replace '"', '')
  $scriptSafe = $ScriptPath
  $repoSafe = $repoRoot
  $launcherLines = @(
    "@echo off",
    "title `"$titleSafe`"",
    "cd /d `"$repoSafe`"",
    "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$scriptSafe`"$argLine",
    "if errorlevel 1 echo [ERROR] Script exited with code %errorlevel%"
  )
  if (-not $NoPause) {
    $launcherLines += @(
      "echo.",
      "echo --- Press any key to close this window ---",
      "pause >nul"
    )
  }
  $launcherLines | Set-Content -Path $launcher -Encoding ASCII
  $cmdMode = if ($NoPause) { "/c" } else { "/k" }
  # Pure cmd.exe window — avoids cmd parsing bugs with :7002 in title and keeps window open.
  Start-Process -FilePath "cmd.exe" -ArgumentList @($cmdMode, "`"$launcher`"") `
    -WorkingDirectory $repoRoot -WindowStyle Normal
}


function Start-HomeTunnel {
  param(
    [int]$Port = 0,
    [switch]$Force,
    [switch]$PreferVisible
  )
  if ($Port -le 0) { $Port = $BotPort }
  $restartScript = Join-Path $scriptDir "restart-home-tunnel.ps1"
  if (-not (Test-Path $restartScript)) { throw "Missing script: $restartScript" }
  $doForce = [bool]$Force
  if ((Use-NamedTunnel) -and -not $PreferVisible) {
    & $restartScript -Port $Port -Force:$doForce -Hidden | Out-Null
    return
  }
  if ($PreferVisible) {
    Start-VisibleConsole -ScriptPath $restartScript -ExtraArgs @("-Port", "$Port", "-Force") -Title "Doxed Cloudflare Tunnel" -NoPause
    return
  }
  Start-HiddenPs1 -ScriptPath $restartScript -ExtraArgs @("-Port", "$Port", "-Force", "-Hidden")
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

function Test-NamedTunnelConfigured {
  $configDir = Join-Path $env:USERPROFILE ".cloudflared"
  $cred = Get-ChildItem -Path (Join-Path $configDir "doxed-btc-bot*.json") -ErrorAction SilentlyContinue | Select-Object -First 1
  $token = Join-Path $configDir "doxed-btc-bot.token"
  return ($null -ne $cred) -or (Test-Path $token)
}

function Use-NamedTunnel {
  if (-not (Test-NamedTunnelConfigured)) { return $false }
  $flag = Join-Path $repoRoot ".home-use-named-tunnel"
  if (Test-Path $flag) { return $true }
  $showcaseLock = Join-Path $repoRoot "config\home-showcase.lock.json"
  if (Test-Path $showcaseLock) {
    try {
      $lock = Get-Content $showcaseLock -Raw | ConvertFrom-Json
      if ($lock.frozen -and -not [bool]$lock.disableTunnel) { return $true }
    } catch { }
  }
  return $false
}

function Start-AnalyzerDashboard {
  return (Test-PortOpen $AnalyzerPort)
}

function Start-CloudflaredNamedHidden {
  # F4 (2026-07-07 incident follow-up) — default --protocol to http2 because
  # this network blocks UDP/7844 to region2.v2.argotunnel.com, which produces
  # QUIC retry storms and silent 4h outages. The bridge spawn path here is
  # the one actually used in production (not run-named-bot-tunnel.ps1) —
  # closing the gap the Cloudflare tunnel investigator flagged.
  # Default port is 7002 (canonical showcase per config/bot-architecture.lock.json).
  param(
    [int]$Port = 7002,
    [string]$Protocol = $(if ($env:CLOUDFLARED_PROTOCOL) { $env:CLOUDFLARED_PROTOCOL } else { 'http2' })
  )
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

  Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
  Start-Sleep -Seconds 2

  # Force --protocol http2 (or whatever $Protocol resolves to). Without this
  # flag cloudflared defaults to "auto" which tries QUIC first and falls back
  # only after long retries — exactly the storm that took the relay down for
  # 3h59m on 2026-07-07. Set CLOUDFLARED_PROTOCOL=auto to revert.
  $args = @("tunnel", "run", "--protocol", $Protocol)
  if (Test-Path $tokenFile) {
    $token = (Get-Content $tokenFile -Raw).Trim()
    $args += @("--token", $token)
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
    $args += $tunnelName
  }

  Set-Content -Path $tunnelUrlFile -Value "https://bot.doxxedcrypto.digital" -NoNewline
  foreach ($rotLog in @($outLog, $errLog)) {
    try {
      if ((Get-Item $rotLog -ErrorAction SilentlyContinue).Length -gt 1048571) {
        $tail = Get-Content $rotLog -Tail 200 -ErrorAction SilentlyContinue
        if ($tail) { $tail | Set-Content $rotLog -Encoding UTF8 }
      }
    } catch { }
  }
  Start-Process -FilePath "cloudflared" -ArgumentList $args -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog -WorkingDirectory $repoRoot
}

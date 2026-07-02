# Visible stop for global showcase. Kills EVERY home-stack process across ALL terminals
# (powershell.exe / pwsh.exe / cmd.exe, visible OR hidden, identified by command-line match
# so the source terminal host does not matter), frees the ports, clears locks, then restarts
# ONLY the bridge (:7810) so the Agent Hub Start button still has a command path.
param(
  [int]$BotPort = 7002,
  [int]$AnalyzerPort = 9001,
  [int]$BridgePort = 7810,
  [switch]$NoWait,
  # Default: spare the bridge and restart it fresh so the Agent Hub Start button keeps a
  # command path. Pass -KillBridgeToo for a total wipe (e.g. before a full manual reset).
  [bool]$KeepBridgeAlive = $true,
  [switch]$KillBridgeToo
)

if ($KillBridgeToo) { $KeepBridgeAlive = $false }

$ErrorActionPreference = "Continue"
$Host.UI.RawUI.WindowTitle = "Doxed Stop Everything"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-common.ps1") -BridgePort $BridgePort -BotPort $BotPort -AnalyzerPort $AnalyzerPort

Write-Host ""
Write-Host "=== Stopping global showcase (all terminals) ===" -ForegroundColor Yellow
Write-Host "Bot :$BotPort | Analyzer :$AnalyzerPort | tunnel | supervisor | bridge | hidden helpers"
Write-Host "Local lab :7800/:9500 untouched."
Write-Host ""

# Protect this stop process + its parent so we do not suicide.
$exclude = @($PID)
try {
  $parent = (Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction SilentlyContinue).ParentProcessId
  if ($parent -gt 0) { $exclude += $parent }
} catch { }
$excludeHash = @{}; foreach ($id in $exclude) { if ($id -gt 0) { $excludeHash[$id] = $true } }

# Command-line patterns that identify ANY home-stack script host, regardless of whether it is
# running in powershell.exe, pwsh.exe, or a cmd.exe wrapper, and whether the window is visible
# or hidden (-WindowStyle Hidden has no window title, so title-based close misses it).
$scriptPatterns = @(
  'start-home-bot','start-home-analyzer','restart-home-tunnel','home-stack-supervisor',
  'relay-state-pusher','auto-wire-after-tunnel','wire-home-bot-background','home-stack-cmd-worker',
  'home-stack-control-panel','home-stack-start-everything','home-stack-stop-everything',
  'home-stack-start-all','home-stack-watch','tunnel-watchdog','overnight-architecture-guard',
  'reset-home-stack','home-stack-mode','home-stack-launcher',
  # Detached/hidden crash monitors launched by start-home-bot / start-home-analyzer /
  # the bridge watchdog scheduled task. They relaunch the bot/analyzer/tunnel on crash,
  # so Stop MUST kill them too, otherwise they immediately undo the user's Stop.
  'bot-auto-restart','analyzer-auto-restart','bridge-watchdog'
)

$killed = New-Object System.Collections.Generic.List[string]

# 1) Kill ALL home-stack script hosts (powershell/pwsh/cmd, visible OR hidden) by command-line match.
#    The bridge launcher (home-stack-launcher.ps1) is spared when KeepBridgeAlive is set so the admin
#    Start button keeps its command path; it is restarted fresh below anyway.
$scriptHosts = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe' OR Name='cmd.exe'" -ErrorAction SilentlyContinue |
  Where-Object {
    $_.CommandLine -and -not $excludeHash.ContainsKey($_.ProcessId) -and
    ($scriptPatterns | Where-Object { $_.CommandLine -like "*$($_)*" })
  })
foreach ($proc in $scriptHosts) {
  # Spare the live bridge launcher itself if requested (we restart it cleanly below).
  if ($KeepBridgeAlive -and $proc.CommandLine -like '*home-stack-launcher*') { continue }
  try { Stop-ProcessTree $proc.ProcessId; $killed.Add("$($proc.Name):$($proc.ProcessId)") } catch { }
}

# 2) Kill Start-VisibleConsole cmd wrappers (cmd /k logs\launchers\run-<guid>.cmd) that the
#    command-line match above may not catch (their command line references the .cmd file, not the
#    .ps1). Spare the bridge wrapper when KeepBridgeAlive.
$launcherCmds = @(Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -like '*launchers\run-*' -and -not $excludeHash.ContainsKey($_.ProcessId) })
foreach ($proc in $launcherCmds) {
  $cmdLine = $proc.CommandLine
  if ($KeepBridgeAlive -and ($cmdLine -like '*home-stack-launcher*' -or $cmdLine -like '*Doxed Home Bridge*')) { continue }
  # The bridge wrapper's .cmd references home-stack-launcher.ps1 indirectly; check the file content.
  $isBridge = $false
  if ($cmdLine -match 'launchers\\run-[^"]*\.cmd') {
    $cmdFile = $Matches[0] -replace 'launchers\\','logs\launchers\'
    $full = Join-Path $repoRoot $cmdFile
    if (Test-Path $full) { $isBridge = (Get-Content $full -Raw) -like '*home-stack-launcher*' }
  }
  if ($KeepBridgeAlive -and $isBridge) { continue }
  try { Stop-ProcessTree $proc.ProcessId; $killed.Add("cmd-wrapper:$($proc.ProcessId)") } catch { }
}

# 3) Kill python workers (bot + analyzer + legacy lab).
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and ($_.CommandLine -like '*btc_conservative_agent*' -or $_.CommandLine -like '*analyzer_research_engine*' -or $_.CommandLine -like '*15minu_bot*') } |
  ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $killed.Add("py:$($_.ProcessId)") } catch {} }

# 4) Kill cloudflared (tunnel).
Get-Process cloudflared -ErrorAction SilentlyContinue | ForEach-Object {
  try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue; $killed.Add("cf:$($_.Id)") } catch {}
}

Start-Sleep -Seconds 3

# 5) Free the ports - kill anything still listening on 7002/7810/9001.
foreach ($port in $BotPort,$BridgePort,$AnalyzerPort) {
  Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -eq $port } |
    ForEach-Object { try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue; $killed.Add("port${port}:$($_.OwningProcess)") } catch {} }
}

# 6) Clear locks + pid files so the next start is clean.
Set-HomeStackUserStopped
Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $repoRoot ".home-stack-supervisor.pid") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $repoRoot ".home-stack-supervisor.lock") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $repoRoot ".home-relay-pusher.lock") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $repoRoot ".home-bot.pid") -Force -ErrorAction SilentlyContinue

Write-Host "Processes killed: $($killed.Count)"
Write-Host ("  " + ($killed -join ' '))
Start-Sleep -Seconds 2

# 7) Restart ONLY the bridge so the Agent Hub Start button has a command path.
#    The bot/analyzer/tunnel/supervisor stay stopped - that is what "Stop" means.
if ($KeepBridgeAlive) {
  Write-Host "Restarting bridge :$BridgePort so admin Start still works..." -ForegroundColor Cyan
  Start-Process -FilePath "powershell.exe" -ArgumentList @("-NoProfile","-ExecutionPolicy","Bypass","-File",(Join-Path $scriptDir "ensure-home-bridge.ps1"),"-Force") -WindowStyle Normal
  $deadline = (Get-Date).AddSeconds(45)
  while ((Get-Date) -lt $deadline) {
    if (Test-PortOpen $BridgePort) {
      Start-Sleep -Seconds 2
      if (Test-PortOpen $BridgePort) { break }
    }
    Start-Sleep -Seconds 2
  }
}

$botOpen = Test-PortOpen $BotPort
$analyzerOpen = Test-PortOpen $AnalyzerPort
$cfRunning = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
$bridgeOpen = Test-PortOpen $BridgePort
Write-Host ""
Write-Host "After stop: bot :$BotPort open=$botOpen | analyzer :$AnalyzerPort open=$analyzerOpen | tunnel=$cfRunning | bridge :$BridgePort open=$bridgeOpen"
if ($botOpen -or $analyzerOpen -or $cfRunning) {
  Write-Host "Some workers still running - run Stop again." -ForegroundColor Red
} else {
  Write-Host "Showcase workers stopped. Bridge :$BridgePort is $($bridgeOpen.ToString().Replace('True','UP').Replace('False','DOWN')) - click Start showcase to bring the stack back." -ForegroundColor Green
}
Write-Host ""
if (-not $NoWait) {
  Write-Host "--- Press Enter to close this window ---" -ForegroundColor Cyan
  try { Read-Host } catch { Start-Sleep -Seconds 3600 }
}

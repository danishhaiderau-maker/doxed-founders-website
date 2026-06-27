# Robust full kill of every home-stack process — visible OR hidden, powershell/pwsh/cmd, python, cloudflared.
# Frees ports 7002/7810/9500 and clears locks/pid files. Leaves nothing behind.
$ErrorActionPreference = "Continue"
$repoRoot = "c:\Users\user\Desktop\Final Bots\doxedcryptofounder"
Set-Location $repoRoot

$scriptPatterns = @(
  'home-stack-launcher','ensure-home-bridge','home-stack-start-everything','home-stack-stop-everything',
  'start-home-bot','start-home-analyzer','restart-home-tunnel','home-stack-supervisor','relay-state-pusher',
  'auto-wire-after-tunnel','wire-home-bot-background','home-stack-cmd-worker','home-stack-control-panel',
  'home-stack-watch','overnight-architecture-guard','tunnel-watchdog','start-local-collection','reset-home-stack',
  'home-stack-start-all','home-stack-mode'
)
$killed = New-Object System.Collections.Generic.List[string]

# 1) Kill ALL home-stack script hosts (powershell/pwsh/cmd, visible OR hidden) by command-line match.
Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe' OR Name='cmd.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and ($scriptPatterns | Where-Object { $_.CommandLine -like "*$($_)*" }) } |
  ForEach-Object {
    $pidv = $_.ProcessId
    try { Stop-Process -Id $pidv -Force -ErrorAction SilentlyContinue; $killed.Add("ps:$pidv") } catch {}
  }

# 2) Kill python workers (bot + analyzer + legacy lab).
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and ($_.CommandLine -like '*btc_conservative_agent*' -or $_.CommandLine -like '*analyzer_research_engine*' -or $_.CommandLine -like '*15minu_bot*') } |
  ForEach-Object {
    $pidv = $_.ProcessId
    try { Stop-Process -Id $pidv -Force -ErrorAction SilentlyContinue; $killed.Add("py:$pidv") } catch {}
  }

# 3) Kill cloudflared.
Get-Process cloudflared -ErrorAction SilentlyContinue | ForEach-Object {
  $pidv = $_.Id
  try { Stop-Process -Id $pidv -Force -ErrorAction SilentlyContinue; $killed.Add("cf:$pidv") } catch {}
}

Start-Sleep -Seconds 3

# 4) Free ports 7002/7810/9500 - kill anything still listening.
foreach ($port in 7002,7810,9500) {
  Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -eq $port } |
    ForEach-Object {
      $owner = $_.OwningProcess
      try { Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue; $killed.Add("port${port}:${owner}") } catch {}
    }
}

# 5) Clear locks + pid files so the next start is clean.
Remove-Item (Join-Path $repoRoot ".home-analyzer-start.lock") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $repoRoot ".home-stack-supervisor.pid") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $repoRoot ".home-stack-supervisor.lock") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $repoRoot ".home-relay-pusher.lock") -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $repoRoot ".home-bot.pid") -Force -ErrorAction SilentlyContinue

Start-Sleep -Seconds 2

Write-Output ("killed: " + ($killed -join ' '))
Write-Output "=== AFTER KILL ==="
foreach ($port in 7002,7810,9500) {
  $listening = [bool](Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -eq $port })
  Write-Output ("port ${port}: " + $listening)
}
Write-Output ("cloudflared: " + [bool](Get-Process cloudflared -ErrorAction SilentlyContinue))
$left = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='cmd.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -and ($scriptPatterns | Where-Object { $_.CommandLine -like "*$($_)*" }) })
Write-Output ("home-stack ps procs left: " + $left.Count)
$pyleft = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*btc_conservative_agent*' -or $_.CommandLine -like '*analyzer_research_engine*' })
Write-Output ("python bot/analyzer left: " + $pyleft.Count)

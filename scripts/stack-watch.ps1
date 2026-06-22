# Poll home stack health every 15s (for restart tests).
param([int]$Minutes = 8)
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$log = Join-Path $repoRoot ".stack-watch.log"
$end = (Get-Date).AddMinutes($Minutes)
while ((Get-Date) -lt $end) {
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  $cf = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count
  $py = @(Get-Process python -ErrorAction SilentlyContinue).Count
  $localOk = $false
  $pubOk = $false
  try {
    $lr = curl.exe -s -m 4 "http://127.0.0.1:7800/api/ping" 2>&1
    $localOk = "$lr" -match '"ok"\s*:\s*true'
  } catch { }
  try {
    $pr = curl.exe -s -m 10 "https://bot.doxxedcrypto.digital/api/ping" 2>&1
    $pubOk = "$pr" -match '"ok"\s*:\s*true'
  } catch { }
  $line = "[$ts] cloudflared=$cf python=$py local=$localOk public=$pubOk"
  Add-Content -Path $log -Value $line
  Write-Output $line
  Start-Sleep -Seconds 15
}

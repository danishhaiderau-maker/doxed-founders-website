# 2-hour stack behavior watch. Emits a heartbeat line each poll and ALERT lines on regressions.
# Probes: bot :7002, analyzer :9500, bridge :7810 (+tunnel.live), tunnel public, site, duplicate positions.
$ProgressPreference = "SilentlyContinue"
$end = (Get-Date).AddHours(2)
$iter = 0
function Probe([string]$u, [int]$t) {
  try { $c = curl.exe -s -o NUL -w "%{http_code}" --max-time $t $u; if ($c -eq "200") { return "200" } else { return "HTTP_$c" } }
  catch { return "FAIL" }
}
while ((Get-Date) -lt $end) {
  $iter++
  $ts = (Get-Date).ToString("HH:mm:ss")
  $bot      = Probe "http://127.0.0.1:7002/api/ping" 6
  $an       = Probe "http://127.0.0.1:9500/api/status" 10
  $bridge   = Probe "http://127.0.0.1:7810/health" 5
  $tunnel   = Probe "https://bot.doxxedcrypto.digital/api/ping" 12
  $site     = Probe "https://doxxedcrypto.digital/api/health" 12
  # bridge tunnel.live + positions dup check (best-effort, non-fatal)
  $tunLive = "?"; $posCount = "?"; $dupIds = 0
  try {
    $s = Invoke-RestMethod "http://127.0.0.1:7810/status" -TimeoutSec 6
    $tunLive = [bool]$s.tunnel.live
  } catch { $tunLive = "err" }
  try {
    $st = Invoke-RestMethod "http://127.0.0.1:7002/api/state" -TimeoutSec 15
    $pos = @($st.positions)
    $posCount = $pos.Count
    $ids = @($pos | ForEach-Object { $_.trade_id } | Where-Object { $_ } | Sort-Object -Unique)
    if ($posCount -gt $ids.Count) { $dupIds = $posCount - $ids.Count }
  } catch { $posCount = "err" }
  $line = "[$ts] #$iter bot=$bot an=$an bridge=$bridge tunnel=$tunnel site=$site tunLive=$tunLive pos=$posCount dup=$dupIds"
  Write-Output $line
  if ($bot -ne "200")    { Write-Output "ALERT bot :7002 down ($bot)" }
  if ($an -ne "200")     { Write-Output "ALERT analyzer :9500 down ($an)" }
  if ($bridge -ne "200") { Write-Output "ALERT bridge :7810 down ($bridge)" }
  if ($tunnel -ne "200") { Write-Output "ALERT public tunnel down ($tunnel)" }
  if ($site -ne "200")   { Write-Output "ALERT site doxxedcrypto.digital down ($site)" }
  if ($dupIds -gt 0)     { Write-Output "ALERT duplicate position render still present (dup=$dupIds) - bot not restarted yet or dedup not live" }
  Start-Sleep -Seconds 300
}
Write-Output "WATCH COMPLETE after $iter polls."

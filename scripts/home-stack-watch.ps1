# Long-run health logger (read-only). Supervisor handles recovery.
param(
  [int]$Hours = 48,
  [int]$IntervalSec = 300,
  [int]$BotPort = 0,
  [int]$AnalyzerPort = 0,
  [int]$BridgePort = 7810
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "home-stack-mode.ps1")
$stackMode = Get-HomeStackMode
if ($BotPort -le 0) { $BotPort = $stackMode.BotPort }
if ($AnalyzerPort -le 0) { $AnalyzerPort = $stackMode.AnalyzerPort }
. (Join-Path $scriptDir "home-stack-common.ps1") -BotPort $BotPort -AnalyzerPort $AnalyzerPort -BridgePort $BridgePort
. (Join-Path $scriptDir "home-stack-health.ps1")

$logFile = Join-Path $repoRoot ".home-stack-watch.log"
$endAt = (Get-Date).AddHours($Hours)
$header = "=== watch started $(Get-Date -Format o) for ${Hours}h every ${IntervalSec}s ==="
Add-Content -Path $logFile -Value ""
Add-Content -Path $logFile -Value $header

while ((Get-Date) -lt $endAt) {
  $url = Get-TunnelUrl
  if ((Test-Path (Join-Path $repoRoot ".home-use-named-tunnel")) -and (Use-NamedTunnel)) {
    $url = "https://bot.doxxedcrypto.digital"
  }
  $bot = Test-BotHealthy
  $bridge = Test-BridgeHealthy
  $analyzer = Test-AnalyzerHealthy
  $cf = @(Get-Process cloudflared -ErrorAction SilentlyContinue).Count -gt 0
  $tunnel = if ($url -and $bot) { Test-TunnelPublicHealthy $url } else { $false }
  $line = "{0} bot={1} bridge={2} analyzer={3} cloudflared={4} tunnel_live={5} url={6}" -f (
    (Get-Date -Format "HH:mm:ss"),
    $bot, $bridge, $analyzer, $cf, $tunnel, $(if ($url) { $url } else { "none" })
  )
  Add-Content -Path $logFile -Value $line
  Write-Host $line
  Start-Sleep -Seconds $IntervalSec
}

Add-Content -Path $logFile -Value "=== watch ended $(Get-Date -Format o) ==="
Write-Host "Watch complete. Log: $logFile"

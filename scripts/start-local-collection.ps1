# Start frozen local collection stack: bot :7002 + analyzer :9500 (visible consoles, no tunnel).
param([switch]$OnceAnalyzer)

$ErrorActionPreference = "Continue"
. (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "local-collection-config.ps1")

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$flagFile = Join-Path $repoRoot ".local-collection-mode"
Set-Content -Path $flagFile -Value "bot=$($LocalCollection.BotPort) analyzer=$($LocalCollection.AnalyzerPort)" -NoNewline

Write-Host ""
Write-Host "=== Local collection (frozen ports) ===" -ForegroundColor Green
Write-Host "  Bot:      http://127.0.0.1:$($LocalCollection.BotPort)"
Write-Host "  Analyzer: http://127.0.0.1:$($LocalCollection.AnalyzerPort)/"
Write-Host "  Data:     $($LocalCollection.DataDir)"
Write-Host ""

foreach ($prodPort in @(7800, 9001)) {
  try {
    $c = New-Object System.Net.Sockets.TcpClient
    $async = $c.ConnectAsync("127.0.0.1", $prodPort)
    if ($async.Wait(800)) {
      $c.Close()
      netstat -ano | Select-String ":$prodPort\s" | ForEach-Object {
        if ("$_" -match '\s(\d+)\s*$') {
          Stop-Process -Id ([int]$matches[1]) -Force -ErrorAction SilentlyContinue
        }
      }
      Write-Host "Released production :$prodPort" -ForegroundColor DarkYellow
      Start-Sleep -Seconds 2
    }
  } catch { }
}

Start-Process -FilePath "powershell.exe" -ArgumentList @(
  "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass",
  "-File", (Join-Path $scriptDir "start-local-collection-bot.ps1"), "-NoWait"
) -WorkingDirectory $repoRoot -WindowStyle Normal

Start-Sleep -Seconds 10

$analyzerArgs = @(
  "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass",
  "-File", (Join-Path $scriptDir "start-local-collection-analyzer.ps1"), "-NoWait"
)
if ($OnceAnalyzer) { $analyzerArgs += "-Once" }
Start-Process -FilePath "powershell.exe" -ArgumentList $analyzerArgs -WorkingDirectory $repoRoot -WindowStyle Normal

$log = Join-Path $repoRoot ".home-start-all.log"
$line = "{0} [local-collection] Bot :$($LocalCollection.BotPort) + Analyzer :$($LocalCollection.AnalyzerPort) windows opened" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
Add-Content -Path $log -Value $line

Write-Host "Two console windows opened. Watch logs there."
Write-Host "Lock file: config\local-collection.lock.json (ports frozen until you edit it)."

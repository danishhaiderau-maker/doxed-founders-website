param(
  [int]$Port = 7800
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$tunnelUrlFile = Join-Path $repoRoot ".home-tunnel-url"
$namedFlag = Join-Path $repoRoot ".home-use-named-tunnel"
$configDir = Join-Path $env:USERPROFILE ".cloudflared"
$cred = Get-ChildItem -Path (Join-Path $configDir "doxed-btc-bot*.json") -ErrorAction SilentlyContinue | Select-Object -First 1
$useNamed = (Test-Path $namedFlag) -and $null -ne $cred

Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$argList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File")
if ($useNamed) {
  Set-Content -Path $tunnelUrlFile -Value "https://bot.doxxedcrypto.digital" -NoNewline
  $argList += (Join-Path $scriptDir "run-named-bot-tunnel.ps1"), "-Port", "$Port"
} else {
  if (Test-Path $tunnelUrlFile) { Remove-Item $tunnelUrlFile -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 1
  $argList += (Join-Path $scriptDir "setup-home-bot-tunnel.ps1"), "-Quick", "-Port", "$Port"
}

Start-Process -FilePath "powershell.exe" -ArgumentList (@("-WindowStyle", "Hidden", "-NoExit") + $argList) -WorkingDirectory $repoRoot -WindowStyle Hidden

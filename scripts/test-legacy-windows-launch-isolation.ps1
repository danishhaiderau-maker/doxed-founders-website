# Static safety contract for the quarantined Windows production launch paths.
# This test never starts, stops, or probes a runtime process.
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$optInPhrase = "I_UNDERSTAND_THIS_STARTS_A_SECOND_AI_TRADING_OWNER"
$script:Passed = 0

function Read-RepoFile([string]$RelativePath) {
  return [System.IO.File]::ReadAllText((Join-Path $repoRoot $RelativePath))
}

function Assert-Contract([bool]$Condition, [string]$Message) {
  if (-not $Condition) {
    throw "FAIL: $Message"
  }
  $script:Passed += 1
  Write-Host "PASS: $Message" -ForegroundColor Green
}

function Assert-GuardBefore(
  [string]$RelativePath,
  [string]$DangerMarker
) {
  $text = Read-RepoFile $RelativePath
  $guardIndex = $text.IndexOf($optInPhrase, [System.StringComparison]::Ordinal)
  $dangerIndex = $text.IndexOf($DangerMarker, [System.StringComparison]::Ordinal)
  Assert-Contract ($guardIndex -ge 0) "$RelativePath contains the exact disaster-recovery opt-in"
  Assert-Contract ($dangerIndex -ge 0) "$RelativePath still has its recoverable legacy implementation"
  Assert-Contract ($guardIndex -lt $dangerIndex) "$RelativePath checks the opt-in before legacy side effects"
  Assert-Contract ($text.Contains("exit 78")) "$RelativePath fails closed without the opt-in"
  Assert-Contract ($text.Contains("fly-canonical.lock.json")) "$RelativePath refuses a second owner while the Fly canonical lock exists"
}

function Assert-QuarantinedRuntimeScript(
  [string]$RelativePath,
  [string[]]$DangerMarkers,
  [bool]$RoutesToMirror = $true
) {
  $text = Read-RepoFile $RelativePath
  $guardIndex = $text.IndexOf('$obsoleteOwnerEnabled =', [System.StringComparison]::Ordinal)
  Assert-Contract ($text.Contains("DCF_ENABLE_OBSOLETE_WINDOWS_TRADING_OWNER")) "$RelativePath checks the obsolete-owner environment key"
  Assert-Contract ($text.Contains($optInPhrase)) "$RelativePath requires the exact disaster-recovery opt-in phrase"
  Assert-Contract ($text.Contains("-ceq")) "$RelativePath compares the opt-in exactly"
  Assert-Contract ($guardIndex -ge 0) "$RelativePath defines the quarantine before runtime behavior"
  Assert-Contract ($text.Contains("exit 78")) "$RelativePath has a fail-closed exit"
  Assert-Contract ($text.Contains("fly-canonical.lock.json")) "$RelativePath refuses a second owner while the Fly canonical lock exists"

  if ($RoutesToMirror) {
    Assert-Contract ($text.Contains("start-fly-desktop-mirror.ps1")) "$RelativePath routes default execution to the Fly desktop mirror/analyzer"
    Assert-Contract ($text.Contains("DCF_LEGACY_WINDOWS_LAUNCH_CONTRACT_TEST")) "$RelativePath exposes a no-side-effect contract-test path"
  }

  foreach ($dangerMarker in $DangerMarkers) {
    $dangerIndex = $text.IndexOf($dangerMarker, [System.StringComparison]::Ordinal)
    Assert-Contract ($dangerIndex -ge 0) "$RelativePath retains recoverable legacy marker '$dangerMarker'"
    Assert-Contract ($guardIndex -lt $dangerIndex) "$RelativePath quarantines before '$dangerMarker'"
  }
}

function Assert-DefaultExecutionFailsClosed(
  [string]$RelativePath
) {
  $absolutePath = Join-Path $repoRoot $RelativePath
  $previousOptIn = (Get-Item -Path "env:DCF_ENABLE_OBSOLETE_WINDOWS_TRADING_OWNER" -ErrorAction SilentlyContinue).Value
  $previousContractTest = (Get-Item -Path "env:DCF_LEGACY_WINDOWS_LAUNCH_CONTRACT_TEST" -ErrorAction SilentlyContinue).Value
  $previousErrorActionPreference = $ErrorActionPreference
  try {
    $env:DCF_ENABLE_OBSOLETE_WINDOWS_TRADING_OWNER = ""
    $env:DCF_LEGACY_WINDOWS_LAUNCH_CONTRACT_TEST = "NO_SIDE_EFFECTS"
    $ErrorActionPreference = "Continue"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $absolutePath *> $null
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $previousErrorActionPreference
    if ($null -eq $previousOptIn) {
      Remove-Item -Path "env:DCF_ENABLE_OBSOLETE_WINDOWS_TRADING_OWNER" -ErrorAction SilentlyContinue
    } else {
      $env:DCF_ENABLE_OBSOLETE_WINDOWS_TRADING_OWNER = $previousOptIn
    }
    if ($null -eq $previousContractTest) {
      Remove-Item -Path "env:DCF_LEGACY_WINDOWS_LAUNCH_CONTRACT_TEST" -ErrorAction SilentlyContinue
    } else {
      $env:DCF_LEGACY_WINDOWS_LAUNCH_CONTRACT_TEST = $previousContractTest
    }
  }
  Assert-Contract ($exitCode -eq 78) "$RelativePath default contract-test execution fails closed with exit 78"
}

$autostart = Read-RepoFile "scripts\start-showcase-bot.cmd"
Assert-Contract ($autostart.Contains("start-fly-desktop-mirror.ps1")) "scheduled-task entry starts the Fly desktop mirror"
foreach ($forbidden in @(
  "home-stack-start-everything.ps1",
  "start-showcase-bot-guard.ps1",
  "btc_conservative_agent.py",
  "cloudflared"
)) {
  Assert-Contract (-not $autostart.Contains($forbidden)) "scheduled-task entry excludes $forbidden"
}

$recovery = Read-RepoFile "scripts\fast-recover-global.ps1"
Assert-Contract ($recovery.Contains("start-fly-desktop-mirror.ps1")) "global recovery starts the Fly desktop mirror"
Assert-Contract ($recovery.Contains("home-stack-launcher.ps1")) "global recovery may restore the local command bridge"
foreach ($forbidden in @(
  "btc_conservative_agent.py",
  "Start-Process -FilePath `"python",
  "taskkill.exe",
  "cloudflared"
)) {
  Assert-Contract (-not $recovery.Contains($forbidden)) "global recovery excludes active legacy marker $forbidden"
}

foreach ($relativePath in @(
  "RECOVER-GLOBAL-STACK.cmd",
  "RESTART-LAUNCHER.cmd",
  "START-HOME.cmd"
)) {
  $shortcut = Read-RepoFile $relativePath
  Assert-Contract ($shortcut.Contains("fast-recover-global.ps1")) "$relativePath converges on canonical mirror recovery"
  Assert-Contract (-not $shortcut.Contains("start-home-stack.ps1")) "$relativePath cannot invoke the obsolete Windows stack"
}

Assert-GuardBefore "scripts\start-home-bot.ps1" '$logsDir ='
Assert-GuardBefore "scripts\start-home-stack.ps1" '$agentDir ='
Assert-GuardBefore "scripts\start-local-collection.ps1" '$flagFile ='
Assert-GuardBefore "scripts\start-local-collection-bot.ps1" '$vaultEnv ='

Assert-QuarantinedRuntimeScript `
  "scripts\home-stack-supervisor.ps1" `
  @("function Restart-BotComponent", "function Restart-TunnelComponent")
Assert-QuarantinedRuntimeScript `
  "scripts\home-stack-supervisor-watchdog.ps1" `
  @('$supervisorHeartbeatFile =', 'Start-Process -FilePath "powershell"')
Assert-QuarantinedRuntimeScript `
  "scripts\bridge-watchdog.ps1" `
  @("function Restart-Tunnel", '$logFile =') `
  $false
$bridgeWatchdog = Read-RepoFile "scripts\bridge-watchdog.ps1"
Assert-Contract (-not $bridgeWatchdog.Contains("start-fly-desktop-mirror.ps1")) "obsolete bridge watchdog cannot start a redundant desktop mirror"
Assert-QuarantinedRuntimeScript `
  "scripts\bot-auto-restart.ps1" `
  @("function Start-BotHidden", '$agentDir  =')
Assert-QuarantinedRuntimeScript `
  "scripts\register-supervisor-watchdog.ps1" `
  @('$identity =', "Register-ScheduledTask") `
  $false

foreach ($relativePath in @(
  "scripts\home-stack-supervisor.ps1",
  "scripts\home-stack-supervisor-watchdog.ps1",
  "scripts\bridge-watchdog.ps1",
  "scripts\bot-auto-restart.ps1",
  "scripts\register-supervisor-watchdog.ps1"
)) {
  Assert-DefaultExecutionFailsClosed $relativePath
}

$bridge = Read-RepoFile "scripts\home-stack-launcher.ps1"
Assert-Contract ($bridge.Contains($optInPhrase)) "home command bridge uses the same exact opt-in"
Assert-Contract ($bridge.Contains('"^/start$" { Invoke-HomeCommand "start-mirror"')) "default /start route is mirror-only"
Assert-Contract ($bridge.Contains('"^/cmd/start-mirror$"')) "home command bridge exposes an explicit safe mirror route"
foreach ($action in @(
  "start-all-local",
  "start-all-global",
  "start-all",
  "start-bot",
  "start-tunnel",
  "enable-named-tunnel",
  "wire",
  "reset-home-stack"
)) {
  $pattern = '(?s)"' + [regex]::Escape($action) + '"\s*\{\s*if \(-not \(Test-LegacyWindowsOwnerOptIn\)\)'
  Assert-Contract ([regex]::IsMatch($bridge, $pattern)) "home command bridge quarantines '$action'"
}

$registration = Read-RepoFile "scripts\register-bot-autostart.ps1"
Assert-Contract ($registration.Contains("DCF desktop mirror only:")) "scheduled-task registration describes the mirror-only contract"
Assert-Contract (-not $registration.Contains("start the showcase bot stack (bot :7002")) "scheduled-task registration removed the obsolete stack description"

foreach ($relativePath in @(
  "scripts\start-home-bot.ps1",
  "scripts\start-home-stack.ps1",
  "scripts\home-stack-launcher.ps1",
  "scripts\start-local-collection.ps1",
  "scripts\start-local-collection-bot.ps1",
  "scripts\register-bot-autostart.ps1",
  "scripts\home-stack-supervisor.ps1",
  "scripts\home-stack-supervisor-watchdog.ps1",
  "scripts\bridge-watchdog.ps1",
  "scripts\bot-auto-restart.ps1",
  "scripts\register-supervisor-watchdog.ps1",
  "scripts\fast-recover-global.ps1",
  "scripts\test-legacy-windows-launch-isolation.ps1"
)) {
  $tokens = $null
  $errors = $null
  [void][System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path $repoRoot $relativePath),
    [ref]$tokens,
    [ref]$errors
  )
  Assert-Contract ($errors.Count -eq 0) "$relativePath parses as valid PowerShell"
}

Write-Host ""
Write-Host "Legacy Windows launch isolation contract: $($script:Passed) checks passed." -ForegroundColor Cyan

param(
  [string]$TaskName = "DoxxedResearchStabilitySupervisor",
  [int]$IntervalMinutes = 5,
  [string]$VaultEnv = ""
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$launcher = Join-Path $scriptDir "start-research-stability-supervisor.ps1"
if (-not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
  throw "Research stability supervisor launcher is missing: $launcher"
}
if ($IntervalMinutes -lt 1) { throw "IntervalMinutes must be at least 1." }

$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$arguments = @(
  "-NoProfile",
  "-ExecutionPolicy", "Bypass",
  "-WindowStyle", "Hidden",
  "-File", ('"' + $launcher + '"'),
  "-RepairMissingLocal"
)
if ($VaultEnv) {
  $resolvedVault = [System.IO.Path]::GetFullPath($VaultEnv)
  if (-not (Test-Path -LiteralPath $resolvedVault -PathType Leaf)) {
    throw "Vault file does not exist: $resolvedVault"
  }
  $arguments += @("-VaultEnv", ('"' + $resolvedVault + '"'))
}

$action = New-ScheduledTaskAction `
  -Execute $powershell `
  -Argument ($arguments -join " ") `
  -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger `
  -Once `
  -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes $IntervalMinutes)
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -RunOnlyIfNetworkAvailable `
  -MultipleInstances IgnoreNew `
  -RestartCount 3 `
  -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited

$description = @(
  "DoxedCrypto read-only research recovery supervisor.",
  "May restore the local Fly mirror loop and refresh one revision-stale analyzer after exact parity.",
  "Cannot restart production trading, wipe evidence, change policies, or arm live execution."
) -join " "
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
  Set-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal | Out-Null
} else {
  Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description $description | Out-Null
}

Start-ScheduledTask -TaskName $TaskName
$registered = Get-ScheduledTask -TaskName $TaskName
$info = $registered | Get-ScheduledTaskInfo
[pscustomobject]@{
  taskName = $TaskName
  state = [string]$registered.State
  lastRunTime = $info.LastRunTime
  nextRunTime = $info.NextRunTime
  intervalMinutes = $IntervalMinutes
  supervisionMode = "CONTINUOUS_LOOP_WITH_SCHEDULED_RESTART"
  repairAuthority = "LOCAL_SYNC_OR_MISSING_OR_REVISION_STALE_ANALYZER_ONLY"
}

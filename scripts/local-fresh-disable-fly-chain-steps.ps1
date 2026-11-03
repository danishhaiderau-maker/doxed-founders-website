# HM S0 / RK (#972 REV5): turn OFF Fly-only ChainSupervisor steps 1–3 via flags.
# Leaves Scheduled Task DoxxedLaptopChainSupervisor ENABLED (analyzer / health / alerts stay).
#
# Steps gated (StateDir flag files):
#   1. Fly segment pull          -> segment-pull.disabled
#   2. fly_* status snapshots    -> fly-status-snapshots.disabled
#   3. auto-ff in Fly mode       -> v2c-auto-ff.disabled
#
# Usage (after R12/RK PASS — do NOT run from cloud agent on Danish laptop):
#   powershell -File scripts\local-fresh-disable-fly-chain-steps.ps1 -Apply
#   powershell -File scripts\local-fresh-disable-fly-chain-steps.ps1 -Rollback
#
# Writes receipt under StateDir for undo. Never disable or unregister the Scheduled Task.
param(
  [string]$StateDir = '',
  [switch]$Apply,
  [switch]$Rollback,
  [switch]$Status
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'laptop-chain-common.ps1')
$cfg = Get-LaptopChainConfig -StateDir $StateDir
$dir = $cfg.StateDir
if (-not (Test-Path -LiteralPath $dir)) {
  New-Item -ItemType Directory -Path $dir -Force | Out-Null
}

$flags = @(
  [ordered]@{ step = 1; name = 'segment-pull.disabled'; what = 'Fly segment pull' },
  [ordered]@{ step = 2; name = 'fly-status-snapshots.disabled'; what = 'fly_* status snapshots' },
  [ordered]@{ step = 3; name = 'v2c-auto-ff.disabled'; what = 'auto-ff in Fly mode' }
)
$receiptPath = Join-Path $dir 'local-fresh-fly-chain-steps.receipt.json'

function Get-FlagState {
  $rows = @()
  foreach ($f in $flags) {
    $path = Join-Path $dir $f.name
    $rows += [ordered]@{
      step = $f.step
      flag = $f.name
      what = $f.what
      path = $path
      present = [bool](Test-Path -LiteralPath $path)
    }
  }
  return $rows
}

if ($Status -or ((-not $Apply) -and (-not $Rollback))) {
  [pscustomobject]@{
    schema = 'local_fresh_fly_chain_steps_status_v1'
    state_dir = $dir
    task = 'DoxxedLaptopChainSupervisor'
    task_action = 'LEAVE_ENABLED'
    flags = @(Get-FlagState)
  } | ConvertTo-Json -Depth 5
  exit 0
}

if ($Apply -and $Rollback) {
  throw 'Use only one of -Apply or -Rollback'
}

$before = @(Get-FlagState)
if ($Apply) {
  foreach ($f in $flags) {
    $path = Join-Path $dir $f.name
    if (-not (Test-Path -LiteralPath $path)) {
      Set-Content -LiteralPath $path -Value ("disabled_by=local-fresh-disable-fly-chain-steps`nts_utc={0}`n" -f (Get-Date).ToUniversalTime().ToString('o')) -Encoding utf8
    }
  }
  $action = 'APPLY_DISABLE_FLY_STEPS'
} else {
  # Rollback: remove only flags this receipt recorded as created by Apply,
  # or if no receipt, remove flags that were absent before and are present now.
  # Prefer receipt previous_state: restore exact prior present/absent.
  $receipt = $null
  if (Test-Path -LiteralPath $receiptPath) {
    try { $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json } catch { $receipt = $null }
  }
  if ($receipt -and $receipt.previous_state) {
    foreach ($row in @($receipt.previous_state)) {
      $path = [string]$row.path
      if (-not $path) { $path = Join-Path $dir ([string]$row.flag) }
      $wasPresent = [bool]$row.present
      $nowPresent = Test-Path -LiteralPath $path
      if ($wasPresent -and -not $nowPresent) {
        # Was present before Apply — leave/create empty marker? Prior was "disabled already"; recreate empty file.
        Set-Content -LiteralPath $path -Value ("restored_previous_present=1`nts_utc={0}`n" -f (Get-Date).ToUniversalTime().ToString('o')) -Encoding utf8
      } elseif ((-not $wasPresent) -and $nowPresent) {
        Remove-Item -LiteralPath $path -Force
      }
    }
  } else {
    foreach ($f in $flags) {
      $path = Join-Path $dir $f.name
      if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
    }
  }
  $action = 'ROLLBACK_FLY_STEPS'
}

$after = @(Get-FlagState)
$receiptObj = [ordered]@{
  schema = 'local_fresh_fly_chain_steps_receipt_v1'
  ts_utc = (Get-Date).ToUniversalTime().ToString('o')
  action = $action
  state_dir = $dir
  task = 'DoxxedLaptopChainSupervisor'
  task_note = 'Task stays Enabled; only Fly-step flag files changed. This script never disables or unregisters the task.'
  previous_state = $before
  after_state = $after
  undo = 'powershell -File scripts\local-fresh-disable-fly-chain-steps.ps1 -Rollback'
}
($receiptObj | ConvertTo-Json -Depth 6) | Set-Content -LiteralPath $receiptPath -Encoding utf8
Write-Host "OK $action receipt=$receiptPath (ChainSupervisor task left Enabled)"
$receiptObj | ConvertTo-Json -Depth 6
exit 0

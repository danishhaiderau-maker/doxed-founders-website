# One supervised analyzer pass from this worktree against the canonical mirror.
#
# Exit codes (real, from WaitForExit):
#   0  pass completed and the dashboard reports a new completed generation
#   1  launcher/runner error (stderr captured in the run log)
#   3  another analyzer pass holds the run mutex
#   6  launcher exited 0 but no new completed generation was observed
#   124 pass exceeded -TimeoutMin and its own process tree was stopped
#   other non-zero: the launcher's own exit code (for example 2 = REFUSED)
param(
  [string]$RepoRoot = '',
  [string]$CanonicalRoot = '',
  [string]$StateDir = '',
  [int]$Port = 9001,
  [int]$TimeoutMin = 240,
  [string]$Reason = 'manual',
  [switch]$EnsureDashboardOnly
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'laptop-chain-common.ps1')
$cfg = Get-LaptopChainConfig -RepoRoot $RepoRoot -CanonicalRoot $CanonicalRoot -StateDir $StateDir
$logName = 'analyzer-run'
$launcher = Join-Path $cfg.RepoRoot 'scripts\start-home-analyzer.ps1'

function Get-AnalyzerStatus {
  try { return Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/status" -TimeoutSec 20 -UseBasicParsing } catch { return $null }
}

# The launcher resolves the canonical store relative to its own checkout. A
# worktree reaches the one canonical mirror through a junction, never a copy.
function Assert-CanonicalDataLink {
  if ($cfg.RepoRoot -eq $cfg.CanonicalRoot) { return }
  $link = Join-Path $cfg.RepoRoot 'services\btc-conservative-agent\canonical-research-data'
  if (Test-Path -LiteralPath $link) {
    $item = Get-Item -LiteralPath $link -Force
    $target = @($item.Target) | Select-Object -First 1
    if ($item.LinkType -ne 'Junction' -or -not $target -or
        [System.IO.Path]::GetFullPath([string]$target).TrimEnd('\') -ne [System.IO.Path]::GetFullPath($cfg.DataRoot).TrimEnd('\')) {
      throw "CANONICAL_DATA_LINK_INVALID: $link must be a junction to $($cfg.DataRoot)"
    }
    return
  }
  New-Item -ItemType Junction -Path $link -Target $cfg.DataRoot | Out-Null
}

function Invoke-Launcher([string[]]$LauncherArgs, [string]$Tag, [int]$TimeoutMs) {
  $stamp = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssZ')
  $stdoutPath = Join-Path $cfg.LogDir "analyzer-$Tag-$stamp.out.log"
  $stderrPath = Join-Path $cfg.LogDir "analyzer-$Tag-$stamp.err.log"
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = Join-Path $PSHOME 'powershell.exe'
  $psi.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $launcher + '" ' + ($LauncherArgs -join ' ')
  $psi.WorkingDirectory = $cfg.RepoRoot
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $psi.RedirectStandardInput = $true
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $process = [System.Diagnostics.Process]::Start($psi)
  $process.StandardInput.WriteLine('')
  $process.StandardInput.Close()
  # Drain both pipes concurrently so a chatty child can never deadlock.
  $stdoutTask = $process.StandardOutput.ReadToEndAsync()
  $stderrTask = $process.StandardError.ReadToEndAsync()
  $timedOut = -not $process.WaitForExit($TimeoutMs)
  if ($timedOut) {
    Invoke-NativeQuiet { & taskkill.exe /PID $process.Id /T /F } | Out-Null
  }
  $process.WaitForExit()
  [System.IO.File]::WriteAllText($stdoutPath, $stdoutTask.Result)
  [System.IO.File]::WriteAllText($stderrPath, $stderrTask.Result)
  $code = if ($timedOut) { 124 } else { $process.ExitCode }
  $tail = (($stderrTask.Result.Trim() -split "`r?`n") | Select-Object -Last 5) -join ' | '
  if (-not $tail) {
    $tail = (($stdoutTask.Result.Trim() -split "`r?`n") | Where-Object { $_ -match 'REFUSED|error|Error|FAILED' } | Select-Object -Last 3) -join ' | '
  }
  return [pscustomobject]@{ ExitCode = $code; TimedOut = $timedOut; Stdout = $stdoutPath; Stderr = $stderrPath; Tail = $tail }
}

$instance = Enter-SingleInstance -Name (Get-ChainMutexName 'LaptopAnalyzerRun')
if (-not $instance) {
  Write-ChainLog -Config $cfg -Name $logName -Message "ANALYZER_BUSY reason=$Reason exit=3"
  exit 3
}

$previous = Read-JsonFile $cfg.AnalyzerStatus
$status = [ordered]@{
  schema = 'laptop_analyzer_run_status_v1'
  pid = $PID
  reason = $Reason
  repoRoot = $cfg.RepoRoot
  revision = ((Invoke-NativeQuiet { & git -C $cfg.RepoRoot rev-parse HEAD }) | Select-Object -First 1)
  startedAt = Get-UtcNowIso
  finishedAt = $null
  state = 'RUNNING'
  exitCode = $null
  detail = $null
  researchModeFlag = $null
  researchModeMatchesFly = $null
  stdoutLog = $null
  stderrLog = $null
  lastSuccessAt = $(if ($previous) { $previous.lastSuccessAt } else { $null })
  lastCompletedGenerationAt = $(if ($previous) { $previous.lastCompletedGenerationAt } else { $null })
}
$exitCode = 1
try {
  Write-JsonAtomic -Path $cfg.AnalyzerStatus -Value $status
  $env:DOXXED_NONINTERACTIVE = '1'
  # A stale user-level value would point the analyzer at a retired store.
  Remove-Item -LiteralPath Env:BTC_AGENT_DATA_DIR -ErrorAction SilentlyContinue
  $health = $null
  if ($env:DOXXED_LAPTOP_CHAIN_OFFLINE -ne '1') {
    try { $health = Get-FlyHealth $cfg.SourceUrl; [void](Publish-UpstreamIdentity -Config $cfg -Health $health) } catch { }
  }
  $mode = Resolve-AnalyzerResearchMode -RepoRoot $cfg.RepoRoot -Health $health
  $env:SCORE_LED_PAPER_RESEARCH_ENABLED = $mode.Flag
  $status.researchModeFlag = $mode.Flag
  $status.researchModeMatchesFly = $mode.Matched
  Assert-CanonicalDataLink

  if (-not (Get-AnalyzerStatus)) {
    $dash = Invoke-Launcher -LauncherArgs @('-DashboardOnly', '-NoWait', "-Port $Port") -Tag 'dashboard' -TimeoutMs 120000
    Write-ChainLog -Config $cfg -Name $logName -Message ("DASHBOARD_START exit={0} {1}" -f $dash.ExitCode, $dash.Tail)
    if ($dash.ExitCode -ne 0) { throw "DASHBOARD_START_FAILED exit=$($dash.ExitCode) $($dash.Tail)" }
  }
  if ($EnsureDashboardOnly) {
    $status.state = 'DASHBOARD_ENSURED'
    $exitCode = 0
  } else {
    $before = Get-AnalyzerStatus
    $beforeCompleted = if ($before -and $before.analysis_run) { ConvertTo-UtcDate $before.analysis_run.last_completed_at } else { $null }
    Write-ChainLog -Config $cfg -Name $logName -Message ("ANALYZER_START reason={0} rev={1} scoreLed={2} flyMatch={3}" -f $Reason, $status.revision, $mode.Flag, $mode.Matched)
    $run = Invoke-Launcher -LauncherArgs @('-Once', "-Port $Port") -Tag 'once' -TimeoutMs ($TimeoutMin * 60 * 1000)
    $status.stdoutLog = $run.Stdout
    $status.stderrLog = $run.Stderr
    $exitCode = $run.ExitCode
    $status.detail = $run.Tail
    if ($exitCode -eq 0) {
      $after = Get-AnalyzerStatus
      $afterCompleted = if ($after -and $after.analysis_run) { ConvertTo-UtcDate $after.analysis_run.last_completed_at } else { $null }
      $phase = if ($after -and $after.analysis_run) { [string]$after.analysis_run.phase } else { 'UNAVAILABLE' }
      if ($afterCompleted -and $phase -ne 'FAILED' -and (-not $beforeCompleted -or $afterCompleted -gt $beforeCompleted)) {
        $status.lastSuccessAt = Get-UtcNowIso
        $status.lastCompletedGenerationAt = $afterCompleted.ToString('o')
      } else {
        $exitCode = 6
        $status.detail = "NO_NEW_COMPLETED_GENERATION phase=$phase $($run.Tail)"
      }
    }
    $status.state = $(if ($exitCode -eq 0) { 'COMPLETED' } elseif ($exitCode -eq 124) { 'TIMEOUT' } else { 'FAILED' })
  }
} catch {
  $exitCode = 1
  $status.state = 'FAILED'
  $status.detail = [string]$_.Exception.Message
} finally {
  $status.exitCode = $exitCode
  $status.finishedAt = Get-UtcNowIso
  Write-JsonAtomic -Path $cfg.AnalyzerStatus -Value $status
  Write-ChainLog -Config $cfg -Name $logName -Message ("ANALYZER_EXIT code={0} state={1} reason={2} detail={3}" -f $exitCode, $status.state, $Reason, $status.detail)
  Exit-SingleInstance $instance
}
exit $exitCode

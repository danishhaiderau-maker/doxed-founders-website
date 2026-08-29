param(
  [string]$SourceDir = "",
  [string]$TargetDir = "",
  [string]$Heartbeat = ""
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "fly-data-paths.ps1")

if (-not $SourceDir) {
  # Explicit legacy source only. It is never selected by operational launchers.
  $legacyBase = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { [System.IO.Path]::GetTempPath() }
  $SourceDir = Join-Path $legacyBase "DoxxedCrypto\fly-data-mirror"
}
if (-not $TargetDir) { $TargetDir = Get-DoxxedFlyMirrorDir }
if (-not $Heartbeat) {
  $legacyHeartbeat = Join-Path $repoRoot ".fly-data-sync-loop.heartbeat.json"
  $Heartbeat = $legacyHeartbeat
}
$source = [System.IO.Path]::GetFullPath($SourceDir)
$target = [System.IO.Path]::GetFullPath($TargetDir)

if (-not (Test-Path -LiteralPath $source -PathType Container)) {
  throw "Legacy mirror does not exist: $source"
}
if ($source -eq $target) { throw "Source and target are the same directory." }
if ($target.StartsWith($source + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase) -or
    $source.StartsWith($target + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Source and target directories must not contain one another."
}
if ($env:OneDrive -and $target.StartsWith([System.IO.Path]::GetFullPath($env:OneDrive), [System.StringComparison]::OrdinalIgnoreCase)) {
  throw "Target is inside OneDrive; choose non-synced machine-local storage."
}

$syncPidFile = Join-Path $repoRoot ".fly-data-sync-loop.lock"
if (Test-Path -LiteralPath $syncPidFile) {
  try {
    $syncPid = [int](Get-Content -LiteralPath $syncPidFile -Raw)
    if (Get-Process -Id $syncPid -ErrorAction SilentlyContinue) {
      throw "Fly sync is running (PID $syncPid). Stop desktop tools before migration so the snapshot cannot change during verification."
    }
  } catch [System.Management.Automation.RuntimeException] { throw }
    catch { }
}

if (-not (Test-Path -LiteralPath $Heartbeat -PathType Leaf)) {
  throw "Completed Fly sync heartbeat does not exist: $Heartbeat"
}
$migration = Join-Path $repoRoot "scripts\migrate_canonical_research_store.py"
& python $migration --source $source --destination $target --heartbeat $Heartbeat
if ($LASTEXITCODE -ne 0) { throw "Verified canonical migration failed with exit code $LASTEXITCODE." }


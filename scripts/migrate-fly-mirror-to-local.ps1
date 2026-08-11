param(
  [string]$SourceDir = "",
  [string]$TargetDir = ""
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
. (Join-Path $scriptDir "fly-data-paths.ps1")

if (-not $SourceDir) {
  $SourceDir = Join-Path $repoRoot "services\btc-conservative-agent\fly-data-mirror"
}
if (-not $TargetDir) { $TargetDir = Get-DoxxedFlyMirrorDir }
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

New-Item -ItemType Directory -Path $target -Force | Out-Null
$sourceFiles = @(Get-ChildItem -LiteralPath $source -Recurse -File -Force)
$copiedBytes = [int64]0
foreach ($file in $sourceFiles) {
  $relative = $file.FullName.Substring($source.Length).TrimStart('\')
  $destination = Join-Path $target $relative
  New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
  Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
  $sourceHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
  $targetHash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash
  if ($sourceHash -ne $targetHash) { throw "Verification failed for $relative" }
  $copiedBytes += $file.Length
}

$targetFiles = @(Get-ChildItem -LiteralPath $target -Recurse -File -Force)
if ($targetFiles.Count -ne $sourceFiles.Count) {
  throw "Verification failed: source has $($sourceFiles.Count) files but target has $($targetFiles.Count)."
}

[pscustomobject]@{
  Status = "verified_copy_complete"
  Source = $source
  Target = $target
  Files = $sourceFiles.Count
  Bytes = $copiedBytes
  SourceRetained = $true
  NextStep = "Restart desktop tools; they now default to Target. Keep Source until a fresh sync and analyzer pass are verified."
}


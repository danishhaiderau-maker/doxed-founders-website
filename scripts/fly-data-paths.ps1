# Machine-local paths used by the read-only Fly mirror and analyzer.
# Raw downloaded evidence must not default to the repository because this
# checkout may live in OneDrive. Reports remain in the repository and can be
# versioned/exported independently from the potentially large raw mirror.
function Get-DoxxedFlyMirrorDir {
  param([string]$RequestedPath = "")

  $candidate = $RequestedPath
  if (-not $candidate) {
    $candidate = [Environment]::GetEnvironmentVariable(
      "DOXXED_FLY_MIRROR_DIR",
      [EnvironmentVariableTarget]::Process
    )
  }
  if (-not $candidate) {
    $base = if ($env:LOCALAPPDATA) {
      $env:LOCALAPPDATA
    } else {
      [System.IO.Path]::GetTempPath()
    }
    $candidate = Join-Path $base "DoxxedCrypto\fly-data-mirror"
  }
  return [System.IO.Path]::GetFullPath($candidate)
}


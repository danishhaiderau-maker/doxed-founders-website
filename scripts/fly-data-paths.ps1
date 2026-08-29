# One repo-contained desktop store is selected by the Fly synchronizer and the
# analyzer. Fly's durable /app/data volume remains authoritative; this folder
# is a read-only, verified derivative and is never uploaded as raw evidence.
function Get-DoxxedFlyMirrorDir {
  param([string]$RequestedPath = "")

  $scriptRoot = Split-Path -Parent $PSScriptRoot
  $canonical = [System.IO.Path]::GetFullPath(
    (Join-Path $scriptRoot "services\btc-conservative-agent\canonical-research-data")
  ).TrimEnd('\')
  $candidate = if ($RequestedPath) { $RequestedPath } else {
    [Environment]::GetEnvironmentVariable("DOXXED_FLY_MIRROR_DIR", [EnvironmentVariableTarget]::Process)
  }
  if (-not $candidate) { $candidate = $canonical }
  $resolved = [System.IO.Path]::GetFullPath($candidate).TrimEnd('\')
  if ($resolved -ne $canonical) {
    throw "DOXXED_FLY_MIRROR_DIR must select the repo-contained canonical store: $canonical"
  }
  if ($resolved.ToLowerInvariant().Contains('\onedrive\')) {
    throw "Canonical research data must never use OneDrive: $resolved"
  }
  return $resolved
}


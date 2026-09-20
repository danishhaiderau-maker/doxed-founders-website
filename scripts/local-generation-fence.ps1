function Get-LocalGenerationFence {
  param([Parameter(Mandatory = $true)][string]$DataRoot)
  $root = [IO.Path]::GetFullPath($DataRoot)
  $path = Join-Path $root '.local-generation-fence.json'
  if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
  try {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $item.Length -gt 1MB) {
      throw 'invalid fence file'
    }
    $value = Get-Content -LiteralPath $path -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
    if ([string]$value.schema -cne 'local_research_generation_fence_v1' -or
        [string]$value.state -cne 'BLOCKED_PENDING_VERIFIED_IMPORT' -or
        [string]::IsNullOrWhiteSpace([string]$value.operation_id) -or
        [string]::IsNullOrWhiteSpace([string]$value.local_generation)) {
      throw 'invalid fence body'
    }
    return $value
  } catch {
    throw 'LOCAL_GENERATION_FENCE_INVALID'
  }
}

function Assert-LocalGenerationUnfenced {
  param(
    [Parameter(Mandatory = $true)][string]$DataRoot,
    [Parameter(Mandatory = $true)][string]$Stage
  )
  $fence = Get-LocalGenerationFence -DataRoot $DataRoot
  if ($null -ne $fence) {
    throw "LOCAL_GENERATION_FENCED:${Stage}:$([string]$fence.operation_id)"
  }
}

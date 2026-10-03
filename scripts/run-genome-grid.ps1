# Refreshes the full-history policy genome grid that backs the :9001 /details
# "Top 100 policy combos" panel (research_dashboard /api/genome-grid).
# Run every 2 hours by the DoxxedGenomeGrid task. Laptop-only research: reads
# the mirror and Tier A tape, writes C:\DoxxedCrypto\analyzer-exports\genome-grid,
# never touches Fly, trading, the relay or Bitfinex. Runs the code the analyzer
# runs (C:\DoxxedCrypto\v2c follows master), at below-normal priority, and the
# study itself skips while the segment analyzer is in its ANALYZER phase.
param(
  [string]$RepoRoot = 'C:\DoxxedCrypto\v2c',
  [string]$OutDir = 'C:\DoxxedCrypto\analyzer-exports\genome-grid',
  [string]$Python = 'python',
  [int]$Workers = 2
)

$ErrorActionPreference = 'Stop'
foreach ($path in @($RepoRoot, $OutDir, $PSScriptRoot)) {
  if ($path.ToLowerInvariant().Contains('\onedrive\')) { throw "Refusing a OneDrive path: $path" }
}
$service = Join-Path $RepoRoot 'services\btc-conservative-agent'
$study = Join-Path $service 'research\genome_grid_study.py'
if (-not (Test-Path -LiteralPath $study)) { throw "genome_grid_study.py not found under $RepoRoot" }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$log = Join-Path $OutDir ('run-{0}.log' -f [datetime]::UtcNow.ToString('yyyyMMdd'))
$lock = Join-Path $OutDir 'run.lock'
try {
  $handle = [System.IO.File]::Open($lock, 'OpenOrCreate', 'ReadWrite', 'None')
} catch {
  Add-Content -LiteralPath $log -Encoding UTF8 -Value ('{0} pid={1} SKIP another genome grid run holds {2}' -f [datetime]::UtcNow.ToString('o'), $PID, $lock)
  exit 0
}
try {
  Remove-Item -LiteralPath Env:BTC_AGENT_DATA_DIR -ErrorAction SilentlyContinue
  $started = [datetime]::UtcNow
  Push-Location $service
  try {
    $out = & $Python $study --workers $Workers --out-dir $OutDir 2>&1 | Out-String
    $code = $LASTEXITCODE
  } finally { Pop-Location }
  $tail = ($out.Trim() -split "`r?`n" | Select-Object -Last 3) -join ' | '
  Add-Content -LiteralPath $log -Encoding UTF8 -Value ('{0} pid={1} exit={2} sec={3:n0} {4}' -f [datetime]::UtcNow.ToString('o'), $PID, $code, ([datetime]::UtcNow - $started).TotalSeconds, $tail)
  # Indicator Edge forward scorer rides the same cadence; its outcome never changes this task's exit code.
  $edge = Join-Path $service 'research\indicator_edge_cycle.py'
  if (Test-Path -LiteralPath $edge) {
    $edgeStarted = [datetime]::UtcNow
    Push-Location $service
    try {
      $edgeOut = & $Python $edge 2>&1 | Out-String
      $edgeCode = $LASTEXITCODE
    } catch {
      $edgeOut = $_.Exception.Message
      $edgeCode = -1
    } finally { Pop-Location }
    $edgeTail = ($edgeOut.Trim() -split "`r?`n" | Select-Object -Last 2) -join ' | '
    Add-Content -LiteralPath $log -Encoding UTF8 -Value ('{0} pid={1} indicator_edge exit={2} sec={3:n0} {4}' -f [datetime]::UtcNow.ToString('o'), $PID, $edgeCode, ([datetime]::UtcNow - $edgeStarted).TotalSeconds, $edgeTail)
  }
  exit $code
} finally {
  $handle.Dispose()
}
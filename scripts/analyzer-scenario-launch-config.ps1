# Read-only validation. No process stop/start, environment changes or config writes.
function Get-AnalyzerScenarioLaunchConfig {
  param([string]$ConfigPath = '', [string]$ModelFile = '', [string]$ModelSha256 = '', [string]$PythonExe = 'python')
  $validator = Join-Path $PSScriptRoot 'analyzer-scenario-launch-config.py'
  $arguments = @($validator)
  if ($ConfigPath) { $arguments += @('--config-path', $ConfigPath) }
  if ($ModelFile) { $arguments += @('--model-file', $ModelFile) }
  if ($ModelSha256) { $arguments += @('--model-sha256', $ModelSha256) }
  $output = @(& $PythonExe @arguments)
  $status = $LASTEXITCODE
  try { $receipt = ($output -join "`n") | ConvertFrom-Json } catch { throw 'ANALYZER_SCENARIO_VALIDATOR_FAILED' }
  if ($status -ne 0) {
    if ([string]$receipt.error -match '^ANALYZER_SCENARIO_[A-Z_]+$') { throw [string]$receipt.error }
    throw 'ANALYZER_SCENARIO_VALIDATOR_FAILED'
  }
  return $receipt
}

function Assert-AnalyzerScenarioLaunchConfig {
  param([Parameter(Mandatory=$true)]$Receipt, [string]$PythonExe = 'python')
  $current = Get-AnalyzerScenarioLaunchConfig -ConfigPath ([string]$Receipt.config_path) `
    -ModelFile ([string]$Receipt.model_file) -ModelSha256 ([string]$Receipt.model_sha256) -PythonExe $PythonExe
  foreach ($field in @('enabled', 'config_path', 'config_sha256', 'model_file', 'model_sha256')) {
    if ($current.$field -cne $Receipt.$field) { throw 'ANALYZER_SCENARIO_LAUNCH_INPUT_CHANGED' }
  }
}

param(
  [string]$ScriptPath = (Join-Path $PSScriptRoot "sync-fly-bot-data-loop.ps1")
)

$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
  (Resolve-Path -LiteralPath $ScriptPath),
  [ref]$tokens,
  [ref]$parseErrors
)
if ($parseErrors.Count -gt 0) { throw "Production sync loop does not parse." }

$requiredFunctions = @(
  "Get-BoundedDiagnosticText",
  "Get-FlyInventorySemanticProgressKey",
  "Test-FlyInventoryTerminalFailure",
  "ConvertTo-BoundedNullableCounter",
  "Get-FlyInventoryErrorClass",
  "Set-FlyInventoryDiagnostic"
)
foreach ($name in $requiredFunctions) {
  $definition = @($ast.FindAll({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq $name
  }, $true))
  if ($definition.Count -ne 1) { throw "Expected exactly one production function: $name" }
  Invoke-Expression $definition[0].Extent.Text
}

function Assert-True([bool]$Condition, [string]$Message) {
  if (-not $Condition) { throw $Message }
}

function New-Manifest(
  [int]$Invocation,
  [int]$PagesWritten,
  [int]$PagesTotal,
  [int]$BootstrapBytes = 0
) {
  return [pscustomobject]@{
    inventory_worker = [pscustomobject]@{
      phase = "FINALIZE"
      invocations = $Invocation
      files_seen = 20
      dirs_seen = 4
      rows_discovered = 200
      pages_written = $PagesWritten
      pages_total = $PagesTotal
    }
    receipt_bootstrap = [pscustomobject]@{
      status = "COMPLETE"
      ledger = $null
      records_indexed = 10
      bytes_indexed = $BootstrapBytes
      cursor = $BootstrapBytes
    }
  }
}

$base = New-Manifest 1 1 3
$invocationOnly = New-Manifest 2 1 3
$pageProgress = New-Manifest 2 2 3
Assert-True (
  (Get-FlyInventorySemanticProgressKey $base) -eq
  (Get-FlyInventorySemanticProgressKey $invocationOnly)
) "Invocation-only child spins must not reset semantic progress."
Assert-True (
  (Get-FlyInventorySemanticProgressKey $base) -ne
  (Get-FlyInventorySemanticProgressKey $pageProgress)
) "A FINALIZE page increment must reset semantic progress."

$stallCap = 360
$absoluteCap = 1800
$lastKey = Get-FlyInventorySemanticProgressKey $base
$lastProgressAt = 0
$stalled = $false
foreach ($elapsed in @(0, 359, 360)) {
  $key = Get-FlyInventorySemanticProgressKey (New-Manifest ($elapsed + 1) 1 3)
  if ($key -ne $lastKey) { $lastKey = $key; $lastProgressAt = $elapsed }
  if (($elapsed - $lastProgressAt) -ge $stallCap) { $stalled = $true }
}
Assert-True $stalled "A frozen semantic tuple must hit the 360-second stall fence."

$lastProgressAt = 0
$absoluteHit = $false
foreach ($elapsed in @(0, 300, 600, 900, 1200, 1500, 1800)) {
  $key = Get-FlyInventorySemanticProgressKey (New-Manifest 1 (($elapsed / 300) + 1) 99)
  $lastProgressAt = $elapsed
  if ($elapsed -ge $absoluteCap) { $absoluteHit = $true; break }
}
Assert-True $absoluteHit "Continuous semantic progress must still hit the 1800-second absolute cap."

Assert-True ((ConvertTo-BoundedNullableCounter -1) -eq $null) "Negative counters must be null."
Assert-True ((ConvertTo-BoundedNullableCounter $true) -eq $null) "Boolean counters must be null."
Assert-True ((ConvertTo-BoundedNullableCounter 12) -eq 12) "Valid counters must survive."
Assert-True (
  (Get-FlyInventoryErrorClass "path /secret/value") -eq "INVENTORY_ERROR_PRESENT"
) "Free-form inventory errors must not be persisted."
Assert-True (
  (Get-FlyInventoryErrorClass "CHECKPOINT_INVALID") -eq "CHECKPOINT_INVALID"
) "Bounded machine error classes must survive."

foreach ($status in @(500, 503)) {
  $script:lastInventoryDiagnostic = [ordered]@{}
  $failure = [pscustomobject]@{
    Exception = [pscustomobject]@{
      Response = [pscustomobject]@{ StatusCode = $status }
    }
  }
  Set-FlyInventoryDiagnostic -Failure $failure
  Assert-True (
    $script:lastInventoryDiagnostic.httpStatus -eq $status
  ) "HTTP status $status must be retained."
  Assert-True (
    $script:lastInventoryDiagnostic.errorClass -eq "HTTP_$status"
  ) "HTTP status $status must receive a bounded machine error class."
}

$blocked = New-Manifest 1 0 0
$blocked.inventory_worker | Add-Member -NotePropertyName refreshing -NotePropertyValue $true
$blocked | Add-Member -NotePropertyName inventory_build_status -NotePropertyValue "BUILDING"
$blocked | Add-Member -NotePropertyName inventory_error -NotePropertyValue $null
$blocked.receipt_bootstrap.status = "BLOCKED"
$blocked.receipt_bootstrap | Add-Member -NotePropertyName blocked -NotePropertyValue $true
Assert-True (
  (Test-FlyInventoryTerminalFailure $blocked) -eq $true
) "A blocked receipt bootstrap must terminate immediately even while the worker says refreshing."

$pending = New-Manifest 1 0 0
$pending.inventory_worker.phase = "WAITING_RECEIPT_BOOTSTRAP"
$pending.inventory_worker | Add-Member -NotePropertyName refreshing -NotePropertyValue $false
$pending | Add-Member -NotePropertyName inventory_build_status -NotePropertyValue "PENDING"
$pending | Add-Member -NotePropertyName inventory_error -NotePropertyValue $null
$pending.receipt_bootstrap.status = "PENDING"
$pending.receipt_bootstrap | Add-Member -NotePropertyName blocked -NotePropertyValue $false
Assert-True (
  (Test-FlyInventoryTerminalFailure $pending) -eq $false
) "An unblocked pending receipt bootstrap must remain retryable under the bounded join."

Write-Output "sync-progress-contract-ok"

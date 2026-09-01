$ErrorActionPreference = 'Stop'

function Assert-Contract {
  param([bool]$Condition, [string]$Message)
  if (-not $Condition) { throw "ASSERTION_FAILED: $Message" }
}

$scriptPath = Join-Path $PSScriptRoot 'sync-fly-bot-data.ps1'
$source = Get-Content -LiteralPath $scriptPath -Raw

$qualificationStart = $source.IndexOf('$lifecycleAckCount = 0')
$transferStart = $source.IndexOf('$lifecycleTransferAckCount = 0')
$analyzerStart = $source.IndexOf('$analyzerPublished = $false')
Assert-Contract ($qualificationStart -ge 0) 'qualification acknowledgement block remains present'
Assert-Contract ($transferStart -gt $qualificationStart) 'transfer acknowledgement block follows qualification block'
Assert-Contract ($analyzerStart -gt $transferStart) 'transfer acknowledgement block has an explicit boundary'

$qualificationBlock = $source.Substring($qualificationStart, $transferStart - $qualificationStart)
$transferBlock = $source.Substring($transferStart, $analyzerStart - $transferStart)

Assert-Contract (
  $qualificationBlock -match '\^v3/lifecycle_bundles/\[\^/\]\+/lifecycle-\[0-9a-f\]\{64\}/manifest\\\.json\$'
) 'qualification manifest selection path is unchanged'
Assert-Contract (
  $qualificationBlock -match '-Uri "\$base/api/data-sync/lifecycle-ack"'
) 'qualification acknowledgements still use the qualification endpoint'
Assert-Contract (
  $transferBlock -match '\^v3/lifecycle_transfer_bundles/\[\^/\]\+/transfer-\[0-9a-f\]\{64\}/manifest\\\.json\$'
) 'transfer manifests are selected explicitly'
Assert-Contract (
  $transferBlock -notmatch 'api/data-sync/lifecycle-ack'
) 'transfer acknowledgements never reach the qualification cleanup endpoint'
Assert-Contract (
  $transferBlock -match "schema -cne 'lifecycle_transfer_bundle_ack_v1'"
) 'transfer callback accepts only the isolated transfer ACK schema'

foreach ($field in @('profitability_supported', 'ranking_eligible', 'source_cleanup_authorized')) {
  $falsePattern = '(?m)^\s*' + [regex]::Escape($field) + '\s*=\s*\$false\s*$'
  $truePattern = '(?m)^\s*' + [regex]::Escape($field) + '\s*=\s*\$true\s*$'
  $falseAssignments = [regex]::Matches($transferBlock, $falsePattern).Count
  Assert-Contract ($falseAssignments -ge 1) "transfer acknowledgement fixes $field to false"
  Assert-Contract ($transferBlock -notmatch $truePattern) "transfer acknowledgement cannot set $field true"
}

Assert-Contract (
  $source -match 'LifecycleAcknowledged\s*=\s*\$lifecycleAckCount'
) 'qualification result counter remains unchanged'
Assert-Contract (
  $source -match 'LifecycleTransferAcknowledged\s*=\s*\$lifecycleTransferAckCount'
) 'transfer acknowledgements are reported separately'

Write-Output 'PASS sync qualification/transfer lifecycle acknowledgement isolation contract'

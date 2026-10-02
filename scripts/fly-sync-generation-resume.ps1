# Explicit bounded recovery, not a perpetual polling loop. Dot-source to use.
function Test-FlyResumeRevision {
  param([string]$Expected, [string]$Observed)
  return ($Expected -cmatch '^[0-9a-f]{40}$' -and $Observed -cmatch '^[0-9a-f]{12,40}$' -and
    $Expected.StartsWith($Observed, [StringComparison]::Ordinal))
}

function Test-FlyResumeIntegral {
  param([object]$Value)
  return ($null -ne $Value -and (
      $Value -is [byte] -or $Value -is [sbyte] -or
      $Value -is [int16] -or $Value -is [uint16] -or
      $Value -is [int] -or $Value -is [uint32] -or
      $Value -is [long] -or $Value -is [uint64]
    ))
}

function Test-FlyResumeCount {
  param([object]$Value, [long]$Expected)
  if (-not (Test-FlyResumeIntegral -Value $Value)) { return $false }
  try { return ([long]$Value -eq $Expected) }
  catch { return $false }
}

function Test-FlyResumeTerminalAck {
  param(
    [Parameter(Mandatory)][object]$Identity,
    [Parameter(Mandatory)][object]$Result,
    [Parameter(Mandatory)][object]$Manifest
  )
  if (
    -not (Test-FlyResumeIntegral -Value $Manifest.file_count) -or
    -not (Test-FlyResumeIntegral -Value $Manifest.total_bytes) -or
    [long]$Manifest.file_count -le 0 -or
    [long]$Manifest.total_bytes -lt 0
  ) { return $false }
  $expectedCount = [long]$Manifest.file_count
  $expectedBytes = [long]$Manifest.total_bytes
  if (
    $Result.AckAccepted -isnot [bool] -or $Result.AckAccepted -ne $true -or
    $Result.AckFinalized -isnot [bool] -or $Result.AckFinalized -ne $true -or
    $Result.AckCoverageComplete -isnot [bool] -or $Result.AckCoverageComplete -ne $true -or
    $Result.AckManifestPagesComplete -isnot [bool] -or $Result.AckManifestPagesComplete -ne $true -or
    [string]$Result.AckOperation -cne 'FINALIZE' -or
    [string]$Result.AckInventoryStatus -cne 'VALIDATED' -or
    [string]$Result.AckSessionId -cnotmatch '^[0-9a-f]{32}$'
  ) { return $false }
  if (
    -not (Test-FlyResumeCount -Value $Result.AckExpectedCount -Expected $expectedCount) -or
    -not (Test-FlyResumeCount -Value $Result.AckAcceptedCount -Expected $expectedCount) -or
    -not (Test-FlyResumeCount -Value $Result.AckInventoryFileCount -Expected $expectedCount) -or
    -not (Test-FlyResumeCount -Value $Result.AckRejectedCount -Expected 0) -or
    -not (Test-FlyResumeCount -Value $Result.AckManifestFileCount -Expected $expectedCount) -or
    -not (Test-FlyResumeCount -Value $Result.AckManifestTotalBytes -Expected $expectedBytes) -or
    -not (Test-FlyResumeCount -Value $Result.AckLocalContentFileCount -Expected $expectedCount) -or
    -not (Test-FlyResumeCount -Value $Result.AckLocalContentTotalBytes -Expected $expectedBytes) -or
    [string]$Result.AckLocalContentDigestSha256 -cnotmatch '^[0-9a-f]{64}$' -or
    [string]$Result.AckMembershipReceiptSha256 -cnotmatch '^[0-9a-f]{64}$' -or
    [string]$Result.AckMembershipReceiptSchema -cne 'fly_terminal_transfer_membership_receipt_v1' -or
    [string]$Result.AckMembershipContentHashStatus -cne 'LOCAL_COMPLETE_FRESH_RECOMPUTED' -or
    [string]$Result.AckMembershipReceiptName -cne (
      "terminal-transfer-membership-$([string]$Identity.inventory_generation_id)-$([string]$Result.AckSessionId).json"
    )
  ) { return $false }
  if (
    [string]$Result.InventoryGenerationId -cne [string]$Identity.inventory_generation_id -or
    [string]$Result.InventorySha256 -cne [string]$Identity.inventory_sha256 -or
    [string]$Result.CollectionEpochId -cne [string]$Identity.collection_epoch_id -or
    [string]$Result.TileRegistrySignature -cne [string]$Identity.tile_registry_signature -or
    [string]$Result.CanonicalSourceRevision -cne [string]$Identity.source_git_rev -or
    -not (Test-FlyResumeRevision $Identity.source_git_rev $Result.SourceRevision)
  ) { return $false }
  return $true
}

function Get-FlyResumeNonNegativeInt64 {
  param([object]$Value)
  try {
    $number = [long]$Value
    if ($number -ge 0) { return $number }
  } catch {}
  return [long]0
}

function Get-FlyResumeRetryableDeadlineCode {
  param([string]$Text)
  if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
  $match = [regex]::Match(
    $Text,
    '\b(BUNDLE_TRANSFER_DEADLINE|BUNDLE_INDEX_PREPARATION_DEADLINE)\b',
    [System.Text.RegularExpressions.RegexOptions]::CultureInvariant
  )
  if ($match.Success) { return [string]$match.Groups[1].Value }
  return $null
}

function Test-FlyResumePreviousProgressIdentity {
  param(
    [object]$Previous,
    [Parameter(Mandatory)][object]$Identity,
    [object]$Manifest = $null
  )
  if ($null -eq $Previous) { return $false }
  if (
    $Previous.ok -ne $false -or
    $Previous.inProgress -ne $false -or
    $Previous.ackPending -ne $true -or
    [string]$Previous.completionAuthority -cne 'NONE_TRANSFER_PROGRESS_ONLY' -or
    [string]$Previous.inventoryGenerationId -cne [string]$Identity.inventory_generation_id -or
    [string]$Previous.inventorySha256 -cne [string]$Identity.inventory_sha256 -or
    [string]$Previous.collectionEpochId -cne [string]$Identity.collection_epoch_id -or
    [string]$Previous.tileRegistrySignature -cne [string]$Identity.tile_registry_signature -or
    [string]$Previous.sourceRevision -cne [string]$Identity.source_git_rev -or
    -not (Test-FlyResumeRevision $Identity.source_git_rev $Previous.deployedRevision)
  ) { return $false }
  if ($null -ne $Manifest -and -not [string]::IsNullOrWhiteSpace([string]$Manifest.source_git_rev) -and
      [string]$Previous.deployedRevision -cne [string]$Manifest.source_git_rev) { return $false }
  return $true
}

function New-FlyGenerationResumeFailureReceipt {
  param(
    [Parameter(Mandatory)][object]$ErrorRecord,
    [Parameter(Mandatory)][object]$Identity,
    [object]$Manifest = $null,
    [string]$PreviousReceiptPath = '',
    [int]$Attempt = 0
  )
  $message = if ($ErrorRecord.Exception) { [string]$ErrorRecord.Exception.Message } else { [string]$ErrorRecord }
  $originalLength = $message.Length
  $excerpt = if ($message.Length -gt 4096) { $message.Substring(0, 4096) } else { $message }
  $stageMatch = [regex]::Match($excerpt, '(?i)\bstage=([a-z][a-z0-9_]{0,95})')
  $stage = if ($stageMatch.Success) { $stageMatch.Groups[1].Value.ToLowerInvariant() } else { 'UNKNOWN' }
  $statusMatch = [regex]::Match(
    $excerpt,
    '(?i)(?:\bHTTP\b|\bstatus\s+code\b|\bsuccess\s*:\s*)(?:[^0-9]{0,32})([1-5][0-9]{2})\b'
  )
  $httpStatus = if ($statusMatch.Success) { [int]$statusMatch.Groups[1].Value } else { $null }
  $isAckStage = $stage -cmatch '^acknowledgement_(?:page_[0-9]+|finalize)$'
  $serverClass = 'NOT_APPLICABLE'
  if ($isAckStage -and $httpStatus -eq 409) {
    # This is a bounded classification of the current terminal receipt, not a
    # claim that any historical 409 cause has been reproduced or repaired.
    $lower = $excerpt.ToLowerInvariant()
    $serverClass = if ($lower -match 'identity mismatch|server_class=identity_mismatch') { 'IDENTITY_MISMATCH' }
      elseif ($lower -match 'generation metadata mismatch|server_class=generation_metadata_mismatch') { 'GENERATION_METADATA_MISMATCH' }
      elseif ($lower -match 'page hash mismatch|server_class=page_hash_mismatch') { 'PAGE_HASH_MISMATCH' }
      elseif ($lower -match 'page validation failed|server_class=page_validation_failed') { 'PAGE_VALIDATION_FAILED' }
      elseif ($lower -match 'page does not cover|server_class=page_coverage_mismatch') { 'PAGE_COVERAGE_MISMATCH' }
      elseif ($lower -match 'page contains generation mismatches|server_class=page_generation_mismatch') { 'PAGE_GENERATION_MISMATCH' }
      elseif ($lower -match 'conflicting acknowledgement page|server_class=page_conflict') { 'PAGE_CONFLICT' }
      elseif ($lower -match 'page set is incomplete|server_class=page_set_incomplete') { 'PAGE_SET_INCOMPLETE' }
      elseif ($lower -match 'page totals are incomplete|server_class=page_totals_incomplete') { 'PAGE_TOTALS_INCOMPLETE' }
      elseif ($lower -match 'validated disk inventory generation is unavailable|server_class=generation_unavailable') { 'GENERATION_UNAVAILABLE' }
      else { 'UNCLASSIFIED' }
  }
  $failureCode = if ($isAckStage -and $httpStatus -eq 409) {
    "ACK_HTTP_409_$serverClass"
  } elseif ($isAckStage) {
    'ACK_REQUEST_FAILED'
  } else {
    'SYNC_ATTEMPT_FAILED'
  }
  $sha256 = [System.Security.Cryptography.SHA256]::Create()
  try {
    $excerptDigest = -join ($sha256.ComputeHash([System.Text.Encoding]::UTF8.GetBytes($excerpt)) |
      ForEach-Object { $_.ToString('x2') })
  } finally {
    $sha256.Dispose()
  }
  $previous = $null
  if ($PreviousReceiptPath -and (Test-Path -LiteralPath $PreviousReceiptPath -PathType Leaf)) {
    try { $previous = Get-Content -LiteralPath $PreviousReceiptPath -Raw | ConvertFrom-Json }
    catch { $previous = $null }
  }
  $observedRevision = if ($null -ne $Manifest) { [string]$Manifest.source_git_rev } else { '' }
  $trustedPrevious = if (Test-FlyResumePreviousProgressIdentity -Previous $previous -Identity $Identity -Manifest $Manifest) {
    $previous
  } else {
    $null
  }
  # A bundle deadline is an incomplete transfer boundary, not a terminal ACK
  # outcome. Prefer an explicit caught code, then a same-identity heartbeat
  # emitted by the bundle client. Never inherit retryability from stale or
  # foreign receipt bytes.
  $deadlineCodeFromError = Get-FlyResumeRetryableDeadlineCode -Text $message
  $explicitNonDeadlineCode = @(
    [regex]::Matches($excerpt, '\b[A-Z][A-Z0-9_]{2,95}\b') |
      ForEach-Object { [string]$_.Value } |
      Where-Object {
        $_ -cnotin @(
          'HTTP', 'HTTPS', 'GET', 'POST', 'PUT', 'PATCH', 'DELETE',
          'JSON', 'URL', 'URI', 'API',
          'BUNDLE_TRANSFER_DEADLINE', 'BUNDLE_INDEX_PREPARATION_DEADLINE',
          'BUNDLE_TRANSFER_FAILED'
        )
      }
  ) | Select-Object -First 1
  # A current HTTP/ACK/code failure is stronger evidence than an old progress
  # heartbeat. Do not turn a real present failure into a retryable deadline
  # merely because the preceding attempt ended at a valid deadline boundary.
  $currentExplicitNonDeadline = ($null -ne $httpStatus -or $isAckStage -or $null -ne $explicitNonDeadlineCode)
  $deadlineCode = $null
  $deadlineOrigin = 'NONE'
  if (-not $currentExplicitNonDeadline -and $deadlineCodeFromError) {
    $deadlineCode = $deadlineCodeFromError
    $deadlineOrigin = 'ERROR'
  }
  if (-not $currentExplicitNonDeadline -and -not $deadlineCode -and $null -ne $trustedPrevious) {
    $deadlineCode = Get-FlyResumeRetryableDeadlineCode -Text ([string]$trustedPrevious.failureCode)
    if ($deadlineCode) { $deadlineOrigin = 'TRUSTED_HEARTBEAT' }
  }
  $retryableDeadline = -not [string]::IsNullOrWhiteSpace($deadlineCode)
  return [ordered]@{
    schema = 'fly_generation_resume_failure_v1'
    ok = $false
    inProgress = $false
    phase = $(if ($retryableDeadline) { 'retryable_deadline' } else { 'terminal_failure' })
    updatedAt = [DateTimeOffset]::UtcNow.ToString('o')
    failureCode = $(if ($retryableDeadline) { $deadlineCode } else { $failureCode })
    failureStage = $stage
    ackPending = [bool]($retryableDeadline -or $isAckStage)
    completionAuthority = 'NONE_TRANSFER_PROGRESS_ONLY'
    inventoryGenerationId = [string]$Identity.inventory_generation_id
    inventorySha256 = [string]$Identity.inventory_sha256
    sourceRevision = [string]$Identity.source_git_rev
    observedSourceRevision = $(if ($observedRevision) { $observedRevision } else { $null })
    deployedRevision = $(if ($observedRevision) { $observedRevision } else { $null })
    collectionEpochId = [string]$Identity.collection_epoch_id
    tileRegistrySignature = [string]$Identity.tile_registry_signature
    fileIndex = Get-FlyResumeNonNegativeInt64 $(if ($trustedPrevious) { $trustedPrevious.fileIndex } else { 0 })
    fileCount = Get-FlyResumeNonNegativeInt64 $(if ($trustedPrevious) { $trustedPrevious.fileCount } else { 0 })
    verifiedPayloadBytes = Get-FlyResumeNonNegativeInt64 $(if ($trustedPrevious) { $trustedPrevious.verifiedPayloadBytes } else { 0 })
    currentFile = $(if ($trustedPrevious) { [string]$trustedPrevious.currentFile } else { $null })
    failureDiagnostic = [ordered]@{
      ackHttpStatus = $httpStatus
      ackServerClass = $serverClass
      retryableDeadline = [bool]$retryableDeadline
      retryableDeadlineOrigin = $deadlineOrigin
      responseExcerptSha256 = $excerptDigest
      responseExcerptBytes = [System.Text.Encoding]::UTF8.GetByteCount($excerpt)
      responseExcerptTruncated = [bool]($originalLength -gt 4096)
    }
  }
}

function Write-FlyGenerationResumeFailureReceipt {
  param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][object]$Receipt
  )
  $destination = [IO.Path]::GetFullPath($Path)
  $directory = Split-Path -Parent $destination
  [IO.Directory]::CreateDirectory($directory) | Out-Null
  $temporary = "$destination.failure-$PID-$([guid]::NewGuid().ToString('N')).tmp"
  try {
    [IO.File]::WriteAllText(
      $temporary,
      (($Receipt | ConvertTo-Json -Depth 8 -Compress) + [Environment]::NewLine),
      [System.Text.UTF8Encoding]::new($false)
    )
    # The child sync can finish its last heartbeat while Windows still holds
    # a short-lived handle on the destination. Retry the same atomic replace
    # instead of converting a valid bounded deadline into the misleading
    # RESUME_ATTEMPT_FAILURE_RECEIPT_WRITE_FAILED class.
    $lastError = $null
    for ($writeAttempt = 1; $writeAttempt -le 8; $writeAttempt++) {
      try {
        if ([IO.File]::Exists($destination)) {
          [IO.File]::Move($temporary, $destination, $true)
        } else {
          [IO.File]::Move($temporary, $destination)
        }
        $lastError = $null
        break
      } catch {
        $lastError = $_
        if ($writeAttempt -lt 8) {
          Start-Sleep -Milliseconds ([Math]::Min(500, 50 * $writeAttempt))
        }
      }
    }
    if ($null -ne $lastError) { throw $lastError }
  } finally {
    if ([IO.File]::Exists($temporary)) { [IO.File]::Delete($temporary) }
  }
}

function Invoke-FlyGenerationResume {
  param(
    [Parameter(Mandatory)][object]$Identity,
    [Parameter(Mandatory)][scriptblock]$ReadManifest,
    [Parameter(Mandatory)][scriptblock]$RunAttempt
  )
  $fields = @('inventory_generation_id','inventory_sha256','source_git_rev','collection_epoch_id','tile_registry_signature')
  foreach ($field in $fields) {
    if ([string]::IsNullOrWhiteSpace([string]$Identity.$field)) { throw 'RESUME_IDENTITY_INVALID' }
  }
  if ([string]$Identity.inventory_generation_id -cnotmatch '^[0-9a-f]{64}$' -or
      $Identity.inventory_sha256 -cne $Identity.inventory_generation_id -or
      [string]$Identity.source_git_rev -cnotmatch '^[0-9a-f]{40}$') { throw 'RESUME_IDENTITY_INVALID' }
  $previousFiles = [long]0
  $previousBytes = [long]0
  for ($attempt = 1; $attempt -le 4; $attempt++) {
    $manifest = & $ReadManifest $Identity.inventory_generation_id
    if ($manifest.inventory_status -cne 'CURRENT' -or $manifest.inventory_authoritative -isnot [bool] -or
        $manifest.inventory_authoritative -ne $true -or $manifest.inventory_ack_eligible -isnot [bool] -or
        $manifest.inventory_ack_eligible -ne $true) { throw 'RESUME_AUTHORITY_UNAVAILABLE' }
    foreach ($field in $fields) {
      if ($field -eq 'source_git_rev') {
        if (-not (Test-FlyResumeRevision $Identity.source_git_rev $manifest.source_git_rev)) { throw 'RESUME_IDENTITY_CHANGED' }
        continue
      }
      if ([string]$manifest.$field -cne [string]$Identity.$field) { throw 'RESUME_IDENTITY_CHANGED' }
    }
    $outcome = & $RunAttempt $manifest $attempt
    if ($outcome.Success -is [bool] -and $outcome.Success -eq $true) {
      if (-not (Test-FlyResumeTerminalAck -Identity $Identity -Result $outcome.Result -Manifest $manifest)) {
        throw 'RESUME_TERMINAL_ACK_INVALID'
      }
      return $outcome.Result
    }
    $receipt = $outcome.Receipt
    if ($receipt.failureCode -cnotin @('BUNDLE_TRANSFER_DEADLINE','BUNDLE_INDEX_PREPARATION_DEADLINE')) {
      throw 'RESUME_NON_DEADLINE_FAILURE'
    }
    if ($receipt.ok -ne $false -or $receipt.inProgress -ne $false -or $receipt.ackPending -ne $true -or
        $receipt.completionAuthority -cne 'NONE_TRANSFER_PROGRESS_ONLY' -or
        [string]$receipt.inventoryGenerationId -cne [string]$Identity.inventory_generation_id -or
        [string]$receipt.inventorySha256 -cne [string]$Identity.inventory_sha256 -or
        [string]$receipt.collectionEpochId -cne [string]$Identity.collection_epoch_id -or
        [string]$receipt.sourceRevision -cne [string]$Identity.source_git_rev -or
        -not (Test-FlyResumeRevision $Identity.source_git_rev $receipt.deployedRevision) -or
        [string]$receipt.deployedRevision -cne [string]$manifest.source_git_rev -or
        [string]$receipt.tileRegistrySignature -cne [string]$Identity.tile_registry_signature) { throw 'RESUME_PROGRESS_IDENTITY_INVALID' }
    if (($receipt.fileIndex -isnot [int] -and $receipt.fileIndex -isnot [long]) -or
        ($receipt.verifiedPayloadBytes -isnot [int] -and $receipt.verifiedPayloadBytes -isnot [long]) -or
        $receipt.fileIndex -lt $previousFiles -or $receipt.verifiedPayloadBytes -lt $previousBytes -or
        ($receipt.fileIndex -eq $previousFiles -and $receipt.verifiedPayloadBytes -eq $previousBytes)) {
      throw 'RESUME_NO_VERIFIED_PROGRESS'
    }
    $previousFiles = [long]$receipt.fileIndex
    $previousBytes = [long]$receipt.verifiedPayloadBytes
  }
  throw 'RESUME_ATTEMPTS_EXHAUSTED'
}

function Start-FlyGenerationResume {
  param(
    [Parameter(Mandatory)][object]$Identity,
    [Parameter(Mandatory)][string]$TargetDir,
    [Parameter(Mandatory)][string]$ReceiptDirectory,
    [string]$AdminToken = ''
  )
  . (Join-Path $PSScriptRoot 'fly-canonical-lock.ps1')
  . (Join-Path $PSScriptRoot 'home-bot-vault-env.ps1')
  $source = Get-CanonicalFlyBotUrl
  if (-not $AdminToken) { $AdminToken = Import-CanonicalBotAdminToken }
  if (-not $AdminToken) { throw 'ADMIN_TOKEN_REQUIRED' }
  . (Join-Path $PSScriptRoot 'fly-sync-bundles.ps1')
  # GetNewClosure uses a dynamic module: retain the validator explicitly rather
  # than resolving a function from this caller's transient local scope.
  $assertUnlinkedPath = ${function:Assert-FlyBundleUnlinkedPath}
  Assert-FlyBundleUnlinkedPath -Path $ReceiptDirectory
  New-Item -ItemType Directory -Path $ReceiptDirectory -Force | Out-Null
  $scriptPath = Join-Path $PSScriptRoot 'sync-fly-bot-data.ps1'
  $readManifest = {
    param($generation)
    Invoke-RestMethod -Uri "$source/api/data-sync/manifest?paged=1&generation_id=$generation" `
      -Headers @{'X-Bot-Admin-Token'=$AdminToken} -MaximumRedirection 0 -TimeoutSec 30 -ErrorAction Stop
  }.GetNewClosure()
  $run = {
    param($manifest, $attempt)
    $receiptPath = Join-Path $ReceiptDirectory ("resume-$attempt-" + [guid]::NewGuid().ToString('N') + '.json')
    try {
      $result = & $scriptPath -SourceUrl $source -AdminToken $AdminToken -TargetDir $TargetDir `
        -InitialManifest $manifest -ProgressHeartbeatFile $receiptPath -MirroredSourceRevision $Identity.source_git_rev
      return @{Success=$true; Result=$result}
    } catch {
      $failureError = $_
      $failureReceipt = New-FlyGenerationResumeFailureReceipt `
        -ErrorRecord $failureError `
        -Identity $Identity `
        -Manifest $manifest `
        -PreviousReceiptPath $receiptPath `
        -Attempt $attempt
      try {
        Write-FlyGenerationResumeFailureReceipt -Path $receiptPath -Receipt $failureReceipt
      } catch {
        throw 'RESUME_ATTEMPT_FAILURE_RECEIPT_WRITE_FAILED'
      }
      & $assertUnlinkedPath -Path $receiptPath
      if ((Get-Item -LiteralPath $receiptPath).Length -gt 65536) { throw 'RESUME_RECEIPT_LIMIT' }
      return @{Success=$false; Receipt=$failureReceipt}
    }
  }.GetNewClosure()
  Assert-FlyBundleUnlinkedPath -Path $TargetDir
  $leasePath = Join-Path $TargetDir '.fly-mirror-generation.lease'
  Assert-FlyBundleUnlinkedPath -Path $leasePath
  $lease = [IO.File]::Open($leasePath,
    [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
  $resumePreviousOptIn = [Environment]::GetEnvironmentVariable('FLY_SYNC_TRANSPORT_BUNDLES', 'Process')
  try {
    # Prefer caller opt-in. Default serial: Fly bundle builder hits
    # BUNDLE_SLICE_TIMEOUT/CIRCUIT_OPEN on large gens; pin-keepalive + serial
    # can finish ACK while generation stays CURRENT.
    $bundleOptIn = [Environment]::GetEnvironmentVariable('FLY_SYNC_TRANSPORT_BUNDLES', 'Process')
    if ([string]::IsNullOrWhiteSpace($bundleOptIn)) {
      $bundleOptIn = '0'
    }
    [Environment]::SetEnvironmentVariable('FLY_SYNC_TRANSPORT_BUNDLES', $bundleOptIn, 'Process')
    Invoke-FlyGenerationResume -Identity $Identity -ReadManifest $readManifest -RunAttempt $run
  } finally {
    try {
      [Environment]::SetEnvironmentVariable('FLY_SYNC_TRANSPORT_BUNDLES', $resumePreviousOptIn, 'Process')
    } finally {
      $lease.Dispose()
    }
  }
}

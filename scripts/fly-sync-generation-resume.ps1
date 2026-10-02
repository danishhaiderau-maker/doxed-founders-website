# Explicit bounded recovery, not a perpetual polling loop. Dot-source to use.
function Test-FlyResumeRevision {
  param([string]$Expected, [string]$Observed)
  return ($Expected -cmatch '^[0-9a-f]{40}$' -and $Observed -cmatch '^[0-9a-f]{12,40}$' -and
    $Expected.StartsWith($Observed, [StringComparison]::Ordinal))
}

# A successful transport invocation is not terminal authority on its own.  The
# result must bind this exact manifest to the remote FINALIZE receipt and the
# immutable local membership receipt before the caller may treat it as ACKed.
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
    if ($receipt.ok -isnot [bool] -or $receipt.ok -ne $false -or
        $receipt.inProgress -isnot [bool] -or $receipt.inProgress -ne $false -or
        $receipt.ackPending -isnot [bool] -or $receipt.ackPending -ne $true -or
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
      if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) { throw 'RESUME_ATTEMPT_WITHOUT_RECEIPT' }
      & $assertUnlinkedPath -Path $receiptPath
      if ((Get-Item -LiteralPath $receiptPath).Length -gt 65536) { throw 'RESUME_RECEIPT_LIMIT' }
      return @{Success=$false; Receipt=(Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json)}
    }
  }.GetNewClosure()
  Assert-FlyBundleUnlinkedPath -Path $TargetDir
  $leasePath = Join-Path $TargetDir '.fly-mirror-generation.lease'
  Assert-FlyBundleUnlinkedPath -Path $leasePath
  $lease = [IO.File]::Open($leasePath,
    [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
  $resumePreviousOptIn = [Environment]::GetEnvironmentVariable('FLY_SYNC_TRANSPORT_BUNDLES', 'Process')
  try {
    [Environment]::SetEnvironmentVariable('FLY_SYNC_TRANSPORT_BUNDLES', '1', 'Process')
    Invoke-FlyGenerationResume -Identity $Identity -ReadManifest $readManifest -RunAttempt $run
  } finally {
    try {
      [Environment]::SetEnvironmentVariable('FLY_SYNC_TRANSPORT_BUNDLES', $resumePreviousOptIn, 'Process')
    } finally {
      $lease.Dispose()
    }
  }
}

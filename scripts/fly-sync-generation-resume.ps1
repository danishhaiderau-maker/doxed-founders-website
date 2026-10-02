# Explicit bounded recovery, not a perpetual polling loop. Dot-source to use.
function Test-FlyResumeRevision {
  param([string]$Expected, [string]$Observed)
  return ($Expected -cmatch '^[0-9a-f]{40}$' -and $Observed -cmatch '^[0-9a-f]{12,40}$' -and
    $Expected.StartsWith($Observed, [StringComparison]::Ordinal))
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
      if ($outcome.Result.AckAccepted -isnot [bool] -or $outcome.Result.AckAccepted -ne $true -or
          -not (Test-FlyResumeRevision $Identity.source_git_rev $outcome.Result.SourceRevision)) { throw 'RESUME_TERMINAL_ACK_INVALID' }
      return $outcome.Result
    }
    $receipt = $outcome.Receipt
    if ($receipt.failureCode -cnotin @('BUNDLE_TRANSFER_DEADLINE','BUNDLE_INDEX_PREPARATION_DEADLINE')) {
      throw 'RESUME_NON_DEADLINE_FAILURE'
    }
    if ($receipt.ok -ne $false -or $receipt.inProgress -ne $false -or $receipt.ackPending -ne $true -or
        $receipt.completionAuthority -cne 'NONE_TRANSFER_PROGRESS_ONLY' -or
        [string]$receipt.inventoryGenerationId -cne [string]$Identity.inventory_generation_id -or
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
      Assert-FlyBundleUnlinkedPath -Path $receiptPath
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

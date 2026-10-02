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
      $detail = if ($receipt.PSObject.Properties.Name -contains 'transferError' -and
                    -not [string]::IsNullOrWhiteSpace([string]$receipt.transferError)) {
        [string]$receipt.transferError
      } else { 'NO_CHILD_ERROR_CAPTURED' }
      if ($detail.Length -gt 2000) { $detail = $detail.Substring(0, 2000) }
      throw ("RESUME_NON_DEADLINE_FAILURE: " + $detail)
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
  # Scriptblock closures do not reliably resolve helper functions after the
  # child sync throws. Keep the same reparse-point fence local to the closure
  # so the original transfer exception is preserved in the receipt.
  $assertUnlinkedPath = {
    param([Parameter(Mandatory)][string]$Path)
    $current = [IO.Path]::GetFullPath($Path)
    while ($current) {
      if (Test-Path -LiteralPath $current) {
        if ((Get-Item -LiteralPath $current -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
          throw 'BUNDLE_LINK_OR_REPARSE_REJECTED'
        }
      }
      $parent = [IO.Path]::GetDirectoryName($current.TrimEnd('\'))
      if ($parent -eq $current) { break }
      $current = $parent
    }
  }.GetNewClosure()
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
      & $assertUnlinkedPath $receiptPath
      if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
        $transferError = $_.Exception.ToString()
        if ($transferError.Length -gt 4000) { $transferError = $transferError.Substring(0, 4000) }
        $failureObject = [ordered]@{
          ok = $false
          inProgress = $false
          ackPending = $true
          completionAuthority = 'NONE_TRANSFER_PROGRESS_ONLY'
          inventoryGenerationId = [string]$Identity.inventory_generation_id
          collectionEpochId = [string]$Identity.collection_epoch_id
          sourceRevision = [string]$Identity.source_git_rev
          deployedRevision = [string]$Identity.source_git_rev
          tileRegistrySignature = [string]$Identity.tile_registry_signature
          failureCode = 'SYNC_CHILD_EXCEPTION'
          transferError = $transferError
          transferErrorType = $_.Exception.GetType().FullName
        }
        $json = ($failureObject | ConvertTo-Json -Depth 8) + [Environment]::NewLine
        [IO.File]::WriteAllText($receiptPath, $json, (New-Object System.Text.UTF8Encoding($false)))
        return @{Success=$false; Receipt=([pscustomobject]$failureObject)}
      }
      if ((Get-Item -LiteralPath $receiptPath).Length -gt 65536) { throw 'RESUME_RECEIPT_LIMIT' }
      $receiptObject = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
      $transferError = $_.Exception.ToString()
      if ($transferError.Length -gt 4000) { $transferError = $transferError.Substring(0, 4000) }
      $receiptObject | Add-Member -MemberType NoteProperty -Name transferError -Value $transferError -Force
      $receiptObject | Add-Member -MemberType NoteProperty -Name transferErrorType -Value $_.Exception.GetType().FullName -Force
      $json = ($receiptObject | ConvertTo-Json -Depth 8) + [Environment]::NewLine
      [IO.File]::WriteAllText($receiptPath, $json, (New-Object System.Text.UTF8Encoding($false)))
      return @{Success=$false; Receipt=$receiptObject}
    }
  }.GetNewClosure()
  Assert-FlyBundleUnlinkedPath -Path $TargetDir
  $leasePath = Join-Path $TargetDir '.fly-mirror-generation.lease'
  Assert-FlyBundleUnlinkedPath -Path $leasePath
  $lease = [IO.File]::Open($leasePath,
    [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
  try {
    Invoke-FlyGenerationResume -Identity $Identity -ReadManifest $readManifest -RunAttempt $run
  } finally {
    $lease.Dispose()
  }
}

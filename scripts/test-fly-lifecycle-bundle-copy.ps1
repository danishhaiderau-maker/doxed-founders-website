$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'fly-lifecycle-bundle-copy.ps1')

function Assert-True([bool]$Condition, [string]$Message) {
  if (-not $Condition) { throw "ASSERTION_FAILED: $Message" }
}

$testRoot = Join-Path ([IO.Path]::GetTempPath()) ("btc-lifecycle-copy-" + [guid]::NewGuid().ToString('N'))
try {
  $bundleId = 'lifecycle-' + ('a' * 64)
  $bundleRelative = "v3/lifecycle_bundles/aa/$bundleId"
  $bundleRoot = Join-Path $testRoot ($bundleRelative -replace '/', '\')
  New-Item -ItemType Directory -Path $bundleRoot -Force | Out-Null
  $eventPath = Join-Path $bundleRoot 'events.jsonl'
  [IO.File]::WriteAllText($eventPath, "{`"record_id`":`"r1`"}`n", [Text.UTF8Encoding]::new($false))
  $eventRow = [ordered]@{
    path = 'events.jsonl'
    role = 'LIFECYCLE_EVENTS'
    size = [int64](Get-Item $eventPath).Length
    mtime_ns = [int64]1000000000
    sha256 = Get-LifecycleBundleSha256 $eventPath
    row_count = 1
    first_timestamp = '2026-09-01T00:00:00Z'
    last_timestamp = '2026-09-01T00:00:01Z'
  }
  $cleanupMaterial = @([ordered]@{
    path = $eventRow.path; sha256 = $eventRow.sha256; size = $eventRow.size
    mtime_ns = $eventRow.mtime_ns; row_count = $eventRow.row_count
    first_timestamp = $eventRow.first_timestamp; last_timestamp = $eventRow.last_timestamp
  })
  $manifest = [ordered]@{
    schema = 'research_lifecycle_bundle_v1'
    bundle_id = $bundleId
    lifecycle_identity_id = 'identity-1'
    lifecycle_id = 'episode-1|policy-1|FIXED'
    identity = [ordered]@{ collection_epoch_id = 'epoch-1'; episode_id = 'episode-1'; policy_signature = 'policy-1'; research_lane = 'FIXED' }
    provenance = [ordered]@{ source_revision = ('b' * 40); deployed_revision = ('c' * 40); tile_config_signature = ('d' * 64) }
    completion = [ordered]@{ ready = $true; classification = 'NO_FILL'; terminal_ts = [double]1788220800.0; horizon_complete_ts = [double]1788228000.0; blockers = @() }
    files = @($eventRow)
    source_cleanup_authorized = $false
    cleanup_manifest_sha256 = Get-LifecycleTextSha256 (ConvertTo-LifecycleCanonicalJson $cleanupMaterial)
  }
  $manifest.manifest_sha256 = ('0' * 64)
  $manifestPath = Join-Path $bundleRoot 'manifest.json'
  [IO.File]::WriteAllText($manifestPath, (($manifest | ConvertTo-Json -Depth 10 -Compress) + "`n"), [Text.UTF8Encoding]::new($false))
  $manifest.manifest_sha256 = Get-LifecycleManifestContentSha256 $manifestPath
  [IO.File]::WriteAllText($manifestPath, (($manifest | ConvertTo-Json -Depth 10 -Compress) + "`n"), [Text.UTF8Encoding]::new($false))

  $receipts = [Collections.Generic.List[object]]::new()
  $post = {
    param($receipt)
    $receipts.Add(($receipt | ConvertTo-Json -Depth 12 -Compress))
    return [pscustomobject]@{ ok = $true; status = 'ACKNOWLEDGED_SOURCE_RETAINED'; bundle_id = $receipt.bundle_id; source_cleanup_authorized = $false }
  }
  $manifestRelative = "$bundleRelative/manifest.json"
  $first = Publish-LifecycleBundleCopyAndAck -TargetRoot $testRoot -BundleManifestRelativePath $manifestRelative -PostAcknowledgement $post
  $second = Publish-LifecycleBundleCopyAndAck -TargetRoot $testRoot -BundleManifestRelativePath $manifestRelative -PostAcknowledgement $post
  Assert-True ($receipts.Count -eq 2) 'both first and idempotent retry post an acknowledgement'
  Assert-True ($receipts[0] -ceq $receipts[1]) 'idempotent retry reproduces the exact immutable receipt'
  Assert-True (Test-Path -LiteralPath $eventPath) 'canonical source is retained'
  Assert-True (Test-Path -LiteralPath (Join-Path $testRoot "archive/lifecycle_bundles/$bundleId/events.jsonl")) 'recoverable archive is published'
  Assert-True (Test-Path -LiteralPath (Join-Path $testRoot "v3/lifecycle_bundle_index/$bundleId.json")) 'durable index is published'
  Assert-True ($first.Receipt.source_cleanup_authorized -ne $true) 'receipt cannot authorize cleanup'

  $transferId = 'transfer-' + ('e' * 64)
  $transferRelative = "v3/lifecycle_transfer_bundles/ee/$transferId"
  $transferRoot = Join-Path $testRoot ($transferRelative -replace '/', '\')
  New-Item -ItemType Directory -Path $transferRoot -Force | Out-Null
  $transferEventPath = Join-Path $transferRoot 'events.jsonl'
  [IO.File]::WriteAllText($transferEventPath, "{`"record_id`":`"transfer-r1`"}`n", [Text.UTF8Encoding]::new($false))
  $transferEventRow = [ordered]@{
    path = 'events.jsonl'
    role = 'TRANSFER_LIFECYCLE_EVENTS'
    size = [int64](Get-Item $transferEventPath).Length
    mtime_ns = [int64]2000000000
    sha256 = Get-LifecycleBundleSha256 $transferEventPath
    row_count = 1
    first_timestamp = '2026-09-01T01:00:00Z'
    last_timestamp = '2026-09-01T01:00:01Z'
  }
  $transferCleanupMaterial = @([ordered]@{
    path = $transferEventRow.path; sha256 = $transferEventRow.sha256; size = $transferEventRow.size
    mtime_ns = $transferEventRow.mtime_ns; row_count = $transferEventRow.row_count
    first_timestamp = $transferEventRow.first_timestamp; last_timestamp = $transferEventRow.last_timestamp
  })
  $transferManifest = [ordered]@{
    schema = 'research_lifecycle_transfer_bundle_v1'
    bundle_id = $transferId
    lifecycle_identity_id = 'identity-transfer-1'
    lifecycle_id = 'episode-transfer-1|policy-1|FIXED'
    identity = [ordered]@{ collection_epoch_id = 'epoch-1'; episode_id = 'episode-transfer-1'; policy_signature = 'policy-1'; research_lane = 'FIXED' }
    provenance = [ordered]@{ source_revision = ('b' * 40); deployed_revision = ('c' * 40); tile_config_signature = ('d' * 64) }
    maturity = 'TRANSFER_READY'
    transfer_receipt = [ordered]@{
      schema = 'lifecycle_bundle_transfer_ready_v1'
      transfer_ready = $true
      entry_outcome = 'NO_FILL'
      terminal_ts = [double]1788224400.0
      profitability_supported = $false
      source_cleanup_authorized = $false
    }
    qualification_ready = $false
    qualification_blockers = @('POST_OBSERVATION_MISSING')
    profitability_supported = $false
    ranking_eligible = $false
    files = @($transferEventRow)
    source_cleanup_authorized = $false
    cleanup_manifest_sha256 = Get-LifecycleTextSha256 (ConvertTo-LifecycleCanonicalJson $transferCleanupMaterial)
  }
  $transferManifest.manifest_sha256 = ('0' * 64)
  $transferManifestPath = Join-Path $transferRoot 'manifest.json'
  [IO.File]::WriteAllText($transferManifestPath, (($transferManifest | ConvertTo-Json -Depth 10 -Compress) + "`n"), [Text.UTF8Encoding]::new($false))
  $transferManifest.manifest_sha256 = Get-LifecycleManifestContentSha256 $transferManifestPath
  [IO.File]::WriteAllText($transferManifestPath, (($transferManifest | ConvertTo-Json -Depth 10 -Compress) + "`n"), [Text.UTF8Encoding]::new($false))

  $transferReceipts = [Collections.Generic.List[object]]::new()
  $transferPost = {
    param($receipt)
    $transferReceipts.Add(($receipt | ConvertTo-Json -Depth 12 -Compress))
    return [pscustomobject]@{
      ok = $true
      status = 'ACKNOWLEDGED_SOURCE_RETAINED'
      bundle_id = $receipt.bundle_id
      profitability_supported = $false
      ranking_eligible = $false
      source_cleanup_authorized = $false
    }
  }
  $transferManifestRelative = "$transferRelative/manifest.json"
  $transferFirst = Publish-LifecycleBundleCopyAndAck -TargetRoot $testRoot -BundleManifestRelativePath $transferManifestRelative -PostAcknowledgement $transferPost
  $transferSecond = Publish-LifecycleBundleCopyAndAck -TargetRoot $testRoot -BundleManifestRelativePath $transferManifestRelative -PostAcknowledgement $transferPost
  Assert-True ($transferReceipts.Count -eq 2) 'transfer retry posts an acknowledgement twice'
  Assert-True ($transferReceipts[0] -ceq $transferReceipts[1]) 'transfer retry reproduces exact immutable receipt'
  Assert-True (Test-Path -LiteralPath (Join-Path $testRoot "archive/lifecycle_transfer_bundles/$transferId/events.jsonl")) 'transfer archive is independently published'
  Assert-True (Test-Path -LiteralPath (Join-Path $testRoot "v3/lifecycle_transfer_bundle_index/$transferId.json")) 'transfer index is independently published'
  Assert-True ([string]$transferFirst.Receipt.schema -ceq 'lifecycle_transfer_bundle_ack_v1') 'transfer uses a distinct acknowledgement schema'
  Assert-True ($transferFirst.Receipt.qualification_ready -eq $false) 'transfer acknowledgement is not qualification evidence'
  Assert-True ($transferFirst.Receipt.profitability_supported -eq $false) 'transfer acknowledgement is not profitability evidence'
  Assert-True ($transferFirst.Receipt.ranking_eligible -eq $false) 'transfer acknowledgement is not ranking evidence'
  Assert-True ($transferFirst.Receipt.source_cleanup_authorized -eq $false) 'transfer acknowledgement never authorizes cleanup'

  $unsafeTransferPost = {
    param($receipt)
    return [pscustomobject]@{
      ok = $true
      status = 'ACKNOWLEDGED_SOURCE_RETAINED'
      bundle_id = $receipt.bundle_id
      profitability_supported = $true
      ranking_eligible = $true
      source_cleanup_authorized = $true
    }
  }
  $unsafeAckRejected = $false
  try { [void](Publish-LifecycleBundleCopyAndAck -TargetRoot $testRoot -BundleManifestRelativePath $transferManifestRelative -PostAcknowledgement $unsafeTransferPost) }
  catch { $unsafeAckRejected = $_.Exception.Message -match 'incomplete or unsafe|qualification isolation' }
  Assert-True $unsafeAckRejected 'transfer server response cannot enable profitability, ranking, or cleanup'

  $wrongTransferRoot = Join-Path $testRoot "v3\lifecycle_transfer_bundles\aa\$transferId"
  New-Item -ItemType Directory -Path (Split-Path -Parent $wrongTransferRoot) -Force | Out-Null
  Copy-Item -LiteralPath $transferRoot -Destination $wrongTransferRoot -Recurse
  $wrongPathRejected = $false
  try { [void](Publish-LifecycleBundleCopyAndAck -TargetRoot $testRoot -BundleManifestRelativePath "v3/lifecycle_transfer_bundles/aa/$transferId/manifest.json" -PostAcknowledgement $transferPost) }
  catch { $wrongPathRejected = $_.Exception.Message -match 'schema/path identity mismatch|missing|not an allowed' }
  Assert-True $wrongPathRejected 'transfer schema under a wrong shard/path is rejected'

  $savedTransferJson = Get-Content -LiteralPath $transferManifestPath -Raw
  $transferManifest.schema = 'research_lifecycle_bundle_v1'
  $transferManifest.manifest_sha256 = ('0' * 64)
  [IO.File]::WriteAllText($transferManifestPath, (($transferManifest | ConvertTo-Json -Depth 10 -Compress) + "`n"), [Text.UTF8Encoding]::new($false))
  $transferManifest.manifest_sha256 = Get-LifecycleManifestContentSha256 $transferManifestPath
  [IO.File]::WriteAllText($transferManifestPath, (($transferManifest | ConvertTo-Json -Depth 10 -Compress) + "`n"), [Text.UTF8Encoding]::new($false))
  $wrongSchemaRejected = $false
  try { [void](Publish-LifecycleBundleCopyAndAck -TargetRoot $testRoot -BundleManifestRelativePath $transferManifestRelative -PostAcknowledgement $transferPost) }
  catch { $wrongSchemaRejected = $_.Exception.Message -match 'schema/path identity mismatch' }
  Assert-True $wrongSchemaRejected 'transfer path carrying a qualification schema is rejected'
  Assert-True ($transferReceipts.Count -eq 2) 'wrong-schema transfer is never acknowledged'
  [IO.File]::WriteAllText($transferManifestPath, $savedTransferJson, [Text.UTF8Encoding]::new($false))

  $transferManifest = $savedTransferJson | ConvertFrom-Json
  $transferManifest.ranking_eligible = $true
  $transferManifest.manifest_sha256 = ('0' * 64)
  [IO.File]::WriteAllText($transferManifestPath, (($transferManifest | ConvertTo-Json -Depth 10 -Compress) + "`n"), [Text.UTF8Encoding]::new($false))
  $transferManifest.manifest_sha256 = Get-LifecycleManifestContentSha256 $transferManifestPath
  [IO.File]::WriteAllText($transferManifestPath, (($transferManifest | ConvertTo-Json -Depth 10 -Compress) + "`n"), [Text.UTF8Encoding]::new($false))
  $invariantRejected = $false
  try { [void](Publish-LifecycleBundleCopyAndAck -TargetRoot $testRoot -BundleManifestRelativePath $transferManifestRelative -PostAcknowledgement $transferPost) }
  catch { $invariantRejected = $_.Exception.Message -match 'invariant failed' }
  Assert-True $invariantRejected 'transfer ranking/cleanup isolation invariant is fail-closed'
  Assert-True ($transferReceipts.Count -eq 2) 'invalid transfer invariant is never acknowledged'
  [IO.File]::WriteAllText($transferManifestPath, $savedTransferJson, [Text.UTF8Encoding]::new($false))

  [IO.File]::AppendAllText($transferEventPath, "corrupt`n")
  $transferCorruptionRejected = $false
  try { [void](Publish-LifecycleBundleCopyAndAck -TargetRoot $testRoot -BundleManifestRelativePath $transferManifestRelative -PostAcknowledgement $transferPost) }
  catch { $transferCorruptionRejected = $_.Exception.Message -match 'size mismatch|SHA-256 mismatch' }
  Assert-True $transferCorruptionRejected 'transfer corruption prevents acknowledgement'
  Assert-True ($transferReceipts.Count -eq 2) 'corrupt transfer is never posted'

  [IO.File]::AppendAllText($eventPath, "corrupt`n")
  $rejected = $false
  try { [void](Publish-LifecycleBundleCopyAndAck -TargetRoot $testRoot -BundleManifestRelativePath $manifestRelative -PostAcknowledgement $post) }
  catch { $rejected = $_.Exception.Message -match 'size mismatch|SHA-256 mismatch' }
  Assert-True $rejected 'canonical corruption prevents acknowledgement'
  Assert-True ($receipts.Count -eq 2) 'corrupt copy is never posted'

  Write-Output 'PASS qualification and transfer lifecycle canonical/archive/index ACK producer'
} finally {
  if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}

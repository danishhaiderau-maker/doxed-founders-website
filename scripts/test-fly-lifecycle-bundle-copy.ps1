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

  [IO.File]::AppendAllText($eventPath, "corrupt`n")
  $rejected = $false
  try { [void](Publish-LifecycleBundleCopyAndAck -TargetRoot $testRoot -BundleManifestRelativePath $manifestRelative -PostAcknowledgement $post) }
  catch { $rejected = $_.Exception.Message -match 'size mismatch|SHA-256 mismatch' }
  Assert-True $rejected 'canonical corruption prevents acknowledgement'
  Assert-True ($receipts.Count -eq 2) 'corrupt copy is never posted'

  Write-Output 'PASS lifecycle canonical/archive/index atomic idempotent ACK producer'
} finally {
  if (Test-Path -LiteralPath $testRoot) { Remove-Item -LiteralPath $testRoot -Recurse -Force }
}

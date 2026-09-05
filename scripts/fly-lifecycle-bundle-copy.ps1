$ErrorActionPreference = "Stop"

function Get-LifecycleBundleSha256 {
  param([Parameter(Mandatory = $true)][string]$Path)
  return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function ConvertTo-LifecycleCanonicalJson {
  param([AllowNull()]$Value)
  # The Fly verifier uses Python json.dumps(sort_keys=True, separators=(",", ":")).
  # Build PSCustomObjects in ordinal key order and ask ConvertTo-Json only to
  # perform JSON escaping/primitive rendering.
  if ($null -eq $Value) { return "null" }
  if ($Value -is [string]) { return ($Value | ConvertTo-Json -Compress) }
  if ($Value -is [bool]) { return $(if ($Value) { "true" } else { "false" }) }
  if ($Value -is [double] -or $Value -is [single]) {
    $rendered = ([double]$Value).ToString('R', [Globalization.CultureInfo]::InvariantCulture).ToLowerInvariant()
    if ($rendered -notmatch '[\.e]') { $rendered += '.0' }
    return $rendered
  }
  if ($Value -is [byte] -or $Value -is [int16] -or $Value -is [int32] -or
      $Value -is [int64] -or $Value -is [uint16] -or $Value -is [uint32] -or
      $Value -is [uint64] -or $Value -is [decimal]) {
    return [Convert]::ToString($Value, [Globalization.CultureInfo]::InvariantCulture)
  }
  if ($Value -is [System.Collections.IDictionary] -or $Value -is [pscustomobject]) {
    $names = if ($Value -is [System.Collections.IDictionary]) {
      @($Value.Keys | ForEach-Object { [string]$_ })
    } else { @($Value.PSObject.Properties.Name) }
    $parts = foreach ($name in @($names | Sort-Object -CaseSensitive)) {
      $child = if ($Value -is [System.Collections.IDictionary]) { $Value[$name] } else { $Value.$name }
      "$(ConvertTo-LifecycleCanonicalJson -Value $name):$(ConvertTo-LifecycleCanonicalJson -Value $child)"
    }
    return "{$($parts -join ',')}"
  }
  if ($Value -is [System.Collections.IEnumerable]) {
    $parts = foreach ($child in $Value) { ConvertTo-LifecycleCanonicalJson -Value $child }
    return "[$($parts -join ',')]"
  }
  throw "Unsupported lifecycle canonical JSON value: $($Value.GetType().FullName)"
}

function Get-LifecycleTextSha256 {
  param([Parameter(Mandatory = $true)][string]$Text)
  $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
  $hasher = [Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($hasher.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant() }
  finally { $hasher.Dispose() }
}

function Get-LifecycleHmacSha256 {
  param([Parameter(Mandatory = $true)][string]$Text, [Parameter(Mandatory = $true)][string]$Secret)
  $hmac = [Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($Secret))
  try {
    $hash = $hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))
    return ([BitConverter]::ToString($hash)).Replace('-', '').ToLowerInvariant()
  }
  finally { $hmac.Dispose() }
}

function Get-LifecycleManifestContentSha256 {
  param([Parameter(Mandatory = $true)][string]$ManifestPath)
  $hasher = Join-Path $PSScriptRoot 'hash-lifecycle-manifest.py'
  $result = & python $hasher $ManifestPath
  if ($LASTEXITCODE -ne 0 -or [string]$result -notmatch '^[0-9a-f]{64}$') {
    throw "Unable to compute canonical lifecycle manifest SHA-256."
  }
  return [string]$result
}

function Resolve-LifecycleContainedPath {
  param(
    [Parameter(Mandatory = $true)][string]$Root,
    [Parameter(Mandatory = $true)][string]$Relative
  )
  if ([string]::IsNullOrWhiteSpace($Relative) -or [IO.Path]::IsPathRooted($Relative) -or
      @($Relative.Replace('\', '/').Split('/')) -contains '..') {
    throw "Unsafe lifecycle bundle relative path: $Relative"
  }
  $rootFull = [IO.Path]::GetFullPath($Root).TrimEnd('\')
  $candidate = [IO.Path]::GetFullPath((Join-Path $rootFull ($Relative -replace '/', '\')))
  if (-not $candidate.StartsWith(($rootFull + '\'), [StringComparison]::OrdinalIgnoreCase)) {
    throw "Lifecycle bundle path escaped its root: $Relative"
  }
  return $candidate
}

function Test-LifecycleStrictBoolean {
  param([AllowNull()]$Value, [bool]$Expected)
  return ($Value -is [bool] -and $Value -eq $Expected)
}

function Assert-LifecycleQualificationEvidence {
  param([Parameter(Mandatory = $true)]$Manifest)
  $collection = $Manifest.evidence_collection
  $receipt = $collection.receipt
  $objectTypes = { param($Value) return ($Value -is [System.Collections.IDictionary] -or $Value -is [pscustomobject]) }
  if (-not (
    [string]$Manifest.maturity -ceq 'QUALIFICATION_READY' -and
    (& $objectTypes $collection) -and (& $objectTypes $receipt) -and
    (Test-LifecycleStrictBoolean $collection.ready $true) -and
    (Test-LifecycleStrictBoolean $Manifest.source_cleanup_authorized $false) -and
    $collection.blockers -is [System.Collections.IList] -and @($collection.blockers).Count -eq 0 -and
    [string]$receipt.schema -ceq 'lifecycle_evidence_collected_v1' -and
    [string]$receipt.evidence_collected_receipt_sha256 -cmatch '^[0-9a-f]{64}$' -and
    (& $objectTypes $Manifest.identity) -and (& $objectTypes $Manifest.provenance) -and
    (& $objectTypes $receipt.identity) -and (& $objectTypes $receipt.provenance)
  )) { throw 'Qualification lifecycle bundle maturity/evidence invariant failed.' }
  foreach ($field in @('collection_epoch_id', 'episode_id', 'policy_signature', 'research_lane')) {
    if ([string]::IsNullOrWhiteSpace([string]$Manifest.identity.$field) -or
        [string]$receipt.identity.$field -cne [string]$Manifest.identity.$field) {
      throw 'Qualification lifecycle evidence identity mismatch.'
    }
  }
  foreach ($field in @('source_revision', 'deployed_revision', 'tile_config_signature', 'config_signature')) {
    if ([string]::IsNullOrWhiteSpace([string]$Manifest.provenance.$field) -or
        [string]$receipt.provenance.$field -cne [string]$Manifest.provenance.$field) {
      throw 'Qualification lifecycle evidence provenance mismatch.'
    }
  }
  if ((ConvertTo-LifecycleCanonicalJson $receipt.identity) -cne (ConvertTo-LifecycleCanonicalJson $Manifest.identity) -or
      (ConvertTo-LifecycleCanonicalJson $receipt.provenance) -cne (ConvertTo-LifecycleCanonicalJson $Manifest.provenance)) {
    throw 'Qualification lifecycle evidence identity/provenance fields mismatch.'
  }
  # Use the same Python JSON canonicalization as Fly, including unicode escaping
  # and float rendering. Only this public receipt enters stdin, never credentials.
  $material = ConvertTo-LifecycleCanonicalJson $receipt
  $actual = $material | & python -c 'import sys,json,hashlib; r=json.load(sys.stdin); r.pop("evidence_collected_receipt_sha256",None); print(hashlib.sha256(json.dumps(r,separators=(",",":"),sort_keys=True).encode("utf-8")).hexdigest())'
  if ($LASTEXITCODE -ne 0 -or [string]$actual -cnotmatch '^[0-9a-f]{64}$' -or
      [string]$actual -cne [string]$receipt.evidence_collected_receipt_sha256) {
    throw 'Qualification lifecycle evidence receipt SHA-256 mismatch.'
  }
}

function Resolve-LifecycleBundleContract {
  param(
    [Parameter(Mandatory = $true)][string]$BundleManifestRelativePath,
    [Parameter(Mandatory = $true)]$Manifest
  )
  $relative = $BundleManifestRelativePath.Replace('\', '/').Trim('/')
  $schema = [string]$Manifest.schema
  $bundleId = [string]$Manifest.bundle_id
  if ($schema -ceq 'research_lifecycle_bundle_v1') {
    if ($bundleId -notmatch '^lifecycle-[0-9a-f]{64}$' -or
        $relative -cne "v3/lifecycle_bundles/$($bundleId.Substring(10, 2))/$bundleId/manifest.json") {
      throw "Qualification lifecycle bundle schema/path identity mismatch."
    }
    Assert-LifecycleQualificationEvidence $Manifest
    return [pscustomobject]@{
      Kind = 'QUALIFICATION'
      ArchiveDirectory = 'lifecycle_bundles'
      IndexDirectory = 'lifecycle_bundle_index'
      IndexSchema = 'laptop_lifecycle_bundle_index_v1'
      AckSchema = 'lifecycle_bundle_cleanup_ack_v1'
    }
  }
  if ($schema -ceq 'research_lifecycle_transfer_bundle_v1') {
    if ($bundleId -notmatch '^transfer-[0-9a-f]{64}$' -or
        $relative -cne "v3/lifecycle_transfer_bundles/$($bundleId.Substring(9, 2))/$bundleId/manifest.json") {
      throw "Transfer lifecycle bundle schema/path identity mismatch."
    }
    if (-not (
      [string]$Manifest.maturity -ceq 'TRANSFER_READY' -and
      (Test-LifecycleStrictBoolean $Manifest.qualification_ready $false) -and
      (Test-LifecycleStrictBoolean $Manifest.profitability_supported $false) -and
      (Test-LifecycleStrictBoolean $Manifest.ranking_eligible $false) -and
      (Test-LifecycleStrictBoolean $Manifest.source_cleanup_authorized $false) -and
      $null -eq $Manifest.completion -and
      [string]$Manifest.transfer_receipt.schema -ceq 'lifecycle_bundle_transfer_ready_v1' -and
      (Test-LifecycleStrictBoolean $Manifest.transfer_receipt.transfer_ready $true) -and
      (Test-LifecycleStrictBoolean $Manifest.transfer_receipt.profitability_supported $false) -and
      (Test-LifecycleStrictBoolean $Manifest.transfer_receipt.source_cleanup_authorized $false)
    )) {
      throw "Transfer lifecycle bundle invariant failed."
    }
    return [pscustomobject]@{
      Kind = 'TRANSFER'
      ArchiveDirectory = 'lifecycle_transfer_bundles'
      IndexDirectory = 'lifecycle_transfer_bundle_index'
      IndexSchema = 'laptop_lifecycle_transfer_bundle_index_v1'
      AckSchema = 'lifecycle_transfer_bundle_ack_v1'
    }
  }
  throw "Unsupported lifecycle bundle schema."
}

function Test-LifecycleBundleCopy {
  param(
    [Parameter(Mandatory = $true)][string]$BundleRoot,
    [Parameter(Mandatory = $true)]$Manifest
  )
  $bundleFull = [IO.Path]::GetFullPath($BundleRoot)
  if (-not (Test-Path -LiteralPath $bundleFull -PathType Container)) { throw "Lifecycle bundle is missing: $bundleFull" }
  if ((Get-Item -LiteralPath $bundleFull).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "Lifecycle bundle root cannot be a reparse point."
  }
  if ([string]$Manifest.schema -cnotin @('research_lifecycle_bundle_v1', 'research_lifecycle_transfer_bundle_v1')) {
    throw "Unsupported lifecycle bundle schema."
  }
  if ([string]$Manifest.bundle_id -cne (Split-Path -Leaf $bundleFull)) { throw "Lifecycle bundle directory identity mismatch." }
  if (-not (Test-LifecycleStrictBoolean $Manifest.source_cleanup_authorized $false)) { throw "Lifecycle bundle incorrectly authorizes source cleanup." }
  if ([string]$Manifest.schema -ceq 'research_lifecycle_bundle_v1') {
    Assert-LifecycleQualificationEvidence $Manifest
  }
  if ([string]$Manifest.schema -ceq 'research_lifecycle_transfer_bundle_v1' -and -not (
      [string]$Manifest.maturity -ceq 'TRANSFER_READY' -and
      (Test-LifecycleStrictBoolean $Manifest.qualification_ready $false) -and
      (Test-LifecycleStrictBoolean $Manifest.profitability_supported $false) -and
      (Test-LifecycleStrictBoolean $Manifest.ranking_eligible $false) -and
      $null -eq $Manifest.completion -and
      [string]$Manifest.transfer_receipt.schema -ceq 'lifecycle_bundle_transfer_ready_v1' -and
      (Test-LifecycleStrictBoolean $Manifest.transfer_receipt.transfer_ready $true) -and
      (Test-LifecycleStrictBoolean $Manifest.transfer_receipt.profitability_supported $false) -and
      (Test-LifecycleStrictBoolean $Manifest.transfer_receipt.source_cleanup_authorized $false)
    )) {
    throw "Transfer lifecycle bundle invariant failed."
  }

  $actualManifestSha = Get-LifecycleManifestContentSha256 (Join-Path $bundleFull 'manifest.json')
  if ($actualManifestSha -cne ([string]$Manifest.manifest_sha256).ToLowerInvariant()) {
    throw "Lifecycle manifest SHA-256 mismatch."
  }

  $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  $fileRows = @($Manifest.files)
  if ($fileRows.Count -eq 0) { throw "Lifecycle bundle manifest has no files." }
  foreach ($row in $fileRows) {
    $relative = [string]$row.path
    if (-not $seen.Add($relative)) { throw "Duplicate lifecycle manifest path: $relative" }
    $path = Resolve-LifecycleContainedPath -Root $bundleFull -Relative $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Lifecycle bundle file is missing: $relative" }
    $item = Get-Item -LiteralPath $path
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Lifecycle bundle file cannot be a reparse point: $relative" }
    if ([int64]$item.Length -ne [int64]$row.size) { throw "Lifecycle bundle file size mismatch: $relative" }
    if ((Get-LifecycleBundleSha256 $path) -cne ([string]$row.sha256).ToLowerInvariant()) {
      throw "Lifecycle bundle file SHA-256 mismatch: $relative"
    }
  }
  $declared = [Collections.Generic.HashSet[string]]::new($seen, [StringComparer]::Ordinal)
  [void]$declared.Add('manifest.json')
  foreach ($file in @(Get-ChildItem -LiteralPath $bundleFull -Recurse -File -Force)) {
    $relative = $file.FullName.Substring($bundleFull.Length).TrimStart('\').Replace('\', '/')
    if (-not $declared.Contains($relative)) { throw "Lifecycle bundle contains an undeclared file: $relative" }
  }

  $treeRows = foreach ($relative in @($declared | Sort-Object -CaseSensitive)) {
    $path = Resolve-LifecycleContainedPath -Root $bundleFull -Relative $relative
    [ordered]@{ path = $relative; sha256 = Get-LifecycleBundleSha256 $path; size = [int64](Get-Item $path).Length }
  }
  return [pscustomobject]@{
    ManifestSha256 = ([string]$Manifest.cleanup_manifest_sha256).ToLowerInvariant()
    TreeSha256 = Get-LifecycleTextSha256 (ConvertTo-LifecycleCanonicalJson @($treeRows))
  }
}

function Publish-LifecycleBundleCopyAndAck {
  param(
    [Parameter(Mandatory = $true)][string]$TargetRoot,
    [Parameter(Mandatory = $true)][string]$BundleManifestRelativePath,
    [Parameter(Mandatory = $true)][scriptblock]$PostAcknowledgement
  )
  $targetFull = [IO.Path]::GetFullPath($TargetRoot).TrimEnd('\')
  $manifestPath = Resolve-LifecycleContainedPath -Root $targetFull -Relative $BundleManifestRelativePath
  $bundleRoot = Split-Path -Parent $manifestPath
  $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
  $contract = Resolve-LifecycleBundleContract `
    -BundleManifestRelativePath $BundleManifestRelativePath `
    -Manifest $manifest
  $canonicalProof = Test-LifecycleBundleCopy -BundleRoot $bundleRoot -Manifest $manifest
  $bundleId = [string]$manifest.bundle_id

  $archiveParent = Join-Path $targetFull ("archive\" + $contract.ArchiveDirectory)
  $archiveRoot = Join-Path $archiveParent $bundleId
  New-Item -ItemType Directory -Path $archiveParent -Force | Out-Null
  if (-not (Test-Path -LiteralPath $archiveRoot -PathType Container)) {
    $stagingParent = Join-Path $archiveParent (".staging-$PID-" + [guid]::NewGuid().ToString('N'))
    $staging = Join-Path $stagingParent $bundleId
    try {
      [IO.Directory]::CreateDirectory($staging) | Out-Null
      foreach ($file in @(Get-ChildItem -LiteralPath $bundleRoot -Recurse -File -Force)) {
        $relative = $file.FullName.Substring($bundleRoot.Length).TrimStart('\')
        $destination = Resolve-LifecycleContainedPath -Root $staging -Relative $relative
        [IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
        [IO.File]::Copy($file.FullName, $destination, $false)
      }
      $stagedManifest = Get-Content -LiteralPath (Join-Path $staging 'manifest.json') -Raw | ConvertFrom-Json
      [void](Test-LifecycleBundleCopy -BundleRoot $staging -Manifest $stagedManifest)
      [IO.Directory]::Move($staging, $archiveRoot)
    } finally {
      if (Test-Path -LiteralPath $stagingParent) { Remove-Item -LiteralPath $stagingParent -Recurse -Force -ErrorAction SilentlyContinue }
    }
  }
  $archiveManifest = Get-Content -LiteralPath (Join-Path $archiveRoot 'manifest.json') -Raw | ConvertFrom-Json
  $archiveProof = Test-LifecycleBundleCopy -BundleRoot $archiveRoot -Manifest $archiveManifest
  if ($archiveProof.TreeSha256 -cne $canonicalProof.TreeSha256) { throw "Canonical/archive lifecycle tree mismatch." }

  $indexRoot = Join-Path $targetFull ("v3\" + $contract.IndexDirectory)
  New-Item -ItemType Directory -Path $indexRoot -Force | Out-Null
  $indexPath = Join-Path $indexRoot "$bundleId.json"
  if (-not (Test-Path -LiteralPath $indexPath -PathType Leaf)) {
    $acknowledgedAt = [DateTimeOffset]::UtcNow.ToString('o')
    $index = [ordered]@{
      schema = $contract.IndexSchema
      bundle_id = $bundleId
      lifecycle_id = [string]$manifest.lifecycle_id
      manifest_sha256 = $canonicalProof.ManifestSha256
      canonical_relative_path = $bundleRoot.Substring($targetFull.Length).TrimStart('\').Replace('\', '/')
      archive_relative_path = $archiveRoot.Substring($targetFull.Length).TrimStart('\').Replace('\', '/')
      canonical_tree_sha256 = $canonicalProof.TreeSha256
      archive_tree_sha256 = $archiveProof.TreeSha256
      acknowledged_at = $acknowledgedAt
      source_cleanup_authorized = $false
    }
    if ($contract.Kind -ceq 'TRANSFER') {
      $index['bundle_kind'] = 'TRANSFER'
      $index['qualification_ready'] = $false
      $index['profitability_supported'] = $false
      $index['ranking_eligible'] = $false
    }
    $temporaryIndex = "$indexPath.$PID.$([guid]::NewGuid().ToString('N')).tmp"
    try {
      [IO.File]::WriteAllText($temporaryIndex, (($index | ConvertTo-Json -Depth 6 -Compress) + [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
      if (Test-Path -LiteralPath $indexPath) { throw "Conflicting lifecycle index appeared during publication." }
      [IO.File]::Move($temporaryIndex, $indexPath)
    } finally { Remove-Item -LiteralPath $temporaryIndex -Force -ErrorAction SilentlyContinue }
  }
  $savedIndex = Get-Content -LiteralPath $indexPath -Raw | ConvertFrom-Json
  if ([string]$savedIndex.bundle_id -cne $bundleId -or
      [string]$savedIndex.lifecycle_id -cne [string]$manifest.lifecycle_id -or
      [string]$savedIndex.manifest_sha256 -cne $canonicalProof.ManifestSha256 -or
      [string]$savedIndex.canonical_tree_sha256 -cne $canonicalProof.TreeSha256 -or
      [string]$savedIndex.archive_tree_sha256 -cne $archiveProof.TreeSha256 -or
      -not (Test-LifecycleStrictBoolean $savedIndex.source_cleanup_authorized $false)) {
    throw "Existing lifecycle bundle index conflicts with verified copies."
  }
  if ($contract.Kind -ceq 'TRANSFER' -and -not (
      [string]$savedIndex.schema -ceq [string]$contract.IndexSchema -and
      [string]$savedIndex.bundle_kind -ceq 'TRANSFER' -and
      (Test-LifecycleStrictBoolean $savedIndex.qualification_ready $false) -and
      (Test-LifecycleStrictBoolean $savedIndex.profitability_supported $false) -and
      (Test-LifecycleStrictBoolean $savedIndex.ranking_eligible $false)
    )) {
    throw "Existing transfer lifecycle index violates qualification isolation."
  }
  $indexSha = Get-LifecycleBundleSha256 $indexPath
  $ackAt = [string]$savedIndex.acknowledged_at
  $files = @($manifest.files | Sort-Object { [string]$_.path })
  $completion = $(if ($contract.Kind -ceq 'TRANSFER') { $manifest.transfer_receipt } else { $manifest.completion })
  $terminalOutcome = $(if ($contract.Kind -ceq 'TRANSFER') {
    ([string]$completion.entry_outcome).ToUpperInvariant()
  } else {
    ([string]$completion.classification).ToUpperInvariant()
  })
  $terminalAt = [DateTimeOffset]::FromUnixTimeMilliseconds(
    [Convert]::ToInt64(([double]$completion.terminal_ts) * 1000.0)
  ).UtcDateTime.ToString('yyyy-MM-ddTHH:mm:ss.fffZ', [Globalization.CultureInfo]::InvariantCulture)
  $receipt = [ordered]@{
    schema = $contract.AckSchema
    bundle_id = $bundleId
    lifecycle_id = [string]$manifest.lifecycle_id
    bundle_manifest_path = $BundleManifestRelativePath.Replace('\', '/')
    source_git_rev = [string]$manifest.provenance.source_revision
    deployed_git_rev = [string]$manifest.provenance.deployed_revision
    collection_epoch_id = [string]$manifest.identity.collection_epoch_id
    tile_registry_signature = [string]$manifest.provenance.tile_config_signature
    config_signature = [string]$manifest.provenance.config_signature
    terminal_outcome = $terminalOutcome
    terminal_at = $terminalAt
    pending_order_ids = @()
    open_position_ids = @()
    files = $files
    manifest_sha256 = $canonicalProof.ManifestSha256
    immutable_identity_sha256 = ''
    laptop_acknowledgement = [ordered]@{
      canonical = [ordered]@{ complete = $true; bundle_id = $bundleId; lifecycle_id = [string]$manifest.lifecycle_id; sha256 = $canonicalProof.TreeSha256; manifest_sha256 = $canonicalProof.ManifestSha256; acknowledged_at = $ackAt }
      archive = [ordered]@{ complete = $true; bundle_id = $bundleId; lifecycle_id = [string]$manifest.lifecycle_id; sha256 = $archiveProof.TreeSha256; manifest_sha256 = $canonicalProof.ManifestSha256; acknowledged_at = $ackAt }
      index = [ordered]@{ complete = $true; bundle_id = $bundleId; lifecycle_id = [string]$manifest.lifecycle_id; sha256 = $indexSha; manifest_sha256 = $canonicalProof.ManifestSha256; acknowledged_at = $ackAt }
    }
  }
  if ($contract.Kind -ceq 'TRANSFER') {
    $receipt['bundle_kind'] = 'TRANSFER'
    $receipt['qualification_ready'] = $false
    $receipt['profitability_supported'] = $false
    $receipt['ranking_eligible'] = $false
    $receipt['source_cleanup_authorized'] = $false
  }
  if ($contract.Kind -ceq 'QUALIFICATION') {
    Assert-LifecycleQualificationEvidence $manifest
    # Cleanup/ACK eligibility on Fly requires explicit maturity + evidence-collection
    # bindings. Transfer-ready copies must never set these fields.
    if ([string]$manifest.maturity -cne 'QUALIFICATION_READY') {
      throw "Qualification lifecycle acknowledgement refused immature maturity."
    }
    $evidence = $manifest.evidence_collection
    if ($null -eq $evidence -or -not (Test-LifecycleStrictBoolean $evidence.ready $true)) {
      throw "Qualification lifecycle acknowledgement refused incomplete evidence collection."
    }
    $collectionSha = [string]$evidence.receipt.evidence_collected_receipt_sha256
    if ($collectionSha -notmatch '^[0-9a-f]{64}$') {
      throw "Qualification lifecycle acknowledgement missing evidence_collected_receipt_sha256."
    }
    $receipt['qualification_maturity'] = 'QUALIFICATION_READY'
    $receipt['evidence_collection_ready'] = $true
    $receipt['evidence_collected_receipt_sha256'] = $collectionSha.ToLowerInvariant()
  }
  $identity = [ordered]@{
    bundle_id = $bundleId
    collection_epoch_id = [string]$receipt.collection_epoch_id
    deployed_git_rev = [string]$receipt.deployed_git_rev
    lifecycle_id = [string]$receipt.lifecycle_id
    manifest_sha256 = [string]$receipt.manifest_sha256
    source_git_rev = [string]$receipt.source_git_rev
    terminal_at = [string]$receipt.terminal_at
    terminal_outcome = [string]$receipt.terminal_outcome
    tile_registry_signature = [string]$receipt.tile_registry_signature
  }
  if ($contract.Kind -ceq 'TRANSFER') {
    $identity.bundle_kind = [string]$receipt.bundle_kind
    $identity.qualification_ready = $false
    $identity.profitability_supported = $false
    $identity.ranking_eligible = $false
    $identity.source_cleanup_authorized = $false
  }
  $receipt.immutable_identity_sha256 = Get-LifecycleTextSha256 (ConvertTo-LifecycleCanonicalJson $identity)
  $attestationSecret = [string][Environment]::GetEnvironmentVariable('LIFECYCLE_LAPTOP_ATTESTATION_KEY')
  $attestationKeyId = [string][Environment]::GetEnvironmentVariable('LIFECYCLE_LAPTOP_ATTESTATION_KEY_ID')
  if ($attestationSecret -and $attestationKeyId) {
    $attestationMaterial = [ordered]@{
      schema = 'lifecycle_laptop_attestation_v1'
      bundle_id = $bundleId
      lifecycle_id = [string]$receipt.lifecycle_id
      immutable_identity_sha256 = [string]$receipt.immutable_identity_sha256
      manifest_sha256 = [string]$receipt.manifest_sha256
      canonical_sha256 = [string]$receipt.laptop_acknowledgement.canonical.sha256
      archive_sha256 = [string]$receipt.laptop_acknowledgement.archive.sha256
      index_sha256 = [string]$receipt.laptop_acknowledgement.index.sha256
    }
    $receipt['laptop_attestation'] = [ordered]@{
      schema = 'lifecycle_laptop_attestation_v1'
      key_id = $attestationKeyId
      hmac_sha256 = Get-LifecycleHmacSha256 (ConvertTo-LifecycleCanonicalJson $attestationMaterial) $attestationSecret
    }
  }
  $response = & $PostAcknowledgement $receipt
  if ($null -eq $response -or -not (Test-LifecycleStrictBoolean $response.ok $true) -or
      [string]$response.status -cne 'ACKNOWLEDGED_SOURCE_RETAINED' -or
      [string]$response.bundle_id -cne $bundleId -or
      -not (Test-LifecycleStrictBoolean $response.source_cleanup_authorized $false)) {
    throw "Fly lifecycle acknowledgement was incomplete or unsafe for bundle $bundleId."
  }
  if ($contract.Kind -ceq 'TRANSFER' -and -not (
      (Test-LifecycleStrictBoolean $response.profitability_supported $false) -and
      (Test-LifecycleStrictBoolean $response.ranking_eligible $false) -and
      (Test-LifecycleStrictBoolean $receipt.qualification_ready $false) -and
      (Test-LifecycleStrictBoolean $receipt.profitability_supported $false) -and
      (Test-LifecycleStrictBoolean $receipt.ranking_eligible $false) -and
      (Test-LifecycleStrictBoolean $receipt.source_cleanup_authorized $false)
    )) {
    throw "Transfer lifecycle acknowledgement violates qualification isolation."
  }
  return [pscustomobject]@{ BundleId = $bundleId; Receipt = $receipt; Response = $response }
}

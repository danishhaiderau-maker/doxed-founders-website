function Assert-FlyForensicPayload {
  param($Row, [string]$Path)
  $binding = $Row.forensic_component
  if ($null -eq $binding -or [string]$binding.expected_sha256 -cnotmatch '^[0-9a-f]{64}$') {
    throw 'FORENSIC_BINDING_INVALID'
  }
  Assert-FlyBundleUnlinkedPath -Path $Path
  if ((Get-Item -LiteralPath $Path -ErrorAction Stop).Length -ne [int64]$binding.size -or
      (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant() -cne [string]$binding.expected_sha256) {
    throw 'FORENSIC_PAYLOAD_MISMATCH'
  }
}

function Assert-FlyForensicGroups {
  param([object[]]$Rows, [string]$Root)
  $members = @{}
  foreach ($row in $Rows) {
    if ($members.ContainsKey([string]$row.path)) { throw 'FORENSIC_DUPLICATE_PATH' }
    $members[[string]$row.path] = $row
  }
  foreach ($row in $Rows) {
    $binding = $row.forensic_component
    if ([string]$row.path -cmatch '^v3/lifecycle_bundle_index/recovery-quarantine/[0-9a-f]{16}/lifecycle_index\.sqlite3(?:-wal|-shm)?$' -and $null -eq $binding) {
      throw 'FORENSIC_BINDING_MISSING'
    }
    if ($null -eq $binding) { continue }
    $rel = [string]$row.path
    if ($rel -cnotmatch '^v3/lifecycle_bundle_index/recovery-quarantine/[0-9a-f]{16}/lifecycle_index\.sqlite3(?:-wal|-shm)?$' -or
        [string]$binding.path -cne $rel -or
        [string]$binding.schema -cne 'quarantine_original_component_binding_v1' -or
        [string]$binding.expected_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
        [string]$row.consistency_mode -cne 'strict_generation_v1') {
      throw 'FORENSIC_BINDING_INVALID'
    }
    $parent = $rel.Substring(0, $rel.LastIndexOf('/'))
    $required = @($binding.required_components)
    if ($required.Count -lt 1 -or $required.Count -gt 3 -or 'lifecycle_index.sqlite3' -cnotin $required) {
      throw 'FORENSIC_GROUP_INVALID'
    }
    foreach ($name in $required) {
      if ([string]$name -cnotmatch '^lifecycle_index\.sqlite3(?:-wal|-shm)?$') { throw 'FORENSIC_COMPONENT_INVALID' }
      $other = $members["$parent/$name"]
      if ($null -eq $other -or $null -eq $other.forensic_component -or
          [string]$other.forensic_component.receipt_file_sha256 -cne [string]$binding.receipt_file_sha256) {
        throw 'FORENSIC_GROUP_INCOMPLETE'
      }
    }
    $local = Join-Path $Root $rel
    Assert-FlyForensicPayload -Row $row -Path $local
  }
}

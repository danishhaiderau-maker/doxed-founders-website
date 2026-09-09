# Invocation-local only: cache immutable manifest data, never snapshot leases.
function Get-FlyCachedCompleteManifest {
  param([object]$FreshManifest, [hashtable]$Cache, [scriptblock]$Expand)
  if ($null -eq $Cache) { return (& $Expand $FreshManifest) }
  if ($FreshManifest.inventory_status -cne 'CURRENT' -or
      $FreshManifest.inventory_authoritative -isnot [bool] -or
      $FreshManifest.inventory_authoritative -ne $true -or
      $FreshManifest.inventory_ack_eligible -isnot [bool] -or
      $FreshManifest.inventory_ack_eligible -ne $true) { throw 'MANIFEST_CACHE_AUTHORITY_UNAVAILABLE' }
  $fields = @('schema','inventory_generation_id','inventory_sha256','inventory_generated_at',
    'source_git_rev','collection_epoch_id','tile_registry_signature','file_count','total_bytes',
    'manifest_page_count','manifest_page_sha256','manifest_page_index','manifest_page_cursor')
  if ($Cache.ContainsKey('CompleteJson')) {
    $complete = $Cache.CompleteJson | ConvertFrom-Json
    foreach ($field in $fields) {
      if ([string]$complete.$field -cne [string]$FreshManifest.$field) { throw 'MANIFEST_CACHE_IDENTITY_CHANGED' }
    }
    return $complete
  }
  $complete = & $Expand $FreshManifest
  if ($complete.manifest_pages_complete -ne $true -or
      $complete.manifest_pages_aggregated -ne $complete.manifest_page_count -or
      @($complete.files).Count -ne $complete.file_count) { throw 'MANIFEST_CACHE_INCOMPLETE' }
  # Serialize before caller adds mutable SQLite leases or changes row sizes.
  $Cache.CompleteJson = ConvertTo-Json -InputObject $complete -Depth 100 -Compress
  return ($Cache.CompleteJson | ConvertFrom-Json)
}

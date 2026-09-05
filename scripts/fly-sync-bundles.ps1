function Assert-FlyBundleUnlinkedPath {
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
}

function Receive-FlyTransportBundles {
  param(
    [Parameter(Mandatory)][object]$Manifest,
    [Parameter(Mandatory)][string]$SourceUrl,
    [Parameter(Mandatory)][string]$AdminToken,
    [Parameter(Mandatory)][string]$TargetRoot,
    [Parameter(Mandatory)][string]$ClientScript,
    [Parameter(Mandatory)][System.Collections.IDictionary]$SyncState,
    [Parameter(Mandatory)][scriptblock]$SaveCheckpoint,
    [Parameter(Mandatory)][scriptblock]$Progress
  )
  $mirror = [IO.Path]::GetFullPath($TargetRoot).TrimEnd('\', '/')
  # A short canonical workspace staging path avoids legacy Python MAX_PATH.
  $workspace = Split-Path -Parent (Split-Path -Parent $ClientScript)
  $stage = Join-Path (Join-Path $workspace '.batch-transfer') ([guid]::NewGuid().ToString('N').Substring(0,12))
  Assert-FlyBundleUnlinkedPath -Path $mirror
  Assert-FlyBundleUnlinkedPath -Path $stage
  New-Item -ItemType Directory -Path $stage -ErrorAction Stop | Out-Null
  $stagePrefix = [IO.Path]::GetFullPath($stage).TrimEnd('\') + '\'
  $rows = [Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal)
  foreach ($row in @($Manifest.files)) {
    if ($rows.ContainsKey([string]$row.path)) { throw 'BUNDLE_MANIFEST_DUPLICATE' }
    $rows.Add([string]$row.path, $row)
  }
  $start = [Diagnostics.ProcessStartInfo]::new()
  $start.FileName = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
  [void]$start.ArgumentList.Add($ClientScript)
  $start.UseShellExecute = $false
  $start.CreateNoWindow = $true
  $start.RedirectStandardInput = $true
  $start.RedirectStandardOutput = $true
  $start.RedirectStandardError = $true
  $start.StandardInputEncoding = [Text.UTF8Encoding]::new($false)
  $start.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
  $process = [Diagnostics.Process]::new()
  $process.StartInfo = $start
  $complete = $false
  $started = $false
  $files = 0
  $lastIndexWait = -1.0
  $clock = [Diagnostics.Stopwatch]::StartNew()
  try {
    if (-not $process.Start()) { throw 'BUNDLE_CHILD_START_FAILED' }
    $started = $true
    $stderr = $process.StandardError.ReadToEndAsync()
    # Credential goes through a private pipe, never process arguments or disk.
    $request = @{ source_url=$SourceUrl; admin_token=$AdminToken; manifest=$Manifest; staging_root=$stage; verified_local_root=$mirror }
    $inputTask = $process.StandardInput.WriteAsync(($request | ConvertTo-Json -Depth 40 -Compress))
    if (-not $inputTask.Wait(30000)) { throw 'BUNDLE_CHILD_INPUT_TIMEOUT' }
    $process.StandardInput.Close()
    while ($true) {
      $lineTask = $process.StandardOutput.ReadLineAsync()
      while (-not $lineTask.Wait(1000)) {
        if ($clock.Elapsed.TotalSeconds -gt 1900) { throw 'BUNDLE_CLIENT_WALL_TIMEOUT' }
      }
      $line = $lineTask.Result
      if ($clock.Elapsed.TotalSeconds -gt 1900) { throw 'BUNDLE_CLIENT_WALL_TIMEOUT' }
      if ($null -eq $line) { break }
      if ($line.Length -gt 2097152) { throw 'BUNDLE_RECEIPT_LIMIT' }
      $receipt = $line | ConvertFrom-Json
      if ($receipt.schema -cne 'fly_bundle_staging_receipt_v1') { throw 'BUNDLE_RECEIPT_SCHEMA' }
      if ($receipt.status -ceq 'FAILED') { throw ('BUNDLE_TRANSFER_FAILED: ' + [string]$receipt.error) }
      if ($receipt.status -ceq 'INDEX_WAITING') {
        if ($complete -or $files -ne 0) { throw 'BUNDLE_RECEIPT_SEQUENCE' }
        foreach ($field in @('inventory_generation_id','inventory_sha256','source_git_rev','collection_epoch_id','tile_registry_signature')) {
          if ([string]$receipt.generation.$field -cne [string]$Manifest.$field) { throw 'BUNDLE_RECEIPT_IDENTITY' }
        }
        $elapsed = [double]$receipt.elapsed_seconds
        $retry = [double]$receipt.next_retry_seconds
        if ([double]::IsNaN($elapsed) -or [double]::IsInfinity($elapsed) -or
            $elapsed -lt 0 -or $elapsed -gt 600 -or $elapsed -lt $lastIndexWait -or
            [double]::IsNaN($retry) -or [double]::IsInfinity($retry) -or $retry -le 0 -or $retry -gt 30) {
          throw 'BUNDLE_INDEX_WAIT_INVALID'
        }
        $lastIndexWait = $elapsed
        & $Progress 0 'bundle_index_wait'
        continue
      }
      if ($receipt.status -ceq 'COMPLETE') {
        if ($complete -or [int64]$receipt.files -ne $files -or $receipt.ack_sent -ne $false) { throw 'BUNDLE_TERMINAL_COUNTS_MISMATCH' }
        $complete = $true; continue
      }
      if ($complete -or $receipt.status -cne 'PACKAGE_VERIFIED') { throw 'BUNDLE_RECEIPT_SEQUENCE' }
      foreach ($field in @('inventory_generation_id','inventory_sha256','source_git_rev','collection_epoch_id','tile_registry_signature')) {
        if ([string]$receipt.generation.$field -cne [string]$Manifest.$field) { throw 'BUNDLE_RECEIPT_IDENTITY' }
      }
      if (@($receipt.members).Count -gt 256) { throw 'BUNDLE_MEMBER_LIMIT' }
      $reusedLocal = $false
      if ($receipt.PSObject.Properties.Name -contains 'reused_local') {
        if ($receipt.reused_local -isnot [bool]) { throw 'BUNDLE_REUSE_FLAG_INVALID' }
        $reusedLocal = $receipt.reused_local
      }
      foreach ($member in @($receipt.members)) {
        $rel = [string]$member.path
        if (-not $rows.ContainsKey($rel)) { throw 'BUNDLE_MEMBER_NOT_IN_MANIFEST' }
        $row = $rows[$rel]
        foreach ($field in @('size','inode','mtime_ns','consistency_mode')) {
          if ([string]$row.$field -cne [string]$member.$field) { throw 'BUNDLE_MEMBER_IDENTITY' }
        }
        $staged = [IO.Path]::GetFullPath([string]$member.staged_path)
        if (-not $reusedLocal -and -not $staged.StartsWith($stagePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'BUNDLE_STAGE_ESCAPE' }
        $destination = [IO.Path]::GetFullPath((Join-Path $mirror ($rel.Replace('/', '\'))))
        if (-not $destination.StartsWith(($mirror + '\'), [StringComparison]::OrdinalIgnoreCase)) { throw 'BUNDLE_DESTINATION_ESCAPE' }
        if ($reusedLocal -and -not $staged.Equals($destination, [StringComparison]::OrdinalIgnoreCase)) { throw 'BUNDLE_REUSE_PATH_MISMATCH' }
        if ([string]$member.sha256 -notmatch '^[0-9a-f]{64}$') { throw 'BUNDLE_MEMBER_HASH_MISSING' }
        Assert-FlyBundleUnlinkedPath -Path $staged
        Assert-FlyBundleUnlinkedPath -Path $destination
        if ((Get-Item -LiteralPath $staged -Force).Length -ne [int64]$row.size) { throw 'BUNDLE_STAGE_SIZE_MISMATCH' }
        if ((Get-FileHash -LiteralPath $staged -Algorithm SHA256).Hash.ToLowerInvariant() -cne [string]$member.sha256) { throw 'BUNDLE_STAGE_HASH_MISMATCH' }
        Test-MirrorCandidate -Path $staged -RelativePath $rel -ExpectedSize ([int64]$row.size)
        if (-not $reusedLocal) {
        $parent = Split-Path -Parent $destination
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        $candidate = $destination + '.' + [guid]::NewGuid().ToString('N') + '.download'
        try {
          [IO.File]::Copy($staged, $candidate, $false)
          Test-MirrorCandidate -Path $candidate -RelativePath $rel -ExpectedSize ([int64]$row.size)
          if ((Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash.ToLowerInvariant() -cne [string]$member.sha256) { throw 'BUNDLE_CANDIDATE_HASH_MISMATCH' }
          Publish-MirrorCandidate -Candidate $candidate -Destination $destination
        } finally {
          if (Test-Path -LiteralPath $candidate) { Remove-Item -LiteralPath $candidate -Force }
        }
        }
        $SyncState[$rel] = [ordered]@{ inode=[int64]$row.inode; size=[int64]$row.size;
          mtime_ns=[int64]$row.mtime_ns; synced_at=[DateTimeOffset]::UtcNow.ToString('o');
          full_sha256=[string]$member.sha256; transport='GENERATION_BOUND_BUNDLE' }
        $files += 1
      }
      & $SaveCheckpoint
      # These are transport scratch copies, not Fly evidence or the canonical
      # mirror. Reclaim only this verified package after durable checkpointing.
      if (-not $reusedLocal) {
      foreach ($member in @($receipt.members)) {
        $staged = [IO.Path]::GetFullPath([string]$member.staged_path)
        Assert-FlyBundleUnlinkedPath -Path $staged
        if (-not $staged.StartsWith($stagePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'BUNDLE_STAGE_ESCAPE' }
        Remove-Item -LiteralPath $staged -Force -ErrorAction Stop
        $empty = [IO.Path]::GetDirectoryName($staged)
        while ($empty.StartsWith($stagePrefix, [StringComparison]::OrdinalIgnoreCase)) {
          try { [IO.Directory]::Delete($empty, $false) } catch [IO.IOException] { break }
          $empty = [IO.Path]::GetDirectoryName($empty)
        }
      }
      if ($receipt.PSObject.Properties.Name -contains 'package_path') {
        $package = [IO.Path]::GetFullPath([string]$receipt.package_path)
        Assert-FlyBundleUnlinkedPath -Path $package
        if (-not $package.StartsWith($stagePrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'BUNDLE_PACKAGE_ESCAPE' }
        Remove-Item -LiteralPath $package -Force -ErrorAction Stop
      }
      }
      & $Progress $files 'bundle_verified'
    }
    if (-not $process.WaitForExit(5000)) { throw 'BUNDLE_CHILD_EXIT_TIMEOUT' }
    if ($process.ExitCode -ne 0 -or -not $complete) { throw 'BUNDLE_TERMINAL_RECEIPT_MISSING' }
    return [pscustomobject]@{ Files=$files; StagingRoot=$stage; AckSent=$false }
  } finally {
    if ($started -and -not $process.HasExited) { $process.Kill(); $process.WaitForExit(5000) | Out-Null }
    $process.Dispose()
    # Delete only an empty unique scratch directory; failed nonempty candidates
    # remain inspectable. No recursive deletion or source-byte cleanup here.
    try { [IO.Directory]::Delete($stage, $false) } catch [IO.IOException] { }
  }
}

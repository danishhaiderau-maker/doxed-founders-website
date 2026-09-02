function Test-MirrorCandidate {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$RelativePath,
    [Nullable[Int64]]$ExpectedSize = $null,
    [string]$ExpectedSha256 = $null
  )
  $normalizedRelativePath = $RelativePath.Replace("\", "/").Trim("/")
  $relativeParts = @($normalizedRelativePath.Split("/"))
  if (
    [string]::IsNullOrWhiteSpace($normalizedRelativePath) -or
    @($relativeParts | Where-Object { $_ -in @("", ".", "..") }).Count -gt 0
  ) {
    throw "Downloaded candidate has an invalid relative path: $RelativePath."
  }
  $name = $normalizedRelativePath.ToLowerInvariant()
  $opaqueCorruptEvidence = $name.StartsWith(
    "corrupt_evidence_quarantine/",
    [System.StringComparison]::Ordinal
  )
  if ($opaqueCorruptEvidence) {
    if ($relativeParts.Count -lt 2) {
      throw "Downloaded quarantine candidate has an invalid relative path: $RelativePath."
    }
    if ($null -eq $ExpectedSize -or [int64]$ExpectedSize -lt 0) {
      throw "Quarantine evidence manifest size is unavailable for $RelativePath."
    }
    $expectedHash = [string]$ExpectedSha256
    if ($expectedHash -notmatch '^[0-9a-fA-F]{64}$') {
      throw "Quarantine evidence manifest SHA-256 is unavailable for $RelativePath."
    }
    $candidateSize = [int64](Get-Item -LiteralPath $Path).Length
    if ($candidateSize -ne [int64]$ExpectedSize) {
      throw "Quarantine evidence manifest size mismatch for $RelativePath."
    }
    $candidateHash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash
    if (-not $candidateHash.Equals($expectedHash, [System.StringComparison]::OrdinalIgnoreCase)) {
      throw "Quarantine evidence manifest SHA-256 mismatch for $RelativePath."
    }
    # These are immutable forensic bytes. A corrupt or truncated JSONL payload
    # is the evidence being preserved, so semantic parsing would destroy the
    # quarantine contract. Size/hash binding above is the admission gate.
    return
  }
  # This legacy filename is an append-only newline-delimited crash journal,
  # not one JSON document. Validating the whole file as JSON stalls the mirror
  # as soon as a second crash record is appended.
  if ($name -eq "crash_dump.json") {
    $name = "crash_dump.jsonl"
  }
  if ($name -match '\.json$') {
    try {
      $null = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
      throw "Downloaded JSON candidate is invalid for ${RelativePath}: $($_.Exception.Message)"
    }
    return
  }
  if ($name -match '\.jsonl(?:\.\d+)?$') {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
      if ($stream.Length -eq 0) { return }
      $stream.Seek(-1, [System.IO.SeekOrigin]::End) | Out-Null
      if ($stream.ReadByte() -ne 10) {
        throw "Downloaded JSONL candidate has an incomplete final record for $RelativePath."
      }
      $stream.Seek(0, [System.IO.SeekOrigin]::Begin) | Out-Null
      $reader = [System.IO.StreamReader]::new($stream, [System.Text.UTF8Encoding]::new($false, $true), $true, 65536, $true)
      try {
        $lineNumber = 0
        while (($line = $reader.ReadLine()) -ne $null) {
          $lineNumber += 1
          if ([string]::IsNullOrWhiteSpace($line)) { continue }
          try { $null = $line | ConvertFrom-Json } catch {
            throw "Downloaded JSONL candidate has invalid JSON at ${RelativePath}:${lineNumber}."
          }
        }
      } finally { $reader.Dispose() }
    } finally { $stream.Dispose() }
    return
  }
  if ($name -match '\.csv(?:\.\d+)?$') {
    $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::Read)
    try {
      if ($stream.Length -eq 0) { return }
      $stream.Seek(-1, [System.IO.SeekOrigin]::End) | Out-Null
      if ($stream.ReadByte() -ne 10) {
        throw "Downloaded CSV candidate has an incomplete final record for $RelativePath."
      }
      $stream.Seek(0, [System.IO.SeekOrigin]::Begin) | Out-Null
      $reader = [System.IO.StreamReader]::new($stream, [System.Text.UTF8Encoding]::new($false, $true), $true, 65536, $true)
      try {
        $header = $reader.ReadLine()
        if ([string]::IsNullOrWhiteSpace($header) -or $header.IndexOf(',') -lt 0) {
          throw "Downloaded CSV candidate has no usable header for $RelativePath."
        }
      } finally { $reader.Dispose() }
    } finally { $stream.Dispose() }
  }
}

function Publish-MirrorCandidate {
  param(
    [Parameter(Mandatory = $true)][string]$Candidate,
    [Parameter(Mandatory = $true)][string]$Destination,
    [int]$ReplaceAttempts = 12
  )
  $backup = "$Candidate.replace-backup"
  try {
    if (Test-Path -LiteralPath $Destination) {
      Invoke-MirrorAtomicReplace `
        -Candidate $Candidate `
        -Destination $Destination `
        -Backup $backup `
        -Attempts $ReplaceAttempts
    } else {
      [System.IO.File]::Move($Candidate, $Destination)
    }
  } finally {
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
  }
}

function Invoke-MirrorAtomicReplace {
  param(
    [Parameter(Mandatory = $true)][string]$Candidate,
    [Parameter(Mandatory = $true)][string]$Destination,
    [Parameter(Mandatory = $true)][string]$Backup,
    [int]$Attempts = 12
  )
  $boundedAttempts = [Math]::Max(1, [Math]::Min(20, $Attempts))
  for ($attempt = 1; $attempt -le $boundedAttempts; $attempt++) {
    try {
      [System.IO.File]::Replace($Candidate, $Destination, $Backup, $true)
      return
    } catch [System.IO.IOException] {
      if ($attempt -ge $boundedAttempts) {
        throw [System.IO.IOException]::new(
          "Atomic mirror publish failed after $attempt attempt(s): destination=$Destination candidate=$Candidate error=$($_.Exception.Message)",
          $_.Exception
        )
      }
      # Readers of a large immutable snapshot can briefly hold a Windows file
      # handle without FILE_SHARE_DELETE. Keep the validated candidate and the
      # prior mirror intact, then retry only the atomic publish operation.
      Start-Sleep -Milliseconds ([Math]::Min(2000, 100 * [Math]::Pow(2, $attempt - 1)))
    }
  }
}

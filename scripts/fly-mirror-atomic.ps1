function Test-MirrorCandidate {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$RelativePath
  )
  $name = $RelativePath.ToLowerInvariant()
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
    [Parameter(Mandatory = $true)][string]$Destination
  )
  $backup = "$Candidate.replace-backup"
  try {
    if (Test-Path -LiteralPath $Destination) {
      [System.IO.File]::Replace($Candidate, $Destination, $backup, $true)
    } else {
      [System.IO.File]::Move($Candidate, $Destination)
    }
  } finally {
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
  }
}

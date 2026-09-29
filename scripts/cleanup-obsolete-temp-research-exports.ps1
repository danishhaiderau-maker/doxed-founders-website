[CmdletBinding()]
param(
    [switch]$Execute
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# This script intentionally defaults to a read-only dry run.  It cleans only the
# exact, independently verified Temp exports described below and fails closed if
# the machine no longer matches that evidence.
$TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
$ExpectedTempRoot = 'C:\Users\danis\AppData\Local\Temp'
$CanonicalRepo = 'C:\DoxxedCrypto\btc-v31-current'
$ProtectedRoots = @(
    $CanonicalRepo,
    'C:\DoxxedCrypto\btc-v31-current-mirror',
    'C:\DoxxedCrypto\quarantine'
)

$RetainedArchive = Join-Path $TempRoot 'research-download-9f72f760a2e64bb38113b33fee8f17e8.zip'
$RetainedArchiveSha256 = 'E36D3545F21DD85FB57C0B460BA66B277FFA0DAACC1EEEB9B9E862D6B5408FF9'
$ExpectedLoose1391Count = 1391
$ExpectedLoose1391Bytes = [int64]3883968100
$ExpectedLoose461Count = 461
$ExpectedLoose461Bytes = [int64]519998123
$ExpectedOldSourceCount = 461
$ExpectedOldSourceBytes = [int64]519998123
$ExpectedTotalEligibleBytes = [int64]9990064461
$ExpectedWholeDirectoryFacts = @{
    'b5b82a2b-5427-4ddc-b2ef-57e0780a0aa8_complete_research_evidence_bundle_20260827_030209.zip.aa8' = @(714, [int64]1055319097)
    '61d750b6-7bd1-4563-8afa-f2ab885cb5cf_complete_research_evidence_bundle_20260827_030209.zip.5cf' = @(714, [int64]1055319097)
    '466f7e11-778f-4719-b3ba-d559dc76731d_complete_research_evidence_bundle_20260827_030209.zip.31d' = @(668, [int64]1050922225)
    'acd9d733-e414-4f5b-b8d3-c013218fe6c7_complete_research_evidence_bundle_20260821_200630.zip.6c7' = @(461, [int64]519998123)
    'doxed-deploy-6c6ea2dd' = @(2115, [int64]50328604)
    'doxed-deploy-df0eab57' = @(2135, [int64]50534517)
    'doxed-deploy-e33f0fbc' = @(2115, [int64]50328609)
    'doxed-deploy-f22bcb93' = @(2143, [int64]50660701)
    'doxed-fly-deploy-7ba198ad' = @(2165, [int64]51212615)
    'founder-ide-next-tests' = @(14959, [int64]570333450)
    'FounderModelVerification-deepseek-r1-20260827' = @(6, [int64]328450244)
    'vscode-stable-user-x64-fts7k15zc7m' = @(2, [int64]197603076)
    'codex-download-everything-audit-20260828-002932' = @(1, [int64]160591231)
}
$ExpectedWholeFileFacts = @{
    'codex-essential-bundle-final.zip' = @([int64]132376647, 'D81EDA7B575A5B13CB767E707E26F4701297B2F7AD39B88F38A92F46D934D1C8')
    'codex-final-complete-bundle-5554aea3660245c48fa548f4dc18f424.zip' = @([int64]132256938, '7BD71899DD8D905EA861DFDE6BAA114B9665E0C6481FC615055641D1BE198E9F')
    'codex-current-complete-research-bundle.zip' = @([int64]129863064, '9A1D4863D4592D64A97E9D2B2A4E04C32C5AF328F74706501DD8B14F0994DBBA')
}

function Get-CanonicalPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd('\')
}

function Assert-UnderTemp([string]$Path) {
    $full = Get-CanonicalPath $Path
    if ($full -eq $TempRoot -or -not $full.StartsWith($TempRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Candidate escapes the exact Temp root: $full"
    }
    foreach ($protected in $ProtectedRoots) {
        $guard = Get-CanonicalPath $protected
        if ($full -eq $guard -or $full.StartsWith($guard + '\', [StringComparison]::OrdinalIgnoreCase)) {
            throw "Candidate intersects protected storage: $full"
        }
    }
    return $full
}

function Assert-NoReparsePoint([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Reparse point is never eligible: $Path"
    }
    if ($item.PSIsContainer) {
        $hit = Get-ChildItem -LiteralPath $Path -Force -Recurse | Where-Object {
            ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        } | Select-Object -First 1
        if ($null -ne $hit) { throw "Candidate contains reparse point: $($hit.FullName)" }
    }
}

function Resolve-OneDirectory([string]$Prefix, [string]$Suffix) {
    $matches = @(Get-ChildItem -LiteralPath $TempRoot -Force -Directory | Where-Object {
        $_.Name.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase) -and
        $_.Name.EndsWith($Suffix, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($matches.Count -ne 1) { throw "Expected exactly one directory matching '$Prefix...$Suffix'; found $($matches.Count)." }
    return $matches[0].FullName
}

function Resolve-OneFile([string]$Name) {
    $matches = @(Get-ChildItem -LiteralPath $TempRoot -Force -File | Where-Object {
        $_.Name.Equals($Name, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($matches.Count -ne 1) { throw "Expected exactly one file named '$Name'; found $($matches.Count)." }
    return $matches[0].FullName
}

function Get-TreeFacts([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    if (-not $item.PSIsContainer) {
        return [pscustomobject]@{ Count = 1; Bytes = [int64]$item.Length }
    }
    $files = @(Get-ChildItem -LiteralPath $Path -Force -File -Recurse)
    return [pscustomobject]@{
        Count = $files.Count
        Bytes = [int64](($files | Measure-Object -Property Length -Sum).Sum)
    }
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToUpperInvariant()
}

function Normalize-BrowserName([string]$Name) {
    # Browser downloads append " (N)" immediately before the final extension.
    return [regex]::Replace($Name, ' \([0-9]+\)(?=\.[^.]+$)', '')
}

function Add-IndexValue([hashtable]$Index, [string]$Name, [int64]$Length, [string]$Hash) {
    $key = '{0}|{1}|{2}' -f $Name.ToLowerInvariant(), $Length, $Hash.ToUpperInvariant()
    $Index[$key] = $true
}

function New-ZipIndex([string]$ZipPath) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $index = @{}
    $zip = [IO.Compression.ZipFile]::OpenRead($ZipPath)
    try {
        foreach ($entry in $zip.Entries) {
            if ([string]::IsNullOrEmpty($entry.Name)) { continue }
            $sha = [Security.Cryptography.SHA256]::Create()
            $stream = $entry.Open()
            try { $hash = ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '') }
            finally { $stream.Dispose(); $sha.Dispose() }
            Add-IndexValue $index $entry.Name ([int64]$entry.Length) $hash
        }
    }
    finally { $zip.Dispose() }
    return $index
}

function New-DirectoryIndex([string]$Directory) {
    $index = @{}
    foreach ($file in Get-ChildItem -LiteralPath $Directory -Force -File -Recurse) {
        Add-IndexValue $index $file.Name ([int64]$file.Length) (Get-Sha256 $file.FullName)
    }
    return $index
}

function Select-VerifiedLooseFiles(
    [datetime]$FromInclusive,
    [datetime]$UntilExclusive,
    [hashtable]$Index,
    [int]$ExpectedCount,
    [int64]$ExpectedBytes,
    [string]$Label
) {
    $selected = @()
    foreach ($file in Get-ChildItem -LiteralPath $TempRoot -Force -File) {
        if ($file.LastWriteTime -lt $FromInclusive -or $file.LastWriteTime -ge $UntilExclusive) { continue }
        $normalized = Normalize-BrowserName $file.Name
        $hash = Get-Sha256 $file.FullName
        $key = '{0}|{1}|{2}' -f $normalized.ToLowerInvariant(), ([int64]$file.Length), $hash
        if ($Index.ContainsKey($key)) { $selected += $file }
    }
    $bytes = [int64](($selected | Measure-Object -Property Length -Sum).Sum)
    if ($selected.Count -ne $ExpectedCount -or $bytes -ne $ExpectedBytes) {
        throw "$Label drifted: expected $ExpectedCount files/$ExpectedBytes bytes; found $($selected.Count)/$bytes."
    }
    foreach ($file in $selected) {
        Assert-UnderTemp $file.FullName | Out-Null
        Assert-NoReparsePoint $file.FullName
    }
    return @($selected)
}

function Assert-NoProcessReferences([string[]]$Paths) {
    $processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    foreach ($path in $Paths) {
        foreach ($process in $processes) {
            $command = [string]$process.CommandLine
            $executable = [string]$process.ExecutablePath
            if (($command -and $command.IndexOf($path, [StringComparison]::OrdinalIgnoreCase) -ge 0) -or
                ($executable -and $executable.IndexOf($path, [StringComparison]::OrdinalIgnoreCase) -ge 0)) {
                throw "PID $($process.ProcessId) references cleanup candidate: $path"
            }
        }
    }
}

if ($TempRoot -ne $ExpectedTempRoot) {
    throw "Unexpected Temp root '$TempRoot'; expected '$ExpectedTempRoot'."
}
if (-not (Test-Path -LiteralPath $RetainedArchive -PathType Leaf)) { throw "Retained proof archive is missing." }
if ((Get-Sha256 $RetainedArchive) -ne $RetainedArchiveSha256) { throw "Retained proof archive SHA-256 mismatch." }
Assert-UnderTemp $RetainedArchive | Out-Null
Assert-NoReparsePoint $RetainedArchive

# Resolve whole-directory candidates uniquely using their immutable UUID prefix
# and exported-bundle suffix.  The .5cf extraction is intentionally deleted;
# the smaller SHA-pinned research-download archive above is intentionally kept.
$OldSource = Resolve-OneDirectory 'acd9d733-e414-4f5b-b8d3-c013218fe6c7_' '.zip.6c7'
$WholeDirectories = @(
    (Resolve-OneDirectory 'b5b82a2b-5427-4ddc-b2ef-57e0780a0aa8_' '.zip.aa8'),
    (Resolve-OneDirectory '61d750b6-7bd1-4563-8afa-f2ab885cb5cf_' '.zip.5cf'),
    (Resolve-OneDirectory '466f7e11-778f-4719-b3ba-d559dc76731d_' '.zip.31d'),
    $OldSource,
    (Resolve-OneDirectory 'doxed-deploy-6c6ea2dd' 'doxed-deploy-6c6ea2dd'),
    (Resolve-OneDirectory 'doxed-deploy-df0eab57' 'doxed-deploy-df0eab57'),
    (Resolve-OneDirectory 'doxed-deploy-e33f0fbc' 'doxed-deploy-e33f0fbc'),
    (Resolve-OneDirectory 'doxed-deploy-f22bcb93' 'doxed-deploy-f22bcb93'),
    (Resolve-OneDirectory 'doxed-fly-deploy-7ba198ad' 'doxed-fly-deploy-7ba198ad'),
    (Resolve-OneDirectory 'founder-ide-next-tests' 'founder-ide-next-tests'),
    (Resolve-OneDirectory 'FounderModelVerification-deepseek-r1-20260827' 'FounderModelVerification-deepseek-r1-20260827'),
    (Resolve-OneDirectory 'vscode-stable-user-x64-fts7k15zc7m' 'vscode-stable-user-x64-fts7k15zc7m'),
    (Resolve-OneDirectory 'codex-download-everything-audit-20260828-002932' 'codex-download-everything-audit-20260828-002932')
)
$WholeFiles = @(
    (Resolve-OneFile 'codex-essential-bundle-final.zip'),
    (Resolve-OneFile 'codex-final-complete-bundle-5554aea3660245c48fa548f4dc18f424.zip'),
    (Resolve-OneFile 'codex-current-complete-research-bundle.zip')
)

foreach ($path in @($WholeDirectories + $WholeFiles)) {
    Assert-UnderTemp $path | Out-Null
    Assert-NoReparsePoint $path
}

foreach ($path in $WholeDirectories) {
    $name = Split-Path -Leaf $path
    if (-not $ExpectedWholeDirectoryFacts.ContainsKey($name)) { throw "No exact facts registered for $path" }
    $facts = Get-TreeFacts $path
    $expected = $ExpectedWholeDirectoryFacts[$name]
    if ($facts.Count -ne [int]$expected[0] -or $facts.Bytes -ne [int64]$expected[1]) {
        throw "Whole directory drifted: $path expected $($expected[0])/$($expected[1]); found $($facts.Count)/$($facts.Bytes)."
    }
}
foreach ($path in $WholeFiles) {
    $name = Split-Path -Leaf $path
    if (-not $ExpectedWholeFileFacts.ContainsKey($name)) { throw "No exact facts registered for $path" }
    $expected = $ExpectedWholeFileFacts[$name]
    $item = Get-Item -LiteralPath $path -Force
    $hash = Get-Sha256 $path
    if ($item.Length -ne [int64]$expected[0] -or $hash -ne [string]$expected[1]) {
        throw "Whole file drifted: $path expected $($expected[0])/$($expected[1]); found $($item.Length)/$hash."
    }
}

$oldFacts = Get-TreeFacts $OldSource
if ($oldFacts.Count -ne $ExpectedOldSourceCount -or $oldFacts.Bytes -ne $ExpectedOldSourceBytes) {
    throw "Old extraction source drifted: expected $ExpectedOldSourceCount/$ExpectedOldSourceBytes; found $($oldFacts.Count)/$($oldFacts.Bytes)."
}

$zipIndex = New-ZipIndex $RetainedArchive
$Loose1391 = @(Select-VerifiedLooseFiles ([datetime]'2026-08-27T13:03:00') ([datetime]'2026-08-27T13:07:00') $zipIndex $ExpectedLoose1391Count $ExpectedLoose1391Bytes 'New loose group')
$hexSubset = @($Loose1391 | Where-Object { $_.Name -match '^[0-9a-f]{64}( \([0-9]+\))?\.json$' })
if ($hexSubset.Count -ne 523) { throw "Expected the independently observed 523 hash-named JSON subset; found $($hexSubset.Count)." }

$oldIndex = New-DirectoryIndex $OldSource
$Loose461 = @(Select-VerifiedLooseFiles ([datetime]'2026-08-22T06:07:00') ([datetime]'2026-08-22T06:08:00') $oldIndex $ExpectedLoose461Count $ExpectedLoose461Bytes 'Old loose group')
$excludedWav = Join-Path $TempRoot '20a49cab-34ad-4c13-b7b3-75db98e3c98c.tmp.wav'
if (@($Loose461 | Where-Object { $_.FullName -eq $excludedWav }).Count -ne 0) { throw 'Unrelated WAV was selected.' }

$loosePaths = @($Loose1391.FullName + $Loose461.FullName)
if (@($loosePaths | Group-Object | Where-Object Count -gt 1).Count -ne 0) { throw 'Loose candidate groups overlap.' }
$allPaths = @($WholeDirectories + $WholeFiles + $loosePaths)
Assert-NoProcessReferences $allPaths

$wholeBytes = [int64]0
foreach ($path in @($WholeDirectories + $WholeFiles)) { $wholeBytes += (Get-TreeFacts $path).Bytes }
$totalBytes = $wholeBytes + $ExpectedLoose1391Bytes + $ExpectedLoose461Bytes
if ($totalBytes -ne $ExpectedTotalEligibleBytes) {
    throw "Deletion manifest byte total drifted: expected $ExpectedTotalEligibleBytes; found $totalBytes."
}

$modeLabel = if ($Execute) { 'EXECUTION REQUESTED' } else { 'DRY RUN (default)' }
Write-Host $modeLabel
Write-Host "Retained proof: $RetainedArchive ($RetainedArchiveSha256)"
Write-Host "Verified loose groups: 1391/$ExpectedLoose1391Bytes bytes and 461/$ExpectedLoose461Bytes bytes"
Write-Host "Whole targets: $($WholeDirectories.Count) directories and $($WholeFiles.Count) files"
Write-Host "Total eligible bytes: $totalBytes"
$allPaths | Sort-Object | ForEach-Object { Write-Host "ELIGIBLE: $_" }

if (-not $Execute) {
    Write-Host 'No files were removed. Re-run with -Execute only after reviewing this complete manifest.'
    exit 0
}

# Removal is deliberately explicit and occurs only after every candidate has
# passed every guard.  LiteralPath prevents wildcard expansion.
foreach ($file in @($Loose1391 + $Loose461)) {
    Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
}
foreach ($file in $WholeFiles) {
    Remove-Item -LiteralPath $file -Force -ErrorAction Stop
}
foreach ($directory in $WholeDirectories) {
    Remove-Item -LiteralPath $directory -Recurse -Force -ErrorAction Stop
}

Write-Host "Cleanup completed: $totalBytes verified bytes were selected."

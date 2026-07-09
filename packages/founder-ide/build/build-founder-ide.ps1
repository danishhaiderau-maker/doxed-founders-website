# build-founder-ide.ps1
#
# PowerShell wrapper for build/build-founder-ide.sh. Invokes Git Bash so the
# VSCodium build (which requires POSIX sed/grep/find) works on Windows.
#
# Usage (from a VSCodium downstream checkout root):
#   .\build\build-founder-ide.ps1
#   .\build\build-founder-ide.ps1 -ExtensionVsix C:\path\to\founder-ide-extension.vsix
#   .\build\build-founder-ide.ps1 -SkipPrepare
#
# Requires Git for Windows (Git Bash) at the default install path, or
# $env:GIT_BASH pointing at bash.exe.

[CmdletBinding()]
param(
    [string]$ExtensionVsix = "",
    [string]$BuildFlags    = "",
    [switch]$SkipPrepare
)

$ErrorActionPreference = "Stop"

# Locate Git Bash.
$gitBash = $env:GIT_BASH
if (-not $gitBash) {
    $candidates = @(
        "C:\Program Files\Git\bin\bash.exe",
        "C:\Program Files (x86)\Git\bin\bash.exe"
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { $gitBash = $c; break }
    }
}
if (-not $gitBash -or -not (Test-Path $gitBash)) {
    throw "Git Bash not found. Install Git for Windows (winget install --id Git.Git -e) or set `$env:GIT_BASH."
}
Write-Host "[build-founder-ide.ps1] Git Bash: $gitBash"

# Resolve the build script path relative to this file.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildSh   = Join-Path $scriptDir "build-founder-ide.sh"
if (-not (Test-Path $buildSh)) { throw "Missing: $buildSh" }

# Convert to a path Git Bash understands (forward slashes, /c/...).
function ConvertTo-BashPath([string]$p) {
    $p = (Resolve-Path $p).Path
    $p = $p -replace '\\','/'
    if ($p -match '^([A-Za-z]):/?(.*)') { $p = '/' + $matches[1].ToLower() + '/' + $matches[2] }
    return $p
}

$buildShBash   = ConvertTo-BashPath $buildSh
$extensionArg  = ""
if ($ExtensionVsix) {
    $env:EXTENSION_VSIX = (Resolve-Path $ExtensionVsix).Path
    $extensionArg = $env:EXTENSION_VSIX
}
if ($SkipPrepare)   { $env:SKIP_PREPARE = "1" }
if ($BuildFlags)    { $env:VSCODIUM_BUILD_FLAGS = $BuildFlags }

Write-Host "[build-founder-ide.ps1] invoking: bash $buildShBash"
Write-Host "[build-founder-ide.ps1] EXTENSION_VSIX=$extensionArg  SKIP_PREPARE=$SkipPrepare  BUILD_FLAGS=$BuildFlags"

& $gitBash -lc "bash '$buildShBash'"
$exit = $LASTEXITCODE
if ($exit -ne 0) { throw "build-founder-ide.sh exited with code $exit" }

Write-Host "[build-founder-ide.ps1] done"

# build-stack-installer.ps1
#
# Orchestrates the complete Founder IDE build:
#   1. Build the Founder OS chat extension .vsix (packages/founder-ide-extension)
#   2. Build the Founder IDE application payload
#   3. Build and embed the Founder relay, then create the IDE installer
#   4. Compose the mode-aware Founder-IDE-Setup-<v>.exe bootstrapper
#
# Founder Node is an internal runtime of Founder IDE. Its unpacked Electron
# payload is copied under resources/founder-relay before the IDE installer is
# created. Users install, launch, update, and uninstall one application.
#
# This script is the entry point for producing a downloadable installer. It
# does NOT clone VSCodium (one-time setup on the build machine - see
# RELEASES.md). It expects to be run from a VSCodium downstream checkout that
# has the monorepo's packages/founder-ide/ layered in.
#
# Usage:
#   .\packages\founder-ide\installer\build-stack-installer.ps1
#   .\packages\founder-ide\installer\build-stack-installer.ps1 -SkipIdeBuild
#
# Env / params:
#   -MonorepoRoot        - path to the Founder OS monorepo (default: detected up from this script)
#   -VscodiumCheckout    - path to the VSCodium downstream checkout (default: current dir)
#   -Version             - Founder IDE version (default: 0.1.0)
#   -SkipExtensionBuild  - skip step 1 (you already built the .vsix)
#   -SkipIdeBuild        - skip step 2 (you already built Founder IDE)
#   -SkipFounderNodeBuild - reuse apps/founder-node/release/win-unpacked
#   -IsccPath            - path to iscc.exe (default: auto-detect)

[CmdletBinding()]
param(
    [string]$MonorepoRoot     = "",
    [string]$VscodiumCheckout = (Get-Location).Path,
    [string]$Version          = "0.9.4",
    [switch]$SkipExtensionBuild,
    [switch]$SkipIdeBuild,
    [switch]$SkipFounderNodeBuild,
    [string]$IsccPath         = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"

# --- Locate the monorepo root ------------------------------------------------
if (-not $MonorepoRoot) {
    $p = Split-Path -Parent $MyInvocation.MyCommand.Path
    for ($i=0; $i -lt 5 -and $p; $i++) {
        if (Test-Path (Join-Path $p "package.json")) { $MonorepoRoot = $p; break }
        $p = Split-Path -Parent $p
    }
    if (-not $MonorepoRoot) { throw "Could not locate monorepo root (no package.json walking up from script)." }
}
Write-Host "[stack] monorepo root: $MonorepoRoot"
Write-Host "[stack] vscodium checkout: $VscodiumCheckout"
Write-Host "[stack] version: $Version"

# --- Locate iscc (Inno Setup compiler) ---------------------------------------
if (-not $IsccPath) {
    $candidates = @(
        "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        "C:\Program Files\Inno Setup 6\ISCC.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { $IsccPath = $c; break } }
    if (-not $IsccPath) {
        $IsccPath = (Get-Command iscc -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
    }
}
if (-not $IsccPath -or -not (Test-Path $IsccPath)) {
    throw "Inno Setup (iscc.exe) not found. Install: winget install --id JRSoftware.InnoSetup -e"
}
Write-Host "[stack] iscc: $IsccPath"

# --- Staging dir -------------------------------------------------------------
$staging = Join-Path $VscodiumCheckout "staging"
New-Item -ItemType Directory -Force -Path $staging | Out-Null
Write-Host "[stack] staging: $staging"

# --- Step 1: build the chat extension .vsix ---------------------------------
if (-not $SkipExtensionBuild) {
    Write-Host "`n[stack] STEP 1/4 - building Founder OS chat extension .vsix" -ForegroundColor Cyan
    $extDir = Join-Path $MonorepoRoot "packages\founder-ide-extension"
    if (-not (Test-Path (Join-Path $extDir "package.json"))) {
        throw "Extension not found at $extDir"
    }
    Push-Location $extDir
    try {
        if (-not (Test-Path "node_modules")) {
            Write-Host "[stack]   npm install (extension)"
            npm install --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "npm install (extension) failed" }
        }
        Write-Host "[stack]   npm run package"
        npm run package
        if ($LASTEXITCODE -ne 0) { throw "npm run package failed" }
        $vsix = Get-ChildItem -Path "." -Filter "founder-ide-extension-*.vsix" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        if (-not $vsix) { throw "No .vsix produced in $extDir" }
        $vsixDest = Join-Path $staging "founder-ide-extension.vsix"
        Copy-Item $vsix.FullName $vsixDest -Force
        Write-Host "[stack]   -> $vsixDest"
    } finally { Pop-Location }
} else {
    Write-Host "`n[stack] STEP 1/4 - SKIPPED (SkipExtensionBuild)" -ForegroundColor DarkGray
    $vsixDest = Join-Path $staging "founder-ide-extension.vsix"
    if (-not (Test-Path $vsixDest)) { throw "SkipExtensionBuild set but $vsixDest not staged." }
}

# --- Step 2: build Founder IDE application payload ---------------------------
$ideSetup = Join-Path $staging "Founder-IDE-Setup-x64.exe"
$ideRoot = Join-Path $VscodiumCheckout "VSCode-win32-x64"
if (-not $SkipIdeBuild) {
    Write-Host "`n[stack] STEP 2/4 - building Founder IDE (VSCodium downstream)" -ForegroundColor Cyan
    $buildPs1 = Join-Path $VscodiumCheckout "build\build-founder-ide.ps1"
    if (-not (Test-Path $buildPs1)) {
        throw "build-founder-ide.ps1 not found at $buildPs1 (is VscodiumCheckout correct?)"
    }
    & $buildPs1 -ExtensionVsix $vsixDest
    if ($LASTEXITCODE -ne 0) { throw "build-founder-ide.ps1 failed (exit $LASTEXITCODE)" }

} else {
    Write-Host "`n[stack] STEP 2/4 - SKIPPED (SkipIdeBuild)" -ForegroundColor DarkGray
}
if (-not (Test-Path (Join-Path $ideRoot "Founder IDE.exe"))) {
    throw "Founder IDE application payload not found at $ideRoot"
}

# Embed the Founder extension into the application payload even when the
# expensive IDE compilation is skipped. A staged VSIX alone is not installed
# on a clean machine by the inner setup executable.
$builtinExtension = Join-Path $ideRoot "resources\app\extensions\founder-ide-extension"
$extensionUnpack = Join-Path $staging "founder-ide-extension-unpacked"
if (Test-Path $extensionUnpack) { Remove-Item $extensionUnpack -Recurse -Force }
New-Item -ItemType Directory -Path $extensionUnpack -Force | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($vsixDest, $extensionUnpack)
$unpackedExtension = Join-Path $extensionUnpack "extension"
if (-not (Test-Path (Join-Path $unpackedExtension "package.json"))) {
    throw "Staged Founder extension VSIX is malformed: $vsixDest"
}
if (Test-Path $builtinExtension) { Remove-Item $builtinExtension -Recurse -Force }
Copy-Item $unpackedExtension $builtinExtension -Recurse -Force
Remove-Item $extensionUnpack -Recurse -Force
Write-Host "[stack]   embedded Founder extension -> $builtinExtension"

# Patch the compiled shell after the downstream build and before installer
# packaging. Both scripts fail closed if an upstream minified signature moves.
$ideAppRoot = Join-Path $ideRoot "resources\app"
foreach ($patchName in @("patch-founder-settings-entry.py", "patch-founder-native-ai.py")) {
    $patchScript = Join-Path $MonorepoRoot "packages\founder-ide\scripts\$patchName"
    if (-not (Test-Path $patchScript)) { throw "Founder IDE shell patch not found: $patchScript" }
    Write-Host "[stack]   applying $patchName"
    python $patchScript --app $ideAppRoot
    if ($LASTEXITCODE -ne 0) { throw "$patchName failed (exit $LASTEXITCODE)" }
}

# --- Step 3: build and embed the Founder relay -------------------------------
$nodeDir = Join-Path $MonorepoRoot "apps\founder-node"
$relayRoot = Join-Path $nodeDir "release\win-unpacked"
if (-not $SkipFounderNodeBuild) {
    Write-Host "`n[stack] STEP 3/4 - building and embedding Founder relay" -ForegroundColor Cyan
    if (-not (Test-Path (Join-Path $nodeDir "package.json"))) {
        throw "Founder Node not found at $nodeDir"
    }
    Push-Location $nodeDir
    try {
        if (-not (Test-Path "node_modules")) {
            Write-Host "[stack]   npm ci (founder-node)"
            npm ci --no-audit --no-fund
            if ($LASTEXITCODE -ne 0) { throw "npm ci (founder-node) failed" }
        }
        Write-Host "[stack]   npm run build"
        npm run build
        if ($LASTEXITCODE -ne 0) { throw "Founder Node build failed" }
        # Disable electron-builder's auto-discovery so it doesn't try to sign
        # with a cert that isn't present in CI. Signing is done by a later
        # dedicated step (Azure Trusted Signing) on the outer bundle.
        $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
        # electron-builder --dir can retain files from a previous unpacked
        # payload. Start clean so renamed or retired assets cannot survive in
        # the one-app installer after their source references are removed.
        if (Test-Path $relayRoot) {
            Write-Host "[stack]   removing stale Founder relay payload"
            Remove-Item $relayRoot -Recurse -Force
        }
        Write-Host "[stack]   electron-builder --win --dir"
        npx electron-builder --win --x64 --dir --publish never
        if ($LASTEXITCODE -ne 0) { throw "electron-builder --win failed (exit $LASTEXITCODE)" }
    } finally { Pop-Location }
} else {
    Write-Host "`n[stack] STEP 3/4 - SKIPPED (SkipFounderNodeBuild)" -ForegroundColor DarkGray
}

if (-not (Test-Path (Join-Path $relayRoot "Founder Node.exe"))) {
    throw "Founder relay payload not found at $relayRoot"
}
$embedScript = Join-Path $MonorepoRoot "packages\founder-ide\scripts\embed-founder-relay.ps1"
& $embedScript -IdeRoot $ideRoot -RelayRoot $relayRoot

# The relay must be embedded before gulp packages the inner installer.
$vscodeSource = Join-Path $VscodiumCheckout "VSCode"
Push-Location $vscodeSource
try {
    # Inno prints one line per compressed file (thousands of lines for the
    # Electron payload). Capture that noise so CI and agent shells do not
    # terminate an otherwise healthy compiler when their output buffer fills.
    $packagingLog = Join-Path $staging "founder-ide-inner-installer.log"
    & cmd.exe /d /c "npx.cmd gulp vscode-win32-x64-user-setup > `"$packagingLog`" 2>&1"
    $packagingExit = $LASTEXITCODE
    Get-Content $packagingLog -Tail 30
    if ($packagingExit -ne 0) {
        throw "Founder IDE installer packaging failed (exit $packagingExit; log: $packagingLog)"
    }
} finally { Pop-Location }

$innerDir = Join-Path $vscodeSource ".build\win32-x64\user-setup"
$candidate = Get-ChildItem -Path $innerDir -Filter "FounderIDESetup.exe" -ErrorAction SilentlyContinue |
             Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $candidate) { throw "Founder IDE setup .exe not found in $innerDir after packaging." }
Copy-Item $candidate.FullName $ideSetup -Force
Write-Host "[stack]   one-app installer payload -> $ideSetup"

# --- Step 4: compose the mode-aware Founder IDE bootstrapper ------------------
Write-Host "`n[stack] STEP 4/4 - composing Founder IDE installer via Inno Setup" -ForegroundColor Cyan
$iss = Join-Path $VscodiumCheckout "installer\founder-stack.iss"
if (-not (Test-Path $iss)) {
    # Fall back to the monorepo copy if the checkout doesn't have it.
    $iss = Join-Path $MonorepoRoot "packages\founder-ide\installer\founder-stack.iss"
}
if (-not (Test-Path $iss)) { throw "founder-stack.iss not found." }

# Run iscc with our staging paths + version. iscc resolves #define paths
# relative to the .iss file, so pass absolute paths. Use FORWARD SLASHES in
# the ISCC defines: ISCC's #define substitution treats `\` as an escape inside
# string literals (e.g. `\D:` is read as a "filename prefix"), so Windows-style
# backslash paths produce "Unknown filename prefix" compile errors.
$ideSetupAbs = ((Resolve-Path $ideSetup).Path) -replace '\\','/'

& $IsccPath `
    "/DFOUNDER_STACK_VERSION=$Version" `
    "/DFOUNDER_IDE_SETUP=`"$ideSetupAbs`"" `
    $iss
if ($LASTEXITCODE -ne 0) { throw "iscc failed (exit $LASTEXITCODE)" }

# Locate the produced bundle. OutputDir in founder-stack.iss is relative to
# the script location, while downstream builds may carry their own dist dir.
$bundleDirs = @(
    (Join-Path $VscodiumCheckout "dist"),
    (Join-Path (Split-Path -Parent $iss) "dist"),
    (Split-Path -Parent $iss)
) | Select-Object -Unique
$bundle = $bundleDirs |
          Where-Object { Test-Path $_ } |
          ForEach-Object {
              Get-ChildItem -Path $_ -Filter "Founder-IDE-Setup-*.exe" -ErrorAction SilentlyContinue
          } |
          Sort-Object LastWriteTime -Descending |
          Select-Object -First 1
if (-not $bundle) {
    throw "Founder IDE installer not found after iscc. Searched: $($bundleDirs -join ', ')"
}

Write-Host "`n[stack] DONE - one Founder IDE installer:" -ForegroundColor Green
Write-Host "        $($bundle.FullName)" -ForegroundColor Green
Write-Host "        size: $([math]::Round($bundle.Length / 1MB, 1)) MB"
Write-Host ""
Write-Host "[stack] Next: smoke-test on a clean Windows VM, then publish:" -ForegroundColor Cyan
Write-Host "        gh release create founder-stack-v$Version `"$($bundle.FullName)`" --repo danishhaiderau-maker/doxed-founders-website --title `"Founder IDE $Version`""

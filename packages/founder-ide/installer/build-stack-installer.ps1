# build-stack-installer.ps1
#
# Orchestrates the full Founder Stack build:
#   1. Build the Founder OS chat extension .vsix (packages/founder-ide-extension)
#   2. Build Founder IDE (build/build-founder-ide.sh -> VSCodium dev/build.sh)
#   3. Build Founder Node (apps/founder-node electron-builder --win)
#   4. Compose it into Founder-Stack-Setup-<v>.exe via Inno Setup (iscc)
#
# As of 0.9.2 the bundle includes Founder Node again: the IDE Gateway client
# requires ~/FounderVault/node-config.json, which only Founder Node creates on
# first pairing. Founder Node is installed in every deployment mode (it is
# required, not optional).
#
# This script is the entry point for producing a downloadable installer. It
# does NOT clone VSCodium (one-time setup on the build machine — see
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
#   -Version             - Founder Stack version (default: 0.1.0)
#   -SkipExtensionBuild  - skip step 1 (you already built the .vsix)
#   -SkipIdeBuild        - skip step 2 (you already built Founder IDE)
#   -SkipFounderNodeBuild - skip step 3 (you already built Founder Node)
#   -IsccPath            - path to iscc.exe (default: auto-detect)

[CmdletBinding()]
param(
    [string]$MonorepoRoot     = "",
    [string]$VscodiumCheckout = (Get-Location).Path,
    [string]$Version          = "0.1.0",
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
    Write-Host "`n[stack] STEP 1/4 — building Founder OS chat extension .vsix" -ForegroundColor Cyan
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
    Write-Host "`n[stack] STEP 1/4 — SKIPPED (SkipExtensionBuild)" -ForegroundColor DarkGray
    $vsixDest = Join-Path $staging "founder-ide-extension.vsix"
    if (-not (Test-Path $vsixDest)) { throw "SkipExtensionBuild set but $vsixDest not staged." }
}

# --- Step 2: build Founder IDE -----------------------------------------------
$ideSetup = Join-Path $staging "Founder-IDE-Setup-x64.exe"
if (-not $SkipIdeBuild) {
    Write-Host "`n[stack] STEP 2/4 — building Founder IDE (VSCodium downstream)" -ForegroundColor Cyan
    $buildPs1 = Join-Path $VscodiumCheckout "build\build-founder-ide.ps1"
    if (-not (Test-Path $buildPs1)) {
        throw "build-founder-ide.ps1 not found at $buildPs1 (is VscodiumCheckout correct?)"
    }
    & $buildPs1 -ExtensionVsix $vsixDest
    if ($LASTEXITCODE -ne 0) { throw "build-founder-ide.ps1 failed (exit $LASTEXITCODE)" }

    # Find the produced installer and copy it to staging with a stable name.
    $candidate = Get-ChildItem -Path (Join-Path $VscodiumCheckout "VSCode") -Recurse -Filter "Founder-IDE-Setup-*.exe" -ErrorAction SilentlyContinue |
                 Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $candidate) { throw "Founder IDE setup .exe not found in VSCode\... after build." }
    Copy-Item $candidate.FullName $ideSetup -Force
    Write-Host "[stack]   -> $ideSetup"
} else {
    Write-Host "`n[stack] STEP 2/4 — SKIPPED (SkipIdeBuild)" -ForegroundColor DarkGray
    if (-not (Test-Path $ideSetup)) { throw "SkipIdeBuild set but $ideSetup not staged." }
}

# --- Step 3: build Founder Node ---------------------------------------------
# Founder Node is required by the IDE Gateway client (it creates
# ~/FounderVault/node-config.json on first pairing). Built via electron-builder
# into apps/founder-node/release/, then staged with a stable name.
$founderNodeSetup = Join-Path $staging "Founder-Node-win-x64.exe"
if (-not $SkipFounderNodeBuild) {
    Write-Host "`n[stack] STEP 3/4 — building Founder Node (electron-builder --win)" -ForegroundColor Cyan
    $nodeDir = Join-Path $MonorepoRoot "apps\founder-node"
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
        Write-Host "[stack]   electron-builder --win"
        npx electron-builder --win --x64 --publish never
        if ($LASTEXITCODE -ne 0) { throw "electron-builder --win failed (exit $LASTEXITCODE)" }
    } finally { Pop-Location }

    $nodeCandidate = Get-ChildItem -Path (Join-Path $nodeDir "release") -Filter "Founder-Node-*.exe" -ErrorAction SilentlyContinue |
                     Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $nodeCandidate) { throw "Founder Node setup .exe not found in $nodeDir\release after build." }
    Copy-Item $nodeCandidate.FullName $founderNodeSetup -Force
    Write-Host "[stack]   -> $founderNodeSetup"
} else {
    Write-Host "`n[stack] STEP 3/4 — SKIPPED (SkipFounderNodeBuild)" -ForegroundColor DarkGray
    if (-not (Test-Path $founderNodeSetup)) { throw "SkipFounderNodeBuild set but $founderNodeSetup not staged." }
}

# --- Step 4: compose the Founder Stack installer via Inno Setup ---------------
Write-Host "`n[stack] STEP 4/4 — composing Founder Stack installer via Inno Setup" -ForegroundColor Cyan
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
$ideSetupAbs    = ((Resolve-Path $ideSetup).Path)    -replace '\\','/'
$founderNodeAbs = ((Resolve-Path $founderNodeSetup).Path) -replace '\\','/'

& $IsccPath `
    "/DFOUNDER_STACK_VERSION=$Version" `
    "/DFOUNDER_IDE_SETUP=`"$ideSetupAbs`"" `
    "/DFOUNDER_NODE_SETUP=`"$founderNodeAbs`"" `
    $iss
if ($LASTEXITCODE -ne 0) { throw "iscc failed (exit $LASTEXITCODE)" }

# Locate the produced bundle.
$bundleDir = Join-Path $VscodiumCheckout "dist"
if (-not (Test-Path $bundleDir)) { $bundleDir = (Split-Path -Parent $iss) }
$bundle = Get-ChildItem -Path $bundleDir -Filter "Founder-Stack-Setup-*.exe" -ErrorAction SilentlyContinue |
          Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $bundle) { throw "Founder Stack installer not found in $bundleDir after iscc." }

Write-Host "`n[stack] DONE — Founder Stack installer:" -ForegroundColor Green
Write-Host "        $($bundle.FullName)" -ForegroundColor Green
Write-Host "        size: $([math]::Round($bundle.Length / 1MB, 1)) MB"
Write-Host ""
Write-Host "[stack] Next: smoke-test on a clean Windows VM, then publish:" -ForegroundColor Cyan
Write-Host "        gh release create v$Version `"$($bundle.FullName)`" --repo danishhaiderau-maker/doxed-founders-website --title `"Founder Stack $Version`""

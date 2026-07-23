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

# The current Void build cache is the VS Code source tree itself, while older
# downstream checkouts keep that source under a VSCode/ child. Detect both so
# a warm local build and the clean CI build package the same application.
$vscodeSource = $VscodiumCheckout
if (-not (Test-Path (Join-Path $vscodeSource "gulpfile.js"))) {
    $nestedVscodeSource = Join-Path $VscodiumCheckout "VSCode"
    if (Test-Path (Join-Path $nestedVscodeSource "gulpfile.js")) {
        $vscodeSource = $nestedVscodeSource
    } else {
        throw "VS Code source tree not found at $VscodiumCheckout or $nestedVscodeSource"
    }
}
Write-Host "[stack] vscode source: $vscodeSource"

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
# VS Code's gulp package tasks always emit beside the source directory. That
# is `$VscodiumCheckout\VSCode-win32-x64` for the historical nested layout and
# the checkout parent for a direct `...\void-builder\vscode` source tree.
$ideRoot = Join-Path (Split-Path -Parent $vscodeSource) "VSCode-win32-x64"
Write-Host "[stack] IDE payload: $ideRoot"
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
$ideAppRoot = Join-Path $ideRoot "resources\app"

# A warm VS Code checkout can retain out-vscode from an earlier React build
# because the upstream gulp dependency graph does not track the nested tsup
# bundle. Refuse to ship a payload whose Founder composer is stale.
$workbenchBundle = Join-Path $ideRoot "resources\app\out\vs\workbench\workbench.desktop.main.js"
if (-not (Test-Path $workbenchBundle)) {
    throw "Founder IDE workbench bundle not found at $workbenchBundle"
}
$workbenchText = [System.IO.File]::ReadAllText($workbenchBundle)
$expectedFounderComposer = @(
    "Founder Second brain",
    "Run an independent read-only review",
    "founder.personalAi.transcribe",
    "Founder work mode: Ask",
    "Founder work mode: Plan",
    "Founder work mode: Build",
    "Founder work mode: Debug",
    "Founder work mode: Team"
)
$staleFounderComposer = @("label:`"Verify`"", "label:`"Challenge`"", "Founder actions")
if ($expectedFounderComposer.Where({ -not $workbenchText.Contains($_) }).Count -gt 0 -or
    $staleFounderComposer.Where({ $workbenchText.Contains($_) }).Count -gt 0) {
    throw "Founder IDE payload contains a stale chat composer. Rebuild the React bundle and remove out-vscode before packaging."
}
Write-Host "[stack]   Founder work modes, Second brain, and voice composer payload verified"

# Search and the context index use VS Code's pinned ripgrep executable. A
# cached dependency install can retain the package while omitting its
# postinstall download, which only becomes visible as an ENOENT after launch.
$ripgrepRelativePath = "node_modules\@vscode\ripgrep\bin\rg.exe"
$ripgrepDest = Join-Path $ideAppRoot $ripgrepRelativePath
$ripgrepSha256 = "5075519D24E22733AACDDDD218C7023FC94C49150397E1EDA5C4F6B866C3174E"
if (-not (Test-Path $ripgrepDest)) {
    $ripgrepSource = Join-Path $vscodeSource $ripgrepRelativePath
    if (-not (Test-Path $ripgrepSource)) {
        throw "Founder IDE ripgrep runtime is missing from both payload and source: $ripgrepRelativePath"
    }
    New-Item -ItemType Directory -Path (Split-Path $ripgrepDest -Parent) -Force | Out-Null
    Copy-Item $ripgrepSource $ripgrepDest -Force
    Write-Host "[stack]   restored ripgrep runtime -> $ripgrepDest"
}
if ((Get-Item $ripgrepDest).Length -lt 1000000) {
    throw "Founder IDE ripgrep runtime is unexpectedly small: $ripgrepDest"
}
$actualRipgrepSha256 = (Get-FileHash -LiteralPath $ripgrepDest -Algorithm SHA256).Hash
if ($actualRipgrepSha256 -ne $ripgrepSha256) {
    throw "Founder IDE ripgrep runtime checksum mismatch: expected $ripgrepSha256, got $actualRipgrepSha256"
}
Write-Host "[stack]   ripgrep runtime checksum verified"

# Verify the bindings used during a supported Windows 10/11 startup before
# spending time building the inner and outer installers. Parcel intentionally
# ships its watcher through a platform package, and node-pty uses ConPTY on
# every Windows build supported by this release.
$requiredNativeBindings = @(
    "resources\app\node_modules\@parcel\watcher-win32-x64\watcher.node",
    "resources\app\node_modules\@vscode\deviceid\build\Release\*.node",
    "resources\app\node_modules\@vscode\spdlog\build\Release\*.node",
    "resources\app\node_modules\@vscode\sqlite3\build\Release\*.node",
    "resources\app\node_modules\@vscode\windows-ca-certs\build\Release\*.node",
    "resources\app\node_modules\@vscode\windows-mutex\build\Release\*.node",
    "resources\app\node_modules\@vscode\windows-process-tree\build\Release\*.node",
    "resources\app\node_modules\@vscode\windows-registry\build\Release\*.node",
    "resources\app\node_modules\@*\policy-watcher\build\Release\*.node",
    "resources\app\node_modules\kerberos\build\Release\*.node",
    "resources\app\node_modules\native-is-elevated\build\Release\*.node",
    "resources\app\node_modules\native-keymap\build\Release\*.node",
    "resources\app\node_modules\native-watchdog\build\Release\*.node",
    "resources\app\node_modules\node-pty\build\Release\conpty.node",
    "resources\app\node_modules\node-pty\build\Release\conpty_console_list.node",
    "resources\app\node_modules\windows-foreground-love\build\Release\*.node"
)
foreach ($bindingPattern in $requiredNativeBindings) {
    $binding = Get-ChildItem -Path (Join-Path $ideRoot $bindingPattern) -ErrorAction SilentlyContinue |
               Select-Object -First 1
    if (-not $binding) {
        throw "Founder IDE native binding is missing: $bindingPattern"
    }
}
Write-Host "[stack]   all supported Windows startup bindings verified"

# node-pty's postinstall step copies its matching ConPTY runtime beside the
# native bindings. Warm VS Code payloads can retain the .node files while
# omitting this directory, which only becomes visible after install as a
# terminal launch failure. Restore the runtime from the pinned node-pty source
# used by this checkout and verify both files before packaging.
$nodePtyRelease = Join-Path $ideAppRoot "node_modules\node-pty\build\Release"
$conptyRuntime = Join-Path $nodePtyRelease "conpty"
$conptyDll = Join-Path $conptyRuntime "conpty.dll"
$openConsole = Join-Path $conptyRuntime "OpenConsole.exe"
if (-not (Test-Path $conptyDll) -or -not (Test-Path $openConsole)) {
    $conptyVersions = Join-Path $vscodeSource "node_modules\node-pty\third_party\conpty"
    $conptyVersion = Get-ChildItem -Path $conptyVersions -Directory -ErrorAction SilentlyContinue |
                     Sort-Object Name -Descending |
                     Select-Object -First 1
    if (-not $conptyVersion) {
        throw "Founder IDE ConPTY runtime source is missing below $conptyVersions"
    }
    $conptySource = Join-Path $conptyVersion.FullName "win10-x64"
    foreach ($runtimeFile in @("conpty.dll", "OpenConsole.exe")) {
        $sourceFile = Join-Path $conptySource $runtimeFile
        if (-not (Test-Path $sourceFile)) {
            throw "Founder IDE ConPTY runtime source is missing: $sourceFile"
        }
    }
    New-Item -ItemType Directory -Path $conptyRuntime -Force | Out-Null
    Copy-Item (Join-Path $conptySource "conpty.dll") $conptyDll -Force
    Copy-Item (Join-Path $conptySource "OpenConsole.exe") $openConsole -Force
    Write-Host "[stack]   restored node-pty ConPTY runtime -> $conptyRuntime"
}
if ((Get-Item $conptyDll).Length -lt 50000 -or (Get-Item $openConsole).Length -lt 500000) {
    throw "Founder IDE ConPTY runtime is unexpectedly small below $conptyRuntime"
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

# VS Code requires its Electron-targeted SQLite binding during main-process
# startup. Cached payload builds can otherwise look healthy while omitting the
# native file and then emit an uncaught exception on every launch.
$sqliteRelativePath = "node_modules\@vscode\sqlite3\build\Release\vscode-sqlite3.node"
$sqliteNativeDest = Join-Path $ideAppRoot $sqliteRelativePath
if (-not (Test-Path $sqliteNativeDest)) {
    $sqliteNativeSource = Join-Path $vscodeSource $sqliteRelativePath
    if (-not (Test-Path $sqliteNativeSource)) {
        throw "Founder IDE SQLite native binding is missing from both payload and source: $sqliteRelativePath"
    }
    New-Item -ItemType Directory -Path (Split-Path $sqliteNativeDest -Parent) -Force | Out-Null
    Copy-Item $sqliteNativeSource $sqliteNativeDest -Force
    Write-Host "[stack]   restored SQLite native binding -> $sqliteNativeDest"
}
if ((Get-Item $sqliteNativeDest).Length -lt 100000) {
    throw "Founder IDE SQLite native binding is unexpectedly small: $sqliteNativeDest"
}

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

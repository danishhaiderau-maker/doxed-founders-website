# install-founder-stack-lite.ps1
#
# Interim "Founder Stack Lite" installer for Windows.
#
# The full Founder Stack installer (Founder-Stack-Setup-<v>.exe, composed via
# Inno Setup) bundles a rebranded VSCodium fork ("Founder IDE") + Founder Node
# in one .exe. Building the VSCodium fork requires a toolchain this machine
# does not have right now (jq, 7-Zip, Rustup, Inno Setup, Python 3.11, ~50 GB
# VSCodium checkout). This script is the interim deliverable: it installs
# Founder Node and wires the Founder OS Chat extension (.vsix) into whatever
# VS Code-class editor it detects (Cursor, VSCodium, VS Code), so founders get
# the Node + the Chat provider in one download today.
#
# Run from the folder that contains:
#   - Founder-Node-0.8.0-win-x64.exe   (the electron-builder NSIS installer)
#   - founder-ide-extension-0.1.0.vsix (the chat extension)
#   - install-founder-stack-lite.ps1   (this script)
#
# Usage (PowerShell):
#   .\install-founder-stack-lite.ps1
#   .\install-founder-stack-lite.ps1 -SkipNodeInstall     # ext only
#   .\install-founder-stack-lite.ps1 -SkipExtensionInstall # node only
#
# No admin elevation is required (per-user installs).

[CmdletBinding()]
param(
    [switch]$SkipNodeInstall,
    [switch]$SkipExtensionInstall,
    [string]$NodeInstaller = "Founder-Node-0.8.0-win-x64.exe",
    [string]$Vsix = "founder-ide-extension-0.1.0.vsix"
)

$ErrorActionPreference = "Stop"
$here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$node  = Join-Path $here $NodeInstaller
$vsix  = Join-Path $here $Vsix

Write-Host ""
Write-Host "=== Founder Stack Lite (interim installer) ===" -ForegroundColor Cyan
Write-Host "This installs:"
Write-Host "  1. Founder Node  (local vault + metadata sync)"
Write-Host "  2. Founder OS Chat extension into your VS Code-class editor"
Write-Host ""

# ----------------------------------------------------------------------------
# 1. Install Founder Node (electron-builder NSIS, silent, per-user)
# ----------------------------------------------------------------------------
if (-not $SkipNodeInstall) {
    if (-not (Test-Path $node)) {
        Write-Warning "Founder Node installer not found at: $node"
        Write-Warning "Skipping node install. Build it with: cd apps\founder-node; npm run pack:win"
    } else {
        Write-Host "[1/2] Installing Founder Node..." -ForegroundColor Green
        Write-Host "      $node"
        # /S = silent, /allusers=0 = per-user (no elevation)
        Start-Process -FilePath $node -ArgumentList "/S","/allusers=0" -Wait -NoNewWindow
        Write-Host "[1/2] Founder Node installed." -ForegroundColor Green
    }
} else {
    Write-Host "[1/2] SKIPPED (-SkipNodeInstall)" -ForegroundColor DarkGray
}

# ----------------------------------------------------------------------------
# 2. Install the Founder OS Chat .vsix into detected editors
# ----------------------------------------------------------------------------
if (-not $SkipExtensionInstall) {
    if (-not (Test-Path $vsix)) {
        Write-Warning "Extension .vsix not found at: $vsix"
    } else {
        Write-Host "[2/2] Installing Founder OS Chat extension..." -ForegroundColor Green

        # Candidate editors: name -> cli path. Cursor, VSCodium, VS Code (user + system).
        $editors = @(
            @{ Name = "Cursor";   Cli = Join-Path $env:LOCALAPPDATA "Programs\cursor\resources\app\bin\cursor.cmd" },
            @{ Name = "Cursor";   Cli = "cursor" },
            @{ Name = "VSCodium"; Cli = Join-Path $env:LOCALAPPDATA "Programs\VSCodium\bin\codium.cmd" },
            @{ Name = "VSCodium"; Cli = "codium" },
            @{ Name = "VS Code";  Cli = Join-Path $env:LOCALAPPDATA "Programs\Microsoft VS Code\bin\code.cmd" },
            @{ Name = "VS Code";  Cli = "code" }
        )

        $installed = $false
        foreach ($ed in $editors) {
            $cli = $ed.Cli
            $resolved = $null
            if (Test-Path $cli) {
                $resolved = $cli
            } else {
                $g = Get-Command $cli -ErrorAction SilentlyContinue
                if ($g) { $resolved = $g.Source }
            }
            if (-not $resolved) { continue }

            Write-Host "      Detected $($ed.Name): $resolved"
            try {
                & $resolved --install-extension $vsix --force 2>&1 | ForEach-Object { Write-Host "        $_" }
                Write-Host "      -> installed into $($ed.Name)" -ForegroundColor Green
                $installed = $true
            } catch {
                Write-Warning "      install into $($ed.Name) failed: $_"
            }
        }

        if (-not $installed) {
            Write-Warning "No VS Code-class editor detected on PATH."
            Write-Host ""
            Write-Host "To install the extension manually, run ONE of:" -ForegroundColor Yellow
            Write-Host "  cursor  --install-extension `"$vsix`""
            Write-Host "  codium  --install-extension `"$vsix`""
            Write-Host "  code    --install-extension `"$vsix`""
            Write-Host ""
            Write-Host "Or in VS Code/Cursor: Extensions pane -> '...' -> 'Install from VSIX...' -> select:"
            Write-Host "  $vsix"
        }
    }
} else {
    Write-Host "[2/2] SKIPPED (-SkipExtensionInstall)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Cyan
Write-Host "Next steps:"
Write-Host "  - Launch Founder Node (Start Menu -> Founder Node) and complete pairing."
    Write-Host "  - Open your editor; the 'Founder OS' chat participant/provider appears."
Write-Host "  - The full Founder Stack (rebranded Founder IDE + Node in one .exe)"
Write-Host "    requires the VSCodium toolchain build — see packages\founder-ide\RELEASES.md"
Write-Host ""

# install-founder-stack-desktop.ps1
#
# Creates a single Desktop shortcut "Founder Stack.lnk" that launches
# Founder Node (tray) + Founder IDE via launch-founder-stack.ps1.
# Removes the duplicate Founder IDE / Founder Node Desktop shortcuts
# (merge UX â€” one primary launcher).

[CmdletBinding()]
param(
    [switch]$KeepAdvancedNodeShortcut,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$launchPs1 = Join-Path $PSScriptRoot "launch-founder-stack.ps1"
$launchCmd = Join-Path $PSScriptRoot "Founder-Stack.cmd"
$desktop = [Environment]::GetFolderPath("Desktop")
$stackLnk = Join-Path $desktop "Founder Stack.lnk"
$ideLnk = Join-Path $desktop "Founder IDE.lnk"
$nodeLnk = Join-Path $desktop "Founder Node.lnk"
$osBat = Join-Path $desktop "Founder OS.bat"
$osLnk = Join-Path $desktop "Founder OS.lnk"

if (-not (Test-Path -LiteralPath $launchPs1)) {
    throw "Missing launcher: $launchPs1"
}

# Resolve icons: Stack uses IDE icon; optional Node keep uses Node icon.
$ideExe = Join-Path $env:LOCALAPPDATA "FounderIDE\Founder IDE.exe"
$nodeIcon = Join-Path $repoRoot "apps\founder-node\build\icon.ico"
$ideIcon = if (Test-Path $ideExe) { "$ideExe,0" } else { $null }
$stackIcon = $ideIcon
if (-not $stackIcon -and (Test-Path $nodeIcon)) { $stackIcon = $nodeIcon }

# --- Write .cmd wrapper (double-click / shortcut friendly; bypasses policy UX) ---
$cmdBody = @"
@echo off
REM Founder Stack â€” starts Founder Node tray (if needed) then Founder IDE.
set "SCRIPT=%~dp0launch-founder-stack.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
"@

if ($DryRun) {
    Write-Host "[dry-run] would write $launchCmd"
} else {
    Set-Content -LiteralPath $launchCmd -Value $cmdBody -Encoding ASCII
    Write-Host "Wrote $launchCmd"
}

# --- Create Founder Stack.lnk ---
if ($DryRun) {
    Write-Host "[dry-run] would create $stackLnk -> $launchCmd"
} else {
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($stackLnk)
    $lnk.TargetPath = $launchCmd
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.WindowStyle = 7  # minimized â€” brief console flash only
    $lnk.Description = "Founder Stack - Founder Node tray + Founder IDE"
    if ($stackIcon) { $lnk.IconLocation = $stackIcon }
    $lnk.Save()
    Write-Host "Created Desktop shortcut: Founder Stack.lnk"
    if ($stackIcon) { Write-Host "  Icon: $stackIcon" }
}

# --- Remove duplicate Desktop shortcuts ---
$toRemove = @($ideLnk, $osBat, $osLnk)
if (-not $KeepAdvancedNodeShortcut) {
    $toRemove += $nodeLnk
}

foreach ($path in $toRemove) {
    if (Test-Path -LiteralPath $path) {
        if ($DryRun) {
            Write-Host "[dry-run] would remove $path"
        } else {
            Remove-Item -LiteralPath $path -Force
            Write-Host "Removed: $(Split-Path -Leaf $path)"
        }
    }
}

if ($KeepAdvancedNodeShortcut -and -not (Test-Path -LiteralPath $nodeLnk) -and -not $DryRun) {
    # Recreate Node as secondary "advanced" only if requested and missing
    $nodeExe = Join-Path $repoRoot "apps\founder-node\release\win-unpacked\Founder Node.exe"
    if (Test-Path $nodeExe) {
        $shell = New-Object -ComObject WScript.Shell
        $lnk = $shell.CreateShortcut($nodeLnk)
        $lnk.TargetPath = $nodeExe
        $lnk.WorkingDirectory = Split-Path $nodeExe
        $lnk.Description = "Founder Node (tray only - advanced)"
        if (Test-Path $nodeIcon) { $lnk.IconLocation = $nodeIcon }
        $lnk.Save()
        Write-Host "Kept advanced: Founder Node.lnk (Node icon)"
    }
}

Write-Host ""
Write-Host "Desktop now:" -ForegroundColor Cyan
Get-ChildItem $desktop -Filter "Founder*" | ForEach-Object { Write-Host "  $($_.Name)" }
Write-Host ""
Write-Host "Double-click 'Founder Stack' to start Node tray + IDE."


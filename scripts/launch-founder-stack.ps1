# launch-founder-stack.ps1
#
# Single entry point for the Founder Stack desktop kit:
#   1. Start Founder Node tray (if not already running)
#   2. Start Founder IDE
#
# Used by the "Founder Stack" Desktop shortcut created by
# install-founder-stack-desktop.ps1.

[CmdletBinding()]
param(
    [switch]$SkipNode,
    [switch]$SkipIde,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"

function Write-Stack([string]$Message, [string]$Color = "Cyan") {
    if (-not $Quiet) {
        Write-Host "[Founder Stack] $Message" -ForegroundColor $Color
    }
}

function Find-FounderNodeExe {
    $candidates = @(
        (Join-Path $PSScriptRoot "..\apps\founder-node\release\win-unpacked\Founder Node.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Founder Node\Founder Node.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\founder-node\Founder Node.exe"),
        (Join-Path $env:LOCALAPPDATA "founder-node\Founder Node.exe")
    )
    foreach ($c in $candidates) {
        $full = [System.IO.Path]::GetFullPath($c)
        if (Test-Path -LiteralPath $full) { return $full }
    }
    # Last resort: Start Menu shortcut target
    $startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
    $lnk = Get-ChildItem -Path $startMenu -Filter "Founder Node.lnk" -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($lnk) {
        $shell = New-Object -ComObject WScript.Shell
        $target = $shell.CreateShortcut($lnk.FullName).TargetPath
        if ($target -and (Test-Path -LiteralPath $target)) { return $target }
    }
    return $null
}

function Find-FounderIdeExe {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "FounderIDE\Founder IDE.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Founder IDE\Founder IDE.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\FounderIDE\Founder IDE.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path -LiteralPath $c) { return $c }
    }
    return $null
}

function Test-ProcessRunning([string]$NameContains) {
    $procs = Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessName -like "*$NameContains*" -or $_.MainWindowTitle -like "*$NameContains*"
    }
    return [bool]$procs
}

# --- Founder Node (tray) ----------------------------------------------------
if (-not $SkipNode) {
    $nodeRunning = (Get-Process -Name "Founder Node" -ErrorAction SilentlyContinue) -or
                   (Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -eq "Founder Node" })
    # Electron apps often show as the exe base name without extension
    if (-not $nodeRunning) {
        $nodeRunning = [bool](Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq "Founder Node.exe" -or $_.CommandLine -like "*Founder Node.exe*" } |
            Select-Object -First 1)
    }

    if ($nodeRunning) {
        Write-Stack "Founder Node already running." "DarkGray"
    } else {
        $nodeExe = Find-FounderNodeExe
        if (-not $nodeExe) {
            Write-Stack "Founder Node.exe not found - skipping tray start." "Yellow"
            Write-Stack "Build/install Node, or place it under apps\founder-node\release\win-unpacked\" "Yellow"
        } else {
            Write-Stack "Starting Founder Node tray..."
            Write-Stack "  $nodeExe" "DarkGray"
            Start-Process -FilePath $nodeExe
            Start-Sleep -Seconds 1
        }
    }
}

# --- Founder IDE ------------------------------------------------------------
if (-not $SkipIde) {
    $ideExe = Find-FounderIdeExe
    if (-not $ideExe) {
        Write-Stack "Founder IDE.exe not found under %LOCALAPPDATA%\FounderIDE\" "Red"
        Write-Stack "Install Founder IDE first, then re-run this launcher." "Red"
        if (-not $Quiet) { exit 1 }
        exit 1
    }
    Write-Stack "Starting Founder IDE..."
    Write-Stack "  $ideExe" "DarkGray"
    Start-Process -FilePath $ideExe
}

Write-Stack "Done - Stack launch requested." "Green"


# launch-founder-stack.ps1
#
# Single entry point for the Founder Stack desktop kit:
#   1. Start Founder Node tray (if not already running)
#   2. Start Founder IDE (usable on-screen window - native title bar, welcome page)
#
# Used by the "Founder Stack" Desktop shortcut created by
# install-founder-stack-desktop.ps1.
#
# Recovery flags:
#   -RestartIde              Kill stale IDE then relaunch
#   -DisableGpu              Electron --disable-gpu (rare; Cursor/Chrome work on this machine)
#   FOUNDER_IDE_DISABLE_GPU=1 / FOUNDER_IDE_RESTART=1  env equivalents

[CmdletBinding()]
param(
    [switch]$SkipNode,
    [switch]$SkipIde,
    [switch]$Quiet,
    [switch]$DisableGpu,
    [switch]$RestartIde
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

function Stop-FounderIde {
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq "Founder IDE.exe" }
    if (-not $procs) { return }
    Write-Stack "Stopping hung/stale Founder IDE..." "Yellow"
    foreach ($p in $procs) {
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 2
    cmd.exe /c "taskkill /F /IM ""Founder IDE.exe"" >nul 2>&1" | Out-Null
    Start-Sleep -Seconds 1
}

function Repair-FounderIdeWindowState {
    # Dual/off-screen windows (e.g. y=-40) + empty dark editor look like a "black screen".
    $storePath = Join-Path $env:APPDATA "Founder IDE\User\globalStorage\storage.json"
    if (-not (Test-Path -LiteralPath $storePath)) { return }
    try {
        $j = (Read-JsonFileNoBom $storePath)
        $backup = $null
        if ($j.windowsState -and $j.windowsState.lastActiveWindow) {
            $backup = $j.windowsState.lastActiveWindow.backupPath
        }
        $ui = [PSCustomObject]@{ mode = 1; x = 120; y = 80; width = 1400; height = 900 }
        $win = [PSCustomObject]@{ uiState = $ui }
        if ($backup) { $win | Add-Member -NotePropertyName backupPath -NotePropertyValue $backup -Force }
        $j.windowsState = [PSCustomObject]@{
            lastActiveWindow = $win
            openedWindows    = @()
        }
        if ($j.windowSplash -and $j.windowSplash.layoutInfo) {
            if (-not $j.windowSplash.layoutInfo.sideBarWidth -or [int]$j.windowSplash.layoutInfo.sideBarWidth -eq 0) {
                $j.windowSplash.layoutInfo.sideBarWidth = 300
            }
        }
        Write-Utf8NoBomFile $storePath ($j | ConvertTo-Json -Depth 20)
        Write-Stack "Reset IDE window position (on-screen)." "DarkGray"
    } catch {
        Write-Stack "Could not repair window state: $($_.Exception.Message)" "DarkGray"
    }
}

function Write-Utf8NoBomFile([string]$FilePath, [string]$Content) {
    $dir = Split-Path -Parent $FilePath
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    # Windows PowerShell Set-Content -Encoding UTF8 writes a BOM that breaks
    # Electron JSON.parse (FileStorage / blank frameless chrome). Always no-BOM.
    $utf8 = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($FilePath, $Content, $utf8)
}

function Read-JsonFileNoBom([string]$FilePath) {
    if (-not (Test-Path -LiteralPath $FilePath)) { return $null }
    $bytes = [System.IO.File]::ReadAllBytes($FilePath)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        $text = [System.Text.Encoding]::UTF8.GetString($bytes, 3, $bytes.Length - 3)
    } elseif ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
        $text = [System.Text.Encoding]::Unicode.GetString($bytes, 2, $bytes.Length - 2)
    } else {
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
    }
    try { return ($text | ConvertFrom-Json) } catch { return $null }
}

function Ensure-FounderIdeWelcomeSettings {
    $settingsPath = Join-Path $env:APPDATA "Founder IDE\User\settings.json"
    $dir = Split-Path $settingsPath -Parent
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $s = [ordered]@{}
    $obj = Read-JsonFileNoBom $settingsPath
    if ($obj) {
        foreach ($p in $obj.PSObject.Properties) { $s[$p.Name] = $p.Value }
    }
    # Empty editor + Default Dark Modern reads as a full black screen.
    $s["workbench.startupEditor"] = "welcomePage"
    $s["window.restoreWindows"] = "none"
    # Native Windows chrome: custom titlebar was painting invisible/blank (no min/max/close).
    $s["window.titleBarStyle"] = "native"
    $s["window.customTitleBarVisibility"] = "windowed"
    $s["window.menuBarVisibility"] = "classic"
    $s["window.commandCenter"] = $false
    if (-not $s.Contains("workbench.colorTheme")) {
        $s["workbench.colorTheme"] = "Default Dark Modern"
    }
    Write-Utf8NoBomFile $settingsPath ($s | ConvertTo-Json -Depth 10)
    Write-Stack "Ensured native title bar + welcome settings (UTF-8 no BOM)." "DarkGray"
}

function Get-IdeLaunchArgs {
    $list = New-Object System.Collections.Generic.List[string]
    # Prefer fresh window so native titlebar settings apply (avoid sticky custom chrome)
    $list.Add("--new-window") | Out-Null
    if ($DisableGpu -or $env:FOUNDER_IDE_DISABLE_GPU -eq "1") {
        $list.Add("--disable-gpu") | Out-Null
        $list.Add("--disable-gpu-compositing") | Out-Null
        Write-Stack "GPU disabled for this launch (FOUNDER_IDE_DISABLE_GPU / -DisableGpu)." "Yellow"
    }
    return , $list.ToArray()
}

function Move-FounderIdeOnScreen {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class FounderIdeWin {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr hWnd, IntPtr after, int X, int Y, int cx, int cy, uint flags);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT r);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@ -ErrorAction SilentlyContinue

    $mains = Get-Process -Name "Founder IDE" -ErrorAction SilentlyContinue |
        Where-Object { $_.MainWindowHandle -ne [IntPtr]::Zero }
    foreach ($main in $mains) {
        $h = $main.MainWindowHandle
        $r = New-Object FounderIdeWin+RECT
        [void][FounderIdeWin]::GetWindowRect($h, [ref]$r)
        if ($r.Top -lt 0 -or $r.Left -lt -100 -or ($r.Right - $r.Left) -lt 200) {
            Write-Stack "Repositioning off-screen IDE window (was y=$($r.Top))..." "Yellow"
            [void][FounderIdeWin]::ShowWindow($h, 9)
            [void][FounderIdeWin]::SetWindowPos($h, [IntPtr]::Zero, 120, 80, 1400, 900, 0x0040)
        } else {
            [void][FounderIdeWin]::ShowWindow($h, 9)
        }
        [void][FounderIdeWin]::SetForegroundWindow($h)
    }

    $storePath = Join-Path $env:APPDATA "Founder IDE\User\globalStorage\storage.json"
    if (Test-Path -LiteralPath $storePath) {
        try {
            $j = (Read-JsonFileNoBom $storePath)
            $backup = $null
            if ($j.windowsState -and $j.windowsState.lastActiveWindow) {
                $backup = $j.windowsState.lastActiveWindow.backupPath
            }
            $ui = [PSCustomObject]@{ mode = 1; x = 120; y = 80; width = 1400; height = 900 }
            $win = [PSCustomObject]@{ uiState = $ui }
            if ($backup) { $win | Add-Member -NotePropertyName backupPath -NotePropertyValue $backup -Force }
            $j.windowsState = [PSCustomObject]@{ lastActiveWindow = $win; openedWindows = @() }
            Write-Utf8NoBomFile $storePath ($j | ConvertTo-Json -Depth 20)
        } catch { }
    }
}

# --- Founder Node (tray) ----------------------------------------------------
if (-not $SkipNode) {
    $nodeRunning = [bool](Get-Process -Name "Founder Node" -ErrorAction SilentlyContinue)
    if (-not $nodeRunning) {
        $nodeRunning = [bool](Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq "Founder Node.exe" } |
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
        exit 1
    }

    if ($RestartIde -or $env:FOUNDER_IDE_RESTART -eq "1") {
        Stop-FounderIde
    }

    Ensure-FounderIdeWelcomeSettings

    # Always repair window/storage before launch (BOM + bad chrome state).
    Repair-FounderIdeWindowState

    $launchArgs = Get-IdeLaunchArgs
    Write-Stack "Starting Founder IDE..."
    Write-Stack "  $ideExe" "DarkGray"
    if ($launchArgs.Count -gt 0) {
        Start-Process -FilePath $ideExe -ArgumentList $launchArgs
    } else {
        Start-Process -FilePath $ideExe
    }

    $ok = $false
    for ($i = 0; $i -lt 24; $i++) {
        Start-Sleep -Milliseconds 500
        $main = Get-Process -Name "Founder IDE" -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowHandle -ne 0 } |
            Select-Object -First 1
        if ($main -and $main.MainWindowTitle) {
            Write-Stack "IDE window ready: '$($main.MainWindowTitle)' (pid $($main.Id))" "Green"
            Move-FounderIdeOnScreen
            $ok = $true
            break
        }
    }
    if (-not $ok) {
        Write-Stack "IDE started but no titled window yet - if the screen stays black, re-run:" "Yellow"
        Write-Stack "  Founder-Stack.cmd -RestartIde -DisableGpu" "Yellow"
    }
}

Write-Stack "Done - Stack launch requested." "Green"

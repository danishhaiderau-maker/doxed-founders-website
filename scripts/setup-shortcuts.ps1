$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$startup = [Environment]::GetFolderPath('Startup')
$repo = "$env:USERPROFILE\Desktop\Final Bots\doxedcryptofounder"
$fideExe = [Environment]::GetFolderPath('LocalApplicationData') + '\FounderIDE\Founder IDE.exe'
$fnodeExe = "$repo\apps\founder-node\release\win-unpacked\Founder Node.exe"

# Founder OS
$s = $ws.CreateShortcut("$desktop\Founder OS.lnk")
$s.TargetPath = "$desktop\Founder OS.bat"
$s.WorkingDirectory = $desktop
$s.Description = 'Launch Founder IDE + Founder Node'
$s.Save()
Write-Host 'Created: Founder OS.lnk'

# Founder Node
if (Test-Path $fnodeExe) {
    $s = $ws.CreateShortcut("$desktop\Founder Node.lnk")
    $s.TargetPath = $fnodeExe
    $s.WorkingDirectory = Split-Path $fnodeExe
    $s.Description = 'Founder Node System Tray App'
    $s.Save()
    Write-Host 'Fixed: Founder Node.lnk'
    
    $s = $ws.CreateShortcut("$startup\Founder Node.lnk")
    $s.TargetPath = $fnodeExe
    $s.WorkingDirectory = Split-Path $fnodeExe
    $s.Description = 'Founder Node auto-start'
    $s.Save()
    Write-Host 'Created Startup: Founder Node.lnk'
}

# Founder IDE
if (Test-Path $fideExe) {
    $s = $ws.CreateShortcut("$desktop\Founder IDE.lnk")
    $s.TargetPath = $fideExe
    $s.WorkingDirectory = Split-Path $fideExe
    $s.Description = 'Founder IDE AI Code Editor'
    $s.Save()
    Write-Host 'Verified: Founder IDE.lnk'
}

Write-Host 'Done!'

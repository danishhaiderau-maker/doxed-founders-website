$ws = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')

Write-Host "=== Desktop Shortcuts ==="
$shortcuts = @('Founder OS', 'Founder IDE', 'Founder Node')
foreach ($name in $shortcuts) {
    $path = Join-Path $desktop "$name.lnk"
    if (Test-Path $path) {
        $lnk = $ws.CreateShortcut($path)
        Write-Host "  $name -> $($lnk.TargetPath)"
    } else {
        Write-Host "  $name MISSING"
    }
}

Write-Host ""
Write-Host "=== Founder IDE Installation ==="
$fide = [Environment]::GetFolderPath('LocalApplicationData') + '\FounderIDE\Founder IDE.exe'
Write-Host "  Exe: $(Test-Path $fide)"
$fideJs = [Environment]::GetFolderPath('LocalApplicationData') + '\FounderIDE\resources\app\out\vs\workbench\workbench.desktop.main.js'
Write-Host "  Workbench JS: $(Test-Path $fideJs)"

Write-Host ""
Write-Host "=== Founder Node ==="
$fnode = "$env:USERPROFILE\Desktop\Final Bots\doxedcryptofounder\apps\founder-node\release\win-unpacked\Founder Node.exe"
Write-Host "  Exe: $(Test-Path $fnode)"

Write-Host ""
Write-Host "=== Extension ==="
$ext = "$env:USERPROFILE\.founder-ide\extensions\founder-ide-extension\extension\out\extension.js"
Write-Host "  Installed: $(Test-Path $ext)"

Write-Host ""
Write-Host "=== Branding ==="
$text = [System.IO.File]::ReadAllText($fideJs)
$count = ([regex]::Matches($text, 'Founder IDE')).Count
Write-Host "  'Founder IDE' references in workbench JS: $count"
$vscodiumCount = ([regex]::Matches($text, 'VSCodium')).Count
Write-Host "  'VSCodium' references remaining: $vscodiumCount"

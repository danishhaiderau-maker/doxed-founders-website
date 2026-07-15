$WshShell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')

$shortcuts = @('Founder IDE', 'Founder Node')
foreach ($name in $shortcuts) {
    $path = Join-Path $desktop "$name.lnk"
    if (Test-Path $path) {
        $lnk = $WshShell.CreateShortcut($path)
        Write-Output "=== $name ==="
        Write-Output "Target: $($lnk.TargetPath)"
        Write-Output "Args: $($lnk.Arguments)"
        Write-Output "WorkingDir: $($lnk.WorkingDirectory)"
        Write-Output ""
    } else {
        Write-Output "=== $name === NOT FOUND"
        Write-Output ""
    }
}

# Check Founder Node dist
$distDir = "C:\Users\user\Desktop\Final Bots\doxedcryptofounder\apps\founder-node\dist"
if (Test-Path "$distDir\main.js") {
    Write-Output "Founder Node dist found at: $distDir"
}
# Also check for Founder Node installed via electron-builder  
$npmGlobal = "$env:APPDATA\npm\node_modules\@dcf\founder-node"
if (Test-Path $npmGlobal) {
    Write-Output "Founder Node npm global: $npmGlobal"
}

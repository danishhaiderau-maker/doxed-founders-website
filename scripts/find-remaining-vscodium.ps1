$file = [Environment]::GetFolderPath('LocalApplicationData') + '\FounderIDE\resources\app\out\vs\workbench\workbench.desktop.main.js'
$lines = Select-String -Path $file -Pattern 'VSCodium' -AllMatches

foreach ($match in $lines) {
    $line = $match.Line
    $idx = $line.IndexOf('VSCodium')
    $start = [Math]::Max(0, $idx - 30)
    $len = [Math]::Min(150, $line.Length - $start)
    Write-Host ('Line ' + $match.LineNumber + ': ...' + $line.Substring($start, $len) + '...')
}

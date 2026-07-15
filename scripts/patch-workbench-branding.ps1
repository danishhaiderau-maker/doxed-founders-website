$file = [Environment]::GetFolderPath('LocalApplicationData') + '\FounderIDE\resources\app\out\vs\workbench\workbench.desktop.main.js'
Write-Output "Patching: $file"
$text = [System.IO.File]::ReadAllText($file)
$text = $text.Replace('VSCodium version:', 'Founder IDE version:')
$text = $text.Replace('By default, VSCodium trusts "localhost" as well as the following domains:', 'By default, Founder IDE trusts "localhost" as well as the following domains:')
$text = $text.Replace('By default, VSCodium trusts "localhost".', 'By default, Founder IDE trusts "localhost".')
$text = $text.Replace('recommended by VSCodium that should not be recommended', 'recommended by Founder IDE that should not be recommended')
[System.IO.File]::WriteAllText($file, $text)
Write-Output "Workbench JS patched successfully."

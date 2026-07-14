$file = [Environment]::GetFolderPath('LocalApplicationData') + '\FounderIDE\resources\app\product.json'
Write-Output "Patching: $file"
$text = [System.IO.File]::ReadAllText($file)
$text = $text.Replace('"win32MutexName":  "vscodium"', '"win32MutexName":  "founder-ide"')
$text = $text.Replace('"win32RegValueName":  "VSCodium"', '"win32RegValueName":  "Founder IDE"')
$text = $text.Replace('"win32AppUserModelId":  "VSCodium.VSCodium"', '"win32AppUserModelId":  "FounderIDE.FounderIDE"')
$text = $text.Replace('"linuxIconName":  "vscodium"', '"linuxIconName":  "founder-ide"')
$text = $text.Replace('https://github.com/VSCodium/vscodium/blob/master/LICENSE', 'https://github.com/doxxedcryptofounder/doxedcryptofounder/blob/main/LICENSE')
[System.IO.File]::WriteAllText($file, $text)
Write-Output "product.json patched successfully."

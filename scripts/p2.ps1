$file = [Environment]::GetFolderPath('LocalApplicationData') + '\FounderIDE\resources\app\out\vs\workbench\workbench.desktop.main.js'
$text = [System.IO.File]::ReadAllText($file)
$text = $text.Replace('for VSCodium and more head over to our', 'for Founder IDE and more head over to our')
$text = $text.Replace('"VSCodium Settings"', '"Founder IDE Settings"')
$text = $text.Replace('get started in VSCodium."', 'get started in Founder IDE."')
$text = $text.Replace('VSCodium installs only newly', 'Founder IDE installs only newly')
[System.IO.File]::WriteAllText($file, $text)
$remaining = ([regex]::Matches($text, 'VSCodium')).Count
Write-Host "VSCodium refs remaining: $remaining"

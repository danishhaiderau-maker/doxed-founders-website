$file = [Environment]::GetFolderPath('LocalApplicationData') + '\FounderIDE\resources\app\out\vs\workbench\workbench.desktop.main.js'
Write-Host "Patching: $file"
$text = [System.IO.File]::ReadAllText($file)

$text = $text.Replace('searchGitHub("VSCodium/vscodium"', 'searchGitHub("doxedcryptofounder/doxedcryptofounder"')
$text = $text.Replace('raw.githubusercontent.com/VSCodium/vscodium/', 'raw.githubusercontent.com/doxxedcryptofounder/doxedcryptofounder/')
$text = $text.Replace('The core editor in VSCodium is packed with features.', 'The core editor in Founder IDE is packed with features.')
$text = $text.Replace('VSCodium comes with the powerful IntelliSense', 'Founder IDE comes with the powerful IntelliSense')
$text = $text.Replace("VSCodium's IntelliSense uses JSDoc", "Founder IDE's IntelliSense uses JSDoc")
$text = $text.Replace('features in VSCodium.  But don', 'features in Founder IDE.  But don')
$text = $text.Replace('Changes the locale of VSCodium based on', 'Changes the locale of Founder IDE based on')
$text = $text.Replace('VSCodium extension marketplace with featured', 'Founder IDE extension marketplace with featured')
$text = $text.Replace('bundled with VSCodium.', 'bundled with Founder IDE.')
$text = $text.Replace('VSCodium Release Notes', 'Founder IDE Release Notes')

[System.IO.File]::WriteAllText($file, $text)

$remaining = ([regex]::Matches($text, 'VSCodium')).Count
Write-Host "VSCodium references remaining: $remaining"

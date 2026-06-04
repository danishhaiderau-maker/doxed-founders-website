# Writes Android SDK license acceptance files (non-interactive builds).
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$sdkDir = Join-Path $root '.tools\android-sdk'
$licenses = Join-Path $sdkDir 'licenses'
New-Item -ItemType Directory -Force -Path $licenses | Out-Null

$pairs = @{
  'android-sdk-license' = "24333f8a63b6825ea9c5514f83da29cec3e04d35`n"
  'android-sdk-preview-license' = "84831b9409646a918e6c48c1210e9f2a977d4b8`n"
  'android-googletv-license' = "601085b94cd77f0b54ff86406957099ebe79c4c6`n"
  'google-gdk-license' = "33b6a2b64607f11b759f320eadf4f1baf9690f5`n"
  'intel-android-extra-license' = "d975f4516986778a2af62f750ed2d183adc750e0`n"
  'mips-android-sysimage-license' = "e9acab5b7f70c24b9498ceecc280c8dde9fe93c4`n"
}

foreach ($entry in $pairs.GetEnumerator()) {
  Set-Content -Path (Join-Path $licenses $entry.Key) -Value $entry.Value -NoNewline -Encoding ascii
}

Write-Host "Android licenses written to $licenses"

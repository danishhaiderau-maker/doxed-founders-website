# Downloads portable JDK + Android SDK into .tools/ (no admin required).
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tools = Join-Path $root '.tools'
$jdkDir = Join-Path $tools 'jdk-17'
$sdkDir = Join-Path $tools 'android-sdk'

New-Item -ItemType Directory -Force -Path $tools | Out-Null

if (-not (Test-Path (Join-Path $jdkDir 'bin\java.exe'))) {
  Write-Host 'Downloading JDK 17 (zip)...'
  $jdkZip = Join-Path $tools 'jdk.zip'
  Invoke-WebRequest -Uri 'https://aka.ms/download-jdk/microsoft-jdk-17.0.15-windows-x64.zip' -OutFile $jdkZip -UseBasicParsing
  Expand-Archive -Path $jdkZip -DestinationPath $tools -Force
  Remove-Item $jdkZip -Force
  $extracted = Get-ChildItem $tools -Directory | Where-Object { $_.Name -like 'jdk-*' } | Select-Object -First 1
  if ($extracted -and $extracted.FullName -ne $jdkDir) {
    if (Test-Path $jdkDir) { Remove-Item $jdkDir -Recurse -Force }
    Rename-Item $extracted.FullName $jdkDir
  }
}

if (-not (Test-Path (Join-Path $sdkDir 'cmdline-tools\latest\bin\sdkmanager.bat'))) {
  Write-Host 'Downloading Android command-line tools...'
  $cmdZip = Join-Path $tools 'cmdline-tools.zip'
  Invoke-WebRequest -Uri 'https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip' -OutFile $cmdZip -UseBasicParsing
  $cmdExtract = Join-Path $tools 'cmdline-tools-extract'
  if (Test-Path $cmdExtract) { Remove-Item $cmdExtract -Recurse -Force }
  Expand-Archive -Path $cmdZip -DestinationPath $cmdExtract -Force
  Remove-Item $cmdZip -Force
  $latest = Join-Path $sdkDir 'cmdline-tools\latest'
  New-Item -ItemType Directory -Force -Path (Split-Path $latest) | Out-Null
  if (Test-Path $latest) { Remove-Item $latest -Recurse -Force }
  Move-Item (Join-Path $cmdExtract 'cmdline-tools') $latest
  Remove-Item $cmdExtract -Recurse -Force

  $env:JAVA_HOME = $jdkDir
  $env:ANDROID_HOME = $sdkDir
  $env:ANDROID_SDK_ROOT = $sdkDir
  $sdkmanager = Join-Path $latest 'bin\sdkmanager.bat'
  & (Join-Path $root 'scripts/accept-android-licenses.ps1')
  & $sdkmanager --sdk_root=$sdkDir 'platform-tools' 'platforms;android-35' 'build-tools;35.0.0'
}

Write-Host "JAVA_HOME=$jdkDir"
Write-Host "ANDROID_HOME=$sdkDir"

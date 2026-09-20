# Provision a dedicated laptop-reset capability hash. The capability is typed
# locally, never loaded from the bot vault, printed, logged, put in a URL, or
# passed to a child process. Enter the same value into the dashboard's in-memory
# prompt when using Laptop Fresh Collection.
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$stateBase = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { throw 'LOCALAPPDATA_REQUIRED' }
$stateDir = Join-Path $stateBase 'DoxxedCrypto\local-research-reset'
$hashPath = Join-Path $stateDir 'capability.sha256'

function Read-SecretText([string]$Prompt) {
  $secure = Read-Host -Prompt $Prompt -AsSecureString
  $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
  finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}

$first = Read-SecretText 'Enter a new local reset capability (32+ characters)'
$second = Read-SecretText 'Enter it again'
try {
  if ($first.Length -lt 32 -or $first -cne $second) { throw 'LOCAL_RESET_CAPABILITY_MISMATCH_OR_TOO_SHORT' }
  $bytes = [Text.Encoding]::UTF8.GetBytes($first)
  $sha = [Security.Cryptography.SHA256]::Create()
  try { $hex = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
  finally { $sha.Dispose(); [Array]::Clear($bytes, 0, $bytes.Length) }
  New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
  $temporary = "$hashPath.tmp-$PID"
  [IO.File]::WriteAllText($temporary, $hex + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
  Move-Item -LiteralPath $temporary -Destination $hashPath -Force
  Write-Host 'Local laptop-reset capability hash provisioned. Restart the local controller.' -ForegroundColor Green
} finally {
  $first = $null
  $second = $null
}

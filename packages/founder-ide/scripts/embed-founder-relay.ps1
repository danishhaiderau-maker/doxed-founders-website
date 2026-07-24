[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$IdeRoot,

    [Parameter(Mandatory = $true)]
    [string]$RelayRoot
)

$ErrorActionPreference = "Stop"

$resolvedIdeRoot = (Resolve-Path -LiteralPath $IdeRoot).Path
$resolvedRelayRoot = (Resolve-Path -LiteralPath $RelayRoot).Path
$relayExecutable = Join-Path $resolvedRelayRoot "Founder Node.exe"
$relayAsar = Join-Path $resolvedRelayRoot "resources\app.asar"

if (-not (Test-Path -LiteralPath $relayExecutable)) {
    throw "Founder relay executable is missing: $relayExecutable"
}
if (-not (Test-Path -LiteralPath $relayAsar)) {
    throw "Founder relay app.asar is missing: $relayAsar"
}

$resourcesRoot = Join-Path $resolvedIdeRoot "resources"
if (-not (Test-Path -LiteralPath $resourcesRoot)) {
    throw "Founder IDE resources directory is missing: $resourcesRoot"
}

$destination = Join-Path $resourcesRoot "founder-relay"
$expectedPrefix = $resolvedIdeRoot.TrimEnd('\') + '\'
$destinationFull = [System.IO.Path]::GetFullPath($destination)
if (-not $destinationFull.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to replace relay outside Founder IDE: $destinationFull"
}

if (Test-Path -LiteralPath $destinationFull) {
    Remove-Item -LiteralPath $destinationFull -Recurse -Force
}
New-Item -ItemType Directory -Path $destinationFull -Force | Out-Null
Copy-Item -Path (Join-Path $resolvedRelayRoot "*") -Destination $destinationFull -Recurse -Force

$embeddedExecutable = Join-Path $destinationFull "Founder Node.exe"
$embeddedAsar = Join-Path $destinationFull "resources\app.asar"
if (-not (Test-Path -LiteralPath $embeddedExecutable) -or -not (Test-Path -LiteralPath $embeddedAsar)) {
    throw "Embedded Founder relay validation failed in $destinationFull"
}

Write-Host "Embedded Founder relay: $embeddedExecutable"

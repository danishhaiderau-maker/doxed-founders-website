# Partial research viewer launcher. Not a production run approval.
# Requires every argument explicitly. No PATH lookup, no environment fallback,
# no default host, port, interpreter, or data root. Does not create directories.
param(
  [string]$Interpreter = "",
  [string]$DataRoot = "",
  [string]$BindHost = "",
  [string]$Port = ""
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($Interpreter) -or [string]::IsNullOrWhiteSpace($DataRoot) -or [string]::IsNullOrWhiteSpace($BindHost) -or [string]::IsNullOrWhiteSpace($Port)) {
  Write-Error "REFUSED_MISSING_ARG: -Interpreter -DataRoot -BindHost -Port are required"
  exit 2
}
if ($BindHost -cne "127.0.0.1") {
  Write-Error "REFUSED_BIND_HOST: host must be the literal 127.0.0.1"
  exit 2
}
if ($Port -notmatch '^[0-9]+$') {
  Write-Error "REFUSED_PORT: not an integer"
  exit 2
}
$PortNumber = [int]$Port
if ($PortNumber -lt 1024 -or $PortNumber -gt 65535) {
  Write-Error "REFUSED_PORT: outside 1024-65535"
  exit 2
}
if (-not (Test-Path -LiteralPath $DataRoot -PathType Container)) {
  Write-Error "REFUSED_DATA_ROOT: missing or not a directory; not created"
  exit 2
}
if (-not (Test-Path -LiteralPath $Interpreter -PathType Leaf)) {
  Write-Error "REFUSED_INTERPRETER: explicit interpreter path missing; no PATH lookup"
  exit 2
}
$Shim = Join-Path $PSScriptRoot "..\services\btc-conservative-agent\research_dashboard.py"
if (-not (Test-Path -LiteralPath $Shim -PathType Leaf)) {
  Write-Error "REFUSED_ENTRYPOINT: shim missing"
  exit 2
}
& $Interpreter $Shim --standalone --viewer-mode --bind-host $BindHost --port "$PortNumber" --data-root $DataRoot
exit $LASTEXITCODE

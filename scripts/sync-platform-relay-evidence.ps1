param(
  [Parameter(Mandatory=$true)][string]$ApiBaseUrl,
  [Parameter(Mandatory=$true)][string]$AgentSlug,
  [Parameter(Mandatory=$true)][string]$UserId,
  [Parameter(Mandatory=$true)][string]$Destination
)
$ErrorActionPreference = 'Stop'
$adminToken = [Environment]::GetEnvironmentVariable('BOT_ADMIN_TOKEN', 'Process')
if ([string]::IsNullOrWhiteSpace($adminToken)) {
  throw 'BOT_ADMIN_TOKEN must be supplied through the process environment or secret vault.'
}
$uri = "$($ApiBaseUrl.TrimEnd('/'))/trading-agents/$AgentSlug/ops/relay-evidence?userId=$([uri]::EscapeDataString($UserId))"
$payload = Invoke-RestMethod -Method Get -Uri $uri -Headers @{ 'X-Bot-Admin-Token' = $adminToken }
if ($payload.schema -ne 'relay_lifecycle_evidence_v1' -or -not $payload.generatedAt -or -not $payload.generatingRevision -or -not $payload.runIdentity) {
  throw 'Relay evidence provenance is incomplete; refusing to publish an unqualified artifact.'
}
if ($null -eq $payload.records) { throw 'Relay evidence records are missing.' }
$parent = Split-Path -Parent $Destination
if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
$temp = "$Destination.$([guid]::NewGuid().ToString('N')).tmp"
try {
  $payload | ConvertTo-Json -Depth 100 | Set-Content -LiteralPath $temp -Encoding utf8
  Move-Item -LiteralPath $temp -Destination $Destination -Force
} finally {
  if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
}
Write-Output $Destination

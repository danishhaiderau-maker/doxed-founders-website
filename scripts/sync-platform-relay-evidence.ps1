param()

$ErrorActionPreference = 'Stop'
$maxBytes = 25MB
$maxAgeMinutes = 15
$futureToleranceMinutes = 5

function Stop-RelayEvidenceSync([string]$Code) {
  throw "[RELAY_EVIDENCE_$Code]"
}

$apiBaseUrl = [Environment]::GetEnvironmentVariable('PLATFORM_API_BASE_URL', 'Process')
$agentSlug = [Environment]::GetEnvironmentVariable('PLATFORM_RELAY_AGENT_SLUG', 'Process')
$userId = [Environment]::GetEnvironmentVariable('PLATFORM_RELAY_USER_ID', 'Process')
$destination = [Environment]::GetEnvironmentVariable('PLATFORM_RELAY_EVIDENCE_FILE', 'Process')
$sourceBotUrl = [Environment]::GetEnvironmentVariable('PLATFORM_SOURCE_BOT_URL', 'Process')
$adminToken = [Environment]::GetEnvironmentVariable('BOT_ADMIN_TOKEN', 'Process')
if (@($apiBaseUrl, $agentSlug, $userId, $destination, $sourceBotUrl, $adminToken) |
    Where-Object { [string]::IsNullOrWhiteSpace($_) }) {
  Stop-RelayEvidenceSync 'CONFIG_MISSING'
}
if ($env:PLATFORM_RELAY_EVIDENCE_MAX_AGE_MINUTES) {
  try { $maxAgeMinutes = [Math]::Max(1, [int]$env:PLATFORM_RELAY_EVIDENCE_MAX_AGE_MINUTES) }
  catch { Stop-RelayEvidenceSync 'CONFIG_INVALID' }
}

$uri = "$($apiBaseUrl.TrimEnd('/'))/trading-agents/$([uri]::EscapeDataString($agentSlug))/ops/relay-evidence?userId=$([uri]::EscapeDataString($userId))"
try {
  $response = Invoke-WebRequest -Method Get -Uri $uri -Headers @{
    'X-Bot-Admin-Token' = $adminToken
    'Accept' = 'application/json'
  } -TimeoutSec 45 -UseBasicParsing
} catch {
  # Never include the underlying exception: PowerShell may embed the URI,
  # query-scoped user ID, or request details in it.
  Stop-RelayEvidenceSync 'HTTP_FAILED'
}
$raw = [string]$response.Content
if ([Text.Encoding]::UTF8.GetByteCount($raw) -gt $maxBytes) {
  Stop-RelayEvidenceSync 'TOO_LARGE'
}
try { $payload = $raw | ConvertFrom-Json } catch { Stop-RelayEvidenceSync 'JSON_INVALID' }

if ($payload.schema -ne 'relay_lifecycle_evidence_v1') { Stop-RelayEvidenceSync 'SCHEMA_INVALID' }
if (-not $payload.generatedAt -or -not $payload.generatingRevision -or -not $payload.runIdentity) {
  Stop-RelayEvidenceSync 'PROVENANCE_INCOMPLETE'
}
if ([string]$payload.generatingRevision -notmatch '^[0-9a-fA-F]{7,64}$') {
  Stop-RelayEvidenceSync 'REVISION_INVALID'
}
if ([string]$payload.agentSlug -cne [string]$agentSlug -or
    [string]$payload.userId -cne [string]$userId) {
  Stop-RelayEvidenceSync 'SCOPE_MISMATCH'
}
$generatedAtRaw = $payload.generatedAt
$generatedAt = [DateTimeOffset]::MinValue
if ($generatedAtRaw -is [DateTimeOffset]) {
  $generatedAt = [DateTimeOffset]$generatedAtRaw
} elseif ($generatedAtRaw -is [DateTime]) {
  $generatedAt = [DateTimeOffset]([DateTime]$generatedAtRaw)
} elseif (-not [DateTimeOffset]::TryParse(
  [string]$generatedAtRaw,
  [Globalization.CultureInfo]::InvariantCulture,
  [Globalization.DateTimeStyles]::RoundtripKind,
  [ref]$generatedAt
)) {
  Stop-RelayEvidenceSync 'GENERATED_AT_INVALID'
}
$now = [DateTimeOffset]::UtcNow
if ($generatedAt -gt $now.AddMinutes($futureToleranceMinutes)) { Stop-RelayEvidenceSync 'FUTURE' }
if ($generatedAt -lt $now.AddMinutes(-$maxAgeMinutes)) { Stop-RelayEvidenceSync 'STALE' }
if ($null -eq $payload.records) { Stop-RelayEvidenceSync 'RECORDS_MISSING' }

$eventIds = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($record in @($payload.records)) {
  if ($null -eq $record -or -not $record.canonicalTradeId -or
      -not $record.lifecycleId -or -not $record.participantId -or
      $null -eq $record.events) {
    Stop-RelayEvidenceSync 'RECORD_INVALID'
  }
  foreach ($event in @($record.events)) {
    if ($null -eq $event -or -not $event.id -or -not $event.eventType -or -not $event.createdAt) {
      Stop-RelayEvidenceSync 'EVENT_INVALID'
    }
    if (-not $eventIds.Add([string]$event.id)) { Stop-RelayEvidenceSync 'DUPLICATE_EVENT' }
  }
}

# Forward the exact validated bytes to the volume-backed source bot so its
# append-derived counterfactual worker can join platform events by canonical
# trade ID. Forwarding happens before local replacement: any network or ACK
# contradiction leaves the previous qualified local artifact untouched.
$sha256 = [Security.Cryptography.SHA256]::Create()
try {
  $digest = ([BitConverter]::ToString(
    $sha256.ComputeHash([Text.Encoding]::UTF8.GetBytes($raw))
  ) -replace '-', '').ToLowerInvariant()
} finally {
  $sha256.Dispose()
}
try {
  $forward = Invoke-RestMethod -Method Post `
    -Uri ($sourceBotUrl.TrimEnd('/') + '/api/data-sync/platform-relay-evidence') `
    -Headers @{ 'X-Bot-Admin-Token' = $adminToken; 'Content-Type' = 'application/json' } `
    -Body ([Text.Encoding]::UTF8.GetBytes($raw)) -TimeoutSec 45
} catch {
  Stop-RelayEvidenceSync 'FORWARD_FAILED'
}
if ($forward.ok -ne $true -or [string]$forward.schema -ne 'relay_lifecycle_evidence_v1' -or
    [string]$forward.sha256 -cne $digest -or [int]$forward.records -ne @($payload.records).Count) {
  Stop-RelayEvidenceSync 'FORWARD_ACK_INVALID'
}

$parent = Split-Path -Parent $destination
if (-not $parent) { Stop-RelayEvidenceSync 'DESTINATION_INVALID' }
New-Item -ItemType Directory -Force -Path $parent | Out-Null
$temp = "$destination.$([guid]::NewGuid().ToString('N')).tmp"
try {
  [IO.File]::WriteAllText($temp, $raw, [Text.UTF8Encoding]::new($false))
  Move-Item -LiteralPath $temp -Destination $destination -Force
} catch {
  Stop-RelayEvidenceSync 'PUBLISH_FAILED'
} finally {
  if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue }
}
Write-Output $destination

# Shared helpers for the laptop research chain: ACK watcher, analyzer runner,
# monitor and supervisor. Windows PowerShell 5.1 compatible (the supervisor
# runs under the system powershell.exe).

function Get-UtcNowIso { [datetime]::UtcNow.ToString('o') }

function Assert-NotOneDrive([string]$Path) {
  if ($Path -and $Path.ToLowerInvariant().Contains('\onedrive\')) {
    throw "LAPTOP_CHAIN_ONEDRIVE_REFUSED: $Path"
  }
}

function Get-LaptopChainConfig {
  param([string]$RepoRoot = '', [string]$CanonicalRoot = '', [string]$StateDir = '', [string]$SourceUrl = '')
  if (-not $RepoRoot) { $RepoRoot = Split-Path -Parent $PSScriptRoot }
  if (-not $CanonicalRoot) {
    $CanonicalRoot = if ($env:DOXXED_CANONICAL_ROOT) { $env:DOXXED_CANONICAL_ROOT } else { 'C:\DoxxedCrypto\btc-v31-current' }
  }
  if (-not $StateDir) {
    $StateDir = if ($env:DOXXED_LAPTOP_CHAIN_STATE) { $env:DOXXED_LAPTOP_CHAIN_STATE } else { 'C:\DoxxedCrypto\laptop-chain' }
  }
  if (-not $SourceUrl) { $SourceUrl = 'https://doxed-btc-bot.fly.dev' }
  $RepoRoot = [System.IO.Path]::GetFullPath($RepoRoot).TrimEnd('\')
  $CanonicalRoot = [System.IO.Path]::GetFullPath($CanonicalRoot).TrimEnd('\')
  $StateDir = [System.IO.Path]::GetFullPath($StateDir).TrimEnd('\')
  foreach ($path in @($RepoRoot, $CanonicalRoot, $StateDir)) { Assert-NotOneDrive $path }
  $dataRoot = Join-Path $CanonicalRoot 'services\btc-conservative-agent\canonical-research-data'
  $config = [pscustomobject]@{
    RepoRoot = $RepoRoot
    CanonicalRoot = $CanonicalRoot
    StateDir = $StateDir
    DataRoot = $dataRoot
    SourceUrl = $SourceUrl.TrimEnd('/')
    LogDir = Join-Path $StateDir 'logs'
    RunDir = Join-Path $StateDir 'run'
    QuarantineDir = Join-Path $StateDir 'quarantine'
    AlertDir = Join-Path $StateDir 'alerts'
    # Path only — never load the vault here. Admin token enters process env
    # solely via Import-LaptopChainVaultAllowlist / Get-AdminToken (allowlist).
    VaultEnv = Join-Path (Split-Path -Parent $CanonicalRoot) 'doxedcryptofounder-secrets\vault\home-bot.env'
    WatcherStatus = Join-Path $StateDir 'laptop-ack-watcher.status.json'
    WatcherLock = Join-Path $StateDir 'laptop-ack-watcher.lock.json'
    AnalyzerStatus = Join-Path $StateDir 'analyzer-run.status.json'
    CycleStatus = Join-Path $StateDir 'segment-analyzer-cycle.status.json'
    MonitorState = Join-Path $StateDir 'laptop-chain-monitor.state.json'
    HeartbeatFile = Join-Path $dataRoot '.fly-data-sync-loop.heartbeat.json'
    UpstreamIdentityFile = Join-Path $dataRoot '.fly-upstream-identity.json'
    GenerationLeaseFile = Join-Path $dataRoot '.fly-mirror-generation.lease'
    SegmentShadowRoot = $(if ($env:RESEARCH_SEGMENT_SHADOW_ROOT) { $env:RESEARCH_SEGMENT_SHADOW_ROOT } else { 'C:\DoxxedCrypto\fly-mirror-segments' })
    FlySegmentHeadFile = Join-Path $StateDir 'fly_segment_head_snapshot_v1.json'
    RelayStatusSnapshotFile = Join-Path $StateDir 'relay_status_snapshot_v1.json'
  }
  foreach ($dir in @($config.StateDir, $config.LogDir, $config.RunDir, $config.QuarantineDir, $config.AlertDir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }
  return $config
}

function Write-ChainLog {
  param([Parameter(Mandatory = $true)]$Config, [Parameter(Mandatory = $true)][string]$Name, [Parameter(Mandatory = $true)][string]$Message)
  $file = Join-Path $Config.LogDir ('{0}-{1}.log' -f $Name, [datetime]::UtcNow.ToString('yyyyMMdd'))
  $line = '{0} pid={1} {2}' -f (Get-UtcNowIso), $PID, ($Message -replace '[\r\n]+', ' ')
  Add-Content -LiteralPath $file -Value $line -Encoding UTF8
  Write-Host $line
}

# One file per UTC day; files older than RetainDays move to logs\archive.
# Logs are archived, never deleted.
function Invoke-ChainLogRotation {
  param([Parameter(Mandatory = $true)]$Config, [int]$RetainDays = 14)
  $archive = Join-Path $Config.LogDir 'archive'
  $cutoff = [datetime]::UtcNow.AddDays(-$RetainDays)
  foreach ($file in @(Get-ChildItem -LiteralPath $Config.LogDir -File -Filter '*.log' -ErrorAction SilentlyContinue)) {
    if ($file.LastWriteTimeUtc -lt $cutoff) {
      New-Item -ItemType Directory -Path $archive -Force | Out-Null
      Move-Item -LiteralPath $file.FullName -Destination (Join-Path $archive $file.Name) -Force -ErrorAction SilentlyContinue
    }
  }
}

function Write-JsonAtomic {
  param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)]$Value, [int]$Depth = 8)
  $target = [System.IO.Path]::GetFullPath($Path)
  $temporary = "$target.tmp-$PID-$([Guid]::NewGuid().ToString('N'))"
  $encoding = New-Object System.Text.UTF8Encoding($false)
  try {
    [System.IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth $Depth) + [Environment]::NewLine, $encoding)
    Move-Item -LiteralPath $temporary -Destination $target -Force
  } finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue }
  }
}

function Read-JsonFile([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  try { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}

function ConvertTo-UtcDate($Value) {
  if (-not $Value) { return $null }
  try {
    return ([DateTimeOffset]::Parse([string]$Value, [Globalization.CultureInfo]::InvariantCulture)).UtcDateTime
  } catch { return $null }
}

function Get-ProcessStartUtc([int]$ProcessId) {
  if ($ProcessId -le 0) { return $null }
  try { return (Get-Process -Id $ProcessId -ErrorAction Stop).StartTime.ToUniversalTime() } catch { return $null }
}

# A pid alone can be reused; an owner is alive only when the recorded start
# time matches the running process.
function Test-RecordedOwnerAlive {
  param($ProcessId, $StartedAt)
  $ownerPid = 0
  if (-not [int]::TryParse([string]$ProcessId, [ref]$ownerPid) -or $ownerPid -le 0) { return $false }
  $actual = Get-ProcessStartUtc $ownerPid
  if ($null -eq $actual) { return $false }
  $recorded = ConvertTo-UtcDate $StartedAt
  if ($null -eq $recorded) { return $true }
  return [Math]::Abs(($actual - $recorded).TotalSeconds) -le 5
}

# Native tools write progress to stderr; under ErrorActionPreference=Stop,
# Windows PowerShell turns that into a terminating error.
function Invoke-NativeQuiet([scriptblock]$Command) {
  $previous = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try { & $Command 2>$null } finally { $ErrorActionPreference = $previous }
}

function Get-ChainMutexName([string]$Suffix) {
  $prefix = if ($env:DOXXED_LAPTOP_CHAIN_MUTEX_PREFIX) { $env:DOXXED_LAPTOP_CHAIN_MUTEX_PREFIX } else { 'Doxxed' }
  return "$prefix$Suffix"
}

# Named mutex: the OS releases it when the owner dies, so a dead owner is
# taken over (AbandonedMutexException) without killing anything.
function Enter-SingleInstance {
  param([Parameter(Mandatory = $true)][string]$Name)
  $mutex = $null
  foreach ($namespace in @('Global', 'Local')) {
    try { $mutex = New-Object System.Threading.Mutex($false, "$namespace\$Name"); break } catch { $mutex = $null }
  }
  if (-not $mutex) { throw "MUTEX_UNAVAILABLE: $Name" }
  try {
    if ($mutex.WaitOne(0)) { return [pscustomobject]@{ Mutex = $mutex; Name = $Name; Abandoned = $false } }
  } catch [System.Threading.AbandonedMutexException] {
    return [pscustomobject]@{ Mutex = $mutex; Name = $Name; Abandoned = $true }
  }
  $mutex.Dispose()
  return $null
}

function Exit-SingleInstance($Handle) {
  if (-not $Handle) { return }
  try { $Handle.Mutex.ReleaseMutex() } catch { }
  $Handle.Mutex.Dispose()
}

function Test-SingleInstanceHeld([string]$Name) {
  $probe = Enter-SingleInstance -Name $Name
  if ($probe) { Exit-SingleInstance $probe; return $false }
  return $true
}

function Get-SegmentPullSleepSeconds {
  # Poll quickly while Fly is draining a backlog (it publishes large segments
  # back-to-back) or when the head is ahead of what was just applied.
  param($Pull, [int]$IntervalSec, [int]$CatchUpIntervalSec, [long]$CatchUpUnshippedBytes)
  if (-not $Pull -or -not $Pull.ok -or -not $Pull.remote_head) { return $IntervalSec }
  $head = $Pull.remote_head
  $unshipped = 0L
  if ($null -ne $head.unshipped_bytes) { $unshipped = [long]$head.unshipped_bytes }
  $behind = ($null -ne $head.published_seq) -and ($null -ne $Pull.applied_seq) -and ([long]$head.published_seq -gt [long]$Pull.applied_seq)
  if ($behind -or $unshipped -gt $CatchUpUnshippedBytes) { return [Math]::Min($IntervalSec, $CatchUpIntervalSec) }
  return $IntervalSec
}

function Get-FlyHealth([string]$SourceUrl) {
  return Invoke-RestMethod -Uri "$SourceUrl/health" -TimeoutSec 30 -UseBasicParsing
}

function Publish-UpstreamIdentity {
  param([Parameter(Mandatory = $true)]$Config, [Parameter(Mandatory = $true)]$Health)
  $identity = [ordered]@{
    schema = 'fly_upstream_identity_v1'
    observed_at = Get-UtcNowIso
    source = "$($Config.SourceUrl)/health"
    source_git_rev = [string]$Health.source_git_rev
    analyzer_sync_id = [string]$Health.analyzer_sync_id
    bot_version = [string]$Health.bot_version
    tile_registry_signature = [string]$Health.tile_registry_signature
    tile_registry_schema = [string]$Health.tile_registry_schema
  }
  if (Test-Path -LiteralPath $Config.DataRoot -PathType Container) {
    Write-JsonAtomic -Path $Config.UpstreamIdentityFile -Value $identity
  }
  return [pscustomobject]$identity
}

# RK 05:12 / HM HOLD 7767d31f7f94 + b5700e72694d + RK 08:26: never put
# BITFINEX_* into supervisor/bot process env from home-bot.env. Values never
# printed. True allowlists only (denylist mode removed). Each BotAllowlist
# name is justified (code ref) or dropped — see allowlist table in #981 asks.
$script:LaptopChainVaultAllowlist = @('BOT_ADMIN_TOKEN')
# Bot starters: explicit non-BITFINEX names only (disaster-recovery / showcase).
# Dropped vs rev3: PLATFORM_* (snapshots load via home-bot-vault-env), 
# LIFECYCLE_LAPTOP_ATTESTATION_KEY_ID (no getenv reader).
# Keep reasons (RK 09:33 / HM HOLD 716511e8582d) — full table in #981 asks:
# BOT_ADMIN_TOKEN — bot admin auth (btc_conservative_agent / bot.py)
# BOT_CONTROL_SECRET — relay/control HMAC (bot.py)
# SHOWCASE_WEBHOOK_SECRET — showcase webhook HMAC (bot.py)
# SHOWCASE_RELAY_WEBHOOK_URL — paper relay delivery URL (bot.py)
# CRASH_NOTIFY_WEBHOOK — crash notify (start-home-bot / bot-auto-restart)
# LIFECYCLE_LAPTOP_ATTESTATION_KEY — lifecycle cleanup attest (bot.py)
# LIFECYCLE_CLEANUP_ENABLED — lifecycle cleanup toggle (bot.py)
# DROPPED DEEPSEEK_API_KEY — code path exists (bot.py getenv) but no observed
#   monthly R50 USD figure in-repo; RK 09:33 requires real cost or drop.
# DROPPED MONITOR_READ_TOKEN — NOT GET-only: Nest assertBotReadToken is the
#   only auth on POST ops pause + POST ops refresh-flat-audit
#   (trading-agents.controller.ts). Separate apps/api PR (other worker) must
#   require assertBotAdminToken on those POSTs; do not vault-export here.
# ADDED LAPTOP_MONITOR_READ_TOKEN — bot.py GET-only monitor routes
#   (_laptop_monitor_read_authorized); never Nest POST; bot process only.
$script:LaptopChainBotVaultAllowlist = @(
  'BOT_ADMIN_TOKEN',
  'BOT_CONTROL_SECRET',
  'SHOWCASE_WEBHOOK_SECRET',
  'SHOWCASE_RELAY_WEBHOOK_URL',
  'CRASH_NOTIFY_WEBHOOK',
  'LIFECYCLE_LAPTOP_ATTESTATION_KEY',
  'LIFECYCLE_CLEANUP_ENABLED',
  'LAPTOP_MONITOR_READ_TOKEN'
)
$script:BitfinexEnvRefusalExitCode = 79
# RK 12:27: bot starters refuse when LAPTOP_MONITOR_READ_TOKEN empty or equals MONITOR_READ_TOKEN.
$script:LaptopMonitorTokenRefusalExitCode = 78

function Test-LaptopChainVaultNameAllowed {
  param(
    [string]$Name,
    [ValidateSet('AdminTokenOnly', 'BotAllowlist')][string]$Mode = 'AdminTokenOnly'
  )
  if (-not $Name) { return $false }
  if ($Name.StartsWith('BITFINEX_', [StringComparison]::OrdinalIgnoreCase)) { return $false }
  $list = if ($Mode -eq 'BotAllowlist') {
    $script:LaptopChainBotVaultAllowlist
  } else {
    $script:LaptopChainVaultAllowlist
  }
  foreach ($allowed in $list) {
    if ([string]::Equals($allowed, $Name, [StringComparison]::Ordinal)) { return $true }
  }
  return $false
}

function Read-LaptopChainVaultValue {
  param(
    [Parameter(Mandatory = $true)][string]$VaultEnv,
    [Parameter(Mandatory = $true)][string]$Name,
    [ValidateSet('AdminTokenOnly', 'BotAllowlist')][string]$Mode = 'AdminTokenOnly'
  )
  if (-not (Test-LaptopChainVaultNameAllowed -Name $Name -Mode $Mode)) { return $null }
  if (-not (Test-Path -LiteralPath $VaultEnv -PathType Leaf)) { return $null }
  $pattern = '^\s*' + [regex]::Escape($Name) + '=(.*)$'
  foreach ($line in Get-Content -LiteralPath $VaultEnv -ErrorAction SilentlyContinue) {
    if ($line -match $pattern) {
      return [string]$Matches[1].Trim().Trim('"').Trim("'")
    }
  }
  return $null
}

# True when a BITFINEX_* binding must refuse (RK 08:26): KEY/SECRET/TOKEN in
# the name, or BITFINEX_LIVE_ENABLED whose value is not exactly '0'.
function Test-BitfinexEnvNameRefused {
  param(
    [Parameter(Mandatory = $true)][string]$Name,
    [AllowNull()][string]$Value
  )
  if (-not ($Name -like 'BITFINEX_*')) { return $false }
  if ([string]::Equals($Name, 'BITFINEX_LIVE_ENABLED', [StringComparison]::OrdinalIgnoreCase)) {
    return ($Value -cne '0')
  }
  $upper = $Name.ToUpperInvariant()
  return ($upper.Contains('KEY') -or $upper.Contains('SECRET') -or $upper.Contains('TOKEN'))
}

# COUNT of refused BITFINEX_* names in this process env. Never returns/prints values.
function Get-BitfinexProcessEnvNameCount {
  $count = 0
  $vars = [Environment]::GetEnvironmentVariables('Process')
  foreach ($key in @($vars.Keys)) {
    $name = [string]$key
    if (Test-BitfinexEnvNameRefused -Name $name -Value ([string]$vars[$key])) { $count++ }
  }
  return $count
}

# Remove every BITFINEX_* name from process env. Returns how many names were
# cleared. Never prints values. Starters re-set BITFINEX_LIVE_ENABLED=0 after.
function Clear-BitfinexProcessEnvNames {
  $cleared = 0
  foreach ($key in @([Environment]::GetEnvironmentVariables('Process').Keys)) {
    if ([string]$key -like 'BITFINEX_*') {
      [Environment]::SetEnvironmentVariable([string]$key, $null, 'Process')
      $cleared++
    }
  }
  return $cleared
}

# Log names-only refused count; exit 79 if any refused name remains (fail closed).
# Used inside launched processes / supervisor children — NOT in bot starters
# (RK 08:26: starters = allowlist filter only; no pre-strip refusal).
function Assert-BitfinexProcessEnvAbsent {
  param(
    $Config = $null,
    [string]$LogName = ''
  )
  $count = Get-BitfinexProcessEnvNameCount
  $message = "BITFINEX_ENV_NAME_COUNT count=$count"
  if ($Config -and $LogName) {
    Write-ChainLog -Config $Config -Name $LogName -Message $message
  } else {
    Write-Host $message
  }
  if ($count -gt 0) {
    $refuse = "REFUSED_BITFINEX_ENV_NAMES count=$count"
    if ($Config -and $LogName) {
      Write-ChainLog -Config $Config -Name $LogName -Message $refuse
    } else {
      Write-Host $refuse
    }
    exit $script:BitfinexEnvRefusalExitCode
  }
}

# AdminTokenOnly (default): BOT_ADMIN_TOKEN only (laptop chain / analyzer cycle).
# BotAllowlist: explicit non-BITFINEX bot starter names only (true allowlist).
# Never prints values. RK 09:33: do NOT strip inherited BITFINEX_* — vault never
# sets those names; inherited KEY/SECRET/TOKEN must reach in-process exit 79.
function Import-LaptopChainVaultAllowlist {
  param(
    [Parameter(Mandatory = $true)][string]$VaultEnv,
    [ValidateSet('AdminTokenOnly', 'BotAllowlist')][string]$Mode = 'AdminTokenOnly'
  )
  if (-not (Test-Path -LiteralPath $VaultEnv -PathType Leaf)) {
    return
  }
  $names = if ($Mode -eq 'BotAllowlist') {
    $script:LaptopChainBotVaultAllowlist
  } else {
    $script:LaptopChainVaultAllowlist
  }
  foreach ($name in $names) {
    if (-not (Test-LaptopChainVaultNameAllowed -Name $name -Mode $Mode)) { continue }
    $value = Read-LaptopChainVaultValue -VaultEnv $VaultEnv -Name $name -Mode $Mode
    if (-not $value) { continue }
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
  }
}

# Read a vault line by exact name for compare-only (never exports into process env;
# never prints the value). Used to detect LAPTOP == MONITOR without vault-exporting MONITOR.
function Read-VaultEnvLineCompareOnly {
  param(
    [Parameter(Mandatory = $true)][string]$VaultEnv,
    [Parameter(Mandatory = $true)][string]$Name
  )
  if (-not $Name) { return $null }
  if (-not (Test-Path -LiteralPath $VaultEnv -PathType Leaf)) { return $null }
  $pattern = '^\s*' + [regex]::Escape($Name) + '=(.*)$'
  foreach ($line in Get-Content -LiteralPath $VaultEnv -ErrorAction SilentlyContinue) {
    if ($line -match $pattern) {
      return [string]$Matches[1].Trim().Trim('"').Trim("'")
    }
  }
  return $null
}

# RK 12:27: bot process start refuses when LAPTOP_MONITOR_READ_TOKEN is empty/short
# or equals MONITOR_READ_TOKEN. Names/reasons only — never prints values.
function Assert-LaptopMonitorReadTokenForBotStart {
  param(
    [Parameter(Mandatory = $true)][string]$VaultEnv
  )
  $minLen = 24
  $laptop = [Environment]::GetEnvironmentVariable('LAPTOP_MONITOR_READ_TOKEN', 'Process')
  if (-not $laptop) { $laptop = '' }
  $laptop = $laptop.Trim()
  if ($laptop.Length -lt $minLen) {
    Write-Host 'REFUSED_LAPTOP_MONITOR_READ_TOKEN empty_or_short'
    exit $script:LaptopMonitorTokenRefusalExitCode
  }
  $monitor = [Environment]::GetEnvironmentVariable('MONITOR_READ_TOKEN', 'Process')
  if (-not $monitor) {
    $monitor = Read-VaultEnvLineCompareOnly -VaultEnv $VaultEnv -Name 'MONITOR_READ_TOKEN'
  }
  if (-not $monitor) { $monitor = '' }
  $monitor = $monitor.Trim()
  if ($monitor.Length -gt 0 -and [string]::Equals($laptop, $monitor, [StringComparison]::Ordinal)) {
    Write-Host 'REFUSED_LAPTOP_MONITOR_READ_TOKEN equals_MONITOR_READ_TOKEN'
    exit $script:LaptopMonitorTokenRefusalExitCode
  }
}

function Get-AdminToken([string]$VaultEnv) {
  if ($env:BOT_ADMIN_TOKEN) { return $env:BOT_ADMIN_TOKEN }
  $token = Read-LaptopChainVaultValue -VaultEnv $VaultEnv -Name 'BOT_ADMIN_TOKEN' -Mode 'AdminTokenOnly'
  if (-not $token) { throw 'BOT_ADMIN_TOKEN_UNAVAILABLE' }
  return $token
}

# ACKs are proven by the immutable terminal membership receipts the sync
# client writes only after a validated remote FINALIZE.
function Get-AckedGenerations([string]$DataRoot) {
  $set = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
  $last = $null
  $dir = Join-Path $DataRoot 'receipts\terminal-transfer-membership'
  $files = @(Get-ChildItem -LiteralPath $dir -File -Filter 'terminal-transfer-membership-*.json' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTimeUtc)
  foreach ($file in $files) {
    if ($file.Name -match '^terminal-transfer-membership-([0-9a-f]{64})-[0-9a-f]{32}\.json$') {
      [void]$set.Add($Matches[1])
      $last = [pscustomobject]@{ generation = $Matches[1]; receipt = $file.FullName; writtenAt = $file.LastWriteTimeUtc.ToString('o') }
    }
  }
  return [pscustomobject]@{ Set = $set; Last = $last }
}

# Never leave an in-progress receipt behind: preserve it, then publish a
# terminal failure with a bounded retry declaration the analyzer can honour.
function Set-SyncHeartbeatTerminalFailure {
  param(
    [Parameter(Mandatory = $true)]$Config,
    [Parameter(Mandatory = $true)][string]$Reason,
    [int]$ConsecutiveFailures = 1,
    [int]$BackoffSec = 60
  )
  $current = Read-JsonFile $Config.HeartbeatFile
  if ($null -eq $current -or $current.inProgress -ne $true) { return $false }
  # A running sync loop owns and closes its own receipt.
  if (Test-SyncLoopGuardHeld) { return $false }
  $stamp = [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfffZ')
  Copy-Item -LiteralPath $Config.HeartbeatFile -Destination (Join-Path $Config.QuarantineDir "heartbeat-in-progress-$stamp.json") -Force
  $now = [datetime]::UtcNow
  $backoff = [Math]::Max(60, [Math]::Min(1800, $BackoffSec))
  $terminal = [ordered]@{}
  foreach ($property in $current.PSObject.Properties) { $terminal[$property.Name] = $property.Value }
  $terminal['ok'] = $false
  $terminal['inProgress'] = $false
  $terminal['phase'] = 'failed'
  $terminal['failureCode'] = $Reason
  $terminal['abandonedPhase'] = [string]$current.phase
  $terminal['syncedAt'] = $now.ToString('o')
  $terminal['pollOk'] = $false
  $terminal['pollFailedAt'] = $now.ToString('o')
  $terminal['pollStage'] = 'laptop_ack_watcher'
  $terminal['pollError'] = $Reason
  $terminal['consecutiveFailures'] = [Math]::Max(1, $ConsecutiveFailures)
  $terminal['backoffSec'] = $backoff
  $terminal['nextRetryAt'] = $now.AddSeconds($backoff).ToString('o')
  Write-JsonAtomic -Path $Config.HeartbeatFile -Value $terminal
  return $true
}

function Test-InProgressHeartbeatAbandoned {
  param([Parameter(Mandatory = $true)]$Config, [int]$StaleMinutes = 15)
  $current = Read-JsonFile $Config.HeartbeatFile
  if ($null -eq $current -or $current.inProgress -ne $true) { return $false }
  if ($current.PSObject.Properties.Name -contains 'ownerPid' -and $current.ownerPid) {
    if (-not (Test-RecordedOwnerAlive -ProcessId $current.ownerPid -StartedAt $current.ownerStartedAt)) { return $true }
  }
  $updated = ConvertTo-UtcDate $(if ($current.updatedAt) { $current.updatedAt } else { $current.syncedAt })
  return ($null -eq $updated) -or (([datetime]::UtcNow - $updated).TotalMinutes -gt $StaleMinutes)
}

# The sync loop holds this guard for its lifetime; while it is held the loop,
# not the watcher, owns the mirror.
function Test-SyncLoopGuardHeld {
  $base = if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { [System.IO.Path]::GetTempPath() }
  $guard = Join-Path $base 'DoxxedCrypto\locks\.fly-data-sync-loop.guard'
  if (-not (Test-Path -LiteralPath $guard)) { return $false }
  try {
    $stream = [System.IO.File]::Open($guard, 'Open', 'ReadWrite', 'None')
    $stream.Dispose()
    return $false
  } catch { return $true }
}

function Resolve-FullRevision {
  param([Parameter(Mandatory = $true)][string]$RepoRoot, [Parameter(Mandatory = $true)][string]$Revision)
  if ($Revision -notmatch '^[0-9a-fA-F]{7,40}$') { return $null }
  foreach ($attempt in 1..2) {
    $full = Invoke-NativeQuiet { & git -C $RepoRoot rev-parse --verify --quiet "$Revision^{commit}" }
    if ($LASTEXITCODE -eq 0 -and [string]$full -match '^[0-9a-f]{40}$') { return ([string]$full).Trim() }
    if ($attempt -eq 1) { Invoke-NativeQuiet { & git -C $RepoRoot fetch --quiet origin } | Out-Null }
  }
  return $null
}

function Test-RevisionPrefixMatch([string]$Left, [string]$Right) {
  $a = $Left.Trim().ToLowerInvariant(); $b = $Right.Trim().ToLowerInvariant()
  return [bool]($a -and $b -and ($a.StartsWith($b) -or $b.StartsWith($a)))
}

# The registry has one research-mode switch. Pick the value that reproduces
# the identity Fly reports, so the analyzer compares like with like; when no
# value matches, run the default and let /api/status report the mismatch.
function Resolve-AnalyzerResearchMode {
  param([Parameter(Mandatory = $true)][string]$RepoRoot, $Health)
  $agentDir = Join-Path $RepoRoot 'services\btc-conservative-agent'
  # Windows PowerShell strips embedded double quotes from native arguments; keep the probe quote-free.
  $probe = 'import combo_pathway_config as c; print(c.ANALYZER_SYNC_ID); print(c.active_tile_registry_signature())'
  $previous = $env:SCORE_LED_PAPER_RESEARCH_ENABLED
  $candidates = @()
  try {
    foreach ($flag in @('', '1')) {
      $env:SCORE_LED_PAPER_RESEARCH_ENABLED = $flag
      Push-Location $agentDir
      try { $out = Invoke-NativeQuiet { & python -c $probe } } finally { Pop-Location }
      $lines = @($out | ForEach-Object { [string]$_ } | Where-Object { $_.Trim() })
      if ($LASTEXITCODE -eq 0 -and $lines.Count -ge 2) {
        $candidates += [pscustomobject]@{ Flag = $flag; SyncId = $lines[-2].Trim(); Signature = $lines[-1].Trim() }
      }
    }
  } finally {
    $env:SCORE_LED_PAPER_RESEARCH_ENABLED = $previous
  }
  foreach ($candidate in $candidates) {
    if ($Health -and $candidate.SyncId -eq [string]$Health.analyzer_sync_id -and
        $candidate.Signature -eq [string]$Health.tile_registry_signature) {
      return [pscustomobject]@{ Flag = $candidate.Flag; Matched = $true; SyncId = $candidate.SyncId; Signature = $candidate.Signature }
    }
  }
  $default = $candidates | Select-Object -First 1
  return [pscustomobject]@{
    Flag = ''
    Matched = $false
    SyncId = $(if ($default) { $default.SyncId } else { $null })
    Signature = $(if ($default) { $default.Signature } else { $null })
  }
}

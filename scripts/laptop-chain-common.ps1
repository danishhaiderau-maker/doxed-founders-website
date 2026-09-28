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
    VaultEnv = Join-Path (Split-Path -Parent $CanonicalRoot) 'doxedcryptofounder-secrets\vault\home-bot.env'
    WatcherStatus = Join-Path $StateDir 'laptop-ack-watcher.status.json'
    WatcherLock = Join-Path $StateDir 'laptop-ack-watcher.lock.json'
    AnalyzerStatus = Join-Path $StateDir 'analyzer-run.status.json'
    MonitorState = Join-Path $StateDir 'laptop-chain-monitor.state.json'
    HeartbeatFile = Join-Path $dataRoot '.fly-data-sync-loop.heartbeat.json'
    UpstreamIdentityFile = Join-Path $dataRoot '.fly-upstream-identity.json'
    GenerationLeaseFile = Join-Path $dataRoot '.fly-mirror-generation.lease'
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

function Get-AdminToken([string]$VaultEnv) {
  if ($env:BOT_ADMIN_TOKEN) { return $env:BOT_ADMIN_TOKEN }
  if (-not (Test-Path -LiteralPath $VaultEnv -PathType Leaf)) { throw 'BOT_ADMIN_TOKEN_UNAVAILABLE' }
  $raw = Get-Content -LiteralPath $VaultEnv -Raw
  if ($raw -notmatch '(?m)^BOT_ADMIN_TOKEN=(.+)$') { throw 'BOT_ADMIN_TOKEN_UNAVAILABLE' }
  return $Matches[1].Trim().Trim('"').Trim("'")
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

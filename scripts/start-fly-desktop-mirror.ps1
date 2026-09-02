param(
  [switch]$NoWait,
  [string]$SourceUrl = "https://doxed-btc-bot.fly.dev",
  [switch]$ReloadFailedSyncOwner,
  [int]$ExpectedSyncPid = 0,
  [string]$ExpectedSyncCreationUtc = "",
  [string]$ExpectedHeartbeatSha256 = "",
  [string]$ExpectedPollFailedAt = "",
  [string]$ExpectedClientRevision = "",
  [string]$ExpectedBootstrapReceiptPath = "",
  [string]$ExpectedBootstrapReceiptSha256 = "",
  [string]$ExpectedDeployedRevision = "",
  [string]$ExpectedEpochId = "",
  [string]$ExpectedConfigSignature = "",
  [string]$ExpectedRuntimeTreeSha256 = ""
)

$ErrorActionPreference = "Continue"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = Split-Path -Parent $scriptDir
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if ($pythonCommand) {
  $python = $pythonCommand.Source
} else {
  $pythonCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python313\python.exe")
  )
  $python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
  if (-not $python) {
    throw "Python runtime not found. Install Python or add python.exe to PATH before starting the Fly desktop mirror."
  }
}
. (Join-Path $scriptDir "fly-canonical-lock.ps1")
. (Join-Path $scriptDir "fly-data-paths.ps1")
$SourceUrl = Get-CanonicalFlyBotUrl -RequestedUrl $SourceUrl

function Get-ExactSyncOwnerProcess {
  param([int]$ProcessId, [string]$ExactScriptPath)
  $owners = @(
    Get-CimInstance Win32_Process -ErrorAction Stop |
      Where-Object {
        $_.CommandLine -and
        [string]$_.CommandLine -like "*$ExactScriptPath*" -and
        [string]$_.Name -match '^(powershell|pwsh)\.exe$'
      }
  )
  if ($owners.Count -ne 1 -or [int]$owners[0].ProcessId -ne $ProcessId) {
    throw "Sync reload refused: exact single-owner process proof failed."
  }
  $owner = $owners[0]
  $filePattern = '(?i)(?:^|\s)-File\s+(?:"' +
    [regex]::Escape($ExactScriptPath) + '"|' +
    [regex]::Escape($ExactScriptPath) + ')(?:\s|$)'
  if ([string]$owner.CommandLine -notmatch $filePattern) {
    throw "Sync reload refused: owner command is not bound to the exact script path."
  }
  return $owner
}

function Get-ProcessCreationUtc {
  param($Owner)
  $created = if ($Owner.CreationDate -is [datetime]) {
    [datetime]$Owner.CreationDate
  } else {
    [Management.ManagementDateTimeConverter]::ToDateTime([string]$Owner.CreationDate)
  }
  return ([DateTimeOffset]$created.ToUniversalTime()).ToString('o')
}

function Get-Sha256Hex {
  param([string]$Path)
  $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
  $hasher = [Security.Cryptography.SHA256]::Create()
  try { return ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
  finally { $hasher.Dispose(); $stream.Dispose() }
}

function Get-SyncReloadRuntimeProof {
  param([string]$RepositoryRoot)
  $relativePaths = @(
    'scripts/start-fly-desktop-mirror.ps1',
    'scripts/sync-fly-bot-data-loop.ps1',
    'scripts/sync-fly-bot-data.ps1',
    'scripts/fly-data-paths.ps1',
    'scripts/fly-canonical-lock.ps1',
    'scripts/start-home-analyzer.ps1',
    'scripts/research-stability-supervisor.py',
    'services/btc-conservative-agent/analyzer_research_engine_v62.py',
    'services/btc-conservative-agent/research_v3_store.py'
  )
  $rows = @()
  foreach ($relativePath in $relativePaths) {
    $absolutePath = Join-Path $RepositoryRoot $relativePath
    if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
      throw "Sync reload refused: runtime provenance file is missing: $relativePath"
    }
    $rows += [ordered]@{
      path = $relativePath.Replace('\\', '/')
      sha256 = Get-Sha256Hex -Path $absolutePath
    }
  }
  $lines = @($rows | ForEach-Object { "$($_.path)`t$($_.sha256)" })
  $payload = [Text.Encoding]::UTF8.GetBytes(($lines -join "`n") + "`n")
  $hasher = [Security.Cryptography.SHA256]::Create()
  try { $treeHash = ([BitConverter]::ToString($hasher.ComputeHash($payload))).Replace('-', '').ToLowerInvariant() }
  finally { $hasher.Dispose() }
  return @{ Hash = $treeHash; Files = $rows }
}

function Write-ImmutableBytes {
  param([string]$Path, [byte[]]$Bytes, [string]$ExpectedSha256)
  if (Test-Path -LiteralPath $Path) {
    $existingHash = Get-Sha256Hex -Path $Path
    if ($existingHash -cne $ExpectedSha256) {
      throw "Sync reload refused: immutable receipt conflicts: $Path"
    }
    return
  }
  $temporary = "$Path.tmp-$PID-$([Guid]::NewGuid().ToString('n'))"
  try {
    $options = [IO.FileOptions]::WriteThrough
    $stream = [IO.FileStream]::new(
      $temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write,
      [IO.FileShare]::None, 4096, $options
    )
    try {
      $stream.Write($Bytes, 0, $Bytes.Length)
      $stream.Flush($true)
    } finally { $stream.Dispose() }
    $writtenHash = Get-Sha256Hex -Path $temporary
    if ($writtenHash -cne $ExpectedSha256) {
      throw "Sync reload refused: immutable receipt write hash mismatch."
    }
    try {
      Move-Item -LiteralPath $temporary -Destination $Path -ErrorAction Stop
    } catch {
      # A concurrent identical preservation is idempotent; a different file is
      # an immutable-receipt conflict and must remain terminal.
      if (-not (Test-Path -LiteralPath $Path -PathType Leaf) -or
          (Get-Sha256Hex -Path $Path) -cne $ExpectedSha256) {
        throw
      }
    }
  } finally {
    Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
  }
}

function Read-BootstrapAuthorizationReceipt {
  param(
    [string]$Path, [string]$Sha256, [string]$DeployedRevision,
    [string]$EpochId, [string]$ConfigSignature
  )
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Sync reload refused: authenticated bootstrap authorization receipt is missing."
  }
  $actualHash = Get-Sha256Hex -Path $Path
  if ($actualHash -cne $Sha256.ToLowerInvariant()) {
    throw "Sync reload refused: bootstrap authorization receipt hash changed."
  }
  $receipt = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
  $bootstrap = $receipt.receipt_bootstrap
  if ([string]$receipt.schema -cne 'fly_sync_reload_bootstrap_authorization_v1' -or
      [string]$receipt.source_revision -cne $DeployedRevision.ToLowerInvariant() -or
      [string]$receipt.epoch_id -cne $EpochId -or
      [string]$receipt.config_signature -cne $ConfigSignature -or
      [string]$receipt.inventory_status -cne 'CURRENT' -or
      [string]$bootstrap.status -cne 'COMPLETE' -or
      $bootstrap.complete -ne $true -or $bootstrap.blocked -eq $true -or
      $receipt.paper_only -ne $true -or $receipt.bitfinex_live_enabled -eq $true -or
      $receipt.live_armed -eq $true -or
      $null -eq $receipt.pending_orders -or $null -eq $receipt.open_positions -or
      [int]$receipt.pending_orders -ne 0 -or [int]$receipt.open_positions -ne 0 -or
      [string]::IsNullOrWhiteSpace([string]$receipt.workflow_run_id)) {
    throw "Sync reload refused: bootstrap authorization receipt is not terminal, exact, flat, and paper-only."
  }
  $capturedAt = [DateTimeOffset]::Parse([string]$receipt.captured_at).ToUniversalTime()
  if ($capturedAt -gt [DateTimeOffset]::UtcNow.AddSeconds(5) -or
      $capturedAt -lt [DateTimeOffset]::UtcNow.AddMinutes(-5)) {
    throw "Sync reload refused: bootstrap authorization receipt is not fresh."
  }
  return @{ Receipt = $receipt; Hash = $actualHash; Path = [IO.Path]::GetFullPath($Path) }
}

function Assert-TerminalSyncReloadProof {
  param(
    [string]$LockPath,
    [string]$HeartbeatPath,
    [int]$ProcessId,
    [string]$HeartbeatSha256,
    [string]$PollFailedAt,
    [string]$ExactScriptPath,
    [string]$ExpectedCreationUtc
  )
  if (-not (Test-Path -LiteralPath $LockPath -PathType Leaf) -or
      -not (Test-Path -LiteralPath $HeartbeatPath -PathType Leaf)) {
    throw "Sync reload refused: owner lock or terminal heartbeat is missing."
  }
  $lockedPid = [int](Get-Content -LiteralPath $LockPath -Raw)
  if ($lockedPid -ne $ProcessId) {
    throw "Sync reload refused: owner lock PID changed."
  }
  $actualHash = Get-Sha256Hex -Path $HeartbeatPath
  if ($actualHash -cne $HeartbeatSha256.ToLowerInvariant()) {
    throw "Sync reload refused: terminal heartbeat hash changed."
  }
  $heartbeat = Get-Content -LiteralPath $HeartbeatPath -Raw | ConvertFrom-Json
  if ($heartbeat.ok -eq $true -or $heartbeat.pollOk -eq $true -or
      $heartbeat.inProgress -eq $true -or
      [string]$heartbeat.pollFailedAt -cne $PollFailedAt -or
      [string]$heartbeat.pollStage -notin @('loop_full_manifest', 'loop_manifest_preflight') -or
      [string]::IsNullOrWhiteSpace([string]$heartbeat.pollError)) {
    throw "Sync reload refused: heartbeat is not the exact terminal failure."
  }
  $nextRetry = [DateTimeOffset]::Parse([string]$heartbeat.nextRetryAt).ToUniversalTime()
  if ($nextRetry -le [DateTimeOffset]::UtcNow.AddSeconds(5)) {
    throw "Sync reload refused: owner lacks the minimum five-second terminal backoff fence."
  }
  $owner = Get-ExactSyncOwnerProcess -ProcessId $ProcessId -ExactScriptPath $ExactScriptPath
  $creationUtc = Get-ProcessCreationUtc -Owner $owner
  $expectedCreation = [DateTimeOffset]::Parse($ExpectedCreationUtc).ToUniversalTime()
  if ([DateTimeOffset]::Parse($creationUtc).ToUniversalTime() -ne $expectedCreation) {
    throw "Sync reload refused: owner creation identity changed."
  }
  $started = [DateTimeOffset]::Parse($creationUtc).UtcDateTime
  $failed = [DateTimeOffset]::Parse($PollFailedAt).UtcDateTime
  if ($started -ge $failed) {
    throw "Sync reload refused: process identity post-dates the failure proof."
  }
  $children = @(
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$ProcessId" -ErrorAction Stop |
      Where-Object { [string]$_.Name -notmatch '^conhost\.exe$' }
  )
  if ($children.Count -ne 0) {
    throw "Sync reload refused: sync owner still has active child work."
  }
  return @{ Owner = $owner; Heartbeat = $heartbeat; Hash = $actualHash; CreationUtc = $creationUtc }
}

if ($ReloadFailedSyncOwner) {
  if ($ExpectedSyncPid -le 0 -or
      [string]::IsNullOrWhiteSpace($ExpectedSyncCreationUtc) -or
      $ExpectedHeartbeatSha256 -notmatch '^[0-9a-fA-F]{64}$' -or
      [string]::IsNullOrWhiteSpace($ExpectedPollFailedAt) -or
      $ExpectedClientRevision -notmatch '^[0-9a-fA-F]{40}$' -or
      $ExpectedBootstrapReceiptSha256 -notmatch '^[0-9a-fA-F]{64}$' -or
      $ExpectedDeployedRevision -notmatch '^[0-9a-fA-F]{40}$' -or
      [string]::IsNullOrWhiteSpace($ExpectedEpochId) -or
      [string]::IsNullOrWhiteSpace($ExpectedConfigSignature) -or
      $ExpectedRuntimeTreeSha256 -notmatch '^[0-9a-fA-F]{64}$') {
    throw "Sync reload refused: exact process, heartbeat, bootstrap, deployment, and runtime provenance are required."
  }
  $actualRevision = [string](& git -C $repoRoot rev-parse HEAD 2>$null)
  if ($LASTEXITCODE -ne 0 -or $actualRevision -cne $ExpectedClientRevision.ToLowerInvariant()) {
    throw "Sync reload refused: checkout revision does not match the authorized client revision."
  }
  if ($actualRevision -cne $ExpectedDeployedRevision.ToLowerInvariant()) {
    throw "Sync reload refused: local client/analyzer HEAD does not equal the deployed revision."
  }
  & git -C $repoRoot diff --quiet -- 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw "Sync reload refused: tracked working tree is dirty; analyzer provenance would be false."
  }
  & git -C $repoRoot diff --cached --quiet -- 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw "Sync reload refused: staged working tree is dirty; analyzer provenance would be false."
  }
  $runtimeProof = Get-SyncReloadRuntimeProof -RepositoryRoot $repoRoot
  if ($runtimeProof.Hash -cne $ExpectedRuntimeTreeSha256.ToLowerInvariant()) {
    throw "Sync reload refused: sync/analyzer runtime-tree provenance changed."
  }
  $bootstrapProof = Read-BootstrapAuthorizationReceipt `
    -Path $ExpectedBootstrapReceiptPath -Sha256 $ExpectedBootstrapReceiptSha256 `
    -DeployedRevision $ExpectedDeployedRevision -EpochId $ExpectedEpochId `
    -ConfigSignature $ExpectedConfigSignature
  $syncScript = [IO.Path]::GetFullPath((Join-Path $scriptDir 'sync-fly-bot-data-loop.ps1'))
  $syncLock = Join-Path $repoRoot '.fly-data-sync-loop.lock'
  $canonicalMirror = Get-DoxxedFlyMirrorDir
  $syncHeartbeat = Join-Path $canonicalMirror '.fly-data-sync-loop.heartbeat.json'
  $proof = Assert-TerminalSyncReloadProof `
    -LockPath $syncLock -HeartbeatPath $syncHeartbeat `
    -ProcessId $ExpectedSyncPid -HeartbeatSha256 $ExpectedHeartbeatSha256 `
    -PollFailedAt $ExpectedPollFailedAt -ExactScriptPath $syncScript `
    -ExpectedCreationUtc $ExpectedSyncCreationUtc

  $receiptDir = Join-Path $canonicalMirror 'logs\sync-owner-reloads'
  New-Item -ItemType Directory -Path $receiptDir -Force -ErrorAction Stop | Out-Null
  $bootstrapReceiptPath = Join-Path $receiptDir ("bootstrap-$($bootstrapProof.Hash).json")
  $bootstrapBytes = [IO.File]::ReadAllBytes($bootstrapProof.Path)
  Write-ImmutableBytes -Path $bootstrapReceiptPath -Bytes $bootstrapBytes -ExpectedSha256 $bootstrapProof.Hash
  $receiptPath = Join-Path $receiptDir ("terminal-$($proof.Hash).json")
  $terminalBytes = [IO.File]::ReadAllBytes($syncHeartbeat)
  Write-ImmutableBytes -Path $receiptPath -Bytes $terminalBytes -ExpectedSha256 $proof.Hash

  # Repeat every mutable proof immediately before the one authorized stop.
  [void](Assert-TerminalSyncReloadProof `
    -LockPath $syncLock -HeartbeatPath $syncHeartbeat `
    -ProcessId $ExpectedSyncPid -HeartbeatSha256 $ExpectedHeartbeatSha256 `
    -PollFailedAt $ExpectedPollFailedAt -ExactScriptPath $syncScript `
    -ExpectedCreationUtc $ExpectedSyncCreationUtc)
  $runtimeProofAtStop = Get-SyncReloadRuntimeProof -RepositoryRoot $repoRoot
  if ($runtimeProofAtStop.Hash -cne $runtimeProof.Hash) {
    throw "Sync reload refused: sync/analyzer runtime tree changed before takeover."
  }
  & git -C $repoRoot diff --quiet -- 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw "Sync reload refused: tracked working tree changed before takeover."
  }
  & git -C $repoRoot diff --cached --quiet -- 2>$null
  if ($LASTEXITCODE -ne 0) {
    throw "Sync reload refused: staged working tree changed before takeover."
  }
  $bootstrapProofAtStop = Read-BootstrapAuthorizationReceipt `
    -Path $ExpectedBootstrapReceiptPath -Sha256 $ExpectedBootstrapReceiptSha256 `
    -DeployedRevision $ExpectedDeployedRevision -EpochId $ExpectedEpochId `
    -ConfigSignature $ExpectedConfigSignature
  if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf) -or
      (Get-Sha256Hex -Path $receiptPath) -cne $proof.Hash) {
    throw "Sync reload refused: terminal heartbeat receipt was not durably retained."
  }
  if (-not (Test-Path -LiteralPath $bootstrapReceiptPath -PathType Leaf) -or
      (Get-Sha256Hex -Path $bootstrapReceiptPath) -cne $bootstrapProofAtStop.Hash) {
    throw "Sync reload refused: bootstrap authorization receipt was not durably retained."
  }
  # Bind the destructive action to an opened process object whose start time
  # matches the repeatedly proven CIM creation identity. This closes the PID
  # reuse window left by Stop-Process -Id.
  $stopTarget = Get-Process -Id $ExpectedSyncPid -ErrorAction Stop
  $stopCreationUtc = ([DateTimeOffset]$stopTarget.StartTime.ToUniversalTime()).ToString('o')
  if ([DateTimeOffset]::Parse($stopCreationUtc).ToUniversalTime() -ne
      [DateTimeOffset]::Parse($proof.CreationUtc).ToUniversalTime()) {
    throw "Sync reload refused: opened stop target is not the proven owner instance."
  }
  $stopTarget.Kill()
  $stopDeadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
  while ((Get-Process -Id $ExpectedSyncPid -ErrorAction SilentlyContinue) -and
      [DateTimeOffset]::UtcNow -lt $stopDeadline) {
    Start-Sleep -Milliseconds 100
  }
  if (Get-Process -Id $ExpectedSyncPid -ErrorAction SilentlyContinue) {
    throw "Sync reload refused: prior owner did not terminate."
  }
  if ([int](Get-Content -LiteralPath $syncLock -Raw) -ne $ExpectedSyncPid) {
    throw "Sync reload refused: owner lock changed after termination."
  }
  Remove-Item -LiteralPath $syncLock,$syncHeartbeat -Force -ErrorAction Stop
  $spawned = Start-Process -FilePath 'powershell.exe' `
    -ArgumentList (
      "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$syncScript`" " +
      "-SourceUrl `"$SourceUrl`""
    ) -WorkingDirectory $repoRoot -WindowStyle Hidden -PassThru
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
  do {
    Start-Sleep -Milliseconds 250
    $newPid = 0
    try { $newPid = [int](Get-Content -LiteralPath $syncLock -Raw -ErrorAction Stop) } catch { }
  } while (($newPid -le 0 -or $newPid -eq $ExpectedSyncPid) -and [DateTimeOffset]::UtcNow -lt $deadline)
  if ($newPid -le 0 -or $newPid -eq $ExpectedSyncPid -or
      -not $spawned -or $newPid -ne [int]$spawned.Id) {
    throw "Sync reload failed: replacement owner did not claim the repository lock."
  }
  [void](Get-ExactSyncOwnerProcess -ProcessId $newPid -ExactScriptPath $syncScript)
  $newOwner = Get-ExactSyncOwnerProcess -ProcessId $newPid -ExactScriptPath $syncScript
  $newCreationUtc = Get-ProcessCreationUtc -Owner $newOwner
  $reloadReceipt = Join-Path $receiptDir ("reload-$($proof.Hash).json")
  $reloadPayload = [ordered]@{
    schema = 'fly_desktop_sync_owner_reload_v1'
    terminal_heartbeat_sha256 = $proof.Hash
    terminal_heartbeat_receipt = $receiptPath
    poll_failed_at = $ExpectedPollFailedAt
    old_process_creation_utc = $proof.CreationUtc
    new_process_creation_utc = $newCreationUtc
    client_revision = $ExpectedClientRevision.ToLowerInvariant()
    deployed_revision = $ExpectedDeployedRevision.ToLowerInvariant()
    epoch_id = $ExpectedEpochId
    config_signature = $ExpectedConfigSignature
    source_url = $SourceUrl
    bootstrap_receipt = $bootstrapReceiptPath
    bootstrap_receipt_sha256 = $bootstrapProof.Hash
    bootstrap_workflow_run_id = [string]$bootstrapProof.Receipt.workflow_run_id
    runtime_tree_sha256 = $runtimeProof.Hash
    runtime_files = $runtimeProof.Files
    old_pid = $ExpectedSyncPid
    new_pid = $newPid
    reloaded_at = [DateTimeOffset]::UtcNow.ToString('o')
  }
  $reloadBytes = [Text.Encoding]::UTF8.GetBytes(($reloadPayload | ConvertTo-Json -Depth 6 -Compress) + "`n")
  $reloadHasher = [Security.Cryptography.SHA256]::Create()
  try { $reloadHash = ([BitConverter]::ToString($reloadHasher.ComputeHash($reloadBytes))).Replace('-', '').ToLowerInvariant() }
  finally { $reloadHasher.Dispose() }
  Write-ImmutableBytes -Path $reloadReceipt -Bytes $reloadBytes -ExpectedSha256 $reloadHash
  Write-Output "Sync owner reloaded: old=$ExpectedSyncPid new=$newPid receipt=$reloadReceipt"
  return
}

# Stop only the former desktop production runtime and its relay publisher.
# The analyzer remains independent and is restarted below against Fly data.
foreach ($name in @(
  ".home-bot.pid",
  ".home-bot-crash-monitor.pid",
  ".home-bot-starter.pid",
  ".home-relay-pusher.pid"
)) {
  $path = Join-Path $repoRoot $name
  if (Test-Path -LiteralPath $path) {
    try {
      $procId = [int](Get-Content -LiteralPath $path -Raw)
      if ($procId -gt 0) {
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
      }
    } catch { }
  }
}

# Start the local :7002 compatibility proxy. It has no AI or strategy code.
$proxyPidFile = Join-Path $repoRoot ".fly-dashboard-proxy.pid"
$proxyAlive = $false
$proxyEndpointAlive = $false
try {
  $proxyProbe = Invoke-WebRequest `
    -UseBasicParsing `
    -Uri "http://127.0.0.1:7002/health" `
    -TimeoutSec 8
  $proxyEndpointAlive = (
    [string]$proxyProbe.Headers["X-Desktop-Mirror"] -eq "fly"
  )
} catch { }
$proxyListenerPids = @(
  Get-NetTCPConnection `
    -LocalAddress "127.0.0.1" `
    -LocalPort 7002 `
    -State Listen `
    -ErrorAction SilentlyContinue |
      Select-Object -ExpandProperty OwningProcess -Unique
)
if (Test-Path -LiteralPath $proxyPidFile) {
  try {
    $proxyPid = [int](Get-Content -LiteralPath $proxyPidFile -Raw)
    $proxyAlive = [bool](
      (Get-Process -Id $proxyPid -ErrorAction SilentlyContinue) -and
      ($proxyEndpointAlive -or ($proxyPid -in $proxyListenerPids))
    )
  } catch { }
}
# A recovery can be launched from a clean integration worktree while an older
# read-only mirror (from the normal desktop checkout) is already healthy. Adopt
# that sole listener instead of relying only on a worktree-local PID marker and
# accidentally creating a second SO_REUSEADDR listener on Windows.
if (-not $proxyAlive -and $proxyEndpointAlive) {
  $proxyAlive = $true
  if ($proxyListenerPids.Count -eq 1) {
    $proxyPid = [int]$proxyListenerPids[0]
    Set-Content -LiteralPath $proxyPidFile -Value "$proxyPid" -NoNewline -Encoding UTF8
  }
}
if (-not $proxyAlive -and $proxyListenerPids.Count -gt 0) {
  throw (
    "Desktop mirror port 127.0.0.1:7002 already has $($proxyListenerPids.Count) " +
    "unowned listener(s). Use the authenticated Reset desktop tools control; " +
    "recovery will not start another proxy or terminate an unverified process."
  )
}
if (-not $proxyAlive) {
  $proxyScript = Join-Path $scriptDir "fly-dashboard-proxy.py"
  $proxyArguments = "`"$proxyScript`" --bind 127.0.0.1 --port 7002 --upstream `"$SourceUrl`""
  $proxy = Start-Process -FilePath $python `
    -ArgumentList $proxyArguments `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -PassThru
  Set-Content -LiteralPath $proxyPidFile -Value "$($proxy.Id)" -NoNewline -Encoding UTF8
}

# Start one incremental Fly data synchronizer.
$syncLock = Join-Path $repoRoot ".fly-data-sync-loop.lock"
$canonicalMirror = Get-DoxxedFlyMirrorDir
$syncHeartbeat = Join-Path $canonicalMirror ".fly-data-sync-loop.heartbeat.json"
$syncHeartbeatMaxAgeSec = 600
$syncBackoffGraceSec = 300
$syncAlive = $false
if (Test-Path -LiteralPath $syncLock) {
  try {
    $syncPid = [int](Get-Content -LiteralPath $syncLock -Raw)
    $syncProcess = Get-Process -Id $syncPid -ErrorAction SilentlyContinue
    if ($syncProcess -and $syncProcess.ProcessName -match "^(powershell|pwsh)$") {
      $syncAgeSec = ((Get-Date) - $syncProcess.StartTime).TotalSeconds
      $heartbeatAgeSec = if (Test-Path -LiteralPath $syncHeartbeat) {
        ((Get-Date).ToUniversalTime() - (Get-Item -LiteralPath $syncHeartbeat).LastWriteTimeUtc).TotalSeconds
      } else {
        $syncAgeSec
      }
      $insideDeclaredBackoff = $false
      if (Test-Path -LiteralPath $syncHeartbeat -PathType Leaf) {
        try {
          $heartbeatState = Get-Content -LiteralPath $syncHeartbeat -Raw | ConvertFrom-Json
          $nextRetryRaw = [string]($heartbeatState.nextRetryAt)
          if ($nextRetryRaw) {
            $nextRetryAt = [DateTimeOffset]::Parse($nextRetryRaw).ToUniversalTime()
            $insideDeclaredBackoff = (
              [DateTimeOffset]::UtcNow -le $nextRetryAt.AddSeconds($syncBackoffGraceSec)
            )
          }
        } catch { }
      }
      $syncAlive = (
        $heartbeatAgeSec -le $syncHeartbeatMaxAgeSec -or $insideDeclaredBackoff
      )
      if (-not $syncAlive) {
        Stop-Process -Id $syncPid -Force -ErrorAction SilentlyContinue
      }
    }
  } catch { }
}
if (-not $syncAlive) {
  Remove-Item -LiteralPath $syncLock,$syncHeartbeat -Force -ErrorAction SilentlyContinue
  Start-Process -FilePath "powershell.exe" `
    -ArgumentList (
      "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"" +
      (Join-Path $scriptDir "sync-fly-bot-data-loop.ps1") +
      "`" -SourceUrl `"$SourceUrl`""
    ) `
    -WindowStyle Hidden | Out-Null
}

# The existing analyzer launcher detects fly-canonical.lock.json and reads the
# synchronized mirror instead of files written by a second local bot.
& (Join-Path $scriptDir "start-home-analyzer.ps1") -Port 9001 -NoWait

if (-not $NoWait) {
  Write-Host "Fly is the sole AI/trading owner." -ForegroundColor Green
  Write-Host "Desktop :7002 proxies Fly; desktop :9001 analyzes the Fly data mirror."
}

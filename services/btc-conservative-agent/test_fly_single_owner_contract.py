from __future__ import annotations

import base64
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
DIAGNOSTICS = ROOT / "diagnostics"


def text(name: str) -> str:
    return (SCRIPTS / name).read_text(encoding="utf-8")


lock = json.loads((ROOT / "config" / "fly-canonical.lock.json").read_text())
architecture = json.loads(
    (ROOT / "config" / "bot-architecture.lock.json").read_text()
)
desktop_mirror = json.loads(
    (ROOT / "config" / "home-showcase.lock.json").read_text()
)
local_collection = json.loads(
    (ROOT / "config" / "local-collection.lock.json").read_text()
)

assert lock["frozen"] is True
assert lock["desktopBotEnabled"] is False
assert lock["sourceUrl"] == "https://doxed-btc-bot.fly.dev"
assert architecture["runtimeRoles"]["fly"]["aiOwner"] is True
assert architecture["runtimeRoles"]["desktop"]["aiOwner"] is False
assert architecture["canonicalSource"]["processEntrypoint"] == "btc_conservative_agent.py"
assert architecture["canonicalSource"]["strategyModule"] == "bot.py"
assert desktop_mirror["mode"] == "fly-mirror"
assert desktop_mirror["authoritative"] is False
assert desktop_mirror["disableLocalStrategy"] is True
assert desktop_mirror["disableTunnel"] is True
assert local_collection["enabled"] is False
assert local_collection["disableLocalStrategy"] is True

for guarded in (
    "start-home-bot.ps1",
    "bot-auto-restart.ps1",
    "home-stack-supervisor.ps1",
    "home-stack-supervisor-watchdog.ps1",
    "relay-state-pusher.ps1",
    "stack-monitor.ps1",
    "home-stack-start-everything.ps1",
):
    assert "fly-canonical.lock.json" in text(guarded), guarded

assert "start-fly-desktop-mirror.ps1" in text("start-showcase-bot.cmd")
assert "fly-dashboard-proxy.py" in text("start-fly-desktop-mirror.ps1")
assert "sync-fly-bot-data-loop.ps1" in text("start-fly-desktop-mirror.ps1")
assert "$env:BTC_AGENT_DATA_DIR = $analyzerDataDir" in text(
    "start-home-analyzer.ps1"
)
for analyzer_launcher in ("start-home-analyzer.ps1",):
    analyzer_text = text(analyzer_launcher)
    assert '$env:RESEARCH_DASHBOARD_BIND_HOST = "127.0.0.1"' in analyzer_text
    assert '"BITFINEX_API_KEY"' in analyzer_text
    assert "Remove-Item -LiteralPath" in analyzer_text
    assert "allowedAnalyzerVars" in analyzer_text
    assert 'Set-Item -Path ("env:" + $matches[1].Trim())' not in analyzer_text
sync_loop = text("sync-fly-bot-data-loop.ps1")
assert "Get-CanonicalFlyBotUrl -RequestedUrl $SourceUrl" in sync_loop
assert "$env:BOT_ADMIN_TOKEN" in sync_loop
assert 'Set-Item -Path ("env:" + $matches[1].Trim())' not in sync_loop
assert "FLY_VOLUME_SYNC_THRESHOLD_MB" in sync_loop
assert "/api/data-sync/identity" in sync_loop
assert "$FullSyncIntervalSec = 1800" in sync_loop
assert "identity match; full inventory not due" in sync_loop
assert "size -le 50MB" not in sync_loop
assert "Incremental chunk sync already" in sync_loop

# A pinned boundary resume owns a separate, whole-session guard. The ordinary
# loop must defer before both local candidate mutation and its first remote
# preflight; the defer receipt makes no current/parity/ACK claim.
assert ".fly-pinned-generation-resume.guard" in sync_loop
assert "Enter-PinnedGenerationResumeGuard" in sync_loop
defer_at = sync_loop.rindex(
    "Enter-PinnedGenerationResumeGuard -GuardPath $pinnedResumeGuardFile"
)
assert defer_at < sync_loop.index("Remove-OrphanedMirrorCandidates -MirrorPath $mirrorDir")
assert defer_at < sync_loop.index('$currentStage = "loop_manifest_preflight"')
assert "PINNED_GENERATION_RESUME_DEFERRED" in sync_loop
assert "pollOk = $null" in sync_loop
assert "revisionParity = 'UNKNOWN'" in sync_loop
release_at = sync_loop.index(
    "if ($pinnedResumeGuard) { $pinnedResumeGuard.Dispose(); $pinnedResumeGuard = $null }",
    defer_at,
)
assert release_at < sync_loop.rindex("Start-Sleep -Seconds $sleepSec")
defer_function_start = sync_loop.index("function Write-PinnedGenerationResumeDeferredHeartbeat")
defer_function = sync_loop[
    defer_function_start : sync_loop.index(
        "function Publish-AnalyzerLeaseDeferredReceipt", defer_function_start
    )
]
assert "Invoke-RestMethod" not in defer_function

launcher = (DIAGNOSTICS / "start-singleton-generation-resume.ps1").read_text(
    encoding="utf-8"
)
assert ".score-led-boundary\\scripts\\fly-sync-generation-resume.ps1" in launcher
assert "BOUNDARY_RESUME_HELPER_REQUIRED" in launcher
assert "Join-Path $repo 'scripts\\fly-sync-generation-resume.ps1'" not in launcher
assert ".fly-pinned-generation-resume.guard" in launcher
assert "fly_generation_resume_session_result_v1" in launcher
assert "Write-FlyGenerationResumeSessionJsonCreateOnly" in launcher
assert "[IO.File]::Move" in launcher
assert "[IO.FileMode]::CreateNew" in launcher
assert "yyyyMMdd-HHmmss-fff" in launcher
assert "[Guid]::NewGuid().ToString('N')" in launcher
assert "SESSION_RECEIPT_COLLISION" in launcher
assert "SESSION_START_COLLISION" in launcher
assert "fly_generation_resume_session_start_v1" in launcher
assert "session_name = $sessionName" in launcher
assert "session_nonce = $sessionNonce" in launcher
assert "receiptDir=$receiptDir" not in launcher
guard_wait = launcher[launcher.index("$sessionStage = 'PINNED_GUARD_WAIT'") : launcher.index("$sessionStage = 'IDENTITY'")]
assert "$pinnedGuardWaitMaxSec = 7200" in launcher
assert "while (-not $pinnedResumeGuard" in guard_wait
assert "Start-Sleep -Seconds $pinnedGuardRetrySec" in guard_wait
assert "Invoke-RestMethod" not in guard_wait
assert launcher.index("$sessionStage = 'PINNED_GUARD_WAIT'") < launcher.index("Import-CanonicalBotAdminToken")
# Only the five reviewed authority fields cross into the session-start receipt.
identity_projection = launcher[
    launcher.index("$identity = [pscustomobject][ordered]@{") : launcher.index("# The exclusive guard serializes active launchers")
]
for field in (
    "inventory_generation_id",
    "inventory_sha256",
    "source_git_rev",
    "collection_epoch_id",
    "tile_registry_signature",
):
    assert field in identity_projection
assert "written_at_utc" not in identity_projection
assert "file_count" not in identity_projection
assert "total_bytes" not in identity_projection
assert "note" not in identity_projection
assert "identity = $identityCandidate" not in launcher
# The guard is authoritative for a resident normal loop. Only an actual
# transfer helper (not sync-fly-bot-data-loop.ps1) can trigger a second-owner
# refusal after the guard has been acquired.
singleton_start = launcher.index("$sessionStage = 'SINGLETON_CHECK'")
singleton = launcher[singleton_start : launcher.index("$session =", singleton_start)]
assert "$_ .ProcessId" not in singleton  # guard against a typo with a space
assert "$_.ProcessId -ne $PID" in singleton
assert "sync-fly-bot-data-loop.ps1" not in singleton
assert "start-singleton-generation-resume\\.ps1" in singleton
assert "Start-FlyGenerationResume" in singleton
for direct_owner in (
    "sync-fly-bot-data\\.ps1",
    "oneshot_detached_loop\\.ps1",
    "oneshot_sync_wrapper\\.ps1",
    "oneshot_sync_task\\.ps1",
    "oneshot_manual_boot\\.ps1",
):
    assert direct_owner in singleton
assert "sync-fly-bot-data-loop\\.ps1" not in singleton
# A fixed exact-identity success fence serializes multiple waiting launchers
# without treating failed or malformed historical receipts as success.
assert "fly_generation_resume_success_fence_v1" in launcher
assert "PINNED_GENERATION_ALREADY_COMPLETE" in launcher
assert "PINNED_SUCCESS_FENCE_INVALID" in launcher
assert "session_result_sha256" in launcher
assert "session_start_sha256" in launcher
assert "Get-FlyGenerationResumeSessionSha256 -LiteralPath $priorStartPath" in launcher
assert "Get-FlyGenerationResumeSessionSha256 -LiteralPath $priorResultPath" in launcher
assert "Get-FlyGenerationResumeSessionProgressBinding" in launcher
assert "Get-FlyGenerationResumeSessionMembershipBinding" in launcher
assert "Assert-FlyGenerationResumeSessionPointerBinding" in launcher
assert "terminal_membership_content_digest_sha256" in launcher
assert "terminal_membership_manifest_page_digest_sha256" in launcher
assert "[string]$pointer.source_revision -cne [string]$Identity.source_git_rev" in launcher
success_fence = launcher[
    launcher.index("$sessionStage = 'SUCCESS_FENCE'") : launcher.index("$sessionStage = 'PINNED_AUTHORITY'")
]
for required_reopen in (
    "Get-FlyGenerationResumeSessionSha256 -LiteralPath $priorStartPath",
    "Get-FlyGenerationResumeSessionSha256 -LiteralPath $priorResultPath",
    "Get-FlyGenerationResumeSessionProgressBinding",
    "Get-FlyGenerationResumeSessionMembershipBinding",
    "Assert-FlyGenerationResumeSessionPointerBinding",
):
    assert required_reopen in success_fence
assert "TERMINAL_IDENTITY_BINDING_INVALID" in launcher
assert "TERMINAL_ACK_BINDING_INVALID" in launcher
assert "Assert-FlyGenerationResumeSessionTerminalAck" in launcher
assert "terminal_progress_receipt_name" in launcher
assert "terminal_progress_receipt_sha256" in launcher
assert "canonical_pointer_sha256" in launcher
assert "terminal_membership_receipt_name" in launcher
assert "terminal_membership_receipt_sha256" in launcher
assert "terminal_membership_receipt_schema" in launcher
assert "terminal_membership_receipt_content_hash_status" in launcher
assert "terminal_progress_receipt_path" not in launcher.lower()
for strict_progress_field in ("$progress.fileIndex", "$progress.fileCount", "$progress.fileBytes", "$progress.remoteBytes"):
    assert strict_progress_field in launcher
for strict_membership_field in (
    "manifest_file_count",
    "manifest_total_bytes",
    "local_content_coverage_complete",
    "local_full_file_sha256",
    "sorted_file_digest_sha256",
    "relative_path",
    "size_bytes",
):
    assert strict_membership_field in launcher
assert "UTF8_PATH_BYTE_LENGTH_RELATIVE_PATH_SIZE_BYTES_SHA256_UTF8_LF_V1" in launcher
assert "[Text.Encoding]::UTF8.GetByteCount($relative) -gt 1024" in launcher
assert "\\x7F" in launcher

watcher = (DIAGNOSTICS / "stay-with-364636c8.ps1").read_text(encoding="utf-8")
assert "Read-TerminalSessionResult" in watcher
assert "SESSION_RESULT_FAILED_TERMINAL" in watcher
assert "Resolve-ActiveGenerationResumeSession" in watcher
assert "SESSION_BOUND name=" in watcher
assert "verified-generation-transfer-20260912-150024" not in watcher
assert "-Method POST" not in watcher
assert "Start-Process" not in watcher
assert "Get-CimInstance" not in watcher
assert "XFER_DEAD_RELAUNCH" not in watcher
assert "$_.Exception.Message" not in watcher
assert "OBSERVATION_ERROR code=" in watcher
assert "Get-ObserverErrorCode" in watcher
assert watcher.index("$terminalResult=Read-TerminalSessionResult") < watcher.index(
    "Invoke-RestMethod -Uri \"$fly/api/status\""
)
assert "ack_accepted" in watcher
assert "ack_expected_count" in watcher
assert "terminal_membership_receipt_name" in watcher
assert "canonical_pointer_sha256" in watcher
assert "SESSION_RESULT_INVALID" in watcher
assert "Test-ObserverExactIdentity" in watcher
assert "Test-ObserverExactProgressIdentity" in watcher
assert "ExpectedSessionName" in watcher
assert "ExpectedSessionNonce" in watcher
assert "ExpectedIdentity" in watcher
assert "PROGRESS_IDENTITY_INVALID" in watcher
assert "Get-ObserverMembershipPageDigest" in watcher
assert "terminal_membership_content_digest_sha256" in watcher
assert "terminal_membership_manifest_page_digest_sha256" in watcher
assert "[string]$pointer.source_revision -cne [string]$result.source_git_rev" in watcher
assert "[Text.Encoding]::UTF8.GetByteCount($relative) -gt 1024" in watcher
assert "\\x7F" in watcher
for strict_membership_field in (
    "manifest_file_count",
    "manifest_total_bytes",
    "local_content_coverage_complete",
    "local_full_file_sha256",
    "sorted_file_digest_sha256",
):
    assert strict_membership_field in watcher

# The touched loop emits only typed local failures and redacted path summaries.
assert "Get-FlySyncSafeErrorCode" in sync_loop
assert "$failureMessage = $_.Exception.Message" not in sync_loop
assert "$report.fly_error = $_.Exception.Message" not in sync_loop
assert "$report.fly_runtime_path" not in sync_loop
assert "$quarantineResult.Destination" not in sync_loop
assert "destination=REDACTED" in sync_loop


def run_membership_contract_synthetic(
    script_path: Path,
    count_name: str,
    digest_name: str,
    page_digest_name: str,
    failure_code: str,
) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return
    path_literal = str(script_path).replace("'", "''")
    command = rf"""
$ErrorActionPreference='Stop'
trap {{ Write-Output ('SYNTHETIC_ERROR='+$_.Exception.Message); exit 1 }}
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('{path_literal}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'PARSE_FAILED'}}
foreach($name in @('{count_name}','{digest_name}','{page_digest_name}')){{
  $definition=$ast.FindAll({{param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name}},$true) | Select-Object -First 1
  if(-not $definition){{throw 'FUNCTION_MISSING'}}
  Invoke-Expression $definition.Extent.Text
}}
$a='a'*64; $b='b'*64; $c='c'*64
$payload="5:a.txt:3:$a`n12:nested/b.bin:4:$b`n"
$hasher=[Security.Cryptography.SHA256]::Create()
try{{$digest=-join ($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($payload))|ForEach-Object{{$_.ToString('x2')}})}}finally{{$hasher.Dispose()}}
$membership=[pscustomobject]@{{
  manifest_file_count=2; manifest_total_bytes=7
  manifest_pages=[pscustomobject]@{{
    descriptor_schema='fly_manifest_page_descriptor_v1'
    canonicalization='PAGE_INDEX_PAGE_SHA256_FILE_COUNT_TOTAL_BYTES_UTF8_LF_V1'
    sorted_page_digest_sha256=''
    descriptors=@([pscustomobject]@{{page_index=0;page_sha256=$c;file_count=2;total_bytes=7}})
  }}
  content_coverage=[pscustomobject]@{{
    local_content_coverage_complete=$true
    local_full_file_sha256=[pscustomobject]@{{
      status='COMPLETE_FRESH_RECOMPUTED'
      canonicalization='UTF8_PATH_BYTE_LENGTH_RELATIVE_PATH_SIZE_BYTES_SHA256_UTF8_LF_V1'
      file_count=2; total_bytes=7; sorted_file_digest_sha256=$digest
      files=@(
        [pscustomobject]@{{relative_path='nested/b.bin';size_bytes=4;sha256=$b}},
        [pscustomobject]@{{relative_path='a.txt';size_bytes=3;sha256=$a}}
      )
    }}
  }}
}}
$pagePayload="0:$c`:2`:7`n"
$hasher=[Security.Cryptography.SHA256]::Create()
try{{$pageDigest=-join ($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($pagePayload))|ForEach-Object{{$_.ToString('x2')}})}}finally{{$hasher.Dispose()}}
$membership.manifest_pages.sorted_page_digest_sha256=$pageDigest
$actual=& '{digest_name}' -Membership $membership -ExpectedCount 2
if($actual -cne $digest){{throw 'VALID_SYNTHETIC_REJECTED'}}
$actualPage=& '{page_digest_name}' -Membership $membership -ExpectedPageCount 1 -ExpectedFileCount 2 -ExpectedTotalBytes 7
if($actualPage -cne $pageDigest){{throw 'VALID_PAGE_SYNTHETIC_REJECTED'}}
$membership.content_coverage.local_content_coverage_complete=$false
try{{& '{digest_name}' -Membership $membership -ExpectedCount 2; throw 'INVALID_SYNTHETIC_ACCEPTED'}}catch{{
  if($_.Exception.Message -cne '{failure_code}'){{throw}}
}}
$membership.content_coverage.local_content_coverage_complete=$true
foreach($unsafe in @(('é'*513),"bad$([char]0x7f)path")){{
  $membership.content_coverage.local_full_file_sha256.files[0].relative_path=$unsafe
  $unsafePayload="$([Text.Encoding]::UTF8.GetByteCount($unsafe))`:$unsafe`:3`:$a`n12`:nested/b.bin`:4`:$b`n"
  $hasher=[Security.Cryptography.SHA256]::Create()
  try{{$unsafeDigest=-join ($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($unsafePayload))|ForEach-Object{{$_.ToString('x2')}})}}finally{{$hasher.Dispose()}}
  $membership.content_coverage.local_full_file_sha256.sorted_file_digest_sha256=$unsafeDigest
  try{{& '{digest_name}' -Membership $membership -ExpectedCount 2; throw 'UNSAFE_MEMBER_ACCEPTED'}}catch{{
    if($_.Exception.Message -cne '{failure_code}'){{throw}}
  }}
}}
Write-Output 'SYNTHETIC_OK'
exit 0
"""
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


run_membership_contract_synthetic(
    DIAGNOSTICS / "start-singleton-generation-resume.ps1",
    "Test-FlyGenerationResumeSessionCount",
    "Get-FlyGenerationResumeSessionMembershipDigest",
    "Get-FlyGenerationResumeSessionMembershipPageDigest",
    "TERMINAL_MEMBERSHIP_RECEIPT_BINDING_INVALID",
)


def run_pointer_membership_binding_synthetic() -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return
    script_path = DIAGNOSTICS / "start-singleton-generation-resume.ps1"
    path_literal = str(script_path).replace("'", "''")
    with tempfile.TemporaryDirectory(prefix="fly-pointer-binding-") as temporary:
        root_literal = str(Path(temporary) / "mirror").replace("'", "''")
        command = rf"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('{path_literal}',[ref]$tokens,[ref]$errors)
if($errors.Count){{throw 'PARSE_FAILED'}}
foreach($name in @(
  'Test-FlyGenerationResumeSessionRevision','Test-FlyGenerationResumeSessionCount',
  'Get-FlyGenerationResumeSessionMembershipDigest','Get-FlyGenerationResumeSessionMembershipPageDigest',
  'Get-FlyGenerationResumeSessionSha256','Get-FlyGenerationResumeSessionMembershipBinding',
  'Assert-FlyGenerationResumeSessionPointerBinding'
)){{
  $definition=$ast.FindAll({{param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $name}},$true)|Select-Object -First 1
  if(-not $definition){{throw 'FUNCTION_MISSING'}}
  Invoke-Expression $definition.Extent.Text
}}
$root='{root_literal}'
$receiptDir=Join-Path $root 'receipts\terminal-transfer-membership'
[void][IO.Directory]::CreateDirectory($receiptDir)
$gen='a'*64; $revision='b'*40; $ack='c'*32; $fileSha='d'*64; $pageSha='e'*64
$localPayload="5:a.txt`:3`:$fileSha`n"
$pagePayload="0:$pageSha`:1`:3`n"
$sha=[Security.Cryptography.SHA256]::Create()
try{{$localDigest=-join($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($localPayload))|%{{$_.ToString('x2')}})}}finally{{$sha.Dispose()}}
$sha=[Security.Cryptography.SHA256]::Create()
try{{$pageDigest=-join($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($pagePayload))|%{{$_.ToString('x2')}})}}finally{{$sha.Dispose()}}
$membership=[ordered]@{{
  schema='fly_terminal_transfer_membership_receipt_v1'; inventory_generation_id=$gen; inventory_sha256=$gen
  source_git_rev=$revision; collection_epoch_id='epoch'; tile_registry_signature='tiles'; manifest_file_count=1; manifest_total_bytes=3
  post_ack_identity_fence='PASSED'
  remote_final_ack=[ordered]@{{ok=$true;operation='FINALIZE';inventory_status='VALIDATED';ack_session_id=$ack;expected_count=1;accepted_count=1;rejected_count=0}}
  manifest_pages=[ordered]@{{descriptor_schema='fly_manifest_page_descriptor_v1';canonicalization='PAGE_INDEX_PAGE_SHA256_FILE_COUNT_TOTAL_BYTES_UTF8_LF_V1';sorted_page_digest_sha256=$pageDigest;descriptors=@([ordered]@{{page_index=0;page_sha256=$pageSha;file_count=1;total_bytes=3}})}}
  content_coverage=[ordered]@{{local_content_coverage_complete=$true;promotion_content_hash_status='LOCAL_COMPLETE_FRESH_RECOMPUTED';local_full_file_sha256=[ordered]@{{status='COMPLETE_FRESH_RECOMPUTED';canonicalization='UTF8_PATH_BYTE_LENGTH_RELATIVE_PATH_SIZE_BYTES_SHA256_UTF8_LF_V1';file_count=1;total_bytes=3;sorted_file_digest_sha256=$localDigest;files=@([ordered]@{{relative_path='a.txt';size_bytes=3;sha256=$fileSha}})}}}}
}}
$membershipName="terminal-transfer-membership-$gen-$ack.json"
$membershipPath=Join-Path $receiptDir $membershipName
[IO.File]::WriteAllText($membershipPath,(($membership|ConvertTo-Json -Depth 12 -Compress)+[Environment]::NewLine),[Text.UTF8Encoding]::new($false))
$membershipSha=Get-FlyGenerationResumeSessionSha256 -LiteralPath $membershipPath
$identity=[pscustomobject]@{{inventory_generation_id=$gen;inventory_sha256=$gen;source_git_rev=$revision;collection_epoch_id='epoch';tile_registry_signature='tiles'}}
$result=[pscustomobject]@{{AckSessionId=$ack;AckExpectedCount=1;ManifestPageCount=1;terminal_membership_receipt_name=$membershipName;terminal_membership_receipt_sha256=$membershipSha;terminal_membership_receipt_schema='fly_terminal_transfer_membership_receipt_v1';terminal_membership_receipt_content_hash_status='LOCAL_COMPLETE_FRESH_RECOMPUTED';CanonicalPointerPath=(Join-Path $root 'canonical_dataset_current.json');CanonicalPointerSha256=''}}
$binding=Get-FlyGenerationResumeSessionMembershipBinding -Identity $identity -Result $result -TargetDirectory $root
$pointer=[ordered]@{{dataset_epoch='epoch';source_revision=$revision;deployed_revision=$revision;tile_config_signature='tiles';terminal_membership_receipt_name=$membershipName;terminal_membership_receipt_sha256=$membershipSha;terminal_membership_schema='fly_terminal_transfer_membership_receipt_v1';terminal_membership_content_digest_sha256=$localDigest;terminal_membership_manifest_page_digest_sha256=$pageDigest;entry_hash=('f'*64);dataset_checksum=('1'*64)}}
[IO.File]::WriteAllText($result.CanonicalPointerPath,(($pointer|ConvertTo-Json -Compress)+[Environment]::NewLine),[Text.UTF8Encoding]::new($false))
$result.CanonicalPointerSha256=Get-FlyGenerationResumeSessionSha256 -LiteralPath $result.CanonicalPointerPath
Assert-FlyGenerationResumeSessionPointerBinding -Identity $identity -Result $result -Membership $binding -TargetDirectory $root
$pointer.terminal_membership_content_digest_sha256='0'*64
[IO.File]::WriteAllText($result.CanonicalPointerPath,(($pointer|ConvertTo-Json -Compress)+[Environment]::NewLine),[Text.UTF8Encoding]::new($false))
$result.CanonicalPointerSha256=Get-FlyGenerationResumeSessionSha256 -LiteralPath $result.CanonicalPointerPath
try{{Assert-FlyGenerationResumeSessionPointerBinding -Identity $identity -Result $result -Membership $binding -TargetDirectory $root;throw 'DIGEST_MISMATCH_ACCEPTED'}}catch{{if($_.Exception.Message -cne 'CANONICAL_POINTER_BINDING_INVALID'){{throw}}}}
$pointer.terminal_membership_content_digest_sha256=$localDigest
$pointer.source_revision=$revision.Substring(0,12)
[IO.File]::WriteAllText($result.CanonicalPointerPath,(($pointer|ConvertTo-Json -Compress)+[Environment]::NewLine),[Text.UTF8Encoding]::new($false))
$result.CanonicalPointerSha256=Get-FlyGenerationResumeSessionSha256 -LiteralPath $result.CanonicalPointerPath
try{{Assert-FlyGenerationResumeSessionPointerBinding -Identity $identity -Result $result -Membership $binding -TargetDirectory $root;throw 'SHORT_REVISION_ACCEPTED'}}catch{{if($_.Exception.Message -cne 'CANONICAL_POINTER_BINDING_INVALID'){{throw}}}}
Write-Output 'POINTER_MEMBERSHIP_BINDING_OK'
"""
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "POINTER_MEMBERSHIP_BINDING_OK" in completed.stdout


run_pointer_membership_binding_synthetic()
run_membership_contract_synthetic(
    DIAGNOSTICS / "stay-with-364636c8.ps1",
    "Test-ObserverExactCount",
    "Get-ObserverMembershipDigest",
    "Get-ObserverMembershipPageDigest",
    "SESSION_RESULT_INVALID",
)

assert "AI" not in text("fly-dashboard-proxy.py").replace(
    "contains no strategy, exchange, or AI code", ""
)
assert "python bot.py" not in (
    ROOT / "services" / "btc-conservative-agent" / "start.ps1"
).read_text(encoding="utf-8")
assert "REFUSED_NON_FLY_RUNTIME" in (
    ROOT / "services" / "btc-conservative-agent" / "start.ps1"
).read_text(encoding="utf-8")
assert "desktop strategy environment export is disabled" in text(
    "print-home-bot-env.mjs"
)
assert "Railway showcase credential push is disabled" in text(
    "push-showcase-bot-credentials.mjs"
)
assert "railwayBotControl: 'disabled'" in text("railway-showcase-control.mjs")
assert "Local strategy lab is disabled" in text("home-stack-local-lab.ps1")
assert "REFUSED_LEGACY_TUNNEL" in text("refuse-legacy-tunnel.ps1")
assert "fly-canonical.lock.json" in text("setup-named-tunnel-api.mjs")

print("Fly single-owner desktop mirror contract checks passed")

from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCERS = (
    ROOT / "scripts" / "sync-fly-bot-data.ps1",
)


def function_body(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    next_function = source.find("\nfunction ", start + 1)
    return source[start:] if next_function < 0 else source[start:next_function]


sources = [path.read_text(encoding="utf-8-sig") for path in PRODUCERS]
helpers = [function_body(source, "Resolve-DataSyncManifestMemberPath") for source in sources]
assert len(helpers) == 1

for source in sources:
    helper = function_body(source, "Resolve-DataSyncManifestMemberPath")
    for required in (
        "$MemberPath.Contains('\\')",
        "$MemberPath.Contains(':')",
        "$MemberPath.Split('/')",
        "[Text.Encoding]::UTF8.GetByteCount($MemberPath) -gt 1024",
        "[IO.Path]::IsPathRooted($MemberPath)",
        "$rootFull + [IO.Path]::DirectorySeparatorChar",
        "StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)",
        "Assert-FlyBundleUnlinkedPath -Path $candidate",
        "DATA_SYNC_MANIFEST_MEMBER_INVALID",
        "DATA_SYNC_MANIFEST_MEMBER_OUTSIDE_ROOT",
        "\\x7F",
    ):
        assert required in helper
    assert "Assert-FlyBundleUnlinkedPath -Path $rootFull" not in helper
    assert helper.count("Assert-FlyBundleUnlinkedPath") == 1
    admission = source.index("$admittedMemberPaths =")
    assert admission < source.index("$localRetirementEnabled")
    assert admission < source.index("$incomingGrowth =")
    assert admission < source.index("Receive-FlyTransportBundles")
    assert "$candidate = $admittedMemberPaths[[string]$row.path]" in source
    assert "$local = Resolve-DataSyncManifestMemberPath -Root $targetRoot -MemberPath $rel" in source
    assert "$localAfterParentCreate = Resolve-DataSyncManifestMemberPath" in source


def run_direct_contract(producer: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return
    producer_source = producer.read_text(encoding="utf-8-sig")
    bundle_source = (producer.parent / "fly-sync-bundles.ps1").read_text(encoding="utf-8-sig")
    imported_functions = "\n".join(
        (
            function_body(bundle_source, "Assert-FlyBundleUnlinkedPath"),
            function_body(producer_source, "Resolve-DataSyncManifestMemberPath"),
        )
    )
    with tempfile.TemporaryDirectory(prefix="fly-member-admission-") as temporary:
        root_literal = str(Path(temporary) / "mirror").replace("'", "''")
        command = rf"""
$ErrorActionPreference='Stop'
{imported_functions}
$root='{root_literal}'
[void][IO.Directory]::CreateDirectory($root)
$valid=Resolve-DataSyncManifestMemberPath -Root $root -MemberPath 'nested/good.jsonl'
$prefix=[IO.Path]::GetFullPath($root).TrimEnd('\','/')+[IO.Path]::DirectorySeparatorChar
if(-not $valid.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)){{throw 'VALID_MEMBER_OUTSIDE'}}
$invalid=@(
  '..\outside.txt',
  '../mirror-sibling/evil.txt',
  'nested\evil.txt',
  '/absolute.txt',
  'C:/drive.txt',
  '//server/share.txt',
  'nested//empty.txt',
  'nested/../escape.txt',
  'nested/COM1.txt',
  ('nested/'+('é'*510)+'.txt'),
  "nested/$([char]0x7f)evil.txt"
)
foreach($member in $invalid){{
  try{{Resolve-DataSyncManifestMemberPath -Root $root -MemberPath $member | Out-Null; throw 'INVALID_MEMBER_ACCEPTED'}}
  catch{{
    if($_.Exception.Message -cnotin @('DATA_SYNC_MANIFEST_MEMBER_INVALID','DATA_SYNC_MANIFEST_MEMBER_OUTSIDE_ROOT')){{throw}}
  }}
}}

# The retained candidate guard must still walk through the root and its
# ancestors. Simulate the filesystem cmdlets so this assertion is independent
# of Windows symlink privileges.
$script:visited=[Collections.Generic.List[string]]::new()
$script:reparseAt=''
function Test-Path {{ param([string]$LiteralPath,[string]$PathType) return $true }}
function Get-Item {{
  param([string]$LiteralPath,[switch]$Force)
  $full=[IO.Path]::GetFullPath($LiteralPath).TrimEnd('\')
  $script:visited.Add($full)
  $attributes=if($full -ceq $script:reparseAt){{[IO.FileAttributes]::ReparsePoint}}else{{[IO.FileAttributes]::Directory}}
  return [pscustomobject]@{{Attributes=$attributes}}
}}
$null=Resolve-DataSyncManifestMemberPath -Root $root -MemberPath 'nested/missing.jsonl'
$rootFull=[IO.Path]::GetFullPath($root).TrimEnd('\')
if(-not $script:visited.Contains($rootFull)){{throw 'CANDIDATE_GUARD_DID_NOT_REACH_ROOT'}}
if(-not $script:visited.Contains([IO.Path]::GetDirectoryName($rootFull))){{throw 'CANDIDATE_GUARD_DID_NOT_REACH_ROOT_ANCESTOR'}}
$script:reparseAt=$rootFull
$rootReparseRejected=$false
try{{Resolve-DataSyncManifestMemberPath -Root $root -MemberPath 'nested/reparse.jsonl' | Out-Null}}
catch{{$rootReparseRejected=$_.Exception.Message -ceq 'BUNDLE_LINK_OR_REPARSE_REJECTED'}}
if(-not $rootReparseRejected){{throw 'ROOT_REPARSE_WAS_NOT_REJECTED_BY_CANDIDATE_GUARD'}}
$script:reparseAt=[IO.Path]::GetFullPath((Join-Path $root 'dangling')).TrimEnd('\')
$danglingAncestorRejected=$false
try{{Resolve-DataSyncManifestMemberPath -Root $root -MemberPath 'dangling/child.jsonl' | Out-Null}}
catch{{$danglingAncestorRejected=$_.Exception.Message -ceq 'BUNDLE_LINK_OR_REPARSE_REJECTED'}}
if(-not $danglingAncestorRejected){{throw 'DANGLING_REPARSE_ANCESTOR_WAS_NOT_REJECTED'}}

Remove-Item Function:\Test-Path
Remove-Item Function:\Get-Item
function Test-Path {{ throw 'SIMULATED_ACCESS_DENIED' }}
$accessDenied=$false
try{{Resolve-DataSyncManifestMemberPath -Root $root -MemberPath 'nested/denied.jsonl' | Out-Null}}
catch{{$accessDenied=$_.Exception.Message -ceq 'SIMULATED_ACCESS_DENIED'}}
if(-not $accessDenied){{throw 'CANDIDATE_GUARD_ACCESS_ERROR_NOT_FAIL_CLOSED'}}
Write-Output 'DIRECT_MEMBER_CONTRACT_OK'
"""
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "DIRECT_MEMBER_CONTRACT_OK" in completed.stdout


def run_validation_progress_contract(producer: Path) -> None:
    powershell = shutil.which("powershell.exe")
    if not powershell:
        return
    source = producer.read_text(encoding="utf-8-sig")
    heartbeat_function = function_body(source, "Write-SyncProgressHeartbeat")
    with tempfile.TemporaryDirectory(prefix="fly-admission-progress-") as temporary:
        heartbeat = str(Path(temporary) / "progress.json").replace("'", "''")
        command = rf"""
$ErrorActionPreference='Stop'
{heartbeat_function}
$ProgressHeartbeatFile='{heartbeat}'
$ProgressRelayEvidenceJson=''
$SourceUrl='fixture://local'
$MirroredSourceRevision=('a'*40)
$manifest=[pscustomobject]@{{
  source_git_rev=('a'*40);inventory_generation_id=('b'*64);inventory_sha256=('b'*64);
  inventory_generated_at='2026-09-21T00:00:00Z';collection_epoch_id='fixture';
  manifest_page_count=102;tile_registry_signature=('c'*64)
}}
Write-SyncProgressHeartbeat -Phase 'manifest_path_validation' -ValidationOnly `
  -ValidationIndex 250 -ValidationCount 25447
$progress=Get-Content -LiteralPath $ProgressHeartbeatFile -Raw | ConvertFrom-Json
if($progress.inProgress -ne $true -or $progress.validationOnly -ne $true){{throw 'VALIDATION_PROGRESS_NOT_IN_PROGRESS'}}
if($progress.completionAuthority -cne 'NONE_PATH_VALIDATION_ONLY'){{throw 'VALIDATION_PROGRESS_AUTHORITY_INVALID'}}
if($progress.validatedMemberCount -ne 250 -or $progress.expectedMemberCount -ne 25447){{throw 'VALIDATION_PROGRESS_COUNT_INVALID'}}
if($progress.fileIndex -ne 0 -or $progress.fileCount -ne 0 -or $progress.fileBytes -ne 0 -or $progress.remoteBytes -ne 0){{throw 'VALIDATION_PROGRESS_CLAIMED_TRANSFER'}}
if($progress.PSObject.Properties.Name -contains 'ackAccepted'){{throw 'VALIDATION_PROGRESS_CLAIMED_ACK'}}
Write-Output 'VALIDATION_PROGRESS_CONTRACT_OK'
"""
        encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
        completed = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert "VALIDATION_PROGRESS_CONTRACT_OK" in completed.stdout


for producer in PRODUCERS:
    run_direct_contract(producer)
    run_validation_progress_contract(producer)


for source in sources:
    assert '$validatedManifestMemberCount % 250' in source
    assert '-Phase "manifest_path_validation"' in source
    assert "-ValidationOnly" in source
    assert "completionAuthority'] = 'NONE_PATH_VALIDATION_ONLY'" in source
    assert "$progress.fileIndex = 0" in source
    assert "$progress.fileCount = 0" in source
    assert "$progress.fileBytes = 0" in source
    assert "$progress.remoteBytes = 0" in source
    assert "--terminal-membership-receipt $terminalMembershipReceiptPath" in source

for source in sources:
    publish = "Publish-MirrorCandidate -Candidate $candidate -Destination $localBeforePublish"
    assert publish in source
    publish_at = source.index(publish)
    revalidate = "Resolve-DataSyncManifestMemberPath -Root $targetRoot -MemberPath $rel"
    assert source.rfind(revalidate, 0, publish_at) > publish_at - 500

print("Fly sync manifest member pre-admission checks passed")

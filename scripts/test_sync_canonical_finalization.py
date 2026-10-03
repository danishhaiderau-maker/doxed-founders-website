from pathlib import Path
import subprocess
import shutil
import re
import pytest


@pytest.mark.parametrize('failure', [False, True])
def test_actual_finalization_does_not_publish_before_manifest(tmp_path, failure):
    source = Path(__file__).with_name('sync-fly-bot-data.ps1').read_text(encoding='utf-8-sig')
    start = source.index('$canonicalCandidate = if ($ProgressHeartbeatFile)')
    end = source.index('[pscustomobject]@{', start)
    block = source[start:end]
    root = str(tmp_path).replace("'", "''")
    helper = str(Path(__file__).with_name('fly-mirror-atomic.ps1')).replace("'", "''")
    harness = f"""
$ErrorActionPreference='Stop'
. '{helper}'
$ProgressHeartbeatFile='{root}/public.json'
$repoRoot='{root}'
$targetRoot='{root}'
$selectedFiles=@(@{{size=4}})
$terminalAcknowledgement=[pscustomobject]@{{AckAccepted=$true;AckFinalized=$true;AckCoverageComplete=$true;AckAcceptedCount=[long]1;AckExpectedCount=[long]1;AckRejectedCount=[long]0;AckOperation='FINALIZE';AckInventoryStatus='VALIDATED';AckSessionId=('d'*32);AckManifestPagesComplete=$true;AckInventoryFileCount=[long]1}}
Set-Content -LiteralPath $ProgressHeartbeatFile -Value 'INCOMPLETE'
function Write-SyncProgressHeartbeat {{
 param($Phase,$FileIndex,$FileCount,$FileBytes,$RemoteBytes,[switch]$Completed,$ReceiptTarget,$TerminalAcknowledgement)
 if(-not $Completed -or $ReceiptTarget -eq $ProgressHeartbeatFile){{throw 'PRIVATE_REQUIRED'}}
 if($TerminalAcknowledgement.AckAccepted -ne $true -or $TerminalAcknowledgement.AckFinalized -ne $true -or $TerminalAcknowledgement.AckInventoryFileCount -ne 1){{throw 'ACK_RECEIPT_MISSING'}}
 Set-Content -LiteralPath $ReceiptTarget -Value 'COMPLETE'
}}
function python {{
 if((Get-Content -LiteralPath $ProgressHeartbeatFile -Raw).Trim() -ne 'INCOMPLETE'){{throw 'EARLY_PUBLICATION'}}
 if((Get-Content -LiteralPath $canonicalCandidate -Raw).Trim() -ne 'COMPLETE'){{throw 'CANDIDATE_MISSING'}}
 Set-Content -LiteralPath (Join-Path $targetRoot 'canonical_dataset_current.json') -Value '{{"schema":"test"}}'
 $global:LASTEXITCODE={1 if failure else 0}
 'MANIFEST_RECEIPT'
}}
$caught=$false
try {{
{block}
}} catch {{if($_.Exception.Message -notlike 'Canonical manifest commit failed*'){{throw}}; $caught=$true}}
if($caught -ne ${str(failure).lower()}){{throw 'FAILURE_PROPAGATION'}}
$expected='{ 'INCOMPLETE' if failure else 'COMPLETE' }'
if((Get-Content -LiteralPath $ProgressHeartbeatFile -Raw).Trim() -ne $expected){{throw 'WRONG_PUBLIC_RECEIPT'}}
if(Test-Path -LiteralPath $canonicalCandidate){{throw 'TEMP_NOT_CLEANED'}}
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_manifest_does_not_reference_private_receipt_path():
    source = Path(__file__).with_name('migrate_canonical_research_store.py').read_text()
    body = source[source.index('    manifest = append_manifest('):source.index('    parity = publish_parity_status(')]
    assert 'heartbeat_path' not in body


def test_terminal_heartbeat_carries_final_remote_ack_custody_fields():
    source = Path(__file__).with_name('sync-fly-bot-data.ps1').read_text(encoding='utf-8-sig')
    acknowledgement_start = source.index('$terminalAcknowledgement = New-DataSyncTerminalAcknowledgement')
    acknowledgement_end = source.index('$terminalMembershipReceiptPath = Get-DataSyncTerminalMembershipReceiptPath', acknowledgement_start)
    acknowledgement = source[acknowledgement_start:acknowledgement_end]
    start = source.index('if ($null -ne $TerminalAcknowledgement)')
    end = source.index('$backup = "$temporary.replace-backup"', start)
    terminal = source[start:end]

    assert '-MembershipReceipt $terminalMembershipEvidence' in acknowledgement
    assert '-PersistedMembershipReceipt $terminalMembershipReceipt' in acknowledgement
    assert '-AckAcceptedCount ([int64]$ackAccepted)' in acknowledgement
    assert '-AckRejectedCount ([int64]$ackRejected)' in acknowledgement
    assert "$progress['ackInventoryFileCount'] = [int64]$TerminalAcknowledgement.AckInventoryFileCount" in terminal
    assert "$progress['ackMembershipReceiptSha256'] = [string]$TerminalAcknowledgement.AckMembershipReceiptSha256" in terminal
    assert "$progress['ackLocalContentDigestSha256'] = [string]$TerminalAcknowledgement.AckLocalContentDigestSha256" in terminal
    assert "$progress['completionAuthority'] = 'REMOTE_ACK_FINALIZED'" in terminal
    assert "$progress['ackPending'] = $false" in terminal
    result_block = source[source.rindex('[pscustomobject]@{'):]
    assert 'AckAccepted = [bool]$terminalAcknowledgement.AckAccepted' in result_block
    assert 'AckMembershipReceiptSha256 = [string]$terminalAcknowledgement.AckMembershipReceiptSha256' in result_block
    assert 'AckLocalContentDigestSha256 = [string]$terminalAcknowledgement.AckLocalContentDigestSha256' in result_block
    assert 'if ([int]$manifest.file_count -le 0)' in source
    assert 'Fly sync refuses to finalize an empty acknowledgement set.' in source


def test_actual_atomic_helper_publication_failure_preserves_old_receipt(tmp_path):
    source = Path(__file__).with_name('sync-fly-bot-data.ps1').read_text(encoding='utf-8-sig')
    start = source.index('$canonicalCandidate = if ($ProgressHeartbeatFile)')
    block = source[start:source.index('[pscustomobject]@{', start)]
    root = str(tmp_path).replace("'", "''")
    helper = str(Path(__file__).with_name('fly-mirror-atomic.ps1')).replace("'", "''")
    harness = f"""
$ErrorActionPreference='Stop'
. '{helper}'
$ProgressHeartbeatFile='{root}/public.json'
$repoRoot='{root}'
$targetRoot='{root}'
$selectedFiles=@(@{{size=4}})
$terminalAcknowledgement=[pscustomobject]@{{AckAccepted=$true;AckFinalized=$true;AckCoverageComplete=$true;AckAcceptedCount=[long]1;AckExpectedCount=[long]1;AckRejectedCount=[long]0;AckOperation='FINALIZE';AckInventoryStatus='VALIDATED';AckSessionId=('d'*32);AckManifestPagesComplete=$true;AckInventoryFileCount=[long]1}}
Set-Content -LiteralPath $ProgressHeartbeatFile -Value 'INCOMPLETE'
function Start-Sleep {{param($Milliseconds)}}
function Write-SyncProgressHeartbeat {{
 param($Phase,$FileIndex,$FileCount,$FileBytes,$RemoteBytes,[switch]$Completed,$ReceiptTarget,$TerminalAcknowledgement)
 if($TerminalAcknowledgement.AckAccepted -ne $true -or $TerminalAcknowledgement.AckFinalized -ne $true -or $TerminalAcknowledgement.AckInventoryFileCount -ne 1){{throw 'ACK_RECEIPT_MISSING'}}
 Set-Content -LiteralPath $ReceiptTarget -Value 'COMPLETE'
}}
function python {{
 $global:LASTEXITCODE=0
 Set-Content -LiteralPath (Join-Path $targetRoot 'canonical_dataset_current.json') -Value '{{"schema":"test"}}'
 # Injection: hold the prior public receipt without FILE_SHARE_DELETE.
 $script:reader=[IO.File]::Open($ProgressHeartbeatFile,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 'MANIFEST_RECEIPT'
}}
$caught=$false
try {{
{block}
}} catch {{
 if($_.Exception.Message -notlike '*Atomic mirror publish failed after 12 attempt(s)*'){{throw}}
 $caught=$true
}} finally {{if($script:reader){{$script:reader.Dispose()}}}}
if(-not $caught){{throw 'FAILURE_NOT_PROPAGATED'}}
if((Get-Content -LiteralPath $ProgressHeartbeatFile -Raw).Trim() -ne 'INCOMPLETE'){{throw 'OLD_RECEIPT_LOST'}}
if(Test-Path -LiteralPath $canonicalCandidate){{throw 'CANDIDATE_NOT_CLEANED'}}
if(Test-Path -LiteralPath $canonicalBackup){{throw 'BACKUP_NOT_CLEANED'}}
"""
    import os
    if os.name != 'nt':
        pytest.skip('Windows share-delete publication semantics')
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_terminal_membership_receipt_is_fenced_before_canonical_promotion():
    source = Path(__file__).with_name('sync-fly-bot-data.ps1').read_text(encoding='utf-8-sig')

    post_ack_fence = source.index(
        'Assert-DataSyncManifestIdentity -Initial $manifest -Final $postAckManifest'
    )
    receipt_build = source.index(
        '$terminalMembershipEvidence = New-DataSyncTerminalMembershipReceipt'
    )
    receipt_write = source.index(
        '$terminalMembershipReceipt = Write-DataSyncTerminalMembershipReceipt', receipt_build
    )
    acknowledgement = source.index(
        '$terminalAcknowledgement = New-DataSyncTerminalAcknowledgement', receipt_write
    )
    canonical_candidate = source.index('$canonicalCandidate = if ($ProgressHeartbeatFile)')
    migration_receipt_argument = source.index(
        '--terminal-membership-receipt $terminalMembershipReceiptPath'
    )
    result_block = source[source.rindex('[pscustomobject]@{'):]

    assert post_ack_fence < receipt_build < receipt_write < acknowledgement < canonical_candidate < migration_receipt_argument
    assert '-PostAckIdentityFencePassed' in source[receipt_build:receipt_write]
    assert '-CanonicalSourceRevision $canonicalSourceRevision' in source[receipt_build:receipt_write]
    canonical_fence = source[post_ack_fence:receipt_build]
    assert "$MirroredSourceRevision -notmatch '^[0-9a-fA-F]{40}$'" in canonical_fence
    assert '$finalManifestSourceRevision = [string]$postAckManifest.source_git_rev' in canonical_fence
    assert '$MirroredSourceRevision.StartsWith($finalManifestSourceRevision' in canonical_fence
    assert 'terminal_membership_receipt_name' in result_block
    assert 'terminal_membership_receipt_sha256' in result_block
    assert 'terminal_membership_receipt_schema' in result_block
    assert 'terminal_membership_receipt_content_hash_status' in result_block
    assert 'AckMembershipReceiptSha256' in result_block
    assert 'AckLocalContentDigestSha256' in result_block
    assert 'TerminalMembershipReceiptName' not in result_block


def test_terminal_membership_receipt_contract_is_content_honest_and_fail_closed():
    source = Path(__file__).with_name('sync-fly-bot-data.ps1').read_text(encoding='utf-8-sig')
    page_digest = source[
        source.index('function Get-DataSyncTerminalMembershipPageDigest'):
        source.index('function Get-DataSyncTerminalMembershipLocalContentDigest')
    ]
    local_digest = source[
        source.index('function Get-DataSyncTerminalMembershipLocalContentDigest'):
        source.index('function Assert-DataSyncTerminalMembershipIdentityText')
    ]
    builder = source[
        source.index('function New-DataSyncTerminalMembershipReceipt'):
        source.index('function Write-DataSyncTerminalMembershipReceipt')
    ]
    writer = source[
        source.index('function Write-DataSyncTerminalMembershipReceipt'):
        source.index('$syncState = @{}')
    ]

    assert 'total_bytes = [int64]$pageBytes' in source
    assert "PAGE_INDEX_PAGE_SHA256_FILE_COUNT_TOTAL_BYTES_UTF8_LF_V1" in page_digest
    assert 'page descriptor totals do not match the manifest' in page_digest
    assert 'Get-FileHash -LiteralPath $local -Algorithm SHA256' in local_digest
    assert '$syncState' not in local_digest
    assert "UTF8_PATH_BYTE_LENGTH_RELATIVE_PATH_SIZE_BYTES_SHA256_UTF8_LF_V1" in local_digest

    assert "schema = 'fly_terminal_transfer_membership_receipt_v1'" in builder
    for field in (
        'inventory_generation_id', 'inventory_sha256', 'source_git_rev',
        'collection_epoch_id', 'tile_registry_signature', 'remote_final_ack',
        'expected_count', 'accepted_count', 'rejected_count',
        'post_ack_identity_fence', 'sorted_page_digest_sha256',
    ):
        assert field in builder
    assert "remote_per_file_content_sha256 = 'UNAVAILABLE_NOT_DECLARED_BY_MANIFEST'" in builder
    assert 'local_content_coverage_complete = $true' in builder
    assert "promotion_content_hash_status = 'LOCAL_COMPLETE_FRESH_RECOMPUTED'" in builder
    assert 'promotion_consumer_must_verify_local_content_digest = $true' in builder
    # The persisted receipt contains a relative, sorted membership list, never
    # absolute local locations, credentials, or errors.
    persisted_receipt = builder[builder.index('return [pscustomobject][ordered]@{'):]
    assert 'files = @($localContent.files)' in persisted_receipt
    assert not re.search(r'(?m)^\s+path\s*=', persisted_receipt)
    for forbidden_field in ('headers =', 'admin_token =', 'exception ='):
        assert forbidden_field not in persisted_receipt

    assert 'Same-volume create-only move is atomic' in writer
    assert 'immutable-name collision' in writer
    assert 'Get-FileHash -LiteralPath $destination -Algorithm SHA256' in writer
    assert 'exceeds the 32 MiB safety bound' in writer


def test_terminal_membership_receipt_synthetic_write_and_fail_closed_contract(tmp_path):
    source_path = Path(__file__).with_name('sync-fly-bot-data.ps1')
    source = source_path.read_text(encoding='utf-8-sig')
    start = source.index('function Get-DataSyncManifestIdentityValue')
    end = source.index('$syncState = @{}', start)
    functions = source[start:end]
    root = str(tmp_path).replace("'", "''")
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell:
        pytest.skip('PowerShell unavailable')
    harness = f"""
$ErrorActionPreference='Stop'
function Assert-FlyBundleUnlinkedPath {{ param([string]$Path) }}
$functionSource=@'
{functions}
'@
Invoke-Expression $functionSource
$targetRoot='{root}'
[IO.File]::WriteAllText((Join-Path $targetRoot 'a.jsonl'),'abc')
[IO.File]::WriteAllText((Join-Path $targetRoot 'b.csv'),'xy')
$generation='c'*64
$canonicalRevision='abcdefabcdefabcdefabcdefabcdefabcdefabcd'
$manifest=[pscustomobject]@{{
 inventory_generation_id=$generation; inventory_sha256=$generation;
 inventory_generated_at='2026-09-14T00:00:00.0000000+00:00';
 source_git_rev='abcdefabcdef'; collection_epoch_id='epoch-1';
 tile_registry_signature='registry-1'; file_count=2; total_bytes=5;
 manifest_page_count=2;
 manifest_page_receipts=@(
  [pscustomobject]@{{page_index=1;page_sha256=('b'*64);file_count=1;total_bytes=2}},
  [pscustomobject]@{{page_index=0;page_sha256=('a'*64);file_count=1;total_bytes=3}}
 )
}}
$files=@([pscustomobject]@{{path='a.jsonl';size=3}},[pscustomobject]@{{path='b.csv';size=2}})
$ack=[pscustomobject]@{{
 ok=$true;operation='FINALIZE';inventory_status='VALIDATED';
 inventory_generation_id=$generation;inventory_sha256=$generation;
 inventory_generated_at=$manifest.inventory_generated_at;inventory_file_count=2;
 manifest_page_count=2;manifest_pages_complete=$true
}}
$receipt=New-DataSyncTerminalMembershipReceipt -Manifest $manifest -FinalAck $ack `
 -SelectedFiles $files -TargetRoot $targetRoot -AckExpectedCount 2 `
 -AckAcceptedCount 2 -AckRejectedCount 0 -AckSessionId ('d'*32) `
 -CanonicalSourceRevision $canonicalRevision `
 -PostAckIdentityFencePassed
if($receipt.source_git_rev -cne $canonicalRevision){{throw 'SOURCE_REVISION_NOT_CANONICAL'}}
if($receipt.content_coverage.local_content_coverage_complete -ne $true){{throw 'LOCAL_COVERAGE_NOT_COMPLETE'}}
if($receipt.manifest_pages.descriptors[0].page_index -ne 0){{throw 'PAGE_NOT_SORTED'}}
$written=Write-DataSyncTerminalMembershipReceipt -TargetRoot $targetRoot -Receipt $receipt
if($written.name -notmatch '^terminal-transfer-membership-c{{64}}-d{{32}}\\.json$'){{throw 'UNSAFE_NAME'}}
if($written.sha256 -notmatch '^[0-9a-f]{{64}}$'){{throw 'BAD_HASH'}}
$stored=Get-Content -LiteralPath (Join-Path $targetRoot ('receipts\\terminal-transfer-membership\\'+$written.name)) -Raw
if($stored -match '"path"|"headers"|"admin_token"|"exception"'){{throw 'FORBIDDEN_RECEIPT_FIELD'}}
if($stored -notmatch '"relative_path"'){{throw 'RELATIVE_MEMBERSHIP_MISSING'}}
if($stored.Contains($targetRoot)){{throw 'ABSOLUTE_PATH_DISCLOSED'}}
$manifest.manifest_page_receipts[0].total_bytes=3
$caught=$false
try {{
 New-DataSyncTerminalMembershipReceipt -Manifest $manifest -FinalAck $ack `
  -SelectedFiles $files -TargetRoot $targetRoot -AckExpectedCount 2 `
  -AckAcceptedCount 2 -AckRejectedCount 0 -AckSessionId ('e'*32) `
  -CanonicalSourceRevision $canonicalRevision `
  -PostAckIdentityFencePassed | Out-Null
}} catch {{$caught=$true}}
if(-not $caught){{throw 'PAGE_TOTAL_MISMATCH_NOT_FAIL_CLOSED'}}
$manifest.manifest_page_receipts[0].total_bytes=2
foreach($invalidRevision in @('abcdefabcdef', ('f'*40))) {{
 $caught=$false
 try {{
  New-DataSyncTerminalMembershipReceipt -Manifest $manifest -FinalAck $ack `
   -SelectedFiles $files -TargetRoot $targetRoot -AckExpectedCount 2 `
   -AckAcceptedCount 2 -AckRejectedCount 0 -AckSessionId ('e'*32) `
   -CanonicalSourceRevision $invalidRevision `
   -PostAckIdentityFencePassed | Out-Null
 }} catch {{$caught=$true}}
 if(-not $caught){{throw 'INVALID_CANONICAL_REVISION_NOT_FAIL_CLOSED'}}
}}
"""
    # The extracted production helper set is intentionally large.  Passing it
    # through -Command can exceed Windows' command-line limit, so execute the
    # same isolated harness from a temporary test file instead.
    harness_path = tmp_path / 'terminal-membership-contract.ps1'
    harness_path.write_text(harness, encoding='utf-8')
    result = subprocess.run(
        [shell, '-NoProfile', '-File', str(harness_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr

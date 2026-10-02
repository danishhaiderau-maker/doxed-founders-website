from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import shutil
import threading
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
$terminalMembershipReceiptPath=(Join-Path $targetRoot 'terminal-membership.json')
Set-Content -LiteralPath $terminalMembershipReceiptPath -Value '{{"schema":"test"}}'
Set-Content -LiteralPath $ProgressHeartbeatFile -Value 'INCOMPLETE'
function Write-SyncProgressHeartbeat {{
 param($Phase,$FileIndex,$FileCount,$FileBytes,$RemoteBytes,[switch]$Completed,$ReceiptTarget,$TerminalAcknowledgement)
 if(-not $Completed -or $ReceiptTarget -eq $ProgressHeartbeatFile){{throw 'PRIVATE_REQUIRED'}}
 if($TerminalAcknowledgement.AckAccepted -ne $true -or $TerminalAcknowledgement.AckFinalized -ne $true){{throw 'ACK_RECEIPT_MISSING'}}
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
    result_block = source[source.rindex('[pscustomobject]@{'):]

    assert '-MembershipReceipt $terminalMembershipEvidence' in acknowledgement
    assert '-PersistedMembershipReceipt $terminalMembershipReceipt' in acknowledgement
    assert '-AckAcceptedCount ([int64]$ackAccepted)' in acknowledgement
    assert '-AckRejectedCount ([int64]$ackRejected)' in acknowledgement
    assert "$progress['ackInventoryFileCount'] = [int64]$TerminalAcknowledgement.AckInventoryFileCount" in terminal
    assert "$progress['ackMembershipReceiptSha256'] = [string]$TerminalAcknowledgement.AckMembershipReceiptSha256" in terminal
    assert "$progress['ackLocalContentDigestSha256'] = [string]$TerminalAcknowledgement.AckLocalContentDigestSha256" in terminal
    assert "$progress['completionAuthority'] = 'REMOTE_ACK_FINALIZED'" in terminal
    assert "$progress['ackPending'] = $false" in terminal
    assert 'AckAccepted = [bool]$terminalAcknowledgement.AckAccepted' in result_block
    assert 'AckMembershipReceiptSha256 = [string]$terminalAcknowledgement.AckMembershipReceiptSha256' in result_block
    assert 'AckLocalContentDigestSha256 = [string]$terminalAcknowledgement.AckLocalContentDigestSha256' in result_block
    assert 'if ([int]$manifest.file_count -le 0)' in source


@pytest.mark.parametrize('field,value,rejected', [
    (None, None, False),
    ('accepted', "'1'", True),
    ('rejected_count', "'0'", True),
    ('inventory_file_count', "'1'", True),
    ('manifest_page_count', "'1'", True),
    ('ok', "'true'", True),
    ('manifest_pages_complete', '1', True),
    ('ack_session_id', '123', True),
    ('inventory_generation_id', "[char[]]('a'*64)", True),
    ('inventory_status', "[char[]]'VALIDATED'", True),
])
def test_raw_finalize_types_gate_membership_persistence(tmp_path, field, value, rejected):
    source = Path(__file__).with_name('sync-fly-bot-data.ps1').read_text(encoding='utf-8-sig')
    helper_start = source.index('function Test-DataSyncTerminalAcknowledgementCount')
    helper_end = source.index('function New-DataSyncTerminalAcknowledgement', helper_start)
    helpers = source[helper_start:helper_end]
    ack_call = source.index('$ack = Invoke-DataSyncJsonRequest')
    raw_validation = source.index('Assert-DataSyncRawFinalizeAcknowledgement', ack_call)
    receipt_build = source.index('$terminalMembershipEvidence = New-DataSyncTerminalMembershipReceipt')
    receipt_write = source.index('$terminalMembershipReceipt = Write-DataSyncTerminalMembershipReceipt')
    assert ack_call < raw_validation < receipt_build < receipt_write

    marker = str(tmp_path / 'FINALIZE_VALIDATED.json').replace("'", "''")
    mutation = '' if field is None else f'$ack.{field}={value}'
    expected_rejection = '$true' if rejected else '$false'
    harness = f"""
$ErrorActionPreference='Stop'
{helpers}
$manifest=[pscustomobject]@{{file_count=[long]1;manifest_page_count=[long]1;inventory_generation_id=('a'*64);inventory_sha256=('a'*64);inventory_generated_at='2026-09-14T00:00:00Z'}}
$session=('d'*32)
$ack=[pscustomobject]@{{ok=$true;manifest_pages_complete=$true;operation='FINALIZE';inventory_status='VALIDATED';inventory_generation_id=('a'*64);inventory_sha256=('a'*64);inventory_generated_at='2026-09-14T00:00:00Z';ack_session_id=$session;accepted=[long]1;rejected_count=[long]0;inventory_file_count=[long]1;manifest_page_count=[long]1}}
{mutation}
$caught=$false
try {{
 Assert-DataSyncRawFinalizeAcknowledgement -Manifest $manifest -FinalAck $ack -AckSessionId $session
 Set-Content -LiteralPath '{marker}' -Value 'FINALIZE_VALIDATED'
}} catch {{
 if($_.Exception.Message -cne 'Raw FINALIZE acknowledgement fields are invalid.'){{throw}}
 $caught=$true
}}
$expectedRejection={expected_rejection}
if($caught -ne $expectedRejection){{throw 'RAW_FINALIZE_GATE_OUTCOME_INVALID'}}
if($expectedRejection -and (Test-Path -LiteralPath '{marker}')){{throw 'FINALIZE_MEMBERSHIP_PERSISTED'}}
if(-not $expectedRejection -and -not (Test-Path -LiteralPath '{marker}')){{throw 'TYPED_FINALIZE_NOT_ACCEPTED'}}
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('timestamp,valid', [
    ('2026-09-14T00:00:00.123456+00:00', True),
    ('not-a-timestamp', False),
])
def test_invoke_rest_method_timestamp_shape_is_normalized_before_finalize(tmp_path, timestamp, valid):
    body = (
        '{"inventory_generated_at":"' + timestamp + '",'
        '"ok":true,"manifest_pages_complete":true,"operation":"FINALIZE",'
        '"inventory_status":"VALIDATED","inventory_generation_id":"' + ('a' * 64) + '",'
        '"inventory_sha256":"' + ('a' * 64) + '","ack_session_id":"' + ('d' * 32) + '",'
        '"accepted":1,"rejected_count":0,"inventory_file_count":1,"manifest_page_count":1}'
    ).encode('utf-8')

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source = Path(__file__).with_name('sync-fly-bot-data.ps1').read_text(encoding='utf-8-sig')
        transport_start = source.index('function ConvertTo-DataSyncCanonicalInventoryTimestamp')
        transport_end = source.index('function New-DataSyncManifestUri', transport_start)
        transport = source[transport_start:transport_end]
        validator_start = source.index('function Test-DataSyncTerminalAcknowledgementCount')
        validator_end = source.index('function New-DataSyncTerminalAcknowledgement', validator_start)
        validator = source[validator_start:validator_end]
        marker = str(tmp_path / 'FINALIZE_VALIDATED.json').replace("'", "''")
        url = f'http://127.0.0.1:{server.server_port}/finalize'
        expected = '$true' if valid else '$false'
        harness = f"""
$ErrorActionPreference='Stop'
$manifestTimeoutSec=5
$transportAttempts=1
$headers=@{{}}
function Test-DataSyncResourcePressureError {{param([string]$Message) return $false}}
function Get-DataSyncRetryDelaySec {{param([int]$Attempt,[bool]$ResourcePressure) return 1}}
{transport}
{validator}
$raw=Invoke-RestMethod -Uri '{url}' -Method Get
$isExpectedRawShape=$(if({expected}){{$raw.inventory_generated_at -is [DateTime]}}else{{$raw.inventory_generated_at -is [string]}})
if(-not $isExpectedRawShape){{throw 'INVOKE_REST_METHOD_SHAPE_UNEXPECTED'}}
$caught=$false
try {{
 $ack=Invoke-DataSyncJsonRequest -Stage 'local_finalize_mock' -Uri '{url}' -Method Get -MaxAttempts 1 -MaxElapsedSec 10
 $manifest=[pscustomobject]@{{file_count=[long]1;manifest_page_count=[long]1;inventory_generation_id=('a'*64);inventory_sha256=('a'*64);inventory_generated_at='2026-09-14T00:00:00.123456+00:00'}}
 Assert-DataSyncRawFinalizeAcknowledgement -Manifest $manifest -FinalAck $ack -AckSessionId ('d'*32)
 Set-Content -LiteralPath '{marker}' -Value 'FINALIZE_VALIDATED'
}} catch {{
 if({expected}){{throw}}
 $caught=$true
}}
if($caught -eq {expected}){{throw 'TIMESTAMP_GATE_OUTCOME_INVALID'}}
if({expected} -and -not (Test-Path -LiteralPath '{marker}')){{throw 'VALID_FINALIZE_NOT_PERSISTED'}}
if(-not {expected} -and (Test-Path -LiteralPath '{marker}')){{throw 'INVALID_FINALIZE_PERSISTED'}}
"""
        shell = shutil.which('pwsh') or shutil.which('powershell')
        result = subprocess.run([shell, '-NoProfile', '-Command', harness],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


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
$terminalMembershipReceiptPath=(Join-Path $targetRoot 'terminal-membership.json')
Set-Content -LiteralPath $terminalMembershipReceiptPath -Value '{{"schema":"test"}}'
Set-Content -LiteralPath $ProgressHeartbeatFile -Value 'INCOMPLETE'
function Start-Sleep {{param($Milliseconds)}}
function Write-SyncProgressHeartbeat {{
 param($Phase,$FileIndex,$FileCount,$FileBytes,$RemoteBytes,[switch]$Completed,$ReceiptTarget,$TerminalAcknowledgement)
 if($TerminalAcknowledgement.AckAccepted -ne $true -or $TerminalAcknowledgement.AckFinalized -ne $true){{throw 'ACK_RECEIPT_MISSING'}}
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


def test_terminal_membership_receipt_precedes_canonical_publication():
    source = Path(__file__).with_name('sync-fly-bot-data.ps1').read_text(encoding='utf-8-sig')
    post_ack_fence = source.index(
        'Assert-DataSyncManifestIdentity -Initial $manifest -Final $postAckManifest'
    )
    receipt_build = source.index('$terminalMembershipEvidence = New-DataSyncTerminalMembershipReceipt')
    receipt_write = source.index('$terminalMembershipReceipt = Write-DataSyncTerminalMembershipReceipt')
    acknowledgement = source.index('$terminalAcknowledgement = New-DataSyncTerminalAcknowledgement')
    canonical_candidate = source.index('$canonicalCandidate = if ($ProgressHeartbeatFile)')
    migration_call = source.index('$canonicalManifestReceipt = & python $migrationScript', canonical_candidate)
    result_block = source[source.rindex('[pscustomobject]@{'):]

    assert post_ack_fence < receipt_build < receipt_write < acknowledgement < canonical_candidate < migration_call
    assert '-PostAckIdentityFencePassed' in source[receipt_build:receipt_write]
    assert '-CanonicalSourceRevision $canonicalSourceRevision' in source[receipt_build:receipt_write]
    assert 'Test-Path -LiteralPath $terminalMembershipReceiptPath -PathType Leaf' in source[migration_call:]
    assert "$MirroredSourceRevision -notmatch '^[0-9a-fA-F]{40}$'" in source[post_ack_fence:receipt_build]
    assert 'terminal_membership_receipt_name' in result_block
    assert 'terminal_membership_receipt_sha256' in result_block


def test_terminal_membership_receipt_contract_recomputes_local_hashes():
    source = Path(__file__).with_name('sync-fly-bot-data.ps1').read_text(encoding='utf-8-sig')
    page_digest = source[source.index('function Get-DataSyncTerminalMembershipPageDigest'):source.index('function Get-DataSyncTerminalMembershipLocalContentDigest')]
    local_digest = source[source.index('function Get-DataSyncTerminalMembershipLocalContentDigest'):source.index('function Assert-DataSyncTerminalMembershipIdentityText')]
    builder = source[source.index('function New-DataSyncTerminalMembershipReceipt'):source.index('function Get-DataSyncTerminalMembershipReceiptPath')]

    assert 'PAGE_INDEX_PAGE_SHA256_FILE_COUNT_TOTAL_BYTES_UTF8_LF_V1' in page_digest
    assert 'page descriptor totals do not match the manifest' in page_digest
    assert 'Get-FileHash -LiteralPath $local -Algorithm SHA256' in local_digest
    assert 'UTF8_PATH_BYTE_LENGTH_RELATIVE_PATH_SIZE_BYTES_SHA256_UTF8_LF_V1' in local_digest
    assert "schema = 'fly_terminal_transfer_membership_receipt_v1'" in builder
    assert "remote_per_file_content_sha256 = 'UNAVAILABLE_NOT_DECLARED_BY_MANIFEST'" in builder
    assert 'promotion_consumer_must_verify_local_content_digest = $true' in builder

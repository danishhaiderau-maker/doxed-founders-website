"""Exercise the actual resume authority gate without network or source writes."""
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.parametrize('case,expected', [
    ('success', 'OK'),
    ('stale', 'RESUME_AUTHORITY_UNAVAILABLE'),
    ('string_authority', 'RESUME_AUTHORITY_UNAVAILABLE'),
    ('changed_epoch', 'RESUME_IDENTITY_CHANGED'),
    ('bad_ack', 'RESUME_TERMINAL_ACK_INVALID'),
    ('numeric_ack', 'RESUME_TERMINAL_ACK_INVALID'),
    ('zero_ack', 'RESUME_TERMINAL_ACK_INVALID'),
    ('count_mismatch', 'RESUME_TERMINAL_ACK_INVALID'),
    ('wrong_terminal_identity', 'RESUME_TERMINAL_ACK_INVALID'),
    ('no_progress', 'RESUME_NO_VERIFIED_PROGRESS'),
    ('wrong_retry_sha', 'RESUME_PROGRESS_IDENTITY_INVALID'),
])
def test_real_resume_authority_and_progress(case, expected):
    script = str(Path(__file__).with_name('fly-sync-generation-resume.ps1')).replace("'", "''")
    harness = f"""
$ErrorActionPreference='Stop'
. '{script}'
$identity=@{{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev=('b'*40);collection_epoch_id='epoch-test';tile_registry_signature=('c'*64)}}
$script:attempts=0
$read={{param($generation)
 $m=$identity.Clone();$m.file_count=[long]5;$m.total_bytes=[long]50;$m.inventory_status='CURRENT';$m.inventory_authoritative=$true;$m.inventory_ack_eligible=$true
 if('{case}' -eq 'stale'){{$m.inventory_status='STALE_REVALIDATING'}}
 if('{case}' -eq 'string_authority'){{$m.inventory_authoritative='true'}}
 if('{case}' -eq 'changed_epoch'){{$m.collection_epoch_id='different'}}
 return $m
}}
$run={{param($manifest,$attempt)
 $script:attempts++
 if('{case}' -in @('no_progress','wrong_retry_sha')){{return @{{Success=$false;Receipt=@{{failureCode='BUNDLE_TRANSFER_DEADLINE';ok=$false;inProgress=$false;ackPending=$true;completionAuthority='NONE_TRANSFER_PROGRESS_ONLY';inventoryGenerationId=('a'*64);inventorySha256=$(if('{case}' -eq 'wrong_retry_sha'){{'d'*64}}else{{'a'*64}});collectionEpochId='epoch-test';sourceRevision=('b'*40);deployedRevision=('b'*40);tileRegistrySignature=('c'*64);fileIndex=0;verifiedPayloadBytes=0}}}}}}
 $result=@{{
  AckAccepted=$true;AckFinalized=$true;AckCoverageComplete=$true;AckManifestPagesComplete=$true;
  AckAcceptedCount=[long]5;AckExpectedCount=[long]5;AckRejectedCount=[long]0;AckInventoryFileCount=[long]5;
  AckManifestFileCount=[long]5;AckManifestTotalBytes=[long]50;AckLocalContentFileCount=[long]5;AckLocalContentTotalBytes=[long]50;
  AckLocalContentDigestSha256=('e'*64);AckMembershipReceiptName=("terminal-transfer-membership-"+('a'*64)+"-"+('d'*32)+".json");
  AckMembershipReceiptSha256=('f'*64);AckMembershipReceiptSchema='fly_terminal_transfer_membership_receipt_v1';AckMembershipContentHashStatus='LOCAL_COMPLETE_FRESH_RECOMPUTED';
 AckOperation='FINALIZE';AckInventoryStatus='VALIDATED';AckSessionId=('d'*32);
  InventoryGenerationId=('a'*64);InventorySha256=('a'*64);CollectionEpochId='epoch-test';
  TileRegistrySignature=('c'*64);SourceRevision=('b'*40);CanonicalSourceRevision=('b'*40)
 }}
 if('{case}' -eq 'bad_ack'){{$result.AckAccepted=$false}}
 if('{case}' -eq 'numeric_ack'){{$result.AckAccepted=[long]5}}
 if('{case}' -eq 'zero_ack'){{$result.AckAcceptedCount=[long]0;$result.AckExpectedCount=[long]0;$result.AckInventoryFileCount=[long]0}}
 if('{case}' -eq 'count_mismatch'){{$result.AckAcceptedCount=[long]4}}
 if('{case}' -eq 'wrong_terminal_identity'){{$result.InventoryGenerationId=('d'*64)}}
 return @{{Success=$true;Result=$result}}
}}
$actual='OK'
try{{$null=Invoke-FlyGenerationResume -Identity $identity -ReadManifest $read -RunAttempt $run}}
catch{{$actual=$_.Exception.Message}}
if($actual -cne '{expected}'){{throw "Unexpected outcome: $actual"}}
if('{case}' -in @('stale','string_authority','changed_epoch') -and $script:attempts -ne 0){{throw 'UNAUTHORIZED_ATTEMPT'}}
if('{case}' -eq 'no_progress' -and $script:attempts -ne 1){{throw 'RETRIED_WITHOUT_PROGRESS'}}
if('{case}' -eq 'wrong_retry_sha' -and $script:attempts -ne 1){{throw 'HASH_MISMATCH_RETRIED'}}
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_resume_failure_receipt_is_terminal_and_redacted(tmp_path):
    script = str(Path(__file__).with_name('fly-sync-generation-resume.ps1')).replace("'", "''")
    receipt = str(tmp_path / 'terminal-failure.json').replace("'", "''")
    harness = f"""
$ErrorActionPreference='Stop'
. '{script}'
$identity=@{{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev=('b'*40);collection_epoch_id='epoch-test';tile_registry_signature=('c'*64)}}
try {{
  throw 'Fly data-sync stage=acknowledgement_page_0 failed after 1/5 attempt(s): Response status code does not indicate success: 409 (Conflict): acknowledgement identity mismatch: source_git_rev'
}} catch {{
  $result=New-FlyGenerationResumeFailureReceipt -ErrorRecord $_ -Identity $identity -Attempt 1
  Write-FlyGenerationResumeFailureReceipt -Path '{receipt}' -Receipt $result
}}
$stored=Get-Content -LiteralPath '{receipt}' -Raw | ConvertFrom-Json
if($stored.ok -ne $false -or $stored.inProgress -ne $false -or $stored.failureCode -cne 'ACK_HTTP_409_IDENTITY_MISMATCH'){{throw 'BAD_FAILURE_CODE'}}
if($stored.failureStage -cne 'acknowledgement_page_0' -or $stored.failureDiagnostic.ackHttpStatus -ne 409){{throw 'BAD_ACK_DIAGNOSTIC'}}
if($stored.failureDiagnostic.responseExcerptSha256 -cnotmatch '^[0-9a-f]{{64}}$'){{throw 'MISSING_REDACTED_DIGEST'}}
if((Get-Content -LiteralPath '{receipt}' -Raw) -match 'Response status code does not indicate success'){{throw 'RAW_RESPONSE_LEAK'}}
Write-Output 'OK'
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'OK'


@pytest.mark.parametrize(
    'deadline_code,expected_origin',
    [
        ('BUNDLE_TRANSFER_DEADLINE', 'TRUSTED_HEARTBEAT'),
        ('BUNDLE_INDEX_PREPARATION_DEADLINE', 'ERROR'),
    ],
)
def test_resume_failure_receipt_preserves_retryable_deadline_before_ack(
    tmp_path, deadline_code, expected_origin
):
    script = str(Path(__file__).with_name('fly-sync-generation-resume.ps1')).replace("'", "''")
    receipt = str(tmp_path / 'incomplete.json').replace("'", "''")
    error_text = (
        'generic bundle client stopped after a deadline' if expected_origin == 'TRUSTED_HEARTBEAT'
        else f'Fly data-sync failed: {deadline_code}'
    )
    harness = f"""
$ErrorActionPreference='Stop'
. '{script}'
$identity=@{{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev=('b'*40);collection_epoch_id='epoch-test';tile_registry_signature=('c'*64)}}
$previous=@{{
  ok=$false;inProgress=$false;phase='bundle_failed';failureCode='BUNDLE_TRANSFER_DEADLINE';ackPending=$true;
  completionAuthority='NONE_TRANSFER_PROGRESS_ONLY';inventoryGenerationId=('a'*64);inventorySha256=('a'*64);
  sourceRevision=('b'*40);deployedRevision=('b'*12);collectionEpochId='epoch-test';tileRegistrySignature=('c'*64);
  fileIndex=[long]17;fileCount=[long]99;verifiedPayloadBytes=[long]12345;currentFile='v3/evidence/17.jsonl'
}}
$previous | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath '{receipt}' -Encoding utf8
$manifest=[pscustomobject]@{{source_git_rev=('b'*12)}}
try {{ throw '{error_text}' }} catch {{
  $result=New-FlyGenerationResumeFailureReceipt -ErrorRecord $_ -Identity $identity -Manifest $manifest -PreviousReceiptPath '{receipt}' -Attempt 1
}}
if($result.failureCode -cne '{deadline_code}' -or $result.phase -cne 'retryable_deadline' -or
    $result.ackPending -ne $true -or $result.ok -ne $false -or $result.inProgress -ne $false){{throw 'BAD_RETRYABLE_STATE'}}
if($result.fileIndex -ne 17 -or $result.fileCount -ne 99 -or $result.verifiedPayloadBytes -ne 12345){{throw 'PROGRESS_NOT_PRESERVED'}}
if($result.failureDiagnostic.retryableDeadline -ne $true -or $result.failureDiagnostic.retryableDeadlineOrigin -cne '{expected_origin}'){{throw 'BAD_CLASSIFICATION'}}
Write-Output 'OK'
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'OK'


def test_resume_failure_receipt_does_not_inherit_deadline_from_foreign_progress(tmp_path):
    script = str(Path(__file__).with_name('fly-sync-generation-resume.ps1')).replace("'", "''")
    receipt = str(tmp_path / 'foreign.json').replace("'", "''")
    harness = f"""
$ErrorActionPreference='Stop'
. '{script}'
$identity=@{{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev=('b'*40);collection_epoch_id='epoch-test';tile_registry_signature=('c'*64)}}
$foreign=@{{
  failureCode='BUNDLE_TRANSFER_DEADLINE';inventoryGenerationId=('d'*64);inventorySha256=('d'*64);
  sourceRevision=('b'*40);deployedRevision=('b'*40);collectionEpochId='epoch-test';tileRegistrySignature=('c'*64);
  fileIndex=[long]17;fileCount=[long]99;verifiedPayloadBytes=[long]12345
}}
$foreign | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath '{receipt}' -Encoding utf8
$manifest=[pscustomobject]@{{source_git_rev=('b'*40)}}
try {{ throw 'generic sync exception' }} catch {{
  $result=New-FlyGenerationResumeFailureReceipt -ErrorRecord $_ -Identity $identity -Manifest $manifest -PreviousReceiptPath '{receipt}' -Attempt 1
}}
if($result.failureCode -cne 'SYNC_ATTEMPT_FAILED' -or $result.phase -cne 'terminal_failure' -or $result.ackPending -ne $false){{throw 'FOREIGN_DEADLINE_INHERITED'}}
if($result.fileIndex -ne 0 -or $result.verifiedPayloadBytes -ne 0 -or $result.failureDiagnostic.retryableDeadline -ne $false){{throw 'FOREIGN_PROGRESS_INHERITED'}}
Write-Output 'OK'
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'OK'


def test_resume_failure_receipt_does_not_reopen_terminal_ack_state(tmp_path):
    script = str(Path(__file__).with_name('fly-sync-generation-resume.ps1')).replace("'", "''")
    receipt = str(tmp_path / 'terminal.json').replace("'", "''")
    harness = f"""
$ErrorActionPreference='Stop'
. '{script}'
$identity=@{{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev=('b'*40);collection_epoch_id='epoch-test';tile_registry_signature=('c'*64)}}
$terminal=@{{
  ok=$false;inProgress=$false;failureCode='BUNDLE_TRANSFER_DEADLINE';ackPending=$false;
  completionAuthority='REMOTE_ACK_FINALIZED';inventoryGenerationId=('a'*64);inventorySha256=('a'*64);
  sourceRevision=('b'*40);deployedRevision=('b'*40);collectionEpochId='epoch-test';tileRegistrySignature=('c'*64);
  fileIndex=[long]17;fileCount=[long]99;verifiedPayloadBytes=[long]12345
}}
$terminal | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath '{receipt}' -Encoding utf8
$manifest=[pscustomobject]@{{source_git_rev=('b'*40)}}
try {{ throw 'generic sync exception' }} catch {{
  $result=New-FlyGenerationResumeFailureReceipt -ErrorRecord $_ -Identity $identity -Manifest $manifest -PreviousReceiptPath '{receipt}' -Attempt 1
}}
if($result.failureCode -cne 'SYNC_ATTEMPT_FAILED' -or $result.phase -cne 'terminal_failure' -or $result.ackPending -ne $false){{throw 'TERMINAL_STATE_REOPENED'}}
if($result.fileIndex -ne 0 -or $result.verifiedPayloadBytes -ne 0 -or $result.failureDiagnostic.retryableDeadline -ne $false){{throw 'TERMINAL_PROGRESS_INHERITED'}}
Write-Output 'OK'
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'OK'


@pytest.mark.parametrize(
    'error_text',
    [
        'Fly data-sync stage=file_chunk failed: Response status code 503 SERVICE UNAVAILABLE',
        'Fly data-sync failed: HASH_MISMATCH',
    ],
)
def test_resume_failure_receipt_does_not_inherit_deadline_over_current_explicit_failure(
    tmp_path, error_text
):
    script = str(Path(__file__).with_name('fly-sync-generation-resume.ps1')).replace("'", "''")
    receipt = str(tmp_path / 'deadline-before-http.json').replace("'", "''")
    safe_error = error_text.replace("'", "''")
    harness = f"""
$ErrorActionPreference='Stop'
. '{script}'
$identity=@{{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev=('b'*40);collection_epoch_id='epoch-test';tile_registry_signature=('c'*64)}}
$previous=@{{
  ok=$false;inProgress=$false;failureCode='BUNDLE_TRANSFER_DEADLINE';ackPending=$true;
  completionAuthority='NONE_TRANSFER_PROGRESS_ONLY';inventoryGenerationId=('a'*64);inventorySha256=('a'*64);
  sourceRevision=('b'*40);deployedRevision=('b'*12);collectionEpochId='epoch-test';tileRegistrySignature=('c'*64);
  fileIndex=[long]17;fileCount=[long]99;verifiedPayloadBytes=[long]12345
}}
$previous | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath '{receipt}' -Encoding utf8
$manifest=[pscustomobject]@{{source_git_rev=('b'*12)}}
try {{ throw '{safe_error}' }} catch {{
  $result=New-FlyGenerationResumeFailureReceipt -ErrorRecord $_ -Identity $identity -Manifest $manifest -PreviousReceiptPath '{receipt}' -Attempt 1
}}
if($result.failureCode -cne 'SYNC_ATTEMPT_FAILED' -or $result.phase -cne 'terminal_failure' -or $result.ackPending -ne $false){{throw 'CURRENT_FAILURE_MASKED'}}
if($result.fileIndex -ne 17 -or $result.verifiedPayloadBytes -ne 12345){{throw 'TRUSTED_PROGRESS_LOST'}}
if($result.failureDiagnostic.retryableDeadline -ne $false -or $result.failureDiagnostic.retryableDeadlineOrigin -cne 'NONE'){{throw 'STALE_DEADLINE_INHERITED'}}
Write-Output 'OK'
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'OK'


def test_resume_retries_an_identity_bound_deadline_then_requires_terminal_ack():
    script = str(Path(__file__).with_name('fly-sync-generation-resume.ps1')).replace("'", "''")
    harness = f"""
$ErrorActionPreference='Stop'
. '{script}'
$identity=@{{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev=('b'*40);collection_epoch_id='epoch-test';tile_registry_signature=('c'*64)}}
$script:attempts=0
$read={{param($generation) [pscustomobject]@{{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev=('b'*40);collection_epoch_id='epoch-test';tile_registry_signature=('c'*64);file_count=[long]1;total_bytes=[long]3;inventory_status='CURRENT';inventory_authoritative=$true;inventory_ack_eligible=$true}}}}
$run={{param($manifest,$attempt)
 $script:attempts++
 if($attempt -eq 1){{return @{{Success=$false;Receipt=@{{failureCode='BUNDLE_TRANSFER_DEADLINE';ok=$false;inProgress=$false;phase='retryable_deadline';ackPending=$true;completionAuthority='NONE_TRANSFER_PROGRESS_ONLY';inventoryGenerationId=('a'*64);inventorySha256=('a'*64);collectionEpochId='epoch-test';sourceRevision=('b'*40);deployedRevision=('b'*40);tileRegistrySignature=('c'*64);fileIndex=[long]7;verifiedPayloadBytes=[long]777}}}}}}
 return @{{Success=$true;Result=@{{AckAccepted=$true;AckFinalized=$true;AckCoverageComplete=$true;AckManifestPagesComplete=$true;AckAcceptedCount=[long]1;AckExpectedCount=[long]1;AckRejectedCount=[long]0;AckInventoryFileCount=[long]1;AckManifestFileCount=[long]1;AckManifestTotalBytes=[long]3;AckLocalContentFileCount=[long]1;AckLocalContentTotalBytes=[long]3;AckLocalContentDigestSha256=('e'*64);AckMembershipReceiptName=("terminal-transfer-membership-"+('a'*64)+"-"+('d'*32)+".json");AckMembershipReceiptSha256=('f'*64);AckMembershipReceiptSchema='fly_terminal_transfer_membership_receipt_v1';AckMembershipContentHashStatus='LOCAL_COMPLETE_FRESH_RECOMPUTED';AckOperation='FINALIZE';AckInventoryStatus='VALIDATED';AckSessionId=('d'*32);InventoryGenerationId=('a'*64);InventorySha256=('a'*64);CollectionEpochId='epoch-test';TileRegistrySignature=('c'*64);SourceRevision=('b'*40);CanonicalSourceRevision=('b'*40)}}}}
}}
$result=Invoke-FlyGenerationResume -Identity $identity -ReadManifest $read -RunAttempt $run
if($script:attempts -ne 2 -or $result.AckAccepted -ne $true){{throw 'RETRYABLE_RESUME_DID_NOT_REACH_ACK'}}
Write-Output 'OK'
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'OK'


def test_resume_failure_receipt_recognizes_wrapped_bundle_deadline():
    script = str(Path(__file__).with_name('fly-sync-generation-resume.ps1')).replace("'", "''")
    harness = f"""
$ErrorActionPreference='Stop'
. '{script}'
$identity=@{{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev=('b'*40);collection_epoch_id='epoch-test';tile_registry_signature=('c'*64)}}
$manifest=[pscustomobject]@{{source_git_rev=('b'*12)}}
try {{ throw 'BUNDLE_TRANSFER_FAILED: BUNDLE_TRANSFER_DEADLINE' }} catch {{
  $result=New-FlyGenerationResumeFailureReceipt -ErrorRecord $_ -Identity $identity -Manifest $manifest -Attempt 1
}}
if($result.failureCode -cne 'BUNDLE_TRANSFER_DEADLINE' -or $result.phase -cne 'retryable_deadline' -or $result.ackPending -ne $true -or $result.failureDiagnostic.retryableDeadlineOrigin -cne 'ERROR'){{throw 'WRAPPED_DEADLINE_NOT_CLASSIFIED'}}
Write-Output 'OK'
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == 'OK'

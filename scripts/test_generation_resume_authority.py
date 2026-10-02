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
    ('no_progress', 'RESUME_NO_VERIFIED_PROGRESS'),
    ('retry_success', 'OK'),
    ('numeric_ok', 'RESUME_PROGRESS_IDENTITY_INVALID'),
    ('string_in_progress', 'RESUME_PROGRESS_IDENTITY_INVALID'),
    ('missing_ack_pending', 'RESUME_PROGRESS_IDENTITY_INVALID'),
])
def test_real_resume_authority_and_progress(case, expected):
    script = str(Path(__file__).with_name('fly-sync-generation-resume.ps1')).replace("'", "''")
    harness = f"""
$ErrorActionPreference='Stop'
. '{script}'
$identity=@{{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev=('b'*40);collection_epoch_id='epoch-test';tile_registry_signature=('c'*64)}}
$script:attempts=0
$read={{param($generation)
 $m=$identity.Clone();$m.inventory_status='CURRENT';$m.inventory_authoritative=$true;$m.inventory_ack_eligible=$true
 $m.file_count=[long]1;$m.total_bytes=[long]10
 if('{case}' -eq 'stale'){{$m.inventory_status='STALE_REVALIDATING'}}
 if('{case}' -eq 'string_authority'){{$m.inventory_authoritative='true'}}
 if('{case}' -eq 'changed_epoch'){{$m.collection_epoch_id='different'}}
 return $m
}}
$run={{param($manifest,$attempt)
 $script:attempts++
 if('{case}' -in @('no_progress','numeric_ok','string_in_progress','missing_ack_pending') -or ('{case}' -eq 'retry_success' -and $attempt -eq 1)){{
  $progress=@{{failureCode='BUNDLE_TRANSFER_DEADLINE';ok=$false;inProgress=$false;ackPending=$true;completionAuthority='NONE_TRANSFER_PROGRESS_ONLY';inventoryGenerationId=('a'*64);inventorySha256=('a'*64);collectionEpochId='epoch-test';sourceRevision=('b'*40);deployedRevision=('b'*40);tileRegistrySignature=('c'*64);fileIndex=$(if('{case}' -eq 'no_progress'){{0}}else{{1}});verifiedPayloadBytes=$(if('{case}' -eq 'no_progress'){{0}}else{{10}})}}
  if('{case}' -eq 'numeric_ok'){{$progress.ok=0}}
  if('{case}' -eq 'string_in_progress'){{$progress.inProgress='false'}}
  if('{case}' -eq 'missing_ack_pending'){{$null=$progress.Remove('ackPending')}}
  return @{{Success=$false;Receipt=$progress}}
 }}
 $session=('d'*32)
 return @{{Success=$true;Result=@{{AckAccepted=('{case}' -ne 'bad_ack');AckFinalized=$true;AckCoverageComplete=$true;AckManifestPagesComplete=$true;AckOperation='FINALIZE';AckInventoryStatus='VALIDATED';AckSessionId=$session;AckExpectedCount=[long]1;AckAcceptedCount=[long]1;AckInventoryFileCount=[long]1;AckRejectedCount=[long]0;AckManifestFileCount=[long]1;AckManifestTotalBytes=[long]10;AckLocalContentFileCount=[long]1;AckLocalContentTotalBytes=[long]10;AckLocalContentDigestSha256=('e'*64);AckMembershipReceiptSha256=('f'*64);AckMembershipReceiptSchema='fly_terminal_transfer_membership_receipt_v1';AckMembershipContentHashStatus='LOCAL_COMPLETE_FRESH_RECOMPUTED';AckMembershipReceiptName=("terminal-transfer-membership-$($manifest.inventory_generation_id)-$session.json");InventoryGenerationId=$manifest.inventory_generation_id;InventorySha256=$manifest.inventory_sha256;CollectionEpochId=$manifest.collection_epoch_id;TileRegistrySignature=$manifest.tile_registry_signature;CanonicalSourceRevision=('b'*40);SourceRevision=('b'*40)}}}}
}}
$actual='OK'
try{{$null=Invoke-FlyGenerationResume -Identity $identity -ReadManifest $read -RunAttempt $run}}
catch{{$actual=$_.Exception.Message}}
if($actual -cne '{expected}'){{throw "Unexpected outcome: $actual"}}
if('{case}' -in @('stale','string_authority','changed_epoch') -and $script:attempts -ne 0){{throw 'UNAUTHORIZED_ATTEMPT'}}
if('{case}' -eq 'no_progress' -and $script:attempts -ne 1){{throw 'RETRIED_WITHOUT_PROGRESS'}}
if('{case}' -in @('numeric_ok','string_in_progress','missing_ack_pending') -and $script:attempts -ne 1){{throw 'RETRIED_INVALID_PROGRESS_TYPE'}}
if('{case}' -eq 'retry_success' -and $script:attempts -ne 2){{throw 'VALID_PROGRESS_DID_NOT_RETRY'}}
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr

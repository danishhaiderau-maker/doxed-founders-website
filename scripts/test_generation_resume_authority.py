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
 if('{case}' -eq 'stale'){{$m.inventory_status='STALE_REVALIDATING'}}
 if('{case}' -eq 'string_authority'){{$m.inventory_authoritative='true'}}
 if('{case}' -eq 'changed_epoch'){{$m.collection_epoch_id='different'}}
 return $m
}}
$run={{param($manifest,$attempt)
 $script:attempts++
 if('{case}' -eq 'no_progress'){{return @{{Success=$false;Receipt=@{{failureCode='BUNDLE_TRANSFER_DEADLINE';ok=$false;inProgress=$false;ackPending=$true;completionAuthority='NONE_TRANSFER_PROGRESS_ONLY';inventoryGenerationId=('a'*64);collectionEpochId='epoch-test';sourceRevision=('b'*40);deployedRevision=('b'*40);tileRegistrySignature=('c'*64);fileIndex=0;verifiedPayloadBytes=0}}}}}}
 return @{{Success=$true;Result=@{{AckAccepted=('{case}' -ne 'bad_ack');SourceRevision=('b'*40)}}}}
}}
$actual='OK'
try{{$null=Invoke-FlyGenerationResume -Identity $identity -ReadManifest $read -RunAttempt $run}}
catch{{$actual=$_.Exception.Message}}
if($actual -cne '{expected}'){{throw "Unexpected outcome: $actual"}}
if('{case}' -in @('stale','string_authority','changed_epoch') -and $script:attempts -ne 0){{throw 'UNAUTHORIZED_ATTEMPT'}}
if('{case}' -eq 'no_progress' -and $script:attempts -ne 1){{throw 'RETRIED_WITHOUT_PROGRESS'}}
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness],
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr

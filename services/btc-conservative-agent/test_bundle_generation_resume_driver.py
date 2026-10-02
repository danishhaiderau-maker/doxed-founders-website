import subprocess
from pathlib import Path
import pytest

PWSH = Path('C:/Users/danis/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe')
SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/fly-sync-generation-resume.ps1'


@pytest.mark.parametrize('case,expected', [
    ('success','OK:2'), ('exhaust','RESUME_ATTEMPTS_EXHAUSTED:4'),
    ('stalled','RESUME_NO_VERIFIED_PROGRESS:2'), ('integrity','RESUME_NON_DEADLINE_FAILURE:1'),
    ('changed','RESUME_IDENTITY_CHANGED:1'), ('stale','RESUME_AUTHORITY_UNAVAILABLE:1'),
    ('wrongreceipt','RESUME_PROGRESS_IDENTITY_INVALID:1'), ('ack','RESUME_TERMINAL_ACK_INVALID:1'),
    ('incomplete_terminal','RESUME_TERMINAL_ACK_INVALID:1'),
    ('wrong_terminal_generation','RESUME_TERMINAL_ACK_INVALID:1'),
    ('missing_terminal_session','RESUME_TERMINAL_ACK_INVALID:1'),
    ('wrong_terminal_session','RESUME_TERMINAL_ACK_INVALID:1')])
def test_actual_bounded_driver(case, expected):
    code = r'''
. '__SCRIPT__'
$identity = [pscustomobject]@{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev='rev';collection_epoch_id='epoch';tile_registry_signature='tile'}
$script:attempts=0
$read = {
 param($g)
 $m=[pscustomobject]@{inventory_generation_id=$g;inventory_sha256=$g;source_git_rev='rev';collection_epoch_id='epoch';tile_registry_signature='tile';inventory_status='CURRENT';inventory_authoritative=$true;inventory_ack_eligible=$true;file_count=1;total_bytes=10}
 if ($script:attempts -gt 0 -and '__CASE__' -eq 'changed') {$m.source_git_rev='other'}
 if ($script:attempts -gt 0 -and '__CASE__' -eq 'stale') {$m.inventory_status='BUILDING'}
 return $m
}
$script:newTerminal = {
 param($m,[string]$session=('d'*32))
 [pscustomobject]@{AckAccepted=$true;AckFinalized=$true;AckCoverageComplete=$true;AckManifestPagesComplete=$true;AckOperation='FINALIZE';AckInventoryStatus='VALIDATED';AckSessionId=$session;AckExpectedCount=1;AckAcceptedCount=1;AckInventoryFileCount=1;AckRejectedCount=0;AckManifestFileCount=1;AckManifestTotalBytes=10;AckLocalContentFileCount=1;AckLocalContentTotalBytes=10;AckLocalContentDigestSha256=('e'*64);AckMembershipReceiptSha256=('f'*64);AckMembershipReceiptSchema='fly_terminal_transfer_membership_receipt_v1';AckMembershipContentHashStatus='LOCAL_COMPLETE_FRESH_RECOMPUTED';AckMembershipReceiptName=("terminal-transfer-membership-$($m.inventory_generation_id)-$session.json");InventoryGenerationId=$m.inventory_generation_id;InventorySha256=$m.inventory_sha256;CollectionEpochId=$m.collection_epoch_id;TileRegistrySignature=$m.tile_registry_signature;CanonicalSourceRevision=$m.source_git_rev;SourceRevision=$m.source_git_rev}
}
$run = {
 param($m,$n)
 $script:attempts++
 if (('__CASE__' -eq 'success' -and $n -eq 2) -or '__CASE__' -in @('ack','incomplete_terminal','wrong_terminal_generation','missing_terminal_session','wrong_terminal_session')) {
  $terminal=& $script:newTerminal $m
  if('__CASE__' -eq 'ack') {$terminal.AckAccepted=$false}
  if('__CASE__' -eq 'incomplete_terminal') {$terminal.PSObject.Properties.Remove('AckFinalized')}
  if('__CASE__' -eq 'wrong_terminal_generation') {$terminal.InventoryGenerationId=('b'*64)}
  if('__CASE__' -eq 'missing_terminal_session') {$terminal.PSObject.Properties.Remove('AckSessionId')}
  if('__CASE__' -eq 'wrong_terminal_session') {$terminal.AckSessionId=('c'*32)}
  return @{Success=$true;Result=$terminal}
 }
 $count=if('__CASE__' -eq 'stalled'){1}else{$n}
 return @{Success=$false;Receipt=@{failureCode=$(if('__CASE__' -eq 'integrity'){'HASH_MISMATCH'}else{'BUNDLE_TRANSFER_DEADLINE'});ok=$false;inProgress=$false;ackPending=$true;completionAuthority='NONE_TRANSFER_PROGRESS_ONLY';inventoryGenerationId=$(if('__CASE__' -eq 'wrongreceipt'){'b'*64}else{'a'*64});inventorySha256=('a'*64);collectionEpochId='epoch';deployedRevision='rev';tileRegistrySignature='tile';fileIndex=$count;verifiedPayloadBytes=($count*10)}}
}
try { $null=Invoke-FlyGenerationResume -Identity $identity -ReadManifest $read -RunAttempt $run; Write-Output "OK:$script:attempts" }
catch {Write-Output "$($_.Exception.Message):$script:attempts"}
'''.replace('__SCRIPT__', str(SCRIPT)).replace('__CASE__',case).replace("'rev'", "('1'*40)").replace("deployedRevision=", "sourceRevision=('1'*40);deployedRevision=")
    result = subprocess.run([str(PWSH),'-NoProfile','-NonInteractive','-Command',code],capture_output=True,text=True,timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected, result.stdout + result.stderr


@pytest.mark.parametrize('fail', [False, True])
def test_wrapper_uses_actual_success_return_shape_and_holds_mirror_lease(tmp_path, fail):
    import shutil
    shutil.copyfile(SCRIPT, tmp_path / SCRIPT.name)
    (tmp_path/'fly-canonical-lock.ps1').write_text("function Get-CanonicalFlyBotUrl { 'https://doxed-btc-bot.fly.dev' }")
    (tmp_path/'home-bot-vault-env.ps1').write_text('')
    (tmp_path/'fly-sync-bundles.ps1').write_text('function Assert-FlyBundleUnlinkedPath {param($Path)}')
    (tmp_path/'sync-fly-bot-data.ps1').write_text(r'''
param($SourceUrl,$AdminToken,$TargetDir,$InitialManifest,$ProgressHeartbeatFile,$MirroredSourceRevision)
$blocked=$false
try {$h=[IO.File]::Open((Join-Path $TargetDir '.fly-mirror-generation.lease'),'OpenOrCreate','ReadWrite','None');$h.Dispose()}
catch {$blocked=$true}
if(-not $blocked){throw 'LEASE_NOT_HELD'}
if('__FAIL__' -eq 'True') {
 @{failureCode='HASH_MISMATCH'} | ConvertTo-Json | Set-Content -LiteralPath $ProgressHeartbeatFile
 throw 'SYNTHETIC_TRANSFER_FAILURE'
}
# Normal sync return protocol must carry a complete, identity-bound terminal ACK.
$session=('d'*32)
[pscustomobject]@{SourceRevision=$InitialManifest.source_git_rev;CanonicalSourceRevision=$InitialManifest.source_git_rev;InventoryGenerationId=$InitialManifest.inventory_generation_id;InventorySha256=$InitialManifest.inventory_sha256;CollectionEpochId=$InitialManifest.collection_epoch_id;TileRegistrySignature=$InitialManifest.tile_registry_signature;AckAccepted=$true;AckFinalized=$true;AckCoverageComplete=$true;AckManifestPagesComplete=$true;AckOperation='FINALIZE';AckInventoryStatus='VALIDATED';AckSessionId=$session;AckExpectedCount=1;AckAcceptedCount=1;AckInventoryFileCount=1;AckRejectedCount=0;AckManifestFileCount=1;AckManifestTotalBytes=10;AckLocalContentFileCount=1;AckLocalContentTotalBytes=10;AckLocalContentDigestSha256=('e'*64);AckMembershipReceiptSha256=('f'*64);AckMembershipReceiptSchema='fly_terminal_transfer_membership_receipt_v1';AckMembershipContentHashStatus='LOCAL_COMPLETE_FRESH_RECOMPUTED';AckMembershipReceiptName=("terminal-transfer-membership-$($InitialManifest.inventory_generation_id)-$session.json");Files=1;Bytes=10}
'''.replace('__FAIL__', str(fail)))
    (tmp_path/'mirror').mkdir()
    code = r'''
. '__SCRIPT__'
$identity=[pscustomobject]@{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev='rev';collection_epoch_id='epoch';tile_registry_signature='tile'}
function Invoke-RestMethod {
 param($Uri,$Headers,$MaximumRedirection,$TimeoutSec,$ErrorAction)
 if($Uri -notlike '*generation_id=*' -or $MaximumRedirection -ne 0){throw 'REQUEST_NOT_PINNED'}
 [pscustomobject]@{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev='rev';collection_epoch_id='epoch';tile_registry_signature='tile';inventory_status='CURRENT';inventory_authoritative=$true;inventory_ack_eligible=$true;file_count=1;total_bytes=10}
}
try {
$r=Start-FlyGenerationResume -Identity $identity -TargetDir '__ROOT__/mirror' -ReceiptDirectory '__ROOT__/receipts' -AdminToken 'synthetic'
if($r.AckAccepted -ne $true){throw 'NO_ACK'}
Write-Output 'PASS'
} catch { Write-Output $_.Exception.Message }
'''.replace('__SCRIPT__',str(tmp_path/SCRIPT.name)).replace('__ROOT__',str(tmp_path)).replace("'rev'", "('1'*40)")
    result=subprocess.run([str(PWSH),'-NoProfile','-NonInteractive','-Command',code],capture_output=True,text=True,timeout=30)
    expected = 'RESUME_NON_DEADLINE_FAILURE' if fail else 'PASS'
    assert result.returncode==0 and result.stdout.strip()==expected,result.stdout+result.stderr


@pytest.mark.parametrize('observed,expected', [('9e623f953546','PASS'),('000000000000','RESUME_PROGRESS_IDENTITY_INVALID')])
def test_actual_failed_heartbeat_short_deployed_revision_shape(observed,expected):
    # Shape/values from batch-sync-8d2ab54-progress.json; no credentials.
    code=r'''
. '__SCRIPT__'
$identity=[pscustomobject]@{inventory_generation_id=('a'*64);inventory_sha256=('a'*64);source_git_rev='9e623f95354666c1a8eb11e5c0d3094c631f36d5';collection_epoch_id='epoch-5a856f6f873c84fee7cefb2a';tile_registry_signature='tile'}
$read={param($g) [pscustomobject]@{inventory_generation_id=$g;inventory_sha256=$g;source_git_rev='9e623f953546';collection_epoch_id='epoch-5a856f6f873c84fee7cefb2a';tile_registry_signature='tile';inventory_status='CURRENT';inventory_authoritative=$true;inventory_ack_eligible=$true;file_count=1;total_bytes=10}}
$run={param($m,$n)
 if($n -eq 2){$session=('d'*32);return @{Success=$true;Result=@{AckAccepted=$true;AckFinalized=$true;AckCoverageComplete=$true;AckManifestPagesComplete=$true;AckOperation='FINALIZE';AckInventoryStatus='VALIDATED';AckSessionId=$session;AckExpectedCount=1;AckAcceptedCount=1;AckInventoryFileCount=1;AckRejectedCount=0;AckManifestFileCount=1;AckManifestTotalBytes=10;AckLocalContentFileCount=1;AckLocalContentTotalBytes=10;AckLocalContentDigestSha256=('e'*64);AckMembershipReceiptSha256=('f'*64);AckMembershipReceiptSchema='fly_terminal_transfer_membership_receipt_v1';AckMembershipContentHashStatus='LOCAL_COMPLETE_FRESH_RECOMPUTED';AckMembershipReceiptName=("terminal-transfer-membership-$($m.inventory_generation_id)-$session.json");InventoryGenerationId=$m.inventory_generation_id;InventorySha256=$m.inventory_sha256;CollectionEpochId=$m.collection_epoch_id;TileRegistrySignature=$m.tile_registry_signature;CanonicalSourceRevision='9e623f95354666c1a8eb11e5c0d3094c631f36d5';SourceRevision='9e623f953546'}}}
 return @{Success=$false;Receipt=@{ok=$false;inProgress=$false;phase='bundle_failed';sourceRevision='9e623f95354666c1a8eb11e5c0d3094c631f36d5';observedSourceRevision='__OBS__';deployedRevision='__OBS__';failureCode='BUNDLE_INDEX_PREPARATION_DEADLINE';ackPending=$true;completionAuthority='NONE_TRANSFER_PROGRESS_ONLY';inventoryGenerationId=('a'*64);inventorySha256=('a'*64);collectionEpochId='epoch-5a856f6f873c84fee7cefb2a';tileRegistrySignature='tile';fileIndex=6982;verifiedPayloadBytes=58569323;reusedLocalBytes=56062082;newlyTransferredPayloadBytes=2507241}}
}
try {$null=Invoke-FlyGenerationResume $identity $read $run;Write-Output 'PASS'}catch{Write-Output $_.Exception.Message}
'''.replace('__SCRIPT__',str(SCRIPT)).replace('__OBS__',observed)
    result=subprocess.run([str(PWSH),'-NoProfile','-NonInteractive','-Command',code],capture_output=True,text=True,timeout=30)
    assert result.returncode==0 and result.stdout.strip()==expected,result.stdout+result.stderr

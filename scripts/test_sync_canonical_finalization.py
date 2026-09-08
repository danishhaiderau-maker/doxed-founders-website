from pathlib import Path
import subprocess
import shutil
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
Set-Content -LiteralPath $ProgressHeartbeatFile -Value 'INCOMPLETE'
function Write-SyncProgressHeartbeat {{
 param($Phase,$FileIndex,$FileCount,$FileBytes,$RemoteBytes,[switch]$Completed,$ReceiptTarget)
 if(-not $Completed -or $ReceiptTarget -eq $ProgressHeartbeatFile){{throw 'PRIVATE_REQUIRED'}}
 Set-Content -LiteralPath $ReceiptTarget -Value 'COMPLETE'
}}
function python {{
 if((Get-Content -LiteralPath $ProgressHeartbeatFile -Raw).Trim() -ne 'INCOMPLETE'){{throw 'EARLY_PUBLICATION'}}
 if((Get-Content -LiteralPath $canonicalCandidate -Raw).Trim() -ne 'COMPLETE'){{throw 'CANDIDATE_MISSING'}}
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
Set-Content -LiteralPath $ProgressHeartbeatFile -Value 'INCOMPLETE'
function Start-Sleep {{param($Milliseconds)}}
function Write-SyncProgressHeartbeat {{
 param($Phase,$FileIndex,$FileCount,$FileBytes,$RemoteBytes,[switch]$Completed,$ReceiptTarget)
 Set-Content -LiteralPath $ReceiptTarget -Value 'COMPLETE'
}}
function python {{
 $global:LASTEXITCODE=0
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

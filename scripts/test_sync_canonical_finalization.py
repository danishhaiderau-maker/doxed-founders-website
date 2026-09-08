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
    harness = f"""
$ErrorActionPreference='Stop'
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

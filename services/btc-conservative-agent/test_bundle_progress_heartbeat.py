import json
import subprocess
import pytest
from test_fly_sync_bundle_powershell import ROOT, PWSH


@pytest.mark.skipif(not PWSH.exists(), reason='PowerShell unavailable')
@pytest.mark.parametrize('failed', [False, True])
def test_bundle_heartbeat_has_no_completion_authority(tmp_path, failed):
    target = tmp_path / 'heartbeat.json'
    script = f"""
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{ROOT.as_posix()}/scripts/sync-fly-bot-data.ps1',[ref]$tokens,[ref]$errors)
if ($errors.Count) {{throw 'PARSE_FAILED'}}
$fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Write-SyncProgressHeartbeat'}},$true)
Invoke-Expression $fn.Extent.Text
$ProgressHeartbeatFile='{target.as_posix()}'
$SourceUrl='https://doxed-btc-bot.fly.dev';$MirroredSourceRevision='abc'
$manifest=[pscustomobject]@{{source_git_rev='abc';inventory_generation_id='current-generation';collection_epoch_id='current-epoch';tile_registry_signature='tile'}}
Write-SyncProgressHeartbeat -Phase 'bundle_verified' -FileIndex 3 -FileCount 9 -FileBytes 100 -BundleProgress ([pscustomobject]@{{VerifiedBytes=100;ReusedBytes=40;Failed=${str(failed).lower()};FailureCode='PACKAGE_RETRY_EXHAUSTED'}})
Get-Content -LiteralPath $ProgressHeartbeatFile -Raw
"""
    result = subprocess.run([str(PWSH), '-NoProfile', '-Command', script], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    row = json.loads(result.stdout)
    assert row['inProgress'] is (not failed) and row['ackPending'] is True
    assert row['ok'] is (not failed)
    assert row['fileIndex'] == 3
    assert row['completionAuthority'] == 'NONE_TRANSFER_PROGRESS_ONLY'
    assert row['inventoryGenerationId'] == 'current-generation'
    assert row['collectionEpochId'] == 'current-epoch'
    assert row['verifiedPayloadBytes'] == 100 and row['reusedLocalBytes'] == 40
    assert row['newlyTransferredPayloadBytes'] == 60 and row['networkBytes'] is None
    assert row['revisionParityScope'] == 'REQUEST_VS_MANIFEST_NOT_MIRROR_COMPLETION'

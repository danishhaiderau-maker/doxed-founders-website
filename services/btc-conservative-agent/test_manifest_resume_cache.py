import subprocess
from pathlib import Path
import pytest

PWSH = Path('C:/Users/danis/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe')
SCRIPT = Path(__file__).resolve().parents[2] / 'scripts/fly-sync-manifest-cache.ps1'


@pytest.mark.parametrize('case,expected', [
    ('reuse', 'OK:1:7'), ('revision', 'MANIFEST_CACHE_IDENTITY_CHANGED'),
    ('epoch', 'MANIFEST_CACHE_IDENTITY_CHANGED'), ('sha', 'MANIFEST_CACHE_IDENTITY_CHANGED'),
    ('authority', 'MANIFEST_CACHE_AUTHORITY_UNAVAILABLE'),
    ('incomplete', 'MANIFEST_CACHE_INCOMPLETE')])
def test_complete_cache_fenced_and_lease_free(case, expected):
    code = r'''
. '__SCRIPT__'
$m=[pscustomobject]@{schema='fly_runtime_incremental_sync_v1';inventory_generation_id=('a'*64);inventory_sha256=('a'*64);inventory_generated_at='now';source_git_rev=('1'*40);collection_epoch_id='epoch';tile_registry_signature='tile';file_count=1;total_bytes=7;manifest_page_count=165;manifest_page_sha256=('b'*64);manifest_page_index=0;manifest_page_cursor='';inventory_status='CURRENT';inventory_authoritative=$true;inventory_ack_eligible=$true}
$cache=@{}; $script:expanded=0
$expand={param($first) $script:expanded++; $c=$first|ConvertTo-Json|ConvertFrom-Json; $c|Add-Member files @([pscustomobject]@{path='db';size=7});$c|Add-Member manifest_pages_complete ('__CASE__' -ne 'incomplete');$c|Add-Member manifest_pages_aggregated 165;return $c}
try {
 $first=Get-FlyCachedCompleteManifest $m $cache $expand
 $first.files[0].size=99
 $first.files[0]|Add-Member snapshot_lease_id 'must-not-reuse'
 switch('__CASE__') {
 'revision' {$m.source_git_rev='other'}
 'epoch' {$m.collection_epoch_id='other'}
 'sha' {$m.inventory_sha256='other'}
 'authority' {$m.inventory_authoritative=$false}
 }
 $second=Get-FlyCachedCompleteManifest $m $cache $expand
 if($second.files[0].snapshot_lease_id){throw 'LEASE_REUSED'}
 Write-Output "OK:$script:expanded`:$($second.files[0].size)"
} catch {Write-Output $_.Exception.Message}
'''.replace('__SCRIPT__', str(SCRIPT)).replace('__CASE__', case)
    result = subprocess.run([str(PWSH), '-NoProfile', '-NonInteractive', '-Command', code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected, result.stdout + result.stderr

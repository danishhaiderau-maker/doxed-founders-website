"""Execute local pre-ACK verification; never contact Fly or acknowledge data."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_original_components_cannot_enter_snapshot_or_refresh_fallback():
    source = (Path(__file__).resolve().parent / 'sync-fly-bot-data.ps1').read_text(encoding='utf-8-sig')
    start = source.index('  $forensicOriginal =')
    assert source.index('FORENSIC_SNAPSHOT_SUBSTITUTION_FORBIDDEN', start) < source.index('Set-SqliteSnapshotLease -Row $row', start)
    refresh = source.index('      if ($refreshGeneration) {', start)
    assert source.index('FORENSIC_GENERATION_CHANGED', refresh) < source.index('$generationRefreshCount += 1', refresh)
    assert source.index('Assert-FlyForensicPayload -Row $row -Path $candidate') < source.index('Publish-MirrorCandidate -Candidate $candidate')
    assert source.index('Assert-FlyForensicPayload -Row $row -Path $local') < source.index('if (-not ($sameGeneration -and $localSize -eq $remoteSize))')


@pytest.mark.parametrize('case', ['valid', 'tampered', 'missing_component', 'missing_binding'])
def test_local_group_verification(tmp_path, case):
    scripts = Path(__file__).resolve().parent
    parent = 'v3/lifecycle_bundle_index/recovery-quarantine/' + 'a' * 16
    rows = []
    names = ['lifecycle_index.sqlite3', 'lifecycle_index.sqlite3-wal']
    for name in names:
        relative = parent + '/' + name
        payload = b'original forensic bytes'
        local = tmp_path / relative
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(payload)
        rows.append({'path': relative, 'consistency_mode': 'strict_generation_v1',
                     'forensic_component': {'path': relative,
                         'schema': 'quarantine_original_component_binding_v1',
                         'expected_sha256': hashlib.sha256(payload).hexdigest(),
                         'receipt_file_sha256': 'b' * 64,
                         'size': len(payload), 'required_components': names}})
    expected = ''
    if case == 'missing_binding':
        for row in rows:
            del row['forensic_component']
        expected = 'FORENSIC_BINDING_MISSING'
    if case == 'tampered':
        local.write_bytes(b'changed! forensic bytes')
        expected = 'FORENSIC_PAYLOAD_MISMATCH'
    elif case == 'missing_component':
        rows.pop()
        expected = 'FORENSIC_GROUP_INCOMPLETE'
    manifest = tmp_path / 'rows.json'
    manifest.write_text(json.dumps(rows))
    def quote(path):
        return "'" + str(path).replace("'", "''") + "'"
    command = (
        "$ErrorActionPreference='Stop'; . " + quote(scripts / 'fly-sync-bundles.ps1')
        + '; . ' + quote(scripts / 'fly-forensic-group-verify.ps1')
        + '; $rows=Get-Content -Raw -LiteralPath ' + quote(manifest)
        + ' | ConvertFrom-Json; try { Assert-FlyForensicGroups -Rows @($rows) -Root '
        + quote(tmp_path) + "; 'VERIFIED' } catch { Write-Output $_.Exception.Message; exit 7 }")
    result = subprocess.run([shutil.which('pwsh') or shutil.which('powershell'),
                             '-NoProfile', '-Command', command],
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == (7 if expected else 0), result.stdout + result.stderr
    assert (expected or 'VERIFIED') in result.stdout

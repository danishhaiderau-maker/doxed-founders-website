"""Parse/contract only: never execute the operational download wrapper."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parent
WRAPPER = ROOT / 'start-fly-batch-sync.ps1'


def test_wrapper_powershell_parse():
    shell = shutil.which('pwsh') or shutil.which('powershell')
    assert shell
    command = "$tokens=$null; $errors=$null; [void][System.Management.Automation.Language.Parser]::ParseFile('" + str(WRAPPER).replace("'", "''") + "',[ref]$tokens,[ref]$errors); if($errors.Count){$errors | ForEach-Object Message; exit 1}"
    result = subprocess.run([shell, '-NoProfile', '-Command', command], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_scoped_optin_delegates_existing_auth_and_propagates_failure():
    source = WRAPPER.read_text()
    assert source.index("'1', 'Process'") < source.index("& (Join-Path") < source.index('finally {')
    assert "'FLY_SYNC_TRANSPORT_BUNDLES', $batchPreviousOptIn, 'Process'" in source
    assert source.count("& (Join-Path") == 1
    assert 'catch' not in source and '-AdminToken' not in source
    assert "sync-fly-bot-data.ps1" in source
    canonical = (ROOT/'sync-fly-bot-data.ps1').read_text(encoding='utf-8-sig')
    assert 'Import-CanonicalBotAdminToken' in canonical
    assert "$env:FLY_SYNC_TRANSPORT_BUNDLES -eq '1'" in canonical
    for parameter in ('SourceUrl','TargetDir','MirroredSourceRevision','InitialManifest','ProgressHeartbeatFile','MaxLocalMirrorGiB'):
        assert '-'+parameter+' $'+parameter in source


@pytest.mark.parametrize('fails', [False, True])
def test_isolated_invocation_restores_environment(tmp_path, fails):
    # Execute only an isolated copy with a stub sibling, never the downloader.
    wrapper = tmp_path / WRAPPER.name
    wrapper.write_bytes(WRAPPER.read_bytes())
    (tmp_path / 'sync-fly-bot-data.ps1').write_text(
        "param($SourceUrl,$TargetDir,$MirroredSourceRevision,$InitialManifest,$ProgressHeartbeatFile,$MaxLocalMirrorGiB)\n"
        "if($env:FLY_SYNC_TRANSPORT_BUNDLES -ne '1'){throw 'OPTIN_MISSING'}\n"
        "if($MirroredSourceRevision -ne 'test-revision'){throw 'PARAM_MISSING'}\n"
        + ("throw 'EXPECTED_STUB_FAILURE'\n" if fails else "'STUB_SUCCESS'\n"))
    script = ("$env:FLY_SYNC_TRANSPORT_BUNDLES='previous'; $caught=$false; try { & '"
              + str(wrapper).replace("'", "''") + "' -MirroredSourceRevision test-revision } "
              "catch { if($_.Exception.Message -ne 'EXPECTED_STUB_FAILURE'){throw}; $caught=$true }; "
              "if($env:FLY_SYNC_TRANSPORT_BUNDLES -ne 'previous'){throw 'RESTORE_FAILED'}; "
              + ("if(-not $caught){throw 'FAILURE_SWALLOWED'}" if fails else "if($caught){throw 'UNEXPECTED_FAILURE'}"))
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr

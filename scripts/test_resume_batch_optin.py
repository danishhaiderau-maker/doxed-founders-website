"""Execute actual Start with isolated stub dependencies; never contact Fly."""
from pathlib import Path
import shutil
import subprocess
import pytest


@pytest.mark.parametrize('fails', [False, True])
@pytest.mark.parametrize('prior', ['', 'previous'])
def test_actual_start_scopes_batch_and_releases_lease(tmp_path, fails, prior):
    script = Path(__file__).with_name('fly-sync-generation-resume.ps1')
    (tmp_path / script.name).write_bytes(script.read_bytes())
    (tmp_path / 'fly-canonical-lock.ps1').write_text("function Get-CanonicalFlyBotUrl { 'https://invalid.test' }")
    (tmp_path / 'home-bot-vault-env.ps1').write_text('')
    (tmp_path / 'fly-sync-bundles.ps1').write_text('function Assert-FlyBundleUnlinkedPath { param($Path) }')
    (tmp_path / 'sync-fly-bot-data.ps1').write_text(
        'param($SourceUrl,$AdminToken,$TargetDir,$InitialManifest,$ProgressHeartbeatFile,$MirroredSourceRevision)\n'
        "if($env:FLY_SYNC_TRANSPORT_BUNDLES -ne '1'){throw 'OPTIN_MISSING'}\n'STUB_OK'")
    target = tmp_path / 'target'
    target.mkdir()
    quoted = str(tmp_path).replace("'", "''")
    harness = f"""
$ErrorActionPreference='Stop'
. '{quoted}/fly-sync-generation-resume.ps1'
function Invoke-FlyGenerationResume {{
 param($Identity,$ReadManifest,$RunAttempt)
 $a=& $RunAttempt @{{}} 1
 $b=& $RunAttempt @{{}} 2
 if(-not $a.Success -or -not $b.Success -or $a.Result -ne 'STUB_OK' -or $b.Result -ne 'STUB_OK'){{throw 'STUB_NOT_RUN'}}
 {'throw "EXPECTED_FAILURE"' if fails else "'OK'"}
}}
[Environment]::SetEnvironmentVariable('FLY_SYNC_TRANSPORT_BUNDLES','{prior}','Process')
$caught=$false
try {{ Start-FlyGenerationResume -Identity @{{source_git_rev='test'}} -TargetDir '{quoted}/target' -ReceiptDirectory '{quoted}/receipts' -AdminToken 'synthetic' }}
catch {{if($_.Exception.Message -ne 'EXPECTED_FAILURE'){{throw}}; $caught=$true}}
if($caught -ne ${str(fails).lower()}){{throw 'FAILURE_PROPAGATION'}}
if([string]$env:FLY_SYNC_TRANSPORT_BUNDLES -cne '{prior}'){{throw 'ENV_NOT_RESTORED'}}
$lease=[IO.File]::Open('{quoted}/target/.fly-mirror-generation.lease',[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
$lease.Dispose()
"""
    shell = shutil.which('pwsh') or shutil.which('powershell')
    result = subprocess.run([shell, '-NoProfile', '-Command', harness], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr

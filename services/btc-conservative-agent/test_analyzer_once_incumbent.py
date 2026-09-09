"""Execute the actual PowerShell discovery/refusal blocks without runtime effects."""
from pathlib import Path
import shutil
import subprocess

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / 'scripts/start-home-analyzer.ps1'


def run_case(*, once, commands):
    ps = shutil.which('pwsh') or shutil.which('powershell')
    if not ps:
        pytest.skip('PowerShell unavailable')
    escaped_path = str(LAUNCHER).replace("'", "''")
    command_rows = ','.join("'" + c.replace("'", "''") + "'" for c in commands)
    script = f'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{escaped_path}',[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'PARSE_FAILURE' }}
$discovery=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-CanonicalAnalyzerEnginePids'}},$true)
$guard=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.IfStatementAst] -and $n.Clauses[0].Item1.Extent.Text -eq '$Once -and $discoveredEnginePids.Count -gt 0'}},$true)
if (-not $guard) {{ throw 'MISSING_ONCE_GUARD' }}
$script:commands=@({command_rows})
function Get-Process {{ param($Name,$ErrorAction) for($i=0;$i -lt $script:commands.Count;$i++) {{ [pscustomobject]@{{Id=100+$i}} }} }}
function Get-ProcessCommandLineFast {{ param($ProcessId) return $script:commands[$ProcessId-100] }}
function Stop-Process {{ throw 'MUST_NOT_STOP' }}
function Start-Process {{ throw 'MUST_NOT_START' }}
function Remove-Item {{ param($LiteralPath,[switch]$Force,$ErrorAction) }}
Invoke-Expression $discovery.Extent.Text
$Once=${str(once).lower()};$AnalyzerPort=9001;$lockHandle=$null;$lockFile='synthetic-lock'
$discoveredEnginePids=@(Get-CanonicalAnalyzerEnginePids $AnalyzerPort)
Invoke-Expression $guard.Extent.Text
Write-Output 'PASSED_GUARD_WITHOUT_PROCESS_ACTION'
'''
    return subprocess.run([ps, '-NoProfile','-NonInteractive','-Command',script],capture_output=True,text=True,timeout=25)


@pytest.mark.parametrize('commands', [
    ['python analyzer_research_engine_v62.py --owner-port=9001 --source-revision=old'],
    ['python analyzer_research_engine_v62.py'],
    ['python analyzer_research_engine_v62.py --owner-port=9001','python analyzer_research_engine_v62.py'],
])
def test_once_refuses_owned_or_possible_legacy_incumbent(commands):
    result=run_case(once=True,commands=commands)
    assert result.returncode==2, result.stdout+result.stderr
    assert 'ONCE_ANALYZER_INCUMBENT_EXISTS' in result.stdout
    assert 'PASSED_GUARD' not in result.stdout
    assert 'MUST_NOT_' not in result.stdout+result.stderr


@pytest.mark.parametrize('once,commands', [(True,[]),(False,['python analyzer_research_engine_v62.py --owner-port=9001'])])
def test_new_guard_preserves_unowned_once_and_normal_mode(once,commands):
    result=run_case(once=once,commands=commands)
    assert result.returncode==0, result.stdout+result.stderr
    assert 'PASSED_GUARD_WITHOUT_PROCESS_ACTION' in result.stdout


def test_refusal_is_before_restart_and_listener_cleanup():
    source=LAUNCHER.read_text(encoding='utf-8-sig')
    guard=source.index('if ($Once -and $discoveredEnginePids.Count -gt 0)')
    assert guard < source.index('if ($Restart -and $discoveredEnginePids.Count')
    assert guard < source.index('Stop-ListenPortFast $AnalyzerPort')

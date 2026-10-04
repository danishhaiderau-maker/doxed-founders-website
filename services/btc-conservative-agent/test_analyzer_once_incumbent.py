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


def run_stale_case(procs, revision='newrev'):
    """procs: list of (pid, commandline, parent_alive, cpu_before, cpu_after)."""
    ps = shutil.which('pwsh') or shutil.which('powershell')
    if not ps:
        pytest.skip('PowerShell unavailable')
    escaped_path = str(LAUNCHER).replace("'", "''")
    rows = ','.join(
        "@{{Id={0};Cmd='{1}';Parent={2};ParentAlive=${3};Cpu0={4};Cpu1={5}}}".format(
            pid, cmd.replace("'", "''"), 9000 + pid, str(alive).lower(), c0, c1)
        for pid, cmd, alive, c0, c1 in procs)
    script = f'''
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{escaped_path}',[ref]$tokens,[ref]$errors)
if ($errors.Count) {{ throw 'PARSE_FAILURE' }}
$fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Get-StaleOnceIncumbentPids'}},$true)
if (-not $fn) {{ throw 'MISSING_STALE_RECONCILER' }}
$script:procs=@({rows})
$script:slept=$false
function Get-CimInstance {{ param($ClassName,$Filter,$ErrorAction) $id=[int]($Filter -replace '\\D',''); $p=$script:procs | Where-Object {{ $_.Id -eq $id }}; if ($p) {{ [pscustomobject]@{{ProcessId=$p.Id;CommandLine=$p.Cmd;ParentProcessId=$p.Parent}} }} }}
function Get-Process {{ param($Id,$ErrorAction) $p=$script:procs | Where-Object {{ $_.Id -eq $Id -or ($_.Parent -eq $Id -and $_.ParentAlive) }} | Select-Object -First 1; if (-not $p) {{ return $null }}; if ($p.Id -ne $Id) {{ return [pscustomobject]@{{Id=$Id;CPU=0}} }}; [pscustomobject]@{{Id=$p.Id;CPU=$(if ($script:slept) {{ $p.Cpu1 }} else {{ $p.Cpu0 }})}} }}
function Start-Sleep {{ param($Seconds) $script:slept=$true }}
function Stop-Process {{ throw 'MUST_NOT_STOP' }}
Invoke-Expression $fn.Extent.Text
$stale=@(Get-StaleOnceIncumbentPids @($script:procs | ForEach-Object {{ $_.Id }}) 9001 '{revision}')
Write-Output ('STALE=' + (($stale | ForEach-Object {{ $_.Id }}) -join ','))
'''
    result = subprocess.run([ps, '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, text=True, timeout=25)
    assert result.returncode == 0, result.stdout + result.stderr
    return [line for line in result.stdout.splitlines() if line.startswith('STALE=')][-1][6:]


def test_stale_once_incumbent_requires_orphaned_idle_old_revision_owned_engine():
    owned_old = 'python analyzer_research_engine_v62.py --owner-port=9001 --source-revision=c9c49fc9b94a560211ec8e87c3fa43c45eb33f00'
    assert run_stale_case([(11, owned_old, False, 5.0, 5.0)]) == '11'
    # live parent, busy, same revision, legacy argv: all remain incumbents.
    assert run_stale_case([(12, owned_old, True, 5.0, 5.0)]) == ''
    assert run_stale_case([(13, owned_old, False, 5.0, 9.0)]) == ''
    assert run_stale_case([(14, owned_old, False, 5.0, 5.0)], revision='c9c49fc9b94a560211ec8e87c3fa43c45eb33f00') == ''
    assert run_stale_case([(15, 'python analyzer_research_engine_v62.py', False, 5.0, 5.0)]) == ''


def test_stale_reconcile_runs_before_the_once_refusal():
    source = LAUNCHER.read_text(encoding='utf-8-sig')
    reconcile = source.index('$staleIncumbents = @(Get-StaleOnceIncumbentPids')
    assert reconcile < source.index('if ($Once -and $discoveredEnginePids.Count -gt 0)')
    assert 'analyzer-incumbent-reconcile.receipts.jsonl' in source
    assert "$env:ANALYZER_ONCE_STALE_RECONCILE -ne '0'" in source

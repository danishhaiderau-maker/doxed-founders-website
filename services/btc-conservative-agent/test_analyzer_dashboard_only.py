import subprocess
import base64
import pytest
from test_fly_sync_bundle_powershell import ROOT, PWSH


@pytest.mark.skipif(not PWSH.exists(), reason='PowerShell unavailable')
@pytest.mark.parametrize('defect', ['', 'foreign', 'public', 'engine', 'config', 'changed'])
def test_dashboard_refresh_preserves_engine(tmp_path, defect):
    script=f"""
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('{ROOT.as_posix()}/scripts/start-home-analyzer.ps1',[ref]$tokens,[ref]$errors)
if ($errors.Count) {{throw 'PARSE'}}
$fn=$ast.Find({{param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Restart-OwnedAnalyzerDashboard'}},$true)
Invoke-Expression $fn.Extent.Text
$repoRoot='{tmp_path.as_posix()}';$agentDir=$repoRoot;$AnalyzerPort=9001;$scenarioLaunch=@{{}}
$script:stopped=$false;$script:starts=0;$script:writes=0;$script:reads=0
function Get-Content {{param($LiteralPath,[switch]$Raw,$ErrorAction) if ($LiteralPath -notlike '*.home-analyzer-dashboard.pid') {{throw 'ENGINE_PID_READ'}}; '42'}}
function Get-NetTCPConnection {{param($LocalPort,$State,$ErrorAction) if (-not $script:stopped) {{[pscustomobject]@{{OwningProcess=$(if ('{defect}' -eq 'foreign') {{99}} else {{42}});LocalAddress=$(if ('{defect}' -eq 'public') {{'0.0.0.0'}} else {{'127.0.0.1'}})}}}}}}
function Get-ProcessCommandLineFast {{param($ProcessId) if ('{defect}' -eq 'engine') {{'python analyzer_research_engine_v62.py'}} else {{'python research_dashboard.py --standalone'}}}}
function Get-Process {{param($Id,$ErrorAction) $script:reads++; [pscustomobject]@{{StartTime=$(if ('{defect}' -eq 'changed' -and $script:reads -gt 1) {{2}} else {{1}})}}}}
function Assert-AnalyzerScenarioLaunchConfig {{param($Receipt) if ('{defect}' -eq 'config') {{throw 'CONFIG_INVALID'}}}}
function Stop-Process {{param($Id,[switch]$Force,$ErrorAction) if ($Id -ne 42) {{throw 'ENGINE_STOP'}};$script:stopped=$true}}
function Wait-Process {{param($Id,$Timeout,$ErrorAction)}}
function Start-Process {{param($FilePath,$ArgumentList,$WorkingDirectory,$WindowStyle,[switch]$PassThru) if (($ArgumentList -join ' ') -ne 'research_dashboard.py --standalone' -or $WindowStyle -ne 'Hidden') {{throw 'ENGINE_START'}};$script:starts++;[pscustomobject]@{{Id=43}}}}
function Set-Content {{param($LiteralPath,$Value,[switch]$NoNewline,$Encoding) if ($LiteralPath -notlike '*.home-analyzer-dashboard.pid' -or $Value -ne '43') {{throw 'ENGINE_PID_WRITE'}};$script:writes++}}
$caught=$false
try {{Restart-OwnedAnalyzerDashboard}} catch {{$caught=$true;if (-not '{defect}') {{throw}}}}
if ('{defect}' -and -not $caught) {{throw 'SHOULD_FAIL'}}
if ('{defect}') {{if ($script:stopped -or $script:starts -or $script:writes) {{throw 'UNSAFE_MUTATION'}}}} else {{if (-not $script:stopped -or $script:starts -ne 1 -or $script:writes -ne 1) {{throw 'REFRESH_MISSING'}}}}
exit 0
"""
    result=subprocess.run([str(PWSH),'-NoProfile','-EncodedCommand',base64.b64encode(script.encode('utf-16-le')).decode()],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr


def test_dashboard_branch_after_scrub_provenance_before_engine_operations():
    source=(ROOT/'scripts/start-home-analyzer.ps1').read_text()
    branch=source.index('if ($DashboardOnly) {')
    assert source.index('foreach ($secretName') < branch
    assert source.index('if ($dirtyAnalyzerSources.Count -gt 0)') < branch
    assert source.index('$scenarioLaunch = Get-AnalyzerScenarioLaunchConfig') < branch
    assert branch < source.index('$discoveredEnginePids =')
    block=source[source.index('function Restart-OwnedAnalyzerDashboard'):branch]
    assert '.home-analyzer.pid' not in block

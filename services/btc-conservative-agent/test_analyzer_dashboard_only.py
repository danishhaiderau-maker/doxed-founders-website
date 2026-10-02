import subprocess
import base64
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
PWSH = Path("C:/Users/danis/.cache/codex-runtimes/codex-primary-runtime/dependencies/native/powershell/pwsh.exe")


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


def test_dashboard_entry_pins_arrow_to_the_system_allocator():
    import sys

    shim = (ROOT / "services" / "btc-conservative-agent" / "research_dashboard.py").read_text(encoding="utf-8")
    main = shim.split('if __name__ == "__main__":', 1)[1]
    pin = 'os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")'
    assert pin in main and main.index(pin) < main.index("runpy.run_path(")
    assert "import pyarrow" not in shim and "import pandas" not in shim
    probe = (pin.replace("os.environ", "__import__('os').environ")
             + "; import pyarrow as pa; print(pa.default_memory_pool().backend_name)")
    env = {k: v for k, v in __import__("os").environ.items() if k != "ARROW_DEFAULT_MEMORY_POOL"}
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, env=env, check=True)
    assert out.stdout.strip() == "system"

"""Execute the read-only PowerShell prelaunch validator; never start analyzer."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
PWSH = shutil.which("pwsh") or shutil.which("powershell")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell unavailable")


def quoted(value):
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture
def configured(tmp_path):
    from test_declared_shadow_scenario_input import scenario
    model = tmp_path / "scenario with spaces.json"
    raw = json.dumps(scenario()).encode()
    model.write_bytes(raw)
    config = tmp_path / "launch.json"
    config.write_text(json.dumps({"schema": "analyzer_shadow_scenario_launch_v1",
        "model_file": str(model), "model_sha256": hashlib.sha256(raw).hexdigest()}))
    return config, model


def execute(body):
    script = "$ErrorActionPreference='Stop'\n. " + quoted(ROOT / "scripts/analyzer-scenario-launch-config.ps1") + "\n"
    script += "try {\n" + body + "\n} catch { Write-Output $_.Exception.Message; exit 7 }"
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30)


def load(config):
    return "Get-AnalyzerScenarioLaunchConfig -ConfigPath " + quoted(config) + " -PythonExe " + quoted(sys.executable)


def check():
    return "Assert-AnalyzerScenarioLaunchConfig -Receipt $r -PythonExe " + quoted(sys.executable)


def test_valid_durable_config_and_unchanged_pin(configured):
    config, model = configured
    result = execute("$r=" + load(config) + "\n" + check() + "\n$r|ConvertTo-Json -Compress")
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads(result.stdout)
    assert receipt["enabled"] is True and receipt["model_file"] == str(model)
    assert receipt["model_sha256"] == hashlib.sha256(model.read_bytes()).hexdigest()


@pytest.mark.parametrize("defect,code", [
    ("hash", "HASH_MISMATCH"), ("missing_pair", "LAUNCH_SCHEMA_INVALID"),
    ("missing_config", "INPUT_UNAVAILABLE_OR_INVALID"), ("schema", "MODEL_SCHEMA_INVALID"),
    ("oversized", "SIZE_INVALID")])
def test_prelaunch_rejects_bad_inputs(configured, defect, code):
    config, model = configured
    value = json.loads(config.read_text())
    if defect == "hash": value["model_sha256"] = "0" * 64
    if defect == "missing_pair": del value["model_sha256"]
    if defect == "schema":
        model.write_text('{"schema":"unsupported"}')
        value["model_sha256"] = hashlib.sha256(model.read_bytes()).hexdigest()
    if defect == "oversized": model.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    config.write_text(json.dumps(value))
    if defect == "missing_config": config.unlink()
    result = execute("$r=" + load(config))
    assert result.returncode == 7 and code in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("target,code", [("model", "HASH_MISMATCH"), ("config", "LAUNCH_INPUT_CHANGED")])
def test_changed_input_between_validation_and_spawn_is_refused(configured, target, code):
    config, model = configured
    changed = model if target == "model" else config
    result = execute("$r=" + load(config) + "\n[IO.File]::AppendAllText(" + quoted(changed) + ", ' ')\n" + check())
    assert result.returncode == 7 and code in result.stdout, result.stdout + result.stderr


def test_inherited_incomplete_pair_cannot_reach_spawn(configured):
    _, model = configured
    result = execute("Get-AnalyzerScenarioLaunchConfig -ModelFile " + quoted(model) + " -PythonExe " + quoted(sys.executable))
    assert result.returncode == 7 and "PIN_PAIR_REQUIRED" in result.stdout


def test_no_config_or_pin_preserves_disabled_default():
    result = execute("Get-AnalyzerScenarioLaunchConfig -PythonExe " + quoted(sys.executable) + " | ConvertTo-Json -Compress")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["enabled"] is False


def test_real_launcher_parses_and_validates_before_every_process_mutation():
    launcher = ROOT / "scripts/start-home-analyzer.ps1"
    script = "$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile(" + quoted(launcher) + ",[ref]$tokens,[ref]$errors)\n"
    script += "if($errors.Count){throw 'PARSE_FAILED'}\n"
    script += "$commands=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.CommandAst]},$true))\n"
    script += "$first=($commands|Where-Object {$_.GetCommandName() -eq 'Get-AnalyzerScenarioLaunchConfig'}|Select-Object -First 1).Extent.StartOffset\n"
    script += "$mutators=@($commands|Where-Object {$_.GetCommandName() -in @('Stop-Process','Stop-ListenPortFast','Start-Process','python')})\n"
    script += "if($mutators.Count -lt 6 -or @($mutators|Where-Object {$_.Extent.StartOffset -lt $first}).Count){throw 'VALIDATION_TOO_LATE'}\nWrite-Output 'PASS'"
    result = execute(script)
    assert result.returncode == 0 and "PASS" in result.stdout, result.stdout + result.stderr
    source = launcher.read_text(encoding="utf-8-sig")
    assert source.count("Assert-AnalyzerScenarioLaunchConfig -Receipt $scenarioLaunch") == 6
    assert '"scripts/analyzer-scenario-launch-config.py"' in source
    assert '"scripts/analyzer-scenario-launch-config.ps1"' in source


def test_actual_provenance_filter_refuses_dirty_powershell_helper():
    launcher = ROOT / "scripts/start-home-analyzer.ps1"
    script = "$tokens=$null;$errors=$null;$ast=[Management.Automation.Language.Parser]::ParseFile(" + quoted(launcher) + ",[ref]$tokens,[ref]$errors)\n"
    script += "$assignment=$ast.Find({param($n) $n -is [Management.Automation.Language.AssignmentStatementAst] -and $n.Left.Extent.Text -eq '$dirtyAnalyzerSources'},$true)\n"
    script += "$filter=$assignment.Find({param($n) $n -is [Management.Automation.Language.ScriptBlockExpressionAst]},$true)\n"
    script += "$predicate=[scriptblock]::Create($filter.ScriptBlock.Extent.Text.Trim().Substring(1,$filter.ScriptBlock.Extent.Text.Trim().Length-2))\n"
    script += "$selected=@(' M scripts/analyzer-scenario-launch-config.ps1','?? scripts/analyzer-scenario-launch-config.ps1',' M scripts/unrelated.ps1' | Where-Object $predicate)\n"
    script += "if($selected.Count -ne 2 -or @($selected|Where-Object {$_ -match 'unrelated'}).Count){throw 'HELPER_PROVENANCE_NOT_FAIL_CLOSED'}\nWrite-Output 'PASS'"
    result = execute(script)
    assert result.returncode == 0 and "PASS" in result.stdout, result.stdout + result.stderr

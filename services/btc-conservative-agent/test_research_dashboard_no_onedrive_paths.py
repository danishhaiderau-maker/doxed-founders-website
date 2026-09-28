import json
import os
from pathlib import Path
import subprocess
import sys
import pytest


AGENT_ROOT = Path(__file__).resolve().parent


def test_dashboard_rejects_poisoned_onedrive_environment(tmp_path):
    local_app_data = tmp_path / "LocalAppData"
    env = os.environ.copy()
    env.update(
        {
            "LOCALAPPDATA": str(local_app_data),
            "BTC_AGENT_DATA_DIR": r"C:\Users\tester\OneDrive\stale-mirror",
            "BTC_AGENT_REPORT_DIR": r"C:\Users\tester\OneDrive\stale-reports",
            "RESEARCH_HISTORY_ROOT": r"C:\Users\tester\OneDrive\stale-history",
        }
    )
    code = (
        "import json; import research.research_dashboard as d; "
        "print(json.dumps({'data': str(d.DATA_ROOT), 'reports': str(d.ROOT), "
        "'history': str(d.HISTORY_ROOT)}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=AGENT_ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    paths = json.loads(result.stdout.strip().splitlines()[-1])
    assert "onedrive" not in " ".join(paths.values()).casefold()
    assert Path(paths["data"]) == AGENT_ROOT / "canonical-research-data"
    assert Path(paths["reports"]) == AGENT_ROOT / "canonical-research-data" / "analyzer"


def test_dashboard_rejects_legacy_localappdata_mirror(tmp_path):
    env = os.environ.copy()
    env["BTC_AGENT_DATA_DIR"] = str(
        tmp_path / "LocalAppData" / "DoxxedCrypto" / "fly-data-mirror"
    )
    result = subprocess.run(
        [sys.executable, "-c", "import research.research_dashboard"],
        cwd=AGENT_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "must select the repo-contained canonical-research-data store" in result.stderr


def test_standalone_unset_report_env_uses_canonical_root_in_health():
    env = os.environ.copy()
    env.pop("BTC_AGENT_REPORT_DIR", None)
    env["BTC_AGENT_DATA_DIR"] = str(AGENT_ROOT / "canonical-research-data")
    code = (
        "import json; import research.research_dashboard as d; "
        "h=d.app.test_client().get('/api/health').get_json(); "
        "print(json.dumps({'report_root':h['report_root'],'data_root':h['data_root']}))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=AGENT_ROOT,
        env=env, check=True, capture_output=True, text=True)
    health = json.loads(result.stdout.strip().splitlines()[-1])
    assert Path(health["report_root"]) == AGENT_ROOT / "canonical-research-data" / "analyzer"
    assert Path(health["data_root"]) == AGENT_ROOT / "canonical-research-data"


@pytest.mark.parametrize("subdirectory", ["", "research"])
def test_explicit_source_report_root_rejected(subdirectory):
    env = os.environ.copy()
    env["BTC_AGENT_DATA_DIR"] = str(AGENT_ROOT / "canonical-research-data")
    env["BTC_AGENT_REPORT_DIR"] = str(AGENT_ROOT / subdirectory)
    result = subprocess.run([sys.executable, "-c", "import research.research_dashboard"],
        cwd=AGENT_ROOT, env=env, capture_output=True, text=True)
    assert result.returncode != 0
    assert "cannot select an analyzer source root" in result.stderr


def test_missing_analyzer_directory_does_not_revive_source_report(tmp_path, monkeypatch):
    from research import research_dashboard as dashboard
    source = tmp_path / "agent"
    source.mkdir()
    (source / "ai_calibration_report.json").write_text('{"old_source_report":true}')
    data = source / "canonical-research-data"
    data.mkdir()
    selected = dashboard._select_report_root(data, source)
    assert selected == data / "analyzer" and not selected.exists()
    monkeypatch.setattr(dashboard, "ROOT", selected)
    monkeypatch.setattr(dashboard, "DATA_ROOT", data)
    assert dashboard._read_report("ai_calibration_report.json", {}) == {}
    assert not selected.exists()
    separate = tmp_path / "explicit-diagnostic-reports"
    assert dashboard._select_report_root(data, source, separate) == separate.resolve()

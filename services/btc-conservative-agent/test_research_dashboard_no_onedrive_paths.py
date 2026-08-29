import json
import os
from pathlib import Path
import subprocess
import sys


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
    assert Path(paths["reports"]) == AGENT_ROOT


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

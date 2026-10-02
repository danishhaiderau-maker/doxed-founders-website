"""Run the script-style regression suites as subprocesses so pytest sees their result."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.parametrize("script", [
    "test_dashboard_timestamps.py",
    "test_toggle_matrix.py",
    "test_pause_owner_resume_default.py",
])
def test_script_style_suite_passes(script):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("BTC_AGENT_")}
    result = subprocess.run(
        [sys.executable, str(HERE / script)], cwd=HERE, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600,
    )
    assert result.returncode == 0, (result.stdout[-4000:], result.stderr[-4000:])

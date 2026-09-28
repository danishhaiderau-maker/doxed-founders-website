import os
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bot-auto-restart.ps1"


def test_bot_auto_restart_parses_and_contract_mode_fails_closed_without_side_effects():
    env = os.environ.copy()
    env.pop("DCF_ENABLE_OBSOLETE_WINDOWS_TRADING_OWNER", None)
    env["DCF_LEGACY_WINDOWS_LAUNCH_CONTRACT_TEST"] = "NO_SIDE_EFFECTS"
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 78
    combined = re.sub(r"\s+", " ", completed.stdout + completed.stderr)
    assert "Obsolete Windows bot restart monitor is quarantined" in combined
    assert "DISASTER-RECOVERY OPT-IN ACTIVE" not in combined
    text = SCRIPT.read_text(encoding="utf-8")
    contract_gate = text.index("DCF_LEGACY_WINDOWS_LAUNCH_CONTRACT_TEST")
    safe_mirror_call = text.index("& $mirrorScript -NoWait")
    assert contract_gate < safe_mirror_call
    assert "exit 78" in text[contract_gate:safe_mirror_call]


def test_parameter_block_precedes_every_executable_statement():
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.index("param(") < text.index('$ErrorActionPreference = "Continue"')
    assert text.index("param(") < text.index("Write-Warning")

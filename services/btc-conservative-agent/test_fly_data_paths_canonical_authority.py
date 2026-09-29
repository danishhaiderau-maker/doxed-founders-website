import os
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts" / "fly-data-paths.ps1"
CANONICAL = REPO / "services" / "btc-conservative-agent" / "canonical-research-data"


def _powershell(expression: str, *, legacy_path: str | None = None):
    env = os.environ.copy()
    if legacy_path is not None:
        env["DOXXED_FLY_MIRROR_DIR"] = legacy_path
        env["BTC_AGENT_DATA_DIR"] = legacy_path
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            f". '{HELPER}'; {expression}",
        ],
        cwd=REPO,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )


def test_ambient_legacy_mirror_environment_cannot_select_dataset_authority():
    legacy = str(Path(os.environ.get("LOCALAPPDATA", REPO)) / "DoxxedCrypto" / "fly-data-mirror")
    result = _powershell("Get-DoxxedFlyMirrorDir", legacy_path=legacy)

    assert result.returncode == 0, result.stderr
    assert Path(result.stdout.strip()).resolve() == CANONICAL.resolve()


def test_explicit_noncanonical_requested_path_is_refused():
    result = _powershell("Get-DoxxedFlyMirrorDir -RequestedPath 'C:\\Temp\\wrong-mirror'")

    assert result.returncode != 0
    assert "must select the repo-contained canonical store" in result.stderr

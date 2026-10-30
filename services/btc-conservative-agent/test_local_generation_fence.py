"""Malformed tombstones must block consumers, never look like no reset."""
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from research.local_generation_fence import (
    FENCE_FILE_NAME, LocalGenerationFenced, assert_local_generation_available,
    read_local_generation_fence,
)


def valid_fence():
    return dict(schema="local_research_generation_fence_v1",
                state="BLOCKED_PENDING_VERIFIED_IMPORT", operation_id="operation",
                local_generation="local", tombstone_id="tombstone")


@pytest.mark.parametrize("shape", ["directory", "invalid_json", "list", "missing_tombstone"])
def test_invalid_fence_is_never_absent(tmp_path, shape):
    path = tmp_path / FENCE_FILE_NAME
    if shape == "directory":
        path.mkdir()
    else:
        body = valid_fence()
        body.pop("tombstone_id")
        path.write_text({"invalid_json": "{", "list": "[]",
                         "missing_tombstone": json.dumps(body)}[shape], encoding="utf-8")
    with pytest.raises(LocalGenerationFenced, match="FENCE_INVALID"):
        read_local_generation_fence(tmp_path)


def test_dangling_symlink_fence_blocks(tmp_path):
    path = tmp_path / FENCE_FILE_NAME
    try:
        path.symlink_to(tmp_path / "missing-target")
    except OSError:
        pytest.skip("Host does not permit symlink creation")
    with pytest.raises(LocalGenerationFenced, match="FENCE_INVALID"):
        read_local_generation_fence(tmp_path)


def test_absent_and_valid_fences_remain_distinct(tmp_path):
    assert read_local_generation_fence(tmp_path) is None
    (tmp_path / FENCE_FILE_NAME).write_text(json.dumps(valid_fence()), encoding="utf-8")
    with pytest.raises(LocalGenerationFenced, match="LOCAL_GENERATION_FENCED"):
        assert_local_generation_available(tmp_path, stage="fixture")


# R23 restore (#913): keep the pre-weaken EncodedCommand budget at 15s.
# Module warm-up + telemetry opt-out from #913 stay as CI reliability helpers
# (do not raise the assertion timeout); both need RK review on this head.
_PWSH_TIMEOUT_SEC = 15
_PWSH_ENV = {
    "POWERSHELL_TELEMETRY_OPTOUT": "1",
    "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
}


def test_pwsh_fence_parity_timeout_stays_at_pre_913_budget():
    """R23 pin: #913 raised this to 60; FREEZE item (4) restores 15."""
    assert _PWSH_TIMEOUT_SEC == 15


@pytest.fixture(scope="module")
def powershell_shell():
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is unavailable")
    env = {**os.environ, **_PWSH_ENV}
    subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-NoLogo", "-Command", "exit 0"],
        text=True, capture_output=True, timeout=_PWSH_TIMEOUT_SEC, check=False, env=env,
    )
    return shell


@pytest.mark.parametrize("shape", ["absent", "directory", "invalid_json", "missing_tombstone", "valid"])
def test_powershell_fence_shape_parity(tmp_path, shape, powershell_shell):
    path = tmp_path / FENCE_FILE_NAME
    if shape == "directory":
        path.mkdir()
    elif shape != "absent":
        body = valid_fence()
        if shape == "missing_tombstone":
            body.pop("tombstone_id")
        path.write_text("{" if shape == "invalid_json" else json.dumps(body), encoding="utf-8")
    module = Path(__file__).resolve().parents[2] / "scripts" / "local-generation-fence.ps1"
    quote = lambda value: str(value).replace("'", "''")
    script = f". '{quote(module)}'; try {{ Assert-LocalGenerationUnfenced -DataRoot '{quote(tmp_path)}' -Stage fixture; 'CLEAR' }} catch {{ $_.Exception.Message }}"
    result = subprocess.run(
        [powershell_shell, "-NoProfile", "-NonInteractive", "-NoLogo", "-EncodedCommand",
         base64.b64encode(script.encode("utf-16le")).decode()],
        text=True, capture_output=True, timeout=_PWSH_TIMEOUT_SEC, check=True,
        env={**os.environ, **_PWSH_ENV},
    )
    expected = "CLEAR" if shape == "absent" else (
        "LOCAL_GENERATION_FENCED" if shape == "valid" else "LOCAL_GENERATION_FENCE_INVALID")
    assert expected in result.stdout

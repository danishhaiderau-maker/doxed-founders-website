"""Malformed tombstones must block consumers, never look like no reset."""
import base64
import json
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


@pytest.mark.parametrize("shape", ["absent", "directory", "invalid_json", "missing_tombstone", "valid"])
def test_powershell_fence_shape_parity(tmp_path, shape):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is unavailable")
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
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand",
                             base64.b64encode(script.encode("utf-16le")).decode()],
                            text=True, capture_output=True, timeout=15, check=True)
    expected = "CLEAR" if shape == "absent" else (
        "LOCAL_GENERATION_FENCED" if shape == "valid" else "LOCAL_GENERATION_FENCE_INVALID")
    assert expected in result.stdout

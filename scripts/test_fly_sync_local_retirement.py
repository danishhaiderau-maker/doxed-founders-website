"""Execute the actual laptop retirement block without network or downloader."""
import base64
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "sync-fly-bot-data.ps1"
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(not PWSH, reason="PowerShell 7 required")


def _quote(value):
    return "'" + str(value).replace("'", "''") + "'"


def _run(body):
    encoded = base64.b64encode(body.encode("utf-16le")).decode()
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def _retirement(tmp_path, flag, *, tamper=False):
    source = SOURCE.read_text(encoding="utf-8-sig")
    block = source.split("# Local retirement is separate from downloading.", 1)[1]
    block = "# Local retirement is separate from downloading." + block.split(
        "if ($IncludePath.Count -gt 0)", 1
    )[0]
    setup = f"""
$ErrorActionPreference = 'Stop'
$targetRoot = {_quote(tmp_path)}
$manifest = [pscustomobject]@{{files=@([pscustomobject]@{{path='current.jsonl'}})}}
$syncState = @{{'old.jsonl'=1; 'current.jsonl'=2}}
$script:saved = 0
function Save-SyncState {{ $script:saved += 1 }}
function Write-Host {{ param([Parameter(ValueFromRemainingArguments=$true)]$Values) }}
"""
    setup += ("Remove-Item Env:FLY_SYNC_LOCAL_RETIREMENT_ENABLED -ErrorAction SilentlyContinue\n"
              if flag is None else f"$env:FLY_SYNC_LOCAL_RETIREMENT_ENABLED = {_quote(flag)}\n")
    if flag != "1":
        # Disabled means no retirement enumeration, hashing, or mutation at all.
        setup += """
function Get-ChildItem { throw 'DISABLED_RETIREMENT_ENUMERATED' }
function Get-FileHash { throw 'DISABLED_RETIREMENT_HASHED' }
function Remove-Item { throw 'DISABLED_RETIREMENT_REMOVED' }
"""
    elif tamper:
        setup += r"""
function Get-FileHash {
  param([string]$LiteralPath, [string]$Algorithm)
  $result = Microsoft.PowerShell.Utility\Get-FileHash -LiteralPath $LiteralPath -Algorithm $Algorithm
  if ($LiteralPath.EndsWith('.tmp')) { $result.Hash = '0' * 64 }
  return $result
}
"""
    return _run(setup + "\n$failure = $null\ntry {\n" + block + """
} catch { $failure = $_.Exception.Message }
[ordered]@{ status=$localRetirementStatus; files=$staleRotationFiles;
 bytes=$staleRotationBytes; saved=$script:saved; failure=$failure;
 old_checkpoint=$syncState.ContainsKey('old.jsonl') } | ConvertTo-Json -Compress
""")


@pytest.mark.parametrize("flag", [None, "", "0", "false", "true", "01"])
def test_retirement_is_disabled_without_exact_opt_in(tmp_path, flag):
    (tmp_path / "old.jsonl").write_bytes(b'{"old":1}\n')
    result = _retirement(tmp_path, flag)
    assert result == {"status": "DISABLED_SOURCE_RETAINED", "files": 0,
                      "bytes": 0, "saved": 0, "failure": None, "old_checkpoint": True}
    assert (tmp_path / "old.jsonl").read_bytes() == b'{"old":1}\n'
    assert not (tmp_path / "archive").exists()


def test_enabled_retirement_preserves_archive_first_and_current_files(tmp_path):
    payload = b'{"old":1}\n'
    (tmp_path / "old.jsonl").write_bytes(payload)
    (tmp_path / "current.jsonl").write_bytes(b'{"current":1}\n')
    (tmp_path / "canonical_dataset_manifest.jsonl").write_bytes(b'{"manifest":1}\n')
    result = _retirement(tmp_path, "1")
    assert result == {"status": "ARCHIVED_AND_VERIFIED", "files": 1,
                      "bytes": len(payload), "saved": 1, "failure": None,
                      "old_checkpoint": False}
    assert not (tmp_path / "old.jsonl").exists()
    archived = list((tmp_path / "archive" / "sync-retired").rglob("old.jsonl"))
    assert len(archived) == 1 and archived[0].read_bytes() == payload
    receipt = json.loads(Path(str(archived[0]) + ".receipt.json").read_text(encoding="utf-8-sig"))
    assert receipt["recoverable"] is True and receipt["archive_bytes"] == len(payload)
    assert receipt["verification"] == "COPY_AND_SOURCE_STABILITY_SHA256_VERIFIED_BEFORE_REMOVAL"
    assert (tmp_path / "current.jsonl").exists()
    assert (tmp_path / "canonical_dataset_manifest.jsonl").exists()


def test_failed_archive_verification_retains_original_and_checkpoint(tmp_path):
    payload = b'{"old":1}\n'
    (tmp_path / "old.jsonl").write_bytes(payload)
    result = _retirement(tmp_path, "1", tamper=True)
    assert result["failure"].startswith("Archive verification failed; source retained:")
    assert result["files"] == result["saved"] == 0
    assert result["old_checkpoint"] is True
    assert (tmp_path / "old.jsonl").read_bytes() == payload
    assert not list((tmp_path / "archive").rglob("*.receipt.json"))


def test_complete_sync_script_parses_and_exposes_retirement_receipt():
    result = _run(f"""
$tokens=$null; $errors=$null
[void][System.Management.Automation.Language.Parser]::ParseFile({_quote(SOURCE)}, [ref]$tokens, [ref]$errors)
@{{parse_errors=@($errors).Count}} | ConvertTo-Json -Compress
""")
    assert result["parse_errors"] == 0
    source = SOURCE.read_text(encoding="utf-8-sig")
    for field in ("LocalRetirementStatus", "LocalRetirementFiles", "LocalRetirementBytes"):
        assert field in source
    assert "FLY_SYNC_LOCAL_RETIREMENT_ENABLED -ceq '1'" in source

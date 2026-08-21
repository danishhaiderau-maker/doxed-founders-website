import json
import subprocess
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
HELPER = REPO / "scripts" / "fly-mirror-quarantine.ps1"
LOOP = REPO / "scripts" / "sync-fly-bot-data-loop.ps1"


def _ps_quote(value: Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def test_recursive_quarantine_preserves_paths_bytes_and_hashes(tmp_path):
    mirror = tmp_path / "mirror"
    nested = mirror / "research" / "genome"
    nested.mkdir(parents=True)
    (mirror / "root.jsonl").write_bytes(b"root-old\n")
    (nested / "opportunity.jsonl").write_bytes(b"nested-old\n")
    quarantine = tmp_path / "quarantine"
    command = (
        f". {_ps_quote(HELPER)}; "
        f"$r=Invoke-FlyMirrorEpochQuarantine -MirrorPath {_ps_quote(mirror)} "
        f"-QuarantineRoot {_ps_quote(quarantine)} -FreshCollectionSignalTs 123.5; "
        "$r.ManifestPath"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), result.stderr
    manifest_path = Path(result.stdout.strip().splitlines()[-1])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    assert manifest["schema"] == "fly_mirror_epoch_quarantine_v2"
    assert manifest["complete"] is True
    assert {row["path"] for row in manifest["files"]} == {
        "root.jsonl", "research/genome/opportunity.jsonl"
    }
    assert not any(path.is_file() for path in mirror.rglob("*"))
    destination = manifest_path.parent
    assert (destination / "root.jsonl").read_bytes() == b"root-old\n"
    assert (destination / "research" / "genome" / "opportunity.jsonl").read_bytes() == b"nested-old\n"


def test_loop_acknowledges_signal_only_after_verified_quarantine():
    source = LOOP.read_text(encoding="utf-8")
    quarantine_call = source.index("Invoke-FlyMirrorEpochQuarantine")
    receipt_write = source.index("Set-Content -LiteralPath $freshSignalFile")
    sync_call = source.index('sync-fly-bot-data.ps1')
    assert quarantine_call < receipt_write < sync_call
    assert "Get-ChildItem -LiteralPath $mirrorDir -File" not in source
    assert "fresh-signal state" in source


def test_locked_source_fails_closed_without_complete_manifest(tmp_path):
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    source_file = mirror / "held.jsonl"
    source_file.write_bytes(b"must-survive\n")
    quarantine = tmp_path / "quarantine"
    command = (
        f". {_ps_quote(HELPER)}; "
        f"$s=[IO.File]::Open({_ps_quote(source_file)},[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read); "
        "try { try { Invoke-FlyMirrorEpochQuarantine "
        f"-MirrorPath {_ps_quote(mirror)} -QuarantineRoot {_ps_quote(quarantine)} "
        "-FreshCollectionSignalTs 456.5 -RetryCount 2 -RetryDelayMs 10; exit 9 "
        "} catch { exit 0 } } finally { $s.Dispose() }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert source_file.read_bytes() == b"must-survive\n"
    assert not list(quarantine.rglob("quarantine_manifest.json"))

    retry = (
        f". {_ps_quote(HELPER)}; "
        f"$r=Invoke-FlyMirrorEpochQuarantine -MirrorPath {_ps_quote(mirror)} "
        f"-QuarantineRoot {_ps_quote(quarantine)} -FreshCollectionSignalTs 456.5; "
        "$r.ManifestPath"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", retry],
        capture_output=True, text=True,
    )
    assert completed.returncode == 0, completed.stderr
    manifest = json.loads(Path(completed.stdout.strip().splitlines()[-1]).read_text(encoding="utf-8-sig"))
    assert manifest["file_count"] == 1
    assert manifest["files"][0]["path"] == "held.jsonl"

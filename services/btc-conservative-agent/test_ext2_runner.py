"""Real-run entry point. Synthetic roots only. No GPU on this machine."""
import hashlib
import json
from pathlib import Path

from research.ext2_runner import PIN_PATH, SERVICE_ROOT, run_ext2


def _roots(tmp_path: Path):
    data = tmp_path / "data"
    output = tmp_path / "out"
    data.mkdir()
    output.mkdir()
    return data, output


def test_run_entry_stops_without_a_gpu_and_rejects_the_cpu_flag(tmp_path):
    data, output = _roots(tmp_path)
    refused = run_ext2(data_root=data, output_root=output, allow_cpu_fallback=True)
    assert refused["reason"] == "STOP_CPU_FALLBACK_FORBIDDEN"
    assert refused["status"] == "STOP"
    stopped = run_ext2(
        data_root=data, output_root=output, allow_cpu_fallback=False,
        forbidden_substrings=("super-secret-value",),
    )
    assert stopped["reason"] == "STOP_GPU_MISSING"
    assert stopped["combos_searched"] == 181442
    assert stopped["fallback"] is False
    lattice = [stage for stage in stopped["devices"] if stage["stage"] == "exit_lattice"]
    assert lattice[-1]["device"] is None
    assert lattice[-1]["fallback"] is False
    text = json.dumps(stopped)
    assert "super-secret-value" not in text
    assert "environ" not in stopped


def test_import_pins_match_the_shipped_files_and_a_mismatch_stops(tmp_path):
    document = json.loads(PIN_PATH.read_text(encoding="utf-8"))
    for rel, expected in document["files"].items():
        digest = hashlib.sha256((SERVICE_ROOT / rel).read_bytes()).hexdigest()
        assert digest == expected
    document["files"]["research/ext2_costs.py"] = "0" * 64
    bad = tmp_path / "pins.json"
    bad.write_text(json.dumps(document), encoding="utf-8")
    data, output = _roots(tmp_path)
    receipt = run_ext2(data_root=data, output_root=output, pin_path=bad)
    assert receipt["reason"] == "STOP_IMPORT_PIN"
    assert receipt["status"] == "STOP"


def test_a_bad_data_file_sha_stops_at_the_start(tmp_path):
    data, output = _roots(tmp_path)
    target = data / "trades"
    target.mkdir()
    blob = target / "2024-11-01.json.gz"
    blob.write_bytes(b"not-the-real-bytes")
    manifest = {"trades/2024-11-01.json.gz": "ab" * 32}
    receipt = run_ext2(data_root=data, output_root=output, manifest=manifest, allowlist=list(manifest))
    assert receipt["reason"] == "STOP_SHA_MISMATCH"

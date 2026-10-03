import json

import pytest

from research.entry_baseline_replay import (
    EPISODE_RECEIPTS_SIDECAR_FILE, load_replay_report, write_replay_report,
)


def _report():
    return {
        "schema": "entry_baseline_replay_report_v1",
        "summary": {"episodes": 3},
        "episode_receipts": [{"episode_id": f"ep-{i}", "fills": [i, i + 1], "note": "é"} for i in range(3)],
        "baselines": {"B1": {"n": 3}},
    }


def test_sidecar_round_trip_restores_exact_report_shape(tmp_path):
    report = _report()
    target = tmp_path / "entry_baseline_replay_report.json"
    binding = write_replay_report(report, target)
    body = json.loads(target.read_text(encoding="utf-8"))
    assert "episode_receipts" not in body and body["episode_receipts_sidecar"] == binding
    assert binding["count"] == 3 and (tmp_path / EPISODE_RECEIPTS_SIDECAR_FILE).is_file()
    loaded = load_replay_report(target)
    assert json.dumps(loaded, indent=2) == json.dumps(report, indent=2)
    assert "episode_receipts" not in load_replay_report(target, with_receipts=False)
    assert not list(tmp_path.glob(".*.tmp"))


def test_sidecar_bytes_are_deterministic(tmp_path):
    a, b = tmp_path / "a" / "r.json", tmp_path / "b" / "r.json"
    a.parent.mkdir()
    b.parent.mkdir()
    assert write_replay_report(_report(), a)["sha256"] == write_replay_report(_report(), b)["sha256"]


def test_tampered_or_missing_sidecar_fails_closed(tmp_path):
    target = tmp_path / "entry_baseline_replay_report.json"
    write_replay_report(_report(), target)
    sidecar = tmp_path / EPISODE_RECEIPTS_SIDECAR_FILE
    sidecar.write_bytes(sidecar.read_bytes() + b"x")
    with pytest.raises(ValueError, match="SHA256_MISMATCH"):
        load_replay_report(target)
    sidecar.unlink()
    with pytest.raises(OSError):
        load_replay_report(target)


def test_legacy_inline_report_loads_unchanged(tmp_path):
    target = tmp_path / "entry_baseline_replay_report.json"
    target.write_text(json.dumps(_report()), encoding="utf-8")
    assert load_replay_report(target) == _report()

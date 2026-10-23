from __future__ import annotations

import pytest

import disk_alert


@pytest.fixture(autouse=True)
def _clear():
    for row in disk_alert.red_conditions():
        disk_alert.clear_red(row.get("reason"))
    yield
    for row in disk_alert.red_conditions():
        disk_alert.clear_red(row.get("reason"))


@pytest.mark.parametrize("used,level", [(69, "GREEN"), (70, "AMBER"), (84, "AMBER"), (85, "RED"), (99, "RED")])
def test_thresholds(used, level):
    out = disk_alert.compute_disk_alert({"total_bytes": 100, "used_bytes": used, "hours_to_full": 12.5})
    assert out["level"] == level
    assert out["amber_at"] == 0.70 and out["red_at"] == 0.85
    assert out["hours_to_full"] == 12.5
    assert out["schema"] == disk_alert.SCHEMA


def test_unknown_without_volume():
    assert disk_alert.compute_disk_alert(None)["level"] == "UNKNOWN"
    assert disk_alert.compute_disk_alert({"total_bytes": 0, "used_bytes": 1})["level"] == "UNKNOWN"


def test_retention_red_forces_red_until_cleared():
    disk_alert.raise_red("SEGMENT_CAP_DELETED_REFERENCED", {"count": 1}, now=1.0)
    out = disk_alert.compute_disk_alert({"total_bytes": 100, "used_bytes": 10})
    assert out["level"] == "RED"
    assert [r["reason"] for r in out["retention_red"]] == ["SEGMENT_CAP_DELETED_REFERENCED"]
    disk_alert.clear_red("SEGMENT_CAP_DELETED_REFERENCED")
    assert disk_alert.compute_disk_alert({"total_bytes": 100, "used_bytes": 10})["level"] == "GREEN"


def test_bot_wires_disk_alert_into_health_and_monitor_safety():
    from pathlib import Path
    src = (Path(__file__).resolve().parent / "bot.py").read_text("utf-8")
    assert 'payload["disk_alert"] = disk_alert.compute_disk_alert(payload["volume"])' in src
    assert '"disk_alert": _monitor_part(lambda: disk_alert.compute_disk_alert(' in src

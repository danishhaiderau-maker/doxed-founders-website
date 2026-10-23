from __future__ import annotations

import pytest

import disk_alert


@pytest.fixture(autouse=True)
def _clear():
    disk_alert.configure(None)
    for row in disk_alert.red_conditions():
        disk_alert.clear_red(row.get("reason"))
    yield
    disk_alert.configure(None)
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
    disk_alert.raise_red("SEGMENT_CAP_DELETED_REFERENCED", {"count": 1})
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


def test_c6_red_held_24h_after_last_occurrence_then_clears():
    t0 = 1_000_000.0
    disk_alert.raise_red("BUNDLE_CAP_REACHED", {"count": 1}, now=t0)
    disk_alert.raise_red("BUNDLE_CAP_REACHED", {"count": 2}, now=t0 + 3600)
    row, = disk_alert.red_conditions(now=t0 + 3600)
    assert row["since"] == t0 and row["last"] == t0 + 3600 and row["occurrences"] == 2 and row["count"] == 2
    held = disk_alert.RED_HOLD_SECONDS
    assert disk_alert.red_conditions(now=t0 + 3600 + held - 1)
    assert disk_alert.red_conditions(now=t0 + 3600 + held) == []


def test_c6_red_persists_across_restart_and_expires(tmp_path):
    state = tmp_path / "retention" / "disk_alert_red.json"
    t0 = 2_000_000.0
    disk_alert.configure(state, now=t0)
    disk_alert.raise_red("V22_DISCARDED_WITH_PENDING", {"pending_count": 3}, now=t0)
    assert state.exists()
    doc = __import__("json").loads(state.read_text())
    assert doc["schema"] == disk_alert.STATE_SCHEMA and "V22_DISCARDED_WITH_PENDING" in doc["red"]
    # simulated restart: memory empty, the file brings it back
    disk_alert.configure(None)
    disk_alert.clear_red("V22_DISCARDED_WITH_PENDING")
    disk_alert.configure(state, now=t0 + 60)
    assert [r["reason"] for r in disk_alert.red_conditions(now=t0 + 60)] == ["V22_DISCARDED_WITH_PENDING"]
    # a restart after the hold reloads nothing and rewrites the file empty
    disk_alert.configure(None)
    disk_alert.clear_red("V22_DISCARDED_WITH_PENDING")
    disk_alert.configure(state, now=t0 + disk_alert.RED_HOLD_SECONDS + 1)
    assert disk_alert.red_conditions(now=t0 + disk_alert.RED_HOLD_SECONDS + 1) == []
    assert __import__("json").loads(state.read_text())["red"] == {}


def test_c6_corrupt_state_file_starts_empty(tmp_path):
    state = tmp_path / "disk_alert_red.json"
    state.write_text("{not json")
    disk_alert.configure(state)
    assert disk_alert.red_conditions() == []


def test_c6_segment_shipper_red_and_over_cap_fold_into_disk_alert():
    transfer = {"r5_segment_red": {"SEGMENT_STORE_CAP_REACHED": {"since": 1.0, "last": 2.0}},
                "r5_segment_over_cap": True, "r5_segment_last_deleted": 4, "r5_segment_last_at": 2.0}
    out = disk_alert.compute_disk_alert({"total_bytes": 100, "used_bytes": 10, "transfer": transfer})
    assert out["level"] == "RED"
    assert [r["reason"] for r in out["retention_red"]] == ["SEGMENT_STORE_CAP_REACHED", "SEGMENT_STORE_OVER_CAP"]
    assert out["segment_store"]["last_deleted"] == 4 and out["segment_store"]["over_cap"] is True
    assert out["red_hold_seconds"] == 24 * 3600
    clean = disk_alert.compute_disk_alert({"total_bytes": 100, "used_bytes": 10,
                                           "transfer": {"r5_segment_red": {}, "r5_segment_over_cap": False}})
    assert clean["level"] == "GREEN" and clean["retention_red"] == []


def test_c6_bot_and_server_carry_segment_r5_fields():
    from pathlib import Path
    root = Path(__file__).resolve().parent
    bot = (root / "bot.py").read_text("utf-8")
    server = (root / "research_segment_server.py").read_text("utf-8")
    for field in ("r5_segment_red", "r5_segment_over_cap", "r5_segment_last_deleted", "r5_segment_last_at",
                  "laptop_ack_advanced_at"):
        assert f'"{field}"' in bot.split("_SEGMENT_STATUS_FIELDS", 1)[1][:3000]
        assert f'"{field}"' in server
    assert 'disk_alert.configure(_data_sync_runtime_root() / "retention" / "disk_alert_red.json")' in bot

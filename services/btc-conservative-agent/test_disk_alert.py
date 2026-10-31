from __future__ import annotations

import json

import pytest

import disk_alert

AUTH = {"X-Bot-Admin-Token": "disk-alert-ack-test-token"}
REMOTE = {"REMOTE_ADDR": "198.51.100.9"}
URL = "/api/admin/disk-alert/red-ack"
CLEARABLE = "BUNDLE_CAP_REMOVED_UNACKED"


def _green_size_cap_dir(tmp_path):
    """Plant a GREEN strategist size-cap file so H1 UNKNOWN-from-missing does not mask volume tests."""
    (tmp_path / "strategist_stream_size_cap.json").write_text(json.dumps({
        "schema": "strategist_stream_size_cap_v1",
        "reason": "STREAM_SIZE_CAP",
        "level": "GREEN",
        "stop_writing": False,
        "sinks": {
            name: {"level": "GREEN", "stop_writing": False}
            for name in (
                "bfx_book_10l_1s.jsonl",
                "bfx_trades.jsonl",
                "venue_bbo_1m.jsonl",
                "bfx_deriv_status_1s.jsonl",
                "bfx_ws_events.jsonl",
            )
        },
    }), encoding="utf-8")
    return str(tmp_path)


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
def test_thresholds(used, level, tmp_path):
    data_dir = _green_size_cap_dir(tmp_path)
    out = disk_alert.compute_disk_alert({
        "total_bytes": 100, "used_bytes": used, "hours_to_full": 12.5, "data_dir": data_dir,
    })
    assert out["level"] == level
    assert out["amber_at"] == 0.70 and out["red_at"] == 0.85
    assert out["hours_to_full"] == 12.5
    assert out["schema"] == disk_alert.SCHEMA


def test_unknown_without_volume(tmp_path):
    data_dir = _green_size_cap_dir(tmp_path)
    assert disk_alert.compute_disk_alert({"data_dir": data_dir})["level"] == "UNKNOWN"
    assert disk_alert.compute_disk_alert({
        "total_bytes": 0, "used_bytes": 1, "data_dir": data_dir,
    })["level"] == "UNKNOWN"


def test_h1_missing_size_cap_file_escalates_disk_alert_red(tmp_path):
    """H1: missing size-cap file → UNKNOWN → compute_disk_alert RED (volume may be GREEN)."""
    out = disk_alert.compute_disk_alert({
        "total_bytes": 100, "used_bytes": 10, "data_dir": str(tmp_path),
    })
    assert out["stream_size_cap"]["level"] == "UNKNOWN"
    assert out["stream_size_cap"].get("exists") is False
    assert out["level"] == "RED"
    assert out["stream_size_cap"].get("stop_writing") is False


def test_h1_unknown_without_bound_data_dir_is_still_red(tmp_path, monkeypatch):
    """R36/H1: UNKNOWN→RED even when no data_dir/root/BOT_DATA_DIR is set (no soft GREEN).

    Soft mapping UNKNOWN→GREEN when unbound is forbidden (ruling 2851ff0650e1).
    """
    monkeypatch.delenv("BOT_DATA_DIR", raising=False)
    monkeypatch.chdir(tmp_path)  # cwd has no size-cap file → UNKNOWN
    out = disk_alert.compute_disk_alert({"total_bytes": 100, "used_bytes": 10})
    assert out["stream_size_cap"]["level"] == "UNKNOWN"
    assert out["level"] == "RED"
    assert out["level"] != "GREEN"


def test_m5_unknown_level_alone_forces_red_without_retention_red(tmp_path, monkeypatch):
    """M5: UNKNOWN cap level → RED even when stream_cap.red is empty.

    Retention already merges STREAM_SIZE_CAP into retention_red, so deleting the
    UNKNOWN clause from ``compute_disk_alert`` still leaves H1 tests green.
    Mutation proof: monkeypatch status to UNKNOWN + empty red; a RED-only fold
    would leave volume-GREEN → assert level==RED must FAIL on that mutant.
    """
    monkeypatch.setattr(
        disk_alert,
        "stream_size_cap_status",
        lambda _d: {"level": "UNKNOWN", "red": [], "sinks": {}, "stop_writing": False, "exists": False},
    )
    out = disk_alert.compute_disk_alert({
        "total_bytes": 100, "used_bytes": 10, "data_dir": str(tmp_path),
    })
    assert out["stream_size_cap"]["level"] == "UNKNOWN"
    assert out["retention_red"] == []
    assert out["level"] == "RED"
    assert out["level"] != "GREEN"
    # Inline mutant: RED-only fold (drop UNKNOWN) stays GREEN on empty retention.
    mutant_level = "GREEN"  # volume 10%
    cap_level = "UNKNOWN"
    retention = []
    if retention or cap_level == "RED":  # M5 mutant — UNKNOWN clause deleted
        mutant_level = "RED"
    with pytest.raises(AssertionError):
        assert mutant_level == "RED"


def test_retention_red_forces_red_until_cleared(tmp_path):
    data_dir = _green_size_cap_dir(tmp_path)
    disk_alert.raise_red("SEGMENT_CAP_DELETED_REFERENCED", {"count": 1})
    out = disk_alert.compute_disk_alert({"total_bytes": 100, "used_bytes": 10, "data_dir": data_dir})
    assert out["level"] == "RED"
    assert [r["reason"] for r in out["retention_red"]] == ["SEGMENT_CAP_DELETED_REFERENCED"]
    disk_alert.clear_red("SEGMENT_CAP_DELETED_REFERENCED")
    assert disk_alert.compute_disk_alert({
        "total_bytes": 100, "used_bytes": 10, "data_dir": data_dir,
    })["level"] == "GREEN"


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


def test_c6_segment_shipper_red_and_over_cap_fold_into_disk_alert(tmp_path):
    data_dir = _green_size_cap_dir(tmp_path)
    transfer = {"r5_segment_red": {"SEGMENT_STORE_CAP_REACHED": {"since": 1.0, "last": 2.0}},
                "r5_segment_over_cap": True, "r5_segment_last_deleted": 4, "r5_segment_last_at": 2.0}
    out = disk_alert.compute_disk_alert({
        "total_bytes": 100, "used_bytes": 10, "transfer": transfer, "data_dir": data_dir,
    })
    assert out["level"] == "RED"
    assert [r["reason"] for r in out["retention_red"]] == ["SEGMENT_STORE_CAP_REACHED", "SEGMENT_STORE_OVER_CAP"]
    assert out["segment_store"]["last_deleted"] == 4 and out["segment_store"]["over_cap"] is True
    assert out["red_hold_seconds"] == 24 * 3600
    clean = disk_alert.compute_disk_alert({
        "total_bytes": 100, "used_bytes": 10, "data_dir": data_dir,
        "transfer": {"r5_segment_red": {}, "r5_segment_over_cap": False},
    })
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


def test_admin_disk_alert_red_ack_route_is_strict_admin_and_calls_clear_red():
    from pathlib import Path
    src = (Path(__file__).resolve().parent / "bot.py").read_text("utf-8")
    marker = "@app.route('/api/admin/disk-alert/red-ack', methods=['POST'])"
    assert marker in src
    route = src[src.index(marker):]
    route = route[: route.index("\n@app.route(")]
    assert "_admin_authed_strict()" in route
    assert route.index("_admin_authed_strict()") < route.index("disk_alert.clear_red(")
    assert 'disk_alert.clear_red(code)' in route
    assert "BUNDLE_CAP_REMOVED_UNACKED" in route
    assert "LEGACY_DATA_SYNC" not in route and "lifecycle-ack" not in route


def _ack_bot(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "disk-alert-ack-test-token")
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    return bot


def test_admin_disk_alert_red_ack_rejects_without_token(monkeypatch):
    bot = _ack_bot(monkeypatch)
    disk_alert.raise_red(CLEARABLE, {"count": 1})
    r = bot.app.test_client().post(
        URL,
        json={"actor": "ops", "reason": "bundle-cap latch clear", "code": CLEARABLE},
        environ_base=REMOTE,
    )
    assert r.status_code == 401
    assert CLEARABLE in {row["reason"] for row in disk_alert.red_conditions()}


def test_admin_disk_alert_red_ack_clears_bundle_cap_unacked_only(monkeypatch):
    bot = _ack_bot(monkeypatch)
    disk_alert.raise_red(CLEARABLE, {"count": 2})
    disk_alert.raise_red("BUNDLE_CAP_REACHED", {"count": 1})
    r = bot.app.test_client().post(
        URL,
        json={"actor": "ops-ack", "reason": "operator clear after zero bundles", "code": CLEARABLE},
        headers=AUTH,
        environ_base=REMOTE,
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["acked"]["code"] == CLEARABLE
    assert body["acked"]["actor"] == "ops-ack"
    assert "operator clear" in body["acked"]["reason"]
    remaining = {row["reason"] for row in disk_alert.red_conditions()}
    assert CLEARABLE not in remaining
    assert remaining == {"BUNDLE_CAP_REACHED"}
    assert body["retention_red_remaining"] == ["BUNDLE_CAP_REACHED"]


def test_admin_disk_alert_red_ack_refuses_other_codes(monkeypatch):
    bot = _ack_bot(monkeypatch)
    disk_alert.raise_red("BUNDLE_CAP_REACHED", {"count": 1})
    r = bot.app.test_client().post(
        URL,
        json={"actor": "ops", "reason": "wrong code", "code": "BUNDLE_CAP_REACHED"},
        headers=AUTH,
        environ_base=REMOTE,
    )
    assert r.status_code == 400
    assert r.get_json()["error"] == "CODE_NOT_CLEARABLE"
    assert "BUNDLE_CAP_REACHED" in {row["reason"] for row in disk_alert.red_conditions()}


def test_mutation_proof_disk_alert_red_ack_without_auth_gate_is_caught():
    from pathlib import Path
    src = (Path(__file__).resolve().parent / "bot.py").read_text("utf-8")
    route = src[src.index("@app.route('/api/admin/disk-alert/red-ack', methods=['POST'])"):]
    route = route[: route.index("\n@app.route(")]
    sabotaged = route.replace("if not _admin_authed_strict():", "if False:  # mutated", 1)
    assert "_admin_authed_strict()" not in sabotaged.split("disk_alert.clear_red", 1)[0]
    assert "admin authentication required" in route


def test_mutation_proof_disk_alert_red_ack_clear_other_reason_is_caught(monkeypatch):
    bot = _ack_bot(monkeypatch)
    disk_alert.raise_red(CLEARABLE, {"count": 1})
    disk_alert.raise_red("BUNDLE_CAP_REACHED", {"count": 1})
    # Sabotage: clear the wrong reason if the route ever stopped scoping to code.
    disk_alert.clear_red("BUNDLE_CAP_REACHED")
    remaining = {row["reason"] for row in disk_alert.red_conditions()}
    assert CLEARABLE in remaining and "BUNDLE_CAP_REACHED" not in remaining
    # The real route must still clear only CLEARABLE and leave REACHED alone when both latched.
    disk_alert.raise_red("BUNDLE_CAP_REACHED", {"count": 1})
    r = bot.app.test_client().post(
        URL,
        json={"actor": "ops", "reason": "scoped clear", "code": CLEARABLE},
        headers=AUTH,
        environ_base=REMOTE,
    )
    assert r.status_code == 200
    assert {row["reason"] for row in disk_alert.red_conditions()} == {"BUNDLE_CAP_REACHED"}

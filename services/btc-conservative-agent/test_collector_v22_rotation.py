import json
import multiprocessing
import os
import sqlite3
import threading
import time
from unittest import mock

import pytest

import collector_v22
from collector_v22 import (
    event_already_written,
    research_event_generation_paths,
    rotate_research_events,
    write_research_event_once,
)
from collector_v22_schema import EVENT_SQLITE_INDEX_FILE, OBS_DATA_ERROR, RESEARCH_EVENTS_FILE
from opportunity_capture_v22 import analyze_v22_events


def _event(event_id):
    return {
        "event_id": event_id,
        "trade_id": event_id,
        "collector_version": "collector_v2.2",
        "observation_status": OBS_DATA_ERROR,
        "primary_outcome": "REJECTED",
        "envelope": {"signal_ts": 1700000000.0},
    }


def _hold_cross_process_writer_lock(root, ready, release):
    with collector_v22._event_writer_exclusive(root):
        ready.set()
        release.wait(5.0)


def _cross_process_write(root, event, results):
    try:
        results.put(("ok", write_research_event_once(event, data_dir=root)))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def test_rotation_seals_under_authority_and_preserves_idempotency(tmp_path):
    assert write_research_event_once(_event("before"), data_dir=str(tmp_path))[0]
    result = rotate_research_events(data_dir=str(tmp_path))
    assert result["state"] == "SEALED"
    assert result["generation"] == 1
    paths = research_event_generation_paths(str(tmp_path))
    assert paths == [
        str(tmp_path / f"{RESEARCH_EVENTS_FILE}.1"),
        str(tmp_path / RESEARCH_EVENTS_FILE),
    ]
    assert event_already_written("before", data_dir=str(tmp_path))
    assert write_research_event_once(_event("before"), data_dir=str(tmp_path)) == (
        False,
        "duplicate event_id",
    )
    assert write_research_event_once(_event("after"), data_dir=str(tmp_path))[0]
    with sqlite3.connect(tmp_path / EVENT_SQLITE_INDEX_FILE) as index:
        assert index.execute("SELECT generation FROM events WHERE event_id='before'").fetchone()[0] == 1
        assert index.execute("SELECT generation FROM events WHERE event_id='after'").fetchone()[0] == 0
    report = analyze_v22_events(data_dir=str(tmp_path))
    assert report["replay_integrity"]["ineligible_events"] == 2


def test_unsealed_numeric_file_is_never_exposed(tmp_path):
    active = tmp_path / RESEARCH_EVENTS_FILE
    active.write_text(json.dumps(_event("active")) + "\n", "utf-8")
    (tmp_path / f"{RESEARCH_EVENTS_FILE}.7").write_text(
        json.dumps(_event("orphan")) + "\n", "utf-8"
    )
    assert research_event_generation_paths(str(tmp_path)) == [str(active)]
    assert not event_already_written("orphan", data_dir=str(tmp_path))


@pytest.mark.parametrize("failpoint", ["AFTER_PREPARED", "AFTER_RENAME", "AFTER_SEAL"])
def test_rotation_restart_recovery_is_hash_bound(tmp_path, failpoint):
    assert write_research_event_once(_event("durable"), data_dir=str(tmp_path))[0]
    with pytest.raises(RuntimeError, match="V22_ROTATION_FAILPOINT"):
        rotate_research_events(data_dir=str(tmp_path), failpoint=failpoint)
    if failpoint != "AFTER_SEAL":
        with pytest.raises(RuntimeError, match="V22_ROTATION_IN_PROGRESS"):
            research_event_generation_paths(str(tmp_path))
    # A normal writer/reconcile is the restart boundary and must recover first.
    assert event_already_written("durable", data_dir=str(tmp_path))
    assert write_research_event_once(_event("new"), data_dir=str(tmp_path))[0]
    rows = []
    for path in research_event_generation_paths(str(tmp_path)):
        if os.path.isfile(path):
            rows.extend(json.loads(line) for line in open(path, encoding="utf-8"))
    assert sorted(row["event_id"] for row in rows) == ["durable", "new"]


def test_writer_cannot_cross_rotation_lock_boundary(tmp_path, monkeypatch):
    assert write_research_event_once(_event("before"), data_dir=str(tmp_path))[0]
    renamed = threading.Event()
    release = threading.Event()
    real_replace = collector_v22.os.replace

    def controlled_replace(source, target):
        result = real_replace(source, target)
        if source == str(tmp_path / RESEARCH_EVENTS_FILE):
            renamed.set()
            assert release.wait(2.0)
        return result

    monkeypatch.setattr(collector_v22.os, "replace", controlled_replace)
    rotation = threading.Thread(target=rotate_research_events, kwargs={"data_dir": str(tmp_path)})
    rotation.start()
    assert renamed.wait(1.0)
    written = []
    writer = threading.Thread(
        target=lambda: written.append(write_research_event_once(_event("concurrent"), data_dir=str(tmp_path)))
    )
    writer.start()
    time.sleep(0.05)
    assert writer.is_alive()
    release.set()
    rotation.join(2.0)
    writer.join(2.0)
    assert written and written[0][0]
    sealed = (tmp_path / f"{RESEARCH_EVENTS_FILE}.1").read_text("utf-8")
    active = (tmp_path / RESEARCH_EVENTS_FILE).read_text("utf-8")
    assert '"before"' in sealed and '"concurrent"' not in sealed
    assert '"concurrent"' in active


def test_hash_tamper_revokes_sealed_generation_authority(tmp_path):
    assert write_research_event_once(_event("sealed"), data_dir=str(tmp_path))[0]
    rotate_research_events(data_dir=str(tmp_path))
    sealed = tmp_path / f"{RESEARCH_EVENTS_FILE}.1"
    sealed.write_bytes(sealed.read_bytes() + b"{}\n")
    with pytest.raises(RuntimeError, match="V22_SEAL_INTEGRITY_FAILED"):
        research_event_generation_paths(str(tmp_path))


def test_rotation_refuses_malformed_tail_without_moving_active(tmp_path):
    active = tmp_path / RESEARCH_EVENTS_FILE
    active.write_bytes((json.dumps(_event("good")) + "\n{bad}\n").encode("utf-8"))
    with pytest.raises(RuntimeError, match="V22_(ROTATION|EVENT_INDEX)_JSON_INVALID"):
        rotate_research_events(data_dir=str(tmp_path))
    assert active.is_file()
    assert not (tmp_path / f"{RESEARCH_EVENTS_FILE}.1").exists()
    assert research_event_generation_paths(str(tmp_path)) == [str(active)]


def test_noncanonical_generation_receipt_name_fails_closed(tmp_path):
    seal_dir = tmp_path / "research_events_v22.seals"
    seal_dir.mkdir()
    (seal_dir / "generation-01.json").write_text("{}", "utf-8")
    with pytest.raises(RuntimeError, match="V22_SEAL_RECEIPT_NAME_NONCANONICAL"):
        research_event_generation_paths(str(tmp_path))


def test_same_size_sealed_tamper_invalidates_cached_authority(tmp_path):
    assert write_research_event_once(_event("sealed"), data_dir=str(tmp_path))[0]
    rotate_research_events(data_dir=str(tmp_path))
    sealed = tmp_path / f"{RESEARCH_EVENTS_FILE}.1"
    assert len(research_event_generation_paths(str(tmp_path))) == 2
    payload = bytearray(sealed.read_bytes())
    payload[payload.index(b"sealed")] = ord("x")
    old_mtime = sealed.stat().st_mtime_ns
    sealed.write_bytes(payload)
    os.utime(sealed, ns=(old_mtime + 1_000_000_000, old_mtime + 1_000_000_000))
    with pytest.raises(RuntimeError, match="V22_SEAL_INTEGRITY_FAILED"):
        research_event_generation_paths(str(tmp_path))


def test_conflicting_duplicate_across_generations_fails_closed(tmp_path):
    assert write_research_event_once(_event("duplicate"), data_dir=str(tmp_path))[0]
    rotate_research_events(data_dir=str(tmp_path))
    changed = _event("duplicate")
    changed["exact_reason"] = "DIFFERENT"
    (tmp_path / RESEARCH_EVENTS_FILE).write_text(json.dumps(changed) + "\n", "utf-8")
    with pytest.raises(RuntimeError, match="V22_EVENT_ID_CONFLICT"):
        event_already_written("duplicate", data_dir=str(tmp_path))


def test_exact_duplicate_across_generations_is_deduplicated(tmp_path):
    assert write_research_event_once(_event("duplicate"), data_dir=str(tmp_path))[0]
    rotate_research_events(data_dir=str(tmp_path))
    sealed_payload = (tmp_path / f"{RESEARCH_EVENTS_FILE}.1").read_bytes()
    (tmp_path / RESEARCH_EVENTS_FILE).write_bytes(sealed_payload)
    assert event_already_written("duplicate", data_dir=str(tmp_path))
    with sqlite3.connect(tmp_path / EVENT_SQLITE_INDEX_FILE) as index:
        assert index.execute("SELECT event_id FROM events").fetchall() == [("duplicate",)]


def test_steady_state_append_does_not_rescan_ledger(tmp_path, monkeypatch):
    assert write_research_event_once(_event("first"), data_dir=str(tmp_path))[0]
    monkeypatch.setattr(
        collector_v22,
        "_scan_durable_event_rows_with_count",
        lambda *args, **kwargs: pytest.fail("steady-state append rescanned ledger"),
    )
    assert write_research_event_once(_event("second"), data_dir=str(tmp_path))[0]


def test_second_append_never_loads_or_saves_legacy_json_or_rebuilds(tmp_path, monkeypatch):
    assert write_research_event_once(_event("first"), data_dir=str(tmp_path))[0]
    monkeypatch.setattr(collector_v22, "_load_event_index", lambda *a, **k: pytest.fail("legacy JSON loaded"))
    monkeypatch.setattr(collector_v22, "_save_event_index", lambda *a, **k: pytest.fail("legacy JSON saved"))
    monkeypatch.setattr(collector_v22, "_rebuild_sqlite_event_index", lambda *a, **k: pytest.fail("index rebuilt"))
    monkeypatch.setattr(collector_v22, "_scan_durable_event_rows_with_count", lambda *a, **k: pytest.fail("JSONL scanned"))
    assert write_research_event_once(_event("second"), data_dir=str(tmp_path))[0]


def test_legacy_json_is_preserved_and_exact_hash_coverage_receipted(tmp_path):
    encoded = (json.dumps(_event("legacy"), separators=(",", ":")) + "\n").encode("utf-8")
    (tmp_path / RESEARCH_EVENTS_FILE).write_bytes(encoded)
    legacy = {
        "schema": "research_event_index_v1",
        "events": {"legacy": {"row_sha256": collector_v22.hashlib.sha256(encoded).hexdigest()}},
    }
    legacy_path = tmp_path / "research_events_v22.index.json"
    legacy_bytes = json.dumps(legacy, sort_keys=True).encode("utf-8")
    legacy_path.write_bytes(legacy_bytes)
    assert event_already_written("legacy", data_dir=str(tmp_path))
    assert legacy_path.read_bytes() == legacy_bytes
    with sqlite3.connect(tmp_path / EVENT_SQLITE_INDEX_FILE) as index:
        meta = {key: json.loads(value) for key, value in index.execute("SELECT key,value FROM metadata")}
    assert meta["legacy_json_preserved"] is True
    assert meta["legacy_coverage_proven"] is True
    assert meta["exact_identity_count"] == 1


def test_all_v22_analysis_readers_see_sealed_and_active(tmp_path, monkeypatch):
    assert write_research_event_once(_event("sealed-reader"), data_dir=str(tmp_path))[0]
    rotate_research_events(data_dir=str(tmp_path))
    assert write_research_event_once(_event("active-reader"), data_dir=str(tmp_path))[0]

    from replay_event_report import _load_events
    from research.best_policy_research import _events
    from research.policy_candidate_oos import _read_events
    from research.policy_cycle_snapshot import load_policy_cycle_snapshot
    monkeypatch.delenv("BTC_AGENT_DATA_DIR", raising=False)
    from research import research_dashboard

    expected = {"sealed-reader", "active-reader"}
    assert {row["event_id"] for row in _load_events(str(tmp_path))} == expected
    active = tmp_path / RESEARCH_EVENTS_FILE
    assert {row["event_id"] for row in _events(active)} == expected
    assert {row["event_id"] for row in _read_events(active)} == expected
    with mock.patch(
        "research.v3_policy_report_adapter.has_v3_evidence", return_value=False
    ):
        snapshot = load_policy_cycle_snapshot(str(tmp_path))
    assert {row["event_id"] for row in snapshot["events"]} == expected
    monkeypatch.setattr(research_dashboard, "_data_file_candidates", lambda name: [active])
    assert {
        row["event_id"] for row in research_dashboard._read_research_events_v22()
    } == expected


def test_process_shared_lock_blocks_second_writer(tmp_path):
    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    holder = context.Process(
        target=_hold_cross_process_writer_lock,
        args=(str(tmp_path), ready, release),
    )
    holder.start()
    assert ready.wait(3.0)
    result = []
    writer = threading.Thread(
        target=lambda: result.append(
            write_research_event_once(_event("cross-process"), data_dir=str(tmp_path))
        )
    )
    writer.start()
    time.sleep(0.1)
    assert writer.is_alive()
    release.set()
    writer.join(3.0)
    holder.join(3.0)
    assert holder.exitcode == 0
    assert result and result[0][0]


def test_cross_process_duplicate_is_idempotent_and_conflict_fails_closed(tmp_path):
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    first = context.Process(target=_cross_process_write, args=(str(tmp_path), _event("shared"), results))
    first.start()
    first.join(5.0)
    assert first.exitcode == 0
    first_result = results.get(timeout=1.0)
    assert first_result[0] == "ok" and first_result[1][0] is True
    duplicate = context.Process(target=_cross_process_write, args=(str(tmp_path), _event("shared"), results))
    duplicate.start()
    duplicate.join(5.0)
    assert duplicate.exitcode == 0
    assert results.get(timeout=1.0) == ("ok", (False, "duplicate event_id"))
    changed = _event("shared")
    changed["exact_reason"] = "CONFLICT"
    conflict = context.Process(target=_cross_process_write, args=(str(tmp_path), changed, results))
    conflict.start()
    conflict.join(5.0)
    assert conflict.exitcode == 0
    outcome = results.get(timeout=1.0)
    assert outcome[:2] == ("error", "RuntimeError")
    assert outcome[2] == "V22_EVENT_ID_CONFLICT:shared"

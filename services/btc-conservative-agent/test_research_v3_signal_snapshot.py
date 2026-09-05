import copy
import json

import pytest

from collector_signal_snapshot import freeze_signal_snapshot
from research_v3_bridge import dual_write_provisional_source, dual_write_v22_record
from test_research_v3_bridge import _event


def fixture(tmp_path, event_id="cont-1"):
    record = _event(event_id=event_id)
    record["research_signal_snapshot_ref"] = freeze_signal_snapshot(
        record, data_dir=tmp_path, captured_at=1001,
    )
    return record


def provisional(record):
    return {"created_ts_ts": 1000, "shared_ai_call_id": "scan-1",
            "research_signal_snapshot_ref": record.get("research_signal_snapshot_ref")}


def rows(tmp_path):
    return [json.loads(line) for line in (tmp_path / "v3" / "ledgers" / "lifecycle.jsonl").read_text().splitlines()]


def test_provisional_and_terminal_keep_verified_event_dependency(tmp_path):
    record = fixture(tmp_path)
    before = copy.deepcopy(record)
    dual_write_provisional_source(record["event_id"], provisional(record), epoch_id=record["epoch_id"], data_dir=str(tmp_path))
    dual_write_v22_record(record, data_dir=str(tmp_path))
    events = rows(tmp_path)
    for suffix in ("opened", "terminal"):
        row = next(r for r in events if r["record_id"] == f"lifecycle:cont-1:{suffix}")
        assert row["research_signal_snapshot_ref"] == record["research_signal_snapshot_ref"]
        assert row["signal_ts"] == record["envelope"]["signal_ts"]
        assert "pre_signal_context" not in row
    assert record == before


def test_shared_opportunity_does_not_drop_separate_event_dependencies(tmp_path):
    records = [fixture(tmp_path, event_id=event_id) for event_id in ("lane-1", "lane-2")]
    for record in records:
        dual_write_provisional_source(record["event_id"], provisional(record), epoch_id=record["epoch_id"], data_dir=str(tmp_path))
    lifecycle = rows(tmp_path)
    assert len(lifecycle) == 2
    assert {r["research_signal_snapshot_ref"]["identity"]["event_id"] for r in lifecycle} == {"lane-1", "lane-2"}
    opportunities = (tmp_path / "v3" / "ledgers" / "opportunity.jsonl").read_text().splitlines()
    assert len(opportunities) == 1


@pytest.mark.parametrize("kind", ["provisional", "terminal"])
@pytest.mark.parametrize("defect", ["missing", "hash", "event", "epoch", "time", "path"])
def test_invalid_dependency_refused_before_any_ledger_append(tmp_path, kind, defect):
    record = fixture(tmp_path)
    ref = record["research_signal_snapshot_ref"]
    path = tmp_path / ref["relative_path"]
    if defect == "missing":
        path.unlink()
    elif defect == "hash":
        path.write_bytes(b"bad")
    elif defect == "path":
        ref["relative_path"] = "../other.json"
    else:
        field = {"event": "event_id", "epoch": "epoch_id", "time": "signal_ts"}[defect]
        ref["identity"][field] = 1001 if defect == "time" else "other"
    with pytest.raises((ValueError, FileNotFoundError)):
        if kind == "provisional":
            dual_write_provisional_source(record["event_id"], provisional(record), epoch_id=record["epoch_id"], data_dir=str(tmp_path))
        else:
            dual_write_v22_record(record, data_dir=str(tmp_path))
    assert not (tmp_path / "v3" / "ledgers").exists()


def test_legacy_missing_reference_is_not_invented(tmp_path):
    record = _event()
    dual_write_provisional_source(record["event_id"], {"created_ts_ts": 1000, "shared_ai_call_id": "scan-1"},
                                  epoch_id=record["epoch_id"], data_dir=str(tmp_path))
    dual_write_v22_record(record, data_dir=str(tmp_path))
    assert all("research_signal_snapshot_ref" not in r for r in rows(tmp_path))
    assert all("signal_ts" not in r for r in rows(tmp_path))

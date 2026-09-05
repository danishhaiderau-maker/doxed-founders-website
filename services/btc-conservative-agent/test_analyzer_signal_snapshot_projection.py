import copy
import json

import pytest

from collector_v22 import build_research_event
from collector_signal_snapshot import freeze_signal_snapshot
from research.entry_baseline_replay import materialize_v3_opportunity_replay
from research.baseline_execution_context import VerifiedLedgerRowIndex
from research.policy_evidence_schema import canonical_json
from research_v3_contract import canonical_hash
from test_baseline_execution_context_integration import _dataset, _repin_dataset


def make_dataset(root, *, events=1, suffix="", duplicate_terminal=False):
    _dataset(root, context=False)
    opportunity = json.loads((root / "v3/ledgers/opportunity.jsonl").read_text())
    state_path = root / ".fly-sync-state.json"
    state = json.loads(state_path.read_text())
    rows = []
    refs = []
    for number in range(events):
        event_id = f"event-{number + 1}"
        record = build_research_event(trade_id=event_id, epoch_id="epoch-1", signal_ts=100 + number,
                    signal_price=100, rsi_at_signal=42 + number, atr14_pct=.3,
                    feature_snapshot={"adx": 27 + number})
        ref = freeze_signal_snapshot(record, data_dir=root, captured_at=105 + number)
        refs.append(ref)
        state[ref["relative_path"]] = {}
        row = {"record_id": f"lifecycle:{event_id}:opened", "epoch_id": "epoch-1",
               "episode_id": opportunity["episode_id"], "event_id": event_id,
               "signal_ts": 100 + number, "research_signal_snapshot_ref": ref}
        rows.append(row)
        if duplicate_terminal:
            rows.append({**copy.deepcopy(row), "record_id": f"lifecycle:{event_id}:terminal"})
    relative = "v3/ledgers/lifecycle.jsonl" + suffix
    (root / relative).write_text("".join(canonical_json(row) + "\n" for row in rows))
    state[relative] = {}
    state_path.write_text(json.dumps(state))
    return _repin_dataset(root), relative, rows, refs


def project(root, generation):
    return materialize_v3_opportunity_replay(root, generation=generation)


def evidence(report):
    return report["episode_receipts"][0]["signal_snapshot_evidence"]


@pytest.mark.parametrize("suffix", ["", ".1"])
def test_normal_materializer_preserves_all_event_contexts_and_provenance(tmp_path, suffix):
    generation, relative, _, refs = make_dataset(tmp_path, events=2, suffix=suffix, duplicate_terminal=True)
    report = project(tmp_path, generation)
    projection = evidence(report)
    assert projection["status"] == "VERIFIED_FIRST_COLLECTOR_CAPTURE"
    assert len(projection["contexts"]) == 2
    assert [context["fields"]["rsi_at_signal"] for context in projection["contexts"]] == [42, 43]
    for context, ref in zip(projection["contexts"], refs):
        assert context["reference"] == ref
        assert context["capture_basis"] == "FIRST_COLLECTOR_CAPTURE"
        assert context["availability_at_signal_verified"] is False
        assert context["observed_at_signal_claim"] is False
        assert len(context["source_lifecycle_rows"]) == 2
        assert context["source_lifecycle_rows"][0]["source_id"] == relative
    assert projection["fill_atr_authority"] is False
    assert report["signal_snapshot_coverage"] == {"VERIFIED_FIRST_COLLECTOR_CAPTURE": 1}
    assert report["report_id"] == canonical_hash("entry-baseline-replay", {k:v for k,v in report.items() if k != "report_id"})


@pytest.mark.parametrize("defect", ["missing", "tamper", "unpinned", "size", "event", "epoch", "time", "path"])
def test_declared_snapshot_defects_are_unknown_not_legacy_unavailable(tmp_path, defect):
    generation, relative, rows, refs = make_dataset(tmp_path)
    ref = refs[0]
    path = tmp_path / ref["relative_path"]
    if defect == "missing":
        path.unlink()
    elif defect == "tamper":
        path.write_bytes(b"bad")
    elif defect == "unpinned":
        state_path = tmp_path / ".fly-sync-state.json"
        state = json.loads(state_path.read_text()); state.pop(ref["relative_path"])
        state_path.write_text(json.dumps(state)); generation = _repin_dataset(tmp_path)
    else:
        if defect == "size": ref["bytes"] += 1
        elif defect == "path": ref["relative_path"] = "../outside.json"
        else:
            field = {"event":"event_id", "epoch":"epoch_id", "time":"signal_ts"}[defect]
            ref["identity"][field] = 101 if defect == "time" else "other"
        (tmp_path / relative).write_text(canonical_json(rows[0]) + "\n")
        generation = _repin_dataset(tmp_path)
    projection = evidence(project(tmp_path, generation))
    assert projection["status"] == "UNKNOWN"
    assert projection["contexts"][0]["reason_codes"]


def test_legacy_absent_reference_is_unavailable_and_cannot_change_fill_results(tmp_path):
    generation, relative, rows, _ = make_dataset(tmp_path)
    before = project(tmp_path, generation)
    rows[0].pop("research_signal_snapshot_ref")
    (tmp_path / relative).write_text(canonical_json(rows[0]) + "\n")
    generation = _repin_dataset(tmp_path)
    after = project(tmp_path, generation)
    assert evidence(after)["status"] == "UNAVAILABLE"
    assert before["summaries"] == after["summaries"]
    assert evidence(after)["reason_codes"] == ["SIGNAL_SNAPSHOT_NO_DECLARED_REFERENCE"]


def test_same_event_conflicting_timestamp_does_not_silently_deduplicate(tmp_path):
    generation, relative, rows, _ = make_dataset(tmp_path, duplicate_terminal=True)
    rows[1]["signal_ts"] += 1
    (tmp_path / relative).write_text("".join(canonical_json(row) + "\n" for row in rows))
    projection = evidence(project(tmp_path, _repin_dataset(tmp_path)))
    assert projection["status"] == "UNKNOWN"
    assert "SIGNAL_SNAPSHOT_EVENT_REFERENCE_CONFLICT" in projection["reason_codes"]


def test_unverified_lifecycle_membership_cannot_supply_valid_snapshot(tmp_path):
    generation, relative, rows, _ = make_dataset(tmp_path)
    (tmp_path / relative).write_text(canonical_json({**rows[0], "extra": "unbound"}) + "\n")
    projection = evidence(project(tmp_path, generation))
    assert projection["status"] == "UNKNOWN"
    assert projection["reason_codes"] == ["SIGNAL_SNAPSHOT_LIFECYCLE_SOURCE_NOT_VERIFIED"]


def test_snapshot_changed_after_verification_cannot_publish(tmp_path, monkeypatch):
    import collector_signal_snapshot
    generation, _, _, refs = make_dataset(tmp_path)
    original = collector_signal_snapshot.load_signal_snapshot
    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        with (tmp_path / refs[0]["relative_path"]).open("ab") as handle:
            handle.write(b" ")
        return result
    monkeypatch.setattr(collector_signal_snapshot, "load_signal_snapshot", mutate)
    with pytest.raises(ValueError, match="SOURCE_CHANGED_DURING_REPLAY"):
        project(tmp_path, generation)


def test_group_resource_limit_refuses_instead_of_sampling(tmp_path, monkeypatch):
    generation, _, _, _ = make_dataset(tmp_path)
    def refuse(*args):
        raise ValueError("SIGNAL_SNAPSHOT_LIFECYCLE_GROUP_LIMIT")
    monkeypatch.setattr(VerifiedLedgerRowIndex, "lifecycle_envelopes", refuse)
    projection = evidence(project(tmp_path, generation))
    assert projection["status"] == "UNKNOWN"
    assert projection["contexts"] == []


def test_real_129_row_group_refuses_complete_projection(tmp_path):
    _, relative, rows, _ = make_dataset(tmp_path)
    payload = "".join(canonical_json({**rows[0], "record_id": f"lifecycle:event-1:{i}"}) + "\n"
                      for i in range(129))
    (tmp_path / relative).write_text(payload)
    projection = evidence(project(tmp_path, _repin_dataset(tmp_path)))
    assert projection["status"] == "UNKNOWN"
    assert projection["contexts"] == []
    assert projection["reason_codes"] == ["SIGNAL_SNAPSHOT_LIFECYCLE_GROUP_LIMIT"]

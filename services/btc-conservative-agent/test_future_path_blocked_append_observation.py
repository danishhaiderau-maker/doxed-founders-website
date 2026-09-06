"""Characterize computed coverage versus terminal append disposition."""
import json

import pytest

from research_v3_store import V3EvidenceStore
from research_v3_future_paths import mature_future_market_paths
from test_research_v3_future_paths import _seed, _write_tape, EPOCH, SIGNAL_TS


@pytest.mark.parametrize("disposition", ["blocked", "deferred", "conflict"])
def test_nonraising_terminal_append_does_not_change_computed_complete_count(tmp_path, monkeypatch, disposition):
    _seed(tmp_path, outcomes=("REJECTED",))
    _write_tape(tmp_path)
    original = V3EvidenceStore.append
    intercepted = []
    def append(self, ledger, material, *args, **kwargs):
        if ledger == "market_segment" and material.get("segment_role") == "SIGNAL_TO_120M_FUTURE_PATH":
            intercepted.append(material)
            if disposition == "conflict": return {"written":True,"blocked":True}
            return {"written":False, "duplicate":False, disposition:True,
                    "record_id":material["record_id"], "ledger":ledger}
        return original(self, ledger, material, *args, **kwargs)
    monkeypatch.setattr(V3EvidenceStore, "append", append)
    result = mature_future_market_paths(data_dir=tmp_path, epoch_id=EPOCH,
                                       now_ts=SIGNAL_TS+7300, max_batch=8)
    assert result["complete_count"] == 1 and result["unknown_count"] == 0
    assert result["terminal_append_dispositions"]["unknown" if disposition == "conflict" else disposition] == 1
    assert result["terminal_append_attempted_count"] == 1
    assert sum(result["terminal_append_dispositions"].values()) == 1
    assert result["terminal_append_authority"] == "APPEND_DISPOSITION_ONLY_NOT_QUALIFICATION"
    assert len(intercepted) == 1
    if disposition != "conflict":
        assert any(write.get(disposition) is True and write["written"] is False for write in result["writes"])
    persisted = [json.loads(line) for line in (tmp_path/"v3"/"ledgers"/"market_segment.jsonl").read_text().splitlines()]
    assert not any(row.get("segment_role") == "SIGNAL_TO_120M_FUTURE_PATH" for row in persisted)
    cursor = json.loads((tmp_path/"v3"/"receipts"/"future-path-cursor.json").read_text())
    assert cursor["cursor"] == result["cursor"]
    # The selection is rebuilt from terminal records, not permanently skipped by cursor.
    second = mature_future_market_paths(data_dir=tmp_path, epoch_id=EPOCH,
                                       now_ts=SIGNAL_TS+7400, max_batch=8)
    assert len(intercepted) == 2 and second["mature_selected"] == 1


def test_identity_unknown_terminal_is_accounted_separately_from_mature_selected(tmp_path):
    V3EvidenceStore(tmp_path, epoch_id=EPOCH).append("decision", {
        "record_id":"decision:orphan", "episode_id":"missing", "event_id":"orphan", "primary_outcome":"REJECTED"})
    result = mature_future_market_paths(data_dir=tmp_path, epoch_id=EPOCH,now_ts=SIGNAL_TS+9000)
    assert result["mature_selected"] == 0 and result["unknown_count"] == 1
    assert result["terminal_append_attempted_count"] == 1
    assert result["terminal_append_dispositions"]["written"] == 1

import json
import pytest
import research_v3_bridge as bridge


@pytest.mark.parametrize("receipt, expected", [
    ({"written": True}, "reconciled"),
    ({"written": False, "duplicate": True}, "duplicates"),
    ({"written": False, "deferred": True}, "deferred"),
    ({"written": False, "deferred": True, "duplicate": True}, "deferred"),
    ({"written": False, "blocked": True}, "blocked"),
    ({"written": False}, "unwritten"),
])
def test_reconciliation_counts_exact_append_disposition(tmp_path, monkeypatch, receipt, expected):
    decision = {"epoch_id": "epoch", "episode_id": "episode", "policy_signature": "policy",
                "research_lane": "lane", "order_intent_expected": True,
                "resolution_deadline_ts": 1}
    (tmp_path / "decision.jsonl").write_text(json.dumps(decision) + "\n")
    calls = []
    class Store:
        def __init__(self, *args, **kwargs): pass
        def ledger_path(self, name): return tmp_path / (name + ".jsonl")
        def append(self, name, row):
            calls.append(row)
            return receipt
    monkeypatch.setattr(bridge, "V3EvidenceStore", Store)
    result = bridge.reconcile_overdue_expected_order_decisions(epoch_id="epoch", data_dir=str(tmp_path), observed_ts=2)
    assert len(calls) == 1 and result["expected"] == 1
    for field in ("reconciled", "duplicates", "deferred", "blocked", "unwritten"):
        assert result[field] == int(field == expected)
    assert not (tmp_path / "lifecycle.jsonl").exists()

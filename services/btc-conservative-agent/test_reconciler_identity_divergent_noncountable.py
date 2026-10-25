"""Split from #882 (RK4): identity-divergent episodes are skipped and non-countable."""
import json

import research_v3_bridge as bridge


def test_identity_divergent_episode_is_skipped_and_marked_non_countable(tmp_path, monkeypatch):
    def decision(ep):
        return {"epoch_id": "e", "episode_id": ep, "policy_signature": "sig-decision",
                "research_lane": "lane", "order_intent_expected": True, "resolution_deadline_ts": 1}
    (tmp_path / "decision.jsonl").write_text("".join(json.dumps(decision(ep)) + "\n" for ep in ("div", "lost")))
    (tmp_path / "order_intent.jsonl").write_text(json.dumps(
        {"epoch_id": "e", "episode_id": "div", "policy_signature": "sig-order", "research_lane": "lane"}) + "\n")
    appended = []

    class Store:
        def __init__(self, *args, **kwargs): pass
        def ledger_path(self, name): return tmp_path / (name + ".jsonl")
        def append(self, name, row):
            appended.append(row["episode_id"])
            return {"written": True}

    monkeypatch.setattr(bridge, "V3EvidenceStore", Store)
    result = bridge.reconcile_overdue_expected_order_decisions(epoch_id="e", data_dir=str(tmp_path), observed_ts=5)
    assert appended == ["lost"]
    assert result["identity_divergent"] == 1 and result["reconciled"] == 1
    assert result["non_countable"] == [{
        "episode_id": "div", "research_lane": "LANE", "policy_signature": "sig-decision",
        "countable": False, "reason": "DECISION_ORDER_POLICY_SIGNATURE_DIVERGENT"}]

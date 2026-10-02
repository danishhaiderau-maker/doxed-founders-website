from __future__ import annotations

from collections import Counter

import research_v3_candidates as rc
from research_v3_contract import canonical_hash


def _source():
    prices = [{"bucket_ts": 1000 + i, "ts": 1000 + i, "price": 100.0 + (i % 7) * 0.05} for i in range(120)]
    children = []
    for chase, fill_ts, fill_price in (
        ("C2", 1005.0, 100.1), ("C3", 1005.0, 100.1), ("C4", 1009.0, 100.2),
    ):
        children.append({
            "entry_policy_id": f"ENTRY_OFF_0.3_CHASE_{chase}",
            "offset_pct": 0.3, "chase_id": chase, "fill_model": "IDEAL",
            "fill_ts": fill_ts, "fill_price": fill_price,
        })
    return {
        "episode_id": "ep-1", "event_id": "ev-1", "epoch_id": "epoch-1", "opportunity_id": "opp-1",
        "tape_ids": ["tape-1"], "signal_ts": 1000.0, "direction": "LONG", "atr14_pct": 0.2,
        "leverage": 100, "margin_usd": 0.25, "ordered_1s_prices": prices, "entry_children": children,
    }


def _fill_receipt(source, child, *, microstructure_by_ts):
    return {
        "outcome": "FILL", "trigger_bucket_ts": child["fill_ts"], "fill_price": child["fill_price"],
        "requested_qty": 1.0, "filled_qty": 1.0, "schedule_sha256": "sched-1",
        "chase_bucket_id": "b-1", "evidence_bucket_ids": ["b-1"],
    }


def test_each_distinct_replay_input_is_replayed_once_and_every_input_is_covered(monkeypatch):
    protections = rc.protection_screen()[:3]
    monkeypatch.setattr(rc, "protection_screen", lambda: protections)
    monkeypatch.setattr(rc, "_conservative_child_receipt", _fill_receipt)
    calls = []
    original_cell = rc.replay_cell

    class RecordingArrays(rc.PreparedReplayArrays):
        def __init__(self, ordered, *, direction, entry_price, leverage, fill_ts):
            super().__init__(ordered, direction=direction, entry_price=entry_price,
                             leverage=leverage, fill_ts=fill_ts)
            self.inputs = (entry_price, fill_ts)

    def recording(path, plan, **kwargs):
        calls.append((*path.inputs, kwargs["margin_usd"], plan.floor_key, plan.hard_stop,
                      plan.atr_sl, plan.atr_tp, plan.time_stop_sec))
        return original_cell(path, plan, **kwargs)

    monkeypatch.setattr(rc, "PreparedReplayArrays", RecordingArrays)
    monkeypatch.setattr(rc, "replay_cell", recording)
    source = _source()
    screen = rc.evaluate_protection_screen([source])
    assert screen["input_events"] == 1
    # Both worlds replay the same fill once each; nothing else is replayed.
    expected = Counter()
    for fill_price, fill_ts in {(c["fill_price"], c["fill_ts"]) for c in source["entry_children"]}:
        for protection in protections:
            plan = rc.ReplayPlan(protection)
            expected[(fill_price, fill_ts, 0.25, plan.floor_key, plan.hard_stop,
                      plan.atr_sl, plan.atr_tp, plan.time_stop_sec)] += 2
    assert Counter(calls) == expected
    assert len(calls) == 2 * 2 * len(protections)


def test_receipt_binder_binds_each_signature_like_a_fresh_identity():
    source = _source()
    receipt = _fill_receipt(source, source["entry_children"][0], microstructure_by_ts={})
    identity_for, bind = rc._candidate_receipt_binder(receipt, source)
    first, second = bind("sig-a"), bind("sig-b")
    assert first["identity"]["candidate_policy_signature"] == "sig-a"
    assert second["identity"]["candidate_policy_signature"] == "sig-b"
    assert first["identity"]["fill_receipt_id"] == canonical_hash("candidate-fill", {
        "epoch_id": "epoch-1", "event_id": "ev-1", "episode_id": "ep-1", "policy_signature": "sig-a",
        "schedule_sha256": "sched-1", "tape_ids": ["tape-1"], "chase_bucket_id": "b-1",
        "evidence_bucket_ids": ["b-1"], "trigger_bucket_ts": 1005.0, "filled_qty": 1.0, "fill_price": 100.1,
    })
    assert first["identity"]["fill_receipt_id"] != second["identity"]["fill_receipt_id"]
    assert first["identity"]["complete"] is True and first["outcome"] == "FILL"
    first["identity"]["tape_ids"].append("mutated")
    assert bind("sig-a")["identity"]["tape_ids"] == ["tape-1"]

    incomplete = dict(source, opportunity_id=None)
    bound = rc._bind_candidate_receipt_identity(receipt, incomplete, candidate_policy_signature="sig-a")
    assert bound["outcome"] == "UNSUPPORTED" and bound["supported"] is False
    assert bound["identity"]["fill_receipt_id"] is None
    assert bound["identity"]["missing_required_identities"] == ["opportunity_id"]
    assert "MISSING_REQUIRED_IDENTITY:opportunity_id" in bound["negative_reasons"]

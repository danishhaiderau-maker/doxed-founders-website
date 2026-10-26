import json
import os
import sys

import pytest

import research_v3_bridge as bridge

# bridge.json is the stdlib json module: patching .loads is process-global. Count only
# frames from the overdue index / overdue_decisions path so full-suite noise cannot
# inflate the second-pass assert (CI saw 4 or 7 instead of 2).
_OVERDUE_LOAD_FRAMES = frozenset({"_overdue_reconcile_index", "overdue_decisions"})


def _store(tmp_path, appended):
    class Store:
        def __init__(self, *a, **k): pass
        def ledger_path(self, name): return tmp_path / (name + ".jsonl")
        def append(self, name, row):
            appended.append(row)
            return {"blocked": True, "reason": "TEST"}
    return Store


def _decision(ep, deadline=1):
    return {"epoch_id": "e", "episode_id": ep, "policy_signature": "p",
            "research_lane": "lane", "order_intent_expected": True,
            "resolution_deadline_ts": deadline, "record_id": f"decision:{ep}"}


def _write(path, rows, mode="a"):
    with open(path, mode) as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_second_pass_decodes_only_appended_rows(tmp_path, monkeypatch):
    bridge._OVERDUE_INDEX_CACHE.clear()
    appended = []
    monkeypatch.setattr(bridge, "V3EvidenceStore", _store(tmp_path, appended))
    big = {"epoch_id": "e", "episode_id": "x", "policy_signature": "p",
           "research_lane": "lane", "blob": "z" * 5000}
    _write(tmp_path / "order_intent.jsonl", [big] * 200)
    _write(tmp_path / "decision.jsonl", [_decision("a"), _decision("b")])
    calls = {"n": 0}
    real = json.loads
    def counting(s, *a, **k):
        if sys._getframe(1).f_code.co_name in _OVERDUE_LOAD_FRAMES:
            calls["n"] += 1
        return real(s, *a, **k)
    monkeypatch.setattr(bridge.json, "loads", counting)
    first = bridge.reconcile_overdue_expected_order_decisions(epoch_id="e", data_dir=str(tmp_path), observed_ts=5)
    assert first["expected"] == 2 and first["blocked"] == 2
    first_calls = calls["n"]
    assert first_calls >= 202
    calls["n"] = 0
    # order intent for "a" arrives; only that one new row is decoded (+1 overdue decision re-read).
    _write(tmp_path / "order_intent.jsonl", [dict(big, episode_id="a")])
    second = bridge.reconcile_overdue_expected_order_decisions(epoch_id="e", data_dir=str(tmp_path), observed_ts=5)
    assert second["expected"] == 2 and second["resolved_or_active"] == 1 and second["blocked"] == 1
    assert calls["n"] == 2
    # the reconcile row content is unchanged from the full-scan implementation
    assert appended[-1]["record_id"].startswith("lifecycle:b:p:LANE:lane-entry:no-order:")
    assert appended[-1]["restart_recovery_provenance"]["source_decision_record_id"] == "decision:b"


def test_replaced_ledger_is_rescanned(tmp_path, monkeypatch):
    bridge._OVERDUE_INDEX_CACHE.clear()
    monkeypatch.setattr(bridge, "V3EvidenceStore", _store(tmp_path, []))
    _write(tmp_path / "decision.jsonl", [_decision("a")])
    _write(tmp_path / "lifecycle.jsonl", [{"epoch_id": "e", "episode_id": "a", "policy_signature": "p",
                                           "research_lane": "lane", "resolution_scope": "LANE_ENTRY",
                                           "entry_resolution": "NO_ORDER"}])
    r1 = bridge.reconcile_overdue_expected_order_decisions(epoch_id="e", data_dir=str(tmp_path), observed_ts=5)
    assert r1["resolved_or_active"] == 1
    tmp = tmp_path / "lifecycle.new"
    _write(tmp, [], mode="w")
    os.replace(tmp, tmp_path / "lifecycle.jsonl")  # rotation: new inode, empty
    r2 = bridge.reconcile_overdue_expected_order_decisions(epoch_id="e", data_dir=str(tmp_path), observed_ts=5)
    assert r2["resolved_or_active"] == 0 and r2["blocked"] == 1


def test_truncated_tail_fails_closed_without_advancing(tmp_path, monkeypatch):
    bridge._OVERDUE_INDEX_CACHE.clear()
    monkeypatch.setattr(bridge, "V3EvidenceStore", _store(tmp_path, []))
    _write(tmp_path / "decision.jsonl", [_decision("a")])
    with open(tmp_path / "decision.jsonl", "a") as fh:
        fh.write(json.dumps(_decision("b"))[:20])
    with pytest.raises(ValueError, match="TRUNCATED_JSONL_LINE:decision:2"):
        bridge.reconcile_overdue_expected_order_decisions(epoch_id="e", data_dir=str(tmp_path), observed_ts=5)
    with open(tmp_path / "decision.jsonl", "a") as fh:
        fh.write(json.dumps(_decision("b"))[20:] + "\n")
    r = bridge.reconcile_overdue_expected_order_decisions(epoch_id="e", data_dir=str(tmp_path), observed_ts=5)
    assert r["expected"] == 2 and r["blocked"] == 2

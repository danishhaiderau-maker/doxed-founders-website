"""Synthetic prospective model: collector buckets to normal bilateral replay.

R17: the bucket -> future-path worker -> bilateral chain is retired (no
market_segments). Its retired behaviour is pinned by
test_research_v3_future_paths_worker.test_worker_retired_r17_writes_no_segment_and_never_no_fill.
"""
import json


from research.entry_baseline_replay import materialize_v3_opportunity_replay
from test_declared_directional_context_integration import dataset, market_entries


def test_actual_direction_conflict_stays_rejected(tmp_path):
    generation, manifest = dataset(tmp_path)
    path = tmp_path/"v3/ledgers/opportunity.jsonl"
    row = json.loads(path.read_text())
    row["causal_identity"] = {"direction":"SHORT"}
    path.write_text(json.dumps(row)+"\n")
    report = materialize_v3_opportunity_replay(tmp_path)
    assert all("CONFLICTING_CAUSAL_IDENTITY:raw_ai_direction" in entry["rejection_codes"]
               for _, entry in market_entries(report))


import pytest as _pytest_r17
from research import r3_quarantine as _r3q_r17


@_pytest_r17.fixture(autouse=True)
def _legacy_limit_fill_1s_unquarantined(monkeypatch):
    # These tests pin the legacy 1 s limit-fill math; production has LIMIT_FILL_1S retired (R17, RK #847 cond 4),
    # covered by test_r17_retired_sources.py.  Kept (not deleted) pending the book_limit_entry rebuild.
    monkeypatch.setattr(_r3q_r17, "ACTIVE", set(_r3q_r17.ACTIVE) - {"LIMIT_FILL_1S"})

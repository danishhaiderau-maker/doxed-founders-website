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

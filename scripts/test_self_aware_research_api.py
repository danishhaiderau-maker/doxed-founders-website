"""Self-aware contract for the materialized research APIs: fresh, complete, sorted, reconciled, append-only."""
from __future__ import annotations

import gzip
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from self_aware import contracts as c  # noqa: E402


def _payload():
    return {"status": "OK", "generated_at": "2026-10-03T12:00:00Z", "age_sec": 60,
            "datasets": [{"name": "top_100_policies", "rows": 1}],
            "contract_inputs": {"research_layer_status": "OK", "families_expected": ["A"], "families_present_table": ["A"],
                                "families_present_totals": ["A"], "regimes_expected": ["trend"], "regimes_present": ["trend"],
                                "reconciliation": {"status": "PASS"}, "forward_chain_ok": True,
                                "sort_keys": {"top_100_policies": {"key": "net_oos_usd", "values": [0.6]}},
                                "top_100_totals": [{"policy_id": "P", "fills": 3, "wins": 2, "losses": 1, "net_pnl_usd": 1.0,
                                                    "net_in_sample_usd": 0.4, "net_oos_usd": 0.6}]}}


def _dir(tmp_path, net=1.0, tamper=False):
    row = {"policy_id": "P", "fill_world": "REALISTIC_V1", "all": {"net_pnl_usd": net, "fills": 3},
           "train": {"net_pnl_usd": 0.4}, "oos": {"net_pnl_usd": net - 0.4}}
    with gzip.open(tmp_path / "genome_grid_rows.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(row) + "\n")
    (tmp_path / "forward-tracker").mkdir()
    prev, lines = "GENESIS", []
    for i in range(2):
        line = json.dumps({"candidate_id": str(i), "prev_sha": prev}, sort_keys=True)
        prev = hashlib.sha256(line.encode()).hexdigest()
        lines.append(line)
    if tamper:
        lines[0] = lines[0].replace('"0"', '"9"')
    (tmp_path / "forward-tracker" / "frozen_candidates.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"genome_grid_dir": tmp_path, "genome_grid": {"generated_at": "2026-10-03T12:00:00Z"}}


def _kinds(obj, ctx):
    viol, met = c._rec_research_api_content(obj, ctx)
    return {v["kind"]: v["severity"] for v in viol}, met


def test_clean_cache_passes_and_is_registered(tmp_path):
    kinds, met = _kinds(_payload(), _dir(tmp_path))
    assert kinds == {} and met["research:top100_rows_matched"] == 1 and met["research:forward_chain_ok"] is True
    spec = next(s for s in c.load_registry()["contracts"] if s["id"] == "analyzer.research_api_content")
    assert spec["reconcile"] == "research_api_content" and "research_api_content" in c.RECONCILERS


def test_totals_drift_unsorted_stale_and_tampered_chain_fail(tmp_path):
    obj = _payload()
    obj["contract_inputs"]["sort_keys"]["top_100_policies"]["values"] = [0.1, 0.6]
    obj["contract_inputs"]["regimes_present"] = []
    ctx = _dir(tmp_path, net=1.5, tamper=True)
    ctx["genome_grid"]["generated_at"] = "2026-10-03T14:00:00Z"
    kinds, _ = _kinds(obj, ctx)
    assert kinds["RESEARCH_SORT_ORDER"] == kinds["RESEARCH_TOTALS_MISMATCH"] == kinds["FORWARD_CHAIN_BROKEN"] == "RED"
    assert kinds["RESEARCH_INCOMPLETE"] == kinds["RESEARCH_API_STALE"] == "AMBER"
    assert _kinds({"status": "UNAVAILABLE"}, {})[0] == {"RESEARCH_API_UNAVAILABLE": "RED"}
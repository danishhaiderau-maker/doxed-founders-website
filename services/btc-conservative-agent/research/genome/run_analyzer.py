"""Genome analyzer entry point."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

from research.genome.hypothesis_engine import generate_hypotheses
from research.genome.loader import load_all_layers
from research.genome.quality_score import summarize_trades
from research.genome.similarity import nearest_cluster


def run_genome_analyzer(db_path: str | None = None, out_dir: str | None = None) -> dict:
    agent_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    db = db_path or os.path.join(agent_root, "research.db")
    out = out_dir or os.path.join(agent_root, "research", "genome")
    os.makedirs(out, exist_ok=True)

    layers = load_all_layers(db)
    trades = layers.get("trade") or []
    markets = layers.get("market") or []

    combo_trades = [t for t in trades if "604" in str(t.get("research_lane", "")).upper()]
    cont_trades = [t for t in trades if str(t.get("research_lane", "")).upper() == "CONTINUOUS"]

    latest_market = markets[0] if markets else {}
    cluster_match = nearest_cluster(latest_market, [])

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1.0.0",
        "trade_summary": summarize_trades(trades),
        "combo_604": summarize_trades(combo_trades),
        "continuous_benchmark": summarize_trades(cont_trades),
        "cluster": cluster_match,
        "hypotheses": generate_hypotheses(combo_trades, cont_trades),
        "layer_counts": {k: len(v) for k, v in layers.items()},
    }

    out_path = os.path.join(out, "genome_analysis_report.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
    return report


if __name__ == "__main__":
    print(json.dumps(run_genome_analyzer(), indent=2))

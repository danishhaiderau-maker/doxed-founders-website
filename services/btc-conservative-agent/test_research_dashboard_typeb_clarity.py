"""Focused dashboard regressions for cohort clarity, ADX labels, and Genome paths."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DASHBOARD_SOURCE = ROOT / "research" / "research_dashboard.py"


def main() -> None:
    source = DASHBOARD_SOURCE.read_text(encoding="utf-8")
    assert "Executed-session P&amp;L" in source
    assert "Shadow-simulation P&amp;L" in source
    assert "<th>Shadow Sims</th>" not in source
    assert "function fmtAdxBucket" in source
    assert "ADX 18–<30" in source
    assert "s.delta_abs ?? s.abs_delta" in source
    assert 'id="typeb-rules-body"' in source

    research_dir = ROOT / "research"
    sys.path.insert(0, str(research_dir))
    import research_dashboard

    original_root = research_dashboard.ROOT
    original_data_root = research_dashboard.DATA_ROOT
    try:
        with tempfile.TemporaryDirectory() as tmp:
            agent_root = Path(tmp)
            genome_dir = agent_root / "research" / "genome"
            genome_dir.mkdir(parents=True)
            expected_genome = {
                "schema": "trading_genome_analysis_v1",
                "generated_at": "2026-07-25T00:00:00+00:00",
            }
            (genome_dir / "genome_analysis_report.json").write_text(
                json.dumps(expected_genome), encoding="utf-8"
            )
            report = {
                "schema": "type_b_predictor_v3",
                "classification": "TYPE_A: MFE<10% | TYPE_B: MFE>=15% | MIXED: between",
                "cohorts": {
                    "TYPE_B": {
                        "trades": 10,
                        "avg_mfe_pct": 21.5,
                        "pnl_usd": 12.0,
                        "ev_usd": 1.2,
                        "wr_pct": 80.0,
                    }
                },
                "top_separators": [{"feature": "adx", "delta_abs": 4.2}],
                "predictor_rules": [{"rule": "ADX 18–<30", "status": "COLLECTING"}],
                "predictor_readiness": {"status": "COLLECTING", "total_trades": 150},
            }
            (agent_root / "type_b_predictor_report.json").write_text(
                json.dumps(report), encoding="utf-8"
            )
            research_dashboard.ROOT = agent_root
            research_dashboard.DATA_ROOT = agent_root
            research_dashboard._API_RESPONSE_CACHE.clear()

            assert research_dashboard._genome_payload() == expected_genome
            payload = research_dashboard._typeb_payload()
            assert payload["cohorts"][0]["avg_mfe_pct"] == 21.5
            assert payload["separators"][0]["delta_abs"] == 4.2
            assert payload["rules"][0]["rule"] == "ADX 18–<30"
            assert payload["predictor_readiness"]["total_trades"] == 150
    finally:
        research_dashboard._API_RESPONSE_CACHE.clear()
        research_dashboard.ROOT = original_root
        research_dashboard.DATA_ROOT = original_data_root

    print("Research dashboard clarity tests passed")


if __name__ == "__main__":
    main()

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
    assert "Type-B Opportunity Collection" in source
    assert "One shared direction call is one independent opportunity" in source
    assert 'id="opportunity-body"' in source
    assert 'id="opportunity-gap-body"' in source
    assert 'id="opportunity-gap-holdout-body"' in source
    assert "The normalized gap is the raw LONG-vs-SHORT score difference divided by 10" in source
    assert "row.feature === 'score_gap'" in source
    assert "condition.feature === 'score_gap'" in source
    assert "Research-only; no execution gate changes." in source
    assert "Paper, live and shadow are recorded as child audit evidence" in source
    assert "Benchmark outcome P&amp;L" in source
    assert 'id="typeb-v2-health"' in source
    assert '"stale_age_sec": stale_age_sec' in source
    assert '"error": rep.get("error")' in source
    assert "Executed-session P&amp;L" not in source
    assert "Shadow-simulation P&amp;L" not in source
    assert "<th>Shadow Sims</th>" not in source
    assert "function fmtAdxBucket" in source
    assert "ADX 18–<30" in source
    assert "s.delta_abs ?? s.abs_delta" in source
    assert 'id="typeb-rules-body"' in source
    assert 'id="collection-contract"' in source
    assert 'id="collection-status"' in source
    assert "they never call an AI" in source
    assert 'href="/download/everything"' in source
    assert "Download Complete Research Evidence Bundle" in source
    assert 'id="dl-chatgpt"' not in source
    assert 'id="dl-complete"' not in source
    assert 'id="dl-gpt-audit"' not in source
    assert 'id="dl-research-pack"' not in source
    assert "Past Analysis — not available yet" in source
    assert '@app.route("/api/accumulator")' in source
    assert '@app.route("/download/accumulator")' in source
    assert "_canonical_chatgpt_research_bundle" in source

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
            (agent_root / "type_b_research_v2_report.json").write_text(
                json.dumps({
                    "schema": "type_b_research_v2_report_v1",
                    "collection_id": "TYPE_B_RESEARCH_V2",
                    "independent_opportunities": 12,
                    "valid_holdout_opportunities": 11,
                    "completed_opportunities": 8,
                    "filled_opportunities": 7,
                    "type_b_outcomes": 3,
                    "net_pnl_usd": 4.2,
                    "modes_observed": {"PAPER": 6, "PAUSED_SHADOW": 6},
                    "entry_probability_table": [{
                        "feature": "score_gap",
                        "bucket": "GAP_4",
                        "evidence_mode": "PAPER",
                        "n": 4,
                    }],
                    "readiness": "COLLECTING",
                    "recent_opportunities": [{"opportunity_id": "scan-v2"}],
                }),
                encoding="utf-8",
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
            v2_payload = research_dashboard._typeb_research_v2_payload()
            assert v2_payload["independent_opportunities"] == 12
            assert v2_payload["completed_opportunities"] == 8
            assert v2_payload["modes_observed"]["PAUSED_SHADOW"] == 6
            assert v2_payload["entry_probability_table"][0]["bucket"] == "GAP_4"
            assert v2_payload["recent_opportunities"][0]["opportunity_id"] == "scan-v2"
    finally:
        research_dashboard._API_RESPONSE_CACHE.clear()
        research_dashboard.ROOT = original_root
        research_dashboard.DATA_ROOT = original_data_root

    print("Research dashboard clarity tests passed")


if __name__ == "__main__":
    main()

"""Loopback-only synthetic renderer preview. Never reads the canonical dataset."""
import os
import tempfile
from pathlib import Path


def main():
    with tempfile.TemporaryDirectory(prefix="btc-synthetic-ui-") as isolated:
        os.environ["BTC_AGENT_DATA_DIR"] = str(Path(__file__).parent / "canonical-research-data")
        os.environ["BTC_AGENT_REPORT_DIR"] = isolated
        from flask import jsonify
        from research import research_dashboard as dashboard
        dashboard.DATA_ROOT = Path(isolated)
        dashboard.ROOT = Path(isolated)

        generation = {key: "SYNTHETIC-" + key for key in (
            "source_revision", "deployed_revision", "analyzer_revision",
            "manifest_entry_hash", "epoch_id", "tile_config_signature",
            "generation_key", "evaluator_version")}
        manifest = {**generation, "dataset_epoch": generation["epoch_id"],
                    "config_signature": generation["tile_config_signature"]}
        report = {"generation": generation, "complete_replay_count": 2,
                  "unknown_replay_count": 1,
                  "conditional_report": {"generation": generation,
                      "complete_replay_count": 3, "unknown_replay_count": 4},
                  "conditional_delayed_variant_reports": [{
                      "timing_model_sha256": "<img src=x onerror=alert(1)>" + "a" * 64,
                      "report": {"generation": generation,
                                 "complete_replay_count": 5}}]}

        def synthetic_design():
            return jsonify(status="SYNTHETIC_UI_FIXTURE", reason="NOT MARKET EVIDENCE",
                qualification_allowed=False, profitability_calculated=False,
                shadow_tiers=dashboard._shadow_tier_projection(report, True, manifest))

        dashboard.app.view_functions["api_research_design"] = synthetic_design

        def synthetic_genome():
            return jsonify(schema="SYNTHETIC_UI_FIXTURE", available=True,
                collector_generation="V3.1", epoch_id="SYNTHETIC-EPOCH",
                warning="SYNTHETIC UI ONLY — NOT MARKET EVIDENCE",
                qualification="NOT_QUALIFIED", live_policy_change_allowed=False,
                shared_context_coverage={
                    "status": "CURRENT_EPOCH_EVIDENCE_ONLY",
                    "eligible_lanes": 140, "cohort_evaluated_lanes": 128,
                    "cohort_bound_lanes": 100, "cohort_pending_lanes": 12,
                    "cohort_evaluation_complete": False,
                    "page_lanes": 64, "bound_lanes": 50, "truncated": True})

        dashboard.app.view_functions["api_genome"] = synthetic_genome

        @dashboard.app.after_request
        def label_fixture(response):
            if response.mimetype == "text/html":
                response.set_data(response.get_data(as_text=True).replace("<body>",
                    '<body><div style="background:#802000;color:white;padding:16px">'
                    'SYNTHETIC UI TEST ONLY — NOT PRODUCTION OR STRATEGY EVIDENCE</div>', 1))
            return response

        dashboard.app.run(host="127.0.0.1", port=9502, use_reloader=False)


if __name__ == "__main__":
    main()

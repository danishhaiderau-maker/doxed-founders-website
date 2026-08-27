import json
from pathlib import Path

import pandas as pd

import analyzer_research_engine_v62 as analyzer


def test_configured_mirror_wins_over_stale_source_file(tmp_path, monkeypatch):
    mirror = tmp_path / "mirror"
    source = tmp_path / "source"
    mirror.mkdir()
    source.mkdir()
    (mirror / "signal_replay.jsonl").write_text("mirror\n", encoding="utf-8")
    (source / "signal_replay.jsonl").write_text("stale-source\n", encoding="utf-8")
    monkeypatch.chdir(source)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(mirror))

    resolved = analyzer._agent_data_path("signal_replay.jsonl")

    assert resolved == str(mirror / "signal_replay.jsonl")


def test_report_provenance_executes_against_canonical_evidence_root(tmp_path, monkeypatch):
    (tmp_path / "relay_lifecycle_evidence_v1.json").write_text(
        json.dumps({
            "schema": "relay_lifecycle_evidence_v1",
            "generatedAt": "2026-08-16T00:00:00Z",
            "generatingRevision": "a" * 40,
            "runIdentity": "run-test",
            "records": [],
        }),
        encoding="utf-8",
    )
    (tmp_path / "counterfactual.jsonl").write_text(
        json.dumps({
            "trade_id": "cont-test",
            "policy_comparability_key": "policy-one",
        }) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))

    provenance = analyzer._report_source_evidence_provenance()

    assert provenance["data_root_kind"] == "CANONICAL_LOCAL_FLY_MIRROR"
    assert len(provenance["source_data_revision"]) == 64
    assert provenance["policy_comparability_key"] == "policy-one"
    assert provenance["policy_comparability_status"] == "SINGLE_COMPARABLE_POLICY"
    assert all(row["available"] for row in provenance["evidence_inputs"].values())
    relay = provenance["evidence_inputs"]["relay_lifecycle_evidence_v1.json"]
    assert relay["generating_revision"] == "a" * 40
    assert relay["producer_service"] == "PLATFORM_RELAY_EXPORTER"
    assert relay["producer_revision_role"] == "EXPORTER_DEPLOYMENT_REVISION"


def test_provenance_uses_joined_policy_keys_when_raw_jsonl_is_null(tmp_path, monkeypatch):
    (tmp_path / "relay_lifecycle_evidence_v1.json").write_text(
        json.dumps({
            "schema": "relay_lifecycle_evidence_v1",
            "generatedAt": "2026-08-16T00:00:00Z",
            "generatingRevision": "a" * 40,
            "runIdentity": "run-test",
            "records": [],
        }),
        encoding="utf-8",
    )
    (tmp_path / "counterfactual.jsonl").write_text(
        json.dumps({"trade_id": "cont-test", "policy_comparability_key": None}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        analyzer,
        "_load_jsonl_by_trade_id",
        lambda path: {
            "cont-test": {
                "trade_id": "cont-test",
                "policy_comparability_key": "policy_comparability_v1:joined",
            }
        },
    )

    provenance = analyzer._report_source_evidence_provenance()

    assert provenance["policy_comparability_key"] == "policy_comparability_v1:joined"
    assert provenance["policy_comparability_status"] == "SINGLE_COMPARABLE_POLICY"


def test_report_stamp_marks_ungated_report_descriptive_and_unqualified(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text('{"result":"descriptive"}', encoding="utf-8")
    cohorts = {
        analyzer.SHOWCASE_STRATEGY: {
            "included_row_count": 2,
            "evidence_row_count": 10,
            "exclusion_reason_counts": {"REPLAY_INCOMPLETE": 8},
        },
        analyzer.BITFINEX_COPY_FIDELITY: {
            "included_row_count": 0,
            "evidence_row_count": 10,
            "exclusion_reason_counts": {"BITFINEX_LINKAGE_MISSING": 10},
        },
        analyzer.REAL_COPY_PARAMETER_OPTIMISATION: {
            "included_row_count": 0,
            "evidence_row_count": 10,
            "exclusion_reason_counts": {"BITFINEX_ACTUAL_PNL_MISSING": 10},
        },
    }
    provenance = {
        "cohort_schema": "analysis_cohorts_v1",
        "generation_revision": "b" * 40,
        "source_data_revision": "c" * 64,
        "policy_comparability_key": None,
        "cohorts": cohorts,
    }

    analyzer._stamp_report_analysis_provenance(str(report_path), provenance)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["schema"] == "analyzer_report_v1"
    assert report["report_eligibility"]["classification"] == "DESCRIPTIVE_UNQUALIFIED"
    assert report["report_eligibility"]["included_row_count"] == 0
    assert report["report_eligibility"]["excluded_row_count"] == 10
    assert report["live_policy_change_allowed"] is False


def test_empty_current_lanes_are_written_and_publish_with_exact_generation_identity(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(analyzer, "_load_signal_snapshots", lambda: {})
    monkeypatch.setattr(analyzer, "load_research_session", lambda: {
        "fresh_collection_mode": True,
        "fresh_collection_start_time": 1_787_780_026.0,
    })

    report = analyzer.benchmark_vs_lanes_report(
        trades=pd.DataFrame(),
        session={
            "fresh_collection_mode": True,
            "fresh_collection_start_time": 1_787_780_026.0,
        },
    )

    assert report["status"] == "CURRENT_SESSION_NO_APPROVE_SNAPSHOTS"
    assert report["session_scope"] == "FRESH-COLLECTION"
    assert report["evidence_scope"] == "CURRENT_SESSION"
    assert set(report["lanes"]) == set(analyzer.ACTIVE_TILE_ORDER)
    assert all(row["real_fills"] == 0 for row in report["lanes"].values())
    assert all("all_time" not in row for row in report["lanes"].values())

    provenance = {
        "cohort_schema": "analysis_cohorts_v1",
        "generation_revision": "revision-current",
        "source_data_revision": "source-current",
        "fresh_epoch_id": "epoch-current",
        "policy_comparability_key": None,
        "cohorts": {
            analyzer.SHOWCASE_STRATEGY: {
                "included_row_count": 0,
                "evidence_row_count": 0,
                "exclusion_reason_counts": {},
            }
        },
    }
    analyzer._stamp_report_analysis_provenance(
        analyzer.BENCHMARK_VS_LANES_REPORT_FILE,
        provenance,
    )
    stamped = json.loads(
        Path(analyzer.BENCHMARK_VS_LANES_REPORT_FILE).read_text(encoding="utf-8")
    )
    assert stamped["generation_revision"] == "revision-current"
    assert stamped["source_data_revision"] == "source-current"
    assert stamped["epoch_id"] == "epoch-current"

    manifest = {
        "generation_id": "generation-current",
        "generation_revision": "revision-current",
        "source_data_revision": "source-current",
        "fresh_epoch": {"epoch_id": "epoch-current"},
        "session_scope": "FRESH-COLLECTION",
        "reports": [{"file": analyzer.BENCHMARK_VS_LANES_REPORT_FILE}],
        "text_artifacts": [],
    }
    analyzer._publish_completed_report_generation(manifest)
    published_dir = Path(analyzer.PUBLISHED_REPORTS_DIR)
    published_manifest = json.loads(
        (published_dir / analyzer.REPORT_MANIFEST_FILE).read_text(encoding="utf-8")
    )
    published_report = json.loads(
        (published_dir / analyzer.BENCHMARK_VS_LANES_REPORT_FILE).read_text(encoding="utf-8")
    )

    assert analyzer.BENCHMARK_VS_LANES_REPORT_FILE in {
        row["file"] for row in published_manifest["reports"]
    }
    assert published_report["generation_revision"] == published_manifest["generation_revision"]
    assert published_report["source_data_revision"] == published_manifest["source_data_revision"]
    assert published_report["epoch_id"] == published_manifest["fresh_epoch"]["epoch_id"]
    assert published_report["evidence_scope"] == "CURRENT_SESSION"


def test_report_stamp_normalizes_report_specific_showcase_counts(tmp_path):
    report_path = tmp_path / "cluster.json"
    report_path.write_text(json.dumps({
        "showcase_cohort": {
            "schema": "analysis_cohorts_v1",
            "eligible_ids": 5,
            "evidence_rows": 44,
            "exclusion_reason_counts": {"REPLAY_INCOMPLETE": 39},
        }
    }), encoding="utf-8")
    cohorts = {
        analyzer.SHOWCASE_STRATEGY: {"included_row_count": 5, "evidence_row_count": 44, "exclusion_reason_counts": {}},
        analyzer.BITFINEX_COPY_FIDELITY: {"included_row_count": 0, "evidence_row_count": 44, "exclusion_reason_counts": {}},
        analyzer.REAL_COPY_PARAMETER_OPTIMISATION: {"included_row_count": 0, "evidence_row_count": 44, "exclusion_reason_counts": {}},
    }
    provenance = {
        "cohort_schema": "analysis_cohorts_v1",
        "generation_revision": "b" * 40,
        "source_data_revision": "c" * 64,
        "policy_comparability_key": None,
        "cohorts": cohorts,
    }

    analyzer._stamp_report_analysis_provenance(str(report_path), provenance)
    eligibility = json.loads(report_path.read_text(encoding="utf-8"))["report_eligibility"]

    assert eligibility["included_row_count"] == 5
    assert eligibility["evidence_row_count"] == 44
    assert eligibility["excluded_row_count"] == 39


def test_fresh_epoch_identity_is_stable_and_cutoff_bound(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    status_dir = tmp_path / "research_accumulator"
    status_dir.mkdir()
    (status_dir / "research_accumulator_status.json").write_text(json.dumps({
        "epoch_start_iso": "2026-08-16T13:12:49.657003+00:00",
        "backfill_policy": "none — only trades after epoch",
    }), encoding="utf-8")
    monkeypatch.setattr(analyzer, "load_research_session", lambda: {})

    first = analyzer._fresh_epoch_provenance()
    second = analyzer._fresh_epoch_provenance()

    assert first == second
    assert first["fresh_epoch_status"] == "BOUND"
    assert first["fresh_epoch_kind"] == "NO_BACKFILL_RESEARCH_ACCUMULATOR"
    assert first["fresh_epoch_cutoff_utc"] == "2026-08-16T13:12:49.657003+00:00"
    assert first["fresh_epoch_id"].startswith("epoch-")


def test_chase_attribution_without_trade_rows_keeps_unknown_hold_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "_load_jsonl_rows", lambda _path: [{
        "trade_id": "cont-no-trade-row",
        "stage": "ORDER_SUBMITTED",
        "research_lane": "BASE",
        "limit_price": 63_000,
    }])
    monkeypatch.setattr(analyzer, "_filter_jsonl_rows_by_session", lambda rows, _session: rows)
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda _name: str(tmp_path / "chase.json"))

    report = analyzer.chase_attribution_report(trades=pd.DataFrame(), session={})

    assert report["trades"][0]["trade_id"] == "cont-no-trade-row"
    assert report["trades"][0]["avg_hold_min"] is None


def test_chase_attribution_keeps_v31_lane_without_legacy_chase_column(tmp_path, monkeypatch):
    def load_rows(path):
        if "duplicate_intent_audit" in str(path):
            return [{
                "trade_id": "fc3-current",
                "research_lane": "FAMILY_CHANDELIER_3",
            }]
        return [{
            "trade_id": "fc3-current",
            "stage": "ORDER_SUBMITTED",
            "research_lane": "UNKNOWN",
            "limit_price": 63_000,
        }]

    monkeypatch.setattr(analyzer, "_load_jsonl_rows", load_rows)
    monkeypatch.setattr(analyzer, "_filter_jsonl_rows_by_session", lambda rows, _session: rows)
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda _name: str(tmp_path / "chase.json"))
    trades = pd.DataFrame([{
        "trade_id": "fc3-current",
        "net_pnl_usd": 0.12,
        "duration_min": 7.5,
    }])

    report = analyzer.chase_attribution_report(trades=trades, session={})

    assert report["trades"][0]["lane"] == "FAMILY_CHANDELIER_3"
    assert report["trades"][0]["avg_hold_min"] == 7.5


def test_fresh_epoch_chase_attribution_excludes_pre_epoch_relay_only_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "_load_jsonl_rows", lambda _path: [])
    monkeypatch.setattr(analyzer, "_filter_jsonl_rows_by_session", lambda rows, _session: rows)
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))
    monkeypatch.setattr(analyzer, "_platform_relay_evidence_index", lambda _path: {
        "legacy-trade": {
            "records": [{
                "canonicalTradeId": "legacy-trade",
                "createdAt": "2026-06-18T10:18:44.999Z",
                "closedAt": "2026-06-18T10:28:58.289Z",
                "events": [],
            }],
        },
    })

    report = analyzer.chase_attribution_report(
        trades=pd.DataFrame(),
        session={"fresh_collection_start_time": 1787520261.5305245},
    )

    assert report["trades"] == []


def test_empty_current_epoch_overwrites_scenario_c_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))
    stale_leak = tmp_path / analyzer.SCENARIO_C_LEAKAGE_REPORT_FILE
    stale_capture = tmp_path / analyzer.SCENARIO_C_CAPTURE_RATIO_REPORT_FILE
    stale_leak.write_text('{"overall":{"left_on_table_usd":24.84}}', encoding="utf-8")
    stale_capture.write_text('{"overall_mfe_positive":{"aggregate_capture_pct":-4.2}}', encoding="utf-8")

    leak = analyzer.scenario_c_leakage_report(trades=pd.DataFrame(), session={})
    capture = analyzer.scenario_c_capture_ratio_report(trades=pd.DataFrame(), session={})

    assert leak["overall"]["left_on_table_usd"] is None
    assert capture["overall_mfe_positive"] == {"trades": 0}
    assert json.loads(stale_leak.read_text(encoding="utf-8"))["evidence_status"] == "INSUFFICIENT_CURRENT_EPOCH_TERMINALS"
    assert json.loads(stale_capture.read_text(encoding="utf-8"))["evidence_status"] == "INSUFFICIENT_CURRENT_EPOCH_TERMINALS"

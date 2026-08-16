import json

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

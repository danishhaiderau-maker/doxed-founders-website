"""Incident receipts taint both real producers without modifying raw evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from research.runtime_identity_incidents import (
    HASH_ENV, PATH_ENV, REASON, IncidentEpisodeIndex,
    assert_publication_incident_input, load_incident_input,
)


@pytest.fixture(autouse=True)
def no_ambient_incident(monkeypatch):
    monkeypatch.delenv(PATH_ENV, raising=False)
    monkeypatch.delenv(HASH_ENV, raising=False)


def _receipt(tmp_path, *, closed=True, **changes):
    payload = {
        "schema": "btc_runtime_revision_incident_receipt_v1",
        "incident_id": "test-deploy-identity", "status": "CLOSED" if closed else "OPEN",
        "conservative_start_utc": "2026-09-05T03:07:36Z",
        "verified_end_utc": "2026-09-05T04:00:00Z" if closed else None,
        "expected_source_revision": "a" * 40,
        "observed_process_source_revision": "b" * 40,
        "qualification_allowed_from_affected_interval": False,
        "analyzer_exclusion_enforced": False,
        **changes,
    }
    path = tmp_path / "incident.json"
    path.write_bytes(json.dumps(payload).encode())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return path, digest


def _input(tmp_path, **changes):
    return load_incident_input(*_receipt(tmp_path, **changes))


@pytest.mark.parametrize("row,affected", [
    ({"timestamp": "2026-09-05T03:07:35Z"}, False),
    ({"timestamp": "2026-09-05T03:07:36Z"}, True),
    ({"timestamp": "2026-09-05T03:59:59Z"}, True),
    ({"timestamp": "2026-09-05T04:00:00Z"}, False),
    ({"coverage": {"start_utc": "2026-09-05T02:00:00Z", "end_utc": "2026-09-05T05:00:00Z"}}, True),
    ({"timestamp": "bad"}, True),
    ({"timestamp": None, "signal_ts": 100}, True),
    ({"no_time": 123}, True),
    ({"bucket_ts": 100}, False),
    ({"coverage": {"end_ts": 100}}, True),
    ({"coverage": {"start_ts": 100}}, True),
    ({"timestamp_ms": 100000}, False),
    ({"timestamp": True}, True),
    ({"timestamp": float("inf")}, True),
    ({"timestamp": "2026-09-05T02:00:00"}, True),
    ({"rows": [None] * 100001, "signal_ts": 100}, True),
])
def test_temporal_scope_is_conservative(tmp_path, row, affected):
    assert _input(tmp_path).affected(row) is affected


def test_open_interval_cannot_be_closed_by_inference(tmp_path):
    source = _input(tmp_path, closed=False)
    assert source.affected({"timestamp": "2030-01-01T00:00:00Z"})


@pytest.mark.parametrize("changes", [
    {"status": "RESOLVED"}, {"expected_source_revision": "abc"},
    {"observed_process_source_revision": "a" * 40},
    {"qualification_allowed_from_affected_interval": True},
    {"verified_end_utc": None},
    {"verified_end_utc": "2026-09-04T00:00:00Z"},
    {"conservative_start_utc": "2026-09-05T03:07:36"},
    {"schema": "other"}, {"incident_id": "../../escape"},
])
def test_invalid_receipt_fails_closed(tmp_path, changes):
    with pytest.raises(ValueError):
        _input(tmp_path, **changes)


def test_no_incident_is_backward_compatible():
    source = load_incident_input()
    assert not source.enabled
    assert not source.affected({})
    assert_publication_incident_input({})


def test_hash_pin_and_absolute_path_required(tmp_path):
    path, digest = _receipt(tmp_path)
    with pytest.raises(ValueError, match="PIN_REQUIRED"):
        load_incident_input(path, "")
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        load_incident_input(path, "0" * 64)
    with pytest.raises(ValueError, match="PATH_NOT_ABSOLUTE"):
        load_incident_input("relative.json", digest)


def test_large_receipt_rejected_without_unbounded_read(tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes(b"x" * (256 * 1024 + 1))
    with pytest.raises(ValueError, match="SIZE_INVALID"):
        load_incident_input(path, "a" * 64)


def test_reparse_or_symlink_is_rejected(tmp_path, monkeypatch):
    path, digest = _receipt(tmp_path)
    original = Path.lstat
    class Reparse:
        st_mode = 0o100644
        st_file_attributes = 0x400
    monkeypatch.setattr(Path, "lstat", lambda self: Reparse() if self == path else original(self))
    with pytest.raises(ValueError, match="LINK_FORBIDDEN"):
        load_incident_input(path, digest)


def test_receipt_change_invalidates_publication(tmp_path, monkeypatch):
    path, digest = _receipt(tmp_path)
    monkeypatch.setenv(PATH_ENV, str(path))
    monkeypatch.setenv(HASH_ENV, digest)
    source = load_incident_input()
    manifest = {"analysis_provenance": {"runtime_identity_incident_input": source.provenance()}}
    assert_publication_incident_input(manifest)
    with pytest.raises(ValueError, match="PUBLICATION_INPUT_MISMATCH"):
        assert_publication_incident_input({})
    path.write_bytes(path.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="INPUT_CHANGED"):
        source.assert_unchanged()
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        assert_publication_incident_input(manifest)
    monkeypatch.setenv(HASH_ENV, hashlib.sha256(path.read_bytes()).hexdigest())
    with pytest.raises(ValueError, match="PUBLICATION_INPUT_MISMATCH"):
        assert_publication_incident_input(manifest)
    monkeypatch.delenv(PATH_ENV)
    monkeypatch.delenv(HASH_ENV)
    with pytest.raises(ValueError, match="PUBLICATION_INPUT_MISMATCH"):
        assert_publication_incident_input(manifest)


def test_linked_lifecycle_taints_all_variants_and_missing_time(tmp_path):
    index = IncidentEpisodeIndex(_input(tmp_path))
    clean = {"epoch_id": "epoch", "episode_id": "episode", "signal_ts": 100}
    index.add([{**clean, "policy_signature": "one", "timestamp": "2026-09-05T03:10:00Z"}])
    assert index.reasons({**clean, "policy_signature": "another"}) == [REASON]
    assert not index.reasons({**clean, "episode_id": "other"})
    index.add([{"epoch_id": "epoch", "episode_id": "other"}])
    assert index.reasons({**clean, "episode_id": "other"}) == [REASON]


def _file_hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


@pytest.mark.parametrize("ledger", ["opportunity", "lifecycle", "execution", "market_segment"])
def test_evaluator_producer_rejects_affected_linked_evidence_without_raw_changes(tmp_path, ledger):
    from test_policy_evidence_evaluator import _fixture, _write
    from research.policy_evidence_evaluator import build_v3_conservative_results
    root = _fixture(tmp_path)
    source = _input(tmp_path)
    # Give every ledger row an outside-incident time, then move only one linked
    # row into the incident. Raw hashes must remain unchanged by evaluation.
    for path in (root / "ledgers").glob("*.jsonl"):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for row in rows:
            row["timestamp"] = "2026-09-04T00:00:00Z"
        _write(path, rows)
    path = root / "ledgers" / f"{ledger}.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    if not rows:
        rows = [{"epoch_id": "epoch-1", "episode_id": "ep-1", "opportunity_id": "opp-1"}]
    rows[0]["timestamp"] = "2026-09-05T03:10:00Z"
    _write(path, rows)
    before = _file_hashes(root)
    report = build_v3_conservative_results(root, incident_input=source)
    assert report["row_count"] > 0
    assert report["classification_counts"]["UNKNOWN"] == report["row_count"]
    assert all(REASON in row["unknown_reason_codes"] for row in report["results"])
    assert report["runtime_identity_incident_input"]["receipt_sha256"] == source.sha256
    assert _file_hashes(root) == before


def test_replay_producer_variants_are_unknown_and_never_no_fill(tmp_path):
    from test_entry_baseline_replay import _write_v3_opportunity
    from research.entry_baseline_replay import materialize_v3_opportunity_replay
    ledgers, row = _write_v3_opportunity(tmp_path)
    source = _input(tmp_path)
    linked = {"epoch_id": "epoch-1", "episode_id": "ep-1", "opportunity_id": "opp-1",
              "policy_signature": "not-selected", "timestamp": "2026-09-05T03:10:00Z"}
    (ledgers / "lifecycle.jsonl").write_text(json.dumps(linked) + "\n")
    before = _file_hashes(tmp_path / "v3")
    report = materialize_v3_opportunity_replay(tmp_path, incident_input=source)
    assert report["same_opportunity_count"] == 1
    for result in report["episode_receipts"][0]["results"]:
        assert result["outcome_state"] == "UNKNOWN"
        assert REASON in result["rejection_codes"]
    assert all(summary["unknown"] == 1 and summary["no_fills"] == 0
               for summary in report["summaries"].values())
    assert _file_hashes(tmp_path / "v3") == before


def test_actual_parent_open_receipt_shape_is_supported(tmp_path):
    source = _input(tmp_path, closed=False,
                    evidence={"ssh_probe_observed_new_code_old_environment": True},
                    observed_bot_normalized_sha12="d4a2d6fdc45f")
    assert source.enabled and source.end is None


def test_unassociated_legacy_unknown_does_not_invent_link_to_clean_postclosure_episode(tmp_path):
    index = IncidentEpisodeIndex(_input(tmp_path))
    index.add([{"record_id": "legacy-no-causal-identity"}])
    clean = {"epoch_id": "new-epoch", "episode_id": "new-episode", "opportunity_id": "new-opportunity",
             "signal_utc": "2026-09-05T05:00:00Z",
             "signed_quantity_constraints": {"captured_at": "2026-09-01T00:00:00Z"}}
    assert index.reasons(clean) == []
    assert index.reasons({"record_id": "legacy-no-causal-identity"}) == [REASON]
    assert index.coverage()["unassociated_unknown_input_rows"] == 1
    assert index.coverage()["unassociated_rows_qualified"] is False
    # A real shared opportunity or episode must still propagate missing-time
    # evidence even when the variant's own timestamp looks clean.
    index.add([{"opportunity_id": "new-opportunity"}])
    assert index.reasons(clean) == [REASON]
    alternate = {**clean, "opportunity_id": "different-opportunity"}
    assert index.reasons(alternate) == []
    index.add([{"episode_id": "new-episode"}])
    assert index.reasons(alternate) == [REASON]


def test_reference_capture_time_cannot_replace_missing_event_time(tmp_path):
    source = _input(tmp_path)
    assert source.affected({"signed_quantity_constraints": {"captured_at": "2026-09-01T00:00:00Z"}})


def test_complete_two_hour_path_is_not_rejected_by_temporal_work_budget(tmp_path):
    source = _input(tmp_path)
    assert not source.affected({"rows": [{"bucket_ts": source.end + index} for index in range(7201)]})


def test_evaluator_clean_episode_survives_unassociated_legacy_rows(tmp_path):
    from test_policy_evidence_evaluator import _fixture, _write
    from research.policy_evidence_evaluator import build_v3_conservative_results
    root = _fixture(tmp_path)
    # Test interval predates all well-formed fixture evidence. The market tape
    # and schedules remain consistent; we do not relabel events in the producer.
    source = _input(tmp_path, conservative_start_utc="1970-01-01T00:00:01Z",
                    verified_end_utc="1970-01-01T00:00:02Z")
    for path in (root / "ledgers").glob("*.jsonl"):
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        for row in rows:
            row["timestamp"] = 10
        _write(path, rows)
    _write(root / "ledgers/lifecycle.jsonl", [{"record_id": "legacy-unassociated"}])
    expected = build_v3_conservative_results(root)
    result = build_v3_conservative_results(root, incident_input=source)
    assert expected["classification_counts"]["FULL_FILL"] > 0
    assert result["classification_counts"] == expected["classification_counts"]
    assert result["runtime_identity_incident_coverage"]["unassociated_unknown_input_rows"] == 1


def test_disabled_publication_fingerprint_remains_byte_identical():
    import ast
    source = (Path(__file__).parent / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "write_report_manifest")
    assignment = next(node for node in function.body if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant)
                              and target.slice.value == "generation_id" for target in node.targets))
    material = next(node for node in ast.walk(assignment.value) if isinstance(node, ast.Dict))
    manifest = {"generated_at": "now", "generation_revision": "code", "source_revision": "source",
                "deployed_revision": "deployed", "dataset_epoch": "epoch", "config_signature": "config",
                "fresh_epoch": {"epoch_id": "epoch"}}
    actual = eval(compile(ast.Expression(material), "<fingerprint>", "eval"), {"manifest": manifest, "analysis_provenance": {}})
    expected = {"generated_at": "now", "revision": "code", "source_revision": "source",
                "deployed_revision": "deployed", "dataset_epoch": "epoch", "config_signature": "config",
                "fresh_epoch": "epoch"}
    assert json.dumps(actual, sort_keys=True) == json.dumps(expected, sort_keys=True)


def test_atomic_publisher_rejects_changed_incident_after_artifact_copy(tmp_path, monkeypatch):
    from test_analyzer_atomic_publication import _load, AGENT
    analyzer = _load("incident_atomic_analyzer", AGENT / "analyzer_research_engine_v62.py")
    import research.mirror_coherence as coherence
    monkeypatch.setattr(coherence, "assert_mirror_coherent", lambda **kwargs: None)
    path, digest = _receipt(tmp_path)
    monkeypatch.setenv(PATH_ENV, str(path))
    monkeypatch.setenv(HASH_ENV, digest)
    incident = load_incident_input()
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    published = Path(analyzer.PUBLISHED_REPORTS_DIR)
    published.mkdir()
    old = published / "prior.txt"
    old.write_text("preserved prior generation")
    Path("current.json").write_text("{}")
    original = analyzer.shutil.copy2
    def changed_during_copy(*args, **kwargs):
        result = original(*args, **kwargs)
        path.write_bytes(path.read_bytes() + b"\n")
        return result
    monkeypatch.setattr(analyzer.shutil, "copy2", changed_during_copy)
    manifest = {"generation_id": "new", "reports": [{"file": "current.json"}], "text_artifacts": [],
                "analysis_provenance": {"runtime_identity_incident_input": incident.provenance()}}
    with pytest.raises(ValueError, match="IDENTITY_INCIDENT_HASH_MISMATCH"):
        analyzer._publish_completed_report_generation(manifest)
    assert old.read_text() == "preserved prior generation"
    assert not (published / "current.json").exists()


def test_baseline_clean_cohort_retains_all_variants_with_incident_enabled(tmp_path):
    from test_entry_baseline_replay import test_v3_materializer_joins_verified_content_addressed_market_segment
    from research.entry_baseline_replay import materialize_v3_opportunity_replay
    test_v3_materializer_joins_verified_content_addressed_market_segment(tmp_path)
    path = tmp_path / "v3/ledgers/market_segment.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    for row in rows:
        row["timestamp"] = 100
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    incident = _input(tmp_path, conservative_start_utc="1970-01-01T00:00:01Z",
                      verified_end_utc="1970-01-01T00:00:02Z")
    expected = materialize_v3_opportunity_replay(tmp_path)
    actual = materialize_v3_opportunity_replay(tmp_path, incident_input=incident)
    assert actual["summaries"] == expected["summaries"]
    assert all(summary["full_fills"] == 1 for summary in actual["summaries"].values())


@pytest.mark.parametrize("affected", [True, False])
def test_genome_qualification_cannot_bypass_incident_but_keeps_descriptive_candidate(tmp_path, monkeypatch, affected):
    from research.research_v3_report import build_safe_policy_genome_v3_report
    from research_v3_store import V3EvidenceStore
    from research_v3_ranking import REQUIRED_GATES
    from research.v3_policy_report_adapter import candidate_from_genome
    path, digest = _receipt(tmp_path)
    monkeypatch.setenv(PATH_ENV, str(path))
    monkeypatch.setenv(HASH_ENV, digest)
    store = V3EvidenceStore(tmp_path, epoch_id="epoch-new")
    timestamp = "2026-09-05T03:10:00Z" if affected else "2026-09-05T05:00:00Z"
    from datetime import datetime
    signal_ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
    store.append("opportunity", {"record_id": "opp", "opportunity_id": "opp",
                                 "episode_id": "episode", "signal_ts": signal_ts})
    candidate = {"policy_id": "candidate", "policy_signature": "signature",
                 "gates": {gate: True for gate in REQUIRED_GATES},
                 "sealed_oos_net_usd": 12, "expectancy_lcb_usd": 1,
                 "supported_conservative_episodes": 10, "full_fills": 10,
                 "ideal_touch_diagnostic": {"oos_net_usd": 12},
                 "validation": {"risk": {"net_pnl_usd": 12}}}
    original = deepcopy(candidate)
    report = build_safe_policy_genome_v3_report(tmp_path, tmp_path / "reports", candidates=[candidate])
    assert candidate == original
    coverage = report["runtime_identity_incident_coverage"]
    assert coverage["affected_selected_episodes"] == int(affected)
    assert coverage["descriptive_statistics_preserved"] is True
    if affected:
        assert report["number_one_strategy"] is None
        assert report["safe_policy_ranking"]["blocked_policy_count"] == 1
        assert REASON in report["blockers"]
        assert report["strategy_leaders"]["descriptive_ideal_touch"]["leader"] is not None
        assert report["strategy_leaders"]["execution_supported"]["leader"] is None
        adapted = candidate_from_genome(report, {})
        assert REASON in adapted["blockers"]
    else:
        assert report["number_one_strategy"]["policy_id"] == "candidate"
        assert report["number_one_strategy"]["sealed_oos_net_usd"] == 12
        assert REASON not in report["blockers"]


@pytest.mark.parametrize("affected", [True, False])
def test_dynamic_cohort_filters_linked_incident_and_never_reuses_original_seal(tmp_path, monkeypatch, affected):
    import dynamic_policy_analyzer as dynamic
    path, digest = _receipt(tmp_path)
    monkeypatch.setenv(PATH_ENV, str(path))
    monkeypatch.setenv(HASH_ENV, digest)
    clean = {"epoch_id": "epoch", "episode_id": "clean", "signal_utc": "2026-09-05T05:00:00Z"}
    suspect = {"epoch_id": "epoch", "episode_id": "suspect",
               "signal_utc": "2026-09-05T03:10:00Z" if affected else "2026-09-05T06:00:00Z"}
    payload = {"generation_revision": "code", "training_episodes": [clean, suspect], "sealed_holdout_episodes": [clean],
               "sealed_holdout_evaluation": {"seal": "original"}}
    original = deepcopy(payload)
    monkeypatch.setattr(dynamic, "_load_verified_canonical_input", lambda root: (payload, {"sha256": "raw"}))
    calls = []
    def orchestrate(training, holdout, **kwargs):
        calls.append((training, holdout, kwargs))
        return {"status": "SUPPORTED"}
    monkeypatch.setattr(dynamic, "orchestrate_dynamic_policy_analysis", orchestrate)
    report = dynamic.build_dynamic_policy_analysis_report(tmp_path, generation_revision="code",
                                                          dataset_epoch=None, source_revision=None)
    assert payload == original
    if affected:
        assert calls[0][0] == [clean]
        assert calls[0][2]["sealed_holdout_evaluation"] is None
        assert report["status"] == "UNKNOWN"
        assert REASON in report["blockers"]
        assert report["descriptive_filtered_cohort_qualification_allowed"] is False
    else:
        assert calls[0][0] == [clean, suspect]
        assert calls[0][2]["sealed_holdout_evaluation"] == {"seal": "original"}
        assert report["status"] == "SUPPORTED"
    assert report["input_receipt"]["runtime_identity_incident_input"]["receipt_sha256"] == digest


def test_legacy_optimizer_shared_eligibility_keeps_clean_trade_and_blocks_ambiguous_link(tmp_path, monkeypatch):
    from test_analyzer_atomic_publication import _load, AGENT
    analyzer = _load("incident_legacy_analyzer", AGENT / "analyzer_research_engine_v62.py")
    path, digest = _receipt(tmp_path)
    monkeypatch.setenv(PATH_ENV, str(path))
    monkeypatch.setenv(HASH_ENV, digest)
    rows = [
        {"trade_id": "old-unbound"},
        {"trade_id": "clean", "episode_id": "clean", "epoch_id": "epoch", "signal_utc": "2026-09-05T05:00:00Z"},
        {"trade_id": "bad", "episode_id": "bad", "epoch_id": "epoch", "signal_utc": "2026-09-05T03:10:00Z"},
        {"trade_id": "variant", "episode_id": "bad", "epoch_id": "epoch", "signal_utc": "2026-09-05T05:00:00Z"},
    ]
    monkeypatch.setattr(analyzer, "_research_opportunity_universe", lambda: rows)
    monkeypatch.setattr(analyzer, "_cohort_eligible_trade_ids", lambda rows, cohort: ({"clean", "bad", "variant"}, {}))
    eligible, excluded, count = analyzer._analysis_eligible_trade_ids()
    assert eligible == {"clean"}
    assert excluded == {REASON: 2}
    assert count == 4


def test_legacy_exit_grid_requires_absolute_replay_timing(tmp_path, monkeypatch):
    from test_analyzer_atomic_publication import _load, AGENT
    analyzer = _load("incident_exit_analyzer", AGENT / "analyzer_research_engine_v62.py")
    path, digest = _receipt(tmp_path)
    monkeypatch.setenv(PATH_ENV, str(path))
    monkeypatch.setenv(HASH_ENV, digest)
    monkeypatch.setattr(analyzer, "_analysis_eligible_trade_ids", lambda cohort: ({"trade"}, {}, 1))
    monkeypatch.setattr(analyzer, "_load_jsonl_replays", lambda: {"trade": {"ticks": [{"t": 10, "price": 100}]}})
    monkeypatch.setattr(analyzer, "_load_jsonl_by_trade_id", lambda path: {"trade": {"timestamp": "2026-09-05T05:00:00Z"}})
    monkeypatch.setattr(analyzer, "analyzer_report_path", lambda name: str(tmp_path / name))
    report = analyzer.qualified_exit_policy_grid_report()
    assert report["grid_exclusion_reason_counts"][REASON] == 1
    assert report["qualified_costed_replays"] == 0
    assert report["live_policy_change_allowed"] is False
    assert report["runtime_identity_incident_input"]["receipt_sha256"] == digest

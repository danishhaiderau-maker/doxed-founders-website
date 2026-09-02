import hashlib
import json
import time
import uuid
from pathlib import Path

import lifecycle_pipeline
import lifecycle_pipeline_worker
import research_v3_store
from lifecycle_bundles import LifecycleKey
from microstructure_tape import build_bucket


NOW = 20_000.0
KEY = LifecycleKey("epoch-1", "episode-1", "policy-1", "CONTINUOUS")
PROV = {
    "source_revision": "src",
    "deployed_revision": "dep",
    "tile_config_signature": "tile",
}


def _row(ledger, record_id, **extra):
    return {
        "ledger": ledger,
        "record_id": record_id,
        "epoch_id": KEY.collection_epoch_id,
        "episode_id": KEY.episode_id,
        "policy_signature": KEY.policy_signature,
        "research_lane": KEY.research_lane,
        "event_id": "trade-1",
        "observed_ts": 10_000.0,
        **PROV,
        **extra,
    }


def _ready_rows():
    return [
        _row(
            "order_intent",
            "schedule",
            intent_kind="AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL",
            schedule_lifecycle_final=True,
            chase_schedule_authoritative=True,
            schedule_sha256="a" * 64,
            chase_schedule={"terminal_ts": 10_000.0, "terminal_reason": "TTL_EXPIRED"},
        ),
        _row("lifecycle", "terminal", terminal=True, terminal_no_fill=True),
        _row(
            "market_segment",
            "post",
            context_role="POST_EXIT_PATH",
            coverage={"complete": True, "gaps_absent": True, "complete_through_ts": 18_000.0},
        ),
    ]


def _append(root: Path, row):
    path = root / "v3" / "ledgers" / f"{row['ledger']}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")


def _patch_provenance(monkeypatch):
    monkeypatch.setattr(lifecycle_pipeline, "_collection_provenance", lambda: dict(PROV))
    monkeypatch.setattr(research_v3_store, "_collection_provenance", lambda: dict(PROV))


def test_pipeline_requires_reindexed_completion_before_materialization(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    for row in _ready_rows():
        _append(tmp_path, row)

    first = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert first["completion_appended_count"] == 1
    assert first["bundle_count"] == 0
    assert first["results"][0]["stage"] == "COMPLETION_PENDING_REINDEX"
    assert first["scan"]["pending_dirty_lifecycles"] == 1
    assert first["source_cleanup_authorized"] is False

    second = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert second["completion_appended_count"] == 0
    assert second["bundle_count"] == 1
    assert second["results"][0]["stage"] == "BUNDLE_MATERIALIZED_OR_VERIFIED"
    assert second["scan"]["pending_dirty_lifecycles"] == 0

    third = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert third["candidate_count"] == 0
    completion_rows = [
        json.loads(line)
        for line in (tmp_path / "v3" / "ledgers" / "lifecycle.jsonl").read_text().splitlines()
        if "bundle_completion" in line
    ]
    assert len(completion_rows) == 1


def test_incomplete_is_unknown_and_late_evidence_redirties(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    rows = _ready_rows()[:-1]
    for row in rows:
        _append(tmp_path, row)
    first = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert first["results"][0]["stage"] == "QUALIFICATION_EVIDENCE_RETRY_QUEUED"
    assert "POST_OBSERVATION" in " ".join(first["results"][0]["blockers"])
    assert first["scan"]["pending_dirty_lifecycles"] == 0

    _append(tmp_path, _ready_rows()[-1])
    second = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert second["completion_appended_count"] == 1
    assert second["scan"]["pending_dirty_lifecycles"] == 1


def test_immature_terminal_is_durably_deferred_and_promoted_at_maturity(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    monkeypatch.setattr(lifecycle_pipeline, "QUALIFICATION_HORIZON_SEC", 100.0)
    rows = _ready_rows()[:-1]
    rows[0]["chase_schedule"]["terminal_ts"] = 19_950.0
    for row in rows:
        _append(tmp_path, row)
    first = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert first["results"][0]["stage"] == "QUALIFICATION_DEFERRED_UNTIL_MATURITY"
    assert first["transfer_ready_count"] == 1
    assert first["transfer_bundle_count"] == 1
    assert first["results"][0]["transfer_stage"] == (
        "TRANSFER_BUNDLE_MATERIALIZED_OR_VERIFIED"
    )
    transfer_manifest = first["results"][0]["transfer_bundle"]["manifest"]
    assert transfer_manifest["maturity"] == "TRANSFER_READY"
    assert transfer_manifest["qualification_ready"] is False
    assert transfer_manifest["ranking_eligible"] is False
    assert transfer_manifest["source_cleanup_authorized"] is False
    assert first["results"][0]["retry_at"] == 20_050.0
    assert first["scan"]["pending_dirty_lifecycles"] == 0

    before = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=20_049.0)
    assert before["candidate_count"] == 0
    assert before["scan"]["promoted_qualification_retries"] == 0

    due = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=20_050.0)
    assert due["candidate_count"] == 1
    assert due["scan"]["promoted_qualification_retries"] == 1
    assert due["results"][0]["stage"] == "QUALIFICATION_EVIDENCE_RETRY_QUEUED"


def test_complete_indexed_horizon_obeys_reindex_before_completion(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    rows = _ready_rows()[:-1]
    rows[0]["chase_schedule"]["terminal_ts"] = 10_000.0
    for row in rows:
        _append(tmp_path, row)
    tape = tmp_path / "market_microstructure_1s.jsonl"
    with tape.open("w", encoding="utf-8", newline="\n") as handle:
        for ts in range(10_000, 17_200):
            row = build_bucket(
                bucket_ts=ts, bid=99, ask=101, bid_qty=2, ask_qty=3,
                last=100, source_ts=ts + 0.9,
            )
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    first = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path, now=17_200.0,
        max_tape_index_bytes=16 * 1024 * 1024, max_tape_index_rows=10_000,
    )
    assert first["results"][0]["stage"] == "QUALIFICATION_HORIZON_PENDING_REINDEX"
    assert first["completion_appended_count"] == 0
    assert first["bundle_count"] == 0

    second = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=17_380.0)
    assert second["results"][0]["stage"] == "COMPLETION_PENDING_REINDEX", second["results"][0]
    assert second["completion_appended_count"] == 1
    assert second["bundle_count"] == 0

    def materialize(_root, _key, indexed_rows, **_kwargs):
        assert any(row.get("context_role") == "POST_EXIT_PATH" for row in indexed_rows)
        assert any("bundle_completion" in row for row in indexed_rows)
        return {"written": True, "completion": {"blockers": []}}
    monkeypatch.setattr(lifecycle_pipeline, "materialize_bundle", materialize)
    third = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=17_380.0)
    assert third["results"][0]["stage"] == "BUNDLE_MATERIALIZED_OR_VERIFIED"
    assert third["bundle_count"] == 1


def test_pressure_mode_clamps_all_relevant_limits(tmp_path):
    report = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path,
        now=NOW,
        max_lifecycles=25,
        max_scan_bytes=32 * 1024 * 1024,
        max_scan_rows=50_000,
        max_lifecycle_rows=100_000,
        max_lifecycle_bytes=64 * 1024 * 1024,
        pressure_mode=True,
    )
    assert report["pressure_mode"] is True
    assert report["scan"]["lifecycle_limit"] == 1
    assert report["scan"]["byte_limit"] == 1024 * 1024
    assert report["scan"]["row_limit"] == 2_000
    assert report["scan"]["max_lifecycle_rows"] == 2_000
    assert report["scan"]["max_lifecycle_bytes"] == 2 * 1024 * 1024
    assert report["source_cleanup_authorized"] is False


def _write_pressure_round_robin_ledgers(root: Path):
    ledger_dir = root / "v3" / "ledgers"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    first = json.dumps({"padding": "a" * 1_046_000}, separators=(",", ":")).encode() + b"\n"
    second = json.dumps({"padding": "b" * 5_000}, separators=(",", ":")).encode() + b"\n"
    (ledger_dir / "opportunity.jsonl").write_bytes(first)
    (ledger_dir / "pre_entry_features.jsonl").write_bytes(second)
    return first, second


def test_pressure_mode_scans_at_most_one_eligible_ledger(tmp_path):
    first, _second = _write_pressure_round_robin_ledgers(tmp_path)

    report = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path, now=NOW, pressure_mode=True,
    )

    assert tuple(report["scan"]["ledgers"]) == ("opportunity",)
    assert report["scan"]["bytes_indexed"] == len(first)
    assert report["scan"]["ledgers"]["opportunity"]["caught_up"] is True
    assert report["source_cleanup_authorized"] is False


def test_pressure_mode_next_invocation_advances_round_robin_with_full_budget(tmp_path):
    first, second = _write_pressure_round_robin_ledgers(tmp_path)
    initial = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path, now=NOW, pressure_mode=True,
    )
    assert initial["scan"]["bytes_indexed"] == len(first)

    report = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path, now=NOW, pressure_mode=True,
    )

    assert tuple(report["scan"]["ledgers"]) == ("pre_entry_features",)
    assert report["scan"]["bytes_indexed"] == len(second)
    assert report["scan"]["ledgers"]["pre_entry_features"]["caught_up"] is True
    assert report["source_cleanup_authorized"] is False


def test_emergency_closure_materializes_only_terminal_lifecycle(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    for row in _ready_rows():
        _append(tmp_path, row)
    reports = []
    for _attempt in range(2 * len(lifecycle_pipeline.LEDGER_NAMES)):
        report = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
            tmp_path, now=NOW, pressure_mode=True, emergency_closure_mode=True,
        )
        reports.append(report)
        assert report["emergency_closure_mode"] is True
        assert len(report["scan"]["ledgers"]) <= 1
        assert report["source_cleanup_authorized"] is False
        if report["bundle_count"] == 1:
            break
    assert any(report["transfer_bundle_count"] == 1 for report in reports)
    assert sum(report["completion_appended_count"] for report in reports) == 1
    assert reports[-1]["bundle_count"] == 1
    assert (tmp_path / "v3" / "ledgers" / "lifecycle.jsonl").is_file()


def test_emergency_closure_defers_nonterminal_without_research_expansion(tmp_path):
    _append(tmp_path, _row("opportunity", "new-opportunity", terminal=False))
    source = tmp_path / "v3" / "ledgers" / "opportunity.jsonl"
    before = source.read_bytes()
    report = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path, now=NOW, pressure_mode=True, emergency_closure_mode=True,
    )
    assert report["candidate_count"] == 1
    assert report["results"][0]["stage"] == "EMERGENCY_NONTERMINAL_BLOCKED"
    assert report["transfer_bundle_count"] == 0
    assert report["completion_appended_count"] == 0
    assert report["bundle_count"] == 0
    assert source.read_bytes() == before
    assert not (tmp_path / "v3" / "lifecycle_transfer_bundles").exists()
    assert not (tmp_path / "v3" / "lifecycle_bundles").exists()
    assert report["source_cleanup_authorized"] is False


def test_runtime_provenance_mismatch_remains_dirty_for_later_retry(tmp_path, monkeypatch):
    for row in _ready_rows():
        _append(tmp_path, row)
    monkeypatch.setattr(
        lifecycle_pipeline,
        "_collection_provenance",
        lambda: {**PROV, "deployed_revision": "new-deploy"},
    )
    first = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert first["results"][0]["stage"] == "RUNTIME_PROVENANCE_MISMATCH"
    # Transfer preserves the internally consistent historical provenance and
    # never authorizes cleanup; qualification remains fail-closed against the
    # current runtime revision.
    assert first["transfer_bundle_count"] == 1
    assert first["results"][0]["transfer_bundle"]["manifest"][
        "source_cleanup_authorized"
    ] is False
    assert first["scan"]["pending_dirty_lifecycles"] == 1

    _patch_provenance(monkeypatch)
    second = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert second["completion_appended_count"] == 1
    assert second["results"][0]["stage"] == "COMPLETION_PENDING_REINDEX"


def test_terminal_candidate_accepts_utc_iso_close_timestamp():
    rows = _ready_rows()
    rows[0]["chase_schedule"].pop("terminal_ts", None)
    rows.append(_row(
        "execution", "close", close_ts="1970-01-01T02:46:40Z",
        execution_world="SHOWCASE_PAPER_OBSERVED",
    ))
    candidate = lifecycle_pipeline._terminal_candidate(KEY, rows)
    assert candidate is not None
    assert candidate["terminal_ts"] == 10_000.0


def test_nonce_bound_worker_result_has_request_and_result_hash(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    work = tmp_path / "v3" / "lifecycle_worker"
    work.mkdir(parents=True)
    nonce = uuid.uuid4().hex
    request = work / f"pipeline-request-{nonce}.json"
    result = work / f"pipeline-result-{nonce}.json"
    payload = {
        "schema": lifecycle_pipeline_worker.REQUEST_SCHEMA,
        "nonce": nonce,
        "data_root": str(tmp_path.resolve()),
        "work_root": str(work.resolve()),
        "source_revision": "src",
        "launched_unix": time.time(),
        "now": NOW,
        "max_lifecycles": 1,
        "max_scan_bytes": 1024,
        "max_scan_rows": 100,
        "max_lifecycle_rows": 100,
        "max_lifecycle_bytes": 1024 * 1024,
        "max_runtime_sec": 5,
        "pressure_mode": True,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    request.write_bytes(raw)
    assert lifecycle_pipeline_worker.run(request, result, nonce) == 0
    receipt = lifecycle_pipeline_worker.verify_result(request, result, nonce)
    assert receipt["nonce"] == nonce
    assert receipt["request_sha256"] == hashlib.sha256(raw).hexdigest()
    claimed = receipt.pop("result_sha256")
    assert claimed == hashlib.sha256(
        json.dumps(receipt, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    assert receipt["source_cleanup_authorized"] is False


def test_integration_api_creates_valid_confined_request(tmp_path):
    work = tmp_path / "v3" / "lifecycle_worker"
    work.mkdir(parents=True)
    launch = lifecycle_pipeline_worker.create_request(
        tmp_path,
        work,
        source_revision="revision",
        now=NOW,
        pressure_mode=True,
        max_lifecycles=1,
        max_runtime_sec=5,
    )
    payload = lifecycle_pipeline_worker._load(
        launch["request_path"], launch["result_path"], launch["nonce"]
    )
    assert payload["_request_sha256"] == launch["request_sha256"]
    assert payload["_pressure"] is True
    assert launch["max_runtime_sec"] == 5


def test_worker_rejects_sensitive_fields_without_result(tmp_path):
    work = tmp_path / "v3" / "lifecycle_worker"
    work.mkdir(parents=True)
    nonce = uuid.uuid4().hex
    request = work / f"pipeline-request-{nonce}.json"
    result = work / f"pipeline-result-{nonce}.json"
    request.write_text(json.dumps({
        "schema": lifecycle_pipeline_worker.REQUEST_SCHEMA,
        "nonce": nonce,
        "data_root": str(tmp_path.resolve()),
        "work_root": str(work.resolve()),
        "max_lifecycles": 1,
        "api_token": "must-not-cross-worker-boundary",
    }))
    assert lifecycle_pipeline_worker.run(request, result, nonce) == 1
    assert not result.exists()

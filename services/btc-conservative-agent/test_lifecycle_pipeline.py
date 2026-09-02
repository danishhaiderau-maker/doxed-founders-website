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
    "source_revision": "a" * 40,
    "deployed_revision": "b" * 40,
    "tile_config_signature": "d" * 64,
    "config_signature": "e" * 64,
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


def _queue_retry(root: Path, key: LifecycleKey, *, retry_at: float = NOW):
    connection = lifecycle_pipeline._open_incremental_index(root)
    try:
        lifecycle_pipeline._ensure_retry_queue(connection)
        lifecycle_pipeline._enqueue_retry(
            connection, key, retry_at=retry_at, reason="TEST_RETRY", now=retry_at,
        )
    finally:
        connection.close()


def _run_until(root: Path, predicate, **kwargs):
    for _attempt in range(2 * len(lifecycle_pipeline.LEDGER_NAMES)):
        report = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
            root, **kwargs,
        )
        if predicate(report):
            return report
    raise AssertionError("bounded lifecycle round-robin did not reach expected state")


def test_pipeline_requires_reindexed_completion_before_materialization(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    for row in _ready_rows():
        _append(tmp_path, row)

    first = _run_until(
        tmp_path, lambda report: report["completion_appended_count"] == 1, now=NOW,
    )
    assert first["completion_appended_count"] == 1
    assert first["bundle_count"] == 0
    assert first["results"][0]["stage"] == "COMPLETION_PENDING_REINDEX"
    assert first["scan"]["pending_dirty_lifecycles"] == 1
    assert first["scan"]["caught_up"] is False
    assert first["source_cleanup_authorized"] is False

    second = _run_until(
        tmp_path, lambda report: report["bundle_count"] == 1, now=NOW,
    )
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
    first = _run_until(tmp_path, lambda report: bool(report["results"]), now=NOW)
    assert first["results"][0]["stage"] == "QUALIFICATION_EVIDENCE_RETRY_QUEUED"
    assert "POST_OBSERVATION" in " ".join(first["results"][0]["blockers"])
    assert first["scan"]["pending_dirty_lifecycles"] == 0

    _append(tmp_path, _ready_rows()[-1])
    second = _run_until(
        tmp_path, lambda report: report["completion_appended_count"] == 1, now=NOW,
    )
    assert second["completion_appended_count"] == 1
    assert second["scan"]["pending_dirty_lifecycles"] == 1


def test_immature_terminal_is_durably_deferred_and_promoted_at_maturity(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    monkeypatch.setattr(lifecycle_pipeline, "QUALIFICATION_HORIZON_SEC", 100.0)
    rows = _ready_rows()[:-1]
    rows[0]["chase_schedule"]["terminal_ts"] = 19_950.0
    for row in rows:
        _append(tmp_path, row)
    first = _run_until(tmp_path, lambda report: bool(report["results"]), now=NOW)
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

    # The mature attempt explicitly scheduled its own later retry.  The
    # consumed maturity retry must not remain immediately due.
    again = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path, now=20_050.0,
    )
    assert again["scan"]["promoted_qualification_retries"] == 0


def test_due_retry_without_terminal_candidate_is_claimed_once(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    row = _row("lifecycle", "nonterminal", terminal=False)
    _append(tmp_path, row)
    _run_until(tmp_path, lambda report: report["scan"]["caught_up"], now=NOW)
    _queue_retry(tmp_path, KEY)

    first = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert first["scan"]["promoted_qualification_retries"] == 1
    assert first["candidate_count"] == 1
    assert first["results"][0]["stage"] == "QUALIFICATION_INCOMPLETE"
    second = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert second["scan"]["promoted_qualification_retries"] == 0
    assert second["candidate_count"] == 0
    _queue_retry(tmp_path, KEY)
    third = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert third["scan"]["promoted_qualification_retries"] == 1
    assert third["candidate_count"] == 1


def test_due_retry_with_post_observation_is_claimed_once(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    row = _row("market_segment", "post-only", context_role="POST_EXIT_PATH")
    _append(tmp_path, row)
    _run_until(tmp_path, lambda report: report["scan"]["caught_up"], now=NOW)
    _queue_retry(tmp_path, KEY)

    first = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert first["scan"]["promoted_qualification_retries"] == 1
    assert first["candidate_count"] == 1
    second = lifecycle_pipeline.process_incremental_lifecycle_pipeline(tmp_path, now=NOW)
    assert second["scan"]["promoted_qualification_retries"] == 0
    assert second["candidate_count"] == 0


def test_retry_promotion_rollback_preserves_retry_without_dirty_token(tmp_path):
    connection = lifecycle_pipeline._open_incremental_index(tmp_path)
    try:
        lifecycle_pipeline._ensure_retry_queue(connection)
        lifecycle_pipeline._enqueue_retry(
            connection, KEY, retry_at=NOW, reason="TEST_RETRY", now=NOW,
        )
        connection.execute("""
            CREATE TRIGGER reject_retry_claim BEFORE DELETE ON qualification_retry
            BEGIN SELECT RAISE(ABORT, 'claim rejected'); END
        """)
        try:
            lifecycle_pipeline._promote_due_retries(connection, now=NOW, maximum=1)
        except Exception as exc:
            assert "claim rejected" in str(exc)
        else:
            raise AssertionError("promotion unexpectedly bypassed rollback trigger")
        assert connection.execute("SELECT COUNT(*) FROM qualification_retry").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM dirty_lifecycle").fetchone()[0] == 0
    finally:
        connection.close()


def test_claimed_retry_cannot_starve_large_dirty_backlog(tmp_path, monkeypatch):
    _patch_provenance(monkeypatch)
    connection = lifecycle_pipeline._open_incremental_index(tmp_path)
    try:
        lifecycle_pipeline._ensure_retry_queue(connection)
        rows = [
            ("epoch-1", f"episode-{number:04d}", "policy-1", "CONTINUOUS")
            for number in range(4_877)
        ]
        with connection:
            connection.executemany(
                "INSERT INTO dirty_lifecycle VALUES (?, ?, ?, ?)", rows,
            )
        retry_key = LifecycleKey(*rows[0])
        lifecycle_pipeline._enqueue_retry(
            connection, retry_key, retry_at=NOW, reason="TEST_RETRY", now=NOW,
        )
    finally:
        connection.close()

    first = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path, now=NOW, max_lifecycles=5,
    )
    second = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path, now=NOW, max_lifecycles=5,
    )
    assert first["scan"]["promoted_qualification_retries"] == 1
    assert second["scan"]["promoted_qualification_retries"] == 0
    assert first["candidate_count"] == second["candidate_count"] == 5
    assert first["scan"]["pending_dirty_lifecycles"] == 4_872
    assert second["scan"]["pending_dirty_lifecycles"] == 4_867


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

    first = _run_until(
        tmp_path, lambda report: bool(report["results"]), now=17_200.0,
        max_tape_index_bytes=16 * 1024 * 1024, max_tape_index_rows=10_000,
    )
    assert first["results"][0]["stage"] == "QUALIFICATION_HORIZON_PENDING_REINDEX"
    assert first["completion_appended_count"] == 0
    assert first["bundle_count"] == 0

    second = _run_until(
        tmp_path, lambda report: report["completion_appended_count"] == 1,
        now=17_380.0,
    )
    assert second["results"][0]["stage"] == "COMPLETION_PENDING_REINDEX", second["results"][0]
    assert second["completion_appended_count"] == 1
    assert second["bundle_count"] == 0

    def materialize(_root, _key, indexed_rows, **_kwargs):
        assert any(row.get("context_role") == "POST_EXIT_PATH" for row in indexed_rows)
        assert any("bundle_completion" in row for row in indexed_rows)
        return {"written": True, "completion": {"blockers": []}}
    monkeypatch.setattr(lifecycle_pipeline, "materialize_bundle", materialize)
    third = _run_until(
        tmp_path, lambda report: report["bundle_count"] == 1, now=17_380.0,
    )
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


def _write_normal_round_robin_ledgers(root: Path):
    ledger_dir = root / "v3" / "ledgers"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    row = json.dumps({"padding": "a" * 2_096_000}, separators=(",", ":")).encode() + b"\n"
    first = row * 4
    second = json.dumps({"padding": "b" * 5_000}, separators=(",", ":")).encode() + b"\n"
    (ledger_dir / "opportunity.jsonl").write_bytes(first)
    (ledger_dir / "pre_entry_features.jsonl").write_bytes(second)
    return first, second


def test_normal_mode_does_not_fragment_next_ledger_first_record(tmp_path):
    first, _second = _write_normal_round_robin_ledgers(tmp_path)

    report = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path, now=NOW,
    )

    assert tuple(report["scan"]["ledgers"]) == ("opportunity",)
    assert report["scan"]["bytes_indexed"] == len(first)
    assert report["scan"]["ledgers"]["opportunity"]["caught_up"] is True
    assert report["scan"]["caught_up"] is False


def test_normal_mode_advances_round_robin_and_reports_full_cycle_caught_up(tmp_path):
    first, second = _write_normal_round_robin_ledgers(tmp_path)
    initial = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path, now=NOW,
    )
    assert initial["scan"]["bytes_indexed"] == len(first)
    assert initial["scan"]["caught_up"] is False

    report = lifecycle_pipeline.process_incremental_lifecycle_pipeline(
        tmp_path, now=NOW,
    )

    assert tuple(report["scan"]["ledgers"]) == ("pre_entry_features",)
    assert report["scan"]["bytes_indexed"] == len(second)
    assert report["scan"]["caught_up"] is True


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
        lambda: {**PROV, "deployed_revision": "c" * 40},
    )
    first = _run_until(tmp_path, lambda report: bool(report["results"]), now=NOW)
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
    second = _run_until(
        tmp_path, lambda report: report["completion_appended_count"] == 1, now=NOW,
    )
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

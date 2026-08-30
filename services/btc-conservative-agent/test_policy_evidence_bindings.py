import hashlib
import json
import os
from pathlib import Path

from research.policy_evidence_bindings import build_v3_binding_index, persist_v3_binding_index
from research.policy_evidence_schema import canonical_json


def _write(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _fixture(tmp_path: Path, *, roles=("ENTRY_PATH", "POST_EXIT_PATH"), schedule=True):
    v3 = tmp_path / "v3"
    identity = {"epoch_id": "epoch-1", "opportunity_id": "opportunity:1", "episode_id": "episode-1"}
    _write(v3 / "ledgers" / "opportunity.jsonl", [{**identity, "ledger": "opportunity"}])
    _write(v3 / "ledgers" / "decision.jsonl", [{
        **identity, "event_id": "decision-1", "policy_signature": "policy-1", "policy_id": "p1"
    }])
    schedule_value = {
        "schema": "schedule-v1", "authoritative": True,
        "intervals": [{"start_ts": 1, "end_ts": 2, "limit_price": 99}],
        "terminal_ts": 2, "terminal_reason": "FILLED",
    }
    intents = [{
        **identity, "policy_signature": "policy-1", "schedule_id": "schedule-1",
        "intent_kind": "AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL",
        "chase_schedule": schedule_value,
    }] if schedule else []
    _write(v3 / "ledgers" / "order_intent.jsonl", intents)
    for name in ("execution", "lifecycle"):
        _write(v3 / "ledgers" / f"{name}.jsonl", [])
    segment_rows = []
    for index, role in enumerate(roles):
        envelope = {
            "schema": "market_segment_v3", "source": "TEST_1S", "symbol": "BTCUSD",
            "timeframe": "1s", "start_ts": index, "end_ts": index + 1,
            "rows": [{"row": index}],
        }
        payload = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(payload).hexdigest()
        relative = f"v3/market_segments/{digest[:2]}/{digest}.json"
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        segment_rows.append({
            **identity, "context_role": role,
            "coverage": {"context_role": role, "conservative_bbo_depth_eligible": True},
            "segment_ref": {
                "sha256": digest, "relative_path": relative, "source": "TEST_1S",
                "symbol": "BTCUSD", "timeframe": "1s", "start_ts": index,
                "end_ts": index + 1, "row_count": 1,
            },
        })
    _write(v3 / "ledgers" / "market_segment.jsonl", segment_rows)
    return v3, schedule_value


def test_exact_binding_requires_verified_tapes_schedule_and_both_horizons(tmp_path):
    v3, schedule = _fixture(tmp_path)
    report = build_v3_binding_index(v3)
    assert report["exactly_bound_count"] == 1
    assert report["unknown_unverifiable_count"] == 0
    row = report["bindings"][0]
    assert row["schedule_sha256"] == hashlib.sha256(canonical_json(schedule).encode()).hexdigest()
    assert len(row["tape_ids"]) == 2
    assert row["conservative_outcome"] is None
    assert report["outcome_evaluation_performed"] is False
    assert report["timestamp_join_performed"] is False


def test_pre_signal_only_and_missing_schedule_fail_closed(tmp_path):
    v3, _ = _fixture(tmp_path, roles=("PRE_SIGNAL_ONLY",), schedule=False)
    row = build_v3_binding_index(v3)["bindings"][0]
    assert row["coverage_status"] == "UNKNOWN_UNVERIFIABLE"
    assert row["unknown_reason_codes"] == [
        "UNKNOWN_AUTHORITATIVE_SCHEDULE_MISSING",
        "UNKNOWN_REQUIRED_ENTRY_HORIZONS_INCOMPLETE",
        "UNKNOWN_REQUIRED_POST_EXIT_HORIZONS_INCOMPLETE",
    ]


def test_complete_all_opportunity_future_path_satisfies_explicit_required_horizons(tmp_path):
    v3, _ = _fixture(tmp_path, roles=("IGNORED_CONTEXT_ROLE",))
    path = v3 / "ledgers" / "market_segment.jsonl"
    row = json.loads(path.read_text().strip())
    row.pop("context_role", None)
    row["segment_role"] = "SIGNAL_TO_120M_FUTURE_PATH"
    row["future_path_status"] = "COMPLETE"
    row["coverage"] = {
        "conservative_bbo_depth_eligible": True,
        "required_horizons_sec": [60, 300, 900, 1800, 3600, 7200],
    }
    _write(path, [row])

    binding = build_v3_binding_index(v3)["bindings"][0]
    assert binding["exact_binding_complete"] is True
    assert binding["required_entry_horizons_complete"] is True
    assert binding["required_post_exit_horizons_complete"] is True
    assert binding["conservative_segment_roles"] == ["SIGNAL_TO_120M_FUTURE_PATH"]


def test_future_path_missing_status_or_required_horizon_remains_unknown(tmp_path):
    v3, _ = _fixture(tmp_path, roles=("IGNORED_CONTEXT_ROLE",))
    path = v3 / "ledgers" / "market_segment.jsonl"
    row = json.loads(path.read_text().strip())
    row.pop("context_role", None)
    row["segment_role"] = "SIGNAL_TO_120M_FUTURE_PATH"
    row["future_path_status"] = "UNKNOWN"
    row["coverage"] = {
        "conservative_bbo_depth_eligible": True,
        "required_horizons_sec": [60, 300, 900, 1800, 3600],
    }
    _write(path, [row])

    binding = build_v3_binding_index(v3)["bindings"][0]
    assert binding["coverage_status"] == "UNKNOWN_UNVERIFIABLE"
    assert binding["conservative_segment_roles"] == []
    assert "UNKNOWN_REQUIRED_ENTRY_HORIZONS_INCOMPLETE" in binding["unknown_reason_codes"]
    assert "UNKNOWN_REQUIRED_POST_EXIT_HORIZONS_INCOMPLETE" in binding["unknown_reason_codes"]


def test_future_path_request_does_not_poison_a_later_verified_object(tmp_path):
    v3, _ = _fixture(tmp_path, roles=("IGNORED_CONTEXT_ROLE",))
    path = v3 / "ledgers" / "market_segment.jsonl"
    complete = json.loads(path.read_text().strip())
    complete.pop("context_role", None)
    complete["segment_role"] = "SIGNAL_TO_120M_FUTURE_PATH"
    complete["future_path_status"] = "COMPLETE"
    complete["coverage"] = {
        "conservative_bbo_depth_eligible": True,
        "required_horizons_sec": [60, 300, 900, 1800, 3600, 7200],
    }
    request = {
        key: complete[key] for key in ("epoch_id", "opportunity_id", "episode_id")
    }
    request.update({
        "segment_role": "SIGNAL_TO_120M_FUTURE_PATH_REQUEST",
        "future_path_status": "PENDING", "segment_ref": None,
    })
    _write(path, [request, complete])

    binding = build_v3_binding_index(v3)["bindings"][0]
    assert binding["exact_binding_complete"] is True
    assert "UNKNOWN_TAPE_SHA256_INVALID" not in binding["unknown_reason_codes"]


def test_unrecognized_or_malformed_segment_ref_still_fails_closed(tmp_path):
    v3, _ = _fixture(tmp_path)
    path = v3 / "ledgers" / "market_segment.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows.append({
        "epoch_id": "epoch-1", "opportunity_id": "opportunity:1",
        "episode_id": "episode-1", "context_role": "ENTRY_PATH",
        "segment_ref": {},
    })
    _write(path, rows)
    binding = build_v3_binding_index(v3)["bindings"][0]
    assert binding["exact_binding_complete"] is False
    assert "UNKNOWN_TAPE_SHA256_INVALID" in binding["unknown_reason_codes"]


def test_unique_terminal_schedule_supersedes_submit_snapshot_and_conflicts_fail_closed(tmp_path):
    v3, submit = _fixture(tmp_path)
    terminal = {
        "schema": "schedule-v1", "authoritative": True,
        "intervals": [{"start_ts": 1, "end_ts": 2, "limit_price": 100}],
        "terminal_ts": 2, "terminal_reason": "FILLED",
    }
    identity = {"epoch_id": "epoch-1", "opportunity_id": "opportunity:1", "episode_id": "episode-1"}
    terminal_row = {
        **identity, "policy_signature": "policy-1", "schedule_id": "schedule-1",
        "intent_kind": "AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL",
        "chase_schedule": terminal,
        "schedule_sha256": hashlib.sha256(canonical_json(terminal).encode()).hexdigest(),
    }
    submit_row = {
        **identity, "policy_signature": "policy-1", "schedule_id": "schedule-1",
        "intent_kind": "ACTUAL_PAPER_LIMIT_SUBMIT", "chase_schedule": submit,
    }
    _write(v3 / "ledgers/order_intent.jsonl", [submit_row, terminal_row])
    binding = build_v3_binding_index(v3)["bindings"][0]
    assert binding["exact_binding_complete"] is True
    assert binding["schedule_sha256"] == terminal_row["schedule_sha256"]

    conflicting = dict(terminal_row)
    conflicting_schedule = dict(terminal)
    conflicting_schedule["terminal_reason"] = "EXPIRED"
    conflicting["chase_schedule"] = conflicting_schedule
    conflicting["schedule_sha256"] = hashlib.sha256(
        canonical_json(conflicting_schedule).encode()
    ).hexdigest()
    _write(v3 / "ledgers/order_intent.jsonl", [submit_row, terminal_row, conflicting])
    binding = build_v3_binding_index(v3)["bindings"][0]
    assert binding["exact_binding_complete"] is False
    assert "UNKNOWN_SCHEDULE_VERSION_CONFLICT" in binding["unknown_reason_codes"]


def test_submit_time_open_schedule_is_not_misrepresented_as_replay_authority(tmp_path):
    v3, schedule = _fixture(tmp_path)
    schedule.pop("terminal_ts")
    schedule.pop("terminal_reason")
    schedule["intervals"][0]["end_ts"] = None
    identity = {"epoch_id": "epoch-1", "opportunity_id": "opportunity:1", "episode_id": "episode-1"}
    _write(v3 / "ledgers/order_intent.jsonl", [{
        **identity, "policy_signature": "policy-1", "schedule_id": "schedule-1",
        "intent_kind": "ACTUAL_PAPER_LIMIT_SUBMIT", "chase_schedule": schedule,
    }])
    binding = build_v3_binding_index(v3)["bindings"][0]
    assert binding["exact_binding_complete"] is False
    assert "UNKNOWN_AUTHORITATIVE_SCHEDULE_MISSING" in binding["unknown_reason_codes"]


def test_tape_checksum_mismatch_is_unknown_not_no_fill(tmp_path):
    v3, _ = _fixture(tmp_path)
    segment = json.loads((v3 / "ledgers" / "market_segment.jsonl").read_text().splitlines()[0])
    target = tmp_path / segment["segment_ref"]["relative_path"]
    target.write_text("tampered", encoding="utf-8")
    row = build_v3_binding_index(v3)["bindings"][0]
    assert "UNKNOWN_TAPE_CHECKSUM_MISMATCH" in row["unknown_reason_codes"]
    assert row["conservative_outcome"] is None


def test_wrong_root_is_rejected(tmp_path):
    bad = tmp_path / "not-v3"
    bad.mkdir()
    try:
        build_v3_binding_index(bad)
    except ValueError as exc:
        assert str(exc) == "V3_BINDING_ROOT_MUST_BE_V3"
    else:
        raise AssertionError("wrong root accepted")


def test_persisted_index_is_generation_bound_and_rebuildable(tmp_path):
    root = tmp_path / "canonical-research-data"
    v3, _ = _fixture(root)
    manifest = {
        "entry_hash": "a" * 64, "dataset_epoch": "epoch-1", "source_revision": "rev-1",
        "tile_config_signature": "b" * 64,
    }
    (root / "canonical_dataset_current.json").write_text(json.dumps(manifest), encoding="utf-8")
    published = tmp_path / "binding-report.json"
    summary = persist_v3_binding_index(root, analyzer_revision="rev-1", summary_destination=published)
    exhaustive = root / summary["exhaustive_relative_path"]
    assert exhaustive.is_file()
    assert hashlib.sha256(exhaustive.read_bytes()).hexdigest() == summary["exhaustive_sha256"]
    assert summary["exactly_bound_count"] == 1
    assert json.loads(published.read_text())["generation"] == summary["generation"]


def test_summary_publications_are_atomic_and_leave_no_temporary_files(tmp_path):
    root = tmp_path / "canonical-research-data"
    _fixture(root)
    manifest = {
        "entry_hash": "a" * 64, "dataset_epoch": "epoch-1", "source_revision": "rev-1",
        "tile_config_signature": "b" * 64,
    }
    (root / "canonical_dataset_current.json").write_text(json.dumps(manifest), encoding="utf-8")
    published = tmp_path / "reports" / "binding-report.json"
    persist_v3_binding_index(root, analyzer_revision="rev-1", summary_destination=published)
    assert json.loads(published.read_text())["schema"] == "v3_policy_evidence_binding_index_v1"
    assert not list(published.parent.glob(".binding-report.json.*.tmp"))
    assert not list((root / "derived").rglob(".binding-index-summary.json.*.tmp"))


def test_publication_symlink_is_rejected_without_modifying_target(tmp_path):
    if not hasattr(os, "symlink"):
        return
    root = tmp_path / "canonical-research-data"
    _fixture(root)
    manifest = {
        "entry_hash": "a" * 64, "dataset_epoch": "epoch-1", "source_revision": "rev-1",
        "tile_config_signature": "b" * 64,
    }
    (root / "canonical_dataset_current.json").write_text(json.dumps(manifest), encoding="utf-8")
    outside = tmp_path / "outside.json"
    outside.write_text('{"preserved":true}', encoding="utf-8")
    link = tmp_path / "binding-report.json"
    try:
        link.symlink_to(outside)
    except OSError:
        return
    try:
        persist_v3_binding_index(root, analyzer_revision="rev-1", summary_destination=link)
    except ValueError as exc:
        assert str(exc) == "BINDING_PUBLICATION_TARGET_SYMLINK_FORBIDDEN"
    else:
        raise AssertionError("symlink publication target accepted")
    assert outside.read_text(encoding="utf-8") == '{"preserved":true}'

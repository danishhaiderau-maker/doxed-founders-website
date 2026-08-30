import hashlib
import gzip
import json
import sqlite3
from pathlib import Path

import pytest

from research.policy_evidence_evaluator import (
    build_v3_conservative_results, persist_v3_conservative_results,
)
from research.policy_evidence_schema import canonical_json
from research.quantity_execution import build_signed_quantity_constraints


def _write(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _constraints():
    return build_signed_quantity_constraints(
        symbol="BTCUSD", quantity_step="0.1", quantity_precision=1,
        min_lot="0.1", min_notional="1", captured_at="2026-01-01T00:00:00Z",
        source_revision="rev-1", source="fixture",
    )


def _row(ts, *, bid=99, ask=101, bid_qty=1, ask_qty=1):
    return {
        "schema": "market_microstructure_1s_v1", "symbol": "BTCUSD",
        "bucket_ts": ts, "fresh": True, "valid_bbo": True,
        "bid": bid, "ask": ask, "bid_qty": bid_qty, "ask_qty": ask_qty,
        "trade_count": 0, "buy_qty": 0, "sell_qty": 0,
    }


def _segment(v3, identity, role, rows, index):
    envelope = {
        "schema": "market_segment_v3", "source": "TEST_1S", "symbol": "BTCUSD",
        "timeframe": "1s", "start_ts": min(r["bucket_ts"] for r in rows),
        "end_ts": max(r["bucket_ts"] for r in rows) + 1, "rows": rows,
    }
    payload = canonical_json(envelope).encode()
    digest = hashlib.sha256(payload).hexdigest()
    relative = f"v3/market_segments/{digest[:2]}/{digest}.json"
    target = v3.parent / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return {
        **identity, "event_id": f"segment-{index}", "context_role": role,
        "coverage": {"context_role": role, "conservative_bbo_depth_eligible": True},
        "segment_ref": {"sha256": digest, "relative_path": relative, "source": "TEST_1S",
                        "symbol": "BTCUSD", "timeframe": "1s",
                        "start_ts": envelope["start_ts"], "end_ts": envelope["end_ts"],
                        "row_count": len(rows)},
    }


def _fixture(tmp_path, *, direction="LONG", entry_rows=None, qty=1, constraints=True):
    v3 = tmp_path / "v3"
    identity = {"epoch_id": "epoch-1", "opportunity_id": "opp-1", "episode_id": "ep-1"}
    decision = {**identity, "event_id": "decision-1", "policy_id": "policy-1",
                "policy_signature": "sig-1", "direction": direction,
                "policy_family": "ATR_TRAIL", "entry_offset_pct": .1,
                "chase_policy": "CHASE_13M", "exit_family": "ATR", "regime": "TREND", "split": "OOS"}
    _write(v3 / "ledgers/opportunity.jsonl", [{**identity, "raw_direction": direction}])
    _write(v3 / "ledgers/decision.jsonl", [decision])
    schedule = {"schema": "schedule-v1", "authoritative": True,
                "intervals": [{"bucket_id": "s0", "start_ts": 10, "end_ts": 12,
                               "limit_price": 100}],
                "terminal_ts": 12, "terminal_reason": "FILLED"}
    intent = {**identity, "event_id": "intent-1", "policy_signature": "sig-1",
              "intent_kind": "AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL",
              "schedule_id": "schedule-1", "chase_schedule": schedule,
              "schedule_sha256": hashlib.sha256(canonical_json(schedule).encode()).hexdigest(),
              "requested_qty": qty, "symbol": "BTCUSD"}
    if constraints:
        intent["signed_quantity_constraints"] = _constraints()
    _write(v3 / "ledgers/order_intent.jsonl", [intent])
    rows = entry_rows if entry_rows is not None else [_row(10, ask=100), _row(11, ask=100)]
    segments = [_segment(v3, identity, "ENTRY_PATH", rows, 1),
                _segment(v3, identity, "POST_EXIT_PATH", [_row(20)], 2)]
    _write(v3 / "ledgers/market_segment.jsonl", segments)
    for name in ("execution", "lifecycle"):
        _write(v3 / f"ledgers/{name}.jsonl", [])
    return v3


@pytest.mark.parametrize("direction,rows", [
    ("LONG", [_row(10, ask=100), _row(11, ask=100)]),
    ("SHORT", [_row(10, bid=100), _row(11, bid=100)]),
    ("LONG", [_row(10, ask=99), _row(11, ask=99)]),
])
def test_buy_sell_touch_and_trade_through_are_full_fills(tmp_path, direction, rows):
    result = build_v3_conservative_results(_fixture(tmp_path, direction=direction, entry_rows=rows))
    row = result["results"][0]
    assert row["classification"] == "FULL_FILL"
    assert row["filled_qty"] == 1
    assert row["comparison_cohort_key"].startswith("cohort-")


def test_partial_fill_preserves_all_quantity_boundaries(tmp_path):
    rows = [_row(10, ask=100, ask_qty=.45), _row(11, ask=100, ask_qty=.45)]
    row = build_v3_conservative_results(_fixture(tmp_path, entry_rows=rows))["results"][0]
    assert row["classification"] == "PARTIAL_FILL"
    assert row["requested_qty"] == 1
    assert row["available_qty"] == .45
    assert row["raw_partial_qty"] == .45
    assert row["rounded_executable_qty"] == .4
    assert row["accumulated_qty"] == .4
    assert row["minimum_lot_decision"] == "PASS"
    assert row["minimum_notional_decision"] == "PASS"
    assert row["quantity_attempts"][0]["accepted"] is True


def test_complete_non_crossing_tape_is_true_no_fill(tmp_path):
    rows = [_row(10, ask=101), _row(11, ask=101)]
    row = build_v3_conservative_results(_fixture(tmp_path, entry_rows=rows))["results"][0]
    assert row["classification"] == "NO_FILL"
    assert row["supported"] is True
    assert row["filled_qty"] == 0


def test_terminal_authoritative_schedule_is_used_instead_of_open_submit_version(tmp_path):
    v3 = _fixture(tmp_path)
    path = v3 / "ledgers/order_intent.jsonl"
    submit = json.loads(path.read_text().strip())
    submit_schedule = dict(submit["chase_schedule"])
    submit_schedule["intervals"] = [{"bucket_id": "s0", "start_ts": 10,
                                      "end_ts": None, "limit_price": 101}]
    submit["intent_kind"] = "ACTUAL_PAPER_LIMIT_SUBMIT"
    submit["chase_schedule"] = submit_schedule
    submit["schedule_sha256"] = hashlib.sha256(
        canonical_json(submit_schedule).encode()
    ).hexdigest()
    terminal = dict(submit)
    terminal_schedule = {
        "authoritative": True,
        "intervals": [{"bucket_id": "s0", "start_ts": 10,
                       "end_ts": 12, "limit_price": 100}],
        "terminal_ts": 12, "terminal_reason": "FILLED",
    }
    terminal["intent_kind"] = "AUTHORITATIVE_PAPER_SCHEDULE_TERMINAL"
    terminal["chase_schedule"] = terminal_schedule
    terminal["schedule_sha256"] = hashlib.sha256(
        canonical_json(terminal_schedule).encode()
    ).hexdigest()
    _write(path, [submit, terminal])

    row = build_v3_conservative_results(v3)["results"][0]
    assert row["classification"] == "FULL_FILL"
    assert row["schedule_sha256"] == terminal["schedule_sha256"]


def test_complete_all_opportunity_future_tape_is_usable_as_exact_entry_path(tmp_path):
    v3 = _fixture(tmp_path)
    path = v3 / "ledgers/market_segment.jsonl"
    segments = [json.loads(line) for line in path.read_text().splitlines()]
    future = segments[0]
    future.pop("context_role", None)
    future["segment_role"] = "SIGNAL_TO_120M_FUTURE_PATH"
    future["future_path_status"] = "COMPLETE"
    future["coverage"] = {
        "conservative_bbo_depth_eligible": True,
        "required_horizons_sec": [60, 300, 900, 1800, 3600, 7200],
    }
    _write(path, [future])

    row = build_v3_conservative_results(v3)["results"][0]
    assert row["classification"] == "FULL_FILL"
    assert row["supported"] is True


def test_recovery_overlay_supersedes_raw_unknown_for_evaluation(tmp_path):
    v3 = _fixture(tmp_path)
    path = v3 / "ledgers/market_segment.jsonl"
    segments = [json.loads(line) for line in path.read_text().splitlines()]
    future = segments[0]
    future.pop("context_role", None)
    future.update({
        "record_id": "recovered-complete",
        "future_path_owner_key": "owner-1",
        "segment_role": "SIGNAL_TO_120M_FUTURE_PATH",
        "future_path_status": "COMPLETE",
        "coverage": {
            "conservative_bbo_depth_eligible": True,
            "required_horizons_sec": [60, 300, 900, 1800, 3600, 7200],
        },
    })
    unknown = {
        key: future[key] for key in ("epoch_id", "opportunity_id", "episode_id")
    }
    unknown.update({
        "record_id": "raw-unknown", "future_path_owner_key": "owner-1",
        "segment_role": "SIGNAL_TO_120M_FUTURE_PATH",
        "future_path_status": "UNKNOWN", "segment_ref": None,
    })
    _write(path, [unknown])
    _write(v3 / "recovery_ledgers/market_segment.jsonl", [future])

    row = build_v3_conservative_results(v3)["results"][0]
    assert row["classification"] == "FULL_FILL"
    assert row["supported"] is True
    assert row["tape_ids"] == [future["segment_ref"]["sha256"]]


def test_incomplete_all_opportunity_future_tape_remains_unknown(tmp_path):
    v3 = _fixture(tmp_path)
    path = v3 / "ledgers/market_segment.jsonl"
    segments = [json.loads(line) for line in path.read_text().splitlines()]
    future = segments[0]
    future.pop("context_role", None)
    future["segment_role"] = "SIGNAL_TO_120M_FUTURE_PATH"
    future["future_path_status"] = "COMPLETE"
    future["coverage"] = {
        "conservative_bbo_depth_eligible": True,
        "required_horizons_sec": [60, 300, 900, 1800, 3600],
    }
    _write(path, [future])

    row = build_v3_conservative_results(v3)["results"][0]
    assert row["classification"] == "UNKNOWN"
    assert "UNKNOWN_FUTURE_ENTRY_PATH_INCOMPLETE" in row["unknown_reason_codes"]


def test_missing_market_second_is_unknown_not_no_fill(tmp_path):
    row = build_v3_conservative_results(
        _fixture(tmp_path, entry_rows=[_row(10, ask=101)])
    )["results"][0]
    assert row["classification"] == "UNKNOWN"
    assert "UNKNOWN_CONSERVATIVE_EVALUATOR_EVIDENCE_GAP" in row["unknown_reason_codes"]


def test_missing_constraints_is_unknown_and_never_speculates(tmp_path):
    row = build_v3_conservative_results(
        _fixture(tmp_path, constraints=False)
    )["results"][0]
    assert row["classification"] == "UNKNOWN"
    assert any("SIGNED_QUANTITY_CONSTRAINTS_MISSING" in reason for reason in row["unknown_reason_codes"])


def test_generation_bound_artifact_and_query_cache_retain_every_result(tmp_path):
    root = tmp_path / "canonical-research-data"
    _fixture(root, entry_rows=[_row(10, ask=101), _row(11, ask=101)])
    manifest = {"entry_hash": "a" * 64, "dataset_epoch": "epoch-1",
                "source_revision": "rev-1", "tile_config_signature": "b" * 64}
    (root / "canonical_dataset_current.json").write_text(json.dumps(manifest), encoding="utf-8")
    summary = persist_v3_conservative_results(root, analyzer_revision="rev-1")
    artifact = root / summary["relative_path"]
    with gzip.open(artifact, "rt", encoding="utf-8") as handle:
        stored = [json.loads(line) for line in handle if line.strip()]
    assert len(stored) == summary["cache_rows_ingested"] == 1
    assert stored[0]["classification"] == "NO_FILL"
    cache = artifact.parent / "results.sqlite"
    with sqlite3.connect(cache) as connection:
        assert connection.execute("SELECT classification FROM episode_policy_result").fetchone()[0] == "NO_FILL"


def test_current_v3_nested_dimensions_are_preserved_and_queryable(tmp_path):
    root = tmp_path / "canonical-research-data"
    v3 = _fixture(root, entry_rows=[_row(10, ask=101), _row(11, ask=101)])
    decision_path = v3 / "ledgers/decision.jsonl"
    decision = json.loads(decision_path.read_text().strip())
    for field in ("direction", "policy_family", "entry_offset_pct", "chase_policy",
                  "exit_family", "regime"):
        decision.pop(field, None)
    decision.update({
        "executed_direction": "LONG", "raw_ai_decision": "APPROVE",
        "long_score": 78, "short_score": 22, "score_gap": 56,
        "paper_policy_spec": {
            "schema": "paper_policy_identity_spec_v3",
            "entry_offset_fraction": 0.003,
            "entry_limit_policy": "OFFSET_0.30_CHASE_W234_S50_I180",
            "exit_config": {"family": "CHANDELIER", "exit_profile_id": "CHANDELIER_1.5"},
        },
    })
    _write(decision_path, [decision])
    opportunity_path = v3 / "ledgers/opportunity.jsonl"
    opportunity = json.loads(opportunity_path.read_text().strip())
    opportunity["feature_snapshot_at_signal"] = {
        "market_context": {"regime_label": "BULL"}
    }
    _write(opportunity_path, [opportunity])
    manifest = {"entry_hash": "a" * 64, "dataset_epoch": "epoch-1",
                "source_revision": "rev-1", "tile_config_signature": "b" * 64}
    (root / "canonical_dataset_current.json").write_text(json.dumps(manifest), encoding="utf-8")
    summary = persist_v3_conservative_results(root, analyzer_revision="rev-1")
    assert summary["cache_rows_ingested"] == 1

    from research.policy_evidence_library import PolicyEvidenceLibrary
    library = PolicyEvidenceLibrary(str(root), manifest, analyzer_revision="rev-1")
    result = library.query({
        "evidence_world": "CONSERVATIVE_BBO_DEPTH_TAPE",
        "entry_offset_pct": "0.30", "family": "CHANDELIER",
        "chase_policy": "OFFSET_0.30_CHASE_W234_S50_I180",
        "exit_family": "CHANDELIER_1.5", "regime": "BULL",
        "side": "LONG", "ai_direction": "LONG", "ai_decision": "APPROVE",
        "policy_signature": "sig-1", "opportunity_id": "opp-1",
    })
    assert result["row_count"] == 1
    stored = result["rows"][0]
    assert stored["entry_offset_pct"] == "0.30"
    assert stored["long_score"] == 78
    assert stored["short_score"] == 22
    assert stored["score_gap"] == 56


def test_missing_opportunity_identity_remains_unknown_in_artifact_and_is_explicitly_skipped_from_cache(tmp_path):
    root = tmp_path / "canonical-research-data"
    v3 = _fixture(root)
    decision_path = v3 / "ledgers/decision.jsonl"
    decision = json.loads(decision_path.read_text().strip())
    decision["opportunity_id"] = None
    _write(decision_path, [decision])
    manifest = {"entry_hash": "a" * 64, "dataset_epoch": "epoch-1",
                "source_revision": "rev-1", "tile_config_signature": "b" * 64}
    (root / "canonical_dataset_current.json").write_text(json.dumps(manifest), encoding="utf-8")
    summary = persist_v3_conservative_results(root, analyzer_revision="rev-1")
    assert summary["row_count"] == 1
    assert summary["cache_rows_ingested"] == 0
    assert summary["cache_rows_skipped_missing_identity"] == 1
    assert summary["cache_skip_reason_counts"] == {
        "RESULT_IDENTITY_MISSING_COMPARISON_COHORT_KEY": 1,
        "RESULT_IDENTITY_MISSING_OPPORTUNITY_ID": 1,
    }
    artifact = root / summary["relative_path"]
    with gzip.open(artifact, "rt", encoding="utf-8") as handle:
        row = json.loads(next(handle))
    assert row["opportunity_id"] is None
    assert row["comparison_cohort_key"] is None
    assert row["classification"] == "UNKNOWN"
    assert "UNKNOWN_CAUSAL_IDENTITY_INCOMPLETE" in row["unknown_reason_codes"]

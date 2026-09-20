import hashlib
import importlib.util
import json
from pathlib import Path

from research.analyzer_current_collection_contract import (
    load_current_collection_contract,
    select_current_rows,
    unique_exact_match,
)


ROOT = Path(__file__).resolve().parent


def _load_analyzer():
    spec = importlib.util.spec_from_file_location(
        "analyzer_current_collection_test",
        ROOT / "analyzer_research_engine_v62.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _manifest(root, *, epoch="epoch-current", revision="a" * 40, config="tiles-current"):
    body = {
        "schema": "canonical_research_manifest_v1",
        "recorded_at": "2033-01-01T00:00:00Z",
        "previous_entry_hash": None,
        "dataset_epoch": epoch,
        "source_revision": revision,
        "deployed_revision": revision,
        "tile_config_signature": config,
        "dataset_checksum": "b" * 64,
    }
    body["entry_hash"] = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()
    (root / "canonical_dataset_current.json").write_text(json.dumps(body), encoding="utf-8")
    return body


def _schedule(*, epoch, episode, opportunity, side, policy):
    common = {
        "schema": "compressed_chase_shadow_v1",
        "execution_class": "SHADOW_ONLY",
        "places_order": False,
        "relay_eligible": False,
        "trade_id": f"trade-{episode}",
        "direction": side,
        "shared_ai_call_id": "shared-call",
        "opportunity_id": opportunity,
        "episode_id": episode,
        "epoch_id": epoch,
        "policy_id": f"policy-{policy}",
        "policy_signature": policy,
        "schedule_generation_id": f"schedule-{episode}-{policy}",
        "identity_complete": True,
        "missing_identity_fields": [],
        "signal_ts": 1000.0,
        "expires_ts": 1001.0,
        "tape_window_start_ts": 1000.0,
        "tape_window_end_ts": 1001.0,
        "requested_qty": 1.0,
        "entry_fee_rate": None,
        "exit_fee_rate": None,
        "slippage_model": "",
        "schedule_seconds": [0],
        "terminal_expiry_sec": 1,
    }
    return [
        {
            **common, "event": "STAGE", "stage_index": 0,
            "stage_due_sec": 0, "observed_ts": 1000.0,
            "scheduled_due_ts": 1000.0, "observed_delay_sec": 0.0,
            "virtual_limit_price": 100.0, "reference_price": 100.0,
            "bbo": {"bid": 99.0, "ask": 100.0, "last": 99.5},
            "bbo_fresh": True, "bbo_valid": True,
            "coverage_status": "OBSERVED", "eligible_at_stage": True,
        },
        {
            **common, "event": "EXPIRED", "stage_index": None,
            "stage_due_sec": None, "observed_ts": 1001.0,
            "bbo": {"bid": 100.0, "ask": 101.0, "last": 100.5},
            "bbo_fresh": True, "bbo_valid": True,
            "coverage_status": "OBSERVED", "eligible_at_stage": False,
        },
    ]


def test_contract_filters_epoch_and_declared_generation_fields(tmp_path):
    manifest = _manifest(tmp_path)
    contract, blockers = load_current_collection_contract(tmp_path)
    assert blockers == []
    assert contract["manifest_entry_hash"] == manifest["entry_hash"]
    selected, blockers = select_current_rows([
        {"epoch_id": "epoch-current", "record_id": "same"},
        {"epoch_id": "epoch-old", "record_id": "same"},
        {
            "epoch_id": "epoch-current", "record_id": "wrong-revision",
            "event_source_revision": "c" * 40,
        },
        {
            "epoch_id": "epoch-current", "dataset_epoch": "epoch-old",
            "record_id": "conflicting-epoch-aliases",
        },
    ], contract, source="FIXTURE")
    assert [row["record_id"] for row in selected] == ["same"]
    assert blockers == [
        "FIXTURE:EPOCH_DECLARATION_CONFLICT",
        "FIXTURE:EPOCH_MISMATCH",
        "FIXTURE:SOURCE_REVISION_MISMATCH",
    ]


def test_exact_join_keeps_children_and_policies_distinct():
    rows = [
        {"epoch_id": "epoch", "episode_id": "child-long", "policy_signature": "p1", "direction": "LONG"},
        {"epoch_id": "epoch", "episode_id": "child-short", "policy_signature": "p1", "direction": "SHORT"},
        {"epoch_id": "epoch", "episode_id": "child-long", "policy_signature": "p2", "direction": "LONG"},
    ]
    match, blockers = unique_exact_match(rows, {
        "epoch_id": "epoch", "episode_id": "child-long",
        "policy_signature": "p2", "direction": "LONG",
    }, source="CHILD")
    assert blockers == []
    assert match["policy_signature"] == "p2"
    assert unique_exact_match(
        rows, {"epoch_id": "epoch", "episode_id": "child-long"}, source="CHILD"
    ) == (None, ["CHILD:EXACT_JOIN_AMBIGUOUS"])
    assert unique_exact_match(
        rows, {"epoch_id": "epoch", "episode_id": ""}, source="CHILD"
    ) == (None, ["CHILD:JOIN_IDENTITY_INCOMPLETE"])


def test_empty_fresh_contract_rejects_tempting_stale_legacy_and_v3(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    _manifest(tmp_path)
    (tmp_path / "trades_3factor.csv").write_text(
        "trade_id,net_pnl_usd\nstale-winner,99\n", encoding="utf-8"
    )
    (tmp_path / "dynamic_policy_analysis_report.json").write_text(
        '{"status":"PASS","winner":"stale"}', encoding="utf-8"
    )
    _write_jsonl(
        tmp_path / "chase_offset_touch_grid.jsonl",
        _schedule(
            epoch="epoch-old", episode="same", opportunity="opportunity:same",
            side="LONG", policy="stale-policy",
        ),
    )
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    report = analyzer.build_missed_opportunity_proof_report(session={})
    assert report["proof_count"] == 0
    assert report["classification_counts"]["PROVEN_MISSED_PROFIT"] == 0
    assert report["empty_reason"] == "CURRENT_COLLECTION_EMPTY_OR_STALE"
    assert "COMPRESSED_SHADOW:EPOCH_MISMATCH" in report["current_collection_blockers"]


def test_current_children_keep_selected_side_separate_from_raw_reject(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    _manifest(tmp_path)
    schedules = []
    schedules += _schedule(
        epoch="epoch-current", episode="child-long", opportunity="opportunity:long",
        side="LONG", policy="policy-long",
    )
    schedules += _schedule(
        epoch="epoch-current", episode="child-short", opportunity="opportunity:short",
        side="SHORT", policy="policy-short",
    )
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", schedules)
    _write_jsonl(tmp_path / "v3" / "ledgers" / "opportunity.jsonl", [
        {
            "record_id": "opportunity:long", "opportunity_id": "opportunity:long",
            "episode_id": "child-long", "shared_ai_call_id": "shared-call",
            "epoch_id": "epoch-current", "raw_direction": "NO_TRADE",
        },
        {
            "record_id": "opportunity:short", "opportunity_id": "opportunity:short",
            "episode_id": "child-short", "shared_ai_call_id": "shared-call",
            "epoch_id": "epoch-current", "raw_direction": "NO_TRADE",
        },
    ])
    _write_jsonl(tmp_path / "v3" / "ledgers" / "decision.jsonl", [
        {
            "record_id": "decision:long", "episode_id": "child-long",
            "shared_ai_call_id": "shared-call", "epoch_id": "epoch-current",
            "policy_signature": "source-long", "raw_ai_decision": "REJECT",
            "executed_direction": "NO_TRADE",
        },
        {
            "record_id": "decision:short", "episode_id": "child-short",
            "shared_ai_call_id": "shared-call", "epoch_id": "epoch-current",
            "policy_signature": "source-short", "raw_ai_decision": "REJECT",
            "executed_direction": "NO_TRADE",
        },
    ])
    _write_jsonl(tmp_path / "v3" / "ledgers" / "execution.jsonl", [])
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    report = analyzer.build_missed_opportunity_proof_report(session={})
    assert report["proof_count"] == 2
    assert {
        (row["episode_id"], row["policy_signature"], row["selected_simulation_side"])
        for row in report["proofs"]
    } == {
        ("child-long", "policy-long", "LONG"),
        ("child-short", "policy-short", "SHORT"),
    }
    assert all(row["raw_ai_decision"] == "REJECT" for row in report["proofs"])
    assert all(row["original_ai_direction"] == "NO_TRADE" for row in report["proofs"])
    assert all(row["classification"] == "INSUFFICIENT_EVIDENCE" for row in report["proofs"])
    assert all(row["cost_assumption"]["explicit_costs_complete"] is False for row in report["proofs"])


def test_current_v3_complete_signed_evidence_is_still_accepted(tmp_path, monkeypatch):
    from test_missed_opportunity_proof import _signed_rows, _write_complete_canonical_tape

    analyzer = _load_analyzer()
    _manifest(tmp_path, epoch="epoch-1", revision="test-revision")
    signed_rows = _signed_rows()
    for row in signed_rows:
        row.update(
            event_source_revision="test-revision",
            event_config_signature="tiles-current",
            quantity_constraint_source_revision_match=True,
        )
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", signed_rows)
    _write_complete_canonical_tape(tmp_path)
    tape_path = tmp_path / "market_microstructure_1s.jsonl"
    tape_rows = [json.loads(line) for line in tape_path.read_text().splitlines()]
    for tape_row in tape_rows:
        tape_row.update(
            source_ts=tape_row["bucket_ts"],
            observed_at_ts=tape_row["bucket_ts"],
            quote_valid_from_ts=tape_row["bucket_ts"],
        )
        tape_row.pop("row_sha256", None)
        tape_row["row_sha256"] = hashlib.sha256(json.dumps(
            tape_row, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
    _write_jsonl(tape_path, tape_rows)
    _write_jsonl(tmp_path / "v3" / "ledgers" / "opportunity.jsonl", [{
        "record_id": "opportunity:episode-1",
        "opportunity_id": "opportunity:episode-1",
        "episode_id": "episode-1",
        "shared_ai_call_id": "scan-1",
        "epoch_id": "epoch-1",
        "signal_ts": 1000,
        "raw_direction": "LONG",
        "feature_snapshot_at_signal": {"regime": "TREND", "adx": 31.5},
    }])
    _write_jsonl(tmp_path / "v3" / "ledgers" / "decision.jsonl", [{
        "record_id": "decision:episode-1",
        "episode_id": "episode-1",
        "shared_ai_call_id": "scan-1",
        "epoch_id": "epoch-1",
        "raw_ai_decision": "APPROVE",
        "executed_direction": "LONG",
        "scores": {"long": 8, "short": 2, "confidence": 60},
    }])
    _write_jsonl(tmp_path / "v3" / "ledgers" / "execution.jsonl", [])
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    report = analyzer.build_missed_opportunity_proof_report(session={})
    assert report["current_collection_blockers"] == []
    assert report["proof_count"] == 1
    row = report["proofs"][0]
    assert row["coverage"]["rejection_codes"] == [], row["coverage"]["rejection_codes"]
    assert row["coverage"]["status"] == "COMPLETE", row["coverage"]
    assert row["classification"] == "PROVEN_MISSED_PROFIT", row
    assert row["coverage"]["identity_join_blockers"] == []
    assert row["selected_simulation_side"] == "LONG"
    assert row["raw_ai_decision"] == "APPROVE"
    assert row["cost_assumption"]["explicit_costs_complete"] is True


def test_duplicate_current_decisions_block_exact_join(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    _manifest(tmp_path)
    _write_jsonl(
        tmp_path / "chase_offset_touch_grid.jsonl",
        _schedule(
            epoch="epoch-current", episode="child", opportunity="opportunity:child",
            side="LONG", policy="shadow-policy",
        ),
    )
    _write_jsonl(tmp_path / "v3" / "ledgers" / "opportunity.jsonl", [{
        "record_id": "opportunity:child", "opportunity_id": "opportunity:child",
        "episode_id": "child", "shared_ai_call_id": "shared-call",
        "epoch_id": "epoch-current", "raw_direction": "LONG",
    }])
    _write_jsonl(tmp_path / "v3" / "ledgers" / "decision.jsonl", [
        {
            "record_id": f"decision:{policy}", "episode_id": "child",
            "shared_ai_call_id": "shared-call", "epoch_id": "epoch-current",
            "policy_signature": policy, "raw_ai_decision": "APPROVE",
        }
        for policy in ("policy-a", "policy-b")
    ])
    _write_jsonl(tmp_path / "v3" / "ledgers" / "execution.jsonl", [])
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    row = analyzer.build_missed_opportunity_proof_report(session={})["proofs"][0]
    assert row["classification"] == "INSUFFICIENT_EVIDENCE"
    assert "CURRENT_DECISION:EXACT_JOIN_AMBIGUOUS" in row["coverage"]["identity_join_blockers"]


def test_missing_schedule_identity_cannot_match_arbitrary_current_row(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    _manifest(tmp_path)
    schedules = _schedule(
        epoch="epoch-current", episode="child", opportunity="opportunity:child",
        side="LONG", policy="shadow-policy",
    )
    for row in schedules:
        row["opportunity_id"] = ""
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", schedules)
    _write_jsonl(tmp_path / "v3" / "ledgers" / "opportunity.jsonl", [{
        "record_id": "opportunity:unrelated", "opportunity_id": "opportunity:unrelated",
        "episode_id": "other", "shared_ai_call_id": "shared-call",
        "epoch_id": "epoch-current", "raw_direction": "LONG",
    }])
    _write_jsonl(tmp_path / "v3" / "ledgers" / "decision.jsonl", [{
        "record_id": "decision:child", "episode_id": "child",
        "shared_ai_call_id": "shared-call", "epoch_id": "epoch-current",
        "raw_ai_decision": "APPROVE",
    }])
    _write_jsonl(tmp_path / "v3" / "ledgers" / "execution.jsonl", [])
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    row = analyzer.build_missed_opportunity_proof_report(session={})["proofs"][0]
    assert row["classification"] == "INSUFFICIENT_EVIDENCE"
    assert row["opportunity_id"] is None
    assert "CURRENT_OPPORTUNITY:JOIN_IDENTITY_INCOMPLETE" in (
        row["coverage"]["identity_join_blockers"]
    )


def test_conflicting_schedule_epoch_aliases_are_excluded_from_report(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    _manifest(tmp_path)
    schedules = _schedule(
        epoch="epoch-current", episode="child", opportunity="opportunity:child",
        side="LONG", policy="shadow-policy",
    )
    for row in schedules:
        row["dataset_epoch"] = "epoch-old"
    _write_jsonl(tmp_path / "chase_offset_touch_grid.jsonl", schedules)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    report = analyzer.build_missed_opportunity_proof_report(session={})
    assert report["proof_count"] == 0
    assert report["classification_counts"]["PROVEN_MISSED_PROFIT"] == 0
    assert "COMPRESSED_SHADOW:EPOCH_DECLARATION_CONFLICT" in (
        report["current_collection_blockers"]
    )


def test_v3_reader_filters_before_reused_record_id_dedup(tmp_path, monkeypatch):
    analyzer = _load_analyzer()
    _manifest(tmp_path)
    _write_jsonl(tmp_path / "v3" / "ledgers" / "decision.jsonl", [
        {"record_id": "decision:reused", "episode_id": "old", "epoch_id": "epoch-old"},
        {"record_id": "decision:reused", "episode_id": "current", "epoch_id": "epoch-current"},
    ])
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    contract, blockers = load_current_collection_contract(tmp_path)
    assert blockers == []
    sink = []
    rows = analyzer._v3_ledger_rows("decision", current_contract=contract, blocker_sink=sink)
    assert [row["episode_id"] for row in rows] == ["current"]
    assert sink == ["V3_DECISION:EPOCH_MISMATCH"]

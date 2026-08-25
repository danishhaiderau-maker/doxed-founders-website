import json
import os
import threading
from pathlib import Path

from collector_v22_schema import RESEARCH_EVENTS_FILE
from research import research_dashboard as dashboard
from research.policy_cycle_snapshot import build_policy_cycle_reports
from research import policy_cycle_snapshot


SIGNAL_TS = 1_800_000_000.0


def _event(index, outcome="ACCEPTED_UNFILLED"):
    path = [
        [(SIGNAL_TS + index * 10_000 + minute * 60) * 1000, 100, 101, 99, 100, 1]
        for minute in range(60)
    ]
    signal_ts = SIGNAL_TS + index * 10_000
    return {
        "event_id": f"event-{index}",
        "event_episode_id": f"episode-{index}",
        "epoch_id": "epoch-cycle",
        "policy_epoch_id": "policy-epoch-cycle",
        "policy_signature": "policy-signature-cycle",
        "collector_version": "collector_v2.2",
        "primary_outcome": outcome,
        "observation_status": "PATH_COMPLETE",
        "envelope": {
            "event_id": f"event-{index}",
            "event_episode_id": f"episode-{index}",
            "epoch_id": "epoch-cycle",
            "policy_epoch_id": "policy-epoch-cycle",
            "policy_signature": "policy-signature-cycle",
            "signal_ts": signal_ts,
            "primary_outcome": outcome,
            "direction": "LONG",
        },
        "canonical_tape": {"path_1m": path},
        "entry_children": [],
    }


def _append(path: Path, row):
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def test_policy_builders_share_pinned_snapshot_while_mirror_grows(tmp_path, monkeypatch):
    event_path = tmp_path / RESEARCH_EVENTS_FILE
    for index, outcome in enumerate(("ACCEPTED_FILLED", "ACCEPTED_UNFILLED", "REJECTED")):
        _append(event_path, _event(index, outcome))

    reports = build_policy_cycle_reports(
        tmp_path,
        tmp_path,
        between_builders_hook=lambda: _append(event_path, _event(3)),
    )
    candidate = reports["candidate"]
    best = reports["best"]

    assert sum(1 for _ in event_path.open(encoding="utf-8")) == 4
    assert reports["cycle_snapshot"]["row_count"] == 3
    assert candidate["cycle_snapshot"] == best["cycle_snapshot"] == reports["cycle_snapshot"]
    assert candidate["evidence"]["current_events"] == best["evidence"]["current_epoch_events"] == 3
    assert candidate["evidence"]["eligible_events"] == best["evidence"]["replay_eligible_events"] == 3
    assert candidate["evidence"]["independent_episodes"] == best["evidence"]["independent_episode_count"] == 3
    assert "POLICY_CYCLE_SNAPSHOT_MISMATCH" not in best["blockers"]

    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [tmp_path / name])
    api_payload = dashboard._best_policy_research_payload()
    assert api_payload["cycle_snapshot"]["snapshot_id"] == reports["cycle_snapshot"]["snapshot_id"]
    assert api_payload["evidence"]["current_epoch_events"] == 3
    assert api_payload["live_observed_evidence"]["current_epoch_events"] == 4


def test_analyzer_uses_single_policy_cycle_orchestrator():
    source = Path(__file__).with_name("analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    start = source.index("def write_report_manifest")
    manifest_body = source[start:start + 12_000]
    assert "build_policy_cycle_reports" in manifest_body
    assert "build_policy_candidate_oos_report(" not in manifest_body
    assert "build_best_policy_research_report(" not in manifest_body
    assert "build_safe_policy_genome_v3_report(" not in manifest_body


def test_policy_snapshot_releases_live_mirror_before_expensive_json_parsing(tmp_path, monkeypatch):
    event_path = tmp_path / RESEARCH_EVENTS_FILE
    _append(event_path, _event(1))
    parsing_started = threading.Event()
    allow_parsing = threading.Event()
    real_loads = json.loads

    def slow_loads(*args, **kwargs):
        parsing_started.set()
        assert allow_parsing.wait(5)
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(policy_cycle_snapshot.json, "loads", slow_loads)
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault(
            "snapshot", policy_cycle_snapshot.load_policy_cycle_snapshot(tmp_path)
        )
    )
    worker.start()
    assert parsing_started.wait(5)
    replacement = tmp_path / "replacement.download"
    replacement.write_text(json.dumps(_event(2)) + "\n", encoding="utf-8")
    try:
        # This fails on Windows when the reader retains a non-delete-sharing
        # handle during JSON parsing. The bytes-then-parse boundary must allow
        # the synchronizer's atomic destination replacement here.
        os.replace(replacement, event_path)
    finally:
        allow_parsing.set()
        worker.join(5)
    assert not worker.is_alive()
    assert result["snapshot"]["receipt"]["source_read_mode"] == "BYTES_THEN_PARSE_V1"
    assert result["snapshot"]["receipt"]["row_count"] == 1


def test_dashboard_releases_live_mirror_before_expensive_json_parsing(tmp_path, monkeypatch):
    event_path = tmp_path / RESEARCH_EVENTS_FILE
    _append(event_path, _event(1))
    parsing_started = threading.Event()
    allow_parsing = threading.Event()
    real_loads = json.loads

    def slow_loads(*args, **kwargs):
        parsing_started.set()
        assert allow_parsing.wait(5)
        return real_loads(*args, **kwargs)

    monkeypatch.setattr(dashboard, "_data_file_candidates", lambda name: [event_path])
    monkeypatch.setattr(dashboard.json, "loads", slow_loads)
    result = {}
    worker = threading.Thread(
        target=lambda: result.setdefault("events", dashboard._read_research_events_v22())
    )
    worker.start()
    assert parsing_started.wait(5)
    replacement = tmp_path / "dashboard-replacement.download"
    replacement.write_text(json.dumps(_event(2)) + "\n", encoding="utf-8")
    try:
        os.replace(replacement, event_path)
    finally:
        allow_parsing.set()
        worker.join(5)
    assert not worker.is_alive()
    assert [row["event_id"] for row in result["events"]] == ["event-1"]


def test_policy_reports_prefer_v31_ledgers_over_empty_retired_v22_file(tmp_path, monkeypatch):
    ledgers = tmp_path / "v3" / "ledgers"
    ledgers.mkdir(parents=True)
    opportunity = {
        "schema": "research_evidence_v3",
        "collector_version": "collector_v3.1",
        "ledger": "opportunity",
        "record_id": "opportunity:episode-v31",
        "episode_id": "episode-v31",
        "epoch_id": "epoch-v31-clean",
        "signal_ts": SIGNAL_TS,
        "feature_snapshot_at_signal": {"market_context": {"regime_label": "BULL"}},
    }
    decision = {
        "schema": "research_evidence_v3",
        "ledger": "decision",
        "record_id": "decision:episode-v31:policy-v31",
        "episode_id": "episode-v31",
        "epoch_id": "epoch-v31-clean",
        "policy_epoch_id": "policy-epoch-v31",
        "policy_signature": "policy-signature-v31",
        "policy_id": "OFFSET_V31",
        "execution_disposition": "ORDER_ELIGIBLE",
        "outcome_state": "CENSORED",
    }
    intent = {
        "schema": "research_evidence_v3",
        "ledger": "order_intent",
        "record_id": "order-intent:event-v31",
        "event_id": "event-v31",
        "episode_id": "episode-v31",
        "epoch_id": "epoch-v31-clean",
        "policy_epoch_id": "policy-epoch-v31",
        "policy_signature": "policy-signature-v31",
        "policy_id": "OFFSET_V31",
        "intent_kind": "ACTUAL_PAPER_LIMIT_SUBMIT",
        "executed_direction": "LONG",
        "execution_basis": {
            "requested_qty": 1,
            "requested_qty_provenance": "SOURCE_TICKET_QTY",
            "exchange_qty_claim": True,
            "market_microstructure_symbol": "tBTCF0:USTF0",
        },
        "chase_schedule": {
            "authoritative": True,
            "intervals": [{"bucket_id": "step-0", "start_ts": 100, "end_ts": 103, "limit_price": 100}],
        },
    }
    lifecycle = {
        "schema": "research_evidence_v3",
        "ledger": "lifecycle",
        "record_id": "lifecycle:event-v31:terminal",
        "event_id": "event-v31",
        "episode_id": "episode-v31",
        "epoch_id": "epoch-v31-clean",
        "policy_epoch_id": "policy-epoch-v31",
        "policy_signature": "policy-signature-v31",
        "policy_id": "OFFSET_V31",
        "terminal": True,
        "outcome_state": "NO_TRADE",
    }
    for name, rows in {
        "opportunity": [opportunity], "decision": [decision],
        "order_intent": [intent], "lifecycle": [lifecycle],
    }.items():
        (ledgers / f"{name}.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
    # The retired file may exist but is no longer authoritative once V3.1 is present.
    (tmp_path / RESEARCH_EVENTS_FILE).write_text("", encoding="utf-8")
    (tmp_path / "market_microstructure_1s.jsonl").write_text("".join(
        json.dumps({
            "schema": "market_microstructure_1s_v1", "symbol": "tBTCF0:USTF0",
            "bucket_ts": ts, "fresh": True, "valid_bbo": True,
            "ask": 100 if ts == 102 else 101, "bid": 99,
            "ask_qty": 2, "bid_qty": 2, "sell_qty": 0, "buy_qty": 0,
            "sell_vwap": None, "buy_vwap": None, "trade_count": 0,
        }) + "\n" for ts in range(100, 103)
    ), encoding="utf-8")

    from research import v3_policy_report_adapter
    actual_build = v3_policy_report_adapter.load_or_build_genome
    build_calls = 0

    def counted_build(*args, **kwargs):
        nonlocal build_calls
        build_calls += 1
        return actual_build(*args, **kwargs)

    monkeypatch.setattr(v3_policy_report_adapter, "load_or_build_genome", counted_build)
    reports = build_policy_cycle_reports(tmp_path, tmp_path)
    assert build_calls == 1
    assert reports["cycle_snapshot"]["schema"] == "policy_cycle_snapshot_v3_1"
    assert reports["cycle_snapshot"]["epoch_id"] == "epoch-v31-clean"
    assert reports["cycle_snapshot"]["row_count"] == 1
    assert reports["candidate"]["schema"] == "policy_candidate_oos_v3_1_adapter_v1"
    assert reports["candidate"]["evidence"]["current_events"] == 1
    assert reports["best"]["schema"] == "best_policy_research_v3_1_adapter_v1"
    assert reports["best"]["epoch_id"] == "epoch-v31-clean"
    assert reports["best"]["evidence"]["current_epoch_events"] == 1
    assert "NO_CURRENT_V22_EPOCH" not in reports["best"]["blockers"]
    assert reports["best"]["status"] == "NO QUALIFIED POLICY"
    assert reports["conservative_fill"]["counts"]["events"] == 1
    assert reports["conservative_fill"]["counts"]["fill"] == 1

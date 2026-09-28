import json
from pathlib import Path

from collector_v22_schema import RESEARCH_EVENTS_FILE
from microstructure_tape import FILE_NAME, build_bucket
from research import research_dashboard as dashboard
from research.policy_cycle_snapshot import (
    CONSERVATIVE_FILL_REPORT_FILE,
    build_policy_cycle_reports,
)
from test_policy_cycle_snapshot import _append, _event
from research.quantity_execution import build_signed_quantity_constraints


def qualified_event(index=1):
    item = _event(index)
    start = int(item["envelope"]["signal_ts"])
    item.update({
        "schema": "research_event_v2.2",
        "direction": "LONG",
        "symbol": "BTC",
        "research_execution_basis": {
            "requested_qty": 1.0,
            "requested_qty_provenance": "SOURCE_TICKET_QTY",
            "exchange_qty_claim": True,
            "signed_quantity_constraints": build_signed_quantity_constraints(
                symbol="BTC", quantity_step="0.00000001", quantity_precision=8,
                min_lot="0.00000001", min_notional="0.000001",
                captured_at="2026-08-30T00:00:00Z", source_revision="test-revision",
                source="TEST_FIXTURE",
            ),
        },
        "research_chase_schedule": {
            "authoritative": True,
            "intervals": [{
                "bucket_id": "chase_3", "start_ts": start, "end_ts": start + 3,
                "limit_price": 100.0, "generation": 3,
            }],
        },
    })
    return item


def bucket(ts, *, ask=101, sell_qty=0, sell_vwap=None):
    trades = []
    if sell_qty:
        trades = [{"received_ts": ts + .5, "p": sell_vwap, "v": sell_qty, "S": "SELL"}]
    return build_bucket(
        bucket_ts=ts, bid=99, ask=ask, bid_qty=2, ask_qty=2, last=100,
        source_ts=ts + .5, trades=trades, symbol="BTC",
    )


def write_tape(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_zero_event_cycle_emits_descriptive_report(tmp_path):
    reports = build_policy_cycle_reports(tmp_path, tmp_path)
    cohort = reports["conservative_fill"]
    assert cohort["counts"]["events"] == 0
    assert cohort["qualification_effect"] == "NONE"
    assert cohort["qualification_promotion_allowed"] is False
    assert (tmp_path / CONSERVATIVE_FILL_REPORT_FILE).is_file()


def test_unsupported_and_complete_receipts_are_pinned_to_cycle(tmp_path):
    unsupported = _event(1)
    unsupported["schema"] = "research_event_v2.2"
    complete = qualified_event(2)
    _append(tmp_path / RESEARCH_EVENTS_FILE, unsupported)
    _append(tmp_path / RESEARCH_EVENTS_FILE, complete)
    start = complete["research_chase_schedule"]["intervals"][0]["start_ts"]
    write_tape(tmp_path / FILE_NAME, [
        bucket(start), bucket(start + 1), bucket(start + 2, ask=100, sell_qty=1, sell_vwap=100),
    ])
    cohort = build_policy_cycle_reports(tmp_path, tmp_path)["conservative_fill"]
    assert cohort["counts"] == {"events": 2, "fill": 1, "partial_fill": 0, "no_fill": 0, "unsupported": 1}
    assert cohort["cycle_snapshot"]["row_count"] == 2
    assert cohort["microstructure_snapshot"]["row_count"] == 3
    assert cohort["epoch_id"] == "epoch-cycle"
    assert cohort["policy_signature"] == "policy-signature-cycle"
    assert cohort["qualification_effect"] == "NONE"


def test_event_and_tape_growth_after_snapshot_cannot_enter_receipts(tmp_path):
    first = qualified_event(1)
    _append(tmp_path / RESEARCH_EVENTS_FILE, first)
    start = first["research_chase_schedule"]["intervals"][0]["start_ts"]
    write_tape(tmp_path / FILE_NAME, [bucket(start), bucket(start + 1), bucket(start + 2)])

    def grow_mirrors():
        _append(tmp_path / RESEARCH_EVENTS_FILE, qualified_event(2))
        _append(tmp_path / FILE_NAME, bucket(start + 3, ask=100, sell_qty=1, sell_vwap=100))

    reports = build_policy_cycle_reports(tmp_path, tmp_path, between_builders_hook=grow_mirrors)
    cohort = reports["conservative_fill"]
    assert sum(1 for _ in (tmp_path / RESEARCH_EVENTS_FILE).open(encoding="utf-8")) == 2
    assert sum(1 for _ in (tmp_path / FILE_NAME).open(encoding="utf-8")) == 4
    assert cohort["cycle_snapshot"]["row_count"] == 1
    assert cohort["microstructure_snapshot"]["row_count"] == 3
    assert cohort["counts"]["events"] == 1
    assert cohort["counts"]["fill"] == 0


def test_dashboard_endpoint_is_read_only_descriptive(monkeypatch):
    payload = {
        "schema": "conservative_fill_descriptive_cohort_v1",
        "qualification": "DESCRIPTIVE_ONLY",
        "qualification_effect": "NONE",
        "qualification_promotion_allowed": False,
        "counts": {"events": 1, "fill": 1},
        "receipts": [{"outcome": "FILL"}],
    }
    monkeypatch.setattr(dashboard, "_read_json", lambda name, default=None: payload)
    response = dashboard.app.test_client().get("/api/conservative-fill-research")
    assert response.status_code == 200
    assert response.get_json() == payload


def test_analyzer_catalog_declares_descriptive_report():
    source = Path(__file__).with_name("analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    assert 'CONSERVATIVE_FILL_DESCRIPTIVE_REPORT_FILE = "conservative_fill_descriptive_report.json"' in source
    assert '("Conservative Fill Receipts", CONSERVATIVE_FILL_DESCRIPTIVE_REPORT_FILE' in source

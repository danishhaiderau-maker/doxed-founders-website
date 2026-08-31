from copy import deepcopy

from research.quantity_execution import build_signed_quantity_constraints
from research.entry_baseline_replay import materialize_same_opportunity_replay
from research_entry_baselines import ENTRY_BASELINE_REGISTRY, classify_baseline_evidence


CONSTRAINTS = build_signed_quantity_constraints(
    symbol="BTC", quantity_step="0.1", quantity_precision=1,
    min_lot="0.1", min_notional="1", captured_at="2026-09-01T00:00:00Z",
    source_revision="fixture", source="TEST_FIXTURE",
)


def _row(ts, *, bid=99, ask=101, bid_qty=2, ask_qty=2):
    return {
        "schema": "market_microstructure_1s_v1", "symbol": "BTC", "bucket_ts": ts,
        "fresh": True, "valid_bbo": True, "bid": bid, "ask": ask,
        "bid_qty": bid_qty, "ask_qty": ask_qty, "buy_qty": 0, "sell_qty": 0,
        "buy_vwap": None, "sell_vwap": None, "trade_count": 0,
    }


def _episode(*, ask_qty=2):
    baselines = ENTRY_BASELINE_REGISTRY["baselines"]
    schedules = {}
    row_timestamps = set()
    for index, baseline in enumerate(baselines):
        if baseline["baseline_id"] == "MARKET_ENTRY_AT_SIGNAL":
            start = 100
        elif baseline["baseline_id"] == "FINAL_MARKET_AFTER_EXPIRY":
            start = 1900
        elif baseline.get("entry_type") == "LIMIT_CHASE_WINDOW":
            start = 100 + baseline["window_start_sec"]
        else:
            start = 102 + index * 2
        row_timestamps.add(start)
        schedules[baseline["baseline_id"]] = {
            "episode_id": "ep-1", "opportunity_id": "opp-1",
            "policy_signature": baseline["policy_signature"],
            "schedule": [{
                "bucket_id": f"{baseline['baseline_id']}:0", "start_ts": start,
                "end_ts": start + 1, "limit_price": 101, "generation": 0,
            }],
        }
    return {
        "episode_id": "ep-1", "opportunity_id": "opp-1", "direction": "LONG",
        "signal_ts": 100, "expiry_ts": 1900, "symbol": "BTC", "requested_qty": 1,
        "requested_remaining_qty": 1, "signed_quantity_constraints": CONSTRAINTS,
        "latency_sec": 0, "fees_usd": 0, "slippage_model": "DECLARED_LIMIT",
        "authoritative_parent_expiry": True, "dataset_epoch": "epoch-1",
        "source_revision": "rev-1", "tile_config_signature": "tiles-1",
        "baseline_schedules": schedules,
        "market_microstructure_rows": [
            _row(ts, ask=101, ask_qty=ask_qty) for ts in sorted(row_timestamps)
        ],
    }


def test_same_opportunities_feed_every_baseline_and_zero_costs_are_valid():
    report = materialize_same_opportunity_replay([_episode()])
    assert report["same_opportunity_count"] == 1
    assert len(report["baseline_ids"]) == 11
    receipt = report["episode_receipts"][0]
    assert {row["opportunity_id"] for row in receipt["results"]} == {"opp-1"}
    assert {row["episode_id"] for row in receipt["results"]} == {"ep-1"}
    assert {row["outcome_state"] for row in receipt["results"]} == {"FULL_FILL"}
    assert all(summary["full_fills"] == 1 for summary in report["summaries"].values())
    evidence = {
        name: (0 if name in {"fees", "latency"} else True)
        for name in ENTRY_BASELINE_REGISTRY["baselines"][0]["required_evidence"]
    }
    evidence["terminal_outcome"] = "FULL_FILL"
    assert classify_baseline_evidence("MARKET_ENTRY_AT_SIGNAL", evidence)["supported"] is True


def test_positive_accepted_partial_is_visible_for_each_baseline():
    report = materialize_same_opportunity_replay([_episode(ask_qty=.4)])
    assert all(summary["partial_fills"] == 1 for summary in report["summaries"].values())
    for result in report["episode_receipts"][0]["results"]:
        assert result["outcome_state"] == "PARTIAL_FILL"
        assert result["conservative_receipt"]["filled_qty"] == .4


def test_missing_tape_is_unknown_for_every_baseline_never_no_fill():
    episode = _episode()
    episode["market_microstructure_rows"] = []
    report = materialize_same_opportunity_replay([episode])
    assert all(summary["unknown"] == 1 for summary in report["summaries"].values())
    assert all(summary["no_fills"] == 0 for summary in report["summaries"].values())


def test_complete_uncrossed_bbo_is_proven_no_fill_not_unknown():
    episode = _episode()
    episode["market_microstructure_rows"] = [
        _row(row["bucket_ts"], ask=101, ask_qty=0)
        for row in episode["market_microstructure_rows"]
    ]
    report = materialize_same_opportunity_replay([episode])
    assert all(summary["no_fills"] == 1 for summary in report["summaries"].values())
    assert all(summary["unknown"] == 0 for summary in report["summaries"].values())


def test_missing_measured_fee_is_unknown_never_assumed_zero():
    episode = _episode()
    episode["fees_usd"] = None
    report = materialize_same_opportunity_replay([episode])
    assert all(summary["unknown"] == 1 for summary in report["summaries"].values())
    for result in report["episode_receipts"][0]["results"]:
        assert "MISSING_FEES" in result["rejection_codes"]


def test_mismatched_schedule_identity_is_unknown_only_for_that_baseline():
    episode = _episode()
    episode["baseline_schedules"]["CHASE_13_MIN_COMPRESSED"]["opportunity_id"] = "other"
    report = materialize_same_opportunity_replay([episode])
    rows = {row["baseline_id"]: row for row in report["episode_receipts"][0]["results"]}
    assert rows["CHASE_13_MIN_COMPRESSED"]["outcome_state"] == "UNKNOWN"
    assert "BASELINE_SCHEDULE_OPPORTUNITY_ID_MISMATCH" in rows["CHASE_13_MIN_COMPRESSED"]["rejection_codes"]
    assert all(row["outcome_state"] == "FULL_FILL" for key, row in rows.items() if key != "CHASE_13_MIN_COMPRESSED")


def test_receipts_and_report_ids_are_deterministic_and_input_not_mutated():
    episode = _episode()
    before = deepcopy(episode)
    first = materialize_same_opportunity_replay([episode])
    second = materialize_same_opportunity_replay([episode])
    assert first == second
    assert episode == before


def test_window_schedule_outside_declared_bucket_is_unknown():
    episode = _episode()
    envelope = episode["baseline_schedules"]["CHASE_WINDOW_3"]
    envelope["schedule"][0]["start_ts"] = episode["signal_ts"]
    envelope["schedule"][0]["end_ts"] = episode["signal_ts"] + 1
    receipt = materialize_same_opportunity_replay([episode])["episode_receipts"][0]
    result = next(row for row in receipt["results"] if row["baseline_id"] == "CHASE_WINDOW_3")
    assert result["outcome_state"] == "UNKNOWN"
    assert result["rejection_codes"] == ["CHASE_WINDOW_SCHEDULE_OUTSIDE_DECLARED_BUCKET"]

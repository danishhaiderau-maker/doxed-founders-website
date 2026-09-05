from copy import deepcopy
import json

import pytest

from research_entry_baselines import materialize_signal_time_baseline_schedules
from research.entry_baseline_replay import materialize_same_opportunity_replay, materialize_v3_opportunity_replay
from test_entry_baseline_replay import _episode


def fresh(direction="LONG"):
    row = _episode()
    row.pop("baseline_schedules")
    row.update(epoch_id="epoch-1", deployed_revision="rev-1", signal_price=100,
               signal_time_bbo={"bid": 99, "ask": 101, "bid_qty": 2, "ask_qty": 2},
               direction=direction, raw_direction=direction)
    row["baseline_schedule_snapshot"] = materialize_signal_time_baseline_schedules(row)
    return row


def entry(receipt):
    return next(item for item in receipt["results"] if item["baseline_id"] == "MARKET_ENTRY_AT_SIGNAL")


@pytest.mark.parametrize("direction", ["LONG", "SHORT", "NO_TRADE", "UNKNOWN", None])
def test_producer_captures_both_sides_independently_without_ai_direction_change(direction):
    row = fresh(direction)
    snapshot = row["baseline_schedule_snapshot"]
    assert row["direction"] == direction
    sides = snapshot["directional_schedules"]
    assert set(sides) == {"LONG", "SHORT"}
    assert sides["LONG"]["episode_id"] != sides["SHORT"]["episode_id"]
    long = sides["LONG"]["schedules"]["MARKET_ENTRY_AT_SIGNAL"]["schedule"][0]
    short = sides["SHORT"]["schedules"]["MARKET_ENTRY_AT_SIGNAL"]["schedule"][0]
    assert long["limit_price"] == 101 and short["limit_price"] == 99
    assert sides["LONG"]["capture_signature"] != sides["SHORT"]["capture_signature"]
    assert sides["LONG"]["source_episode_id"] == sides["SHORT"]["source_episode_id"] == "ep-1"


def test_opposite_side_never_reuses_supplied_actual_schedule():
    row = _episode()
    original = deepcopy(row["baseline_schedules"])
    row.update(signal_time_bbo={"bid": 99, "ask": 101, "bid_qty": 2, "ask_qty": 2}, signal_price=100)
    snapshot = materialize_signal_time_baseline_schedules(row)
    for baseline_id, envelope in original.items():
        assert {key: snapshot["schedules"][baseline_id][key] for key in envelope} == envelope
        assert {key: snapshot["directional_schedules"]["LONG"]["schedules"][baseline_id][key]
                for key in envelope} == envelope
    opposite = snapshot["directional_schedules"]["SHORT"]
    assert opposite["schedules"]["MARKET_ENTRY_AT_SIGNAL"]["schedule"][0]["limit_price"] == 99
    assert row["baseline_schedules"] == original


def test_replay_both_fills_use_separate_side_depth_and_do_not_double_independent_n():
    row = fresh()
    row["market_microstructure_rows"][0]["bid_qty"] = 0.4
    before = deepcopy(row)
    report = materialize_same_opportunity_replay([row])
    assert report["same_opportunity_count"] == 1 and report["directional_episode_count"] == 2
    by_side = {item["direction"]: item for item in report["episode_receipts"]}
    assert entry(by_side["LONG"])["outcome_state"] == "FULL_FILL"
    assert entry(by_side["SHORT"])["outcome_state"] == "PARTIAL_FILL"
    assert entry(by_side["LONG"])["conservative_receipt"]["direction"] == "LONG"
    assert entry(by_side["SHORT"])["conservative_receipt"]["direction"] == "SHORT"
    assert entry(by_side["SHORT"])["conservative_receipt"] != entry(by_side["LONG"])["conservative_receipt"]
    assert by_side["SHORT"]["source_episode_id"] == "ep-1"
    assert by_side["SHORT"]["original_ai_direction"] == "LONG"
    assert row == before


@pytest.mark.parametrize("defect", ["swap", "direction", "signature", "missing"])
def test_cross_side_or_changed_capture_fails_closed(defect):
    row = fresh()
    sides = row["baseline_schedule_snapshot"]["directional_schedules"]
    if defect == "swap":
        sides["SHORT"] = deepcopy(sides["LONG"])
    elif defect == "direction":
        sides["SHORT"]["direction"] = "LONG"
    elif defect == "signature":
        sides["SHORT"]["capture_signature"] = "0" * 64
    else:
        sides.pop("SHORT")
    report = materialize_same_opportunity_replay([row])
    result = next(item for item in report["episode_receipts"] if item["direction"] == "SHORT")
    assert all(item["outcome_state"] == "UNKNOWN" for item in result["results"])


def test_legacy_one_sided_capture_is_not_invented():
    report = materialize_same_opportunity_replay([_episode()])
    assert report["directional_episode_count"] == 1
    assert report["directional_coverage"] == {"LEGACY_SINGLE_SIDE_ONLY": 1}


def test_opposite_fill_cannot_reuse_original_execution_context():
    report = materialize_same_opportunity_replay([fresh()], generation={"source_revision": "rev-1"})
    opposite = next(item for item in report["episode_receipts"] if item["direction"] == "SHORT")
    value = entry(opposite)
    assert value["supported"] is True
    assert value["model_context_status"] == "UNKNOWN"
    assert value["model_context_blockers"] == ["DIRECTION_SPECIFIC_BASELINE_EXECUTION_CONTEXT_REQUIRED"]
    assert "execution_model_context" not in value


def test_separate_ledger_normal_materializer_exposes_both_missing_outcomes(tmp_path):
    row = fresh("NO_TRADE")
    row.pop("market_microstructure_rows")
    row["record_id"] = row["opportunity_id"]
    root = tmp_path / "v3" / "ledgers"
    root.mkdir(parents=True)
    (root / "opportunity.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    report = materialize_v3_opportunity_replay(tmp_path)
    assert report["same_opportunity_count"] == 1
    assert {item["direction"] for item in report["episode_receipts"]} == {"LONG", "SHORT"}
    assert all(item["original_ai_direction"] == "NO_TRADE" for item in report["episode_receipts"])
    assert all(summary["unknown"] == 2 for summary in report["summaries"].values())


def test_raw_ai_and_executed_side_remain_distinct_without_opposite_limit_anchor(tmp_path):
    row = fresh("SHORT")
    row.update(raw_direction="LONG", executed_direction="SHORT", orig_limit_price=987654,
               baseline_signal_inputs={"signal_price": 765432, "orig_limit_price": 987654})
    row["causal_identity"] = {"direction": "LONG"}
    row["baseline_schedule_snapshot"] = materialize_signal_time_baseline_schedules(row)
    by_side = row["baseline_schedule_snapshot"]["directional_schedules"]
    assert by_side["LONG"]["original_ai_direction"] == "LONG"
    assert by_side["LONG"]["source_execution_direction"] == "SHORT"
    assert by_side["SHORT"]["episode_id"] == row["episode_id"]
    assert by_side["LONG"]["episode_id"] != row["episode_id"]
    limits = {side: capture["schedules"]["NO_CHASE_LIMIT"]["schedule"][0]["limit_price"]
              for side, capture in by_side.items()}
    assert limits == {"LONG": 99.9, "SHORT": 100.1}
    report = materialize_same_opportunity_replay([row])
    assert all(item["original_ai_direction"] == "LONG" and item["source_execution_direction"] == "SHORT"
               for item in report["episode_receipts"])
    row.pop("market_microstructure_rows")
    row["record_id"] = row["opportunity_id"]
    root = tmp_path / "v3" / "ledgers"
    root.mkdir(parents=True)
    (root / "opportunity.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    normal = materialize_v3_opportunity_replay(tmp_path)
    assert all("CONFLICTING_CAUSAL_IDENTITY:direction" not in item["materialization_reason_codes"]
               and item["directional_coverage"] == "BOTH_SIDES_CAPTURED" for item in normal["episode_receipts"])


def test_missing_neutral_reference_uses_same_captured_midpoint_for_both_limit_offsets():
    row = fresh()
    row.pop("signal_price")
    row.update(orig_limit_price=987654, baseline_signal_inputs={"signal_price": 765432})
    snapshot = materialize_signal_time_baseline_schedules(row)
    limits = {side: capture["schedules"]["NO_CHASE_LIMIT"]["schedule"][0]["limit_price"]
              for side, capture in snapshot["directional_schedules"].items()}
    assert limits == {"LONG": 99.9, "SHORT": 100.1}


def test_missing_raw_ai_direction_is_not_inferred_from_executed_side():
    row = fresh()
    row.pop("raw_direction")
    snapshot = materialize_signal_time_baseline_schedules(row)
    assert snapshot["original_ai_direction"] == "UNKNOWN"
    assert snapshot["source_execution_direction"] == "LONG"

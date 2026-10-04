"""PR-F research completeness: lifecycle fields, shadow gate, stop axis,
frozen trial, AI usefulness, WAL alarm explanation/clear, analyzer compat."""
import inspect
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import frozen_policy_trial as trial_module
from combo_pathway_config import ACTIVE_TILE_REGISTRY, COMPARISON_BENCHMARK_LANE
from emergency_evidence_wal import (
    ALARM_AUDIT_NAME, ALARM_AUTO_CLEAR_MIN_DWELL_SEC, ALARM_CATALOG,
    EmergencyEvidenceWal, _cross_process_lock, explain_alarm,
)
from frozen_policy_trial import FrozenPolicyTrial, freeze_selection
from research_completeness import (
    COMPLETENESS_SCHEMA, LIFECYCLE_COMPLETENESS_FIELDS, NO_FILL_TTL_OUTCOMES,
    classify_no_fill_ttl_outcome, closed_lifecycle_completeness,
    completeness_projection, exit_depth_context, hard_vs_atr_stop_counterfactual,
    replay_tick_depth, session_label_for_ts, shadow_row_completeness,
    track_path_extreme_timestamps, unfilled_lifecycle_completeness,
)
from research_completeness_report import (
    ai_usefulness_report, build_research_completeness_report,
    frozen_trial_report, lifecycle_completeness_coverage, shadow_leaderboard,
    shadow_rank_gate, stop_axis_summary,
)
from research_v3_bridge import dual_write_paper_close, dual_write_v22_record
from research_v3_store import V3EvidenceStore

IDENTITY = {"epoch_id": "epoch-1", "source_revision": "a" * 40,
            "deployed_revision": "a" * 40, "tile_config_signature": "b" * 64}
LANES = list(ACTIVE_TILE_REGISTRY)


def _utc(hour):
    return datetime(2026, 9, 1, hour, 30, tzinfo=timezone.utc).timestamp()


# -- 1. lifecycle completeness ---------------------------------------------------

@pytest.mark.parametrize("hour,label", [(0, "ASIA"), (7, "ASIA"), (8, "EU"), (15, "EU"), (16, "US"), (23, "US")])
def test_session_label_boundaries(hour, label):
    assert session_label_for_ts(_utc(hour)) == label
    assert session_label_for_ts(_utc(hour) * 1000) == label
    assert session_label_for_ts(datetime.fromtimestamp(_utc(hour), timezone.utc).isoformat()) == label


def test_session_label_unknown_stays_none():
    assert session_label_for_ts(None) is None
    assert session_label_for_ts("garbage") is None
    assert session_label_for_ts(0) is None


def test_extreme_timestamps_track_new_highs_and_lows():
    pos = {"entry_ts": 100.0}
    for ts, value in ((101, 0.5), (102, 2.0), (103, -1.0), (104, 1.0), (105, -0.5)):
        track_path_extreme_timestamps(pos, value, ts)
    pos.update(max_pnl_pct=2.0, max_drawdown=-1.0)
    fields = closed_lifecycle_completeness(pos)
    assert fields["mfe_ts"] == 102 and fields["mfe_ts_basis"] == "OBSERVED_EXIT_WORKER_TICK"
    assert fields["mae_ts"] == 103


def test_entry_floored_extreme_points_at_fill_time():
    pos = {"entry_ts": 100.0, "max_drawdown": 0.0, "max_pnl_pct": 1.0}
    for ts, value in ((101, 0.2), (102, 1.0)):
        track_path_extreme_timestamps(pos, value, ts)
    fields = closed_lifecycle_completeness(pos)
    assert fields["mae_ts"] == 100.0 and fields["mae_ts_basis"] == "ENTRY_FLOOR"


def test_untracked_extremes_are_null_with_reason_not_zero():
    fields = closed_lifecycle_completeness({"entry_ts": _utc(9)})
    assert fields["mfe_ts"] is None and fields["mfe_ts_basis"] == "PATH_EXTREME_NOT_TRACKED"
    assert fields["fill_revalidation_count"] is None
    assert fields["fill_revalidation_count_reason"] == "ORDER_COUNTER_ABSENT"
    assert fields["session_label"] == "EU"


def test_ttl_outcome_classification():
    assert classify_no_fill_ttl_outcome("TTL_EXPIRED", touched=False) == "EXPIRED_NO_TOUCH"
    assert classify_no_fill_ttl_outcome("CHASE_WINDOW_EXHAUSTED", touched=True) == "EXPIRED_TOUCHED_NO_FILL"
    assert classify_no_fill_ttl_outcome("STALE_SIGNAL") == "EXPIRED_TOUCH_UNKNOWN"
    assert classify_no_fill_ttl_outcome("FILL_REVALIDATION_REGIME_FLIP") == "CANCELLED_REVALIDATION"
    assert classify_no_fill_ttl_outcome("OPERATOR_CANCEL") == "CANCELLED_OTHER"
    assert classify_no_fill_ttl_outcome("FILLED") is None
    assert classify_no_fill_ttl_outcome("TTL", filled=True) is None
    assert set(NO_FILL_TTL_OUTCOMES) >= {"EXPIRED_NO_TOUCH", "EXPIRED_TOUCHED_NO_FILL", "CANCELLED_REVALIDATION"}


def test_exit_depth_is_null_with_reason_when_unavailable():
    assert exit_depth_context(None) == (None, "EXIT_DEPTH_SIMULATION_ABSENT")
    assert exit_depth_context({"book_empty": True}) == (None, "EXIT_BOOK_EMPTY_BBO_FALLBACK")
    assert exit_depth_context({"filled_qty": 0.0, "levels_consumed": 0}) == (None, "EXIT_DEPTH_NOT_WALKED")
    depth, reason = exit_depth_context({"filled_qty": 0.01, "levels_consumed": 2, "avg_price": 100.5,
                                        "best_price": 100.4, "fully_filled": True})
    assert reason is None and depth["levels_consumed"] == 2 and depth["visible_executable_qty"] == 0.01


def test_unfilled_completeness_uses_touch_counter_and_revalidation_count():
    fields = unfilled_lifecycle_completeness(
        {"limit_touch_count": 3, "fill_revalidation_count": 2, "created_ts": _utc(17)},
        reason="TTL_EXPIRED",
    )
    assert fields["no_fill_ttl_outcome"] == "EXPIRED_TOUCHED_NO_FILL"
    assert fields["fill_revalidation_count"] == 2
    assert fields["session_label"] == "US"
    assert fields["exit_depth"] is None and fields["exit_depth_unavailable_reason"] == "NO_POSITION_OPENED"
    assert set(LIFECYCLE_COMPLETENESS_FIELDS) <= set(fields)


def test_projection_only_copies_schema_tagged_rows():
    assert completeness_projection({"session_label": "US"}) == {}
    full = closed_lifecycle_completeness({"entry_ts": _utc(1), "fill_revalidation_count": 1})
    assert completeness_projection({**full, "other": 1}) == full


def test_bot_wires_completeness_into_every_lifecycle_writer():
    import bot
    close_src = inspect.getsource(bot.close_position)
    assert "**closed_lifecycle_completeness(pos, exit_sim=exit_sim)" in close_src
    assert "_FROZEN_POLICY_TRIAL.assignment(" in close_src
    for fn in (bot.log_trade_outcome_jsonl, bot.log_trade_lifecycle):
        assert "completeness_projection(" in inspect.getsource(fn)
    assert "track_path_extreme_timestamps(" in inspect.getsource(bot._apply_position_exits)
    assert "shadow_row_completeness(buf)" in inspect.getsource(bot.log_shadow_outcome_jsonl)
    assert "shadow_row_completeness(buf)" in inspect.getsource(bot.finalize_shadow_lane_collecting)


def test_bridge_close_row_carries_completeness_stop_axis_and_trial():
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "market_microstructure_1s.jsonl").write_text("\n".join(json.dumps({
            "bucket_ts": ts, "last": 100.0 - (ts - 1000) * 0.5, "bid": 99.9, "ask": 100.1,
            "bid_qty": 2.0, "ask_qty": 3.0, "fresh": True, "valid_bbo": True,
        }) for ts in range(999, 1006)) + "\n", encoding="utf-8")
        signal = {"trade_id": "p-c", "created_ts_ts": 1000, "raw_direction": "LONG",
                  "shared_ai_call_id": "scan-c", "policy_id": "PATIENT", "research_lane": "PATIENT"}
        position = {"trade_id": "p-c", "entry_ts": 1001, "entry": 100.0, "qty": 0.1, "dir": "LONG",
                    "research_lane": "PATIENT", "leverage": 100, "atr14_3m": 1.0}
        completeness = closed_lifecycle_completeness({**position, "fill_revalidation_count": 1})
        outcome = {"trade_id": "p-c", "close_ts": 1005, "exit": 98.0, "net_pnl_usd": -0.2,
                   "exit_reason": "SL", **completeness,
                   "frozen_trial": {"trial_id": "t", "arm": "CANDIDATE", "paper_only": True}}
        dual_write_paper_close(position, signal, outcome, epoch_id="epoch-v3-test", data_dir=tmp)
        lifecycle = json.loads(V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
                               .ledger_path("lifecycle").read_text().strip())
    assert lifecycle["lifecycle_completeness_schema"] == COMPLETENESS_SCHEMA
    assert lifecycle["fill_revalidation_count"] == 1
    assert lifecycle["exit_depth"] is None
    assert lifecycle["exit_depth_unavailable_reason"] == "EXIT_DEPTH_SIMULATION_ABSENT"
    axis = lifecycle["stop_axis_counterfactual"]
    assert axis["shadow_only"] is True and axis["live_policy_effect"] == "NONE"
    assert axis["hard_stop"]["stop_price"] == 99.7  # 30% margin / 100x = 0.3% below entry
    assert axis["hard_stop"]["status"] == "HIT"
    assert lifecycle["frozen_trial"]["trial_id"] == "t"


def _unfilled_event():
    return {
        "event_id": "cont-nf", "event_episode_id": "episode-nf", "epoch_id": "epoch-v3-test",
        "envelope": {"signal_ts": 1000, "raw_direction": "LONG", "executed_direction": "LONG",
                     "policy_signature": "policy-a", "policy_epoch_id": "pe-a"},
        "event_episode": {"shared_ai_call_id": "scan-nf"},
        "feature_snapshot_at_signal": {"symbol": "tBTCF0:USTF0"},
        "primary_outcome": "ACCEPTED_UNFILLED", "exact_reason": "TTL_EXPIRED",
        "observation_status": "FUNNEL_COMPLETE", "ranking_eligible": True,
        "canonical_tape": {"path_1m": [{"t": 1000, "o": 100, "h": 101, "l": 99, "c": 100}],
                           "canonical_tape_start": 1000, "canonical_tape_end": 1060,
                           "coverage": {"complete": True}},
        "research_execution_basis": {"qty": 0.1},
        "research_chase_schedule": {"authoritative": True, "intervals": []},
        "replay_eligibility": {"eligible": True},
    }


def test_bridge_no_fill_terminal_lifecycle_carries_ttl_outcome():
    with tempfile.TemporaryDirectory() as tmp:
        dual_write_v22_record(_unfilled_event(), data_dir=tmp)
        rows = [json.loads(line) for line in V3EvidenceStore(tmp, epoch_id="epoch-v3-test")
                .ledger_path("lifecycle").read_text().splitlines() if line.strip()]
    terminal = [row for row in rows if row.get("terminal_no_fill")]
    assert terminal, rows
    row = terminal[-1]
    assert row["lifecycle_completeness_schema"] == COMPLETENESS_SCHEMA
    assert row["no_fill_ttl_outcome"] in NO_FILL_TTL_OUTCOMES
    assert row["exit_depth"] is None and row["exit_depth_unavailable_reason"] == "NO_POSITION_OPENED"


# -- 2. shadow identity / costs / depth + rank gate --------------------------------

def _shadow_buf(**over):
    buf = {
        "research_lane": LANES[0], "policy_signature": "sig-1", "collection_epoch_id": "epoch-1",
        "fee_model": {"maker_bps": 2}, "execution_profile": {"executable_marks": "BBO_SIDE"},
        "ticks": [{"depth_bid_qty": 1.0, "depth_ask_qty": 2.0}, {"depth_bid_qty": 3.0, "depth_ask_qty": 4.0}],
    }
    buf.update(over)
    return buf


def test_shadow_row_completeness_complete_and_incomplete():
    ok = shadow_row_completeness(_shadow_buf())
    assert ok["shadow_identity_status"] == "COMPLETE"
    assert ok["cost_assumptions_status"] == "COMPLETE" and len(ok["cost_assumptions"]["cost_assumptions_sha256"]) == 64
    assert ok["shadow_depth_status"] == "TOP_OF_BOOK_QTY" and ok["shadow_depth_context"]["min_top_bid_qty"] == 1.0
    bad = shadow_row_completeness(_shadow_buf(policy_signature=None, fee_model=None, ticks=[]))
    assert "POLICY_SIGNATURE_MISSING" in bad["shadow_identity_missing"]
    assert bad["cost_assumptions"] is None and "FEE_MODEL_MISSING" in bad["cost_assumptions_missing"]
    assert bad["shadow_depth_status"] == "UNAVAILABLE"


def test_replay_tick_depth_rejects_stale_or_missing_book():
    view = {"bid": 99.0, "ask": 101.0, "bid_qty": 1.0, "ask_qty": 2.0, "bbo_ts": 100.0}
    assert replay_tick_depth(view, now=102.0)["depth_ask_qty"] == 2.0
    assert replay_tick_depth(view, now=110.0) is None
    assert replay_tick_depth({**view, "bid_qty": 0}, now=101.0) is None


def test_shadow_rank_gate_excludes_and_counts_reasons():
    good = {**shadow_row_completeness(_shadow_buf()), "filled": True, "net_pnl_usd": 1.0}
    no_depth = {**shadow_row_completeness(_shadow_buf(ticks=[])), "filled": True, "net_pnl_usd": 5.0}
    no_ident = {**shadow_row_completeness(_shadow_buf(collection_epoch_id=None, fee_model=None)),
                "filled": True, "net_pnl_usd": 9.0}
    legacy = {"filled": True, "net_pnl_usd": 50.0}
    gate = shadow_rank_gate([good, no_depth, no_ident, legacy])
    assert gate["eligible_count"] == 1 and gate["excluded_count"] == 3
    assert gate["exclusion_reasons"] == {"COSTS_MISSING": 1, "DEPTH_MISSING": 1, "IDENTITY_MISSING": 1,
                                         "LEGACY_ROW_WITHOUT_COMPLETENESS": 1}
    board = shadow_leaderboard([good, no_depth, no_ident, legacy])
    assert [row["mean_net_pnl_usd"] for row in board["leaderboard"]] == [1.0]
    assert board["rank_gate"]["excluded_count"] == 3


# -- 3. hard-stop vs ATR-stop counterfactual ---------------------------------------

def test_stop_axis_records_both_outcomes_shadow_only():
    path = [{"ts": 1000 + i, "price": p} for i, p in enumerate([100.0, 99.9, 99.6, 99.0, 98.4])]
    axis = hard_vs_atr_stop_counterfactual(
        path, direction="LONG", entry_price=100.0, fill_ts=1000, leverage=100, atr_abs=1.0,
        exit_policy={"initial_stop_atr_k": 1.5, "hard_stop_margin_pct": 30.0},
        actual_exit_price=98.4, actual_close_ts=1004,
    )
    assert axis["status"] == "COMPUTED" and axis["shadow_only"] and axis["live_policy_effect"] == "NONE"
    assert axis["hard_stop"]["status"] == "HIT" and axis["hard_stop"]["hit_ts"] == 1002
    assert axis["atr_stop"]["status"] == "HIT" and axis["atr_stop"]["stop_price"] == 98.5
    assert axis["first_trigger"] == "HARD"
    assert axis["hard_stop"]["parameter_source"] == "TILE_EXIT_POLICY"


def test_stop_axis_censored_at_actual_exit_and_short_side():
    path = [{"ts": 1000 + i, "price": p} for i, p in enumerate([100.0, 100.1, 99.5])]
    axis = hard_vs_atr_stop_counterfactual(
        path, direction="SHORT", entry_price=100.0, fill_ts=1000, leverage=100, atr_pct=1.0,
        actual_exit_price=99.5, actual_close_ts=1002,
    )
    assert axis["hard_stop"]["status"] == "NOT_HIT_BEFORE_ACTUAL_EXIT"
    assert axis["hard_stop"]["exit_price"] == 99.5 and axis["hard_stop"]["margin_return_pct"] == 50.0
    assert axis["atr_stop"]["parameter_source"] == "AXIS_REFERENCE"
    assert axis["first_trigger"] is None


def test_stop_axis_inputs_incomplete_and_path_unavailable():
    assert hard_vs_atr_stop_counterfactual([], direction="LONG", entry_price=None, fill_ts=1, leverage=1)["status"] == "INPUTS_INCOMPLETE"
    axis = hard_vs_atr_stop_counterfactual([], direction="LONG", entry_price=100, fill_ts=1, leverage=10)
    assert axis["status"] == "PATH_UNAVAILABLE" and axis["atr_stop"]["status"] == "ATR_UNAVAILABLE"


def test_stop_axis_summary_counts_legacy_rows():
    rows = [
        {"observation_status": "PAPER_POSITION_CLOSED"},
        {"observation_status": "PAPER_POSITION_CLOSED", "stop_axis_counterfactual": {
            "status": "COMPUTED", "first_trigger": "ATR",
            "hard_stop": {"status": "NOT_HIT_BEFORE_ACTUAL_EXIT", "margin_return_pct": 10.0},
            "atr_stop": {"status": "HIT", "margin_return_pct": -20.0}}},
    ]
    summary = stop_axis_summary(rows)
    assert summary["status_counts"] == {"COMPUTED": 1, "LEGACY_ROW_FIELD_ABSENT": 1}
    assert summary["hit_counts"] == {"atr_stop": 1}
    assert summary["mean_margin_return_pct"] == {"hard_stop": 10.0, "atr_stop": -20.0}


# -- 4. frozen candidate + control trial -------------------------------------------

def test_frozen_trial_default_off_creates_no_orders():
    trial = FrozenPolicyTrial.from_env({})
    assert trial.status()["state"] == "OFF"
    assert trial.status()["creates_orders"] is False and trial.status()["relay_eligible"] is False
    assert FrozenPolicyTrial.may_create_order() is False
    assert trial.assignment(LANES[0], observed_ts=10**10) is None


def _two_lane_registry():
    candidate = LANES[0]
    control = dict(ACTIVE_TILE_REGISTRY[candidate], policy_signature="control-fixture-signature")
    return {candidate: dict(ACTIVE_TILE_REGISTRY[candidate]), "CONTROL_FIXTURE": control}


def test_frozen_trial_fails_closed_without_a_registered_control():
    # No comparison benchmark: promotion stays gated because neither the
    # benchmark, a retired lane nor the candidate itself can be the control.
    assert COMPARISON_BENCHMARK_LANE is None
    assert LANES == ["FAMILY_COMMITTED_FADE_TAKER_90", "FAMILY_NOTRADE_FOLLOW_TAKER_60",
                     "FAMILY_PREMIUM_REVERSION_60M", "FAMILY_RANDOM_CONTROL_TAKER_90"]
    for control in (COMPARISON_BENCHMARK_LANE, "CONTINUOUS", LANES[0]):
        with pytest.raises(ValueError):
            freeze_selection(LANES[0], control, selected_by="danish", frozen_at_ts=1000.0)


def test_frozen_trial_records_identity_and_detects_drift(tmp_path):
    registry = _two_lane_registry()
    selection = freeze_selection(
        LANES[0], "CONTROL_FIXTURE", selected_by="danish", frozen_at_ts=1000.0, registry=registry,
    )
    path = tmp_path / "selection.json"
    path.write_text(json.dumps(selection), encoding="utf-8")
    env = {trial_module.ENABLED_ENV: "1", trial_module.SELECTION_ENV: str(path)}
    trial = FrozenPolicyTrial.from_env(env, registry=registry)
    status = trial.status()
    assert status["state"] == "RECORDING" and status["trial_id"] == selection["trial_id"]
    assert trial.may_create_order() is False
    stamp = trial.assignment(LANES[0], observed_ts=2000)
    assert stamp["arm"] == "CANDIDATE" and stamp["relay_eligible"] is False and stamp["paper_only"] is True
    assert stamp["arm_policy_signature"] == ACTIVE_TILE_REGISTRY[LANES[0]]["policy_signature"]
    assert trial.assignment("CONTROL_FIXTURE", observed_ts=2000)["arm"] == "CONTROL"
    assert trial.assignment(LANES[0], observed_ts=999) is None  # before freeze
    drifted_registry = {lane: dict(spec) for lane, spec in registry.items()}
    drifted_registry[LANES[0]]["policy_signature"] = "changed"
    drifted = FrozenPolicyTrial.from_env(env, registry=drifted_registry)
    assert drifted.status()["state"] == "DRIFTED" and drifted.assignment(LANES[0], observed_ts=2000) is None


def test_frozen_trial_rejects_tampered_or_unregistered_selection(tmp_path):
    registry = _two_lane_registry()
    with pytest.raises(ValueError):
        freeze_selection("NOT_A_TILE", "CONTROL_FIXTURE", selected_by="x", frozen_at_ts=1, registry=registry)
    selection = freeze_selection(LANES[0], "CONTROL_FIXTURE", selected_by="x", frozen_at_ts=1, registry=registry)
    selection["relay_eligible"] = True
    trial = FrozenPolicyTrial(selection, enabled=True, registry=registry)
    assert trial.status()["state"] == "INVALID"
    missing = FrozenPolicyTrial.from_env({trial_module.ENABLED_ENV: "1"})
    assert missing.status()["reason"] == "SELECTION_PATH_MISSING"


def test_frozen_trial_is_not_a_tile_and_owns_no_order_path():
    import combo_pathway_config
    src = inspect.getsource(trial_module)
    for forbidden in ("submit_order", "place_order", "create_order(", "bitfinex", "relay_enqueue"):
        assert forbidden not in src.replace("may_create_order", "")
    assert "frozen_policy_trial" not in inspect.getsource(combo_pathway_config)


def test_frozen_trial_report_groups_arms():
    rows = [
        {"observation_status": "PAPER_POSITION_CLOSED", "net_pnl_usd": 1.0,
         "frozen_trial": {"trial_id": "t1", "arm": "CANDIDATE", "paper_only": True}},
        {"observation_status": "PAPER_POSITION_CLOSED", "net_pnl_usd": -1.0,
         "frozen_trial": {"trial_id": "t1", "arm": "CONTROL", "paper_only": True}},
        {"observation_status": "PAPER_POSITION_CLOSED", "net_pnl_usd": 9.0},
    ]
    report = frozen_trial_report(rows)
    assert report["t1"]["arms"]["CANDIDATE"]["mean_net_pnl_usd"] == 1.0
    assert report["t1"]["arms"]["CONTROL"]["closed"] == 1


# -- 5. AI usefulness --------------------------------------------------------------

def test_ai_usefulness_tie_vs_sided_and_approved_vs_rejected():
    decisions = [
        {"decision_stage": "LANE_POLICY_VERDICT", "shared_ai_call_id": "c1", "episode_id": "e1",
         "research_lane": "A", "raw_ai_decision": "STRONG_APPROVE", "long_score": 70, "short_score": 20},
        {"decision_stage": "LANE_POLICY_VERDICT", "shared_ai_call_id": "c2", "episode_id": "e2",
         "research_lane": "A", "raw_ai_decision": "REJECT", "long_score": 50, "short_score": 50},
        {"decision_stage": "LANE_POLICY_VERDICT", "shared_ai_call_id": "c3", "episode_id": "e3",
         "research_lane": "A", "raw_ai_decision": None, "score_gap": 0},
        {"decision_stage": "OPPORTUNITY", "shared_ai_call_id": "c4"},
    ]
    lifecycles = [{"observation_status": "PAPER_POSITION_CLOSED", "episode_id": "e1",
                   "research_lane": "A", "net_pnl_usd": 2.0}]
    shadow = [{**shadow_row_completeness(_shadow_buf()), "shared_ai_call_id": "c2",
               "filled": True, "net_pnl_usd": -1.0},
              {"shared_ai_call_id": "c2", "filled": True, "net_pnl_usd": 100.0}]
    report = ai_usefulness_report(decisions, lifecycles, shadow)
    assert report["ai_calls"] == 3 and report["lane_decisions"] == 3
    assert report["tie_vs_sided_calls"] == {"SIDED": 1, "TIE": 2}
    approved, rejected = report["approved_vs_rejected"]["APPROVED"], report["approved_vs_rejected"]["REJECTED"]
    assert approved["decisions"] == 1 and approved["paper_mean_net_pnl_usd"] == 2.0 and approved["paper_win_rate"] == 1.0
    assert rejected["decisions"] == 1 and rejected["shadow_mean_net_pnl_usd"] == -1.0  # legacy shadow excluded
    assert report["approved_vs_rejected"]["UNKNOWN"]["decisions"] == 1


# -- 6. WAL latched alarm ----------------------------------------------------------

def _latch_header_corrupt(wal):
    wal.defer(ledger="execution", record_id="terminal:1", payload=b"row")
    original = wal.header_path.read_bytes()
    damaged = bytearray(original); damaged[20] ^= 1
    wal.header_path.write_bytes(damaged)
    with pytest.raises(RuntimeError, match="HEADER_CORRUPT"):
        wal.status()
    wal.header_path.write_bytes(original)


def test_alarm_catalog_explains_every_code():
    for code in ALARM_CATALOG:
        detail = explain_alarm(code)
        assert detail["reason"] and detail["clears_when"]
        assert detail["clear_path"] in {"AUTOMATIC_WHEN_RESOLVED", "AUTHENTICATED_OPERATOR_CLEAR"}
    assert explain_alarm("SOMETHING_NEW")["clear_path"] == "AUTHENTICATED_OPERATOR_CLEAR"


def test_status_explains_latched_alarm_with_first_and_last_seen(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    _latch_header_corrupt(wal)
    details = wal.status()["alarm_details"]
    assert [d["code"] for d in details] == ["EMERGENCY_WAL_HEADER_CORRUPT"]
    assert details[0]["first_seen"] and details[0]["last_seen"] >= details[0]["first_seen"]
    assert details[0]["clear_path"] == "AUTOMATIC_WHEN_RESOLVED" and details[0]["clears_when"]


def test_auto_clear_waits_for_dwell_then_moves_alarm_to_incidents(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    _latch_header_corrupt(wal)
    first_seen = wal.status()["alarm_details"][0]["first_seen"]
    assert wal.auto_clear_resolved_alarms(now=first_seen + 1)["cleared"] == []
    receipt = wal.auto_clear_resolved_alarms(now=first_seen + ALARM_AUTO_CLEAR_MIN_DWELL_SEC + 1)
    assert receipt["cleared"] == ["EMERGENCY_WAL_HEADER_CORRUPT"] and receipt["actor"] == "AUTO"
    status = wal.status()
    assert status["alarms"] == [] and "EMERGENCY_WAL_HEADER_CORRUPT" in status["incident_alarms"]
    audit = [json.loads(l) for l in (tmp_path / ALARM_AUDIT_NAME).read_text().splitlines()]
    assert audit[-1]["cleared"] == ["EMERGENCY_WAL_HEADER_CORRUPT"]


def test_auto_clear_never_clears_while_condition_persists(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    wal.defer(ledger="execution", record_id="terminal:1", payload=b"row")
    damaged = bytearray(wal.header_path.read_bytes()); damaged[20] ^= 1
    wal.header_path.write_bytes(damaged)
    with pytest.raises(RuntimeError):
        wal.status()
    with pytest.raises(RuntimeError):
        wal.clear_resolved_alarms(actor="danish", reason="try", operator_confirmed=True)
    controls, _ = wal._controls()
    assert all("EMERGENCY_WAL_HEADER_CORRUPT" in c["alarms"] for c in controls)


def test_non_auto_alarm_requires_operator_confirmation_and_records_who_why(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    with _cross_process_lock(wal.lock_path), wal.header_path.open("rb") as hf:
        headers = wal._read_headers(hf)
        wal._reconstruct_both_controls(headers, ["EMERGENCY_WAL_RECORD_ID_CONFLICT"])
    auto = wal.auto_clear_resolved_alarms(now=10**12)
    assert auto["cleared"] == []
    refused = wal.clear_resolved_alarms(actor="danish", reason="checked", codes=["EMERGENCY_WAL_RECORD_ID_CONFLICT"])
    assert refused["cleared"] == [] and refused["retained"][0]["why"] == "OPERATOR_CONFIRMATION_REQUIRED"
    ok = wal.clear_resolved_alarms(actor="danish", reason="writer bug fixed", operator_confirmed=True,
                                   codes=["EMERGENCY_WAL_RECORD_ID_CONFLICT"], auth_method="ADMIN_TOKEN_HEADER")
    assert ok["cleared"] == ["EMERGENCY_WAL_RECORD_ID_CONFLICT"]
    audit = [json.loads(l) for l in (tmp_path / ALARM_AUDIT_NAME).read_text().splitlines()]
    assert audit[0]["cleared"] == [] and audit[-1]["actor"] == "danish"
    assert audit[-1]["reason"] == "writer bug fixed" and audit[-1]["auth_method"] == "ADMIN_TOKEN_HEADER"
    assert audit[-1]["ts"] > 0
    assert "EMERGENCY_WAL_RECORD_ID_CONFLICT" in wal.status()["incident_alarms"]


def test_capacity_alarm_not_cleared_while_reserve_full(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    wal.defer(ledger="execution", record_id="terminal:1", payload=b"row")
    with _cross_process_lock(wal.lock_path), wal.header_path.open("rb") as hf:
        headers = wal._read_headers(hf)
        wal._reconstruct_both_controls(headers, ["EMERGENCY_WAL_CAPACITY_EXHAUSTED"])
    receipt = wal.clear_resolved_alarms(actor="danish", reason="x", operator_confirmed=True)
    assert receipt["cleared"] == []
    assert receipt["retained"] == [{"code": "EMERGENCY_WAL_CAPACITY_EXHAUSTED", "why": "CONDITION_STILL_PRESENT"}]


def test_clear_rejects_invalid_actor_or_missing_reason(tmp_path):
    wal = EmergencyEvidenceWal(tmp_path, identity=IDENTITY, extents=1)
    with pytest.raises(ValueError):
        wal.clear_resolved_alarms(actor="bad actor!", reason="x")
    with pytest.raises(ValueError):
        wal.clear_resolved_alarms(actor="danish", reason="  ")


def test_admin_clear_endpoint_requires_admin_and_named_actor(tmp_path, monkeypatch):
    import bot
    src = inspect.getsource(bot.api_emergency_wal_clear_alarms)
    assert "_admin_authed()" in src and "clear_resolved_alarms(" in src
    assert "auth_method=_admin_auth_method()" in src
    monkeypatch.setattr(bot, "_BOT_ADMIN_TOKEN", "secret")
    monkeypatch.setattr(bot, "_DASHBOARD_BOOTSTRAP_COMPLETE", True)
    monkeypatch.setattr(bot, "_data_sync_volume_root", lambda: tmp_path)
    monkeypatch.setattr(bot, "_data_sync_lifecycle_cleanup_current_identity", lambda: {
        "collection_epoch_id": IDENTITY["epoch_id"], "source_git_rev": IDENTITY["source_revision"],
        "deployed_git_rev": IDENTITY["deployed_revision"],
        "tile_registry_signature": IDENTITY["tile_config_signature"],
    })
    wal = EmergencyEvidenceWal(tmp_path / "v3" / "emergency_evidence_wal_v2", identity=IDENTITY)
    # A retained row keeps construction-time recovery from demoting the alarm.
    wal.defer(ledger="execution", record_id="terminal:1", payload=b"row")
    with _cross_process_lock(wal.lock_path), wal.header_path.open("rb") as hf:
        wal._reconstruct_both_controls(wal._read_headers(hf), ["EMERGENCY_WAL_RECORD_ID_CONFLICT"])
    url = "/api/admin/emergency-wal/clear-alarms"
    client = bot.app.test_client()
    body = {"actor": "danish", "reason": "writer fixed", "operator_confirmed": True}
    assert client.post(url, json=body).status_code == 401
    headers = {"X-Bot-Admin-Token": "secret"}
    assert client.post(url, json={**body, "actor": "AUTO"}, headers=headers).status_code == 400
    assert client.post(url, json={"actor": "danish"}, headers=headers).status_code == 400
    assert client.post(url, json={**body, "codes": "ALL"}, headers=headers).status_code == 400
    response = client.post(url, json=body, headers=headers)
    assert response.status_code == 200, response.get_json()
    receipt = response.get_json()
    assert receipt["cleared"] == ["EMERGENCY_WAL_RECORD_ID_CONFLICT"]
    assert receipt["actor"] == "danish" and receipt["auth_method"] == "ADMIN_TOKEN_HEADER"
    assert wal.status()["alarms"] == []


def test_runtime_status_passes_alarm_details_through():
    import lifecycle_pipeline_runtime
    import research_v3_store
    assert "alarm_details" in inspect.getsource(research_v3_store.V3EvidenceStore.emergency_wal_runtime_status)
    assert "auto_clear_resolved_alarms" in inspect.getsource(research_v3_store.V3EvidenceStore.emergency_wal_runtime_status)
    assert '"alarm_details"' in inspect.getsource(lifecycle_pipeline_runtime)


# -- 7. analyzer reads new fields, backwards compatible -----------------------------

def test_lifecycle_coverage_counts_present_explained_null_and_legacy():
    closed = {"observation_status": "PAPER_POSITION_CLOSED",
              **closed_lifecycle_completeness({"entry_ts": _utc(2), "fill_revalidation_count": 0})}
    no_fill = {"terminal_no_fill": True, "outcome_state": "NO_FILL",
               **unfilled_lifecycle_completeness({}, reason="TTL_EXPIRED", touched=False, created_ts=_utc(9))}
    legacy = {"observation_status": "PAPER_POSITION_CLOSED", "net_pnl_usd": 1.0}
    coverage = lifecycle_completeness_coverage([closed, no_fill, legacy])
    assert coverage["CLOSED"]["rows"] == 2 and coverage["CLOSED"]["legacy_rows"] == 1
    fields = coverage["CLOSED"]["fields"]
    assert fields["session_label"]["present"] == 1 and fields["session_label"]["missing"] == 1
    assert fields["exit_depth"]["null_with_reason"] == 1
    assert fields["fill_revalidation_count"]["present"] == 1
    assert coverage["NO_FILL"]["no_fill_ttl_outcomes"] == {"EXPIRED_NO_TOUCH": 1}
    assert coverage["CLOSED"]["session_labels"]["LEGACY_ROW_FIELD_ABSENT"] == 1


def test_full_report_tolerates_legacy_and_malformed_shadow_files(tmp_path):
    (tmp_path / "shadow_outcome.jsonl").write_text(
        json.dumps({"filled": True, "net_pnl_usd": 1.0}) + "\n{not json\n"
        + json.dumps({**shadow_row_completeness(_shadow_buf()), "filled": True, "net_pnl_usd": 2.0}) + "\n",
        encoding="utf-8",
    )
    report = build_research_completeness_report(
        tmp_path, lifecycles=[{"observation_status": "PAPER_POSITION_CLOSED"}], decisions=[],
    )
    assert report["live_policy_effect"] == "NONE"
    assert report["shadow_source"]["parse_errors"] == 1
    assert report["shadow_ranking"]["rank_gate"]["excluded_count"] == 1
    assert report["shadow_ranking"]["leaderboard"][0]["mean_net_pnl_usd"] == 2.0
    assert report["stop_axis_counterfactual"]["status_counts"] == {"LEGACY_ROW_FIELD_ABSENT": 1}


def test_v3_report_embeds_research_completeness_section():
    source = (Path(__file__).parent / "research" / "research_v3_report.py").read_text(encoding="utf-8")
    assert 'report["research_completeness"] = build_research_completeness_report(' in source

"""Tape-sourced multiverse maturation, per-call entry grid and collection health."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import collector_v22
from collector_v22 import build_research_event, terminal_observation
from collector_v22_schema import OBS_DATA_ERROR, OBS_INSUFFICIENT_PATH, OBS_SOURCE_UNAVAILABLE
from microstructure_tape import SCHEMA as TAPE_SCHEMA
from multiverse_collection_health import DEFECT_REASON, build_multiverse_collection_report
from multiverse_entry_grid import (
    GRID_FILE,
    STATUS_GRID_MISSING,
    STATUS_HYDRATED,
    STATUS_INLINE,
    hydrate_entry_children,
    load_grid_index,
    load_order_multiverse,
    make_anchor,
    split_entry_grid,
)
from replay_eligibility import validate_replay_eligibility
from tape_minute_bars import (
    WINDOW_AVAILABLE,
    WINDOW_BEFORE_TAPE,
    WINDOW_SOURCE_BEHIND,
    WINDOW_SOURCE_NOT_READY,
    TapeMinuteBarStore,
    merge_path_candles,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fly_monitor_rules as rules  # noqa: E402

SIGNAL_TS = 1_700_000_040.0  # minute-aligned (1_700_000_040 % 60 == 0)
TAPE_FILE = "market_microstructure_1s.jsonl"


def _tape_row(sec: int, price: float, *, fresh: bool = True) -> dict:
    return {
        "schema": TAPE_SCHEMA, "bucket_ts": sec, "fresh": fresh, "valid_bbo": True,
        "last": price, "buy_vwap": price + 0.5, "sell_vwap": price - 0.5,
        "buy_qty": 0.01, "sell_qty": 0.02, "bid": price - 1, "ask": price + 1,
    }


def _write_tape(path: Path, start: int, end: int, price_fn=lambda s: 100000.0, **kw) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for sec in range(start, end):
            handle.write(json.dumps(_tape_row(sec, price_fn(sec), **kw)) + "\n")


def _bar(minute: int, *, high: float = 100010.0, low: float = 99990.0) -> list:
    return [int((SIGNAL_TS + minute * 60) * 1000), 100000.0, high, low, 100000.0, 1.0]


# --------------------------------------------------------------------- tape
def test_tape_minute_bars_aggregate_closed_fresh_minutes(tmp_path):
    base = int(SIGNAL_TS)
    _write_tape(tmp_path / TAPE_FILE, base, base + 150, price_fn=lambda s: 100000.0 + (s - base))
    store = TapeMinuteBarStore(str(tmp_path))
    assert store.window_state(base, base + 60) == WINDOW_SOURCE_NOT_READY
    store.refresh()
    assert store.initial_scan_complete
    candles = store.candles(base, base + 600)
    # Minute 2 (seconds 120..149) is still open on the tape.
    assert [row[0] for row in candles] == [base * 1000, (base + 60) * 1000]
    first = candles[0]
    assert first[1] == 100000.0            # open = first last price
    assert first[2] == 100000.0 + 59 + 0.5  # high includes buy VWAP
    assert first[3] == 100000.0 - 0.5       # low includes sell VWAP
    assert first[4] == 100000.0 + 59        # close = last price of last second
    assert first[5] == pytest.approx(60 * 0.03)


def test_tape_minute_bars_idempotent_across_rotation(tmp_path):
    base = int(SIGNAL_TS)
    live = tmp_path / TAPE_FILE
    _write_tape(live, base, base + 90)
    store = TapeMinuteBarStore(str(tmp_path))
    store.refresh()
    ingested = store.rows_ingested
    os.replace(live, tmp_path / (TAPE_FILE + ".1"))
    _write_tape(live, base + 90, base + 200)
    store.refresh()
    assert store.rows_ingested == ingested + 110
    assert store.rows_duplicate == 0
    assert [row[0] // 1000 for row in store.candles(base, base + 600)] == [base, base + 60, base + 120]


def test_tape_minute_needs_min_fresh_seconds(tmp_path):
    base = int(SIGNAL_TS)
    _write_tape(tmp_path / TAPE_FILE, base, base + 10)                # 10 fresh seconds
    _write_tape(tmp_path / TAPE_FILE, base + 10, base + 60, fresh=False)
    _write_tape(tmp_path / TAPE_FILE, base + 60, base + 181)
    store = TapeMinuteBarStore(str(tmp_path))
    store.refresh()
    minutes = [row[0] // 1000 for row in store.candles(base, base + 600)]
    assert base not in minutes and base + 60 in minutes


def test_tape_window_states(tmp_path):
    base = int(SIGNAL_TS)
    _write_tape(tmp_path / TAPE_FILE, base, base + 600)
    store = TapeMinuteBarStore(str(tmp_path))
    store.refresh()
    assert store.window_state(base + 10, base + 500) == WINDOW_AVAILABLE
    assert store.window_state(base + 10, base + 5000) == WINDOW_SOURCE_BEHIND
    assert store.window_state(base - 3600, base + 500) == WINDOW_BEFORE_TAPE


def test_merge_prefers_tape_and_fills_gaps_from_cache():
    tape = [[int(SIGNAL_TS * 1000), 1, 2, 0.5, 1.5, 3]]
    cache = [_bar(0), _bar(1)]
    merged, counts = merge_path_candles(tape, cache)
    assert merged[0] == tape[0]
    assert merged[1][0] == _bar(1)[0]
    assert counts == {"tape_bars": 1, "cache_bars_filled": 1}


# ---------------------------------------------------------- collector retry
def test_empty_path_after_deadline_is_retryable_not_terminal():
    event = build_research_event(
        trade_id="no-source", epoch_id="epoch-t", signal_ts=SIGNAL_TS, signal_price=100000.0,
        candles_1m=[], submitted=False, rejected=True, ticket_closed=True,
        evaluation_ts=SIGNAL_TS + 4 * 3600,
        path_source={"window_state": WINDOW_SOURCE_NOT_READY},
    )
    assert event["observation_status"] == OBS_SOURCE_UNAVAILABLE
    assert not terminal_observation(OBS_SOURCE_UNAVAILABLE)
    assert event["immutable"] is False


def test_source_behind_with_partial_path_is_retryable():
    candles = [_bar(i) for i in range(-60, 30)]
    event = build_research_event(
        trade_id="behind", epoch_id="epoch-t", signal_ts=SIGNAL_TS, signal_price=100000.0,
        candles_1m=candles, submitted=False, rejected=True, ticket_closed=True,
        evaluation_ts=SIGNAL_TS + 4 * 3600,
        path_source={"window_state": WINDOW_SOURCE_BEHIND},
    )
    assert event["observation_status"] == OBS_SOURCE_UNAVAILABLE


def test_window_entirely_before_tape_finalizes_data_error_not_eternal_pending():
    event = build_research_event(
        trade_id="pre-tape", epoch_id="epoch-t", signal_ts=SIGNAL_TS, signal_price=100000.0,
        candles_1m=[], submitted=True, rejected=False, ticket_closed=True,
        evaluation_ts=SIGNAL_TS + 12 * 3600,
        path_source={"window_state": WINDOW_BEFORE_TAPE},
    )
    assert event["observation_status"] == OBS_DATA_ERROR
    assert event["canonical_tape"]["coverage"]["reason"] == "PATH_SOURCE_NEVER_RECORDED"
    assert terminal_observation(OBS_DATA_ERROR)
    assert event["ranking_eligible"] is False


def test_partially_recorded_pre_tape_window_keeps_truthful_insufficient_path():
    candles = [_bar(i) for i in range(20, 181)]
    event = build_research_event(
        trade_id="partial-pre-tape", epoch_id="epoch-t", signal_ts=SIGNAL_TS, signal_price=100000.0,
        candles_1m=candles, submitted=True, rejected=False, ticket_closed=True,
        evaluation_ts=SIGNAL_TS + 181 * 60,
        path_source={"window_state": WINDOW_BEFORE_TAPE},
    )
    assert event["observation_status"] == OBS_INSUFFICIENT_PATH
    assert event["canonical_tape"]["path_1m"]


def test_loaded_source_proving_a_hole_still_finalizes_insufficient():
    candles = [_bar(i) for i in range(-60, 181) if i != -30]
    event = build_research_event(
        trade_id="hole", epoch_id="epoch-t", signal_ts=SIGNAL_TS, signal_price=100000.0,
        candles_1m=candles, submitted=False, rejected=True, ticket_closed=True,
        evaluation_ts=SIGNAL_TS + 181 * 60,
        path_source={"window_state": WINDOW_AVAILABLE},
    )
    assert event["observation_status"] == OBS_INSUFFICIENT_PATH
    assert event["canonical_tape"]["path_source"]["window_state"] == WINDOW_AVAILABLE


def test_complete_tape_path_resolves_terminal():
    candles = [_bar(i) for i in range(-60, 190)]
    event = build_research_event(
        trade_id="full", epoch_id="epoch-t", signal_ts=SIGNAL_TS, signal_price=100000.0,
        candles_1m=candles, submitted=False, rejected=True, ticket_closed=True,
        evaluation_ts=SIGNAL_TS + 190 * 60,
        path_source={"window_state": WINDOW_AVAILABLE},
    )
    assert terminal_observation(event["observation_status"])
    assert event["observation_status"] != OBS_INSUFFICIENT_PATH
    assert event["canonical_tape"]["path_1m"]


# --------------------------------------------------------------- entry grid
def _tile_event(trade_id: str, signal_ts: float, anchor: dict) -> dict:
    candles = [_bar(i, high=100000.0 + 5 * (i % 40), low=100000.0 - 5 * (i % 40)) for i in range(-60, 190)]
    return build_research_event(
        trade_id=trade_id, epoch_id="epoch-t", signal_ts=signal_ts, signal_price=100000.0,
        direction="SHORT", candles_1m=candles, ttl_sec=1500.0 + (signal_ts - SIGNAL_TS),
        submitted=True, rejected=False, ticket_closed=True, shared_ai_call_id="call-1",
        evaluation_ts=SIGNAL_TS + 190 * 60, path_source={"window_state": WINDOW_AVAILABLE},
        entry_grid_anchor=anchor,
    )


def test_tiles_of_one_call_share_one_grid_and_hydrate_identically(tmp_path):
    anchor = make_anchor(shared_ai_call_id="call-1", signal_ts=SIGNAL_TS, signal_price=100000.0,
                         direction="SHORT", ttl_sec=1800.0)
    first = _tile_event("tile-a", SIGNAL_TS + 3, anchor)
    second = _tile_event("tile-b", SIGNAL_TS + 11, anchor)
    assert first["entry_children"] == second["entry_children"]
    assert first["entry_grid_anchor"] == anchor
    for event in (first, second):
        assert validate_replay_eligibility(event)["eligible"] in (True, False)
        reasons = validate_replay_eligibility(event).get("reasons") or []
        assert "FILL_BEFORE_SIGNAL" not in reasons

    row_a, grid_a = split_entry_grid(first)
    row_b, grid_b = split_entry_grid(second)
    assert grid_a["grid_sha256"] == grid_b["grid_sha256"]
    assert "entry_children" not in row_a and row_a["entry_children_ref"]["entry_children_count"] == 300
    assert len(json.dumps(row_a)) < 0.3 * len(json.dumps(first))

    with (tmp_path / GRID_FILE).open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(grid_a) + "\n")
    with (tmp_path / "order_multiverse.jsonl").open("w", encoding="utf-8") as handle:
        for row in (row_a, row_b, dict(first, trade_id="legacy-inline")):
            handle.write(json.dumps(row) + "\n")
    rows = list(load_order_multiverse(str(tmp_path)))
    assert [row["entry_children_status"] for row in rows] == [STATUS_HYDRATED, STATUS_HYDRATED, STATUS_INLINE]
    assert rows[0]["entry_children"] == first["entry_children"]
    assert hydrate_entry_children(row_a, {})["entry_children_status"] == STATUS_GRID_MISSING
    assert set(load_grid_index(str(tmp_path))) == {grid_a["grid_sha256"]}


def test_entry_children_memo_returns_independent_copies():
    candles = [_bar(i) for i in range(-60, 60)]
    kwargs = dict(candles_1m=candles, signal_ts=SIGNAL_TS, signal_price=100000.0,
                  direction="SHORT", ttl_sec=1800.0, live_orig=0.3)
    first = collector_v22.build_entry_children(**kwargs)
    first[0]["mutated"] = True
    second = collector_v22.build_entry_children(**kwargs)
    assert "mutated" not in second[0]
    assert second == collector_v22._build_entry_children_uncached(**kwargs)


# ------------------------------------------------------------ monitoring
def test_collection_findings_map_bot_alarms():
    health = {"research_collection": {
        "alarms": ["MULTIVERSE_EMPTY_PATH_RATE_HIGH", "TOUCH_GRID_COVERAGE_LOW",
                   "COLLECTOR_MATURATION_WORKER_STALLED"],
        "multiverse": {"empty_path_1h": 9, "written_1h": 10, "empty_path_rate_1h": 0.9, "pending": 40,
                       "maturation_worker": {"alive": False}},
        "touch_grid": {"armed_calls_1h": 1, "eligible_calls_1h": 5, "coverage_1h": 0.2},
    }}
    found = rules.collection_findings(health)
    assert set(found) == {"multiverse_empty_path", "touch_grid_coverage", "multiverse_worker_stalled"}
    assert "9/10" in found["multiverse_empty_path"] and "90%" in found["multiverse_empty_path"]
    assert "1/5" in found["touch_grid_coverage"]
    assert rules.collection_findings({"research_collection": {"alarms": []}}) == {}
    assert rules.collection_findings({}) == {}


def test_collection_alert_keys_have_policies():
    import fly_monitor_alerts as alerts
    for key in ("multiverse_empty_path", "multiverse_tape_source", "multiverse_worker_stalled",
                "touch_grid_coverage"):
        assert key in alerts.POLICIES


# --------------------------------------------------------------- analyzer
def test_analyzer_report_quarantines_empty_path_rows_and_measures_post_fix(tmp_path):
    anchor = make_anchor(shared_ai_call_id="call-1", signal_ts=SIGNAL_TS, signal_price=100000.0,
                         direction="SHORT", ttl_sec=1800.0)
    good = _tile_event("tile-good", SIGNAL_TS + 3, anchor)
    good_row, grid = split_entry_grid(dict(good, event=good["observation_status"]))
    legacy = build_research_event(
        trade_id="legacy-empty", epoch_id="epoch-t", signal_ts=SIGNAL_TS - 7200, signal_price=100000.0,
        candles_1m=[], submitted=True, rejected=False, ticket_closed=True,
        shared_ai_call_id="call-0", evaluation_ts=SIGNAL_TS,
    )
    assert legacy["observation_status"] == OBS_SOURCE_UNAVAILABLE
    # Emulate a pre-fix row: finalized INSUFFICIENT_PATH with an empty path and no path_source.
    legacy.update(observation_status=OBS_INSUFFICIENT_PATH)
    legacy["canonical_tape"].pop("path_source")
    dangling = dict(good_row, trade_id="tile-dangling",
                    entry_children_ref=dict(good_row["entry_children_ref"], grid_sha256="0" * 64))
    pre_tape = build_research_event(
        trade_id="pre-tape", epoch_id="epoch-t", signal_ts=SIGNAL_TS - 86400, signal_price=100000.0,
        candles_1m=[], submitted=True, rejected=False, ticket_closed=True,
        shared_ai_call_id="call-old", evaluation_ts=SIGNAL_TS,
        path_source={"window_state": WINDOW_BEFORE_TAPE},
    )
    # Tape-sourced row that matured after the fix but whose call predates arming.
    early_row = json.loads(json.dumps(good_row, default=str))
    early_row["trade_id"] = "tile-early"
    early_row["envelope"]["signal_ts"] = SIGNAL_TS - 3600
    early_row["entry_grid_anchor"]["shared_ai_call_id"] = "call-early"
    with (tmp_path / "order_multiverse.jsonl").open("w", encoding="utf-8") as handle:
        for row in (legacy, good_row, dangling, pre_tape, early_row):
            handle.write(json.dumps(row, default=str) + "\n")
    with (tmp_path / GRID_FILE).open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(grid) + "\n")
    with (tmp_path / "chase_offset_touch_grid.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"shared_ai_call_id": "call-0", "discovery_shadow_only": True,
                                 "signal_ts": SIGNAL_TS - 7200}) + "\n")
        handle.write(json.dumps({"shared_ai_call_id": "call-1", "discovery_shadow_only": True,
                                 "signal_ts": SIGNAL_TS,
                                 "tile_admission_basis": "SCORE_LED_ADMISSION"}) + "\n")

    report = build_multiverse_collection_report(str(tmp_path))
    assert report["quarantine"]["reason"] == DEFECT_REASON
    assert report["quarantine"]["rows"] == 1
    assert report["quarantined_trade_ids"] == ["legacy-empty", "pre-tape"]
    assert report["source_never_recorded"]["rows"] == 1
    assert report["source_never_recorded"]["status_counts"] == {OBS_DATA_ERROR: 1}
    assert report["legacy_cache_only_rows"]["empty_path_rate"] == 1.0
    assert report["post_fix_rows"]["rows"] == 3
    assert report["post_fix_rows"]["empty_path_rate"] == 0.0
    assert report["entry_grid"]["missing_grid_references"] == 1
    assert "MULTIVERSE_ENTRY_GRID_REFERENCE_MISSING" in report["alarms"]
    coverage = report["touch_grid_coverage"]
    assert coverage["arm_fix_signal_ts"] == SIGNAL_TS
    assert coverage["post_fix"] == {"tile_calls": 1, "discovery_grid_calls": 1, "coverage": 1.0}
    # call-early matured with a tape path yet was never armed: pre-arm cohort, no alarm.
    assert coverage["legacy"] == {"tile_calls": 2, "discovery_grid_calls": 1, "coverage": 0.5}
    assert "TOUCH_GRID_COVERAGE_LOW" not in report["alarms"]
    assert coverage["discovery_calls_by_admission_basis"] == {
        "SCORE_LED_ADMISSION": 1, "AI_APPROVE_LEGACY": 1,
    }


def test_analyzer_registers_collection_health_report():
    import analyzer_research_engine_v62 as engine
    from research_reset_inventory import ANALYZER_REPORT_FILES
    name = engine.MULTIVERSE_COLLECTION_HEALTH_REPORT_FILE
    assert name in engine.ANALYZER_JSON_REPORT_FILES
    assert name in {row[1] for row in engine.DEEP_DIVE_REPORT_CATALOG}
    assert name in ANALYZER_REPORT_FILES


# ------------------------------------------------------ bot drain contract
def _bot_function(name: str):
    import ast
    tree = ast.parse((Path(__file__).resolve().parent / "bot.py").read_text(encoding="utf-8"))
    return next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)


def test_terminal_execution_releases_superseded_rejected_provisional():
    """A suppressed rejected source must leave pending and the journal, or it
    heads every oldest-first sweep forever (Fly 2026-09-30: 19 rows, every pass)."""
    import ast
    fn = _bot_function("persist_rejected_opportunity")
    branch = next(
        node for node in ast.walk(fn)
        if isinstance(node, ast.If) and "_execution_trade_is_terminal" in ast.unparse(node.test)
    )
    body = "\n".join(ast.unparse(stmt) for stmt in branch.body)
    assert "collector_rejected" in body
    assert "_order_multiverse_pending_src.pop(tid" in body
    assert "remove_provisional_event(tid" in body
    assert isinstance(branch.body[-1], ast.Return)


def test_worker_sweep_orders_by_attempts_and_throttles_v3_reconcile():
    import ast
    source = ast.unparse(_bot_function("_maybe_complete_pending_order_multiverse"))
    assert "attempts.get(pending_id, 0)" in source
    assert "attempts[pending_id] = attempts.get(pending_id, 0) + 1" in source
    assert "COLLECTOR_V3_TERMINAL_RECONCILE_INTERVAL_SEC" in source
    loop = ast.unparse(_bot_function("collector_maturation_worker_loop"))
    assert "COLLECTOR_MATURATION_WORKER_BACKLOG_INTERVAL_SEC" in loop

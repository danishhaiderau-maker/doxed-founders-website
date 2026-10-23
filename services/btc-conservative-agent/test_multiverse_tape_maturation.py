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
from multiverse_entry_grid import make_anchor, split_entry_grid
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


def _tile_event(trade_id: str, signal_ts: float, anchor: dict) -> dict:
    candles = [_bar(i, high=100000.0 + 5 * (i % 40), low=100000.0 - 5 * (i % 40)) for i in range(-60, 190)]
    return build_research_event(
        trade_id=trade_id, epoch_id="epoch-t", signal_ts=signal_ts, signal_price=100000.0,
        direction="SHORT", candles_1m=candles, ttl_sec=1500.0 + (signal_ts - SIGNAL_TS),
        submitted=True, rejected=False, ticket_closed=True, shared_ai_call_id="call-1",
        evaluation_ts=SIGNAL_TS + 190 * 60, path_source={"window_state": WINDOW_AVAILABLE},
        entry_grid_anchor=anchor,
    )


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
def test_analyzer_report_labels_r17_retired_rows_and_quarantines_legacy_defects(tmp_path):
    """R17: every v22 row now has an empty path_1m (RETIRED_R17). The report
    labels such rows "source retired (R17)" - not a collection defect, no
    alarm, no cohort math - while a pre-R17 empty-path row stays quarantined."""
    anchor = make_anchor(shared_ai_call_id="call-1", signal_ts=SIGNAL_TS, signal_price=100000.0,
                         direction="SHORT", ttl_sec=1800.0)
    retired = [_tile_event(f"tile-{i}", SIGNAL_TS + 3 + i, anchor) for i in range(6)]
    for row in retired:
        assert row["canonical_tape"]["path_1m"] == []
        assert row["canonical_tape"]["path_1m_status"] == "RETIRED_R17"
    legacy = build_research_event(
        trade_id="legacy-empty", epoch_id="epoch-t", signal_ts=SIGNAL_TS - 7200, signal_price=100000.0,
        candles_1m=[], submitted=True, rejected=False, ticket_closed=True,
        shared_ai_call_id="call-0", evaluation_ts=SIGNAL_TS,
    )
    assert legacy["observation_status"] == OBS_SOURCE_UNAVAILABLE
    # Emulate a pre-fix, pre-R17 row: INSUFFICIENT_PATH, empty path, no path_source/status.
    legacy.update(observation_status=OBS_INSUFFICIENT_PATH)
    legacy["canonical_tape"].pop("path_source", None)
    legacy["canonical_tape"].pop("path_1m_status", None)
    with (tmp_path / "order_multiverse.jsonl").open("w", encoding="utf-8") as handle:
        for row in [legacy] + retired:
            handle.write(json.dumps(row, default=str) + "\n")

    report = build_multiverse_collection_report(str(tmp_path))
    assert report["source_retired"]["rows"] == 6
    assert report["source_retired"]["label"] == "source retired (R17)"
    assert report["source_retired"]["reason"] == "RETIRED_R17"
    assert report["quarantine"]["reason"] == DEFECT_REASON
    assert report["quarantine"]["rows"] == 1
    assert report["quarantined_trade_ids"] == ["legacy-empty"]
    assert report["post_fix_rows"]["rows"] == 0
    assert report["legacy_cache_only_rows"]["rows"] == 1
    assert report["alarms"] == []
    assert report["rows_total"] == 7
    assert report["touch_grid_coverage"]["post_fix"]["tile_calls"] == 0

    only_retired = tmp_path / "only"
    only_retired.mkdir()
    with (only_retired / "order_multiverse.jsonl").open("w", encoding="utf-8") as handle:
        for row in retired:
            handle.write(json.dumps(row, default=str) + "\n")
    report = build_multiverse_collection_report(str(only_retired))
    assert report["status"] == "SOURCE_RETIRED_R17"
    assert report["quarantine"]["rows"] == 0
    assert report["alarms"] == []
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
    assert "_run_v3_terminal_reconcile(now)" in source
    run = ast.unparse(_bot_function("_run_v3_terminal_reconcile"))
    assert "COLLECTOR_V3_TERMINAL_RECONCILE_INTERVAL_SEC" in run
    loop = ast.unparse(_bot_function("collector_maturation_worker_loop"))
    assert "COLLECTOR_MATURATION_WORKER_BACKLOG_INTERVAL_SEC" in loop


def test_analyzer_labels_late_matured_rows_without_excluding_them(tmp_path):
    anchor = make_anchor(shared_ai_call_id="call-1", signal_ts=SIGNAL_TS, signal_price=100000.0,
                         direction="SHORT", ttl_sec=1800.0)
    rows = []
    for trade_id, lag in (("on-time", 120.0), ("late-derived", 5 * 3600.0), ("late-marked", None)):
        event = _tile_event(trade_id, SIGNAL_TS + 3, anchor)
        row, _grid = split_entry_grid(dict(event, event=event["observation_status"]))
        row = json.loads(json.dumps(row, default=str))
        # Pre-R17 rows already on disk carried a 1m path; R17 only labels new rows.
        row["canonical_tape"]["path_1m"] = [_bar(i) for i in range(0, 3)]
        row["canonical_tape"].pop("path_1m_status", None)
        source = row["canonical_tape"].setdefault("path_source", {})
        source["window_end_ts"] = SIGNAL_TS + 10_800
        if lag is None:
            source.update(maturation_lag_sec=7200.0, late_backfill=True,
                          late_backfill_reason="MATURED_LATE_FROM_RETAINED_TAPE")
        else:
            source["tape_latest_bucket_ts"] = SIGNAL_TS + 10_800 + lag
        rows.append(row)
    with (tmp_path / "order_multiverse.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, default=str) + "\n")
    report = build_multiverse_collection_report(str(tmp_path))
    late = report["late_maturation"]
    assert late["rows"] == 2 and late["marked_rows"] == 1 and late["derived_rows"] == 1
    assert late["action"] == "LABELLED_NOT_EXCLUDED"
    assert sorted(late["trade_ids_sample"]) == ["late-derived", "late-marked"]
    assert late["max_lag_sec"] == 5 * 3600.0
    assert not set(late["trade_ids_sample"]) & set(report["quarantined_trade_ids"])
    assert report["post_fix_rows"]["rows"] == 3

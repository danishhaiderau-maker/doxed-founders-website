import random

import pytest

import research_v3_candidates as rc
from research_v3_candidates import evaluate_protection_screen, protection_replay_window_summary, protection_screen
from book_walk_test_fixture import build_tape
from research_v3_book_walk import BASIS_BOOK_WALK, R17_UNPROVEN, PreparedBookWalkArrays, coverage_summary
from research_v3_policy_replay import ReplayPlan, replay_cell
from research_v3_side_marks import (
    BASIS_SEGMENT,
    BASIS_SEGMENT_AND_TAPE,
    BASIS_TAPE,
    PATH_HORIZON_SEC,
    SideMarkTape,
    event_side_marks,
)


def _quote(ts, bid, ask, **extra):
    return {"schema": "market_microstructure_1s_v1", "symbol": "tBTCF0:USTF0", "bucket_ts": ts,
            "ts": float(ts), "bid": bid, "ask": ask, "last": (bid + ask) / 2, "price": (bid + ask) / 2, **extra}


def _walk(seed, start, seconds, gap_every=0):
    rng = random.Random(seed)
    mid, rows = 50000.0, []
    for i in range(seconds):
        mid *= 1 + rng.gauss(0, 0.0004)
        if gap_every and i % gap_every == gap_every - 1:
            continue
        half = rng.choice((0.5, 1.0, 3.0))
        rows.append(_quote(start + i, round(mid - half, 1), round(mid + half, 1)))
    return rows


def _book_walk(seed, start=990, seconds=PATH_HORIZON_SEC + 900, **kwargs):
    """Random-walk bfx_book_10l rows (+ trade prints at mid) around 50 000."""
    rng = random.Random(seed)
    mid, mids = 50000.0, {}
    for s in range(start, start + seconds):
        mid *= 1 + rng.gauss(0, 0.0004)
        mids[s] = round(mid, 1)
    return build_tape(start, start + seconds - 1, bid=lambda s: mids[s] - 0.5, spread=1.0,
                      trade_every=7, trade_px=lambda s: mids[s], **kwargs)


def _source(direction="LONG", rows=(), fill_ts=1001.0, fill_price=None, regime="BULL", event_id="ev-1",
            episode_id="ep-1", ohlc=()):
    return {
        "epoch_id": "epoch-t", "event_id": event_id, "episode_id": episode_id, "opportunity_id": f"opp-{episode_id}",
        "tape_ids": ["tape-1"], "signal_ts": 1000.0, "direction": direction, "atr14_pct": 0.1,
        "leverage": 100, "margin_usd": 0.25, "regime": regime, "ordered_1s_prices": list(rows),
        "canonical_1m_ohlc": list(ohlc),
        "entry_children": [{"entry_policy_id": "TAKER", "offset_pct": 0.0, "chase_id": "no_chase",
                            "fill_ts": fill_ts, "fill_price": fill_price or 50000.0, "fill_model": "IDEAL_TOUCH"}],
    }


def _diagnostic_outcomes(monkeypatch, sources, **kwargs):
    captured = {}
    original = rc.validate_policy

    def capture(episodes, **inner):
        if inner.get("conservative_execution") is False and inner.get("sealed_holdout") is False:
            for row in episodes:
                for policy_id, outcome in (row.get("policy_outcomes") or {}).items():
                    captured.setdefault(policy_id, []).append(outcome)
        return original(episodes, **inner)

    monkeypatch.setattr(rc, "validate_policy", capture)
    return evaluate_protection_screen(sources, **kwargs), captured


def test_long_marks_on_bid_short_on_ask_and_segment_rows_win():
    tape = SideMarkTape.from_rows([_quote(t, 100.0 + t, 101.0 + t) for t in range(0, 10)], start_ts=0, end_ts=20)
    segment = [_quote(3, 500.0, 501.0), _quote(15, 600.0, 601.0), _quote(4, 9.0, 8.0)]
    long = event_side_marks(direction="LONG", segment_rows=segment, tape=tape, start_ts=0, end_ts=20)
    short = event_side_marks(direction="SHORT", segment_rows=segment, tape=tape, start_ts=0, end_ts=20)
    assert long["basis"] == short["basis"] == BASIS_SEGMENT_AND_TAPE
    assert dict(zip(long["ts"], long["price"]))[3.0] == 500.0
    assert dict(zip(short["ts"], short["price"]))[3.0] == 501.0
    assert dict(zip(long["ts"], long["price"]))[4.0] == 104.0  # crossed segment row rejected
    assert dict(zip(short["ts"], short["price"]))[5.0] == 106.0
    assert list(long["ts"]) == sorted(long["ts"]) and 15.0 in long["ts"]
    assert event_side_marks(direction="LONG", segment_rows=segment, tape=None, start_ts=0, end_ts=20)["basis"] == BASIS_SEGMENT
    assert event_side_marks(direction="LONG", segment_rows=[], tape=tape, start_ts=0, end_ts=20)["basis"] == BASIS_TAPE


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_screen_reprices_exits_on_the_book_walk_never_on_1s_rows(monkeypatch, direction):
    tape, entry = _book_walk(7), 50000.0
    garbage = [_quote(1000 + i, 1.0, 2.0) for i in range(PATH_HORIZON_SEC)]  # banned source: must be ignored
    screen, outcomes = _diagnostic_outcomes(monkeypatch, [_source(direction, garbage, fill_price=entry)],
                                            book_walk=tape)
    assert screen["mark_source"]["events_by_basis"] == {BASIS_BOOK_WALK: 1}
    assert screen["mark_source"]["alert_level"] == "GREEN"
    assert screen["mark_source"]["price_source"] == "BFX_BOOK_10L"
    _, clean = _diagnostic_outcomes(monkeypatch, [_source(direction, (), fill_price=entry)], book_walk=tape)
    def project(found):
        # The second capture wraps the first, so each outcome appears twice there.
        return {k: sorted({(o["net_pnl_usd"], o["exit_reason"], o["outcome_state"]) for o in v}) for k, v in found.items()}

    assert project(clean) == project(outcomes)
    window = tape.window(1000.0 - 2, 1001.0 + PATH_HORIZON_SEC)
    exits = set()
    for protection in protection_screen():
        path = PreparedBookWalkArrays(window, direction=direction, entry_price=entry, leverage=100.0,
                                      fill_ts=1001.0, horizon_sec=PATH_HORIZON_SEC, qty=0.25 * 100.0 / entry)
        expected = replay_cell(path, ReplayPlan(protection), atr_pct_at_fill=0.1, leverage=100.0, margin_usd=0.25)
        outcome = outcomes[f"TAKER|{protection['protection_id']}"][0]
        assert (outcome["net_pnl_usd"], outcome["exit_reason"]) == (expected["net_pnl_usd"], expected["exit_reason"])
        exits.add(outcome["exit_reason"])
    assert len(exits) > 1


def test_unproven_events_are_excluded_and_alerted():
    tape = _book_walk(1, seconds=PATH_HORIZON_SEC + 3000, skip_book=lambda s: 4000 <= s < 4010)
    sources = [
        _source(event_id="a", episode_id="ep-a", fill_price=50000.0),
        _source(event_id="b", episode_id="ep-b", fill_price=50000.0),
        _source(event_id="c", episode_id="ep-c", fill_price=50000.0),
    ]
    sources[1]["signal_ts"], sources[1]["entry_children"][0]["fill_ts"] = 3000.0, 3001.0
    sources[2]["signal_ts"], sources[2]["entry_children"][0]["fill_ts"] = 90000.0, 90001.0
    screen = evaluate_protection_screen(sources, book_walk=tape)
    mark = screen["mark_source"]
    assert mark["events_by_basis"] == {R17_UNPROVEN: 3}
    assert mark["r17_unproven_reasons"] == {"BOOK_ROW_GAP_OVER_2S": 2, "BOOK_ROWS_DO_NOT_COVER_WINDOW": 1}
    assert (mark["countable_events"], mark["alert_level"], mark["reason"]) == (0, "RED", "R17_UNPROVEN_MAJORITY")
    assert screen["candidates"] == []
    clean = _book_walk(1, seconds=PATH_HORIZON_SEC + 3000)
    screen = evaluate_protection_screen(sources, book_walk=clean)
    mark = screen["mark_source"]
    assert mark["events_by_basis"] == {BASIS_BOOK_WALK: 2, R17_UNPROVEN: 1}
    assert (mark["alert_level"], mark["reason"]) == ("AMBER", "R17_UNPROVEN_EVENTS_EXCLUDED")
    assert {base for row in screen["candidates"] for base in row["replay_path_bases"]} == {BASIS_BOOK_WALK}
    assert all(row["episodes_total"] == 2 for row in screen["candidates"])
    window = {"schema": "protection_replay_event_window_v1", "events_eligible": 3, "events_selected": 3,
              "truncated": False, "mark_source": mark}
    summary = protection_replay_window_summary(window)
    assert (summary["alert_level"], summary["reason"], summary["mark_r17_unproven_events"]) == (
        "AMBER", "R17_UNPROVEN_EVENTS_EXCLUDED", 1)
    window["mark_source"] = coverage_summary({BASIS_BOOK_WALK: 3}, {}, {}, None, horizon_sec=PATH_HORIZON_SEC)
    assert protection_replay_window_summary(window)["alert_level"] == "GREEN"


def test_later_event_of_an_unproven_episode_never_stands_in_for_it():
    tape = _book_walk(2)
    first = _source(event_id="a1", episode_id="ep-a", fill_price=50000.0)
    first["signal_ts"], first["entry_children"][0]["fill_ts"] = 90000.0, 90001.0
    second = _source(event_id="a2", episode_id="ep-a", fill_price=50000.0)
    second["entry_children"][0]["entry_policy_id"] = "OTHER_ENTRY"
    screen = evaluate_protection_screen([first, second], book_walk=tape)
    assert screen["mark_source"]["r17_unproven_reasons"] == {
        "BOOK_ROWS_DO_NOT_COVER_WINDOW": 1, "EPISODE_FIRST_EVENT_R17_UNPROVEN": 1}
    assert screen["candidates"] == []


def test_screen_declares_realistic_headline_and_labelled_touch_shadow():
    screen = evaluate_protection_screen([
        _source(rows=_walk(3, 1000, 120), event_id="a", episode_id="ep-a", fill_price=50000.0)],
        book_walk=_book_walk(3))
    declared = screen["fill_model"]
    assert declared["fill_model"] == "REALISTIC_V1"
    assert declared["headline_role"] == "HEADLINE"
    assert (declared["shadow_world"], declared["shadow_role"]) == (
        "IDEAL_TOUCH_DIAGNOSTIC_ONLY", "COMPARISON_SHADOW_NOT_HEADLINE")
    assert declared["headline_entry_evaluator"] == rc.EVALUATOR_VERSION
    assert declared["realistic_exit_model_applied"] is False
    assert all(row["policy_spec"]["fill"]["source_fill_model"] == rc.EVALUATOR_VERSION
               for row in screen["candidates"])


def test_regime_breakdown_uses_the_observed_regime_value():
    sources = [
        _source(rows=_walk(i, 1000, 300), event_id=f"e{i}", episode_id=f"ep{i}", fill_price=50000.0,
                regime={"observed_ts": 999.0 + i, "value": "BULL" if i % 2 else "BEAR"})
        for i in range(4)
    ]
    screen = evaluate_protection_screen(sources, book_walk=_book_walk(4))
    keys = {key for row in screen["candidates"] for key in row["regime_breakdown"]}
    assert keys <= {"BULL", "BEAR"} and keys
    assert rc._regime_label({"observed_ts": 1, "value": "BULL"}) == "BULL"
    assert rc._regime_label({"observed_ts": 1, "value": None}) is None
    assert rc._regime_label("CHOP") == "CHOP"


def test_parallel_assessment_equals_in_process(monkeypatch):
    import json

    sources = [
        _source(direction="LONG" if i % 2 else "SHORT", rows=_walk(20 + i, 1000, 900), event_id=f"e{i}",
                episode_id=f"ep{i}", fill_price=50000.0, regime="BULL" if i % 3 else "CHOP")
        for i in range(6)
    ]

    tape = _book_walk(20)

    def screen(workers):
        monkeypatch.setenv(rc.ASSESS_WORKERS_ENV, str(workers))
        monkeypatch.setattr(rc, "ASSESS_PARALLEL_MIN_CELLS", 0)
        monkeypatch.setattr(rc, "STREAM_PARALLEL_MIN_EVENTS", 0)
        result = evaluate_protection_screen(sources, book_walk=tape)
        streaming = result.pop("streaming")
        return result, streaming

    serial, serial_streaming = screen(1)
    parallel, parallel_streaming = screen(2)
    assert (serial_streaming["assess_workers"], parallel_streaming["assess_workers"]) == (1, 2)
    assert (serial_streaming["stream_workers"], parallel_streaming["stream_workers"]) == (1, 2)
    assert json.dumps(parallel, sort_keys=True, default=str) == json.dumps(serial, sort_keys=True, default=str)
    assert len(serial["candidates"]) == len(protection_screen())

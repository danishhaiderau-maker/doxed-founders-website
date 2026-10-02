import math
import random

import numpy as np
import pytest

import research_v3_candidates as rc
from research_v3_candidates import evaluate_protection_screen, protection_replay_window_summary, protection_screen
from research_v3_policy_replay import replay_protected_policy
from research_v3_side_marks import (
    BASIS_1M_ADVERSE_FIRST,
    BASIS_SEGMENT,
    BASIS_SEGMENT_AND_TAPE,
    BASIS_SEGMENT_LAST_PRICE,
    BASIS_TAPE,
    PATH_HORIZON_SEC,
    SideMarkTape,
    event_side_marks,
    mark_source_summary,
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
def test_screen_replays_exits_on_the_executable_side(monkeypatch, direction):
    rows = _walk(7, 1000, PATH_HORIZON_SEC + 600, gap_every=37)
    entry = 50000.0
    screen, outcomes = _diagnostic_outcomes(monkeypatch, [_source(direction, rows, fill_price=entry)])
    assert screen["mark_source"]["events_by_basis"] == {BASIS_SEGMENT: 1}
    assert screen["mark_source"]["alert_level"] == "GREEN"
    side = "bid" if direction == "LONG" else "ask"
    path = [{"ts": r["ts"], "price": r[side]} for r in rows if 1001.0 <= r["ts"] < 1001.0 + PATH_HORIZON_SEC]
    last_price_path = [{"ts": r["ts"], "price": r["last"]} for r in rows]
    differs = 0
    for protection in protection_screen():
        policy_id = f"TAKER|{protection['protection_id']}"
        spec = {"entry": {}, "fill": {"execution_world": "IDEAL_TOUCH_DIAGNOSTIC_ONLY"}, "portfolio": {},
                "loss_protection": protection["loss_protection"], "profit_protection": protection["profit_protection"]}
        kwargs = dict(direction=direction, entry_price=entry, fill_ts=1001.0, atr_pct_at_fill=0.1,
                      leverage=100.0, margin_usd=0.25, policy_spec=spec, collect_trace=False)
        canonical = replay_protected_policy(path, **kwargs)
        (outcome,) = outcomes[policy_id]
        assert (outcome["net_pnl_usd"], outcome["exit_reason"]) == (canonical["net_pnl_usd"], canonical["exit_reason"])
        differs += replay_protected_policy(last_price_path, **kwargs)["net_pnl_usd"] != canonical["net_pnl_usd"]
    assert differs, "side-correct marks must differ from last-trade marks somewhere"


def test_fallbacks_are_labelled_and_alerted():
    price_only = [{"ts": 1000.0 + i, "price": 100.0 + 0.01 * i} for i in range(50)]
    ohlc = [{"t": 1000.0 + 60 * i, "o": 100, "h": 100.5, "l": 99.5, "c": 100.1} for i in range(5)]
    sources = [
        _source(rows=_walk(1, 1000, 120), event_id="a", episode_id="ep-a", fill_price=50000.0),
        _source(rows=price_only, event_id="b", episode_id="ep-b", fill_price=100.0),
        _source(rows=(), ohlc=ohlc, event_id="c", episode_id="ep-c", fill_price=100.0),
    ]
    screen = evaluate_protection_screen(sources)
    mark = screen["mark_source"]
    assert mark["events_by_basis"] == {BASIS_SEGMENT: 1, BASIS_SEGMENT_LAST_PRICE: 1, BASIS_1M_ADVERSE_FIRST: 1}
    assert (mark["fallback_events"], mark["alert_level"], mark["reason"]) == (2, "RED", "REPLAY_MARK_FALLBACK_MAJORITY")
    bases = {base for row in screen["candidates"] for base in row["replay_path_bases"]}
    assert bases == {BASIS_SEGMENT, BASIS_SEGMENT_LAST_PRICE, BASIS_1M_ADVERSE_FIRST}
    amber = mark_source_summary({BASIS_SEGMENT: 3, BASIS_1M_ADVERSE_FIRST: 1}, {}, None)
    assert (amber["alert_level"], amber["reason"]) == ("AMBER", "REPLAY_MARK_FALLBACK")
    window = {"schema": "protection_replay_event_window_v1", "events_eligible": 4, "events_selected": 4,
              "truncated": False, "mark_source": amber}
    summary = protection_replay_window_summary(window)
    assert (summary["alert_level"], summary["reason"], summary["mark_fallback_events"]) == (
        "AMBER", "REPLAY_MARK_FALLBACK", 1)
    window["mark_source"] = mark_source_summary({BASIS_TAPE: 4}, {}, None)
    assert protection_replay_window_summary(window)["alert_level"] == "GREEN"


def test_regime_breakdown_uses_the_observed_regime_value():
    sources = [
        _source(rows=_walk(i, 1000, 300), event_id=f"e{i}", episode_id=f"ep{i}", fill_price=50000.0,
                regime={"observed_ts": 999.0 + i, "value": "BULL" if i % 2 else "BEAR"})
        for i in range(4)
    ]
    screen = evaluate_protection_screen(sources)
    keys = {key for row in screen["candidates"] for key in row["regime_breakdown"]}
    assert keys <= {"BULL", "BEAR"} and keys
    assert rc._regime_label({"observed_ts": 1, "value": "BULL"}) == "BULL"
    assert rc._regime_label({"observed_ts": 1, "value": None}) is None
    assert rc._regime_label("CHOP") == "CHOP"


def test_engine_genome_grid_and_canonical_replay_agree_on_tape_marks(monkeypatch):
    from research import genome_grid_study as gg

    start, seconds = 10_000, PATH_HORIZON_SEC + 400
    rows = _walk(11, start, seconds, gap_every=53)
    arr = np.full((seconds, 6), np.nan)
    for row in rows:
        arr[row["bucket_ts"] - start] = (row["bid"], row["ask"], row["last"], row["last"], row["last"], 1.0)
    genome_tape = gg.Tape(start, arr[:, 0].copy(), arr[:, 1].copy(), arr[:, 2].copy(), arr[:, 3].copy(),
                          arr[:, 4].copy(), np.nan_to_num(arr[:, 5]), [])
    engine_tape = SideMarkTape.from_rows(rows, start_ts=start, end_ts=start + seconds)
    fill_idx = 3
    window = genome_tape.window(start, start + seconds)
    for direction in ("LONG", "SHORT"):
        entry = float(window["ask"][fill_idx] if direction == "LONG" else window["bid"][fill_idx])
        source = _source(direction, rows=(), fill_ts=float(start + fill_idx), fill_price=entry)
        source["signal_ts"] = float(start)
        screen, outcomes = _diagnostic_outcomes(monkeypatch, [source], mark_tape=engine_tape)
        assert screen["mark_source"]["events_by_basis"] == {BASIS_TAPE: 1}
        genome_path = gg.prepare_path(direction, entry, fill_idx, window)
        side = window["bid"] if direction == "LONG" else window["ask"]
        canonical_prices = [{"ts": float(start + i), "price": float(side[i])}
                            for i in range(fill_idx, fill_idx + PATH_HORIZON_SEC) if not math.isnan(side[i])]
        for protection in protection_screen():
            spec = {"entry": {}, "fill": {"execution_world": "IDEAL_TOUCH_DIAGNOSTIC_ONLY"}, "portfolio": {},
                    "loss_protection": protection["loss_protection"],
                    "profit_protection": protection["profit_protection"]}
            canonical = replay_protected_policy(
                canonical_prices, direction=direction, entry_price=entry, fill_ts=float(start + fill_idx),
                atr_pct_at_fill=0.1, leverage=100.0, margin_usd=0.25, policy_spec=spec, collect_trace=False)
            genome = gg.fast_replay(genome_path, spec, 0.1)
            (engine,) = outcomes[f"TAKER|{protection['protection_id']}"]
            assert (engine["net_pnl_usd"], engine["exit_reason"]) == (canonical["net_pnl_usd"], canonical["exit_reason"])
            assert genome["exit_reason"] == canonical["exit_reason"], protection["protection_id"]
            assert abs(genome["net_pnl_usd"] - canonical["net_pnl_usd"]) <= 1e-6, protection["protection_id"]

"""Lead-lag report: detects a planted lead, stays quiet on independent walks."""

import json
import os
import tempfile

import numpy as np

import cross_venue_tape as cvt
from research import lead_lag_report as llr

T0 = 1_790_812_800
HOURS = 12


def _paths(lag: int, seed: int = 7, independent: bool = False):
    rng = np.random.default_rng(seed)
    n = HOURS * 3600 + 120
    leader = 80000.0 * np.exp(np.cumsum(rng.normal(0, 1.2e-4, n)))
    if independent:
        bfx = 80010.0 * np.exp(np.cumsum(rng.normal(0, 1.2e-4, n)))
    else:
        bfx = np.empty(n)
        bfx[lag:] = leader[:-lag] + 10.0
        bfx[:lag] = leader[0] + 10.0
    return leader, bfx


def _rows_and_quotes(leader, bfx, half_spread=0.05):
    rows, quotes = [], {}
    for m in range(HOURS * 60):
        t0 = T0 + 60 * m
        secs = range(t0, t0 + 60)
        samples = [{"sec": s, "mid": float(leader[s - T0]), "last": None, "buy": 0.1, "sell": 0.0}
                   for s in secs]
        row = cvt.encode_minute(t0, {"binance": samples}, [float(bfx[s - T0]) for s in secs],
                                derivatives={"binance": {"funding_rate": 1e-4, "open_interest": 1000.0 + m}},
                                meta={"collector_version": cvt.COLLECTOR_VERSION, "cpu_pct": 1.0})
        rows.append(json.loads(json.dumps(row)))
        for s in secs:
            mid = float(bfx[s - T0])
            quotes[s] = (mid - half_spread, mid + half_spread)
    return rows, quotes


def test_report_finds_a_two_second_lead_and_a_positive_follow_markout():
    leader, bfx = _paths(lag=2)
    rows, quotes = _rows_and_quotes(leader, bfx)
    report = llr.build_lead_lag_report(rows, quotes)
    assert report["status"] == "OK" and report["mode"] == "SHADOW_ONLY_NO_ORDERS"
    venue = report["venues"]["binance"]
    assert venue["xcorr"]["peak"]["lag_s"] == 2 and venue["xcorr"]["peak"]["corr"] > 0.9
    trig = venue["triggers"]["leader_move_10s_2bp"]
    assert trig["preregistered"] is True
    curve = {c["lag_s"]: c for c in trig["response"]["bitfinex_response"]}
    assert curve[2]["mean"] > 1.0 and curve[2]["t"] > 5
    prereg = [s for s in report["follow_summary"] if s["preregistered"] and s["hold_s"] == 5]
    assert prereg and prereg[0]["verdict"] == "POSITIVE_AFTER_SPREAD"
    assert prereg[0]["ci95"][0] > 0
    assert venue["basis"]["median_bp"] < 0
    assert report["collector"]["cpu_pct_last_hour_mean"] == 1.0


def test_report_shows_no_edge_on_independent_walks():
    leader, bfx = _paths(lag=1, independent=True)
    rows, quotes = _rows_and_quotes(leader, bfx, half_spread=0.5)
    report = llr.build_lead_lag_report(rows, quotes)
    peak = report["venues"]["binance"]["xcorr"]["peak"]
    assert abs(peak["corr"]) < 0.03
    assert not any(s["verdict"] == "POSITIVE_AFTER_SPREAD" for s in report["follow_summary"])


def test_report_gates_short_spans_and_falls_back_without_a_bitfinex_tape():
    leader, bfx = _paths(lag=2)
    rows, _ = _rows_and_quotes(leader, bfx)
    report = llr.build_lead_lag_report(rows[:120])
    assert report["status"] == "NOT_ENOUGH_DATA"
    assert report["span"]["bitfinex_source"] == "CROSS_VENUE_EMBEDDED_MID"
    trig = report["venues"]["binance"]["triggers"]["leader_move_10s_2bp"]
    assert trig["follow"] == "UNAVAILABLE_NO_BITFINEX_BBO"
    assert llr.build_lead_lag_report([])["status"] == "NO_DATA"


def test_loaders_read_rotations_and_window_the_bitfinex_tape():
    leader, bfx = _paths(lag=2)
    rows, quotes = _rows_and_quotes(leader, bfx)
    tmp = tempfile.mkdtemp()
    with open(os.path.join(tmp, cvt.FILE_NAME + ".1"), "w", encoding="utf-8") as fh:
        for row in rows[:30]:
            fh.write(json.dumps(row) + "\n")
    with open(os.path.join(tmp, cvt.FILE_NAME), "w", encoding="utf-8") as fh:
        for row in rows[29:60]:
            fh.write(json.dumps(row) + "\n")
    with open(os.path.join(tmp, llr.BFX_TAPE_FILE), "w", encoding="utf-8") as fh:
        for s in range(T0 - 100, T0 + 3700):
            bid, ask = quotes.get(s, (1.0, 1.1))
            fh.write(json.dumps({"schema": "market_microstructure_1s_v1", "symbol": "tBTCF0:USTF0",
                                 "bucket_ts": s, "fresh": s % 97 != 0, "valid_bbo": True,
                                 "bid": bid, "ask": ask}) + "\n")
    loaded = llr.load_cross_venue_rows(tmp)
    assert [r["minute_ts"] for r in loaded] == [T0 + 60 * m for m in range(60)]
    q = llr.load_bitfinex_quotes(tmp, T0, T0 + 3600)
    assert min(q) >= T0 and max(q) < T0 + 3600
    assert all(s % 97 != 0 for s in q)
    report = llr.build_from_data_dir(tmp)
    assert report["span"]["minutes"] == 60 and report["span"]["bitfinex_source"] == "MICROSTRUCTURE_TAPE"


def _xvl_step_rows(hours=3, step_every=300, step_bp=15.0, lag=3, half_spread=0.5):
    n = hours * 3600 + 120
    leader = np.full(n, 80000.0)
    for j in range(200, n - 100, step_every):
        sign = 1.0 if (j // step_every) % 2 == 0 else -1.0
        leader[j:] *= 1.0 + sign * step_bp / 1e4
    bfx = np.empty(n)
    bfx[lag:] = leader[:-lag] + 10.0
    bfx[:lag] = leader[0] + 10.0
    rows, quotes = [], {}
    for m in range(hours * 60):
        t0 = T0 + 60 * m
        secs = range(t0, t0 + 60)
        samples = [{"sec": s, "mid": float(leader[s - T0]), "last": None, "buy": 0.0, "sell": 0.0}
                   for s in secs]
        row = cvt.encode_minute(t0, {"binance": samples, "bybit": samples},
                                [float(bfx[s - T0]) for s in secs],
                                meta={"collector_version": cvt.COLLECTOR_VERSION, "cpu_pct": 1.0})
        rows.append(json.loads(json.dumps(row)))
        for s in secs:
            mid = float(bfx[s - T0])
            quotes[s] = (mid - half_spread, mid + half_spread)
    return rows, quotes


def _shadow_rows_from(trades, signature):
    from cross_venue_session_follow import OUTCOME_SCHEMA, TRIGGER_SCHEMA
    triggers, outcomes = [], []
    for t in trades:
        base = {"trigger_id": f"xvs-{t['anchor']}", "anchor_bucket_ts": t["anchor"], "side": t["side"],
                "gate": "TRIGGER", "qualifies": True, "cap1_take": True, "policy_signature": signature}
        triggers.append({"schema": TRIGGER_SCHEMA, **base})
        outcomes.append({"schema": OUTCOME_SCHEMA, **base, "status": "OK",
                         "net_bp_after_spread": t["net_bp"]})
    return triggers, outcomes


XVS_LANE = "FAMILY_XVENUE_SESSION_FOLLOW_60M"


def test_xvl_section_replays_the_registered_rule_and_matches_the_shadow_stream():
    from combo_pathway_config import ACTIVE_TILE_REGISTRY
    signature = ACTIVE_TILE_REGISTRY[XVS_LANE]["policy_signature"]
    rows, quotes = _xvl_step_rows(hours=6)
    report = llr.build_lead_lag_report(rows, quotes)
    cell = report["xvl"]["lanes"][XVS_LANE]
    assert cell["rule"]["lead"]["lead_threshold_bps"] == 8.0
    assert cell["rule"]["allowed_sessions"] == ["ASIA", "EU", "US"]
    assert cell["replay"]["trades"] >= 3
    assert cell["shadow"]["triggers_logged"] == 0

    trades = llr.session_follow_replay_trades(llr.Aligned(rows, quotes), llr._xvl_rules()[XVS_LANE][0])
    assert trades and all(u["anchor"] > t["anchor"] + 3600 for t, u in zip(trades, trades[1:]))
    foreign = _shadow_rows_from(trades[:2], "other-signature")
    triggers, outcomes = _shadow_rows_from(trades, signature)
    report = llr.build_lead_lag_report(rows, quotes, xvl_rows=(triggers + foreign[0], outcomes + foreign[1]))
    cell = report["xvl"]["lanes"][XVS_LANE]
    assert cell["shadow"]["triggers_logged"] == len(trades)
    assert cell["shadow"]["by_gate"] == {"TRIGGER": len(trades)}
    assert cell["shadow"]["capacity_one"]["trades"] == len(trades)
    parity = cell["parity"]
    assert parity["match_rate"] == 1.0 and parity["side_agreement"] == 1.0
    assert parity["mean_abs_net_gap_bp"] == 0.0


def test_session_follow_replay_gates_sessions_and_conflicts():
    from dataclasses import replace
    rows, quotes = _xvl_step_rows(hours=6)
    al = llr.Aligned(rows, quotes)
    rule = llr._xvl_rules()[XVS_LANE][0]
    everywhere = llr.session_follow_replay_trades(al, rule)
    assert everywhere
    # T0 is 00:00 UTC, so a six-hour tape is entirely the Asia session.
    eu_only = llr.session_follow_replay_trades(al, replace(rule, allowed_sessions=("EU",)))
    assert eu_only == []


def test_xvl_shadow_loader_reads_rotations_and_no_data_still_reports_the_shadow():
    from cross_venue_session_follow import SHADOW_FILE
    tmp = tempfile.mkdtemp()
    triggers, outcomes = _shadow_rows_from(
        [{"anchor": T0 + 10, "side": "LONG", "net_bp": 2.0},
         {"anchor": T0 + 90, "side": "SHORT", "net_bp": -1.0}], "")
    with open(os.path.join(tmp, SHADOW_FILE + ".1"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(triggers[0]) + "\n" + json.dumps(outcomes[0]) + "\n")
    with open(os.path.join(tmp, SHADOW_FILE), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(triggers[1]) + "\nnot-json\n" + json.dumps(outcomes[1]) + "\n")
    loaded = llr.load_xvl_shadow_rows(tmp)
    assert [len(x) for x in loaded] == [2, 2]
    report = llr.build_from_data_dir(tmp)
    assert report["status"] == "NO_DATA"
    cell = report["xvl"]["lanes"][XVS_LANE]
    assert cell["shadow"]["triggers_logged"] == 2
    assert cell["shadow"]["capacity_one"]["win_rate"] == 0.5
    assert cell["replay"] == "UNAVAILABLE_NO_BITFINEX_BBO"

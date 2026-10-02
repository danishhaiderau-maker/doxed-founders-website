"""Regime shadow prompt (H8) and versioned decision feature snapshots with forward labels."""
import json
import math

import pytest

import ai_regime_shadow as regime
import decision_feature_snapshots as dfs
from ai_shadow_challengers import TapeRing


COMPACT = {"as_of_utc": "2026-10-02T00:00:00Z", "stale": False, "z15": 0.8, "z60": 1.4, "rv15_bp": 2.0,
           "trend_score": 2, "adx15m": 28.0, "donchian_loc_3m": 0.9, "flow_5m": 0.3,
           "funding_bp_8h": 1.0, "oi_change_1h_pct": 0.4, "ret_60m_bp": 40.0}
PREMIUM = {"premium_dev_bp": -1.2, "lead_60s_bp": 0.8, "lead_300s_bp": 2.1, "premium_bp": 3.0}


def test_twelve_facts_include_premium_and_lead_and_no_side_or_scores():
    facts = regime.build_regime_facts(COMPACT, PREMIUM)
    assert len(regime.FACT_KEYS) == 12
    assert {"premium_dev_bp", "lead_60s_bp", "lead_300s_bp"} <= set(regime.FACT_KEYS)
    assert facts["premium_dev_bp"] == -1.2 and facts["missing_facts"] == []
    text = " ".join(m["content"] for m in regime.render_regime_messages(facts))
    assert "60-minute" in text and "persistence" in text
    for banned in ("LONG", "SHORT", "long_score", "0-100"):
        assert banned not in text
    missing = regime.build_regime_facts(COMPACT, None)
    assert missing["missing_facts"] == ["lead_300s_bp", "lead_60s_bp", "premium_dev_bp"]


@pytest.mark.parametrize("text,status", [
    ('{"trending": true, "exhausted": false, "persistence": 0.56, "abstain": false}', "OK"),
    ('{"trending": "yes", "exhausted": false, "persistence": 0.5, "abstain": false}', "OUT_OF_RANGE_OR_MISSING"),
    ('{"trending": true, "exhausted": false, "persistence": 1.4, "abstain": false}', "OUT_OF_RANGE_OR_MISSING"),
    ("no json", "INVALID_JSON"),
])
def test_parse(text, status):
    assert regime.parse_regime_response(text)["parse_status"] == status


def test_forward_labels_definitions():
    sigma60 = 2.0 * math.sqrt(360)
    facts = {"rv15_bp": 2.0}
    assert regime.forward_labels(facts, 40.0, sigma60 + 1)["trending"] is True
    assert regime.forward_labels(facts, 40.0, 5.0) == {"trending": False, "exhausted": False, "persistence": True}
    assert regime.forward_labels(facts, 40.0, -25.0)["exhausted"] is True
    assert regime.forward_labels(facts, 40.0, None)["persistence"] is None


def _scored_row(ts, ai_persist, ai_trend, outcome_persist, outcome_trend):
    return {"decision_ts": ts, "facts": dict(COMPACT, z60=0.2),
            "parsed": {"parse_status": "OK", "abstain": False, "trending": ai_trend, "exhausted": False,
                       "persistence": ai_persist},
            "labels": {"persistence": outcome_persist, "trending": outcome_trend, "exhausted": False}}


def test_kill_verdict_collects_then_kills_a_useless_prompt():
    rows = [_scored_row(i * 900, 0.9, True, i % 2 == 0, i % 3 == 0) for i in range(400)]
    early = regime.score_calls(rows[:50], resamples=50)
    assert early["verdict"]["status"] == "COLLECTING"
    full = regime.score_calls(rows, resamples=50)
    assert full["scored_calls"] == 400 and full["span_days"] >= 4
    rows = [_scored_row(i * 3600, 0.9, True, i % 2 == 0, i % 3 == 0) for i in range(400)]
    report = regime.score_calls(rows, resamples=50)
    assert report["verdict"]["status"] == "KILL_REMOVE_PROMPT"
    assert report["labels"]["persistence"]["brier_ai"] > report["labels"]["persistence"]["brier_climatology"]


def test_kill_verdict_keeps_a_prompt_that_beats_both_baselines():
    rows = [_scored_row(i * 3600, 0.95 if i % 2 == 0 else 0.05, i % 3 == 0, i % 2 == 0, i % 3 == 0)
            for i in range(400)]
    assert regime.score_calls(rows, resamples=50)["verdict"]["status"] == "KEEP"


def test_abstains_are_counted_not_scored():
    row = _scored_row(0, 0.5, False, True, False)
    row["parsed"]["abstain"] = True
    report = regime.score_calls([row], resamples=10)
    assert report["abstained_calls"] == 1 and report["scored_calls"] == 0


def test_budget_spacing_and_cap():
    budget = regime.RegimeBudget(min_interval_sec=900, daily_cap=2)
    assert budget.acquire(1000)[0] and budget.acquire(1500) == (False, "MIN_INTERVAL")
    assert budget.acquire(2000)[0] and budget.acquire(3000) == (False, "DAILY_CAP")


def _ring(start, seconds, mid_fn):
    ring = TapeRing()
    for s in range(seconds):
        mid = mid_fn(s)
        ring.append_bucket({"bucket_ts": start + s, "fresh": True, "valid_bbo": True,
                            "bid": mid - 0.5, "ask": mid + 0.5, "bid_qty": 1, "ask_qty": 1})
    return ring


def test_labels_mature_at_every_horizon_with_excursions():
    start = 1_700_000_000
    ring = _ring(start, 7300, lambda s: 60000.0 + s * 0.01)
    book = dfs.LabelBook()
    assert book.register({"shared_ai_call_id": "c1", "decision_ts": start + 10})
    rows = book.mature(ring, start + 7300)
    assert [r["horizon_min"] for r in rows] == list(dfs.LABEL_HORIZONS_MIN)
    one, last = rows[0], rows[-1]
    assert one["tape_ok"] and one["fwd_ret_bp"] == pytest.approx(0.6 / 60000.1 * 1e4, rel=1e-3)
    assert last["max_down_bp"] == pytest.approx(0.0, abs=1e-6) and last["path_efficiency"] == pytest.approx(1.0)
    assert book.pending_count() == 0


def test_labels_wait_then_report_tape_gap():
    start = 1_700_000_000
    ring = _ring(start, 200, lambda s: 60000.0)
    book = dfs.LabelBook()
    book.register({"shared_ai_call_id": "c1", "decision_ts": start + 10})
    assert [r["horizon_min"] for r in book.mature(ring, start + 200)] == [1]
    late = book.mature(ring, start + 10 + 300 + 200)
    assert late[0]["horizon_min"] == 5 and late[0]["maturity"] == "TAPE_GAP"


def test_restart_recovery_resumes_only_unfinished_horizons():
    rows = [{"row_kind": "SNAPSHOT", "shared_ai_call_id": "a", "decision_ts": 1000.0},
            {"row_kind": "LABEL", "shared_ai_call_id": "a", "horizon_min": 1},
            {"row_kind": "SNAPSHOT", "shared_ai_call_id": "b", "decision_ts": 1000.0},
            *({"row_kind": "LABEL", "shared_ai_call_id": "b", "horizon_min": m} for m in dfs.LABEL_HORIZONS_MIN)]
    pending = dfs.pending_from_rows(rows, 2000.0)
    assert [(s["shared_ai_call_id"], d) for s, d in pending] == [("a", {1})]


def test_snapshot_is_versioned_and_never_gates_orders():
    snap = dfs.build_snapshot(
        call_id="c1", decision_ts=1.0, decision_price=60000.0,
        ai_result={"raw_direction": "LONG", "long_score": 70, "short_score": 30, "ai_committed": True,
                   "prompt_input_revision": "r2", "secret": "x"},
        tape={"ret_1m_bp": 1.0}, leader={"leader_venue": "BINANCE", "venues": {"BINANCE": {"ret_60s_bp": 2}}},
        premium=PREMIUM, compact_facts=COMPACT, regime={"call_state": "CALLED"},
        tile_toggles={"FAMILY_TREND_FADE_60": True}, meta={"epoch_id": "e", "git_rev": "g"},
    )
    assert snap["feature_set_version"] == dfs.FEATURE_SET_VERSION and snap["gates_orders"] is False
    assert snap["book_depth_collected"] is False and "secret" not in snap["ai"]
    assert snap["ai"]["ai_committed"] is True and snap["leader"]["venues"]["BINANCE"]["ret_60s_bp"] == 2
    json.dumps(snap)


def test_bot_logs_regime_and_snapshot_alongside_the_live_call(monkeypatch):
    import bot
    written = {}
    monkeypatch.setattr(bot, "_safe_append_jsonl",
                        lambda path, row, **kw: written.setdefault(path, []).append(row) or True)
    monkeypatch.setattr(bot, "call_deepseek_api_with_meta", lambda messages, **kw: (
        '{"trending": false, "exhausted": false, "persistence": 0.52, "abstain": false}', 120,
        {"served_model": "deepseek-test"}))
    monkeypatch.setattr(bot, "AI_SHADOW_COMPACT_PROMPT_ENABLED", False)
    monkeypatch.setattr(bot, "_AI_REGIME_BUDGET", regime.RegimeBudget())
    monkeypatch.setattr(bot, "_DFS_BOOK", dfs.LabelBook())
    ai_result = {"shared_ai_call_id": "call-r1", "shared_ai_call_ts": "2026-10-02T00:00:00Z",
                 "raw_direction": "SHORT", "long_score": 20, "short_score": 60}
    bot._run_ai_shadow_challengers({"price": 60000.0}, ai_result)
    regime_rows = written[bot.AI_SHADOW_REGIME_PROMPT_FILE]
    assert regime_rows[0]["call_state"] == "CALLED" and regime_rows[0]["parsed"]["parse_status"] == "OK"
    assert regime_rows[0]["served_model"] == "deepseek-test" and regime_rows[0]["gates_orders"] is False
    snap = written[bot.DECISION_FEATURE_SNAPSHOT_FILE][0]
    assert snap["shared_ai_call_id"] == "call-r1" and snap["regime"]["parsed"]["persistence"] == 0.52
    assert set(snap["tile_toggles"]) >= set(bot.research_lane_enabled_map())
    assert bot._DFS_BOOK.pending_count() == 1


def test_regime_is_skipped_when_the_hook_deadline_is_near(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "call_deepseek_api_with_meta", lambda *a, **k: pytest.fail("must not call"))
    row = bot._ai_shadow_run_regime(COMPACT, PREMIUM, "c", 1.0, timeout_sec=2.0)
    assert row["call_state"] == "SKIPPED_HOOK_DEADLINE"

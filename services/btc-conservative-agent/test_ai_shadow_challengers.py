import pytest
"""Shadow AI challenger log, compact prompt, tape features and dead-input alarm."""

import json
import os
import tempfile
import time

os.environ.setdefault("FORCE_PAPER_MODE", "1")
os.environ.setdefault("RESEARCH_DATA_COLLECTION", "1")
os.environ.setdefault("SKIP_EXCHANGE_MARKET_LOAD", "1")

import ai_shadow_challengers as shadow
import cross_venue_tape as cvt


def _row(ts, bid, ask, bid_qty=1.0, ask_qty=1.0, buy=0.0, sell=0.0, fresh=True):
    return {"bucket_ts": ts, "bid": bid, "ask": ask, "bid_qty": bid_qty, "ask_qty": ask_qty,
            "buy_qty": buy, "sell_qty": sell, "fresh": fresh, "valid_bbo": True}


def _ring(start, end, mid_fn, **kw):
    ring = shadow.TapeRing()
    for ts in range(start, end + 1):
        mid = mid_fn(ts)
        ring.append_bucket(_row(ts, mid - 0.5, mid + 0.5, **kw))
    return ring


def _ctx(**over):
    ctx = {
        "price": 100000.0,
        "trade_id": "call-1",
        "market_context": {
            "multi_tf": {"agreement": "BULL_ALIGNED", "trends": {"15m": "BULLISH", "1h": "BULLISH", "4h": "BEARISH"}},
            "ema_alignment": {"stack_bull": True, "stack_bear": False},
            "trend_strength": {"adx": 22.0},
        },
        "trend_health": {"bull_score": 3, "bear_score": 1},
        "sr_bias": "SHORT_PREFERRED",
        "cycle_3m_universe": {"atr14_pct_3m": 0.2, "cycle_bucket": 1000.0, "session_utc": "LONDON"},
        "funding": {"rate": 0.0001, "mark_price": 100010.0, "index_price": 100000.0,
                    "next_time": 4600.0, "updated_ts": 990.0, "open_interest": 5000.0},
    }
    ctx.update(over)
    return ctx


def test_tape_ring_rejects_stale_invalid_and_non_monotonic_rows():
    ring = shadow.TapeRing(10)
    assert ring.append_bucket(_row(5, 100.0, 100.5))
    assert not ring.append_bucket(_row(5, 100.0, 100.5))
    assert not ring.append_bucket(_row(4, 100.0, 100.5))
    assert not ring.append_bucket(_row(6, 100.0, 100.5, fresh=False))
    assert not ring.append_bucket(_row(7, 101.0, 100.0))
    assert len(ring) == 1


def test_merge_history_prepends_only_older_rows():
    ring = shadow.TapeRing(100)
    ring.append_bucket(_row(50, 100.0, 100.5))
    added = ring.merge_history([_row(t, 99.0, 99.5) for t in (52, 48, 49, 50)])
    ts, _ = ring.snapshot()
    assert added == 2
    assert ts == [48, 49, 50]


def test_tape_features_are_null_when_unobserved_and_signed_when_observed():
    empty = shadow.tape_features(shadow.TapeRing(), 1000.0)
    assert empty["ret_1m_bp"] is None and empty["ret_5m"] is None
    ring = _ring(0, 4000, lambda t: 100000.0 + t, buy=2.0, sell=1.0)
    feats = shadow.tape_features(ring, 4000.0)
    assert feats["ret_1m_bp"] > 0 and feats["ret_60m_bp"] > feats["ret_5m_bp"] > 0
    assert abs(feats["ret_1m"] * 1e4 - feats["ret_1m_bp"]) < 1e-3
    assert abs(feats["flow_1m"] - (1.0 / 3.0)) < 1e-3
    assert feats["tape_age_s"] == 0.0 and feats["spread_bp"] > 0


def test_challenger_sides_cover_every_control_and_respect_abstain():
    tape = {"flow_1m": -0.2, "ret_5m_bp": 12.0}
    ai = {"long_score": 70, "short_score": 40, "raw_direction": "LONG", "decision": "APPROVE"}
    out = shadow.compute_challenger_sides(_ctx(), ai, tape, "call-1")
    sides = out["sides"]
    assert set(sides) == set(shadow.CHALLENGERS)
    assert sides["llm_score_led"] == "LONG"
    assert sides["llm_abstain_respecting"] == "LONG"
    assert sides["inverted_ai"] == "SHORT"
    assert sides["ofi_1m"] == "SHORT"
    assert sides["contrarian_5m"] == "SHORT"
    assert sides["rule_vote"] == "LONG"  # +1 mtf, +1 ema, +1 health, -1 sr
    assert sides["random"] == shadow.seeded_random_side("call-1")
    assert sides["compact_v5"] == "NONE"
    narrow = shadow.compute_challenger_sides(
        _ctx(), {"long_score": 52, "short_score": 49, "raw_direction": "LONG"}, tape, "c2")
    assert narrow["sides"]["llm_score_led"] == "LONG"
    assert narrow["sides"]["llm_abstain_respecting"] == "NONE"
    assert narrow["llm"]["abstain_reason"] == "SCORE_GAP_BELOW_5"
    no_trade = shadow.compute_challenger_sides(
        _ctx(), {"long_score": 70, "short_score": 30, "raw_direction": "NO_TRADE"}, tape, "c3")
    assert no_trade["sides"]["llm_score_led"] == "LONG"
    assert no_trade["llm"]["abstain_reason"] == "NO_TRADE"


def test_seeded_random_is_deterministic_and_roughly_balanced():
    sides = [shadow.seeded_random_side(f"id-{i}") for i in range(2000)]
    assert sides == [shadow.seeded_random_side(f"id-{i}") for i in range(2000)]
    assert 900 < sides.count("LONG") < 1100






def test_compact_prompt_facts_render_and_parse():
    tape = shadow.tape_features(_ring(0, 4000, lambda t: 100000.0 + t), 4000.0)
    facts = shadow.build_compact_facts(_ctx(), tape, now_ts=1100.0, as_of_utc="2026-10-01T00:00:00Z",
                                       oi_change_1h_pct=1.5)
    assert facts["funding_bp_8h"] == 1.0
    assert facts["basis_bp"] == 1.0
    assert facts["trend_score"] == 1
    assert facts["closed_3m_age_s"] == 100.0
    messages = shadow.render_compact_messages(facts)
    assert messages[0]["role"] == "system" and "JSON" in messages[0]["content"]
    assert "funding=1.0bp/8h" in messages[1]["content"]
    ok = shadow.parse_compact_response('{"p_long_success":0.61,"p_short_success":0.40,"abstain":false,"drivers":["trend","bogus"]}')
    assert ok["parse_status"] == "OK" and ok["drivers"] == ["TREND"]
    assert shadow.compact_side(ok) == "LONG"
    gap = shadow.parse_compact_response('{"p_long_success":0.42,"p_short_success":0.38,"abstain":false}')
    assert shadow.compact_side(gap) == "LONG"
    short = shadow.parse_compact_response('{"p_long_success":0.36,"p_short_success":0.44,"abstain":false}')
    assert shadow.compact_side(short) == "SHORT"
    weak = shadow.parse_compact_response('{"p_long_success":0.52,"p_short_success":0.50,"abstain":false}')
    assert shadow.compact_side(weak) == "NONE"
    summary = shadow.compact_summary(gap)
    assert summary["side_rule"] == shadow.COMPACT_SIDE_RULE and summary["side"] == "LONG"
    assert summary["gap"] == pytest.approx(0.04)
    assert shadow.parse_compact_response("not json")["parse_status"] == "INVALID_JSON"
    assert shadow.parse_compact_response('{"p_long_success":1.4,"p_short_success":0.2,"abstain":false}')["parse_status"] == "OUT_OF_RANGE_OR_MISSING"
    abstain = shadow.parse_compact_response('{"p_long_success":0.7,"p_short_success":0.3,"abstain":true}')
    assert shadow.compact_side(abstain) == "NONE"


def test_compact_budget_enforces_spacing_and_daily_cap():
    budget = shadow.CompactPromptBudget(min_interval_sec=150, daily_cap=2)
    assert budget.acquire(1000.0) == (True, None)
    assert budget.acquire(1100.0) == (False, "MIN_INTERVAL")
    assert budget.acquire(1200.0) == (True, None)
    assert budget.acquire(1400.0) == (False, "DAILY_CAP")
    assert budget.acquire(86400.0 + 10)[0] is True


def test_open_interest_change_needs_an_hour_of_history():
    oi = shadow.OpenInterestHistory()
    assert oi.observe(0.0, 1000.0) is None
    assert oi.observe(3600.0, 1010.0) == 1.0
    assert oi.observe(3700.0, None) is None


def test_dead_input_detector_flags_null_and_constant_critical_fields():
    detector = shadow.DeadInputDetector(threshold_calls=3)
    for i in range(3):
        report = detector.observe({"raw": {"ret_1m_bp": 0, "stoch_rsi_k_3m": None, "price": 100.0 + i},
                                   "schema": "x"})
    dead = {d["path"]: d["kind"] for d in report["dead_fields"]}
    assert report["status"] == "DEAD_INPUT"
    assert dead == {"raw.ret_1m_bp": "CONSTANT", "raw.stoch_rsi_k_3m": "NULL"}
    healthy = shadow.dead_input_report(
        [{"raw": {"ret_1m_bp": i, "stoch_rsi_k_3m": 50.0 + i}} for i in range(5)], threshold_calls=3)
    assert healthy["status"] == "OK" and healthy["dead_fields"] == []


# ---------------------------------------------------------------------------
# Analyzer report, view and alarm
# ---------------------------------------------------------------------------
from research import ai_challenger_report as report_mod  # noqa: E402
from research import ai_challenger_view as view_mod  # noqa: E402
from research import decision_view  # noqa: E402


def _journal(n_calls=240, edge_bp=4.0, prompt_id="p1"):
    """LLM side is right with a small edge; random is independent of the move."""
    import random as _random
    rng = _random.Random(7)
    rows = []
    for i in range(n_calls):
        call_id = f"c{i}"
        ts = 1_700_000_000 + i * 180
        move = rng.gauss(0.0, 10.0)
        llm = "LONG" if rng.random() < 0.5 else "SHORT"
        move += edge_bp if llm == "LONG" else -edge_bp
        sides = {name: shadow.NONE for name in shadow.CHALLENGERS}
        sides.update({"llm_score_led": llm, "inverted_ai": "SHORT" if llm == "LONG" else "LONG",
                      "rule_vote": llm, "random": shadow.seeded_random_side(call_id)})
        rows.append({"row_kind": "CALL", "shared_ai_call_id": call_id, "decision_ts": ts,
                     "decision_utc": str(ts), "prompt_id": prompt_id, "sides": sides,
                     "llm": {"raw_direction": "NO_TRADE" if i % 10 == 0 else llm, "abstained": False},
                     "win_prob_status": "NOT_REQUESTED_BY_PROMPT",
                     "prompt_payload": {"raw": {"ret_1m_bp": move, "stoch_rsi_k_3m": None}}})
        for h in shadow.MARKOUT_HORIZONS_SEC:
            rows.append({"row_kind": "MARKOUT", "shared_ai_call_id": call_id, "horizon_sec": h,
                         "tape_ok": True, "mid_ret_bp": move, "spread_in_bp": 0.2, "spread_out_bp": 0.2})
        rows.append({"row_kind": "GEOMETRY", "shared_ai_call_id": call_id, "results": [
            {"lane": "tile_a", "side": "LONG", "result": "TARGET" if move > 0 else "STOP", "result_bp": move},
            {"lane": "tile_a", "side": "SHORT", "result": "TARGET" if move < 0 else "STOP", "result_bp": -move},
            {"lane": shadow.COMPACT_QUESTION_LANE, "side": "LONG", "result": "TARGET" if move > 0 else "STOP"},
            {"lane": shadow.COMPACT_QUESTION_LANE, "side": "SHORT", "result": "NO_FILL"},
        ]})
    compact = [{"shared_ai_call_id": f"c{i}", "call_state": "CALLED",
                "parsed": {"parse_status": "OK", "p_long_success": 0.5, "p_short_success": 0.5}}
               for i in range(n_calls)]
    return rows, compact


def test_stats_helpers_match_reference_values():
    assert abs(report_mod.t_two_sided_p(2.0, 10) - 0.07339) < 1e-4
    assert abs(report_mod.t_two_sided_p(1.96, 100000) - 0.05) < 1e-3
    assert report_mod.benjamini_hochberg([0.01, 0.04, 0.03, 0.2]) == [0.04, 0.16 / 3, 0.16 / 3, 0.2]
    stat = report_mod.cluster_test([1.0, 1.0, 3.0, 3.0], [1, 1, 2, 2])
    assert stat["mean"] == 2.0 and stat["clusters"] == 2 and stat["df"] == 1


def test_ai_challenger_report_is_retired_empty_because():
    """R22/HM: ai_shadow_challengers stream retired — report never analyses rows."""
    rows, compact = _journal()
    rep = report_mod.build_ai_challenger_report(rows, compact, dead_input_threshold=5)
    assert rep.get("EMPTY_BECAUSE") == "R17_RETIRED" or rep.get("status") == "RETIRED_R17"
    assert rep.get("rows_read", 0) == 0
    page = view_mod.render_ai_challenger_html(
        rep, evidence={"status": "CURRENT_GENERATION", "generated_at_display": "now"},
        nav_links=(("Decision", "/decision"),))
    assert "AI vs Challengers" in page



import bot  # noqa: E402  # heavy import deferred until bot-level asserts


def test_prompt_version_is_bumped_and_dead_inputs_are_filled():
    assert bot.SHARED_DIRECTION_PROMPT_ID == "shared_direction_conflict_abstain_v4_1_20261001"
    ctx = _ctx()
    ctx["exhaustion_3m"] = {"stoch_rsi_k": 12.5, "stoch_rsi_d": 20.0, "cycle_bucket": 1000.0}
    ctx["cycle_3m_universe"] = ctx["exhaustion_3m"]
    ctx["tape_features"] = shadow.tape_features(_ring(0, 4000, lambda t: 100000.0 + t), 4000.0)
    compact = bot.build_shared_direction_prompt_context(ctx)
    raw = compact["raw"]
    assert compact["schema"] == "shared_direction_prompt_v4_1"
    assert raw["ret_1m_bp"] and raw["ret_5m_bp"] and raw["ret_15m_bp"]
    assert raw["stoch_rsi_k_3m"] == 12.5 and raw["stoch_rsi_d_3m"] == 20.0
    assert raw["closed_3m_ts"] == 1000.0
    deriv = compact["derivatives"]
    assert deriv["funding_bp_8h"] == 1.0 and deriv["basis_bp"] == 1.0
    assert deriv["open_interest"] == 5000.0
    assert "exhaustion_3m block" not in bot.RESEARCH_AI_PROMPT_ADDENDUM


def test_win_prob_status_is_truthful():
    parsed = bot.parse_ai_response_fields('{"direction":"LONG","long_score":70,"short_score":40}')
    assert parsed["win_prob"] == 0
    assert parsed["win_prob_status"] == "NOT_REQUESTED_BY_PROMPT"
    emitted = bot.parse_ai_response_fields(
        'Win probability: 62\n{"direction":"LONG","long_score":70,"short_score":40}')
    assert emitted["win_prob_status"] == "EMITTED"


def test_shadow_purpose_is_allowed_and_request_is_bounded():
    assert "trading_direction_shadow" in bot.TRADING_AI_ALLOWED_PURPOSES
    captured = {}

    class _Resp:
        status_code = 200
        text = __import__("json").dumps(
            {"choices": [{"message": {"content": '{"p_long_success":0.5}'}}],
             "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    def fake_post(url, headers=None, json=None, timeout=None, stream=False):
        captured.update(json=json, timeout=timeout, stream=stream)
        return _Resp()

    original_post, original_usage = bot.requests.post, bot._report_showcase_inference_usage
    original_key = os.environ.get("DEEPSEEK_API_KEY")
    try:
        os.environ["DEEPSEEK_API_KEY"] = "test-key"
        bot.requests.post = fake_post
        bot._report_showcase_inference_usage = lambda *a, **k: None
        bot.call_deepseek_api([{"role": "user", "content": "x"}], temperature=0.0,
                              purpose="trading_direction_shadow", max_tokens=120,
                              response_format={"type": "json_object"}, timeout=20)
        assert captured["json"]["max_tokens"] == 120
        assert captured["json"]["response_format"] == {"type": "json_object"}
        assert captured["json"]["temperature"] == 0.0
        assert captured["stream"] is True
        assert captured["timeout"][1] == 20
        bot.call_deepseek_api([{"role": "user", "content": "x"}], purpose="trading_direction")
        assert "max_tokens" not in captured["json"] and "response_format" not in captured["json"]
    finally:
        bot.requests.post = original_post
        bot._report_showcase_inference_usage = original_usage
        if original_key is None:
            os.environ.pop("DEEPSEEK_API_KEY", None)
        else:
            os.environ["DEEPSEEK_API_KEY"] = original_key


def test_challenger_hook_no_longer_writes_challenger_stream():
    """R22: ai_shadow_challengers.jsonl writer deleted; compact/regime/DFS may still log."""
    tmp = tempfile.mkdtemp()
    names = ("AI_SHADOW_CHALLENGER_FILE", "AI_SHADOW_COMPACT_PROMPT_FILE",
             "AI_SHADOW_REGIME_PROMPT_FILE", "DECISION_FEATURE_SNAPSHOT_FILE",
             "RESEARCH_SESSION_FILE")
    originals = {n: getattr(bot, n) for n in names}
    original_call = bot.call_deepseek_api
    original_budget = bot._AI_SHADOW_BUDGET
    original_regime_budget = bot._AI_REGIME_BUDGET
    original_dfs_book = bot._DFS_BOOK
    original_live = bot._cross_venue_live
    calls = []
    anchor = int(bot._ai_shadow_decision_ts({"shared_ai_call_ts": "2026-10-01T00:00:00+00:00"})) - 1
    falling = [8e4 * (1 - 0.0003 * max(0, s - (anchor - 10)) / 10) for s in range(anchor - 60, anchor + 1)]
    live = {
        "schema": cvt.LIVE_SCHEMA, "history_start_ts": anchor - 60, "mids": {"binance": falling},
        "venues": {"binance": {"connected": True}},
        "derivatives": {"binance": {"funding_rate": 0.0001, "mark": 8e4}},
    }

    def fake_call(messages, temperature=0.4, *, purpose, **kw):
        calls.append((purpose, temperature, kw))
        return '{"p_long_success":0.64,"p_short_success":0.36,"abstain":false,"drivers":["TREND"]}', 12

    def fake_call_with_meta(messages, temperature=0.4, *, purpose, **kw):
        text, latency = fake_call(messages, temperature, purpose=purpose, **kw)
        return text, latency, {"requested_model": "deepseek-flash",
                               "served_model": "deepseek-flash", "system_fingerprint": "fp"}

    original_call_with_meta = bot.call_deepseek_api_with_meta
    with bot.trade_lock:
        orders_before = len(bot.pending_orders)
    try:
        for n in names:
            setattr(bot, n, os.path.join(tmp, originals[n]))
        bot.call_deepseek_api = fake_call
        bot.call_deepseek_api_with_meta = fake_call_with_meta
        bot._AI_SHADOW_BUDGET = shadow.CompactPromptBudget(0, 10)
        bot._AI_REGIME_BUDGET = bot._ai_regime.RegimeBudget()
        bot._DFS_BOOK = bot._dfs.LabelBook()
        bot._cross_venue_live = lambda *a, **k: live
        ctx = _ctx(tape_features=shadow.tape_features(_ring(0, 400, lambda t: 1e5 + t), 400.0))
        ai = {"shared_ai_call_id": "hook-1", "long_score": 70, "short_score": 40,
              "raw_direction": "LONG", "decision": "APPROVE",
              "shared_ai_call_ts": "2026-10-01T00:00:00+00:00",
              "win_prob": 0, "win_prob_status": "NOT_REQUESTED_BY_PROMPT"}
        bot._run_ai_shadow_challengers(ctx, ai)
        # R22: challenger stream must not be written
        assert not os.path.exists(bot.AI_SHADOW_CHALLENGER_FILE) or os.path.getsize(bot.AI_SHADOW_CHALLENGER_FILE) == 0
        with open(bot.AI_SHADOW_COMPACT_PROMPT_FILE, encoding="utf-8") as fh:
            compact_rows = [json.loads(line) for line in fh if line.strip()]
        assert compact_rows[0]["call_state"] == "CALLED"
        assert bot._DFS_BOOK.pending_count() == 1
        with bot.trade_lock:
            assert len(bot.pending_orders) == orders_before
        snap = bot.ai_shadow_dashboard_snapshot()
        assert snap["mode"] == "SHADOW_ONLY_NO_ORDERS"
        assert "pending_calls" not in snap
        assert "book" not in snap
    finally:
        for n in names:
            setattr(bot, n, originals[n])
        bot.call_deepseek_api = original_call
        bot.call_deepseek_api_with_meta = original_call_with_meta
        bot._AI_SHADOW_BUDGET = original_budget
        bot._AI_REGIME_BUDGET = original_regime_budget
        bot._DFS_BOOK = original_dfs_book
        bot._cross_venue_live = original_live



def test_hook_registered_and_files_are_wipe_and_serialization_scoped():
    import inspect
    # Hook name may remain for compact/regime/DFS; challenger stream writer is gone.
    assert "ai_shadow_challengers" in inspect.getsource(bot._run_post_ai_evidence_hook)
    wipe = bot.research_wipe_file_paths()
    assert bot.AI_SHADOW_CHALLENGER_FILE in wipe and bot.AI_SHADOW_COMPACT_PROMPT_FILE in wipe
    assert bot.AI_SHADOW_REGIME_PROMPT_FILE in wipe and bot.DECISION_FEATURE_SNAPSHOT_FILE in wipe
    # Wipe-list basename constant may remain; writer must not append to it.
    assert "AI_SHADOW_CHALLENGER_FILE" in bot._JSONL_SERIALIZED_APPEND_CONSTANTS
    assert {"AI_SHADOW_REGIME_PROMPT_FILE", "DECISION_FEATURE_SNAPSHOT_FILE"} <= set(
        bot._JSONL_SERIALIZED_APPEND_CONSTANTS)
    health = bot.ai_input_health_snapshot()
    assert health["schema"] == shadow.INPUT_HEALTH_SCHEMA
    assert health["prompt_id"] == bot.SHARED_DIRECTION_PROMPT_ID
    src = inspect.getsource(bot._run_ai_shadow_challengers)
    assert "writer deleted" in src or "R22" in src


@pytest.mark.parametrize("p_long,p_short,side", [
    # Most frequent live pairs (mirror, 425 compact calls to 2026-10-02): p_long is
    # pinned at 0.42, so only p_short moves; the relative rule must still fire.
    (0.42, 0.38, "LONG"),
    (0.42, 0.44, "NONE"),
    (0.42, 0.40, "NONE"),
    (0.42, 0.48, "SHORT"),
    (0.52, 0.48, "LONG"),
    (0.42, 0.55, "SHORT"),
])
def test_compact_side_rule_on_observed_live_probability_pairs(p_long, p_short, side):
    parsed = {"parse_status": "OK", "abstain": False, "p_long_success": p_long, "p_short_success": p_short}
    assert shadow.compact_side(parsed) == side
    assert shadow.compact_summary(parsed)["side_rule"] == "relative_gap_0.04_v2"


def test_compact_side_rule_never_trades_abstain_or_invalid():
    base = {"parse_status": "OK", "abstain": False, "p_long_success": 0.30, "p_short_success": 0.60}
    assert shadow.compact_side({**base, "abstain": True}) == "NONE"
    assert shadow.compact_side({**base, "parse_status": "OUT_OF_RANGE_OR_MISSING"}) == "NONE"
    assert shadow.compact_side(None) == "NONE"

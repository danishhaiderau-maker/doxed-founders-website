"""#807/#809 measurement fields (exit trigger vs book, tape age; entry quote vs book) + the fill-isolation contract."""
import ast
import copy
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
import live_vs_paper as lvp  # noqa: E402
import trigger_measure as tm  # noqa: E402

BOOK = {"b": [[83143, 0.0355], [83137, 0.03]], "a": [[83156, 0.08], [83160, 0.03]], "s": 1792395274}


def test_long_uses_best_bid_and_positive_means_book_better_than_trigger():
    # gs1-0f552a03a035 (19 Oct 18:34:37 AEDT): fired on a stale ticker bid 83019 while the book bid was 83143
    assert tm.trigger_vs_book_bp("LONG", 83019.0, BOOK) == round((83143 / 83019 - 1) * 1e4, 3)
    assert tm.trigger_vs_book_bp("LONG", 83019.0, BOOK) > 14.9


def test_short_uses_best_ask_and_sign_flips():
    # SHORT: trigger ask 83200 vs book ask 83156 -> buying back is cheaper on the book -> positive
    assert tm.trigger_vs_book_bp("SHORT", 83200.0, BOOK) == round(-(83156 / 83200 - 1) * 1e4, 3)
    assert tm.trigger_vs_book_bp("SHORT", 83100.0, BOOK) < 0


def test_missing_or_bad_inputs_are_none_never_raise():
    for args in (("FLAT", 1.0, BOOK), ("LONG", None, BOOK), ("LONG", 0, BOOK), ("LONG", "x", BOOK),
                 ("LONG", 83000.0, None), ("LONG", 83000.0, {"b": []}), ("LONG", 83000.0, {"a": [[1, 1]]}),
                 ("LONG", float("nan"), BOOK)):
        assert tm.trigger_vs_book_bp(*args) is None


def test_tape_age():
    assert tm.trigger_tape_age_s(1792395275.306, 1792395273.5) == 1.806
    assert tm.trigger_tape_age_s(100.0, 101.0) is None         # BBO newer than the trigger clock: never clamped to 0
    assert tm.age_s(100.0, 101.0) == (None, tm.NEGATIVE_AGE)
    assert tm.trigger_tape_age_s(None, 1.0) is None and tm.trigger_tape_age_s(1.0, None) is None
    assert tm.trigger_tape_age_s(1.0, 0) is None


def test_fields_shape():
    out = tm.fields(side="LONG", trigger_price=83019.0, trigger_ts=10.0, bbo_ts=7.5, book_signal=BOOK)
    assert set(out) == set(tm.EXIT_KEYS) and out["trigger_tape_age_s"] == 2.5
    # BOOK "s" = 1792395274 vs trigger_ts 10 -> negative book age -> bp null with a reason, never clamped
    assert out["trigger_vs_book_bp"] is None and out["trigger_vs_book_reason"] == "BOOK_NEGATIVE_AGE"
    fresh = tm.fields(side="LONG", trigger_price=83019.0, trigger_ts=BOOK["s"] + 1.5, bbo_ts=BOOK["s"],
                      book_signal=BOOK)
    assert fresh["trigger_book_age_s"] == 1.5 and fresh["trigger_vs_book_bp"] > 14.9
    assert fresh["trigger_vs_book_reason"] is None
    stale = tm.fields(side="LONG", trigger_price=83019.0, trigger_ts=BOOK["s"] + 2.5, bbo_ts=BOOK["s"],
                      book_signal=BOOK)
    assert stale["trigger_book_age_s"] == 2.5 and stale["trigger_vs_book_bp"] is None
    assert stale["trigger_vs_book_reason"] == "BOOK_STALE_GT_2S"
    neg = tm.fields(side="LONG", trigger_price=83019.0, trigger_ts=10.0, bbo_ts=11.0, book_signal=None)
    assert neg["trigger_tape_age_s"] is None and neg["trigger_tape_age_flag"] == tm.NEGATIVE_AGE
    assert neg["trigger_vs_book_reason"] == "NO_BOOK_SNAPSHOT"


def test_live_vs_paper_pair_carries_paper_trigger_fields():
    twin = {"side": "LONG", "entry_price": 83166.0, "exit_price": 83142.98, "exit_ts": 5.0,
            "trigger_vs_book_bp": 14.936, "trigger_tape_age_s": 1.806}
    row = lvp.pair_row("gs1-x", {}, twin)
    assert row["paper_trigger_vs_book_bp"] == 14.936 and row["paper_trigger_tape_age_s"] == 1.806
    bare = lvp.pair_row("gs1-y", {}, {"side": "LONG"})
    assert bare["paper_trigger_vs_book_bp"] is None and bare["paper_trigger_tape_age_s"] is None


def test_bot_wiring_present():
    src = open(os.path.join(os.path.dirname(__file__), "bot.py"), encoding="utf-8").read()
    # RK: the trigger age is stamped from the order-book row (complete at s + 1), never the ticker bbo_ts
    assert 'pos["exit_trigger_bbo_ts"] = (float(book["s"]) + 1.0) if book and book.get("s") is not None else None' in src
    assert 'state.get("bbo_ts")' not in _bot_function(src, "_stamp_trigger_basis")
    assert "_stamp_trigger_book_age(pos, book)" in _bot_function(src, "_stamp_trigger_basis")
    assert "state" not in _bot_function(src, "_stamp_trigger_book_age").split('"""')[-1]
    assert '"exit_trigger_bbo_ts": _ts_or_none(pos.get("exit_trigger_bbo_ts"))' in src
    assert src.count("**_twin_trigger_fields(row)") == 2
    # #809: the decision recorder stamps only the cheap quote (no book read) from values read under state_lock
    assert src.count("_record_adaptive_entry_decision(lane, adaptive, quote_ctx=quote_ctx)") == 1
    assert src.count("_record_adaptive_entry_decision(lane, decision, quote_ctx=quote_ctx)") == 1
    fn = _bot_function(src, "_record_adaptive_entry_decision")
    for banned in ("book_at", "BookTail", "book10l", "paper_fill", "open(", "requests", "urlopen"):
        assert banned not in fn, banned


# ------------------------------------------------------------------ #809 entry side
T0 = 1792395274.0
ENTRY_BOOK = {"b": [[83140.0, 0.2]], "a": [[83150.0, 0.3]], "s": T0 - 1.0}


def _decision(**kw):
    d = {"schema": "x", "action": "TAKER", "direction": "LONG", "signal_ts": T0, "bid": 83160.0, "ask": 83170.0,
         "bbo_age_sec": 0.4, "limit_price": 83178.3, "exit_profile": "FADE", "atr_bp": 4.0}
    d.update(kw)
    return d


def test_quote_source_ws_rest_unknown():
    assert tm.quote_source(5.0, 5.0, 3.0) == "WS"
    assert tm.quote_source(3.0, 5.0, 3.0) == "REST"
    assert tm.quote_source(4.0, 5.0, 3.0) == "UNKNOWN" and tm.quote_source(None, 5.0, 3.0) == "UNKNOWN"


def test_entry_quote_stamp_is_cheap_quote_only():
    q = tm.entry_quote_fields(_decision(), bbo_ts=T0 - 0.4, ws_last_tick=T0 - 0.4, rest_last_tick=T0 - 9,
                              recorded_ts=T0 + 0.01)
    assert set(q) == set(tm.ENTRY_QUOTE_KEYS)
    assert (q["entry_quote_bid"], q["entry_quote_ask"], q["entry_quote_age_s"]) == (83160.0, 83170.0, 0.4)
    assert q["entry_quote_source"] == "WS" and q["entry_decision_signal_ts"] == T0
    assert q["entry_decision_recorded_ts"] == T0 + 0.01 and q["entry_quote_age_flag"] is None
    neg = tm.entry_quote_fields(_decision(bbo_age_sec=-0.3))
    assert neg["entry_quote_age_s"] is None and neg["entry_quote_age_flag"] == tm.NEGATIVE_AGE
    derived = tm.entry_quote_fields(_decision(bbo_age_sec=None), bbo_ts=T0 + 1.0)
    assert derived["entry_quote_age_s"] is None and derived["entry_quote_age_flag"] == tm.NEGATIVE_AGE


def test_entry_long_and_short_sign():
    long_ = tm.entry_fields("LONG", _decision(), ENTRY_BOOK, T0)
    # LONG: quote ask 83170 vs book ask 83150 -> the book was 2.405 bp cheaper (book better for the position = +)
    assert long_["entry_trigger_vs_book_bp"] == round((83170 - 83150) / 83170 * 1e4, 3) > 0
    assert long_["entry_book_best_bid"] == 83140.0 and long_["entry_book_best_ask"] == 83150.0
    assert long_["entry_book_age_s"] == 1.0 and long_["entry_pricer_signal_ts"] == T0
    short = tm.entry_fields("SHORT", _decision(direction="SHORT"), ENTRY_BOOK, T0)
    # SHORT: book bid 83140 vs quote bid 83160 -> selling into the book was worse -> negative
    assert short["entry_trigger_vs_book_bp"] == round((83140 - 83160) / 83160 * 1e4, 3) < 0
    assert set(long_) == set(tm.ENTRY_QUOTE_KEYS + tm.ENTRY_BOOK_KEYS)


def test_entry_book_stale_negative_and_missing():
    stale = tm.entry_fields("LONG", _decision(), {**ENTRY_BOOK, "s": T0 - 2.5}, T0)
    assert stale["entry_book_age_s"] == 2.5 and stale["entry_trigger_vs_book_bp"] is None
    assert stale["entry_trigger_vs_book_reason"] == "BOOK_STALE_GT_2S"
    neg = tm.entry_fields("LONG", _decision(), {**ENTRY_BOOK, "s": T0 + 1.0}, T0)
    assert neg["entry_book_age_s"] is None and neg["entry_book_age_flag"] == tm.NEGATIVE_AGE
    assert neg["entry_trigger_vs_book_bp"] is None and neg["entry_trigger_vs_book_reason"] == "BOOK_NEGATIVE_AGE"
    nobook = tm.entry_fields("LONG", _decision(), None, None)
    assert nobook["entry_trigger_vs_book_reason"] == "NO_BOOK_SNAPSHOT" and nobook["entry_quote_ask"] == 83170.0
    nodec = tm.entry_fields("LONG", {}, ENTRY_BOOK, T0)
    assert nodec["entry_measure_reason"] == "NO_DECISION_RECORD" and nodec["entry_trigger_vs_book_bp"] is None
    assert nodec["entry_book_best_ask"] == 83150.0 and nodec["entry_quote_bid"] is None


def test_live_limit_price_sent_only_for_limit_orders():
    assert tm.live_limit_price_sent({"order_type": "EXCHANGE LIMIT", "order_price": 83170.5}) == 83170.5
    assert tm.live_limit_price_sent({"order_type": "LIMIT", "order_price": 83170.5}) == 83170.5
    assert tm.live_limit_price_sent({"order_type": "MARKET", "order_price": 83170.5}) is None
    assert tm.live_limit_price_sent({"order_type": "STOP LIMIT", "order_price": 83170.5}) is None
    assert tm.live_limit_price_sent(None) is None


def test_pair_row_carries_entry_measure_and_live_limit_sent():
    twin = {"side": "LONG", "paper_entry_quote_ask": 83170.0, "paper_entry_book_best_ask": 83150.0,
            "paper_entry_trigger_vs_book_bp": 2.405, "paper_entry_quote_source": "WS"}
    chain = {"side": "LONG", "order_type": "LIMIT", "order_price": 83171.0}
    row = lvp.pair_row("gs1-x", chain, twin)
    assert row["paper_entry_trigger_vs_book_bp"] == 2.405 and row["paper_entry_quote_source"] == "WS"
    assert row["paper_entry_book_best_ask"] == 83150.0 and row["live_limit_price_sent"] == 83171.0
    bare = lvp.pair_row("gs1-y", {}, {"side": "LONG"})
    assert bare["paper_entry_trigger_vs_book_bp"] is None and bare["live_limit_price_sent"] is None


# ------------------------------------------------------------------ MANDATORY: fill isolation
HERE = os.path.dirname(os.path.abspath(__file__))
# Modules allowed to name the measurement keys: the helper itself and the two read-only display builders.
_ALLOWED = {"trigger_measure.py", "monitor_tiles.py", "live_vs_paper.py"}


def _bot_function(src, name):
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(src, node)
    raise AssertionError(name)


def _string_constants(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    return {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def test_no_runtime_module_names_a_measurement_key():
    """No non-test module except the helper and the read-only display builders may name any measurement key, so no
    pricer, policy, binding, executor or pnl path can read one (dynamic f"paper_{k}" keys live only in
    live_vs_paper/bot._twin_trigger_fields, which build display rows)."""
    keys = set(tm.MEASUREMENT_KEYS) | {f"paper_{k}" for k in tm.ENTRY_QUOTE_KEYS + tm.ENTRY_BOOK_KEYS}
    offenders = {}
    for name in sorted(os.listdir(HERE)):
        if not name.endswith(".py") or name.startswith("test_") or name in _ALLOWED:
            continue
        try:
            hit = keys & _string_constants(os.path.join(HERE, name))
        except SyntaxError:
            continue
        if hit:
            offenders[name] = sorted(hit)
    assert offenders == {}


def test_trigger_measure_used_only_by_measurement_sites():
    src = open(os.path.join(HERE, "bot.py"), encoding="utf-8").read()
    allowed = {"_record_adaptive_entry_decision", "_twin_trigger_fields"}
    tree = ast.parse(src)
    users = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seg = ast.get_source_segment(src, node) or ""
            if "trigger_measure." in seg and not any(
                    isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)) and c is not node
                    and "trigger_measure." in (ast.get_source_segment(src, c) or "") for c in ast.walk(node)):
                users.add(node.name)
    assert users == allowed
    pf = open(os.path.join(HERE, "paper_fill.py"), encoding="utf-8").read()
    assert "trigger_measure" not in pf and "entry_quote" not in pf and "trigger_vs_book" not in pf


# HM C6: the ONLY top-level names of paper_fill.py the realism-v2 stack may change vs master (explicit allowlist; no
# keyword guard). Anything else that differs fails - #809's measurement work leaves the pricer untouched.
PAPER_FILL_CHANGE_ALLOWLIST = frozenset({
    # REALISM_V2 PR2: exchange-resident stop leg (delay 0) + forced first-row leg (RK 17:18 (2)/(4))
    "RESIDENT_LEG", "FORCED_LEG", "FIRST_ROW_LEGS", "FIRST_ROW_HORIZON_S", "ROW_RULE_FIRST_AFTER", "ROW_RULE_LATEST",
    "first_row_at_or_after", "_paper_fill_core", "paper_fill",
    # HM C7: depth-short resident walk -> UNVERIFIED
    "DEPTH_SHORT_PARTIAL",
    # F1 delay pool (live acks, three legs, default until n10, MAX until n20, then q0.90)
    "DelaySampler", "DELAY_LEGS", "PRICED_DELAY_LEGS", "MIN_POOL_N", "QUANTILE_SWITCH_N", "POOL_SCHEMA",
    "UNVERIFIED_DELAY", "delay_stamp", "quantile_method_of", "quantile_nearest_rank",
    # tile-fill site registry (DISLOC row left as is)
    "TILE_FILL_SITES",
    # #845 base (feat/r17-dynamic-exclusion): r17_exclusion fail-closed gate in the fresh-eligibility check
    "row_fresh_eligible",
    # REALISM_V2 F5: settlement-exact funding source (bfx_deriv_status_1s, read-only; no fee term)
    "DERIV_FILE", "DERIV_WINDOW_S", "DerivStatusFiles", "_num_or_none", "_settlement_from_rows", "_DERIV_FILES",
    "deriv_settlement", "DERIV_MAX_AGE_S",  # HM #849 item 5: deriv row staleness bound
    # RK/HM #851 + HM 01:43 blocker 2: measured per-row book receive time (no look-ahead proof), one shared helper
    "RECEIVE_TS_MEASURED", "RECEIVE_TS_INFERRED", "RECEIVE_TS_MISSING", "BOOK_RECEIVE_TS_MISSING",
    "BOOK_RECEIVE_AFTER_FILL", "book_receive_proof", "BookTail",
})


def _top_level_defs(src: str) -> dict:
    import ast
    out = {}
    for n in ast.parse(src).body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out[n.name] = ast.dump(n)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            for tg in (n.targets if isinstance(n, ast.Assign) else [n.target]):
                if isinstance(tg, ast.Name):
                    out[tg.id] = ast.dump(n)
        else:
            out["<module>"] = out.get("<module>", "") + ast.dump(n)
    return out


def test_paper_fill_unchanged_vs_master():
    try:
        out = subprocess.run(["git", "show", "origin/master:./paper_fill.py"], cwd=HERE, capture_output=True,
                             text=True, timeout=30)
    except Exception:  # noqa: BLE001
        pytest.skip("git unavailable")
    if out.returncode != 0 or not out.stdout:
        pytest.skip("origin/master not available in this checkout")
    with open(os.path.join(HERE, "paper_fill.py")) as fh:
        mine = _top_level_defs(fh.read())
    master = _top_level_defs(out.stdout)
    changed = {k for k in set(mine) | set(master) if mine.get(k) != master.get(k)}
    assert not (changed - PAPER_FILL_CHANGE_ALLOWLIST), (
        f"paper_fill.py names changed outside the allowlist: {sorted(changed - PAPER_FILL_CHANGE_ALLOWLIST)}")


def test_paper_fill_allowlist_guard_rejects_unlisted_change():
    src = "def paper_fill():\n    return 1\n\ndef leg_slippage():\n    return 1\n"
    a, b = _top_level_defs(src), _top_level_defs(src.replace("return 1\n\ndef leg", "return 1\n\ndef leg").replace(
        "def leg_slippage():\n    return 1", "def leg_slippage():\n    return 2"))
    changed = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    assert changed == {"leg_slippage"} and changed - PAPER_FILL_CHANGE_ALLOWLIST


def _book_at(t):
    s = int(t) - 1
    return {"s": s, "b": [[83140.0 + (s % 3), 0.5], [83139.0, 1.0]], "a": [[83150.0 + (s % 3), 0.5], [83151.0, 1.0]]}


def test_paper_fill_output_byte_identical_with_measurement_stamped():
    import paper_fill as pf
    for side, leg in (("LONG", "entry"), ("SHORT", "entry"), ("LONG", "exit"), ("SHORT", "exit")):
        before = json.dumps(pf.paper_fill(side, 0.001, T0, 1.5, _book_at, leg=leg), sort_keys=True)
        dec = _decision(direction=side)
        dec.update(tm.entry_quote_fields(dec, bbo_ts=T0 - 0.4, ws_last_tick=T0 - 0.4, recorded_ts=T0))
        tm.entry_fields(side, dec, _book_at(T0), T0)
        after = json.dumps(pf.paper_fill(side, 0.001, T0, 1.5, _book_at, leg=leg), sort_keys=True)
        assert before == after


def test_policy_outputs_identical_with_measurement_stamped():
    import paper_policy_family_gs06_committed_fade_atr_tp as policy
    from gs_tile_contract_support import asia_ts, bar, decide
    d = decide(policy, direction="LONG", engine_bar=bar(atr_pct=50.0, adx=30.0, atr_bp=4.0), ts=asia_ts())
    stamped = copy.deepcopy(d)
    stamped.update(tm.entry_quote_fields(stamped, bbo_ts=stamped.get("signal_ts"), ws_last_tick=stamped.get("signal_ts"),
                                         recorded_ts=1.0))
    stamped.update({f"paper_{k}": 1.0 for k in tm.ENTRY_BOOK_KEYS})
    fa = policy.adaptive_entry_fields("LONG", 60000.25, d)
    fb = policy.adaptive_entry_fields("LONG", 60000.25, stamped)
    # the entry fields carry a copy of the decision (that is how it reaches the position); strip only the measurement
    # keys from that copy -> every price / limit / ttl / validity field is byte-identical
    measured = set(tm.MEASUREMENT_KEYS) | {f"paper_{k}" for k in tm.ENTRY_BOOK_KEYS}
    fb = {**fb, "adaptive_entry_decision": {k: v for k, v in fb["adaptive_entry_decision"].items() if k not in measured}}
    assert json.dumps(fa, sort_keys=True, default=str) == json.dumps(fb, sort_keys=True, default=str)
    assert fa["planned_limit_price"] is not None and fa["planned_limit_price"] == fb["planned_limit_price"]
    for bp, age in ((0.0, 0.0), (-12.5, 120.0), (25.0, 900.0), (3.0, 5400.0)):
        a = policy.exit_action(entry=60000.0, direction="LONG", price=60000.0 * (1 + bp / 1e4), age_sec=age,
                               policy_state={}, entry_decision=d, fill_ts=0)
        b = policy.exit_action(entry=60000.0, direction="LONG", price=60000.0 * (1 + bp / 1e4), age_sec=age,
                               policy_state={}, entry_decision=stamped, fill_ts=0)
        assert repr(a) == repr(b)


def test_tile_row_fill_price_limit_and_pnl_byte_identical():
    import monitor_tiles as mt
    base = {"trade_id": "gs6-iso", "research_lane": "FAMILY_GS06_COMMITTED_FADE_ATR_TP", "ts": T0 + 600,
            "dir": "LONG", "entry": 83150.0, "exit": 83170.0, "net_pnl_usd": 0.01, "net_pnl_usd_raw": 0.0103,
            "net_pnl_bp": 2.4, "exit_reason": "GS_ATR_TAKE_PROFIT", "leverage": 100, "margin_usdt": 0.25,
            "duration_min": 10.0, "shared_ai_call_ts": T0, "limit_price": 83178.3,
            "event_timestamps": {"signal_ts": T0, "exit_trigger_ts": T0 + 599, "exit_trigger_price": 83168.0,
                                 "exit_trigger_bbo_ts": T0 + 598.5},
            "depth_snapshot_signal": ENTRY_BOOK, "pricer_signal_ts": T0,
            "exit_depth_snapshot_signal": {"s": T0 + 598, "b": [[83169.0, 1]], "a": [[83171.0, 1]]},
            "adaptive_entry_decision": _decision()}
    stamped = copy.deepcopy(base)
    stamped["adaptive_entry_decision"].update(tm.entry_quote_fields(
        stamped["adaptive_entry_decision"], bbo_ts=T0 - 0.4, ws_last_tick=T0 - 0.4, recorded_ts=T0))
    kw = dict(status="closed", short_names={}, epoch_id="ce-20261004-v31-freeze21b", forced_reasons=())
    r0, r1 = mt.tile_trade_row(base, **kw), mt.tile_trade_row(stamped, **kw)
    for block in ("fill", "pnl", "order", "pricing"):
        assert json.dumps(r0.get(block), sort_keys=True) == json.dumps(r1.get(block), sort_keys=True), block
    assert r1["trigger"]["entry_quote_source"] == "WS" and r1["trigger"]["entry_trigger_vs_book_bp"] == round(
        (83170 - 83150) / 83170 * 1e4, 3)
    assert r1["exit"]["trigger_book_age_s"] == 1.0 and r1["exit"]["trigger_vs_book_bp"] is not None
    # the measurement never rewrites the trade's own prices
    assert r1["fill"]["fill_price"] == 83150.0 and r1["exit"]["exit_price"] == 83170.0


def test_pair_row_fill_and_pnl_identical_with_measurement():
    chain = {"side": "LONG", "order_type": "LIMIT", "order_price": 83171.0, "fill_price": 83170.0,
             "exit_price": 83180.0, "realized_pnl": 0.01}
    twin = {"side": "LONG", "entry_price": 83150.0, "exit_price": 83170.0, "exit_ts": T0 + 600, "net_pnl_usd": 0.01}
    stamped = {**twin, **{f"paper_{k}": 1.0 for k in tm.ENTRY_QUOTE_KEYS + tm.ENTRY_BOOK_KEYS},
               **{k: 1.0 for k in tm.EXIT_KEYS}}
    a, b = lvp.pair_row("gs6-iso", chain, twin), lvp.pair_row("gs6-iso", chain, stamped)
    measured = {f"paper_{k}" for k in tm.ENTRY_QUOTE_KEYS + tm.ENTRY_BOOK_KEYS} | {
        "paper_trigger_vs_book_bp", "paper_trigger_tape_age_s", "paper_trigger_book_age_s",
        "paper_trigger_vs_book_reason", "paper_trigger_tape_age_flag"}
    assert {k: v for k, v in a.items() if k not in measured} == {k: v for k, v in b.items() if k not in measured}

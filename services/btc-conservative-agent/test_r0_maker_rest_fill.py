"""RK R0 / R4 / R17 (25 Oct): the shared paper_fill maker-rest fill for B1 / B2, and the gb1-d365a7a17cb2 overfill cap.

filled per placement = min(remaining size, trade-through volume after placement - visible queue ahead at placement)."""
import ast
import pathlib

import pytest

import paper_fill as pf
from test_tile_fill_migration import _Clock, _lane_ns

ROOT = pathlib.Path(__file__).resolve().parent


def _p(ts, px, amt, i=None):
    return {"ts": ts, "px": px, "amt": amt, "id": i if i is not None else int(ts * 1000)}


def test_queue_ahead_is_consumed_by_through_volume_then_partials_fill():
    r = pf.maker_rest_fill("LONG", 100.0, 1.0, 0.6, 0.0,
                           [_p(1, 100.0, -5.0),      # at the limit: never counts (no queue_clear credit)
                            _p(2, 99.9, -0.4),       # through 0.4 <= queue 0.6
                            _p(3, 100.2, 3.0),       # wrong side for a buy limit
                            _p(4, 99.8, -0.5),       # through 0.9 -> 0.3 past the queue
                            _p(5, 99.7, None)])      # unsized: fills nothing, counted
    assert [(f["print_ts"], f["qty"], f["queue_ahead"]) for f in r["fills"]] == [(4, 0.3, 0.6)]
    assert r["remaining"] == pytest.approx(0.7) and r["through_seen"] == pytest.approx(0.9) and r["unsized_prints"] == 1
    s = pf.maker_rest_fill("SHORT", 100.0, 1.0, 0.0, 0.0, [_p(1, 100.0, 9.0), _p(2, 100.1, 0.25)])
    assert [(f["print_ts"], f["px"], f["qty"]) for f in s["fills"]] == [(2, 100.0, 0.25)]


def test_placement_ts_and_seen_prints_are_never_counted_again():
    rows = [_p(1, 99.9, -1.0, 11), _p(3, 99.9, -1.0, 12)]
    r = pf.maker_rest_fill("LONG", 100.0, 5.0, 0.0, 0.0, rows, placement_ts=2.0)
    assert [f["print_id"] for f in r["fills"]] == [12]  # a print at/before the placement belongs to the old order
    again = pf.maker_rest_fill("LONG", 100.0, 5.0, 0.0, r["through_seen"], rows, placement_ts=2.0, seen=r["seen"])
    assert again["fills"] == [] and again["through_seen"] == r["through_seen"]


def test_fill_is_capped_at_the_remaining_size():
    r = pf.maker_rest_fill("SHORT", 100.0, 0.3, 0.0, 0.0, [_p(1, 100.5, 0.2), _p(2, 100.6, 0.8)])
    assert r["filled_qty"] == pytest.approx(0.3) and r["remaining"] == 0.0
    assert [f["qty"] for f in r["fills"]] == [pytest.approx(0.2), pytest.approx(0.1)]


def _uncapped_maker_rest_fill():
    """Mutant: paper_fill.maker_rest_fill without the min(rem, ...) cap."""
    src = (ROOT / "paper_fill.py").read_text()
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "maker_rest_fill")
    hit = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "take":
            call = node.value
            assert isinstance(call, ast.Call) and call.func.id == "min"
            node.value = call.args[1]
            hit += 1
    assert hit == 1
    mod = ast.Module(body=[fn], type_ignores=[])
    ns = dict(vars(pf))
    exec(compile(ast.fix_missing_locations(mod), "mutant", "exec"), ns)
    return ns["maker_rest_fill"]


def test_cap_mutation_proof_without_the_cap_the_fill_exceeds_the_remaining_size():
    r = _uncapped_maker_rest_fill()("SHORT", 100.0, 0.3, 0.0, 0.0, [_p(1, 100.5, 0.2), _p(2, 100.6, 0.8)])
    assert r["filled_qty"] > 0.3 + 1e-9  # the test above would fail on this code


# Historical overfill regression gb1-d365a7a17cb2 (24 Oct 2026); replayed on active GS-01 fixture lane: SHORT 0.000310508862 resting at 80513 then 80485; real prints from
# /app/data/runtime/bfx_trades.jsonl (ids 1999206912 / 1999206917 each appear ONCE in the feed).
GB1_REQ = 0.000310508862
GB1_PRINTS = [{"ts": 1792879395.724, "px": 80524.0, "amt": 0.0001, "id": 1999206912, "recv_s": 1792879395},
              {"ts": 1792879410.667, "px": 80528.0, "amt": 0.0001431, "id": 1999206917, "recv_s": 1792879410},
              {"ts": 1792879441.919, "px": 80496.0, "amt": 0.0006, "id": 1999206999, "recv_s": 1792879441}]


def _gb1_ns(monkeypatch):
    import quote_trigger as qt
    monkeypatch.setattr(pf, "book10l_at", lambda t: {"s": int(t) - 1, "a": [[80530.0, 1.0]], "b": [[80500.0, 1.0]]})
    monkeypatch.setattr(qt, "trades_between", lambda a, b: [r for r in GB1_PRINTS if a < r["ts"] <= b])
    tm = {"gs1-r0-overfill-fixture": {"signal_ref": {"adaptive_entry_decision": {"action": "MAKER", "exec": {
        "kind": "TOUCH", "ttl_sec": 600, "reprice_sec": 60}}}}}
    ns = _lane_ns(_Clock(1792879381.0))
    ns["trades_map"] = tm
    order = {"research_lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP", "trade_id": "gs1-r0-overfill-fixture", "signal_dir": "SHORT",
             "qty": GB1_REQ, "signal_created_ts": 1792879381.3403363, "limit_price": 80513.0}
    return ns, order


def _gb1_run_with_racing_duplicate(ns, order):
    """Replays the production interleaving: state_monitor_loop and position_manager both advanced the order over the
    window holding print 1999206912; the loser wrote its fills list on top of the winner's while restoring the stale
    remaining / cursor / seen set (the live result was [P1, P1, P2, P3] = 0.0004105 for 0.0003105)."""
    assert ns["_paper_lane_entry_result"](order, 1792879383.0) is None       # joined at 80513
    assert ns["_paper_lane_entry_result"](order, 1792879398.0) is None       # P1 filled 0.0001
    st = order["_entry_rest"]
    assert [f[2] for f in st["fills"]] == [pytest.approx(0.0001)]
    st.update(remaining=GB1_REQ, cursor_ts=1792879390.0, seen_prints=[])     # the racing writer's stale snapshot
    res = ns["_paper_lane_entry_result"](order, 1792879412.0)
    if res is None:
        order["limit_price"] = 80485.0                                      # 60 s LIMIT_CHASE reprice
        res = ns["_paper_lane_entry_result"](order, 1792879443.0)
    return res


def test_gb1_overfill_regression_fills_never_exceed_the_requested_size(monkeypatch):
    ns, order = _gb1_ns(monkeypatch)
    res = _gb1_run_with_racing_duplicate(ns, order)
    total = sum(f[2] for f in order["_entry_rest"]["fills"])
    assert total <= GB1_REQ + 1e-12
    assert res and res["status"] == "FILLED" and res["filled_qty"] == pytest.approx(GB1_REQ, abs=1e-12)
    assert res["entry_unfilled_qty"] == 0.0
    assert all(set(s) >= {"print_ts", "queue_ahead", "qty"} for s in res["entry_rest_fill_stamps"])  # R17


def test_gb1_overfill_mutation_proof_without_the_remaining_cap_the_replay_overfills(monkeypatch):
    src = (ROOT / "bot.py").read_text()
    cap = 'st["remaining"] = round(max(0.0, req - filled), 12)'
    assert src.count(cap) == 1
    ns, order = _gb1_ns(monkeypatch)
    fn_src = ast.get_source_segment(src, next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef)
                                              and n.name == "_paper_entry_maker_rest"))
    exec(compile(fn_src.replace(cap, "pass"), "mutant_bot", "exec"), ns)
    monkeypatch.setattr(pf, "maker_rest_fill", _uncapped_maker_rest_fill())
    _gb1_run_with_racing_duplicate(ns, order)
    assert sum(f[2] for f in order["_entry_rest"]["fills"]) > GB1_REQ + 1e-9


def test_b1_b2_maker_rest_fills_only_through_the_shared_paper_fill_helper():
    src = (ROOT / "bot.py").read_text()
    fn = next(n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == "_paper_entry_maker_rest")
    calls = {(getattr(c.func.value, "id", ""), c.func.attr) for c in ast.walk(fn)
             if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)}
    assert ("_pf", "maker_rest_fill") in calls
    assert not {a for _, a in calls} & {"maker_rest_step", "maker_rest_full_size"}
    import order_model as om
    assert not hasattr(om, "maker_rest_full_size")  # the all-or-nothing rule (RK: R0 violation) is gone


def test_a5_rest_fill_is_the_shared_maker_fill_with_partials():
    """R4: paper_fill.rest_fill (FADE3 A5) delegates to maker_rest_fill; partial fills, cap, window, print dedup."""
    rows = [_p(1.0, 99.0, -0.3, 1), _p(1.5, 99.0, -0.3, 1),  # same print id twice: counted once
            _p(2.0, 100.0, -9.0, 2),                          # at the limit: never fills
            _p(5.0, 99.0, -9.0, 3)]                           # outside (t0, t0 + ttl]: never fills
    r = pf.rest_fill("LONG", 1.0, 100.0, 0.5, rows, ttl_s=3.0)
    assert r["status"] == "PARTIAL" and r["filled_qty"] == pytest.approx(0.3) and r["price"] == 100.0
    assert r["fill_ts"] == 1.0 and r["evidence_status"] == "INCOMPLETE" and r["fill_stamps"][0]["qty"] == pytest.approx(0.3)
    full = pf.rest_fill("SHORT", 0.2, 100.0, 0.0, [_p(1.0, 100.5, 0.15, 7), _p(2.0, 100.4, 0.5, 8)], ttl_s=3.0)
    assert full["status"] == "FILLED" and full["filled_qty"] == pytest.approx(0.2) and full["fill_ts"] == 2.0
    src = ast.parse((ROOT / "paper_fill.py").read_text(encoding="utf-8"))
    fn = next(n for n in ast.walk(src) if isinstance(n, ast.FunctionDef) and n.name == "rest_fill")
    assert any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "maker_rest_fill" for n in ast.walk(fn))


def test_a5_rest_fill_mutation_proof(monkeypatch):
    """Each new assert above fails on a mutant: all-or-nothing, no dedup, no window."""
    rows = [_p(1.0, 99.0, -0.3, 1), _p(1.5, 99.0, -0.3, 1), _p(5.0, 99.0, -9.0, 3)]
    real = pf.maker_rest_fill

    def all_or_nothing(d, lim, rem, q0, seen_vol, trades, **kw):
        r = real(d, lim, rem, q0, seen_vol, trades, **kw)
        return r if r["remaining"] <= 1e-12 else {**r, "fills": [], "filled_qty": 0.0, "remaining": rem}
    monkeypatch.setattr(pf, "maker_rest_fill", all_or_nothing)
    assert pf.rest_fill("LONG", 1.0, 100.0, 0.5, rows, ttl_s=3.0)["status"] != "PARTIAL"

    def no_dedup(d, lim, rem, q0, seen_vol, trades, **kw):
        return real(d, lim, rem, q0, seen_vol, [{**t, "id": i} for i, t in enumerate(trades)], **kw)
    monkeypatch.setattr(pf, "maker_rest_fill", no_dedup)
    assert pf.rest_fill("LONG", 1.0, 100.0, 0.5, rows, ttl_s=3.0)["filled_qty"] != pytest.approx(0.3)
    monkeypatch.setattr(pf, "maker_rest_fill", real)
    assert pf.rest_fill("LONG", 1.0, 100.0, 0.5, rows, ttl_s=10.0)["status"] == "FILLED"  # window widened -> differs

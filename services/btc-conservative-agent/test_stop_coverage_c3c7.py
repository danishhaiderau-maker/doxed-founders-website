"""HM C3 (resident-stop trades-tail coverage, STOP_COVERAGE_GAP, late prints recorded not skipped) and HM C7
(a depth-short PARTIAL resident-stop walk is priced on the visible depth and stamped UNVERIFIED)."""
import ast
import json
import pathlib
import time

import paper_fill as pf
import quote_trigger as qt

ROOT = pathlib.Path(__file__).resolve().parent
_TREE = []


def _tree():
    if not _TREE:
        _TREE.append(ast.parse((ROOT / "bot.py").read_text(encoding="utf-8")))
    return _TREE[0]


def _defines(n, name):
    if isinstance(n, ast.FunctionDef):
        return n.name == name
    if isinstance(n, ast.Assign):
        return any(getattr(t, "id", "") == name for t in n.targets)
    if isinstance(n, ast.AnnAssign):
        return getattr(n.target, "id", "") == name
    return False


def _ns(names, **extra):
    ns = {"time": time, **extra}
    for name in names:
        node = next(n for n in _tree().body if _defines(n, name))
        exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
    return ns


def _write_tape(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# ---------------------------------------------------------------- C3: coverage


def test_scan_coverage_gap_rules():
    assert qt.stop_scan_coverage(100.0, 110.0, {"lo_s": 90, "hi_s": 110, "n_s": 20})["gap_s"] == 0.0
    assert qt.stop_scan_coverage(100.0, 110.0, {"lo_s": 104, "hi_s": 110, "n_s": 6})["gap_s"] == 4.0
    assert qt.stop_scan_coverage(100.0, 110.0, None)["gap_s"] == 10.0  # no tail -> the whole window is a gap
    assert qt.stop_scan_coverage(100.0, 100.0, None)["gap_s"] == 0.0


def test_trades_tail_received_after_and_coverage(tmp_path):
    p = tmp_path / "bfx_trades.jsonl"
    # second 105 carries a print stamped 101.5 (late by 4.5 s at the end of its second)
    _write_tape(p, [{"s": 100, "tr": [[1, 100200, -0.1, 99.0]]}, {"s": 101, "tr": []},
                    {"s": 105, "tr": [[2, 101500, -0.2, 98.0], [3, 105100, 0.1, 99.5]]}])
    tail = qt.TradesTail(str(p))
    # between() reads only the collector seconds of its window: the late print (written in 105) is missed
    assert [x["id"] for x in tail.between(100.0, 102.0)] == [1]
    late = tail.received_after(100.0, 106.0, 101)
    assert [x["id"] for x in late] == [2, 3] and late[0]["recv_s"] == 105
    assert tail.coverage() == {"lo_s": 100, "hi_s": 105, "n_s": 3}
    assert qt.TradesTail(str(tmp_path / "missing.jsonl")).coverage() is None


def _cov_ns(monkeypatch, cov, late_rows=()):
    monkeypatch.setattr(qt, "trades_coverage", lambda: cov)
    monkeypatch.setattr(qt, "trades_received_after", lambda t0, t1, r: [p for p in late_rows if p["recv_s"] > r])
    return _ns(["STOP_COVERAGE_ROW_KEYS", "_stop_late_prints", "_stamp_stop_coverage"])


def test_stamp_gap_and_late_prints(monkeypatch):
    late_rows = [{"ts": 101.5, "px": 98.0, "amt": -0.2, "id": 2, "recv_s": 105}]
    ns = _cov_ns(monkeypatch, {"lo_s": 104, "hi_s": 110, "n_s": 7}, late_rows)
    pos = {"entry_ts": 100.0}
    assert ns["_stop_late_prints"](pos, 111.0) == []  # first scan reads the whole window
    ns["_stamp_stop_coverage"](pos, 100.0, 111.0, [])
    assert pos["stop_coverage_flag"] == qt.STOP_COVERAGE_GAP and pos["stop_coverage_unverified"] is True
    assert pos["stop_coverage_gap_s"] == 4.0 and pos["_xstop_recv_s"] == 110
    assert (pos["stop_tail_lo_s"], pos["stop_tail_hi_s"]) == (104, 110)
    pos["_xstop_recv_s"] = 104  # a print written in second 105 was not seen by the last scan
    late = ns["_stop_late_prints"](pos, 111.0)
    assert [p["id"] for p in late] == [2]
    ns["_stamp_stop_coverage"](pos, 109.0, 111.0, late)
    assert pos["stop_late_prints_n"] == 1 and pos["stop_late_print_max_s"] == 4.5
    assert pos["stop_coverage_gap_s"] == 4.0  # 109..111 is covered: no new gap


def test_stamp_covered_window_has_no_flag(monkeypatch):
    ns = _cov_ns(monkeypatch, {"lo_s": 50, "hi_s": 110, "n_s": 61})
    pos = {"entry_ts": 100.0}
    ns["_stamp_stop_coverage"](pos, 100.0, 110.0, [])
    assert "stop_coverage_flag" not in pos and "stop_late_prints_n" not in pos


def test_late_print_triggers_resident_stop(monkeypatch):
    """A crossing print that arrives after the cursor passed its time still fires the stop at its own time."""
    import order_model as om
    monkeypatch.setattr(qt, "trades_between", lambda t0, t1: [])
    monkeypatch.setattr(qt, "book_rows_between", lambda t0, t1, f: [])
    monkeypatch.setattr(om, "tile_options", lambda tile: {"resident_stop_trigger_basis": qt.STOP_BASIS_TRADES})
    ns = _ns(["_exchange_stop_trigger"], ACTIVE_TILE_REGISTRY={},
             _exchange_stop_fields=lambda pos: {"exchange_stop_px": 98.5, "exchange_stop_bp": 40})
    pos = {"dir": "LONG", "research_lane": "GS-01"}
    late = [{"ts": 101.5, "px": 98.0, "amt": -0.2, "id": 2, "recv_s": 105}]
    assert ns["_exchange_stop_trigger"](pos, 108.0, 111.0) is None
    hit = ns["_exchange_stop_trigger"](pos, 108.0, 111.0, late_trades=late)
    assert hit and hit["trigger_ts"] == 101.5 and hit["stop_trigger_late_print"] is True


def test_resident_scan_wiring():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    body = src[src.index("def _realism_level_stops("):]
    body = body[:body.index("\ndef ")]
    assert "_stop_late_prints(pos" in body and "late_trades=late" in body and "_stamp_stop_coverage(pos" in body
    assert "or book top reaching" not in body
    assert "**_exit_evidence_fields(pos)," in src


# ---------------------------------------------------------------- C7: depth-short resident walk


def _shallow(t):
    s = int(t)
    return {"s": s, "b": [[100.0, 0.01], [99.9, 0.01]], "a": [[100.1, 0.01], [100.2, 0.01]]}


def test_depth_short_resident_walk_unverified():
    r = pf.paper_fill("LONG", 0.05, 1000.2, 0.0, _shallow, leg=pf.RESIDENT_LEG,
                      delay_source="EXCHANGE_RESIDENT_NO_DELAY")
    assert r["status"] == "PARTIAL" and r["depth_short"] is True and r["depth_short_qty"] == 0.03
    assert r["unverified_reason"] == pf.DEPTH_SHORT_PARTIAL and r["evidence_status"] == "UNVERIFIED"
    assert r["price"] is not None
    full = pf.paper_fill("LONG", 0.015, 1000.2, 0.0, _shallow, leg=pf.RESIDENT_LEG,
                         delay_source="EXCHANGE_RESIDENT_NO_DELAY")
    assert full["status"] == "FILLED" and "depth_short" not in full and full.get("unverified_reason") is None


def test_resident_priced_accepts_depth_short_partial_only():
    ns = _ns(["_resident_priced"])
    rp = ns["_resident_priced"]
    assert rp({"status": "FILLED", "price": 100.0})
    assert rp({"status": "PARTIAL", "price": 100.0, "depth_short": True})
    assert not rp({"status": "PARTIAL", "price": 100.0})
    assert not rp({"status": "NO_FILL_NO_DEPTH", "price": None})
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    body = src[src.index("def _paper_lane_exit("):]
    body = body[:body.index("\ndef ")]
    assert body.count("_resident_priced(res)") == 2 and body.count('"exit_depth_short_qty"') == 2

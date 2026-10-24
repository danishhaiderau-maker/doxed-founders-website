"""R20 (Danish 25 Oct 06:21 AEDT): hard TOTAL live-loss cap; disarm ALL + force-flat; unchangeable by any bot/admin route."""
import pathlib
import re

import live_guardian as lg
import live_tile_gate as ltg

HERE = pathlib.Path(__file__).resolve().parent


def _c(pnl, closed=True, cid="x"):
    return {"closed": closed, "correlation_id": cid, "_pnl": pnl}


def _patch(monkeypatch):
    monkeypatch.setattr(ltg, "trade_realised_pnl_usd", lambda c: c.get("_pnl"))


def test_constant_pinned_to_d3_total_cap_pending_danish():
    assert lg.LIVE_TOTAL_LOSS_CAP_USD == 0.10 == ltg.DAILY_LOSS_CAP_TOTAL_USD
    assert lg.LIVE_TOTAL_LOSS_RED_CODES <= lg.RED_CODES
    assert lg.VIOL_LIVE_TOTAL_LOSS_CAP not in lg.ENTRY_BLOCK_CODES
    assert lg.VIOL_LIVE_TOTAL_LOSS_CAP_ERROR not in lg.ENTRY_BLOCK_CODES


def test_cumulative_across_days_breaches_and_disarms(monkeypatch):
    _patch(monkeypatch)
    v = lg.total_live_loss_violations([_c(-0.04), _c(-0.04), _c(-0.03), _c(None, closed=False)])
    assert [x["code"] for x in v] == [lg.VIOL_LIVE_TOTAL_LOSS_CAP]
    disarm, amber = lg.split_violations(v)
    assert disarm == v and amber == []


def test_below_cap_and_wins_offset(monkeypatch):
    _patch(monkeypatch)
    assert lg.total_live_loss_violations([_c(-0.09)]) == []
    assert lg.total_live_loss_violations([_c(-0.12), _c(0.05)]) == []


def test_error_fails_closed_red(monkeypatch):
    def boom(c):
        raise RuntimeError("x")
    monkeypatch.setattr(ltg, "trade_realised_pnl_usd", boom)
    v = lg.total_live_loss_violations([_c(-0.01)])
    assert v[0]["code"] == lg.VIOL_LIVE_TOTAL_LOSS_CAP_ERROR
    assert lg.split_violations(v)[0] == v


def test_unreadable_row_blocks_entry(monkeypatch):
    _patch(monkeypatch)
    v = lg.total_live_loss_violations([_c(None)])
    assert lg.split_violations(v) == ([], v)


def test_evaluate_includes_pre_epoch_rows(monkeypatch):
    """An epoch reset can never reset the total cap."""
    _patch(monkeypatch)
    chains = [dict(_c(-0.11), fill_at_ts=1.0, lane="LIVE")]
    v = lg.evaluate(iter(chains), None, {}, 1e9, lane_needle="", epoch_ts=5e8)
    assert lg.VIOL_LIVE_TOTAL_LOSS_CAP in {x["code"] for x in v}


def test_no_route_env_or_setter_can_change_it():
    pat = re.compile(r"LIVE_TOTAL_LOSS_CAP_USD\s*(=|\+=|-=)|setattr\([^)]*LIVE_TOTAL_LOSS_CAP_USD")
    for p in HERE.glob("*.py"):
        if p.name.startswith("test_"):
            continue
        src = p.read_text(encoding="utf-8", errors="ignore")
        hits = [m.group(0) for m in pat.finditer(src)]
        if p.name == "live_guardian.py":
            assert hits == ["LIVE_TOTAL_LOSS_CAP_USD ="], hits
            line = [l for l in src.splitlines() if l.startswith("LIVE_TOTAL_LOSS_CAP_USD =")]
            assert line == ["LIVE_TOTAL_LOSS_CAP_USD = 0.10"]   # literal: no env/config read
        else:
            assert hits == [], (p.name, hits)
    assert "LIVE_TOTAL_LOSS_CAP_USD" not in (HERE / "bot.py").read_text(encoding="utf-8", errors="ignore")


def test_r20_daily_cap_hit_disarms_flattens_red_and_keeps_entry_block():
    assert ltg.cap_breach_response() == ltg.CAP_RESPONSE_DISARM_FLATTEN
    for code in ("DAILY_LOSS_CAP", ltg.VIOL_TILE_LOSS_CAP, ltg.VIOL_TOTAL_LOSS_CAP, ltg.VIOL_TOTAL_LOSS_CAP + "_ERROR"):
        v = [{"code": code, "tile": None, "detail": "x"}]
        disarm, amber = lg.split_violations(v)
        assert disarm == v and amber == v and code in lg.RED_CODES, code
    # unreadable pnl / stale book stay entry blocks only (no force-flat on a reporting gap)
    for code in (ltg.VIOL_PNL_UNREADABLE, "OPEN_MARK_UNREADABLE", ltg.AMBER_BOOK_STALE, lg.VIOL_LIVE_TOTAL_LOSS_UNREADABLE):
        v = [{"code": code, "tile": None, "detail": "x"}]
        assert lg.split_violations(v) == ([], v), code


def test_cap_response_has_no_setter():
    import inspect
    src = inspect.getsource(ltg.cap_breach_response)
    assert "environ" not in src and "getenv" not in src and "return CAP_RESPONSE_DISARM_FLATTEN" in src
    for p in HERE.glob("*.py"):
        if not p.name.startswith("test_"):
            assert not re.search(r"cap_breach_response\s*=|setattr\([^)]*cap_breach_response", p.read_text(errors="ignore")), p.name

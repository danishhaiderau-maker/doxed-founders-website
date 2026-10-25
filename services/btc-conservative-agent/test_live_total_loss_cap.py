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


# ---- HM R3 B1 (#876): the R20 latch never self-clears; arm + admit_entry re-check the all-time cap (fail closed) ----
import test_live_protection as tlp
import test_live_tile_gate as tltg

import pytest as _pytest_r20
pytestmark = _pytest_r20.mark.usefixtures("small_loss_limit")   # R20: legacy logic at a lowered limit (see conftest)

_R20_CODES = sorted(lg.LIVE_TOTAL_LOSS_RED_CODES | lg.DAILY_CAP_DISARM_CODES)


def test_r20_cap_hit_then_flat_latch_still_held():
    for code in _R20_CODES:
        assert code in lg.ACK_ONLY_RED_CODES
        ns = tlp._red_clear_ns({"ts": tlp.NOW - 100, "code": code})
        for _ in range(3):   # fresh FLAT_NO_STOPS after the forced flatten
            assert ns["_live_guardian_red_clear_if_flat"](tlp.NOW) is False, code
        st = ns["_LIVE_GUARDIAN_STATE"]
        assert st["red_alert"]["code"] == code and "red_alert_cleared" not in st


def _arm_ns(chains):
    src = tlp._SRC
    a, b = src.index("def _live_total_loss_chains_strict"), src.index("def _live_fences_arm_block")
    import collections
    ns = {"_LIVE_GUARDIAN_STATE": {}, "_LIVE_GUARDIAN_FORCE_FLAT_LOG": collections.deque(),
          "_live_fences_arm_block": lambda: None}
    exec(src[a:b], ns)
    ns["_live_total_loss_chains_strict"] = lambda now=None: chains
    return ns


def test_r20_rearm_refused_while_total_breached_or_unreadable(monkeypatch):
    _patch(monkeypatch)
    assert _arm_ns([_c(-0.11)])["_live_guardian_arm_block"]() == lg.VIOL_LIVE_TOTAL_LOSS_CAP   # red already acked/cleared
    assert _arm_ns(None)["_live_guardian_arm_block"]() == lg.VIOL_LIVE_TOTAL_LOSS_UNREADABLE   # source unreadable
    assert _arm_ns([_c(None)])["_live_guardian_arm_block"]() == lg.VIOL_LIVE_TOTAL_LOSS_UNREADABLE
    assert _arm_ns([_c(-0.05)])["_live_guardian_arm_block"]() is None
    ns = _arm_ns([])
    ns["_live_total_loss_chains_strict"] = lambda now=None: (_ for _ in ()).throw(RuntimeError("x"))
    assert ns["_live_guardian_arm_block"]().startswith(lg.VIOL_LIVE_TOTAL_LOSS_CAP_ERROR)
    # admit_entry refuses too
    ok, reasons, _ = tltg.admit(chains=[_c(-0.11)])
    assert not ok and lg.VIOL_LIVE_TOTAL_LOSS_CAP in reasons
    monkeypatch.setattr(lg, "total_live_loss_violations", lambda ch: 1 / 0)
    ok, reasons, _ = tltg.admit()
    assert not ok and lg.VIOL_LIVE_TOTAL_LOSS_CAP_ERROR in reasons


def test_r20_latch_survives_restart(tmp_path):
    for code in (lg.VIOL_LIVE_TOTAL_LOSS_CAP, "DAILY_LOSS_CAP"):
        ns = tlp._persist_ns(tmp_path)
        ns["_LIVE_GUARDIAN_STATE"].update(red_alert={"ts": tlp.NOW, "code": code})
        assert ns["_live_guardian_persist"]()
        ns2 = tlp._persist_ns(tmp_path)
        ns2["_live_guardian_restore"](tlp.NOW + 30)
        red = ns2["_LIVE_GUARDIAN_STATE"]["red_alert"]
        assert red["code"] == code
        c = tlp._red_clear_ns(dict(red), fetched=tlp.NOW + 60)
        assert c["_live_guardian_red_clear_if_flat"](tlp.NOW + 60) is False and c["_LIVE_GUARDIAN_STATE"]["red_alert"]["code"] == code

"""REALISM_V2 F6 (HM 23 Oct): every exit path stamps exit_pricing_basis + exit_order_class; fill_model_version and
fresh_epoch_id at row creation; a row-build missing-stamp count that alerts (tile_integrity realism-v2-stamps);
the REALISM_V2 marker (fill_model_version 2) with hard rejection of UNVERIFIED rows in every analyzer
(paper_fill.row_fresh_strict). No back-fill: pre-V2 rows are quarantined, never counted, never alerted on."""
import ast
from pathlib import Path

import pytest

import paper_fill as pf
import tile_integrity as ti

ROOT = Path(__file__).resolve().parent


def _v2_row(**kw):
    row = {"trade_id": "t1", "research_lane": "GS1", "exit_reason": "PATH_END_60M", "exit_status": "PAPER_FILL",
           "fill_model": pf.FILL_MODEL, "fill_model_version": 2, "delay_source": "LIVE_POOLED_ENTRY",
           "priced_delay_model": "LIVE_POOLED_ENTRY", "exit_delay_source": "LIVE_POOLED_EXIT",
           "trigger_price_source": "book", "funding_verified": True, "funding_status": "NO_CROSSING",
           "exit_pricing_basis": "MODEL_TIME", "exit_order_class": "BOT_SENT", "fresh_epoch_id": "fse-1",
           "model_fill_ts": 2_000_000_000.0, "entry_ts": 2_000_000_000.0, "fill_valid": True,
           "all_legs_book10l": True, "fresh_eligible": True}
    row.update(kw)
    return row


# ------------------------------------------------------------------ marker + hard rejection
def test_version_bumped_once_for_the_group():
    assert pf.FILL_MODEL_VERSION == pf.REALISM_V2_FILL_MODEL_VERSION == 2


def test_a_fully_stamped_v2_row_counts():
    row = _v2_row()
    assert pf.realism_v2_reasons(row, floor_ts=0) == []
    assert pf.row_fresh_strict(row)


def test_csv_reloaded_strings_are_typed():
    row = _v2_row(fill_model_version="2", funding_verified="True", model_fill_ts="2000000000.0")
    assert pf.realism_v2_reasons(row, floor_ts=0) == []


@pytest.mark.parametrize("change,reason", [
    ({"fill_model_version": 1}, "PRE_REALISM_V2_FILL_MODEL"),
    ({"fill_model_version": None}, "PRE_REALISM_V2_FILL_MODEL"),
    ({"delay_source": "DEFAULT_UNMEASURED", "priced_delay_model": "DEFAULT_1_5S"}, "ENTRY_DELAY_NOT_LIVE_MEASURED"),
    ({"delay_source": "lane_p50_gs1"}, "ENTRY_DELAY_NOT_LIVE_MEASURED"),
    ({"exit_delay_source": "DEFAULT_UNMEASURED"}, "EXIT_DELAY_NOT_LIVE_MEASURED"),
    ({"trigger_price_source": "ticker"}, "TICKER_TRIGGER_UNFLIPPED_TILE"),
    ({"trigger_price_source": None}, "TICKER_TRIGGER_UNFLIPPED_TILE"),
    ({"funding_verified": False}, "FUNDING_UNVERIFIED"),
    ({"funding_verified": None}, "FUNDING_UNVERIFIED"),
    ({"exit_pricing_basis": "REPRICED_CURRENT_TIME"}, "EXIT_BASIS_REPRICED_CURRENT_TIME"),
    ({"exit_pricing_basis": "NO_BOOK_UNPRICED"}, "EXIT_BASIS_NO_BOOK_UNPRICED"),
    ({"non_fill_category": "BOOK_DEPTH_EXCEEDED"}, "NON_FILL_BOOK_DEPTH_EXCEEDED"),
    ({"exit_pricing_basis": None}, "MISSING_EXIT_PRICING_BASIS"),
    ({"exit_order_class": ""}, "MISSING_EXIT_ORDER_CLASS"),
    ({"fresh_epoch_id": "nan"}, "MISSING_FRESH_EPOCH_ID"),
])
def test_every_unverified_kind_is_hard_rejected(change, reason):
    row = _v2_row(**change)
    assert reason in pf.realism_v2_reasons(row, floor_ts=0)
    assert not pf.row_fresh_strict(row)


def test_exchange_resident_exit_has_no_delay_and_counts():
    row = _v2_row(exit_delay_source="EXCHANGE_RESIDENT_NO_DELAY", exit_order_class="EXCHANGE_RESIDENT",
                  exit_pricing_basis="EXCHANGE_RESIDENT_TRIGGER_INSTANT")
    assert pf.realism_v2_reasons(row, floor_ts=0) == []


def test_deploy_floor_from_env(monkeypatch):
    row = _v2_row(model_fill_ts=1_000.0)
    monkeypatch.delenv(pf.REALISM_V2_FLOOR_ENV, raising=False)
    assert pf.realism_v2_floor_ts() == 0.0 and pf.realism_v2_reasons(row) == []
    monkeypatch.setenv(pf.REALISM_V2_FLOOR_ENV, "1500")
    assert pf.realism_v2_reasons(row) == ["BEFORE_REALISM_V2_FLOOR"] and not pf.row_fresh_strict(row)
    assert pf.realism_v2_reasons(_v2_row(model_fill_ts=1_500.0)) == []
    # ISO entry_ts fallback when the model fill time is absent
    assert pf.realism_v2_reasons(_v2_row(model_fill_ts=None, entry_ts="1970-01-01T00:16:40Z")) == ["BEFORE_REALISM_V2_FLOOR"]


def test_monitor_and_fills_by_model_inherit_the_rejection():
    import fills_by_model as fm
    import monitor_tiles as mt
    assert fm.classify(_v2_row()) == "countable"
    assert fm.classify(_v2_row(fill_model_version=1)) != "countable"
    blk = mt.pricing_block(_v2_row(fill_model_version=1))
    assert blk["fresh_eligible"] is False and "PRE_REALISM_V2_FILL_MODEL" in blk["realism_v2_unverified"]


# ------------------------------------------------------------------ every exit path stamps basis + class
def _paper_exit_env(monkeypatch, fill):
    import bot
    import tile_fill
    clock = {"t": 1000.0}
    monkeypatch.setattr(bot.time, "time", lambda: clock["t"])
    monkeypatch.setattr(bot.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(bot, "_disloc_delay", lambda *a, **k: (1.5, "LIVE_POOLED_EXIT"))
    monkeypatch.setattr(bot, "_is_paper_book_lane", lambda p: False)
    monkeypatch.setattr(bot, "_paper_exit_missing_book_seconds", lambda t0: 0)
    monkeypatch.setattr(bot, "_paper_exit_sim", lambda res, pos, d, q, marks: {"price": res["price"]})
    res = ({"status": "FILLED", "price": 80000.0, "fill_ts": 1001.5} if fill
           else {"status": "REJECT_NO_BOOK", "price": None})
    monkeypatch.setattr(tile_fill, "exit_fill", lambda *a, **k: dict(res))
    monkeypatch.setattr(tile_fill, "exit_resident_fill", lambda *a, **k: dict(res))
    return bot


_POS = {"research_lane": "GS1", "dir": "LONG", "qty": 1.0, "exit_trigger_ts": 1000.0, "trade_id": "t1"}


@pytest.mark.parametrize("path,reason,no_wait,fill,cls", [
    ("lane", "PATH_END_60M", False, True, "BOT_SENT"),
    ("lane_no_book", "PATH_END_60M", False, False, "BOT_SENT"),
    ("exchange_stop", "EXCHANGE_STOP", False, True, "EXCHANGE_RESIDENT"),
    ("forced", "STALE_POSITION_FORCE", True, True, "BOT_SENT"),
    ("forced_no_book", "STALE_POSITION_FORCE", True, False, "BOT_SENT"),
    ("admin", "ADMIN_CLOSE", True, True, "BOT_SENT"),
    ("admin_no_book", "ADMIN_CLOSE", True, False, "BOT_SENT"),
    ("exchange_stop_inline_no_book", "EXCHANGE_STOP", True, False, "EXCHANGE_RESIDENT"),
])
def test_every_exit_path_returns_a_basis_and_an_order_class(monkeypatch, path, reason, no_wait, fill, cls):
    import order_model as om
    bot = _paper_exit_env(monkeypatch, fill)
    if reason == "EXCHANGE_STOP":
        reason = om.exchange_stop_reason(40)
    monkeypatch.setattr(bot._PAPER_EXIT_TLS, "no_wait", no_wait, raising=False)
    _out, marks = bot._paper_lane_exit(dict(_POS), reason)
    assert marks.get("exit_pricing_basis"), path
    assert marks.get("exit_order_class") == cls, path


def test_forced_exit_stamps_bot_sent_itself(monkeypatch):
    bot = _paper_exit_env(monkeypatch, True)
    for fill in (True, False):
        bot = _paper_exit_env(monkeypatch, fill)
        _o, marks = bot._paper_forced_exit(dict(_POS), 1000.0, "LONG", 1.0)
        assert marks["exit_order_class"] == "BOT_SENT" and marks["exit_pricing_basis"]


def test_non_paper_lane_rows_get_a_legacy_basis(monkeypatch):
    import bot
    monkeypatch.setattr(bot, "_is_paper_book_lane", lambda p: False)
    assert bot._exit_basis_fallback({}) == "LEGACY_PRICING"
    assert bot._exit_basis_fallback({"bitfinex_live_entry": True}) == "LIVE_EXCHANGE_FILL"
    monkeypatch.setattr(bot, "_is_paper_book_lane", lambda p: True)
    assert bot._exit_basis_fallback({}) == ""  # a paper tile with no stamp is a bug: counted MISSING, never masked


def _dict_keys_in(func: str, var: str):
    tree = ast.parse((ROOT / "bot.py").read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == func)
    for n in ast.walk(fn):
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == var for t in n.targets) \
                and isinstance(n.value, ast.Dict):
            return {k.value for k in n.value.keys if isinstance(k, ast.Constant)}
    raise AssertionError(f"{func}.{var} not found")


def test_reconcile_row_carries_every_stamp():
    reconcile = _dict_keys_in("api_reconcile_phantom_cancel", "trade_row")
    assert set(pf.REALISM_V2_STAMP_FIELDS) <= reconcile


def test_close_row_carries_every_stamp():
    tree = ast.parse((ROOT / "bot.py").read_text())
    found = False
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef):
            continue
        for n in ast.walk(fn):
            if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "trade_row" for t in n.targets) \
                    and isinstance(n.value, ast.Dict):
                keys = {k.value for k in n.value.keys if isinstance(k, ast.Constant)}
                if "exit_fill_price" in keys:  # the closed-position row (fill_model_version comes via _entry_pricing_fields)
                    found = True
                    assert {"exit_pricing_basis", "exit_order_class", "fresh_epoch_id", "trigger_price_source"} <= keys
    assert found
    src = (ROOT / "bot.py").read_text()
    seg = src[src.index("def _entry_pricing_fields"):][:6000]
    assert '"fill_model_version": efs.get("fill_model_version")' in seg


# ------------------------------------------------------------------ missing-stamp count + alert
def test_missing_stamp_count_only_post_fix_rows_and_alerts():
    import monitor_tiles as mt
    rows = [_v2_row(), _v2_row(trade_id="t2", exit_order_class=None, fresh_epoch_id=""),
            {"trade_id": "old", "fill_model_version": 1, "model_fill_ts": 1.0},           # quarantined, not counted
            {"trade_id": "d", "event": "DISLOC_CLOSED", "fill_model_version": 2}]          # DISLOC ledger: out of scope
    out = mt.realism_v2_stamp_missing(rows, floor_ts=0)
    assert out["checked"] == 2 and out["missing"] == 1
    assert out["by_field"] == {"exit_order_class": 1, "fresh_epoch_id": 1}
    part = ti.realism_v2_stamp_check(out)
    assert part["status"] == "RED" and "t2" in part["reasons"][0]
    res = ti.evaluate(now=0, rows=[], book_by_sec={}, chains=[], v1_since_ts=None,
                      feed={"status": "GREEN", "reasons": []}, pnl_reconcile=None, stamps=out)
    assert res["alert"] is True and res["parts"]["realism-v2-stamps"]["status"] == "RED"


def test_floor_counts_rows_by_time_even_if_version_missing():
    import monitor_tiles as mt
    out = mt.realism_v2_stamp_missing([{"trade_id": "x", "model_fill_ts": 2000.0}], floor_ts=1500.0)
    assert out["checked"] == 1 and "fill_model_version" in out["by_field"]


def test_stamp_check_green_when_clean_and_amber_on_count_failure():
    assert ti.realism_v2_stamp_check({"checked": 3, "missing": 0})["status"] == "GREEN"
    assert ti.realism_v2_stamp_check({"error": "ValueError"})["status"] == "AMBER"
    assert ti.realism_v2_stamp_check(None)["status"] == "GREEN"


def test_bot_wires_the_count_into_tile_integrity():
    src = (ROOT / "bot.py").read_text()
    seg = src[src.index("def _tile_integrity_run_once"):][:3000]
    assert "realism_v2_stamp_missing(tile_rows)" in seg and "stamps=stamps" in seg


def test_close_row_fresh_eligible_is_anded_with_the_rejection():
    src = (ROOT / "bot.py").read_text()
    assert 'if trade_row.get("fresh_eligible") and __import__("paper_fill").realism_v2_reasons(trade_row):' in src

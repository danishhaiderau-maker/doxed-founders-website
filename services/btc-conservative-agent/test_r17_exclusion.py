"""R17 (RK 23 Oct 2026): dynamic non-countable exclusion (pinned id lists AND non-book exit triggers AND rows stamped
on write). RK: "a static list alone is a VIOLATION" -> the trigger rule must hold for ids never on any list."""
import ast
import hashlib
import json
import shutil
from pathlib import Path

import pandas as pd
import pytest

import analysis_hygiene
import paper_fill
import r17_exclusion as r17

ROOT = Path(__file__).resolve().parent
EPOCH = 1792263671.26
RK_SHA = "46bc50129176563e15e68591d8f419b66815daec58475206f89b288367a801b7"
FINAL_ID_LIST_SHA = "a4701c554f5085408bbde2c206b0d043169577359d52f17b6a41b40fc36e2bc7"


@pytest.fixture(autouse=True)
def _epoch():
    old = paper_fill.FRESH_EPOCH_TS
    paper_fill.FRESH_EPOCH_TS = EPOCH
    yield
    paper_fill.FRESH_EPOCH_TS = old


def good_row(**extra):
    row = {"trade_id": "gs1-000000000000", "research_lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP",
           "exit_status": "PAPER_FILL", "fill_model": paper_fill.FILL_MODEL, "fill_model_version": 1,
           "entry_ts": EPOCH + 60, "exit_ts": EPOCH + 120, "fill_valid": True, "all_legs_book10l": True}
    row.update(extra)
    return row


def _rk_ids():
    return json.loads((ROOT / "r17" / "r17_ids_20261023.json").read_text())


def test_pinned_lists_ship_verbatim_and_load_clean():
    assert r17.PINNED_LISTS["r17_ids_20261023.json"] == RK_SHA
    assert hashlib.sha256((ROOT / "r17" / "r17_ids_20261023.json").read_bytes()).hexdigest() == RK_SHA
    final = json.loads((ROOT / "r17" / "r17_final_ids_20261023.json").read_text())
    assert hashlib.sha256(json.dumps(sorted(final), separators=(",", ":")).encode()).hexdigest() == FINAL_ID_LIST_SHA
    assert r17.LOAD_ERRORS == ()
    rk = {i for v in _rk_ids().values() for i in v}
    assert len(rk) == 995 and len(final) == 1030 and rk <= set(final)
    assert r17.R17_IDS == frozenset(final)


def test_rk_list_ids_are_non_countable_and_never_fresh():
    for key, ids in _rk_ids().items():
        row = good_row(trade_id=ids[0])
        assert r17.non_countable_reason(row) == r17.REASON_ID_LIST, key
        assert paper_fill.row_fresh_eligible(row) is False
        assert paper_fill.row_fresh_strict(row) is False
    assert paper_fill.row_fresh_strict(good_row()) is True  # control: an unlisted book-triggered row still counts


@pytest.mark.parametrize("src", sorted(r17.TICKER_TRIGGER_SOURCES))
def test_ticker_sourced_trigger_is_rejected_for_ids_on_no_list(src):
    tid = "gs1-ffffffffffff"
    assert tid not in r17.R17_IDS
    ev = {"schema": "trade_event_timestamps_v1", "exit_trigger_source": src}
    for row in (good_row(trade_id=tid, exit_trigger_source=src),
                good_row(trade_id=tid, event_timestamps=ev),
                good_row(trade_id=tid, event_timestamps=repr(ev)),        # bot.log_trade CSV dict repr
                good_row(trade_id=tid, event_timestamps=json.dumps(ev)),
                good_row(trade_id=tid, exit_trigger_source=src.lower())):
        assert r17.non_countable_reason(row) == f"R17_TICKER_TRIGGER:{src}"
        assert paper_fill.row_fresh_strict(row) is False and paper_fill.row_fresh_eligible(row) is False


def test_book_and_admin_triggers_are_not_rejected_by_the_trigger_rule():
    for src in (None, "", "PAPER_BOOK_EXIT", "ADMIN_MANUAL_CLOSE", "PATH_END_60M"):
        assert r17.non_countable_reason(good_row(exit_trigger_source=src)) is None, src


def test_wrapped_trade_id_keeps_exclusion_and_stamped_rows_stay_excluded():
    tid = next(iter(sorted(r17.R17_IDS)))
    assert r17.non_countable_reason(good_row(trade_id=f"adopt:{tid}")) == r17.REASON_ID_LIST
    assert r17.non_countable_reason(good_row(non_countable="True", non_countable_reason="X")) == "X"
    assert r17.non_countable_reason(good_row(non_countable=True)) == r17.REASON_STAMPED
    assert r17.non_countable_reason(good_row(non_countable=False)) is None


def test_fail_closed_when_a_list_is_missing_or_altered(tmp_path):
    shutil.copytree(ROOT / "r17", tmp_path / "r17")
    (tmp_path / "r17" / "r17_ids_20261023.json").write_text("{}")
    ids, errors = r17.load_lists(tmp_path / "r17")
    assert errors and "sha256" in errors[0]
    (tmp_path / "r17" / "r17_final_ids_20261023.json").unlink()
    _, errors = r17.load_lists(tmp_path / "r17")
    assert len(errors) == 2
    assert r17.non_countable_reason(good_row(), load_errors=errors) == r17.REASON_UNVERIFIED


def test_stamp_on_write_marks_new_rows():
    tid = next(iter(sorted(r17.R17_IDS)))
    bad = r17.stamp_on_write(good_row(trade_id=tid, fresh_eligible=True))
    assert bad["non_countable"] is True and bad["non_countable_reason"] == r17.REASON_ID_LIST
    assert bad["fresh_eligible"] is False
    tick = r17.stamp_on_write(good_row(trade_id="rnd-ffffffffffff",
                                       event_timestamps={"exit_trigger_source": "WS_TICK"}))
    assert tick["non_countable"] is True and tick["non_countable_reason"] == "R17_TICKER_TRIGGER:WS_TICK"
    ok = r17.stamp_on_write(good_row(fresh_eligible=True))
    assert ok["non_countable"] is False and ok["non_countable_reason"] is None and ok["fresh_eligible"] is True


def test_close_position_stamps_trade_row_on_write_before_any_writer():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "close_position")
    assign = next(n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                  and any(getattr(t, "id", "") == "trade_row" for t in n.targets))
    stamp = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "stamp_on_write"]
    assert stamp and all(isinstance(c.args[0], ast.Name) and c.args[0].id == "trade_row" for c in stamp)
    writers = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
               and getattr(n.func, "id", "") in ("log_trade", "log_trade_outcome_jsonl", "dual_write_paper_close")]
    assert writers and assign.end_lineno < stamp[0].lineno < min(writers)
    seg = ast.get_source_segment(src, fn)
    assert '"non_countable": True' in seg and "R17_STAMP_ERROR" in seg  # stamping error fails closed


def test_analyzer_hygiene_excludes_r17_rows_without_opt_in():
    tid = next(iter(sorted(r17.R17_IDS)))
    df = pd.DataFrame([
        {"trade_id": "a", "exit_reason": "BREAKEVEN_LOCK", "net_pnl_usd": 0.01, "notional_usd": 25.0, "close_ts": 1.0},
        {"trade_id": tid, "exit_reason": "BREAKEVEN_LOCK", "net_pnl_usd": 0.01, "notional_usd": 25.0, "close_ts": 1.0},
        {"trade_id": "c", "exit_reason": "TP1", "net_pnl_usd": 0.01, "notional_usd": 25.0, "close_ts": 1.0,
         "event_timestamps": repr({"exit_trigger_source": "POSITION_MANAGER"})},
        {"trade_id": "d", "exit_reason": "TP1", "net_pnl_usd": 0.01, "notional_usd": 25.0, "close_ts": 1.0,
         "non_countable": True, "non_countable_reason": "R17_TICKER_TRIGGER:WS_TICK"},
    ])
    out = analysis_hygiene.apply(df, include_forced=True, include_late=True)
    assert list(out["trade_id"]) == ["a"]
    assert analysis_hygiene.SUMMARY["r17_non_countable_excluded"] == 3
    assert list(analysis_hygiene.r17_filter(df)["trade_id"]) == ["a"]


def test_analyzer_engine_falls_back_to_r17_filter_when_hygiene_fails():
    src = (ROOT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    i = src.index("Hygiene filter failed")
    tail = src[i:i + 900]
    assert "_hyg.r17_filter(trades)" in tail and "trades.iloc[0:0]" in tail


def test_r17_lists_ship_in_the_fly_image():
    lines = (ROOT / ".dockerignore").read_text().splitlines()
    for name in ("!r17/", "!r17/r17_ids_20261023.json", "!r17/r17_final_ids_20261023.json"):
        assert name in lines

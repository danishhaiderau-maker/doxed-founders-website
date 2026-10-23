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


PROOF = {"trigger_price_source": "book", "delay_source": "lane_q90_n24", "delay_unverified": False,
         "exit_priced_delay_source": "lane_max_n12", "entry_walk_bp": 0.4, "exit_walk_bp": 0.6,
         "exit_legs_unmeasured": 0, "entry_fill_sim": {"price_age_s": 0.21, "price_stale": False},
         "exit_price_age_s": 0.33, "levels_crossed_unfired": [], "stop_coverage": "STOP_COVERAGE_OK"}


def good_row(**extra):
    row = {"trade_id": "gs1-000000000000", "research_lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP",
           "exit_status": "PAPER_FILL", "fill_model": paper_fill.FILL_MODEL, "fill_model_version": 1,
           "entry_ts": EPOCH + 60, "exit_ts": EPOCH + 120, "fill_valid": True, "all_legs_book10l": True,
           # proof fields (#841 stamps; levels_crossed_unfired / stop_coverage per REALISM_V2 C3)
           **PROOF}
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


@pytest.mark.parametrize("field,value,reason", [
    ("trigger_price_source", None, "TRIGGER_SOURCE_MISSING"),
    ("trigger_price_source", "ticker", "TRIGGER_NOT_BOOK"),
    ("trigger_price_source", "last_trade", "TRIGGER_NOT_BOOK"),
    ("delay_source", None, "DELAY_SOURCE_MISSING"),
    ("delay_source", "default_until_n10", "DELAY_NOT_MEASURED"),
    ("delay_source", "lane_max_n9", "DELAY_NOT_MEASURED"),
    ("delay_unverified", True, "DELAY_UNVERIFIED"),
    ("exit_priced_delay_source", None, "EXIT_DELAY_NOT_MEASURED"),
    ("exit_priced_delay_source", "default_until_n10", "EXIT_DELAY_NOT_MEASURED"),
    ("entry_walk_bp", None, "ENTRY_NOT_BOOK_WALKED"),
    ("entry_walk_bp", float("nan"), "ENTRY_NOT_BOOK_WALKED"),
    ("entry_fill_sim", {}, "ENTRY_PRICE_AGE_MISSING"),
    ("entry_fill_sim", {"price_age_s": 0.2, "price_stale": True}, "ENTRY_PRICE_STALE"),
    ("exit_walk_bp", None, "EXIT_NOT_BOOK_WALKED"),
    ("exit_legs_unmeasured", 1, "EXIT_LEG_UNMEASURED"),
    ("exit_price_age_s", None, "EXIT_PRICE_AGE_MISSING"),
    ("levels_crossed_unfired", None, "LEVEL_CROSS_UNKNOWN"),
    ("levels_crossed_unfired", ["SL"], "LEVEL_CROSSED_UNFIRED"),
    ("levels_crossed_unfired", "unknown", "LEVEL_CROSSED_UNFIRED"),
    ("stop_coverage", "STOP_COVERAGE_GAP", "STOP_COVERAGE_GAP"),
    ("stop_coverage", "STOP_COVERAGE_UNVERIFIED", "STOP_COVERAGE_GAP"),
])
def test_any_missing_or_unknown_proof_excludes_ids_on_no_list(field, value, reason):
    tid = "gs1-ffffffffffff"
    assert tid not in r17.R17_IDS
    row = good_row(trade_id=tid, **{field: value})
    got = r17.non_countable_reason(row)
    assert got and got.startswith("R17_UNPROVEN:") and reason in got.split(":", 1)[1].split("|")
    assert paper_fill.row_fresh_strict(row) is False and paper_fill.row_fresh_eligible(row) is False


def test_row_without_any_proof_lists_every_reason():
    bare = {"trade_id": "gs1-eeeeeeeeeeee", "exit_status": "PAPER_FILL"}
    got = r17.non_countable_reason(bare).split(":", 1)[1].split("|")
    assert set(got) >= {"TRIGGER_SOURCE_MISSING", "DELAY_SOURCE_MISSING", "EXIT_DELAY_NOT_MEASURED",
                        "ENTRY_NOT_BOOK_WALKED", "ENTRY_PRICE_AGE_MISSING", "EXIT_NOT_BOOK_WALKED",
                        "EXIT_PRICE_AGE_MISSING", "LEVEL_CROSS_UNKNOWN"}


def test_loop_name_is_not_evidence_either_way():
    # #841 still names the loop (POSITION_MANAGER / PROTECTIVE_EXIT_WORKER / WS_TICK) on book-triggered rows
    for src in (None, "", "POSITION_MANAGER", "PROTECTIVE_EXIT_WORKER", "WS_TICK", "ADMIN_MANUAL_CLOSE"):
        assert r17.non_countable_reason(good_row(exit_trigger_source=src)) is None, src


def test_resident_exit_needs_no_exit_delay_and_csv_reprs_parse():
    row = good_row(exit_priced_delay_source=None, exit_pricing_basis=r17.RESIDENT_EXIT_BASIS,
                   entry_fill_sim=repr({"price_age_s": 0.1}), levels_crossed_unfired="[]")
    assert r17.non_countable_reason(row) is None


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
    tick = r17.stamp_on_write(good_row(trade_id="rnd-ffffffffffff", trigger_price_source="ticker"))
    assert tick["non_countable"] is True and tick["non_countable_reason"] == "R17_UNPROVEN:TRIGGER_NOT_BOOK"
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
        {"trade_id": "a", "exit_reason": "BREAKEVEN_LOCK", "net_pnl_usd": 0.01, "notional_usd": 25.0, "close_ts": 1.0,
         **{k: (repr(v) if isinstance(v, (dict, list)) else v) for k, v in PROOF.items()}},
        {"trade_id": tid, "exit_reason": "BREAKEVEN_LOCK", "net_pnl_usd": 0.01, "notional_usd": 25.0, "close_ts": 1.0},
        {"trade_id": "c", "exit_reason": "TP1", "net_pnl_usd": 0.01, "notional_usd": 25.0, "close_ts": 1.0,
         **{k: (repr(v) if isinstance(v, (dict, list)) else v) for k, v in PROOF.items()},
         "delay_source": "default_until_n10"},
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

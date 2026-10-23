"""R17 dynamic non-countable exclusion: pinned id lists, plus countable-only-with-proof against the r17_fields
closed-row contract, plus rows stamped on write. RK: "a static list alone is a VIOLATION", so the proof rule must
hold for ids that are on no list."""
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


PROOF = {"trigger_price_source": "book", "delay_source": "lane_q90_n24", "exit_priced_delay_source": "lane_max_n12",
         "entry_walk_bp": 0.4, "exit_walk_bp": 0.6, "exit_legs_unmeasured": 0, "evidence_status": "COMPLETE",
         "entry_book_age_ms": 210, "exit_price_age_s": 0.33, "exit_evidence_status": "COMPLETE",
         "exit_unverified_reason": None, "levels_crossed_unfired": [], "stop_coverage": "STOP_COVERAGE_OK",
         # RK/HM K1: hold path proven clear of the liquidation distance
         "liq_unverified": False, "liq_mark_rows_n": 30, "liq_trigger_ts": None,
         "entry_book_receive_ts": 1000.2, "entry_book_receive_mono": 50.2, "entry_book_receive_ts_source": "WS_FRAME", "entry_book_receive_age_ms": 120.0,
         "exit_book_receive_ts": 1100.4, "exit_book_receive_mono": 150.4, "exit_book_receive_ts_source": "WS_FRAME", "exit_book_receive_age_ms": 80.0,
         # HM #849 item 1: settlement-exact funding, every crossed settlement priced, fill-ts window ends
         "funding_model": "SETTLEMENT_EXACT_V2", "funding_status": "PRICED", "funding_verified": True,
         "funding_ts_fallbacks": [],}


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
    ("exit_priced_delay_source", None, "EXIT_DELAY_NOT_MEASURED"),
    ("exit_priced_delay_source", "default_until_n10", "EXIT_DELAY_NOT_MEASURED"),
    ("entry_walk_bp", None, "ENTRY_NOT_BOOK_WALKED"),
    ("entry_walk_bp", float("nan"), "ENTRY_NOT_BOOK_WALKED"),
    ("evidence_status", None, "ENTRY_EVIDENCE_NOT_COMPLETE"),
    ("evidence_status", "UNVERIFIED", "ENTRY_EVIDENCE_NOT_COMPLETE"),
    ("entry_book_age_ms", None, "ENTRY_PRICE_AGE_MISSING"),
    ("entry_book_age_ms", -5, "ENTRY_PRICE_AGE_MISSING"),
    ("exit_walk_bp", None, "EXIT_NOT_BOOK_WALKED"),
    ("exit_legs_unmeasured", 1, "EXIT_LEG_UNMEASURED"),
    ("exit_legs_unmeasured", None, "EXIT_LEG_UNMEASURED"),
    ("exit_price_age_s", None, "EXIT_PRICE_AGE_MISSING"),
    ("exit_evidence_status", None, "EXIT_EVIDENCE_NOT_COMPLETE"),
    ("exit_evidence_status", "UNVERIFIED", "EXIT_EVIDENCE_NOT_COMPLETE"),
    ("exit_unverified_reason", "STALE_BOOK", "EXIT_UNVERIFIED"),
    ("exit_unverified_reason", "FORCED_NO_BOOK", "EXIT_UNVERIFIED"),
    ("levels_crossed_unfired", None, "LEVEL_CROSS_UNKNOWN"),
    ("levels_crossed_unfired", ["SL"], "LEVEL_CROSSED_UNFIRED"),
    ("levels_crossed_unfired", "None", "LEVEL_CROSSED_UNFIRED"),
    ("levels_crossed_unfired", "none", "LEVEL_CROSSED_UNFIRED"),
    ("levels_crossed_unfired", "0", "LEVEL_CROSSED_UNFIRED"),
    ("levels_crossed_unfired", "unknown", "LEVEL_CROSSED_UNFIRED"),
    ("stop_coverage", None, "STOP_COVERAGE_MISSING"),
    ("stop_coverage", "WHATEVER", "STOP_COVERAGE_UNKNOWN"),
    ("stop_coverage", "STOP_COVERAGE_GAP", "STOP_COVERAGE_UNKNOWN"),
    ("stop_coverage", "STOP_COVERAGE_UNVERIFIED", "STOP_COVERAGE_UNKNOWN"),
    # RK/HM K1: liquidation not proven clear
    ("liq_unverified", True, "LIQ_UNVERIFIED"),
    ("liq_unverified", "true", "LIQ_UNVERIFIED"),
    ("liq_unverified", None, "LIQ_CHECK_MISSING"),
    ("liq_mark_rows_n", 0, "LIQ_MARKS_MISSING"),
    ("liq_mark_rows_n", None, "LIQ_MARKS_MISSING"),
    ("liq_trigger_ts", 1.0e9, "LIQ_DISTANCE_REACHED"),
    # RK/HM #851: the priced book rows must carry the MEASURED WS receive time (wall + monotonic, WS_FRAME)
    ("entry_book_receive_ts", None, "BOOK_RECEIVE_TS_MISSING"),
    ("entry_book_receive_mono", None, "BOOK_RECEIVE_TS_MISSING"),
    ("entry_book_receive_ts_source", "INFERRED_BUCKET_LAG", "BOOK_RECEIVE_TS_MISSING"),
    ("entry_book_receive_ts_source", "MISSING", "BOOK_RECEIVE_TS_MISSING"),
    ("exit_book_receive_ts", None, "EXIT_BOOK_RECEIVE_TS_MISSING"),
    ("exit_book_receive_mono", float("nan"), "EXIT_BOOK_RECEIVE_TS_MISSING"),
    ("exit_book_receive_ts_source", None, "EXIT_BOOK_RECEIVE_TS_MISSING"),
    # HM #849 items 1-3: funding must be settlement-exact, priced, verified and fill-ts windowed
    ("funding_verified", False, "FUNDING_UNVERIFIED"),
    ("funding_verified", None, "FUNDING_UNVERIFIED"),
    ("funding_status", "UNVERIFIED_NO_DERIV_STATUS", "FUNDING_UNVERIFIED"),
    ("funding_status", "UNVERIFIED_PARTIAL_TS_MISSING", "FUNDING_UNVERIFIED"),
    ("funding_status", "UNVERIFIED_STALE_DERIV", "FUNDING_UNVERIFIED"),
    ("funding_status", "FUNDING_PRICER_ERROR", "FUNDING_PRICER_ERROR"),
    ("funding_model", None, "FUNDING_UNVERIFIED"),
    ("funding_ts_fallbacks", None, "FUNDING_UNVERIFIED"),
    ("funding_ts_fallbacks", ["EXIT_FILL_TS_FROM_NOW"], "FUNDING_TS_FALLBACK"),
    ("funding_ts_fallbacks", '["ENTRY_FILL_TS_FROM_ENTRY_TS"]', "FUNDING_TS_FALLBACK"),
    ("entry_book_receive_age_ms", None, "BOOK_RECEIVE_TS_MISSING"),  # HM 02:55: age from receipt is a proof field
    ("exit_book_receive_age_ms", -5.0, "EXIT_BOOK_RECEIVE_TS_MISSING"),
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
    assert set(got) == {"FUNDING_UNVERIFIED", "TRIGGER_SOURCE_MISSING", "DELAY_SOURCE_MISSING", "EXIT_DELAY_NOT_MEASURED",
                        "ENTRY_NOT_BOOK_WALKED", "ENTRY_EVIDENCE_NOT_COMPLETE", "ENTRY_PRICE_AGE_MISSING",
                        "EXIT_NOT_BOOK_WALKED", "EXIT_LEG_UNMEASURED", "EXIT_PRICE_AGE_MISSING",
                        "EXIT_EVIDENCE_NOT_COMPLETE", "LEVEL_CROSS_UNKNOWN", "STOP_COVERAGE_MISSING",
                        "LIQ_CHECK_MISSING", "BOOK_RECEIVE_TS_MISSING", "EXIT_BOOK_RECEIVE_TS_MISSING"}


def test_missing_stop_coverage_alone_excludes():
    row = good_row()
    del row["stop_coverage"]
    assert r17.non_countable_reason(row) == "R17_UNPROVEN:STOP_COVERAGE_MISSING"


def test_contract_module_is_the_single_source():
    import r17_fields
    assert set(PROOF) <= set(r17_fields.REQUIRED_FIELDS) | {"exit_unverified_reason"}
    assert set(r17_fields.REQUIRED_FIELDS) - {"exit_pricing_basis", "exit_book_age_ms"} <= set(PROOF)
    assert r17.F is r17_fields
    src = (ROOT / "r17_exclusion.py").read_text()
    assert "import os" not in src and "environ" not in src and "getenv" not in src  # proof rule cannot be switched off


def test_841_shaped_closed_row_with_contract_fields_counts():
    """A closed row shaped like #841 @9f095dbf trade_row (entry pricing fields flattened from entry_fill_sim,
    slippage fields, exit marks from _paper_exit_* copied over) plus the contract fields counts."""
    row = {"trade_id": "gs1-aaaaaaaaaaaa", "research_lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP",
           "exit_status": "PAPER_FILL", "fill_model": paper_fill.FILL_MODEL, "fill_model_version": 1,
           "entry_ts": EPOCH + 60, "exit_ts": EPOCH + 120, "fill_valid": True, "all_legs_book10l": True,
           # _entry_pricing_fields
           "delay_s": 1.9, "delay_source": "lane_q90_n31", "priced_delay_s": 1.9, "priced_delay_model": "PAPER_MEASURED",
           "evidence_status": "COMPLETE",
           # _slippage_row_fields
           "book_slippage_usd_entry": 0.001, "entry_walk_bp": 0.3, "book_slippage_usd_exit": 0.002, "exit_walk_bp": 0.5,
           "exit_legs": 1, "exit_legs_unmeasured": 0,
           # exit marks (_paper_exit_*), resident stop exit
           "exit_pricing_basis": "EXCHANGE_RESIDENT_FIRST_ROW_AT_OR_AFTER_TRIGGER", "exit_price_age_s": 0.4,
           "exit_book_row_rule": "FIRST_ROW_AT_OR_AFTER", "exit_evidence_status": "COMPLETE",
           "exit_unverified_reason": None, "exit_trigger_source": "PROTECTIVE_EXIT_WORKER",
           # contract fields REALISM_V2 copies onto trade_row
           "trigger_price_source": "book", "exit_priced_delay_source": "EXCHANGE_RESIDENT_NO_DELAY",
           "entry_book_age_ms": 180, "levels_crossed_unfired": [], "stop_coverage": "STOP_COVERAGE_OK",
           "liq_unverified": False, "liq_mark_rows_n": 30, "liq_trigger_ts": None,
           "entry_book_receive_ts": 1000.2, "entry_book_receive_mono": 50.2, "entry_book_receive_ts_source": "WS_FRAME", "entry_book_receive_age_ms": 120.0,
         "exit_book_receive_ts": 1100.4, "exit_book_receive_mono": 150.4, "exit_book_receive_ts_source": "WS_FRAME", "exit_book_receive_age_ms": 80.0,
         # HM #849 item 1: settlement-exact funding, every crossed settlement priced, fill-ts window ends
         "funding_model": "SETTLEMENT_EXACT_V2", "funding_status": "PRICED", "funding_verified": True,
         "funding_ts_fallbacks": [],}
    assert r17.non_countable_reason(row) is None
    assert r17.stamp_on_write(dict(row))["non_countable"] is False


def test_loop_name_is_not_evidence_either_way():
    # #841 still names the loop (POSITION_MANAGER / PROTECTIVE_EXIT_WORKER / WS_TICK) on book-triggered rows
    for src in (None, "", "POSITION_MANAGER", "PROTECTIVE_EXIT_WORKER", "WS_TICK", "ADMIN_MANUAL_CLOSE"):
        assert r17.non_countable_reason(good_row(exit_trigger_source=src)) is None, src


def test_resident_exit_needs_no_exit_delay_and_csv_reprs_parse():
    row = good_row(exit_priced_delay_source=None, exit_pricing_basis=r17.RESIDENT_EXIT_BASIS,
                   levels_crossed_unfired="[]", exit_unverified_reason="")
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


def test_c5_labels_name_r17_not_no_trades():
    import pandas as pd
    import analysis_hygiene as hyg
    import fills_by_model as fm
    import monitor_tiles as mt
    bad = good_row(trade_id="gs1-unproven00001", stop_coverage=None, exit_unverified_reason="STALE_BOOK")
    out = hyg.apply(pd.DataFrame([bad]))
    assert out.empty
    assert hyg.SUMMARY["r17_reason_counts"] == {"EXIT_UNVERIFIED": 1, "STOP_COVERAGE_MISSING": 1}
    label = hyg.r17_empty_label()
    assert label.startswith("0 countable: 1 excluded R17_UNPROVEN") and "STOP_COVERAGE_MISSING=1" in label
    assert fm.classify(bad) == "r17_unproven"
    tile = fm.summarise([bad], [bad["research_lane"].upper()])["tiles"][bad["research_lane"].upper()]
    assert tile["r17_label"].startswith("R17_UNPROVEN: 1 of 1") and tile["r17_reason_counts"]["EXIT_UNVERIFIED"] == 1
    assert "non_countable_reason" in mt.PRICING_KEYS
    blk = mt.pricing_block(bad)
    assert blk["fresh_eligible"] is False and blk["r17_label"] == "R17_UNPROVEN"
    assert blk["non_countable_reason"] == "R17_UNPROVEN:EXIT_UNVERIFIED|STOP_COVERAGE_MISSING"


@pytest.mark.parametrize("field,value,reason", [
    ("trigger_price_source", "BOOK", "TRIGGER_NOT_BOOK"),
    ("trigger_price_source", "Book", "TRIGGER_NOT_BOOK"),
    ("evidence_status", "complete", "ENTRY_EVIDENCE_NOT_COMPLETE"),
    ("exit_evidence_status", "complete", "EXIT_EVIDENCE_NOT_COMPLETE"),
    ("exit_evidence_status", "Complete", "EXIT_EVIDENCE_NOT_COMPLETE"),
    ("stop_coverage", "stop_coverage_ok", "STOP_COVERAGE_UNKNOWN"),
    ("stop_coverage", "Stop_Coverage_OK", "STOP_COVERAGE_UNKNOWN"),
    ("entry_book_age_ms", float("inf"), "ENTRY_PRICE_AGE_MISSING"),
    ("entry_book_age_ms", float("nan"), "ENTRY_PRICE_AGE_MISSING"),
    ("entry_book_age_ms", "inf", "ENTRY_PRICE_AGE_MISSING"),
    ("exit_price_age_s", float("inf"), "EXIT_PRICE_AGE_MISSING"),
    ("exit_price_age_s", -0.1, "EXIT_PRICE_AGE_MISSING"),
    ("entry_walk_bp", float("-inf"), "ENTRY_NOT_BOOK_WALKED"),
    ("entry_walk_bp", -0.4, "ENTRY_NOT_BOOK_WALKED"),
    ("exit_walk_bp", float("inf"), "EXIT_NOT_BOOK_WALKED"),
    ("exit_walk_bp", float("nan"), "EXIT_NOT_BOOK_WALKED"),
])
def test_exact_case_and_finite_numbers(field, value, reason):
    assert r17.non_countable_reason(good_row(**{field: value})) == f"R17_UNPROVEN:{reason}"


@pytest.mark.parametrize("field,value", [("trigger_price_source", " book "), ("evidence_status", "COMPLETE "),
                                         ("stop_coverage", " STOP_COVERAGE_OK")])
def test_surrounding_spaces_only_are_stripped(field, value):
    assert r17.non_countable_reason(good_row(**{field: value})) is None


def test_exit_book_age_ms_proves_latest_row_exit_age():
    """HM R3: BOT_SENT MODEL_TIME / TP / BE latest-row legs carry book_age_ms but price_age_s=None."""
    assert r17.non_countable_reason(good_row(exit_price_age_s=None, exit_book_age_ms=1100)) is None
    assert r17.non_countable_reason(good_row(exit_price_age_s=None, exit_book_age_ms=float("inf"))) == \
        "R17_UNPROVEN:EXIT_PRICE_AGE_MISSING"
    assert r17.non_countable_reason(good_row(exit_price_age_s=None, exit_book_age_ms=-1)) == \
        "R17_UNPROVEN:EXIT_PRICE_AGE_MISSING"


def test_r17_filter_fallback_populates_reason_counts():
    import pandas as pd
    import analysis_hygiene as hyg
    out = hyg.r17_filter(pd.DataFrame([good_row(trade_id="gs1-fallback00001", stop_coverage="x"), good_row()]))
    assert len(out) == 1
    assert hyg.SUMMARY["r17_reason_counts"] == {"STOP_COVERAGE_UNKNOWN": 1}
    assert hyg.SUMMARY["r17_non_countable_excluded"] == 1


def test_analyzer_has_no_bare_no_trades_wording():
    import re
    src = (ROOT / "analyzer_research_engine_v62.py").read_text(encoding="utf-8")
    bare = [ln for ln in src.splitlines() if re.search(r"No (executed )?trades", ln) and "_r17_empty_note()" not in ln]
    assert bare == []


def test_fly_dashboard_shows_r17_label_per_tile():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert '__TILE_R17_COUNTS__' in src and 'html_escape(_dashboard_r17_tile_text(tiles))' in src
    ns = {}
    start = src.index("def _dashboard_r17_tiles(")
    end = src.index("def _dashboard_tile_offsets_text(")
    bad = good_row(trade_id="gs1-dash00000001", stop_coverage=None)
    ns["trades"] = [bad, good_row()]
    exec(src[start:end], ns)
    lane = bad["research_lane"].upper()
    text = ns["_dashboard_r17_tile_text"]([{"lane": lane, "label": "GS1"}])
    assert text.startswith("GS1: 1 countable, R17_UNPROVEN: 1 of 2 rows excluded (STOP_COVERAGE_MISSING=1)")

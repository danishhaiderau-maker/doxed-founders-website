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
         "exit_unverified_reason": None, "levels_crossed_unfired": [], "stop_coverage": "STOP_COVERAGE_OK"}


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
    assert set(got) == {"TRIGGER_SOURCE_MISSING", "DELAY_SOURCE_MISSING", "EXIT_DELAY_NOT_MEASURED",
                        "ENTRY_NOT_BOOK_WALKED", "ENTRY_EVIDENCE_NOT_COMPLETE", "ENTRY_PRICE_AGE_MISSING",
                        "EXIT_NOT_BOOK_WALKED", "EXIT_LEG_UNMEASURED", "EXIT_PRICE_AGE_MISSING",
                        "EXIT_EVIDENCE_NOT_COMPLETE", "LEVEL_CROSS_UNKNOWN", "STOP_COVERAGE_MISSING"}


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
           "entry_book_age_ms": 180, "levels_crossed_unfired": [], "stop_coverage": "STOP_COVERAGE_OK"}
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


# RK #847 condition 1 / R3: an entry priced on the 1s tape (research_v3_candidates' CONSERVATIVE entry via
# evaluate_limit_fill on market_microstructure_1s) is never countable. The reason lives in the SAME contract
# module (r17_fields.FORBIDDEN + r17_exclusion.proof_failures); there is no second filter.
@pytest.mark.parametrize("field,value", [
    ("entry_price_source", "MARKET_MICROSTRUCTURE_1S"),
    ("entry_price_source", " market_microstructure_1s "),
    ("entry_price_source", "BOOK_TAPE_1S"),
    ("entry_price_source", "TICKER"),
    ("r17_entry_status", "R17_UNPROVEN_ENTRY"),
    ("r17_unproven_reason", "R17_UNPROVEN_ENTRY"),
])
def test_entry_priced_on_1s_tape_is_never_countable_even_with_full_proof(field, value):
    row = good_row(trade_id="gs1-dddddddddddd", **{field: value})
    assert r17.non_countable_reason(row) == "R17_UNPROVEN:ENTRY_PRICED_ON_1S_TAPE"
    assert r17.stamp_on_write(dict(row))["fresh_eligible"] is False
    assert paper_fill.row_fresh_eligible(row) is False


def test_book_entry_price_source_is_not_flagged():
    for value in ("BFX_BOOK_10L", "book", None, ""):
        assert r17.non_countable_reason(good_row(entry_price_source=value)) is None


def test_entry_priced_on_1s_tape_reason_lives_in_the_one_contract_module():
    import r17_fields
    import research_v3_candidates
    assert r17_fields.REASON_ENTRY_PRICED_ON_1S_TAPE == "ENTRY_PRICED_ON_1S_TAPE"
    assert set(r17_fields.FORBIDDEN) == {"entry_price_source", *r17_fields.ENTRY_STATUS_FIELDS}
    # the stamper imports the constants from the contract module; it does not define its own (R4)
    assert research_v3_candidates.R17_UNPROVEN_ENTRY is r17_fields.R17_UNPROVEN_ENTRY
    import book_limit_entry
    assert book_limit_entry.R17_UNPROVEN_ENTRY is r17_fields.R17_UNPROVEN_ENTRY
    # #848: the entry is book-priced now; no 1s source constant is left in the stamper
    assert book_limit_entry.ENTRY_PRICE_SOURCE_BOOK not in r17_fields.ENTRY_PRICE_SOURCES_1S
    for name in ("research_v3_candidates.py", "book_limit_entry.py"):
        src = (ROOT / name).read_text()
        assert 'R17_UNPROVEN_ENTRY = "' not in src and 'ENTRY_PRICE_SOURCE_1S' not in src


def _rows_as_research_v3_candidates_emits_them():
    """The receipt and the outcome research_v3_candidates really emits for a 1s-priced CONSERVATIVE entry."""
    import test_research_v3_candidates as T
    import research_v3_candidates as C
    src = T.conservative_source(visible_qty=0.25)
    receipt = C._conservative_child_receipt(src, src["entry_children"][0])
    store = C._ProtectionCellStore()
    cell = (C._KIND_REPLAYED, C._ENTRY_UNPROVEN_PREFIX + "PARTIAL_FILL", "ATR_TP", 1.5, 0.5, 0.1, 0.2)
    diag = (C._KIND_NO_FILL, None, None, float("nan"))
    store.append(0, 0, 0, 0, store.encode_conservative(cell), store.encode_diagnostic(diag))
    outcome = store.conservative_outcome(0, "BOOK_WALK", {})
    receipt = dict(receipt)
    # #848 prices the entry on the book, so a fresh receipt is no longer 1s-priced; stamp it the way pre-#848
    # (legacy, still on disk) receipts are, so the test exercises exactly those rows.
    import r17_fields
    receipt["entry_price_source"] = r17_fields.ENTRY_PRICE_SOURCE_1S
    return {"receipt": receipt, "outcome": dict(outcome)}


@pytest.mark.parametrize("which", ["receipt", "outcome"])
def test_845_fail_closed_rule_alone_already_rejects_1s_priced_entries(which, monkeypatch):
    """Defence in depth: with the ENTRY_PRICED_ON_1S_TAPE check switched OFF, #845's positive contract by itself
    still rejects the rows research_v3_candidates emits for a 1s-priced entry (no book trigger, no measured delay,
    no book walk, no stop coverage)."""
    row = _rows_as_research_v3_candidates_emits_them()[which]
    with_check = r17.proof_failures(row)
    assert "ENTRY_PRICED_ON_1S_TAPE" in with_check
    monkeypatch.setattr(r17, "entry_priced_on_1s_tape", lambda _row: False)
    alone = r17.proof_failures(row)
    assert "ENTRY_PRICED_ON_1S_TAPE" not in alone
    assert {"TRIGGER_SOURCE_MISSING", "DELAY_SOURCE_MISSING", "ENTRY_PRICE_AGE_MISSING",
            "STOP_COVERAGE_MISSING"} <= set(alone)
    assert r17.non_countable_reason(row).startswith("R17_UNPROVEN:")


def test_entry_proof_failures_is_the_entry_subset_of_proof_failures():
    """HM #848 C3: research entry receipts gate on the same entry rule the row check uses (one module)."""
    assert r17.entry_proof_failures(good_row()) == []
    bad = good_row(trigger_price_source="ticker", delay_source="default", evidence_status="INCOMPLETE",
                   entry_book_age_ms=None, entry_price_source="market_microstructure_1s")
    entry = r17.entry_proof_failures(bad)
    assert entry == ["TRIGGER_NOT_BOOK", "DELAY_NOT_MEASURED", "ENTRY_EVIDENCE_NOT_COMPLETE",
                     "ENTRY_PRICE_AGE_MISSING", "ENTRY_PRICED_ON_1S_TAPE"]
    assert set(entry) <= set(r17.proof_failures(bad))
    exit_only = good_row(exit_walk_bp=None, stop_coverage=None)
    assert r17.entry_proof_failures(exit_only) == [] and r17.proof_failures(exit_only)


def test_c4_every_1s_limit_fill_receipt_is_stamped_unproven_in_one_place():
    """HM #848 C4: conservative_limit_fill (1s tape) stamps R17_UNPROVEN_ENTRY + ENTRY_PRICED_ON_1S_TAPE for every
    caller (analyzer v62, entry_baseline_replay, policy_evidence_evaluator, latency_schedule_replay,
    conservative_fill_cohort); the analyzer tape join carries it at top level too."""
    import r17_fields
    from research.conservative_limit_fill import evaluate_limit_fill
    r = evaluate_limit_fill([], direction="LONG", requested_qty=1.0, chase_schedule=[])
    assert r["entry_price_source"] == r17_fields.ENTRY_PRICE_SOURCE_1S
    assert r["r17_entry_status"] == r17_fields.R17_UNPROVEN_ENTRY
    assert r["r17_entry_reason"] == r17_fields.REASON_ENTRY_PRICED_ON_1S_TAPE
    assert "ENTRY_PRICED_ON_1S_TAPE" in r17.proof_failures(good_row(**{k: r[k] for k in (
        "entry_price_source", "r17_entry_status")}))
    src = (ROOT / "analyzer_research_engine_v62.py").read_text()
    join = src[src.index('"schema": "analyzer_compressed_chase_tape_join_v2"'):]
    join = join[:join.index('"conservative_evaluator_receipt": evaluator')]
    assert '"r17_entry_status": "R17_UNPROVEN_ENTRY"' in join and '"ENTRY_PRICED_ON_1S_TAPE"' in join
    assert r17_fields.R17_UNPROVEN_ENTRY == "R17_UNPROVEN_ENTRY" and r17_fields.ENTRY_PRICE_SOURCE_1S == "MARKET_MICROSTRUCTURE_1S"

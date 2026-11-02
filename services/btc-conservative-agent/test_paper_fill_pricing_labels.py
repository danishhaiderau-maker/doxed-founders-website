"""Pricing labels, fail-closed counting and the by-model route (HM 18 Oct 2026 amendments 1-5)."""
import ast
import json
from pathlib import Path

import pytest

import fills_by_model
import monitor_gaps
import monitor_tiles
import paper_fill
from research import fill_model as research_fill_model
import research_v3_bridge


@pytest.fixture(autouse=True)
def _r17_proof_rule_out_of_scope(monkeypatch):
    """R17 proof rule (countable only with book/delay/walk/level proof) is covered by test_r17_exclusion.py.
    These synthetic rows test other properties, so the proof check is neutralised here; the pinned-list and
    stamped exclusions still apply."""
    import r17_exclusion
    monkeypatch.setattr(r17_exclusion, "proof_failures", lambda row: [])


ROOT = Path(__file__).resolve().parent
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
EPOCH = 1792263671.26
MODEL = paper_fill.FILL_MODEL


@pytest.fixture(autouse=True)
def _epoch():
    old = paper_fill.FRESH_EPOCH_TS
    paper_fill.FRESH_EPOCH_TS = EPOCH
    yield
    paper_fill.FRESH_EPOCH_TS = old


def good_row(**extra):
    row = {"research_lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP", "exit_status": "PAPER_FILL", "fill_model": MODEL,
           "fill_model_version": 1, "entry_ts": EPOCH + 60, "exit_ts": EPOCH + 120, "fill_valid": True,
           "all_legs_book10l": True}
    row.update(extra)
    return row


def test_default_paper_fill_model_is_book10l_and_realistic_on_flips_off():
    tree = ast.parse(BOT)
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "PAPER_FILL_MODEL")
    getenv = next(n for n in ast.walk(node.value) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "getenv")
    default = ast.literal_eval(getenv.args[1])
    assert default == MODEL
    assert (default.strip().upper() == research_fill_model.FILL_MODEL_VERSION) is False  # realistic_on (:25350)
    assert "REALISTIC_V1" not in default
    assert research_fill_model.FILL_MODEL_VERSION == "REALISTIC_V1"  # the flag only turns on when explicitly set


def test_receipt_uses_pricer_evidence_not_the_venue_gate():
    order = {"fill_sim": {"fill_model": MODEL, "evidence_status": "COMPLETE", "book_s": 1792300000, "price_age_s": 0.5},
             "filled_qty": 0.0003, "qty": 0.0003}
    rec = research_v3_bridge._paper_fill_execution_receipt(order, {"qty": 0.0003}, {})
    assert rec["fill_model"] == MODEL and rec["fill_basis"] == "BOOK10L_TAKER_WALK"
    assert rec["execution_basis"] == f"{MODEL}:BOOK10L_TAKER_WALK" and rec["conservative_fill_supported"] is True
    unstamped = {**order, "fill_sim": {"fill_model": MODEL, "evidence_status": "COMPLETE"}}  # RK #882 R3: no book time/age
    assert research_v3_bridge._paper_fill_execution_receipt(unstamped, {"qty": 0.0003}, {})["conservative_fill_supported"] is False
    legacy = research_v3_bridge._paper_fill_execution_receipt({"qty": 1.0}, {"qty": 1.0}, {})
    assert legacy["execution_basis"] == "UNSUPPORTED"  # untouched for non-paper orders


def test_tile_fill_block_prefers_pricer_evidence():
    raw = {"entry_fill_sim": {"fill_model": MODEL, "evidence_status": "COMPLETE"}}
    block = monitor_tiles._fill_block({"fill_basis": None, "execution_basis": "UNSUPPORTED"}, filled=True, raw=raw)
    assert block["fill_basis"] == "BOOK10L_TAKER_WALK" and block["execution_basis"].endswith(":BOOK10L_TAKER_WALK")
    assert block["fill_model"] == MODEL and block["liquidity"] == "TAKER"
    plain = monitor_tiles._fill_block({}, filled=True, raw={})
    assert plain["fill_basis"] == monitor_tiles.FILL_BASIS_NOT_INDEXED


def test_row_fresh_strict_requires_every_leg_and_validity():
    assert paper_fill.row_fresh_strict(good_row())
    assert not paper_fill.row_fresh_strict(good_row(fill_valid=False))
    assert not paper_fill.row_fresh_strict(good_row(non_fill_category="NO_BOOK_EXIT"))
    assert not paper_fill.row_fresh_strict(good_row(exit_fallback="LEGACY_BBO_FALLBACK_NO_BOOK"))
    assert not paper_fill.row_fresh_strict(good_row(all_legs_book10l=False))
    assert not paper_fill.row_fresh_strict(good_row(all_legs_book10l="False"))  # CSV reload string
    leg = {"realized_gross_usd": 0.1, "leg_priced_by": "paper_fill_book10l", "leg_fresh_eligible": True}
    assert paper_fill.row_fresh_strict(good_row(partial_exit_receipts=[leg]))
    assert not paper_fill.row_fresh_strict(good_row(partial_exit_receipts=[{**leg, "leg_priced_by": None}]))
    assert not paper_fill.row_fresh_strict(good_row(partial_exit_receipts=[{**leg, "leg_fallback": "TRIGGER_PRICE_UNPRICED_LEG"}]))
    assert not paper_fill.row_fresh_strict(good_row(partial_exit_receipts=json.dumps([{**leg, "leg_fresh_eligible": False}])))
    assert not paper_fill.row_fresh_strict(good_row(exit_fill_sim={"fill_model": "REALISTIC_V1"}))


def test_rows_without_a_timestamp_fail_closed_as_pre_epoch():
    row = good_row()
    for k in ("entry_ts", "exit_ts"):
        row.pop(k)
    assert paper_fill.row_before_epoch(row) is True
    assert not paper_fill.row_fresh_strict(row)
    assert paper_fill.row_before_epoch({"entry_ts": EPOCH - 1}) is True
    assert paper_fill.row_before_epoch({"entry_ts": EPOCH + 1}) is False
    paper_fill.FRESH_EPOCH_TS = None
    assert paper_fill.row_before_epoch({}) is False  # no stamped epoch: nothing to compare against


def test_by_model_categories_never_mix_with_countable():
    lane = "FAMILY_GS01_XV_PREMIUM_ATR_TP"
    rows = [good_row(), good_row(non_fill_category="NO_BOOK_EXIT", exit_fallback="LEGACY_BBO_FALLBACK_NO_BOOK"),
            good_row(non_fill_category="TP1_UNPRICED_LEG", fill_valid=False), good_row(fill_model="REALISTIC_V1"),
            good_row(fill_model_invalid=True), good_row(entry_ts=EPOCH - 5, exit_ts=EPOCH - 1),
            good_row(all_legs_book10l=False), {"research_lane": lane, "exit_reason": "X"}]
    out = fills_by_model.summarise(rows, [lane], refused={"count": 2})
    cat = out["tiles"][lane]["by_category"]
    assert cat["countable"] == 1 and cat["no_book_exit"] == 1 and cat["tp1_unpriced_leg"] == 1
    assert cat["other_fill_model"] == 1 and cat["fill_model_invalid"] == 1 and cat["pre_epoch"] == 1
    assert cat["not_fresh_book10l"] == 1 and cat["legacy_pricing"] == 1
    assert out["tiles"][lane]["countable"] == 1 and out["non_paper_fills_refused"] == {"count": 2}


def test_taker_book_walk_fills_never_raise_fill_outside_bbo():
    counters = monitor_gaps.FillSimCounters()
    counters.taker_fill("FAMILY_GS01_XV_PREMIUM_ATR_TP", 81558.0, 81600.0, 81601.0, "SHORT")  # 3 bp away, a depth walk
    snap = counters.snapshot()
    assert snap["alerts"] == [] and snap["level"] == monitor_gaps.GREEN
    assert snap["lanes"]["FAMILY_GS01_XV_PREMIUM_ATR_TP"]["taker_book_walk_fills"] == 1
    assert snap["lanes"]["FAMILY_GS01_XV_PREMIUM_ATR_TP"]["maker_fills"] == 0
    counters.maker_fill("MAKER_LANE", 81558.0, 81600.0, 81601.0, "SHORT")
    assert "FILL_OUTSIDE_BBO_GT_1BP" in counters.snapshot()["alerts"]
    assert "_FILL_SIM_COUNTERS.taker_fill(" in BOT and "if _is_paper_book_lane(order):" in BOT


def test_tile_integrity_presence_check_registers_rows_since_the_epoch():
    import tile_integrity
    assert tile_integrity.presence_check([], None)["note"] == "V1_FILL_MODEL_NOT_YET_EMITTED"
    assert "v1_since_ts=_tile_integrity_v1_since()" in BOT
    assert "_pf.FRESH_EPOCH_TS" in BOT.split("def _tile_integrity_v1_since")[1][:700]


def _exec_refuse(order_lane, enabled=True):
    tree = ast.parse(BOT)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_refuse_non_paper_fill")
    ns = {"REFUSE_NON_PAPER_BOOK_FILLS": enabled, "_NON_PAPER_FILL_REFUSED": {"count": 0, "last_lane": None}}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), ns)
    order = {"research_lane": order_lane}
    return ns["_refuse_non_paper_fill"](order, "t"), order, ns["_NON_PAPER_FILL_REFUSED"]


def test_non_paper_orders_are_refused_and_tagged_invalid():
    refused, order, counter = _exec_refuse("SOME_LEGACY_LANE")
    assert refused is True and order["fill_model_invalid"] is True and counter["count"] == 1
    assert order["fill_invalid_reason"].startswith("NON_PAPER_BOOK_LANE_LEGACY_PRICER_REFUSED")
    assert _exec_refuse("SOME_LEGACY_LANE", enabled=False)[0] is False  # helper contract only
    # pinned: the module constant is a literal True, not env-switchable
    tree = ast.parse(BOT)
    node = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "REFUSE_NON_PAPER_BOOK_FILLS")
    assert isinstance(node.value, ast.Constant) and node.value.value is True


def test_no_book_exit_marks_and_unpriced_leg_are_non_fills():
    # REALISM_V2 F4: the inline forced path marks NO_BOOK through the shared _paper_unpriced_marks (one source)
    assert '_paper_unpriced_marks(t0, why="INLINE_NO_WAIT_NO_BOOK")' in BOT
    assert BOT.count('"fill_valid": False,\n            "non_fill_category": "NO_BOOK_EXIT"') == 1
    assert 'pos["non_fill_category"] = "TP1_UNPRICED_LEG"' in BOT
    assert '"fresh_epoch_id": _fresh_epoch_dv()' in BOT and '"all_legs_book10l": _all_legs_book10l(pos)' in BOT


def test_analyzer_uses_the_same_strict_rule_as_the_monitors():
    src = (ROOT / "analysis_hygiene.py").read_text(encoding="utf-8")
    assert "_pf.row_fresh_strict(rec)" in src


def test_by_model_route_is_registered_and_read_only():
    assert '@app.route(\'/api/monitor/fills/by-model\', methods=["GET"])' in BOT
    assert '"/api/monitor/fills/by-model"' in BOT.split("_MONITOR_TILE_PATHS")[1][:2000]


def _book(px_a=100.0, px_b=99.0):
    return lambda t: {"s": int(t) - 1, "a": [[px_a, 10.0]], "b": [[px_b, 10.0]],
                      "receive_ts": int(t) - 0.5, "receive_mono": 1.0, "receive_ts_source": "WS_FRAME"}  # #851


def test_pricer_emits_explicit_evidence_status():
    full = paper_fill.paper_fill("LONG", 0.5, 1000.0, 1.0, _book())
    assert full["status"] == "FILLED" and full["evidence_status"] == "COMPLETE"
    part = paper_fill.paper_fill("LONG", 50.0, 1000.0, 1.0, _book())
    assert part["status"] == "PARTIAL" and part["evidence_status"] == "INCOMPLETE"
    none = paper_fill.paper_fill("LONG", 0.5, 1000.0, 1.0, lambda t: None)
    assert none["evidence_status"] == "NO_FILL"


def test_receipt_requires_explicit_complete_evidence():
    for sim in ({"fill_model": MODEL}, {"fill_model": MODEL, "evidence_status": "UNKNOWN"},
                {"fill_model": MODEL, "evidence_status": "INCOMPLETE"}, {"fill_model": MODEL, "evidence_status": None}):
        rec = research_v3_bridge._paper_fill_execution_receipt(
            {"fill_sim": sim, "filled_qty": 0.0003, "qty": 0.0003}, {"qty": 0.0003}, {})
        assert rec["conservative_fill_supported"] is False and rec["execution_basis"] == "UNSUPPORTED", sim


def test_tile_pricer_evidence_requires_explicit_complete():
    for sim in ({"fill_model": MODEL}, {"fill_model": MODEL, "evidence_status": "UNKNOWN"}):
        ev = monitor_tiles.paper_pricer_evidence({"entry_fill_sim": sim})
        assert ev["execution_basis"] == "UNSUPPORTED"
        block = monitor_tiles._fill_block({}, filled=True, raw={"entry_fill_sim": sim})
        assert block["execution_basis"] == "UNSUPPORTED"
    ok = monitor_tiles.paper_pricer_evidence({"entry_fill_sim": {"fill_model": MODEL, "evidence_status": "COMPLETE"}})
    assert ok["execution_basis"] == f"{MODEL}:BOOK10L_TAKER_WALK"


def test_exit_leg_price_is_the_filled_price_not_the_trigger():
    # shape of row gs1-5c965fbbe077: single terminal leg, receipt price = trigger 81691, filled 81697
    raw = {"partial_exit_receipts": [{"closed_qty": 0.0003, "price": 81691.0, "remaining_fraction": 0.0,
                                      "realized_gross_usd": None, "reason": "PATH_END_60M"}],
           "exit_price": 81697.0, "exit_fill_sim": {"status": "FILLED", "price": 81697.0}}
    leg = monitor_tiles._legs(raw)[0]
    assert leg["price"] == 81697.0 and leg["trigger_price"] == 81691.0
    partial = {"partial_exit_receipts": [{"closed_qty": 0.0001, "price": 81600.0, "leg_trigger_price": 81595.0,
                                          "remaining_fraction": 0.5, "realized_gross_usd": 0.01}]}
    leg = monitor_tiles._legs(partial)[0]
    assert leg["price"] == 81600.0 and leg["trigger_price"] == 81595.0


def test_by_model_labels_timestampless_rows_unstamped_not_pre_epoch():
    lane = "FAMILY_GS01_XV_PREMIUM_ATR_TP"
    nots = good_row()
    for k in ("entry_ts", "exit_ts"):
        nots.pop(k)
    rows = [nots, good_row(entry_ts=EPOCH - 5, exit_ts=EPOCH - 1), good_row()]
    cat = fills_by_model.summarise(rows, [lane])["tiles"][lane]["by_category"]
    assert cat["unstamped"] == 1 and cat["pre_epoch"] == 1 and cat["countable"] == 1
    assert fills_by_model.classify(nots) == "unstamped"


def test_tile_fill_block_exposes_pricer_evidence_status():
    sim = {"fill_model": "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY", "evidence_status": "COMPLETE"}
    assert monitor_tiles._fill_block({}, filled=True, raw={"entry_fill_sim": sim})["evidence_status"] == "COMPLETE"
    inc = dict(sim, evidence_status="INCOMPLETE")
    assert monitor_tiles._fill_block({}, filled=True, raw={"entry_fill_sim": inc})["evidence_status"] == "INCOMPLETE"
    assert monitor_tiles._fill_block({}, filled=True, raw={})["evidence_status"] is None

def _result_as_sim():
    tree = ast.parse(BOT)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_paper_result_as_sim")
    ns = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "bot.py", "exec"), ns)
    return ns["_paper_result_as_sim"]


def test_partial_entry_is_deliberately_complete_and_no_fill_is_unknown():
    fn = _result_as_sim()
    book = _book()
    partial = fn(paper_fill.paper_fill("LONG", 50.0, 1000.0, 1.0, book))
    assert partial["partial_fill"] is True and partial["evidence_status"] == "COMPLETE"  # position sized to filled_qty
    full = fn(paper_fill.paper_fill("LONG", 0.5, 1000.0, 1.0, book))
    assert full["fully_filled"] is True and full["evidence_status"] == "COMPLETE"
    nofill = fn(paper_fill.paper_fill("LONG", 0.5, 1000.0, 1.0, lambda t: None))
    assert nofill["evidence_status"] == "UNKNOWN"
    rec = research_v3_bridge._paper_fill_execution_receipt(
        {"fill_sim": nofill, "filled_qty": 0.0, "qty": 0.5}, {"qty": 0.5}, {})
    assert rec["conservative_fill_supported"] is False


def test_multi_leg_row_tp1_plus_terminal_is_strict_fresh_with_filled_leg_prices():
    row = good_row(
        partial_exit_receipts=[
            {"closed_qty": 0.0001, "price": 81600.0, "leg_trigger_price": 81595.0, "remaining_fraction": 0.5,
             "realized_gross_usd": 0.01, "leg_priced_by": "paper_fill_book10l", "leg_fresh_eligible": True,
             "leg_fallback": None, "reason": "TP1"},
            {"closed_qty": 0.0001, "price": 81691.0, "remaining_fraction": 0.0, "realized_gross_usd": None,
             "reason": "PATH_END_60M"}],
        exit_price=81650.0, exit_fill_sim={"status": "FILLED", "price": 81697.0, "fill_model": MODEL,
                                           "evidence_status": "COMPLETE"})
    assert paper_fill.row_fresh_strict(row) is True
    legs = monitor_tiles._legs(row)
    assert [leg["price"] for leg in legs] == [81600.0, 81697.0]  # partial: its fill; terminal: filled, not trigger
    assert [leg["trigger_price"] for leg in legs] == [81595.0, 81691.0]
    bad = dict(row, partial_exit_receipts=[dict(row["partial_exit_receipts"][0], leg_priced_by=None),
                                           row["partial_exit_receipts"][1]])
    assert paper_fill.row_fresh_strict(bad) is False  # one unpriced TP1 leg poisons the whole row


def test_closed_row_flat_evidence_status_reaches_the_tile_fill_block():
    blk = monitor_tiles._fill_block({}, filled=True, raw={"evidence_status": "COMPLETE"})
    assert blk["evidence_status"] == "COMPLETE"
    assert monitor_tiles._fill_block({}, filled=True, raw={})["evidence_status"] is None


def test_tile_trade_row_exposes_numeric_stamps_fresh_id_and_flat_evidence():
    raw = {"trade_id": "t1", "research_lane": "FAMILY_GS01_XV_PREMIUM_ATR_TP", "entry_ts": 1500.0, "exit_ts": 1600.0,
           "fresh_epoch_id": "fse-1000", "evidence_status": "COMPLETE", "fill_model": "BOOK10L_WALK_AT_SIGNAL_PLUS_DELAY",
           "close_ts": "2026-10-18T00:00:00Z", "exit_price": 100.0, "entry": 99.0, "dir": "LONG"}
    row = _closed_tile_row_for(raw)
    assert row["entry_ts"] == 1500.0 and row["exit_ts"] == 1600.0 and row["exit_ts"] >= row["entry_ts"]
    assert row["fresh_epoch_id"] == "fse-1000" and row["fill"]["evidence_status"] == "COMPLETE"
    none_row = _closed_tile_row_for(dict(raw, entry_ts=None, exit_ts=None))
    assert none_row["entry_ts"] is None and none_row["exit_ts"] is None


def _closed_tile_row_for(raw):
    return monitor_tiles.tile_trade_row(raw, status="closed", short_names={}, epoch_id="e", forced_reasons=(),
                                        parse_ts=paper_fill_parse_ts)


def paper_fill_parse_ts(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

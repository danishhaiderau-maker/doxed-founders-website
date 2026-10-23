"""R17 contract (#845 r17_exclusion.proof_failures): REALISM_V2 closed rows carry every proof field under the exact
contract names - trigger_price_source, delay_source (both legs), entry/exit walk + price age, levels_crossed_unfired,
stop_coverage - and an unknown answer is None (fails as UNKNOWN), never a fabricated []."""
import ast
import pathlib
import time

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
    return False


def _ns(names, paper=True, **extra):
    ns = {"time": time, "_is_paper_book_lane": lambda pos: paper, **extra}
    for name in names:
        node = next(n for n in _tree().body if _defines(n, name))
        exec(compile(ast.Module([node], []), "bot.py", "exec"), ns)
    return ns


HELPERS = ("LEVELS_CROSSED_UNFIRED_MAX", "_note_level_crossed_unfired", "_stop_coverage_value",
           "_levels_crossed_unfired", "_finite_nonneg", "_r17_exit_book_age_ms", "_exit_book_age_ms", "_r17_contract_fields",
           "_book_receive_row_fields")


def _clean_pos(**kw):
    pos = {"trigger_price_source": "book", "exit_priced_delay_source": "lane_q90_n12", "exit_price_age_s": 0.4,
           "exit_fill_sim": {"price_stale": False, "book_receive_ts": 1100.4, "book_receive_mono": 150.4,
                             "book_receive_ts_source": "WS_FRAME"}, "stop_tail_lo_s": 90, "stop_tail_hi_s": 200,
           "levels_scan_from_s": 90.0, "levels_scan_to_s": 200.0, "levels_scan_rows_n": 110}
    pos.update(kw)
    return pos


def _proof_failures(row):
    """Mirror of #845 r17_exclusion.proof_failures (feat/r17-dynamic-exclusion @99554e61) for the fields stamped here."""
    import re
    out = []
    if str(row.get("trigger_price_source") or "").lower() != "book":
        out.append("TRIGGER")
    s = str(row.get("exit_priced_delay_source") or "")
    m = re.fullmatch(r"lane_(?:max|q90)_n(\d+)", s)
    if row.get("exit_pricing_basis") != "EXCHANGE_RESIDENT_FIRST_ROW_AT_OR_AFTER_TRIGGER" and not (
            s == "EXCHANGE_RESIDENT_NO_DELAY" or (m and int(m.group(1)) >= 10)):
        out.append("EXIT_DELAY")
    if not isinstance(row.get("exit_price_age_s"), (int, float)) and not isinstance(row.get("exit_eval_price_age_s"), (int, float)):
        out.append("EXIT_AGE")
    if "entry_book_age_ms" in row and not isinstance(row.get("entry_book_age_ms"), (int, float)):
        out.append("ENTRY_AGE")
    if row.get("exit_price_stale") is True:
        out.append("EXIT_STALE")
    v = row.get("levels_crossed_unfired")
    if v is None:
        out.append("LEVEL_CROSS_UNKNOWN")
    elif v not in ([], 0, False, "[]", "0", "false"):
        out.append("LEVEL_CROSSED_UNFIRED")
    if str(row.get("stop_coverage") or "").upper() in ("STOP_COVERAGE_GAP", "STOP_COVERAGE_UNVERIFIED"):
        out.append("STOP_COVERAGE_GAP")
    return out


def test_clean_paper_row_carries_contract_names_and_passes():
    row = _ns(HELPERS)["_r17_contract_fields"](_clean_pos())
    assert set(row) == {"trigger_price_source", "exit_priced_delay_source", "exit_price_age_s",
                        "exit_eval_price_age_s", "exit_price_stale", "levels_crossed_unfired", "stop_coverage",
                        "exit_book_age_ms", "exit_book_receive_ts", "exit_book_receive_mono",
                        "exit_book_receive_ts_source"}
    assert row["exit_book_age_ms"] == 400 and row["exit_book_receive_ts_source"] == "WS_FRAME"
    assert row["levels_crossed_unfired"] == [] and row["stop_coverage"] == qt.STOP_COVERAGE_OK
    assert row["exit_price_stale"] is False
    assert _proof_failures(row) == []


def test_stop_coverage_gap_and_unverified():
    f = _ns(HELPERS)
    assert f["_stop_coverage_value"](_clean_pos(stop_coverage_flag=qt.STOP_COVERAGE_GAP, stop_coverage_gap_s=3.0)) \
        == qt.STOP_COVERAGE_GAP
    assert f["_stop_coverage_value"](_clean_pos(stop_tail_lo_s=None, stop_tail_hi_s=None)) == qt.STOP_COVERAGE_UNVERIFIED
    row = f["_r17_contract_fields"](_clean_pos(stop_coverage_gap_s=3.0))
    assert row["stop_coverage"] == qt.STOP_COVERAGE_GAP and row["levels_crossed_unfired"] is None
    assert "STOP_COVERAGE_GAP" in _proof_failures(row) and "LEVEL_CROSS_UNKNOWN" in _proof_failures(row)


def test_missing_evidence_is_unknown_never_empty():
    f = _ns(HELPERS)
    for kw in ({"trigger_gap_seconds_n": 2}, {"stale_book_since_s": 150.0}, {"stale_book_age_s": 3.5}):
        assert f["_levels_crossed_unfired"](_clean_pos(**kw)) is None, kw


def test_levels_crossed_unfired_is_none_unless_the_hold_scan_ran():
    """HM (c): [] means "the real hold scan ran and nothing was crossed"; a position that never ran it answers None."""
    f = _ns(HELPERS)
    pos = _clean_pos()
    for k in ("levels_scan_from_s", "levels_scan_to_s", "levels_scan_rows_n"):
        pos.pop(k)
    assert f["_levels_crossed_unfired"](pos) is None
    row = f["_r17_contract_fields"](pos)
    assert row["levels_crossed_unfired"] is None and "LEVEL_CROSS_UNKNOWN" in _proof_failures(row)
    assert f["_levels_crossed_unfired"](_clean_pos()) == []


def test_stale_book_held_level_is_recorded_as_crossed_unfired():
    f = _ns(HELPERS)
    pos = _clean_pos()
    f["_note_level_crossed_unfired"](pos, "TP1", 60100.0, 150.0, "STALE_BOOK_HELD")
    got = f["_levels_crossed_unfired"](pos)
    assert got == [{"reason": "TP1", "why": "STALE_BOOK_HELD", "px": 60100.0, "ts": 150.0}]
    assert "LEVEL_CROSSED_UNFIRED" in _proof_failures(f["_r17_contract_fields"](pos))


def test_live_rows_are_not_rejudged():
    row = _ns(HELPERS, paper=False)["_r17_contract_fields"](_clean_pos())
    assert row["levels_crossed_unfired"] is None and row["stop_coverage"] is None and row["exit_price_stale"] is None


def test_wired_into_closed_row_entry_fields_and_stale_discard():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    cp = next(ast.get_source_segment(src, n) for n in _tree().body
              if isinstance(n, ast.FunctionDef) and n.name == "close_position")
    assert "**_r17_contract_fields(pos)" in cp
    epf = next(ast.get_source_segment(src, n) for n in _tree().body
               if isinstance(n, ast.FunctionDef) and n.name == "_entry_pricing_fields")
    assert '"entry_price_age_s": (efs.get("price_age_s")' in epf and '"entry_price_stale"' in epf
    fam = next(ast.get_source_segment(src, n) for n in _tree().body
               if isinstance(n, ast.FunctionDef) and n.name == "_apply_family_tile_exit")
    assert '_note_level_crossed_unfired(source_pos' in fam and '"STALE_BOOK_HELD"' in fam
    assert qt.STOP_COVERAGE_VALUES == ("STOP_COVERAGE_OK", "STOP_COVERAGE_GAP", "STOP_COVERAGE_UNVERIFIED")


def _good_closed_row():
    """A clean REALISM_V2 paper close assembled from the REAL closed-row helpers (entry pricing, R17 contract fields)
    plus the shared-walk slippage / exit-evidence values those helpers sit next to in close_position."""
    efs = {"fill_model": "BFX_BOOK_10L", "fill_model_version": 3, "delay_s": 1.2, "delay_source": "lane_q90_n14",
           "pool_n": 14, "delay_unverified": False, "price_age_s": 0.6, "price_stale": False,
           "book_age_ms": 600, "evidence_status": "COMPLETE",
           "book_receive_ts": 1000.2, "book_receive_mono": 50.2, "book_receive_ts_source": "WS_FRAME"}
    pos = _clean_pos(entry_fill_sim=efs)
    ns = _ns(HELPERS + ("_entry_pricing_fields",), _entry_fill_sim=lambda p: p.get("entry_fill_sim") or {})
    row = {"trade_id": "rv2-r17-good-1", "exit_pricing_basis": "MODEL_TIME",
           "entry_walk_bp": 0.8, "exit_walk_bp": 1.1, "exit_legs_unmeasured": 0, "exit_evidence_status": "COMPLETE",
           "exit_unverified_reason": None,
           # RK/HM K1: order_model.liquidation_check output with marks read and liq_px never reached
           "liq_unverified": False, "liq_mark_rows_n": 30, "liq_trigger_ts": None}
    row.update(ns["_entry_pricing_fields"](pos, {}))
    row.update(ns["_r17_contract_fields"](pos))
    return row


def test_good_closed_row_has_entry_age_from_the_pricer():
    row = _good_closed_row()
    assert row["entry_price_age_s"] == 0.6 and row["entry_price_stale"] is False
    assert row["entry_book_age_ms"] == 600
    assert row["delay_source"] == "lane_q90_n14" and row["trigger_price_source"] == "book"
    assert _proof_failures(row) == []


def test_good_closed_row_passes_r17_exclusion():
    """Cross-PR (#845 feat/r17-dynamic-exclusion): runs the real r17_exclusion.proof_failures once it is on the path."""
    import pytest
    r17 = pytest.importorskip("r17_exclusion")
    row = _good_closed_row()
    assert r17.proof_failures(row) == []
    bad = dict(row, stop_coverage=qt.STOP_COVERAGE_GAP, levels_crossed_unfired=None)
    fails = r17.proof_failures(bad)
    assert "LEVEL_CROSS_UNKNOWN" in fails and any(f.startswith("STOP_COVERAGE") for f in fails)
    assert r17.non_countable_reason(row, ids=set(), load_errors=[]) is None


def test_good_closed_row_carries_every_r17_fields_contract_key():
    """Cross-PR (#845 r17_fields.REQUIRED_FIELDS): every contract field is a key of the closed row."""
    import pytest
    fields = pytest.importorskip("r17_fields")
    assert set(fields.REQUIRED_FIELDS) <= set(_good_closed_row()), set(fields.REQUIRED_FIELDS) - set(_good_closed_row())


def test_stop_coverage_is_separate_from_the_live_stop_coverage_verified_flag():
    """HM (d): the paper R17 `stop_coverage` value never reads or writes the live-copy `stop_coverage_verified`
    protection flag, and the live gate never reads the paper `stop_coverage` row field."""
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    fns = {n.name: ast.get_source_segment(src, n) for n in _tree().body if isinstance(n, ast.FunctionDef)}
    for name in ("_stop_coverage_value", "_r17_contract_fields", "_levels_crossed_unfired"):
        assert "stop_coverage_verified" not in fns[name], name
    for name, body in fns.items():
        if "stop_coverage_verified" in body:
            assert '"stop_coverage"]' not in body and 'get("stop_coverage")' not in body, name


def test_exit_book_age_ms_comes_from_the_exit_pricer_and_is_finite_nonneg():
    """#845 @470e9cd3 HM R2/R3: exit_book_age_ms = the exit pricer's book_age_ms on latest-row legs (TP/BE/time,
    BOT_SENT MODEL_TIME), price_age_s*1000 on first-row legs; never negative / non-finite / from a top-of-book fallback."""
    import paper_fill as pf
    f = _ns(HELPERS)
    latest = {"price_source": "BFX_BOOK_10L", "book_row_rule": pf.ROW_RULE_LATEST, "book_age_ms": 640, "price_age_s": None}
    assert f["_exit_book_age_ms"](latest) == 640
    first = {"price_source": "BFX_BOOK_10L", "book_row_rule": pf.ROW_RULE_FIRST_AFTER, "book_age_ms": -300,
             "price_age_s": 0.7}
    assert f["_exit_book_age_ms"](first) == 700
    assert f["_exit_book_age_ms"](dict(latest, price_source="BFX_BOOK_TOP_WS")) is None
    for bad in (float("nan"), float("inf"), -1, None, "640", True):
        assert f["_exit_book_age_ms"](dict(latest, book_age_ms=bad)) is None, bad
        assert f["_r17_exit_book_age_ms"]({"exit_book_age_ms": bad, "exit_price_age_s": None}) is None, bad
    assert f["_r17_exit_book_age_ms"]({"exit_book_age_ms": None, "exit_price_age_s": 0.25}) == 250
    row = f["_r17_contract_fields"](_clean_pos(exit_price_age_s=None, exit_book_age_ms=640))
    assert row["exit_price_age_s"] is None and row["exit_book_age_ms"] == 640

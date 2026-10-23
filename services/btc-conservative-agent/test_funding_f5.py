"""REALISM_V2 F5: settlement-exact funding from bfx_deriv_status_1s (HM 23 Oct; RK raw-field condition).

Paid only at 00/08/16 UTC settlements with entry_fill_ts < t <= exit_fill_ts; amount = sign x qty(t) x MARK(t) x
the applied rate (CURRENT_FUNDING of the last status row before t, confirmed by the next_fund_ts flip). No
pro-rating, no predicted state rate, no fee term. Non-crossers book exactly 0. String-form funding_ts_crossed
('[]', '[1792310400]') is JSON-parsed by every reader.
"""
import ast
import json
from pathlib import Path

import paper_fill as pf
import tile_fill as tf

ROOT = Path(__file__).resolve().parent
T = 1792310400  # a real 08:00 UTC settlement (one of the 13 string-form crossings)


def _rows(t, n=90, rate_before=0.0001, rate_after=0.00002, mark_before=80000.0, mark_after=80100.0, flip=True):
    out = []
    for s in range(t - n, t + n):
        pre = s < t
        out.append({"s": s, "mts": (s + 0.4) * 1000, "mark": mark_before if pre else mark_after,
                    "fund_cur": rate_before if pre else rate_after, "fund_accr": 0.00009 if pre else 0.0,
                    "next_fund_ts": t * 1000 if (pre or not flip) else (t + 28800) * 1000})
    return out


def _write(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_settlement_from_rows_uses_last_pre_row_and_flip():
    d = pf._settlement_from_rows(T, _rows(T))
    assert d["rate_applied"] == 0.0001 and d["rate_accrued"] == 0.00009
    assert d["mark_price"] == 80000.0 and d["mark_row_s"] == T - 1 and d["rate_row_s"] == T - 1
    assert abs(d["rate_ts"] - (T - 1 + 0.4)) < 1e-9 and d["confirmed"] is True and d["flip_row_s"] == T
    assert pf._settlement_from_rows(T, _rows(T, flip=False))["confirmed"] is False
    assert pf._settlement_from_rows(T, [r for r in _rows(T) if r["s"] >= T]) is None  # nothing before t


def test_file_reader_bisects_and_follows_rotation(tmp_path):
    p = tmp_path / pf.DERIV_FILE
    rows = _rows(T, n=4000)
    _write(Path(str(p) + ".1"), rows[:3990])  # rotated mid-window: the pre rows are in .1
    _write(p, rows[3990:])
    d = pf.DerivStatusFiles(str(p)).settlement(T)
    assert d["rate_applied"] == 0.0001 and d["mark_row_s"] == T - 1 and d["confirmed"] is True
    assert pf.DerivStatusFiles(str(p)).settlement(T + 28800) is None  # no rows near the next settlement


def test_long_pays_positive_rate_at_mark_short_receives():
    deriv = lambda t: pf._settlement_from_rows(t, _rows(t))
    lo = tf.funding_fields(T - 100, T + 100, direction="LONG", qty=0.5, deriv=deriv)
    assert lo["funding_ts_crossed"] == [T] and lo["funding_status"] == "PRICED" and lo["funding_verified"]
    assert lo["funding_usd_raw"] == 0.5 * 80000.0 * 0.0001  # mark at settlement, not entry notional, unrounded
    assert lo["funding_rate_applied"] == [0.0001] and lo["funding_mark_price"] == [80000.0]
    assert lo["funding_rate_ts"][0] is not None and lo["funding_mark_ts"][0] is not None
    sh = tf.funding_fields(T - 100, T + 100, direction="SHORT", qty=0.5, deriv=deriv)
    assert sh["funding_usd_raw"] == -lo["funding_usd_raw"]


def test_eligibility_is_strict_entry_inclusive_exit_and_no_prorating():
    deriv = lambda t: pf._settlement_from_rows(t, _rows(t))
    assert tf.funding_fields(T, T + 7000, direction="LONG", qty=1, deriv=deriv)["funding_ts_crossed"] == []
    at = tf.funding_fields(T - 1, T, direction="LONG", qty=1, deriv=deriv)
    assert at["funding_ts_crossed"] == [T]
    non = tf.funding_fields(T + 1, T + 28799, direction="LONG", qty=1, deriv=deriv)  # 8 h minus 2 s: no settlement
    assert non["funding_usd_raw"] == 0.0 and non["funding_status"] == "NO_CROSSING" and non["funding_verified"]
    assert isinstance(non["funding_ts_crossed"], list)


def test_two_settlements_and_partial_reduction_before_the_second():
    deriv = lambda t: pf._settlement_from_rows(t, _rows(t))
    qty_at = tf.qty_schedule(1.0, [{"remaining_fraction": 0.5, "closed_qty": 0.5, "leg_fill_ts": T + 100},
                                   {"remaining_fraction": 0.0, "closed_qty": 0.5, "leg_fill_ts": T + 30000}])
    f = tf.funding_fields(T - 10, T + 28800 + 10, direction="LONG", qty_at=qty_at, deriv=deriv)
    assert f["funding_ts_crossed"] == [T, T + 28800] and f["funding_qty"] == [1.0, 0.5]
    assert abs(f["funding_usd_raw"] - (1.0 + 0.5) * 80000.0 * 0.0001) < 1e-12


def test_missing_deriv_status_is_unpriced_and_unverified_never_zero():
    f = tf.funding_fields(T - 10, T + 10, direction="LONG", qty=1.0, deriv=lambda t: None)
    assert f["funding_usd_raw"] is None and f["funding_status"] == "UNVERIFIED_NO_DERIV_STATUS"
    assert f["funding_verified"] is False and f["funding_settlement_status"] == ["NO_DERIV_STATUS"]
    u = tf.funding_fields(T - 10, T + 10, direction="LONG", qty=1.0,
                          deriv=lambda t: pf._settlement_from_rows(t, _rows(t, flip=False)))
    assert u["funding_status"] == "UNVERIFIED_UNCONFIRMED" and u["funding_verified"] is False


def test_string_form_crossings_parse_to_lists():
    assert tf.parse_funding_ts_crossed("[]") == []
    assert tf.parse_funding_ts_crossed("[1792310400]") == [1792310400]  # one of the 13 real crossings
    assert tf.parse_funding_ts_crossed([1792339200]) == [1792339200]
    assert tf.parse_funding_ts_crossed(None) == [] and tf.parse_funding_ts_crossed("") == []
    import monitor_tiles as mt
    assert mt._restore_pricing_value("funding_ts_crossed", "[1792368000]") == [1792368000]
    assert mt._restore_pricing_value("funding_rate_applied", "[0.0001, None]") == [0.0001, None]


def test_bot_reload_and_close_path_wiring():
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    lit = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_RELOAD_LITERAL_FIELDS")
    names = {e.value for e in lit.value.elts}
    assert {"funding_ts_crossed", "funding_rate_applied", "funding_mark_price", "funding_usd_by_settlement"} <= names
    acc = src[src.index("def accrue_position_funding"):src.index("def process_funding_accrual")]
    assert "_is_paper_book_lane(pos)" in acc.split("funding_cost_for_position")[0]  # no pro-rata for tiles 1-5
    close = src[src.index("def close_position"):]
    assert "_paper_funding_v2(pos) if _is_paper_book_lane(pos)" in close
    v2 = src[src.index("def _paper_funding_v2"):src.index("def _funding_rate_now")]
    assert "_funding_rate_now" not in v2 and "state[" not in v2  # never the predicted state rate
    assert "funding_fields(entry_fill_ts, exit_fill_ts" in v2


def test_paper_funding_v2_uses_fill_times_and_original_qty(monkeypatch):
    import types
    src = (ROOT / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_paper_funding_v2")
    ns = {"time": __import__("time"), "logger": types.SimpleNamespace(exception=lambda *a, **k: None)}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "bot.py", "exec"), ns)
    monkeypatch.setattr(pf, "deriv_settlement", lambda t: pf._settlement_from_rows(t, _rows(t)))
    pos = {"dir": "SHORT", "qty": 0.25, "entry_ts": T + 5,  # booking time after t, but the FILL was before t
           "entry_fill_sim": {"fill_ts": T - 2}, "exit_fill_sim": {"fill_ts": T + 60},
           "partial_exit_receipts": [{"remaining_fraction": 0.5, "closed_qty": 0.25, "leg_fill_ts": T + 30}]}
    f = ns["_paper_funding_v2"](pos)
    assert f["funding_ts_crossed"] == [T] and f["funding_qty"] == [0.5]
    assert f["funding_usd_raw"] == -0.5 * 80000.0 * 0.0001

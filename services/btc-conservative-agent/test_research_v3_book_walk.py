"""HM 23 Oct defect 2 + RK binding (1): protection exits are repriced on bfx_book_10l rows only."""
import json

import pytest

import paper_fill
from book_walk_test_fixture import book_row, build_tape, trade_row
from research_v3_book_walk import (
    BASIS_BOOK_WALK,
    R17_UNPROVEN,
    STOP_UNOBSERVABLE_REASON,
    BookWalkTape,
    PreparedBookWalkArrays,
    coverage_summary,
    load_book_walk,
)
from research_v3_candidates import protection_screen
from research_v3_policy_replay import ReplayPlan, replay_cell

ENTRY, ATR, LEV, MARGIN = 100.0, 0.1, 100.0, 0.25


def _plan(protection_id):
    return ReplayPlan(next(p for p in protection_screen() if p["protection_id"] == protection_id))


def _replay(tape, direction, protection_id, *, fill_ts=1000.0, horizon=600):
    window = tape.window(fill_ts - 2, fill_ts + horizon)
    path = PreparedBookWalkArrays(window, direction=direction, entry_price=ENTRY, leverage=LEV,
                                  fill_ts=fill_ts, horizon_sec=horizon, qty=MARGIN * LEV / ENTRY)
    return replay_cell(path, _plan(protection_id), atr_pct_at_fill=ATR, leverage=LEV, margin_usd=MARGIN), path


def _tape(books, trades):
    return BookWalkTape.from_rows(books, trades)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_stop_triggers_on_first_trade_print_and_fills_on_first_book_row(direction):
    sign = 1 if direction == "LONG" else -1
    # Executable exit side sits 5 % margin under water: the book alone never reaches the 10 % ATR stop.
    exit_px = ENTRY - sign * 0.05
    books = [book_row(s, exit_px, exit_px + 0.05) if direction == "LONG" else book_row(s, exit_px - 0.05, exit_px)
             for s in range(990, 1700)]
    trades = [trade_row(s, ENTRY) for s in range(990, 1700, 30)]
    trades.append(trade_row(1050, ENTRY - sign * 0.11, ms=1050400))  # one print through the stop
    result, path = _replay(_tape(books, trades), direction, "ATR_TP_2.5_ATR_SL_1")
    assert result["status"] == "COMPLETE"
    assert result["exit_reason"] == "ATR_STOP"
    # Priced on the book row (5 % margin loss), never at the 11 % print that triggered it.
    assert result["net_pnl_usd"] == pytest.approx(MARGIN * -5.0 / 100.0, abs=1e-9)
    fills = list(path._fills.values())
    assert len(fills) == 1 and fills[0]["price_source"] == "BFX_BOOK_10L"
    assert fills[0]["evidence_status"] == paper_fill.EVIDENCE_COMPLETE
    assert fills[0]["trigger_ts"] == pytest.approx(1050.4)
    assert fills[0]["book_s"] == 1050


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_targets_trigger_on_book_rows_only_not_trade_prints(direction):
    sign = 1 if direction == "LONG" else -1

    def row(s):
        exit_px = ENTRY + sign * (0.30 if s >= 1100 else 0.0)
        return book_row(s, exit_px, exit_px + 0.05) if direction == "LONG" else book_row(s, exit_px - 0.05, exit_px)

    trades = [trade_row(s, ENTRY) for s in range(990, 1700, 30)]
    trades.append(trade_row(1020, ENTRY + sign * 1.0, ms=1020500))  # a far print must not take profit
    result, _ = _replay(_tape([row(s) for s in range(990, 1700)], trades), direction, "ATR_TP_2.5_ATR_SL_1")
    assert result["status"] == "COMPLETE"
    assert result["exit_reason"] not in ("PATH_END", "ATR_STOP", "PHYSICAL_HARD_STOP")
    assert result["net_pnl_usd"] == pytest.approx(MARGIN * 30.0 / 100.0, abs=1e-9)


def test_missing_book_row_after_trigger_is_data_error_never_another_price():
    books = [book_row(s, 99.95, 100.0) for s in range(990, 1700) if not 1050 <= s <= 1056]
    trades = [trade_row(s, ENTRY) for s in range(990, 1700, 30)] + [trade_row(1050, 99.8, ms=1050400)]
    result, _ = _replay(_tape(books, trades), "LONG", "ATR_TP_2.5_ATR_SL_1")
    assert result["status"] == "DATA_ERROR"
    assert result["net_pnl_usd"] is None
    assert result["reasons"] == ["EXIT_BOOK_FILL_REJECT_NO_BOOK"]


def test_coverage_requires_book_rows_and_trade_prints_over_the_whole_window():
    tape = build_tape(1000, 2000)
    assert tape.coverage(1010, 1900)["covered"] is True
    assert tape.coverage(500, 1900)["reason"] == "BOOK_ROWS_DO_NOT_COVER_WINDOW"
    assert tape.coverage(1010, 2500)["reason"] == "BOOK_ROWS_DO_NOT_COVER_WINDOW"
    gappy = build_tape(1000, 2000, skip_book=lambda s: 1500 <= s < 1503)
    assert gappy.coverage(1010, 1900)["reason"] == "BOOK_ROW_GAP_OVER_2S"
    no_trades = BookWalkTape.from_rows([book_row(s, 100, 100.05) for s in range(1000, 2001)], [])
    assert no_trades.coverage(1010, 1900)["reason"] == "TRADE_PRINTS_DO_NOT_COVER_WINDOW"
    sparse = build_tape(1000, 2000, trade_every=400)
    assert sparse.coverage(1010, 1790)["reason"] == "TRADE_PRINT_GAP_OVER_300S"


def test_crossed_or_one_sided_rows_are_rejected():
    tape = BookWalkTape.from_rows([book_row(1, 100.1, 100.0), {"s": 2, "b": [[100, 1]], "a": []},
                                   book_row(3, 100.0, 100.05)], [])
    assert list(tape.book_s) == [3]
    assert tape.receipt["book_rows_rejected"] == 2


def test_load_reads_only_book_and_trade_files_inside_the_windows(tmp_path):
    def write(name, rows):
        (tmp_path / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")

    write("bfx_book_10l_1s.jsonl.1", [book_row(s, 100, 100.05) for s in range(1000, 1500)])
    write("bfx_book_10l_1s.jsonl", [book_row(s, 100, 100.05) for s in range(1500, 2000)] + [book_row(9000, 1, 2)])
    write("bfx_trades.jsonl", [trade_row(s, 100.02) for s in range(1000, 2000, 60)])
    write("market_microstructure_1s.jsonl", [{"bucket_ts": s, "bid": 1.0, "ask": 1.1} for s in range(1000, 2000)])
    tape = load_book_walk(tmp_path, [(1100, 1800)])
    assert tape.coverage(1100, 1800)["covered"] is True
    assert int(tape.book_s.max()) < 9000
    assert tape.receipt["book_source_files"] == ["bfx_book_10l_1s.jsonl.1", "bfx_book_10l_1s.jsonl"]
    assert tape.receipt["trade_source_files"] == ["bfx_trades.jsonl"]
    assert tape.receipt["symbol"] == "tBTCF0:USTF0"  # HM #847 N3
    assert "book_tape_1s" in tape.receipt["never_priced_from"]
    assert "market_microstructure_1s" in tape.receipt["never_priced_from"]


def test_coverage_summary_counts_only_covered_events():
    green = coverage_summary({BASIS_BOOK_WALK: 3}, {BASIS_BOOK_WALK: 3}, {}, None, horizon_sec=7200)
    assert (green["alert_level"], green["countable_events"], green["r17_unproven_events"]) == ("GREEN", 3, 0)
    amber = coverage_summary({BASIS_BOOK_WALK: 3, R17_UNPROVEN: 1}, {}, {"BOOK_ROW_GAP_OVER_2S": 1}, None,
                             horizon_sec=7200)
    assert (amber["alert_level"], amber["reason"], amber["countable_events"]) == (
        "AMBER", "R17_UNPROVEN_EVENTS_EXCLUDED", 3)
    red = coverage_summary({BASIS_BOOK_WALK: 1, R17_UNPROVEN: 2}, {}, {}, {"book_first_s": 5}, horizon_sec=7200)
    assert (red["alert_level"], red["reason"], red["coverage_book_first_s"]) == ("RED", "R17_UNPROVEN_MAJORITY", 5)
    assert coverage_summary({}, {}, {}, None, horizon_sec=7200)["alert_level"] == "AMBER"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_book_touching_the_stop_during_a_trade_gap_is_r17_unproven_never_countable(direction):
    """HM #847 C2 probe: exit side 2 % through entry for 11 s while trade prints are silent for 247 s.

    With a 1 % hard stop the trade-side margin never reaches the stop, so the old rule replayed the
    event as covered and countable although a last-trade stop may have fired during a trade-WS outage.
    """
    sign = 1 if direction == "LONG" else -1
    lev = 1.0  # margin % == price %: hard stop 1 % below entry

    def row(s):
        exit_px = ENTRY - sign * (2.0 if 1100 <= s < 1111 else 0.0)
        return book_row(s, exit_px, exit_px + 0.05) if direction == "LONG" else book_row(s, exit_px - 0.05, exit_px)

    trades = [trade_row(s, ENTRY + sign * 0.05) for s in range(990, 1700) if not 1000 < s < 1247]
    tape = _tape([row(s) for s in range(990, 1700)], trades)
    cover = tape.coverage(998, 1600)
    assert cover["covered"] is True and cover["trade_max_gap_s"] == pytest.approx(247)  # the old hole
    window = tape.window(998, 1600)
    path = PreparedBookWalkArrays(window, direction=direction, entry_price=ENTRY, leverage=lev,
                                  fill_ts=1000.0, horizon_sec=600, qty=MARGIN * lev / ENTRY)
    assert float(path.neg_mae.max()) < 1.0  # trade side alone never reaches the 1 % stop
    plan = ReplayPlan({"loss_protection": {"hard_stop_margin_pct": 1.0}, "profit_protection": {"mode": "NONE"}})
    result = replay_cell(path, plan, atr_pct_at_fill=ATR, leverage=lev, margin_usd=MARGIN)
    assert result["status"] == R17_UNPROVEN
    assert result["reasons"] == [STOP_UNOBSERVABLE_REASON]
    assert result["net_pnl_usd"] is None and result["exit_reason"] is None


def test_book_touch_with_live_trade_prints_is_not_flagged():
    # Same book dip, but prints every second: a quiet stop was observable, so the cell stays countable.
    def row(s):
        exit_px = ENTRY - (2.0 if 1100 <= s < 1111 else 0.0)
        return book_row(s, exit_px, exit_px + 0.05)

    trades = [trade_row(s, ENTRY + 0.05) for s in range(990, 1700)]
    tape = _tape([row(s) for s in range(990, 1700)], trades)
    path = PreparedBookWalkArrays(tape.window(998, 1600), direction="LONG", entry_price=ENTRY, leverage=1.0,
                                  fill_ts=1000.0, horizon_sec=600, qty=MARGIN / ENTRY)
    plan = ReplayPlan({"loss_protection": {"hard_stop_margin_pct": 1.0}, "profit_protection": {"mode": "NONE"}})
    assert replay_cell(path, plan, atr_pct_at_fill=ATR, leverage=1.0, margin_usd=MARGIN)["status"] == "COMPLETE"


def test_coverage_summary_reports_unproven_cells_and_is_never_green():
    out = coverage_summary({BASIS_BOOK_WALK: 3}, {BASIS_BOOK_WALK: 3}, {}, None, horizon_sec=7200,
                           unproven_cells={STOP_UNOBSERVABLE_REASON: 4})
    assert (out["alert_level"], out["reason"], out["r17_unproven_cells"]) == ("AMBER", "R17_UNPROVEN_CELLS_EXCLUDED", 4)
    assert out["r17_unproven_cell_reasons"] == {STOP_UNOBSERVABLE_REASON: 4}


def test_coverage_summary_stamps_the_tape_symbol():
    # HM #847 N3
    out = coverage_summary({BASIS_BOOK_WALK: 1}, {BASIS_BOOK_WALK: 1}, {}, {"book_first_s": 1.0}, horizon_sec=7200)
    assert out["symbol"] == "tBTCF0:USTF0"


def test_stop_exit_cells_carry_the_last_trade_doc_inferred_label():
    # HM #847 N4: a stop exit (trade prints live, so observable) is labelled; a non-stop exit is not.
    def row(s):
        return book_row(s, ENTRY - 2.0 if s >= 1100 else ENTRY, (ENTRY - 2.0 if s >= 1100 else ENTRY) + 0.05)

    trades = [trade_row(s, ENTRY - 2.0 if s >= 1100 else ENTRY + 0.05) for s in range(990, 1700)]
    tape = _tape([row(s) for s in range(990, 1700)], trades)
    path = PreparedBookWalkArrays(tape.window(998, 1600), direction="LONG", entry_price=ENTRY, leverage=1.0,
                                  fill_ts=1000.0, horizon_sec=600, qty=MARGIN / ENTRY)
    plan = ReplayPlan({"loss_protection": {"hard_stop_margin_pct": 1.0}, "profit_protection": {"mode": "NONE"}})
    cell = replay_cell(path, plan, atr_pct_at_fill=ATR, leverage=1.0, margin_usd=MARGIN)
    assert (cell["status"], cell["exit_reason"]) == ("COMPLETE", "PHYSICAL_HARD_STOP")
    assert cell["stop_trigger_label"] == "LAST_TRADE_DOC_INFERRED"


def test_exit_fill_stamps_the_actual_row_ready_delay():
    # HM #847 N5: trigger at 1100.2, next complete row is s=1102 (ready 1103) because 1100/1101 are missing.
    tape = _tape([book_row(s, 100, 100.05) for s in range(990, 1700) if s not in (1100, 1101)],
                 [trade_row(s, 100.02) for s in range(990, 1700)])
    fill = tape.window(998, 1600).exit_fill("LONG", 0.001, 1101.2)
    assert fill["book_row_ready_ts"] == 1103.0
    assert fill["delay_s"] == pytest.approx(1.8)
    assert fill["fill_ts"] == pytest.approx(1103.0)
    near = tape.window(998, 1600).exit_fill("LONG", 0.001, 1102.9)  # row ready 0.1 s later: floored at 0.3 s
    assert near["delay_s"] == pytest.approx(0.3) and near["book_row_ready_ts"] == 1103.0

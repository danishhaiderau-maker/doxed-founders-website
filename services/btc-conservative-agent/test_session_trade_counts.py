"""Unit tests for the canonical session trade-count labelling.

``_session_trade_count`` (all in-session entries) and the lane ledger's
``closes`` (terminal non-forced closes only) are intentionally different; the
breakdown must label them clearly instead of forcing them equal.
"""
from __future__ import annotations

from session_trade_counts import classify_session_row_counts

FORCED = {"ADMIN_MANUAL_CLOSE", "ADMIN_FORCE_FLAT", "CIRCUIT_BREAKER_ADMIN_MANUAL"}


def _row(exit_reason=None, close_ts=None, closed_ts=None):
    row = {}
    if exit_reason is not None:
        row["exit_reason"] = exit_reason
    if close_ts is not None:
        row["close_ts"] = close_ts
    if closed_ts is not None:
        row["closed_ts"] = closed_ts
    return row


def test_all_terminal_non_forced() -> None:
    rows = [_row(exit_reason="TAKE_PROFIT"), _row(exit_reason="STOP_LOSS")]
    out = classify_session_row_counts(rows, forced_reasons=FORCED)
    assert out == {"total": 2, "closed": 2, "forced": 0, "open": 0, "strategy_closes": 2}


def test_forced_close_excluded_from_strategy_closes() -> None:
    rows = [
        _row(exit_reason="TAKE_PROFIT"),
        _row(exit_reason="ADMIN_MANUAL_CLOSE"),
        _row(exit_reason="ADMIN_FORCE_FLAT"),
        _row(exit_reason="CIRCUIT_BREAKER_ADMIN_MANUAL"),
    ]
    out = classify_session_row_counts(rows, forced_reasons=FORCED)
    assert out == {"total": 4, "closed": 4, "forced": 3, "open": 0, "strategy_closes": 1}


def test_open_and_unclosed_rows_counted_separately() -> None:
    rows = [
        _row(),                                    # open (no terminal marker)
        _row(),                                    # open
        _row(exit_reason="TAKE_PROFIT"),           # closed
        _row(close_ts="2026-10-07T00:00:00Z"),     # closed via close_ts
        _row(closed_ts="2026-10-07T00:00:00Z"),    # closed via closed_ts
    ]
    out = classify_session_row_counts(rows, forced_reasons=FORCED)
    assert out == {"total": 5, "closed": 3, "forced": 0, "open": 2, "strategy_closes": 3}


def test_total_minus_closed_equals_open_invariant() -> None:
    rows = [
        _row(exit_reason="TAKE_PROFIT"),
        _row(exit_reason="ADMIN_MANUAL_CLOSE"),
        _row(),
    ]
    out = classify_session_row_counts(rows, forced_reasons=FORCED)
    assert out["open"] == out["total"] - out["closed"]
    assert out["strategy_closes"] == out["closed"] - out["forced"]


def test_forced_reasons_case_insensitive() -> None:
    rows = [_row(exit_reason="admin_manual_close")]
    out = classify_session_row_counts(rows, forced_reasons=FORCED)
    assert out["forced"] == 1
    assert out["strategy_closes"] == 0


def test_non_mapping_rows_ignored() -> None:
    rows = [_row(exit_reason="TAKE_PROFIT"), "not-a-row", None, 42]
    out = classify_session_row_counts(rows, forced_reasons=FORCED)
    assert out["total"] == 1
    assert out["closed"] == 1

"""RK 24 Oct (3) FIRST_AFTER: a row counts only if receive_ts <= fill_ts AND it is the FIRST row with
receive_ts >= trigger (or trigger + delay). No other row may be picked; a missing receive_ts is non-countable."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import paper_fill as pf  # noqa: E402

T = 1_800_000_000


def _row(s, px, rx=None, src="WS_FRAME"):
    r = {"s": s, "b": [[px - 0.5, 5.0]], "a": [[px + 0.5, 5.0]]}
    if rx is not None:
        r.update(receive_ts=rx, receive_mono=1.0, receive_ts_source=src)
    return r


def _tail(tmp_path, rows):
    p = tmp_path / "bfx_book_10l_1s.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return pf.BookTail(str(p))


def _fill(at, trig, leg=pf.RESIDENT_LEG):
    return pf.paper_fill("LONG", 1.0, trig, 0.0, at, leg=leg, delay_source="EXCHANGE_RESIDENT_NO_DELAY")


def test_first_received_row_is_used_and_counts(tmp_path):
    # row T (frames up to T+0.3) received at T+0.31 < trigger T+0.4; row T+1 received T+1.2 is the first after.
    tail = _tail(tmp_path, [_row(T - 1, 99, T - 0.1), _row(T, 100, T + 0.31), _row(T + 1, 101, T + 1.2),
                            _row(T + 2, 102, T + 2.1)])
    r = pf.first_row_received_at_or_after(tail.at, T + 0.4)
    assert r["s"] == T + 1 and r["first_after_unproven"] is None
    out = _fill(tail.at, T + 0.4)
    assert out["book_s"] == T + 1 and out["fill_ts"] == T + 1.2 and out["fill_target_ts"] == T + 0.4
    assert out["book_receive_ts"] <= out["fill_ts"] and out["book_receive_unverified"] is None
    assert out["evidence_status"] == pf.EVIDENCE_COMPLETE


def test_row_received_after_trigger_but_late_second_is_still_first(tmp_path):
    # row T received late at T+1.5 (> T+1): it is still the first received at/after the trigger, never skipped
    tail = _tail(tmp_path, [_row(T - 1, 99, T - 0.1), _row(T, 100, T + 1.5), _row(T + 1, 101, T + 1.6)])
    out = _fill(tail.at, T + 0.4)
    assert out["book_s"] == T and out["evidence_status"] == pf.EVIDENCE_COMPLETE


def test_delayed_leg_uses_trigger_plus_delay(tmp_path):
    tail = _tail(tmp_path, [_row(T, 100, T + 0.9), _row(T + 1, 101, T + 1.9), _row(T + 2, 102, T + 2.9)])
    assert pf.first_row_received_at_or_after(tail.at, T + 0.4 + 1.2)["s"] == T + 1   # rx T+1.9 >= T+1.6
    assert pf.first_row_received_at_or_after(tail.at, T + 0.4)["s"] == T


def test_missing_receive_ts_is_non_countable():
    by = {T: _row(T, 100), T + 1: _row(T + 1, 101)}

    def at(t):
        c = [s for s in by if s + 1 <= t + 1e-9]
        return by[max(c)] if c else None
    out = _fill(at, T + 0.4)
    assert out["price"] is not None and out["evidence_status"] == "UNVERIFIED"
    assert out["unverified_reason"] == pf.BOOK_RECEIVE_TS_MISSING


def test_unknown_earlier_row_blocks_proof(tmp_path):
    # row T-1 has no receive_ts: it could have been received after the trigger, so T+1 cannot be proven first
    tail = _tail(tmp_path, [_row(T - 1, 99), _row(T, 100, T + 0.2), _row(T + 1, 101, T + 1.2)])
    r = pf.first_row_received_at_or_after(tail.at, T + 0.4)
    assert r["s"] == T + 1 and r["first_after_unproven"] == pf.BOOK_FIRST_AFTER_NOT_FIRST
    out = _fill(tail.at, T + 0.4)
    assert out["evidence_status"] == "UNVERIFIED" and out["unverified_reason"] == pf.BOOK_FIRST_AFTER_NOT_FIRST


def test_inferred_receive_ts_is_not_measured(tmp_path):
    tail = _tail(tmp_path, [_row(T, 100, T + 0.9, src=pf.RECEIVE_TS_INFERRED), _row(T + 1, 101, T + 1.9)])
    r = pf.first_row_received_at_or_after(tail.at, T + 0.4)
    assert r["s"] == T and r["first_after_unproven"] == pf.BOOK_RECEIVE_TS_MISSING


def test_proof_rejects_before_trigger_after_fill_and_other_rows():
    good = {"s": T + 1, "receive_ts": T + 1.2, "receive_ts_source": "WS_FRAME", "first_after_unproven": None}
    assert pf.first_after_proof(good, T + 0.4, T + 1.2) == {"first_after_ok": True, "first_after_reason": None}
    assert pf.first_after_proof(good, T + 1.3, T + 1.4)["first_after_reason"] == pf.BOOK_RECEIVE_BEFORE_TRIGGER
    assert pf.first_after_proof(good, T + 0.4, T + 1.1)["first_after_reason"] == pf.BOOK_RECEIVE_AFTER_FILL
    assert pf.first_after_proof({**good, "receive_ts": None}, T, T + 2)["first_after_reason"] == pf.BOOK_RECEIVE_TS_MISSING
    picked_other = {**good, "first_after_unproven": pf.BOOK_FIRST_AFTER_NOT_FIRST}
    assert not pf.first_after_proof(picked_other, T + 0.4, T + 1.2)["first_after_ok"]


def test_no_row_received_after_trigger_yet_fails_closed(tmp_path):
    # only rows received before the trigger exist: the by-second row may be priced but never counts
    tail = _tail(tmp_path, [_row(T - 1, 99, T - 0.1), _row(T, 100, T + 0.3)])
    assert pf.first_row_received_at_or_after(tail.at, T + 0.4) is None
    out = _fill(tail.at, T + 0.4)
    assert out["evidence_status"] != pf.EVIDENCE_COMPLETE


def test_book10l_exposes_exact_row_list():
    assert callable(getattr(pf.book10l_at, "rows_between", None))

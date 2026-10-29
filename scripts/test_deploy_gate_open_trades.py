import deploy_gate_open_trades as gate

GOOD_STATUS = {"live_armed": False, "force_paper_mode": True, "git_rev": "abc"}
# #806: all-safe live surfaces (live copy off, no tile live orders, nothing armed, one flat fresh account)
SAFE_LIVE_COPY = {"live_copy_output": {"enabled": False}, "live_copy_source_enabled": False,
                  "tiles": [{"lane": "L", "bitfinex_live_orders": False}],
                  "website": {"reachable": True, "armed_accounts": []}}
SAFE_EXCHANGE = {"account": "acct-1", "mirror_ok": True, "read_age_s": 3.0, "fresh": True, "positions": [],
                 "orphans": [], "open_orders": 0, "verdict": "FLAT_NO_STOPS", "accounts_seen": ["acct-1"]}


def live_surface(path):
    """Safe answers for the #806 routes, else None."""
    if path == "/api/live-copy/status":
        return SAFE_LIVE_COPY
    if path.startswith("/api/monitor/live/exchange-stops"):
        return SAFE_EXCHANGE
    return None


def _feed(rows, page_size):
    """Fake /tiles/trades: ascending pages with next_cursor, like the real route."""
    def page(rows_, cursor):
        start = int(cursor or 0)
        chunk = rows_[start:start + page_size]
        nxt = start + page_size
        return {"rows": chunk, "next_cursor": str(nxt) if nxt < len(rows_) else None,
                "remaining_after_page": max(0, len(rows_) - nxt)}

    def fetch(path, state=None, status=None):
        raise NotImplementedError
    return page


def _fetcher(all_rows, page_size=3, state=None, status=GOOD_STATUS, fail_page=None):
    page = _feed(all_rows, page_size)
    calls = {"n": 0}

    def fetch(path):
        if live_surface(path) is not None:
            return live_surface(path)
        if path == "/api/status":
            return status
        if path == "/api/state":
            return state if state is not None else {"lane_position_counts": {"L": {"open": sum(1 for r in all_rows if r["status"] != "CLOSED")}}}
        cursor = path.split("cursor=")[1] if "cursor=" in path else None
        calls["n"] += 1
        if fail_page is not None and calls["n"] == fail_page:
            raise TimeoutError("page read failed")
        rows = [r for r in all_rows if r["status"] != "CLOSED"] if "status=open" in path else all_rows
        return _feed(rows, page_size)(rows, cursor)
    return fetch


def _rows(n_closed, n_open):
    return [{"trade_id": f"c{i}", "status": "CLOSED"} for i in range(n_closed)] + \
           [{"trade_id": f"o{i}", "status": "OPEN"} for i in range(n_open)]


def test_all_closed_across_many_pages_passes():
    out = gate.evaluate(_fetcher(_rows(40, 0)))
    assert out["ok"] is True and out["rows_total"] == 40 and out["monitor_open_count"] == 0


def test_open_trades_spanning_several_pages_are_all_seen_and_block():
    rows = _rows(7, 0)
    rows[1]["status"] = "OPEN"          # page 1
    rows[4]["status"] = "OPEN"          # page 2
    rows += [{"trade_id": "late", "status": "OPEN"}]  # last page: page-1-only reads would miss it
    out = gate.evaluate(_fetcher(rows, page_size=3))
    assert out["ok"] is False and out["non_closed_all_pages"] == 3 and out["pages_open"] == 3
    assert any(r.startswith("OPEN_TRADES:3") for r in out["reasons"])


def test_open_trade_only_on_the_last_page_is_found():
    rows = _rows(20, 1)                  # the single open row sits on page 7 of 7
    out = gate.evaluate(_fetcher(rows, page_size=3))
    assert out["non_closed_all_pages"] == 1 and not out["ok"]


def test_a_page_read_error_fails_closed():
    out = gate.evaluate(_fetcher(_rows(10, 0), page_size=3, fail_page=2))
    assert out["ok"] is False and any(r.startswith("TRADES_READ_FAILED") for r in out["reasons"])


def test_count_mismatch_with_the_monitor_fails_closed():
    out = gate.evaluate(_fetcher(_rows(5, 0), state={"lane_position_counts": {"L": {"open": 2}}}))
    assert out["ok"] is False and any(r.startswith("COUNT_MISMATCH") for r in out["reasons"])


def test_missing_monitor_count_or_live_armed_or_paging_loop_fail_closed():
    assert not gate.evaluate(_fetcher(_rows(2, 0), state={}))["ok"]
    assert "LIVE_NOT_OFF" in gate.evaluate(_fetcher(_rows(2, 0), status={**GOOD_STATUS, "live_armed": True}))["reasons"]
    assert "FORCE_PAPER_NOT_TRUE" in gate.evaluate(_fetcher(_rows(2, 0), status={**GOOD_STATUS, "force_paper_mode": False}))["reasons"]

    def looping(path):
        if live_surface(path) is not None:
            return live_surface(path)
        if path == "/api/status":
            return GOOD_STATUS
        if path == "/api/state":
            return {"lane_position_counts": {"L": {"open": 0}}}
        return {"rows": [], "next_cursor": "same", "remaining_after_page": 1}
    out = gate.evaluate(looping)
    assert not out["ok"] and any("CURSOR_LOOP" in r for r in out["reasons"])


def test_per_page_open_counts_are_reported():
    rows = _rows(5, 0); rows[4]["status"] = "OPEN"
    out = gate.evaluate(_fetcher(rows, page_size=2))
    full = [p for p in out["pages"] if p["feed"] == "trades"]
    assert len(full) == 3 and [p["non_closed"] for p in full] == [0, 0, 1]
    assert out["non_closed_all_pages"] == 1 and out["ok"] is False


def test_timeout_on_any_page_blocks():
    for n in (1, 2, 3):
        out = gate.evaluate(_fetcher(_rows(9, 0), page_size=3, fail_page=n))
        assert out["ok"] is False and any(r.startswith("TRADES_READ_FAILED") for r in out["reasons"]), n


# ---- 19 Oct: terminal non-position statuses (HM ruling) ------------------------------------------------------------
import json
from pathlib import Path

_REAL = {r["trade_id"]: r for r in json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "gate_open_and_expired_rows_20261019.json").read_text())}


def _gate_fetch(all_rows, monitor_open):
    def fetch(path):
        if live_surface(path) is not None:
            return live_surface(path)
        if path == "/api/status":
            return GOOD_STATUS
        if path == "/api/state":
            return {"lane_position_counts": {"L": {"open": monitor_open, "pending": 0}}}
        rows = [r for r in all_rows if gate.counts_as_open(r)] if "status=open" in path else all_rows
        return {"rows": rows, "next_cursor": None, "remaining_after_page": 0}
    return fetch


def _expired(**kw):
    row = {"trade_id": "e", "status": "EXPIRED", "entry_ts": None, "fill": None}
    row.update(kw)
    return row


def test_real_open_row_counts_and_real_expired_unfilled_row_does_not():
    o, e = _REAL["gs1-f31e676ca020"], _REAL["gb2-eaec6129ced8"]
    assert gate.counts_as_open(o) is True and gate.counts_as_open(e) is False
    out = gate.evaluate(_gate_fetch([e, o], monitor_open=1))
    assert out["non_closed_all_pages"] == 1 and out["pages_open"] == 1
    assert "OPEN_TRADES:1" in out["reasons"] and not any(r.startswith(("OPEN_VIEWS", "COUNT_MISMATCH")) for r in out["reasons"])


def test_flat_with_only_an_expired_unfilled_row_passes():
    out = gate.evaluate(_gate_fetch([_REAL["gb2-eaec6129ced8"]] + _rows(3, 0), monitor_open=0))
    assert out["ok"] is True


def test_expired_with_an_entry_ts_or_a_fill_snapshot_fails_closed():
    assert gate.counts_as_open(_expired(entry_ts=1792352347.0)) is True
    assert gate.counts_as_open(_expired(fill={"fill_price": 80949.0, "fill_ts": 1.0})) is True
    out = gate.evaluate(_gate_fetch([_expired(entry_ts=1.0)], monitor_open=0))
    assert out["ok"] is False and any(r.startswith("OPEN_TRADES") for r in out["reasons"])


def test_unknown_status_or_missing_entry_field_fails_closed():
    assert gate.counts_as_open({"status": "WEIRD", "entry_ts": None, "fill": None}) is True
    assert gate.counts_as_open({"status": "EXPIRED", "fill": None}) is True            # entry_ts missing
    assert gate.counts_as_open({"status": "EXPIRED", "entry_ts": None, "fill": "garbage"}) is True   # unreadable fill
    assert gate.counts_as_open({"status": "EXPIRED", "entry_ts": None, "order": {"terminal": "FILLED"}}) is True
    assert gate.counts_as_open({"status": "EXPIRED", "entry_ts": None}) is False       # real shape: no fill key, never filled
    assert gate.counts_as_open({"status": None}) is True


def test_other_terminal_non_position_statuses_are_non_open_only_when_unfilled():
    for st in ("CANCELLED", "CANCELED", "REJECTED", "NO_FILL", "expired"):
        assert gate.counts_as_open(_expired(status=st)) is False
        assert gate.counts_as_open(_expired(status=st, entry_ts=5.0)) is True


def test_monitor_count_stays_open_plus_pending_and_mismatch_still_fails():
    out = gate.evaluate(_gate_fetch([_REAL["gs1-f31e676ca020"], _REAL["gb2-eaec6129ced8"]], monitor_open=2))
    assert any(r.startswith("COUNT_MISMATCH") for r in out["reasons"])


def test_pending_unfilled_order_blocks_and_is_covered_by_the_wait():
    pend = {"trade_id": "p", "status": "PENDING", "entry_ts": None, "fill": None}
    assert gate.counts_as_open(pend) is True
    out = gate.evaluate(_gate_fetch([pend], monitor_open=1))
    assert out["reasons"] == ["OPEN_TRADES:1"]   # only OPEN_TRADES: main() waits on exactly this verdict


def test_partial_fill_or_exchange_id_or_filled_qty_on_an_expired_row_fails_closed():
    assert gate.counts_as_open(_expired(fill={"filled_qty": 0.0001})) is True
    assert gate.counts_as_open(_expired(filled_qty=0.0001)) is True
    assert gate.counts_as_open(_expired(order={"terminal": "EXPIRED", "exchange_order_id": "abc"})) is True
    assert gate.counts_as_open(_expired(order={"terminal": "PARTIALLY_FILLED"})) is True
    assert gate.counts_as_open(_expired(order={"terminal": "EXPIRED", "filled_qty": 0.5})) is True
    assert gate.counts_as_open(_expired(order={"terminal": "EXPIRED", "terminal_reason": "x"})) is False


def test_status_must_be_on_the_allowlist():
    for st in ("WORKING", "NEW", "ACTIVE", "SOMETHING_ELSE", "", None):
        assert gate.counts_as_open(_expired(status=st)) is True


def test_missing_fill_key_accepted_only_for_allowlisted_null_entry_zero_qty_no_exchange_id():
    base = {"trade_id": "e", "status": "EXPIRED", "entry_ts": None}            # no 'fill' key, like the real row
    assert gate.counts_as_open(base) is False
    assert gate.counts_as_open(dict(base, qty=0)) is False and gate.counts_as_open(dict(base, qty=0.0)) is False
    assert gate.counts_as_open(dict(base, qty=0.001)) is True
    assert gate.counts_as_open(dict(base, order_id="x1")) is True
    assert gate.counts_as_open(dict(base, status="WEIRD")) is True
    assert gate.counts_as_open(dict(base, entry_ts=1.0)) is True
    # any fill-like key present and non-empty fails closed, even a differently named one
    for k, v in (("fill_price", 80000.0), ("fill_ts", 1.0), ("fill_basis", "BOOK10L_TAKER_WALK"), ("filled_qty", 0.1),
                 ("fill_snapshot", {"a": 1}), ("partial_fill", True)):
        assert gate.counts_as_open(dict(base, **{k: v})) is True, k
    assert gate.counts_as_open(dict(base, fill_price=None, fill_basis="")) is False   # empty fill-like keys are fine

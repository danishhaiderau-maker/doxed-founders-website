import deploy_gate_open_trades as gate

GOOD_STATUS = {"live_armed": False, "force_paper_mode": True, "git_rev": "abc"}


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
        if path == "/api/status":
            return GOOD_STATUS
        if path == "/api/state":
            return {"lane_position_counts": {"L": {"open": 0}}}
        return {"rows": [], "next_cursor": "same", "remaining_after_page": 1}
    out = gate.evaluate(looping)
    assert not out["ok"] and any("CURSOR_LOOP" in r for r in out["reasons"])

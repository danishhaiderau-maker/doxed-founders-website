"""RK #882 (R3/R0): paper fills stamp book_ts + a non-None price age; closes stamp the book they priced on."""
import research_v3_bridge as b
import paper_fill as pf


def _order(sim):
    return {"fill_sim": sim, "qty": 0.001, "requested_qty": 0.001}


def test_book_walked_fill_stamps_book_ts_and_age():
    sim = {"fill_model": sorted(pf.ALLOWED_FILL_MODELS)[0], "evidence_status": pf.EVIDENCE_COMPLETE,
           "book_s": 1792300000, "price_age_s": 0.42, "book_receive_ts": 1792300000.3}
    r = b._paper_fill_execution_receipt(_order(sim), {"qty": 0.001}, {})
    assert r["book_ts"] == 1792300000.3
    assert r["book_age_sec"] is not None and r["book_age_sec"] == 0.42
    assert r["conservative_fill_supported"] is True


def test_fill_with_no_price_age_is_never_supported():
    sim = {"fill_model": sorted(pf.ALLOWED_FILL_MODELS)[0], "evidence_status": pf.EVIDENCE_COMPLETE,
           "book_s": 1792300000}
    r = b._paper_fill_execution_receipt(_order(sim), {"qty": 0.001}, {})
    assert r["book_age_sec"] is None
    assert r["conservative_fill_supported"] is False


def test_book_age_ms_fallback_and_none_without_book():
    assert b._fill_book_stamp({"book_s": 5, "book_age_ms": 1500}) == (5.0, 1.5)
    assert b._fill_book_stamp(None) == (None, None)
    ts, age = b._fill_book_stamp({"book_s": 7, "price_age_s": 0.0})
    assert ts == 7.0 and age is not None and age == 0.0


def test_paper_close_row_stamps_the_book_it_priced_on():
    import inspect
    src = inspect.getsource(b)
    close = src[src.index('f"execution:{event_id}:paper-close"'):]
    close = close[:close.index('store.append("lifecycle"')]
    assert '"exit_book_ts": _fill_book_stamp(' in close and '"exit_price_age_s": _fill_book_stamp(' in close

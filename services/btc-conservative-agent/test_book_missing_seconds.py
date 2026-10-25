"""missing_book_seconds: seconds without a book row between an exit trigger and its pricing (HM 18 Oct)."""
import json

import paper_fill


def _tail(tmp_path, seconds):
    p = tmp_path / "book.jsonl"
    p.write_text("".join(json.dumps({"s": s, "b": [[1, 1]], "a": [[2, 1]]}) + "\n" for s in seconds))
    return paper_fill.BookTail(str(p))


def test_missing_seconds_counts_the_gap(tmp_path):
    t = _tail(tmp_path, [100, 101, 102, 107, 108])  # 103-106 absent
    assert t.missing_seconds(100.5, 108.9) == 4
    assert t.missing_seconds(100, 102) == 0
    assert t.missing_seconds(109, 105) == 0


def test_missing_seconds_unavailable_source_is_none(tmp_path, monkeypatch):
    monkeypatch.setattr(paper_fill, "book10l_at", lambda t: (_ for _ in ()).throw(RuntimeError("no book")))
    assert paper_fill.book10l_missing_seconds(1.0, 2.0) is None


def test_carried_flag_survives_booktail_and_shows_in_the_fill_snapshot(tmp_path):
    p = tmp_path / "book.jsonl"
    p.write_text(json.dumps({"s": 100, "b": [[1, 5]], "a": [[2, 5]], "carried": True}) + "\n"
                 + json.dumps({"s": 101, "b": [[1, 5]], "a": [[2, 5]]}) + "\n")
    t = paper_fill.BookTail(str(p))
    assert t.at(101.5)["s"] == 100 and t.at(101.5)["carried"] is True
    assert "carried" not in t.at(102.5)
    assert paper_fill._snap(t.at(101.5))["carried"] is True
    assert "carried" not in paper_fill._snap(t.at(102.5))
    r = paper_fill.paper_fill("LONG", 1.0, 100.6, 0.5, t.at, leg="entry")
    assert r["status"] == "FILLED" and r["depth_snapshot_fill"]["carried"] is True


def test_paper_fill_still_rejects_a_book_row_older_than_the_age_limit(tmp_path):
    p = tmp_path / "book.jsonl"
    p.write_text(json.dumps({"s": 100, "b": [[1, 5]], "a": [[2, 5]], "carried": True}) + "\n")
    t = paper_fill.BookTail(str(p))
    r = paper_fill.paper_fill("LONG", 1.0, 110.0, 0.5, t.at, leg="entry")
    assert r["status"] == "REJECT_NO_BOOK" and r["price"] is None


def test_by_model_reports_carried_share_per_tile_and_model():
    import fills_by_model as fm
    carried = {"research_lane": "GSB1", "fill_model": paper_fill.FILL_MODEL, "entry_ts": 1e12,
               "exit_provenance": {"depth_snapshot_fill": {"s": 1, "carried": True}}}
    clean = {"research_lane": "GSB1", "fill_model": paper_fill.FILL_MODEL, "entry_ts": 1e12,
             "exit_provenance": {"depth_snapshot_fill": {"s": 1}}}
    out = fm.summarise([carried, clean, clean, clean], ["GSB1", "GSB2"])
    t = out["tiles"]["GSB1"]
    assert t["carried_book_rows"] == 1 and t["carried_book_share"] == 0.25
    assert t["carried_by_fill_model"] == {paper_fill.FILL_MODEL: 1}
    assert out["tiles"]["GSB2"]["carried_book_share"] is None


def test_retry_trigger_is_the_model_time_once_then_the_current_time():
    import bot
    assert bot._paper_exit_attempt_trigger(100.0, 1.5, 0, 105.0) == 100.0
    assert bot._paper_exit_attempt_trigger(100.0, 1.5, 1, 105.0) == 103.5  # fill time == now
    assert bot._paper_exit_attempt_trigger(100.0, 1.5, 3, 100.2) == 100.0  # never earlier than the trigger


def test_exit_retry_loop_rereads_the_same_model_time_never_current_time(monkeypatch):
    """REALISM_V2 F4: a late book row is re-read at the SAME model time (trigger + delay); never current-time
    repricing. A book that never lands leaves the exit UNPRICED (NO_BOOK_UNPRICED, UNVERIFIED)."""
    import bot
    import tile_fill
    clock = {"t": 1000.0}
    monkeypatch.setattr(bot.time, "time", lambda: clock["t"])
    monkeypatch.setattr(bot.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(bot, "_paper_delay", lambda *a, **k: (1.5, "lane_p50"))
    monkeypatch.setattr(bot, "_is_paper_book_lane", lambda p: False)
    monkeypatch.setattr(bot, "_paper_exit_missing_book_seconds", lambda t0: 0)
    monkeypatch.setattr(bot, "_paper_exit_sim", lambda res, pos, d, q, marks: {"price": res["price"]})
    seen = []

    def fake_exit_fill(direction, qty, trigger_ts, delay_s, delay_source):
        seen.append((trigger_ts, delay_s))
        # the model-time book row lands ~1-2 s late
        return {"status": "FILLED", "price": 81805.0} if clock["t"] >= 1003.0 else {"status": "REJECT_NO_BOOK", "price": None}

    monkeypatch.setattr(tile_fill, "exit_fill", fake_exit_fill)
    pos = {"research_lane": "GS1", "dir": "LONG", "qty": 1.0, "exit_trigger_ts": 1000.0}
    out, marks = bot._paper_lane_exit(pos, "PATH_END_60M")
    assert out == {"price": 81805.0} and marks["exit_status"] == "PAPER_FILL"
    assert len(seen) >= 2 and set(seen) == {(1000.0, 1.5)}  # every retry re-reads the same model time
    assert marks["exit_pricing_basis"] == "MODEL_TIME" and "exit_repriced_lag_s" not in marks
    clock["t"] = 2000.0
    monkeypatch.setattr(tile_fill, "exit_fill", lambda *a, **k: {"status": "REJECT_NO_BOOK", "price": None})
    out2, marks2 = bot._paper_lane_exit({**pos, "exit_trigger_ts": 2000.0}, "PATH_END_60M")
    assert out2 is None and marks2["exit_status"] == "NO_BOOK_UNPRICED" and marks2["fill_valid"] is False
    assert marks2["exit_pricing_basis"] == "NO_BOOK_UNPRICED"


def test_repriced_exit_is_labelled_counted_separately_and_stays_countable_only_when_book_priced():
    import fills_by_model as fm
    row = {"research_lane": "GSB1", "fill_model": paper_fill.FILL_MODEL, "entry_ts": 1e12,
           "exit_pricing_basis": "REPRICED_CURRENT_TIME", "exit_repriced_lag_s": 4.2}
    forced = {**row, "non_fill_category": "NO_BOOK_EXIT", "exit_fallback": "LEGACY_BBO_FALLBACK_NO_BOOK"}
    out = fm.summarise([row, forced, {**row, "exit_pricing_basis": "MODEL_TIME"}], ["GSB1"])["tiles"]["GSB1"]
    assert out["repriced_exit_rows"] == 2 and out["repriced_exit_countable"] == 0  # forced is never countable
    assert out["by_category"]["no_book_exit"] == 1 and out["repriced_exit_share"] == round(2 / 3, 4)


def test_no_retry_can_price_later_than_the_force_window():
    import tile_fill
    import bot
    # every retry runs before time - t0 >= EXIT_FORCE_AFTER_S, so lag = now - delay - t0 < EXIT_FORCE_AFTER_S
    assert bot._paper_exit_attempt_trigger(0.0, 1.5, 5, tile_fill.EXIT_FORCE_AFTER_S - 0.01) - 0.0 < tile_fill.EXIT_FORCE_AFTER_S


def _run_exit_with_book_back_at(monkeypatch, back_at):
    import bot
    import tile_fill
    clock = {"t": 3000.0}
    monkeypatch.setattr(bot.time, "time", lambda: clock["t"])
    monkeypatch.setattr(bot.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(bot, "_paper_delay", lambda *a, **k: (1.5, "lane_p50"))
    monkeypatch.setattr(bot, "_is_paper_book_lane", lambda p: False)
    monkeypatch.setattr(bot, "_paper_exit_missing_book_seconds", lambda t0: 0)
    monkeypatch.setattr(bot, "_paper_exit_sim", lambda res, pos, d, q, marks: {"price": res["price"]})
    monkeypatch.setattr(tile_fill, "exit_fill",
                        lambda d, q, trig, delay, src: {"status": "FILLED", "price": 1.0, "trig": trig}
                        if clock["t"] >= back_at else {"status": "REJECT_NO_BOOK", "price": None})
    return bot._paper_lane_exit({"research_lane": "GS1", "dir": "LONG", "qty": 1.0, "exit_trigger_ts": 3000.0}, "PATH_END_60M")


def test_ten_second_retry_cap_boundary(monkeypatch):
    import tile_fill
    model = 3000.0 + 1.5
    out, marks = _run_exit_with_book_back_at(monkeypatch, model + 8.0)  # row lands inside the window
    assert out == {"price": 1.0} and marks["exit_status"] == "PAPER_FILL" and marks["exit_pricing_basis"] == "MODEL_TIME"
    out, marks = _run_exit_with_book_back_at(monkeypatch, model + tile_fill.EXIT_FORCE_AFTER_S + 0.5)
    assert out is None and marks["exit_status"] == "NO_BOOK_UNPRICED" and marks["non_fill_category"] == "NO_BOOK_EXIT"
    assert marks["fill_valid"] is False


def test_carried_row_uses_its_frame_receive_ts_not_the_bucket(tmp_path):
    """HM #851 nit c: a carried-forward row's receive_ts is the frame it repeats (collector writes the frame's time),
    so the pricer proves no look-ahead on that frame time, not on s; a carried row without receive_ts still fails
    closed with BOOK_RECEIVE_TS_MISSING."""
    p = tmp_path / "book.jsonl"
    rows = [{"s": 100, "b": [[1, 5]], "a": [[2, 5]], "receive_ts": 100.1, "receive_mono": 3.1,
             "receive_ts_source": "WS_FRAME"},
            {"s": 101, "b": [[1, 5]], "a": [[2, 5]], "carried": True, "receive_ts": 100.1, "receive_mono": 3.1,
             "receive_ts_source": "WS_FRAME"},
            {"s": 102, "b": [[1, 5]], "a": [[2, 5]], "carried": True}]
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    t = paper_fill.BookTail(str(p))
    row = t.at(102.5)
    assert row["s"] == 101 and row["carried"] is True and row["receive_ts"] == 100.1  # frame time, not 101/102
    proof = paper_fill.book_receive_proof(row, 102.5, "LATEST")
    assert proof["book_receive_ts"] == 100.1 and proof["book_receive_unverified"] is None
    missing = t.at(103.5)
    assert missing["s"] == 102 and missing["receive_ts"] is None
    assert paper_fill.book_receive_proof(missing, 103.5, "LATEST")["book_receive_unverified"] == \
        paper_fill.BOOK_RECEIVE_TS_MISSING

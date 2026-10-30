"""Strategist streams: Bitfinex 10-level book, individual trades, Lighter + Paradex BBO; bounded rotation."""
import json
from pathlib import Path

import strategist_streams_collector as ssc


def _rows(p):
    return [json.loads(x) for x in Path(p).read_text().splitlines()]


RX_KEYS = ("receive_ts", "receive_mono", "receive_ts_source", "rx")


def _book_rows(p):
    """Rows without the measured receive stamps (asserted separately in test_*_carry_measured_receive_ts)."""
    return [{k: v for k, v in r.items() if k not in RX_KEYS} for r in _rows(p)]


_trade_rows = _book_rows


def test_book_trades_and_venues(tmp_path):
    c = ssc.Collector(str(tmp_path))
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "book", "chanId": 1}, chans, 100.0)
    c.on_bfx({"event": "subscribed", "channel": "trades", "chanId": 2}, chans, 100.0)
    snap = [[100 + i, 1, 0.5] for i in range(12)] + [[200 + i, 1, -0.25] for i in range(12)]
    c.on_bfx([1, snap], chans, 100.1)
    c.on_bfx([1, [111, 0, 1]], chans, 100.2)  # delete best bid
    c.on_bfx([2, [[9, 1, 1, 1]]], chans, 100.2)  # history snapshot ignored
    c.on_bfx([2, "te", [10, 100200, -0.01, 150.5]], chans, 100.2)
    c.on_bfx([2, "tu", [10, 100200, -0.01, 150.5]], chans, 100.2)
    c.on_bfx([1, "hb"], chans, 100.3)
    c.on_lighter({"ticker": {"b": {"price": "99.5"}, "a": {"price": "100.5"}}}, 100.4)
    c.on_paradex({"bid": "99.0", "ask": "101.0"}, 100.4)
    c.close_second(100)
    c.close_second(160)  # next minute -> flush minute 60
    book = _rows(tmp_path / ssc.BOOK_FILE)[0]
    assert book["s"] == 100 and len(book["b"]) == 10 and book["b"][0] == [110, 0.5] and book["a"][0] == [200, 0.25]
    assert _trade_rows(tmp_path / ssc.TRADES_FILE) == [{"s": 100, "tr": [[10, 100200, -0.01, 150.5]]}]
    v = _rows(tmp_path / ssc.VENUE_FILE)[0]
    assert v["m"] == 60 and v["lighter"][40] == [99.5, 100.5] and v["paradex"][40] == [99, 101]


def test_stale_book_not_written(tmp_path):
    c = ssc.Collector(str(tmp_path))
    c.book.snapshot([[1, 1, 1], [2, 1, -1]], 10.0)
    c.close_second(50)
    assert not (tmp_path / ssc.BOOK_FILE).exists()


def _quiet_book_collector(tmp_path):
    c = ssc.Collector(str(tmp_path))
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "book", "chanId": 1}, chans, 100.0)
    c.on_bfx([1, [[100, 1, 0.5], [200, 1, -0.5]]], chans, 100.1)
    return c, chans


def test_quiet_book_on_a_live_connection_is_carried_forward_and_flagged(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    c.on_bfx([1, "hb"], chans, 112.0)  # heartbeat ON THE BOOK CHANNEL: delivery alive, book unchanged for 12 s
    for sec in (105, 108, 111):  # book.ts is older than MAX_QUOTE_AGE_SEC for all of them
        c.close_second(sec)
    rows = _rows(tmp_path / ssc.BOOK_FILE)
    assert [r["s"] for r in rows] == [105, 108, 111]
    assert all(r["carried"] is True and r["b"] == [[100, 0.5]] and r["a"] == [[200, 0.5]] for r in rows)


def test_heartbeat_on_another_channel_does_not_keep_the_book_alive(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    c.on_bfx({"event": "subscribed", "channel": "trades", "chanId": 2}, chans, 100.0)
    c.on_bfx([2, "hb"], chans, 112.0)  # trades-channel heartbeat only
    c.on_bfx([2, "te", [10, 112000, 0.01, 100.5]], chans, 112.0)
    c.close_second(125)  # last book-channel frame was at 100.1: 26 s ago
    assert not (tmp_path / ssc.BOOK_FILE).exists()


def test_disconnect_stops_carrying_and_a_crossed_book_is_never_carried(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    c.on_bfx([1, "hb"], chans, 105.0)
    c.on_bfx_disconnect()  # socket dropped: book channel gone
    c.close_second(106)
    assert not (tmp_path / ssc.BOOK_FILE).exists()
    c2 = ssc.Collector(str(tmp_path / "x"))
    chans2 = {}
    c2.on_bfx({"event": "subscribed", "channel": "book", "chanId": 1}, chans2, 100.0)
    c2.on_bfx([1, [[200, 1, 0.5], [100, 1, -0.5]]], chans2, 100.1)  # bid 200 >= ask 100: crossed
    c2.on_bfx([1, "hb"], chans2, 105.0)
    c2.close_second(104)
    assert not (tmp_path / "x" / ssc.BOOK_FILE).exists()


def test_carry_is_capped_at_the_liveness_window(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    c.on_bfx([1, "hb"], chans, 105.0)
    c.close_second(int(105 + ssc.BOOK_LIVENESS_SEC) - 1)  # inside the cap: carried
    c.close_second(int(105 + ssc.BOOK_LIVENESS_SEC) + 1)  # beyond the cap with no further frame: nothing
    assert [r["s"] for r in _rows(tmp_path / ssc.BOOK_FILE)] == [int(105 + ssc.BOOK_LIVENESS_SEC) - 1]


def test_dead_connection_is_not_carried_forward(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    c.close_second(100 + int(ssc.BOOK_LIVENESS_SEC) + 5)  # nothing at all arrived for > liveness window
    assert not (tmp_path / ssc.BOOK_FILE).exists()


def test_fresh_second_is_not_flagged_carried(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    c.close_second(100)
    assert "carried" not in _rows(tmp_path / ssc.BOOK_FILE)[0]


def test_rotation_never_unlinks(tmp_path):
    """R25/R34: size rotate may rename to .N but must never remove a prior segment.

    Mutation proof: restoring the old ``os.remove`` keep-N loop (or any unlink in
    ``Sink._rotate``) makes ``len(rotated)`` stall at N while this assert requires
    file count to grow with every rotate (here 40 writes → many ``x.jsonl.*``).
    """
    path = tmp_path / "x.jsonl"
    s = ssc.Sink(str(path), rotate_bytes=50, max_total_bytes=10 * 1024 * 1024)
    fingerprints = {}
    for i in range(40):
        s.write({"i": i, "pad": "x" * 30})
        for p in tmp_path.glob("x.jsonl.*"):
            if p.name not in fingerprints:
                fingerprints[p.name] = p.read_bytes()
    rotated = sorted(tmp_path.glob("x.jsonl.*"), key=lambda p: int(p.name.rsplit(".", 1)[-1]))
    assert len(rotated) >= 8, f"expected many kept rotations, got {len(rotated)}"
    for name, blob in fingerprints.items():
        p = tmp_path / name
        assert p.exists(), name
        assert p.read_bytes() == blob, name
    src = (Path(__file__).parent / "strategist_streams_collector.py").read_text()
    sink_src = src[src.index("class Sink:"): src.index("\nclass BfxBook")]
    # call-site guard (docstrings may say "never unlinks")
    assert "os.remove(" not in sink_src and "os.unlink(" not in sink_src and "Path.unlink" not in sink_src
    assert "KEEP_ROTATIONS" not in src and "STRATEGIST_STREAM_KEEP_ROTATIONS" not in src
    assert not hasattr(ssc, "KEEP_ROTATIONS")
    assert len(list(tmp_path.glob("x.jsonl*"))) == len(rotated) + (1 if path.exists() else 0)


def test_size_cap_stops_writing_and_alarms_immediately(tmp_path, monkeypatch):
    """R25: when running total would exceed max_total_bytes, refuse write + ALARM; never delete.

    Mutation proof: removing the ``_apply_pressure`` stop / ``self._bytes += need`` path
    lets ``rows`` keep growing past the cap / leaves ``size_cap_hits == 0``.
    """
    alarms = []
    monkeypatch.setattr(ssc, "_log", lambda msg: alarms.append(msg))
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    path = tmp_path / "cap.jsonl"
    cap = 200
    s = ssc.Sink(str(path), rotate_bytes=80, max_total_bytes=cap)
    for i in range(30):
        s.write({"i": i, "pad": "yyyyyyyy"})
    assert s.size_cap_hits >= 1 and s.failures >= 1
    assert any("STREAM_SIZE_CAP" in a and "stop_writing=True" in a for a in alarms), alarms
    assert s._bytes <= cap
    before = {p.name: p.read_bytes() for p in tmp_path.glob("cap.jsonl*")}
    rows_at_cap = s.rows
    for i in range(30, 60):
        s.write({"i": i, "pad": "yyyyyyyy"})
    assert s.rows == rows_at_cap
    assert s.size_cap_hits >= 2
    assert {p.name: p.read_bytes() for p in tmp_path.glob("cap.jsonl*")} == before
    status = json.loads((tmp_path / ssc.SIZE_CAP_STATUS_FILE).read_text())
    assert status["level"] == "RED" and status["reason"] == "STREAM_SIZE_CAP"
    assert status["card"]["total_all_streams_cap_gib"] == 40.0
    sink_src = (Path(__file__).parent / "strategist_streams_collector.py").read_text()
    sink_src = sink_src[sink_src.index("class Sink:"): sink_src.index("\nclass BfxBook")]
    assert "os.remove(" not in sink_src and "_apply_pressure" in sink_src


def test_write_uses_running_total_no_glob_per_row(tmp_path, monkeypatch):
    """R43/R0c: write hot path must not glob/stat every .N; seed once, add need, rename unchanged."""
    calls = {"glob": 0}
    real_glob = ssc.glob.glob

    def counting_glob(*args, **kwargs):
        calls["glob"] += 1
        return real_glob(*args, **kwargs)

    monkeypatch.setattr(ssc.glob, "glob", counting_glob)
    path = tmp_path / "run.jsonl"
    s = ssc.Sink(str(path), rotate_bytes=10 ** 9, max_total_bytes=10 ** 9)  # no rotate
    seeded = calls["glob"]
    for i in range(25):
        s.write({"i": i, "pad": "x" * 20})
    assert calls["glob"] == seeded, f"write hot path globbed {calls['glob'] - seeded} times"
    assert s._bytes == sum(len(json.dumps({"i": i, "pad": "x" * 20}, separators=(",", ":")).encode() + b"\n")
                            for i in range(25))


def test_mutation_per_write_glob_fails_no_glob_assert(tmp_path, monkeypatch):
    """Mutation proof: reintroducing ``_measure_total_bytes`` (glob) on every write MUST
    break the no-glob invariant (``calls['glob'] == seeded``)."""
    calls = {"glob": 0}
    real_glob = ssc.glob.glob

    def counting_glob(*args, **kwargs):
        calls["glob"] += 1
        return real_glob(*args, **kwargs)

    monkeypatch.setattr(ssc.glob, "glob", counting_glob)
    path = tmp_path / "mut.jsonl"
    s = ssc.Sink(str(path), rotate_bytes=10 ** 9, max_total_bytes=10 ** 9)
    seeded = calls["glob"]
    real_write = ssc.Sink.write

    def mutant_write(self, row):
        _ = ssc._measure_total_bytes(self.path)  # MUTANT: per-write glob/stat
        return real_write(self, row)

    monkeypatch.setattr(ssc.Sink, "write", mutant_write)
    for i in range(10):
        s.write({"i": i, "pad": "x" * 20})
    # The no-glob assert from test_write_uses_running_total_no_glob_per_row would fail:
    assert calls["glob"] > seeded, "mutant did not reintroduce per-write glob"
    try:
        assert calls["glob"] == seeded
        raised = False
    except AssertionError:
        raised = True
    assert raised, "no-glob assert must fail under per-write glob mutant"


def test_oserror_reseeds_running_byte_total(tmp_path, monkeypatch):
    """On OSError during write, running total is re-seeded from disk (not left stale)."""
    path = tmp_path / "reseed.jsonl"
    s = ssc.Sink(str(path), rotate_bytes=10 ** 9, max_total_bytes=10 ** 9)
    s.write({"ok": 1})
    before = s._bytes
    assert before > 0
    real_open = open

    def boom(file, mode="r", *args, **kwargs):
        if mode == "a" and str(file) == str(path):
            raise OSError("disk full")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", boom)
    s.write({"fail": 1})
    assert s.failures >= 1
    # Re-seed measured the on-disk file (only the successful first row), not a drifted counter.
    assert s._bytes == ssc._measure_total_bytes(str(path)) == before


def test_size_cap_amber_then_red_before_hard_stop(tmp_path, monkeypatch):
    """70% AMBER log + status; 85% RED STREAM_SIZE_CAP in status (monitor/disk_alert)."""
    import disk_alert
    for row in disk_alert.red_conditions():
        disk_alert.clear_red(row["reason"])
    alarms = []
    monkeypatch.setattr(ssc, "_log", lambda msg: alarms.append(msg))
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    path = tmp_path / "press.jsonl"
    cap = 1000
    s = ssc.Sink(str(path), rotate_bytes=10 ** 9, max_total_bytes=cap)
    # Fill to ~75% -> AMBER
    while s._bytes < int(0.75 * cap):
        s.write({"pad": "y" * 40})
    assert s._alarm_level == "AMBER"
    assert any("level=AMBER" in a for a in alarms)
    status = json.loads((tmp_path / ssc.SIZE_CAP_STATUS_FILE).read_text())
    assert status["level"] == "AMBER"
    # Fill to ~90% -> RED (not yet hard-stopped)
    while s._bytes < int(0.90 * cap) and s.size_cap_hits == 0:
        s.write({"pad": "y" * 40})
    assert s._alarm_level == "RED"
    status = json.loads((tmp_path / ssc.SIZE_CAP_STATUS_FILE).read_text())
    assert status["level"] == "RED" and status["reason"] == "STREAM_SIZE_CAP"
    folded = disk_alert.stream_size_cap_status(tmp_path)
    assert folded["level"] == "RED" and folded["red"][0]["reason"] == "STREAM_SIZE_CAP"
    alert = disk_alert.compute_disk_alert({"total_bytes": 100, "used_bytes": 1, "data_dir": str(tmp_path)})
    assert alert["level"] == "RED"
    assert any(r.get("reason") == "STREAM_SIZE_CAP" for r in alert["retention_red"])
    card = ssc.size_cap_card()
    assert card["stream_sink_count"] == 5 and card["total_all_streams_cap_gib"] == 40.0
    assert card["title"] == "URGENT partial 1 of 5"


def test_size_cap_a5_red_then_green_stays_red(tmp_path, monkeypatch):
    """A5: RED Sink then GREEN Sink → aggregate stays RED (WORST, not last-writer).

    Mutation proof: if `_publish_status` overwrote the whole file with the last
    sink only (no WORST / no sinks map), a later GREEN sink would clear RED and
    this assert on aggregate ``level == "RED"`` would fail.
    """
    import disk_alert
    for row in disk_alert.red_conditions():
        disk_alert.clear_red(row["reason"])
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    alarms = []
    monkeypatch.setattr(ssc, "_log", lambda msg: alarms.append(msg))
    red_path = tmp_path / ssc.BOOK_FILE
    # Seed over-cap bytes so init publishes RED+stop for the book sink.
    red_path.write_bytes(b"x" * 250)
    s_red = ssc.Sink(str(red_path), rotate_bytes=10 ** 9, max_total_bytes=200)
    assert s_red._alarm_level == "RED" and s_red.size_cap_hits >= 1
    status_red = json.loads((tmp_path / ssc.SIZE_CAP_STATUS_FILE).read_text())
    assert status_red["level"] == "RED"
    # GREEN sink (empty trades) must not clear the RED aggregate.
    s_green = ssc.Sink(str(tmp_path / ssc.TRADES_FILE), rotate_bytes=10 ** 9, max_total_bytes=10 ** 9)
    assert s_green._alarm_level == "GREEN"
    status = json.loads((tmp_path / ssc.SIZE_CAP_STATUS_FILE).read_text())
    assert status["level"] == "RED", status
    assert status["sinks"][ssc.BOOK_FILE]["level"] == "RED"
    assert status["sinks"][ssc.TRADES_FILE]["level"] == "GREEN"
    # Mutation: last-writer-wins without WORST would leave level GREEN here.
    last_writer_would_be = status["sinks"][ssc.TRADES_FILE]["level"]
    assert last_writer_would_be == "GREEN"
    assert status["level"] != last_writer_would_be


def test_size_cap_a6_corrupt_or_missing_level_never_green(tmp_path):
    """A6/H2: truncated/invalid JSON or sink with no level → UNKNOWN, never GREEN.

    Also asserts tmp publish uses a unique per-writer name (H2).
    Mutation: treating missing level as GREEN must fail the never-GREEN assert.
    """
    import disk_alert
    dest = tmp_path / ssc.SIZE_CAP_STATUS_FILE
    dest.write_text("{not-json", encoding="utf-8")
    bad = disk_alert.stream_size_cap_status(tmp_path)
    assert bad["level"] == "UNKNOWN" and bad.get("exists") is False
    assert bad["level"] != "GREEN"
    # Missing level on a sink entry → that sink UNKNOWN; roster incomplete → UNKNOWN.
    dest.write_text(json.dumps({
        "schema": ssc.SIZE_CAP_SCHEMA,
        "reason": ssc.SIZE_CAP_REASON,
        "level": "GREEN",
        "stop_writing": False,
        "sinks": {
            ssc.BOOK_FILE: {"path": str(tmp_path / ssc.BOOK_FILE), "stop_writing": False},
            ssc.TRADES_FILE: {"level": "GREEN", "path": str(tmp_path / ssc.TRADES_FILE)},
        },
    }), encoding="utf-8")
    folded = disk_alert.stream_size_cap_status(tmp_path)
    assert folded["sinks"][ssc.BOOK_FILE]["level"] == "UNKNOWN"
    assert folded["level"] == "UNKNOWN"
    assert folded["level"] != "GREEN"
    src = (Path(__file__).parent / "strategist_streams_collector.py").read_text()
    assert 'f"{dest}.tmp.{os.getpid()}.{threading.get_ident()}"' in src or ".tmp." in src
    assert "_size_cap_publish_lock" in src


def test_size_cap_h1_unknown_is_red_on_alarm_health_not_stop_writing(tmp_path, monkeypatch):
    """H1 + HM nit: UNKNOWN aggregate → disk_alert RED + monitor health RED;
    UNKNOWN total must NOT set stop_writing (F1 / HM nit).

    Mutation: if compute_disk_alert only escalates on level==\"RED\" (not UNKNOWN),
    alert.level stays GREEN and this fails. If UNKNOWN forced stop_writing=True,
    the stop_writing assert fails.
    """
    import disk_alert
    import monitor_streams
    for row in disk_alert.red_conditions():
        disk_alert.clear_red(row["reason"])
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    # Partial roster: one GREEN expected sink → missing keys → aggregate UNKNOWN.
    ssc.Sink(str(tmp_path / ssc.BOOK_FILE), rotate_bytes=10 ** 9, max_total_bytes=10 ** 9)
    status = json.loads((tmp_path / ssc.SIZE_CAP_STATUS_FILE).read_text())
    assert status["level"] == "UNKNOWN"
    assert status["stop_writing"] is False, "HM nit: UNKNOWN total must NOT set stop_writing"
    folded = disk_alert.stream_size_cap_status(tmp_path)
    assert folded["level"] == "UNKNOWN"
    assert folded["stop_writing"] is False
    alert = disk_alert.compute_disk_alert({
        "total_bytes": 100, "used_bytes": 1, "data_dir": str(tmp_path),
    })
    assert alert["level"] == "RED"
    assert any(r.get("reason") == "STREAM_SIZE_CAP" for r in alert["retention_red"])
    # Seed a fresh strategist stream part so health walks the strategist path.
    book = tmp_path / ssc.BOOK_FILE
    book.write_text(json.dumps({"s": 100, "b": [[1, 1]], "a": [[2, 1]], "receive_ts": 100.0}) + "\n")
    health = monitor_streams.health(str(tmp_path), now=100.5)
    assert health["stream_size_cap"]["level"] == "UNKNOWN"
    assert health["stream_size_cap"].get("stop_writing") is False
    strat = health["streams"]["bfx_book_10l"]
    assert strat["status"] == "RED"
    assert strat.get("size_cap", {}).get("level") == "UNKNOWN"


def test_size_cap_m2_collector_five_sinks_missing_key_unknown(tmp_path, monkeypatch):
    """M2: real 5-sink Collector; remove one key → reader WORST UNKNOWN."""
    import disk_alert
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    ssc.Collector(str(tmp_path))
    status = json.loads((tmp_path / ssc.SIZE_CAP_STATUS_FILE).read_text())
    assert set(ssc.EXPECTED_STREAM_SINKS) <= set(status["sinks"])
    assert status["level"] == "GREEN"
    assert status["stop_writing"] is False
    del status["sinks"][ssc.EVENTS_FILE]
    status["level"] = "GREEN"  # stale aggregate; reader must recompute
    (tmp_path / ssc.SIZE_CAP_STATUS_FILE).write_text(json.dumps(status), encoding="utf-8")
    folded = disk_alert.stream_size_cap_status(tmp_path)
    assert folded["level"] == "UNKNOWN"
    assert folded["stop_writing"] is False


def test_size_cap_m1_oserror_logged_not_swallowed(tmp_path, monkeypatch):
    """M1: OSError on publish is logged (not silent pass)."""
    logs = []
    monkeypatch.setattr(ssc, "_log", lambda msg: logs.append(msg))
    monkeypatch.setenv("BOT_DATA_DIR", str(tmp_path))
    path = tmp_path / "cap.jsonl"
    s = ssc.Sink(str(path), rotate_bytes=10 ** 9, max_total_bytes=10 ** 9)
    logs.clear()
    real_replace = ssc.os.replace

    def boom(src, dst):
        raise OSError("simulated publish fail")

    monkeypatch.setattr(ssc.os, "replace", boom)
    s._publish_status("GREEN", 0.0, stop_writing=False, need=0)
    assert any("OSError" in m and ssc.SIZE_CAP_STATUS_FILE in m for m in logs), logs
    monkeypatch.setattr(ssc.os, "replace", real_replace)


def test_entrypoint_and_stream_catalog_wired():
    here = Path(__file__).parent
    assert "nice -n 10 python /app/strategist_streams_collector.py" in (here / "fly-entrypoint.sh").read_text()
    import monitor_streams
    assert {"bfx_book_10l", "bfx_trades", "venue_bbo_lighter_paradex"} <= set(monitor_streams.STREAMS)
    src = (here / "strategist_streams_collector.py").read_text()
    assert "import bot" not in src and "api_key" not in src.lower()


def _rows_or_empty(path):
    return _rows(path) if path.exists() else []


def test_row_s_has_no_look_ahead_into_s_plus_1(tmp_path):
    """RK 24 Oct: second s is closed at ~s+1.3-1.5; deltas/trades/deriv received after s+1 must not enter row s."""
    c = ssc.Collector(str(tmp_path), clock=lambda: 0.0)
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "book", "chanId": 1}, chans, 100.0)
    c.on_bfx({"event": "subscribed", "channel": "trades", "chanId": 2}, chans, 100.0)
    c.on_bfx({"event": "subscribed", "channel": "status", "chanId": 7, "key": ssc.DERIV_KEY}, chans, 100.0)
    c.on_bfx([1, [[100, 1, 0.5], [200, 1, -0.5]]], chans, 100.4)
    c.on_bfx([2, "te", [1, 100400, 0.01, 150]], chans, 100.4)
    # arrivals during s+1 = 101.0-101.4, before second 100 is closed
    c.on_bfx([1, [101, 1, 0.7]], chans, 101.2)  # better bid
    c.on_bfx([2, "te", [2, 101200, -0.02, 151]], chans, 101.2)
    deriv = [0] * 23
    deriv[0], deriv[14], deriv[11] = 101200, 150.5, 0.0001
    c.on_bfx([7, deriv], chans, 101.2)
    c.close_second(100, now=101.4)
    book = _book_rows(tmp_path / ssc.BOOK_FILE)
    assert book == [{"s": 100, "b": [[100, 0.5]], "a": [[200, 0.5]]}]
    assert _rows(tmp_path / ssc.BOOK_FILE)[0]["receive_ts"] == 100.4  # the 101.2 delta is not in row 100
    assert _trade_rows(tmp_path / ssc.TRADES_FILE) == [{"s": 100, "tr": [[1, 100400, 0.01, 150]]}]
    assert _rows_or_empty(tmp_path / ssc.DERIV_FILE) == []  # received at 101.2 > s+1
    c.close_second(101, now=102.4)
    book = _rows(tmp_path / ssc.BOOK_FILE)
    assert book[-1]["s"] == 101 and book[-1]["b"] == [[101, 0.7], [100, 0.5]]
    assert _trade_rows(tmp_path / ssc.TRADES_FILE)[-1] == {"s": 101, "tr": [[2, 101200, -0.02, 151]]}
    assert _rows(tmp_path / ssc.DERIV_FILE)[-1]["s"] == 101


def test_ws_events_log_trades_hb_snapshot_and_controls_with_receive_time(tmp_path):
    c = ssc.Collector(str(tmp_path), clock=lambda: 0.0)
    chans = {}
    c.on_bfx({"event": "info", "version": 2}, chans, 99.5)
    c.on_bfx({"event": "subscribed", "channel": "trades", "chanId": 2, "symbol": ssc.BFX_SYMBOL}, chans, 100.0)
    c.on_bfx([2, [[9, 99000, 0.5, 150], [8, 98000, -0.1, 149]]], chans, 100.05)  # post-(re)subscribe snapshot
    c.on_bfx([2, "hb"], chans, 115.0)
    c.on_bfx([2, "te", [10, 115100, 0.01, 151]], chans, 115.1234)
    c.on_bfx_disconnect(now=116.0)
    ev = _rows(tmp_path / ssc.EVENTS_FILE)
    assert [e["ev"] for e in ev] == ["info", "subscribed", "snapshot", "hb", "te", "disconnect"]
    assert ev[0]["r"] == 99.5 and ev[4]["r"] == 115.123 and ev[4]["id"] == 10 and ev[4]["mts"] == 115100
    assert {k: v for k, v in ev[3].items() if k != "mono"} == {"r": 115.0, "ev": "hb", "ch": "trades"}
    assert ev[2]["tr"] == [[9, 99000, 0.5, 150], [8, 98000, -0.1, 149]]
    c.close_second(115, now=116.5)
    # the snapshot is history: never in bfx_trades rows; only the live print is
    assert _trade_rows(tmp_path / ssc.TRADES_FILE) == [{"s": 115, "tr": [[10, 115100, 0.01, 151]]}]


def test_ws_events_file_is_registered_but_not_a_trades_stream():
    import full_reset_no_archive
    import monitor_streams
    assert monitor_streams.STREAMS["bfx_ws_events"] == ssc.EVENTS_FILE
    assert monitor_streams.STREAMS["bfx_trades"] == ssc.TRADES_FILE != ssc.EVENTS_FILE
    # HM #851 C3(c): a real reset classifies the events file and its rotations as DELETE (not a source-text grep)
    for name in (ssc.EVENTS_FILE, ssc.EVENTS_FILE + ".3", ssc.EVENTS_FILE + ".3.gz"):
        assert full_reset_no_archive.classify(name)[0] == "DELETE", name


# ---- HM R2 #851 (24 Oct 2026 AEDT) C1-C3 ----------------------------------------------------------------------------
def _lighter(bid, ask):
    return {"ticker": {"b": {"price": str(bid)}, "a": {"price": str(ask)}}}


def _venue_row(tmp_path, minute):
    return next(r for r in _rows(tmp_path / ssc.VENUE_FILE) if r["m"] == minute)


def test_c1_quote_burst_between_s_plus_1_and_close_keeps_the_s_value(tmp_path):
    c = ssc.Collector(str(tmp_path), clock=lambda: 0.0)
    c.on_lighter(_lighter(99, 100), 100.5)  # the value as of s+1 for s=100
    for i in range(40):  # burst in 101.0-101.2, received before second 100 is closed (more than any count cap)
        c.on_lighter(_lighter(200 + i, 201 + i), 101.0 + i * 0.005)
        c.on_paradex({"bid": 300 + i, "ask": 301 + i}, 101.0 + i * 0.005)
    c.close_second(100, now=101.3)
    c.close_second(101, now=102.3)
    c.close_second(160, now=161.3)  # flush minute 60
    row = _venue_row(tmp_path, 60)
    assert row["lighter"][40] == [99, 100]
    assert row["paradex"][40] is None  # no paradex value received <= s+1
    assert row["lighter"][41] == [239, 240] and row["paradex"][41] == [339, 340]


def test_c1_stall_catch_up_closes_every_second_with_its_own_value(tmp_path):
    c = ssc.Collector(str(tmp_path), clock=lambda: 0.0)
    for k in range(60):  # 10 updates/s for 6 s, then a 5 s stall of the close loop
        t = 100.0 + k * 0.1
        c.on_lighter(_lighter(1000 + k, 1001 + k), t)
    deriv = [0] * 23
    deriv[11] = 0.0001
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "status", "chanId": 7, "key": ssc.DERIV_KEY}, chans, 100.0)
    for k in range(6):
        deriv[0], deriv[14] = int((100 + k) * 1000), 150 + k
        c.on_bfx([7, list(deriv)], chans, 100.5 + k)
    for sec in range(100, 106):  # catch-up loop: all closed at once, ~5 s late
        c.close_second(sec, now=106.4)
    c.close_second(160, now=161.3)
    row = _venue_row(tmp_path, 60)
    assert [row["lighter"][40 + k] for k in range(6)] == [[1009 + 10 * k, 1010 + 10 * k] for k in range(6)]
    assert [r["mark"] for r in _rows(tmp_path / ssc.DERIV_FILE)][:6] == [150, 151, 152, 153, 154, 155]


def test_c1_history_is_bounded_by_time_not_count():
    h = ssc._SecondHistory(keep_s=15)
    for k in range(1000):
        h.add(("v", k), 100.0 + k * 0.01)  # 1000 updates over 10 s -> one entry per second
    assert len(h) == 10 and h.as_of(104) == ("v", 499)
    h.add(("late", 0), 200.0)
    assert len(h) == 1 and h.as_of(150) is None and h.as_of(200) == ("late", 0)


def test_c2_event_rows_carry_mono_and_disconnect_reason(tmp_path):
    ticks = iter([5.0, 5.25, 9.5])
    c = ssc.Collector(str(tmp_path), clock=lambda: 0.0, mono=lambda: next(ticks))
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "trades", "chanId": 2}, chans, 100.0)
    c.on_bfx([2, "hb"], chans, 100.25)
    c.on_bfx_disconnect(now=104.5, reason="ConnectionResetError")
    ev = _rows(tmp_path / ssc.EVENTS_FILE)
    assert [e["mono"] for e in ev] == [5.0, 5.25, 9.5]
    assert ev[-1]["ev"] == "disconnect" and ev[-1]["reason"] == "ConnectionResetError"


def test_c3a_reconnect_mid_second_row_is_the_new_snapshot(tmp_path):
    c = ssc.Collector(str(tmp_path), clock=lambda: 0.0)
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "book", "chanId": 1}, chans, 100.0)
    c.on_bfx([1, [[100, 1, 0.5], [200, 1, -0.5]]], chans, 100.1)  # snapshot A
    c.on_bfx_disconnect(now=100.3, reason="ConnectionResetError")
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "book", "chanId": 5}, chans, 100.5)  # new chanId
    c.on_bfx([5, [[110, 1, 0.7], [190, 1, -0.7]]], chans, 100.6)  # snapshot B, same second
    c.close_second(100, now=101.3)
    assert _book_rows(tmp_path / ssc.BOOK_FILE) == [{"s": 100, "b": [[110, 0.7]], "a": [[190, 0.7]]}]


def test_c3b_hb_only_book_carries_and_a_disconnect_drops_the_carry(tmp_path):
    c, chans = _quiet_book_collector(tmp_path)
    for t in (104.0, 108.0, 110.5):  # only heartbeats on the book channel
        c.on_bfx([1, "hb"], chans, t)
    c.close_second(110, now=111.3)
    rows = _book_rows(tmp_path / ssc.BOOK_FILE)
    assert rows == [{"s": 110, "b": [[100, 0.5]], "a": [[200, 0.5]], "carried": True}]
    c.on_bfx_disconnect(now=111.5, reason="TimeoutError")
    c.close_second(111, now=112.3)  # frozen before the disconnect was seen: dropped anyway (conservative, N1)
    c.close_second(112, now=113.3)
    assert len(_rows(tmp_path / ssc.BOOK_FILE)) == 1


def test_c3d_tu_error_unsubscribed_and_info_rows_are_logged(tmp_path):
    c = ssc.Collector(str(tmp_path), clock=lambda: 0.0)
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "trades", "chanId": 2}, chans, 100.0)
    c.on_bfx([2, "tu", [10, 100100, 0.01, 151]], chans, 100.2)
    c.on_bfx({"event": "error", "msg": "subscribe: dup", "code": 10301}, chans, 100.3)
    c.on_bfx({"event": "unsubscribed", "status": "OK", "chanId": 2}, chans, 100.4)
    c.on_bfx({"event": "info", "code": 20051, "msg": "Stop/Restart Websocket Server"}, chans, 100.5)
    ev = _rows(tmp_path / ssc.EVENTS_FILE)
    assert [e["ev"] for e in ev] == ["subscribed", "tu", "error", "unsubscribed", "info"]
    assert ev[1]["id"] == 10 and ev[1]["mts"] == 100100 and ev[1]["ch"] == "trades"
    assert ev[2]["msg"]["code"] == 10301 and ev[3]["msg"]["chanId"] == 2 and ev[4]["msg"]["code"] == 20051
    assert not (tmp_path / ssc.TRADES_FILE).exists() or _rows(tmp_path / ssc.TRADES_FILE) == []  # tu is not a print


class _FakeWS:
    def __init__(self, collector, frames, end_exc=None):
        self.c, self.frames, self.end_exc, self.sent = collector, list(frames), end_exc, []

    def send(self, s):
        self.sent.append(json.loads(s))

    def recv(self):
        if self.frames:
            return json.dumps(self.frames.pop(0))
        self.c.stop.set()
        if self.end_exc:
            raise self.end_exc
        return json.dumps([1, "hb"])

    def close(self):
        pass


def test_c3e_ws_loop_logs_connect_and_disconnect_reason(tmp_path):
    clock = iter([100.0 + i * 0.1 for i in range(50)])
    c = ssc.Collector(str(tmp_path), clock=lambda: next(clock))
    ws = _FakeWS(c, [{"event": "info", "version": 2},
                     {"event": "subscribed", "channel": "book", "chanId": 1}], ConnectionResetError("reset"))
    c._ws_factory = lambda url: ws
    c._ws_loop("bitfinex", "wss://x", [{"event": "subscribe", "channel": "book"}], c.on_bfx)
    ev = _rows(tmp_path / ssc.EVENTS_FILE)
    assert [e["ev"] for e in ev] == ["connect", "info", "subscribed", "disconnect"]
    assert ev[-1]["reason"] == "ConnectionResetError" and all("mono" in e for e in ev)
    assert ws.sent == [{"event": "subscribe", "channel": "book"}] and c.reconnects["bitfinex"] == 1


def test_c3e_ws_loop_graceful_stop_logs_disconnect_stop(tmp_path):
    clock = iter([100.0 + i * 0.1 for i in range(50)])
    c = ssc.Collector(str(tmp_path), clock=lambda: next(clock))
    ws = _FakeWS(c, [{"event": "subscribed", "channel": "book", "chanId": 1}])
    c._ws_factory = lambda url: ws
    c._ws_loop("bitfinex", "wss://x", [], c.on_bfx)
    ev = _rows(tmp_path / ssc.EVENTS_FILE)
    assert [e["ev"] for e in ev][-1] == "disconnect" and ev[-1]["reason"] == "stop"
    assert c.reconnects["bitfinex"] == 0


def test_book_rows_carry_measured_receive_ts(tmp_path):
    """RK/HM #851: each book row carries the MEASURED wall + monotonic receive time of the last book frame in it
    (rounded up to the ms, never earlier), source WS_FRAME; a carried row keeps the old frame's receive time."""
    ticks = iter([50.0, 50.25, 50.5, 50.75, 51.0, 51.25, 51.5, 51.75, 52.0])
    c = ssc.Collector(str(tmp_path), clock=lambda: 0.0, mono=lambda: next(ticks))
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "book", "chanId": 1}, chans, 100.0)
    c.on_bfx([1, [[100, 1, 0.5], [200, 1, -0.5]]], chans, 100.1234)   # snapshot, mono 50.25
    c.on_bfx([1, [101, 1, 0.5]], chans, 100.6)                          # delta, mono 50.5 (last frame in second 100)
    c.on_bfx([1, [102, 1, 0.5]], chans, 101.2)                          # second 101 (not in row 100)
    c.close_second(100, now=101.4)
    row = _rows(tmp_path / ssc.BOOK_FILE)[0]
    assert row["receive_ts"] == 100.6 and row["receive_mono"] == 50.5 and row["receive_ts_source"] == "WS_FRAME"
    assert row["receive_ts"] <= row["s"] + 1
    assert ssc.book_receive_stamp(100.1234, 1.0)["receive_ts"] == 100.124  # rounded UP, never before the frame
    assert ssc.book_receive_stamp(0.0, 0.0) == {}  # no measured frame time -> no stamp (non-countable downstream)


def test_carried_book_row_keeps_the_frame_receive_time_not_the_bucket(tmp_path):
    """HM #851 nit c: a carried row's receive_ts is the frame it was built from (it is that frame's book)."""
    c, chans = _quiet_book_collector(tmp_path)
    frame = _rows_or_empty(tmp_path / ssc.BOOK_FILE)
    for t in (104.0, 108.0, 110.5):
        c.on_bfx([1, "hb"], chans, t)
    c.close_second(110, now=111.3)
    row = _rows(tmp_path / ssc.BOOK_FILE)[-1]
    assert row["carried"] is True and row["receive_ts"] < 101 and row["receive_ts"] != 110  # the frame, not s
    # HM 02:55 nit: never vacuous - the carried row's receive time IS the snapshot frame's (on_bfx now=100.1), with its mono
    assert frame == []  # no row was closed before the carried one: the frame's time comes from the snapshot itself
    assert row["receive_ts"] == 100.1 and row["receive_ts_source"] == "WS_FRAME" and row["receive_mono"] is not None


def test_trades_deriv_and_quotes_carry_measured_receive_ts(tmp_path):
    """HM #851 nit b: trades (per print + row = last print), deriv and venue quotes carry wall + mono receive."""
    ticks = iter([10.0 + i for i in range(50)])
    c = ssc.Collector(str(tmp_path), clock=lambda: 0.0, mono=lambda: next(ticks))
    chans = {}
    c.on_bfx({"event": "subscribed", "channel": "trades", "chanId": 2}, chans, 100.0, mono=1.0)
    c.on_bfx({"event": "subscribed", "channel": "status", "chanId": 7, "key": ssc.DERIV_KEY}, chans, 100.0, mono=1.0)
    c.on_bfx([2, "te", [10, 100200, -0.01, 150.5]], chans, 100.2001, mono=2.0)
    c.on_bfx([2, "te", [11, 100300, 0.01, 150.6]], chans, 100.3, mono=3.0)
    deriv = [0] * 30
    deriv[2], deriv[3], deriv[8], deriv[11], deriv[17] = 150.0, 150.1, 0.0001, 0.0001, 5000.0
    c.on_bfx([7, deriv], chans, 100.4, mono=4.0)
    c.on_lighter({"ticker": {"b": {"price": "99.5"}, "a": {"price": "100.5"}}}, 100.5, mono=5.0)
    c.on_paradex({"bid": "99.0", "ask": "101.0"}, 100.6, mono=6.0)
    c.close_second(100, now=101.3)
    tr = _rows(tmp_path / ssc.TRADES_FILE)[0]
    assert tr["rx"] == [[100.201, 2.0], [100.3, 3.0]] and tr["receive_ts"] == 100.3 and tr["receive_mono"] == 3.0
    assert tr["receive_ts_source"] == "WS_FRAME"
    d = _rows_or_empty(tmp_path / ssc.DERIV_FILE)
    assert d and d[0]["receive_ts"] == 100.4 and d[0]["receive_mono"] == 4.0
    c.close_second(160, now=161.3)  # roll the minute
    v = _rows(tmp_path / ssc.VENUE_FILE)[0]
    assert v["rx"]["lighter"][40] == [100.5, 5.0] and v["rx"]["paradex"][40] == [100.6, 6.0]
    assert v["lighter"][40] == [99.5, 100.5]


def test_ws_loop_reads_wall_and_mono_at_the_same_receive_point(tmp_path):
    """HM #851 nit a: the handler gets the (wall, mono) pair read together right after recv, before parsing."""
    reads = []
    wall = iter([500.0 + i for i in range(20)])
    mono = iter([7.0 + i for i in range(20)])
    c = ssc.Collector(str(tmp_path), clock=lambda: (reads.append("w"), next(wall))[1],
                      mono=lambda: (reads.append("m"), next(mono))[1])
    got = []

    class _WS:
        n = 0

        def send(self, _):
            pass

        def recv(self):
            _WS.n += 1
            if _WS.n > 1:
                c.stop.set()
                raise TimeoutError("timed out")
            return "{}"

        def close(self):
            pass

    c._ws_factory = lambda url: _WS()
    c._ws_loop("lighter", "wss://x", [], lambda msg, now, mono=None: got.append((now, mono)))
    assert got and got[0][1] is not None
    assert got[0] == (500.0, 7.0) and reads[:2] == ["w", "m"]


def test_bfx_ws_loop_receive_point_reaches_the_book_row_through_the_bitfinex_handler_signature(tmp_path):
    """HM 02:55 nit: the receive point is read once after ws.recv() and passed through the BITFINEX handler signature
    handler(msg, chans, now, mono=...) into the book row's receive_ts / receive_mono (not a re-read inside on_bfx)."""
    wall = iter([100.0 + 0.05 * i for i in range(200)])
    mono = iter([7.0 + i for i in range(200)])
    c = ssc.Collector(str(tmp_path), clock=lambda: next(wall), mono=lambda: next(mono))
    frames = iter([json.dumps({"event": "subscribed", "channel": "book", "chanId": 1}),
                   json.dumps([1, [[100, 1, 0.5], [200, 1, -0.5]]]), json.dumps([1, [101, 1, 0.5]])])
    got = []

    class _WS:
        def send(self, _):
            pass

        def recv(self):
            try:
                return next(frames)
            except StopIteration:
                c.stop.set()
                raise TimeoutError("timed out")

        def close(self):
            pass

    def handler(msg, chans, now, mono=None):  # the exact bitfinex call shape used by _ws_loop
        got.append((now, mono))
        c.on_bfx(msg, chans, now, mono=mono)

    c._ws_factory = lambda url: _WS()
    c._ws_loop("bitfinex", "wss://x", [], handler)
    assert len(got) == 3 and all(m is not None for _, m in got)
    rx_wall, rx_mono = got[-1]  # the last book frame's recv-point pair
    c.close_second(int(rx_wall), now=int(rx_wall) + 1.3)
    row = _rows(tmp_path / ssc.BOOK_FILE)[-1]
    assert row["receive_ts"] == ssc.book_receive_stamp(rx_wall, rx_mono)["receive_ts"]
    assert row["receive_mono"] == round(rx_mono, 3) and row["receive_ts_source"] == "WS_FRAME"

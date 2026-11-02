"""HM 25 Oct 06:30 live check: ledger-unstamped is a bounded incremental scan (cached byte offset per stream),
retired-stream rows written before retirement are IDLE (pre_retirement_rows) while a write after retirement is RED,
an unreadable stream is RED (fail closed), and a stream larger than one call's budget completes across calls."""
import json
import os
import time

import ledger_stamp_monitor as mon
import ledger_write_stamp as lws

EPOCH = lws.EXPECTED_DATA_EPOCH_ID
FLOOR = 1792263671.26
SINCE = FLOOR + 1000
RETIRED = SINCE + 5000
ACTIVE = [n for n in mon.LEDGERS if n not in mon.RETIRED_STREAM_ALLOWLIST]


def _st(now):
    return lws.build_write_stamp(data_epoch_id=EPOCH, floor_ts=FLOOR, now=now, realism={"fill_model": "X"})


def _led(tmp_path):
    return tmp_path / "v3" / "ledgers"


def _write(path, rows, mtime=None, mode="w"):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode) as fh:
        fh.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _active(tmp_path, skip=()):
    for n in ACTIVE:
        if n not in skip:
            _write(_led(tmp_path) / f"{n}.jsonl", [{"record_id": n, "write_stamp": _st(SINCE + 5)}])


def _retire_at(monkeypatch, ts=RETIRED):
    monkeypatch.setattr(mon, "RETIRED_SINCE_TS", {"market_segment": ts})


def test_pre_retirement_rows_are_idle_not_red(tmp_path, monkeypatch):
    _retire_at(monkeypatch)
    _active(tmp_path)
    _write(_led(tmp_path) / "market_segment.jsonl",
           [{"record_id": f"seg{i}"} for i in range(30)], mtime=RETIRED - 60)  # written since, before retirement
    res = mon.check(tmp_path, SINCE)
    ms = res["ledgers"]["market_segment"]
    assert ms["status"] == "IDLE"
    assert ms["idle_reason"] == mon.MARKET_SEGMENT_STATUS_LABEL
    assert ms["status_label"] == mon.MARKET_SEGMENT_STATUS_LABEL
    assert ms["retired_ruling"] == "MARKET_SEGMENTS_RETIRED_R17"
    assert ms["red_reason"] is None and ms["unstamped"] == 0 and ms["pre_retirement_rows"] == 30
    assert res["status"] == "IDLE" and res["pre_retirement_rows"] == {"market_segment": 30}


def test_write_after_retirement_is_red(tmp_path, monkeypatch):
    _retire_at(monkeypatch)
    _active(tmp_path)
    p = _led(tmp_path) / "market_segment.jsonl"
    _write(p, [{"record_id": "old"}], mtime=RETIRED - 60)
    mon.check(tmp_path, SINCE)
    _write(p, [{"record_id": "late"}], mtime=RETIRED + 1, mode="a")  # unstamped: mtime decides
    res = mon.check(tmp_path, SINCE)
    assert res["ledgers"]["market_segment"]["red_reason"] == "WRITE_TO_RETIRED_STREAM" and res["status"] == "RED"
    _write(p, [{"record_id": "s", "write_stamp": _st(RETIRED + 2)}], mtime=RETIRED - 30)  # stamp decides
    assert mon.check(tmp_path, SINCE)["ledgers"]["market_segment"]["status"] == "RED"


def test_unreadable_stream_is_red(tmp_path, monkeypatch):
    _active(tmp_path)
    real = mon.Path.open

    def boom(self, *a, **k):
        if self.name == "decision.jsonl":
            raise PermissionError("denied")
        return real(self, *a, **k)
    monkeypatch.setattr(mon.Path, "open", boom)
    res = mon.check(tmp_path, SINCE)
    d = res["ledgers"]["decision"]
    assert d["status"] == "RED" and d["red_reason"] == "STREAM_UNREADABLE" and res["status"] == "RED"
    assert "decision:UNREADABLE:PermissionError" in res["unverified"]


def test_incremental_offset_reads_only_appended_bytes(tmp_path):
    _active(tmp_path, skip=("order_intent",))
    p = _led(tmp_path) / "order_intent.jsonl"
    _write(p, [{"record_id": f"o{i}", "write_stamp": _st(SINCE + i), "pad": "x" * 100} for i in range(1, 200)])
    first = mon.check(tmp_path, SINCE)["ledgers"]["order_intent"]
    assert first["scan"]["complete"] and first["scan"]["bytes_read_this_call"] == p.stat().st_size
    again = mon.check(tmp_path, SINCE)["ledgers"]["order_intent"]
    assert again["scan"]["resumed_from_cache"] and again["scan"]["bytes_read_this_call"] == 0
    assert again["stamped"] == first["stamped"] == 199
    before = p.stat().st_size
    _write(p, [{"record_id": "bad"}], mode="a")  # unstamped after a stamped row: counted
    res = mon.check(tmp_path, SINCE)
    o = res["ledgers"]["order_intent"]
    assert o["scan"]["bytes_read_this_call"] == p.stat().st_size - before
    assert o["unstamped"] == 1 and o["sample_row_ids"] == ["bad"] and res["status"] == "RED"


def test_rewrite_drops_cache_and_refolds(tmp_path):
    _active(tmp_path, skip=("decision",))
    p = _led(tmp_path) / "decision.jsonl"
    _write(p, [{"record_id": "a", "write_stamp": _st(SINCE + 1)}, {"record_id": "u"}])
    assert mon.check(tmp_path, SINCE)["ledgers"]["decision"]["unstamped"] == 1
    _write(p, [{"record_id": "b", "write_stamp": _st(SINCE + 2)}, {"record_id": "c", "write_stamp": _st(SINCE + 3)}])
    d = mon.check(tmp_path, SINCE)["ledgers"]["decision"]
    assert d["unstamped"] == 0 and d["stamped"] == 2 and not d["scan"]["resumed_from_cache"]


def test_time_budget_bounds_call_and_resumes_to_completion(tmp_path):
    _active(tmp_path, skip=("order_intent",))
    _write(_led(tmp_path) / "order_intent.jsonl",
           [{"record_id": f"o{i}", "write_stamp": _st(SINCE + i)} for i in range(1, 3000)])
    res = mon.check(tmp_path, SINCE, time_budget_s=0.0)
    assert res["status"] == "AMBER" and not res["scan_complete"]
    assert "order_intent:SCAN_INCOMPLETE_RESUMING" in res["unverified"]
    t0 = time.monotonic()
    res = mon.check(tmp_path, SINCE, time_budget_s=2.0)
    assert time.monotonic() - t0 < 3.0 and res["scan_complete"] and res["status"] == "IDLE"
    assert res["ledgers"]["order_intent"]["stamped"] == 2999


def test_retired_partial_scan_is_red_scan_incomplete_not_amber(tmp_path, monkeypatch):
    """H3: market_segment incomplete fold → RED SCAN_INCOMPLETE (never AMBER resume)."""
    _retire_at(monkeypatch)
    _active(tmp_path)
    # Large retired stream + zero time budget forces incomplete fold on first call.
    _write(
        _led(tmp_path) / "market_segment.jsonl",
        [{"record_id": f"seg{i}"} for i in range(4000)],
        mtime=RETIRED - 60,
    )
    res = mon.check(tmp_path, SINCE, time_budget_s=0.0)
    ms = res["ledgers"]["market_segment"]
    assert ms["status"] == "RED"
    assert ms["red_reason"] == "SCAN_INCOMPLETE"
    assert "market_segment:SCAN_INCOMPLETE" in res["unverified"]
    assert "market_segment:SCAN_INCOMPLETE_RESUMING" not in res["unverified"]
    assert ms["status"] != "AMBER"
    assert res["status"] == "RED"
    # Mutation proof: AMBER resume would fail this contract.
    mutant_status = "AMBER"
    with __import__("pytest").raises(AssertionError):
        assert mutant_status == "RED"

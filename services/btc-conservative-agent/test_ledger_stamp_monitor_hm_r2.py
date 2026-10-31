"""HM R2 on #872: non-allowlisted idle stream is RED; any write to a retired stream is RED; IDLE needs the
explicit allowlist AND every writer's retirement flag; ambiguous mtime boundary warns, never skips silently."""
import json
import os

import ledger_stamp_monitor as mon
import ledger_write_stamp as lws

EPOCH = lws.EXPECTED_DATA_EPOCH_ID
FLOOR = 1792263671.26
SINCE = FLOOR + 1000
ACTIVE = [n for n in mon.LEDGERS if n not in mon.RETIRED_STREAM_ALLOWLIST]


def _st(now):
    return lws.build_write_stamp(data_epoch_id=EPOCH, floor_ts=FLOOR, now=now, realism={"fill_model": "X"})


def _write(path, rows, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _active(tmp_path, skip=()):
    for n in ACTIVE:
        if n not in skip:
            _write(tmp_path / "v3" / "ledgers" / f"{n}.jsonl", [{"record_id": n, "write_stamp": _st(SINCE + 5)}])


def test_allowlist_is_explicit_and_market_segment_only():
    assert mon.RETIRED_STREAM_ALLOWLIST == {"market_segment": "MARKET_SEGMENTS_RETIRED_R17"}
    flags = mon.retirement_flags("market_segment")
    assert set(flags) == {"research_v3_bridge.MARKET_SEGMENT_WRITES_RETIRED",
                          "research_v3_future_paths.SEGMENT_WRITES_RETIRED_R17",
                          "recover_archived_future_paths.SEGMENT_WRITES_RETIRED_R17",
                          "qualification_horizon_index.SEGMENT_WRITES_RETIRED_R17"}
    assert all(v is True for v in flags.values())


def test_non_allowlisted_stream_without_writes_since_is_red_not_idle(tmp_path):
    _active(tmp_path, skip=("execution",))
    _write(tmp_path / "v3" / "ledgers" / "execution.jsonl", [{"record_id": "old", "write_stamp": _st(SINCE - 50)}],
           mtime=SINCE - 50)
    res = mon.check(tmp_path, SINCE)
    assert res["ledgers"]["execution"]["status"] == "RED"
    assert res["red_streams"]["execution"] == "NO_WRITE_SINCE_NOT_ALLOWLISTED" and res["status"] == "RED"


def test_missing_non_allowlisted_stream_is_red(tmp_path):
    _active(tmp_path, skip=("lifecycle",))
    res = mon.check(tmp_path, SINCE)
    assert res["ledgers"]["lifecycle"]["status"] == "RED" and res["status"] == "RED"


def test_stamped_write_to_retired_stream_is_red(tmp_path):
    _active(tmp_path)
    _write(tmp_path / "v3" / "ledgers" / "market_segment.jsonl",
           [{"record_id": "market-segment:new", "write_stamp": _st(SINCE + 9)}])
    res = mon.check(tmp_path, SINCE)
    ms = res["ledgers"]["market_segment"]
    assert ms["status"] == "RED" and ms["red_reason"] == "WRITE_TO_RETIRED_STREAM" and ms["unstamped"] == 0
    assert res["status"] == "RED"


def test_retired_idle_only_when_every_flag_true(tmp_path, monkeypatch):
    import qualification_horizon_index as q
    _active(tmp_path)
    _write(tmp_path / "v3" / "ledgers" / "market_segment.jsonl", [{"record_id": "x"}], mtime=SINCE - 100)
    res = mon.check(tmp_path, SINCE)
    assert res["ledgers"]["market_segment"]["status"] == "IDLE" and res["status"] == "IDLE"
    assert res["idle_ledgers"] == {"market_segment": mon.MARKET_SEGMENT_STATUS_LABEL}
    monkeypatch.setattr(q, "SEGMENT_WRITES_RETIRED_R17", False)
    res = mon.check(tmp_path, SINCE)
    assert res["ledgers"]["market_segment"]["status"] == "RED"
    assert res["red_streams"]["market_segment"] == "RETIREMENT_FLAG_MISMATCH"  # HM #872 nit


def test_ambiguous_mtime_boundary_warns_and_contradiction_is_not_green(tmp_path):
    _active(tmp_path, skip=("decision",))
    led = tmp_path / "v3" / "ledgers"
    _write(led / "decision.jsonl", [{"record_id": "d"}], mtime=SINCE - 10)
    _write(led / "decision.jsonl.1", [{"record_id": "d0", "write_stamp": _st(SINCE - 20)}], mtime=SINCE - 20)
    res = mon.check(tmp_path, SINCE)
    assert any(w.startswith("MTIME_BOUNDARY_UNCONFIRMED_BY_STAMP") for w in res["warnings"])
    # restored copy: mtime preserved before since, but the last row was written after since
    _write(led / "decision.jsonl", [{"record_id": "d", "write_stamp": _st(SINCE + 30)}], mtime=SINCE - 10)
    res = mon.check(tmp_path, SINCE)
    assert any(w.startswith("MTIME_BOUNDARY_CONTRADICTED_BY_STAMP") for w in res["warnings"])
    assert "decision:MTIME_BOUNDARY_AMBIGUOUS:decision.jsonl" in res["unverified"]
    assert res["ledgers"]["decision"]["status"] in ("RED", "AMBER") and res["status"] != "GREEN"


def test_allowlist_single_source_of_truth():
    import retired_streams
    assert mon.RETIRED_STREAM_ALLOWLIST is retired_streams.RETIRED_STREAM_ALLOWLIST


def test_flag_mismatch_red_even_with_writes(tmp_path, monkeypatch):
    import research_v3_bridge as b
    _active(tmp_path)
    _write(tmp_path / "v3" / "ledgers" / "market_segment.jsonl", [{"record_id": "x"}], mtime=SINCE + 100)
    monkeypatch.setattr(b, "MARKET_SEGMENT_WRITES_RETIRED", False)
    res = mon.check(tmp_path, SINCE)
    assert res["red_streams"]["market_segment"] == "RETIREMENT_FLAG_MISMATCH"


def test_flag_mismatch_red_on_capped_incomplete_scan(tmp_path, monkeypatch):
    """HM PR880 B1: a mismatch must stay RED even when the scan is capped (budget/deadline) and incomplete."""
    import research_v3_bridge as b
    _active(tmp_path)
    _write(tmp_path / "v3" / "ledgers" / "market_segment.jsonl",
           [{"record_id": f"x{i}", "pad": "p" * 200} for i in range(50)], mtime=SINCE + 100)
    monkeypatch.setattr(b, "MARKET_SEGMENT_WRITES_RETIRED", False)
    for kw in ({"max_bytes": 64}, {"time_budget_s": 0}):
        res = mon.check(tmp_path, SINCE, **kw)
        ms = res["ledgers"]["market_segment"]
        assert ms["scan"]["complete"] is False, kw
        assert ms["red_reason"] == "RETIREMENT_FLAG_MISMATCH" and ms["status"] == "RED", kw
        assert res["red_streams"]["market_segment"] == "RETIREMENT_FLAG_MISMATCH" and res["status"] == "RED", kw

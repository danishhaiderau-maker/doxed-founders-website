"""HM 25 Oct M1/M2/M3 + Q3 + epoch config: monitor never trusts fallback keys, scans every generation, reports
legacy rows apart, shows retired market_segment as IDLE (never GREEN); /api/state total_trades = disk; epoch read
from the one configured value with every other epoch rejected."""
import json
import os
from pathlib import Path

import pytest

import api_state_analytics as asa
import ledger_stamp_monitor as mon
import ledger_write_stamp as lws
import research_freeze

EPOCH = lws.EXPECTED_DATA_EPOCH_ID
FLOOR = 1792263671.26
SINCE = FLOOR + 1000
CENSUS = Path(__file__).resolve().parent / "ci" / "route_census_fields.json"


def _st(now):
    return lws.build_write_stamp(data_epoch_id=EPOCH, floor_ts=FLOOR, now=now, realism={"fill_model": "X"})


def _write(path, rows, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def _led(tmp_path):
    return tmp_path / "v3" / "ledgers"


def test_m1_new_unstamped_row_with_old_fallback_ts_is_counted_not_a_boundary(tmp_path):
    _write(_led(tmp_path) / "decision.jsonl", [
        {"record_id": "old", "write_stamp": _st(SINCE - 10)},
        {"record_id": "hidden", "decision_ts": SINCE - 500},  # new row, stale event key: must NOT end the scan
        {"record_id": "new", "write_stamp": _st(SINCE + 10)},
        {"record_id": "u", "ts": SINCE - 900}])
    res = mon.check(tmp_path, SINCE)
    d = res["ledgers"]["decision"]
    assert res["status"] == "RED" and d["unstamped"] == 2 and set(d["sample_row_ids"]) == {"u", "hidden"}
    assert d["boundary"] == "WRITTEN_AT_TS" and res["boundary_key"] == "write_stamp.written_at_ts"


def test_m1_stamp_without_written_at_ts_is_unstamped(tmp_path):
    bad = dict(_st(SINCE + 5)); bad.pop("written_at_ts")
    _write(_led(tmp_path) / "lifecycle.jsonl", [{"record_id": "o", "write_stamp": _st(SINCE - 5)},
                                                {"record_id": "b", "write_stamp": bad}])
    res = mon.check(tmp_path, SINCE)
    assert res["status"] == "RED" and res["ledgers"]["lifecycle"]["unstamped"] == 1


def test_m2_rotated_generations_are_scanned(tmp_path):
    led = _led(tmp_path)
    _write(led / "execution.jsonl.1", [{"record_id": "o", "write_stamp": _st(SINCE - 5)},
                                       {"record_id": "rot-unstamped"}])
    _write(led / "execution.jsonl", [{"record_id": "n", "write_stamp": _st(SINCE + 5)}])
    res = mon.check(tmp_path, SINCE)
    e = res["ledgers"]["execution"]
    assert res["status"] == "RED" and e["sample_row_ids"] == ["rot-unstamped"]
    assert e["generations_scanned"] == ["execution.jsonl", "execution.jsonl.1"]
    assert [p.name for p in mon.generation_paths(led, "execution")] == ["execution.jsonl", "execution.jsonl.1"]


def test_m3_legacy_rows_below_first_stamp_reported_apart_not_red(tmp_path):
    _write(_led(tmp_path) / "order_intent.jsonl",
           [{"record_id": f"legacy{i}", "submitted_ts": FLOOR + i} for i in range(5)]
           + [{"record_id": "n", "write_stamp": _st(SINCE + 5)}])
    res = mon.check(tmp_path, FLOOR)  # window includes the pre-fix period
    o = res["ledgers"]["order_intent"]
    assert o["unstamped"] == 0 and o["legacy_unstamped_before_first_stamp"] == 5 and o["status"] == "GREEN"
    assert res["legacy_unstamped_before_first_stamp"] == 5


def test_m3_cap_never_green_and_never_hides(tmp_path):
    _write(_led(tmp_path) / "order_intent.jsonl",
           [{"record_id": f"legacy{i}", "pad": "x" * 200} for i in range(50)]
           + [{"record_id": "n", "write_stamp": _st(SINCE + 5)}])
    res = mon.check(tmp_path, FLOOR, max_bytes=2000)
    assert res["status"] == "AMBER" and res["alert"] and "order_intent:SCAN_BUDGET_EXHAUSTED" in res["unverified"]
    assert res["ledgers"]["order_intent"]["unclassified_at_cap"] > 0


def test_unstamped_only_stream_written_since_is_red_fail_closed(tmp_path):
    _write(_led(tmp_path) / "opportunity.jsonl", [{"record_id": "a"}, {"record_id": "b"}])
    res = mon.check(tmp_path, SINCE)  # file mtime is now > since
    assert res["status"] == "RED" and res["ledgers"]["opportunity"]["unstamped"] == 2


def test_retired_market_segment_idle_with_reason_never_green(tmp_path):
    _write(_led(tmp_path) / "market_segment.jsonl", [{"record_id": "market-context:x"}], mtime=SINCE - 100)
    _write(_led(tmp_path) / "decision.jsonl", [{"record_id": "n", "write_stamp": _st(SINCE + 5)}])
    res = mon.check(tmp_path, SINCE)
    ms = res["ledgers"]["market_segment"]
    assert ms["status"] == "IDLE" and ms["idle_reason"] == "MARKET_SEGMENTS_RETIRED_R17"
    assert res["status"] == "IDLE" and res["idle_ledgers"] == {"market_segment": "MARKET_SEGMENTS_RETIRED_R17"}
    assert mon.main(["--data-dir", str(tmp_path), "--since", str(SINCE)]) == 3


def test_market_segment_written_since_unstamped_is_red_not_idle(tmp_path):
    _write(_led(tmp_path) / "market_segment.jsonl", [{"record_id": "market-segment:x"}])
    res = mon.check(tmp_path, SINCE)
    assert res["status"] == "RED" and res["ledgers"]["market_segment"]["status"] == "RED"


def test_census_fixture_fields_present(tmp_path):
    fx = json.loads(CENSUS.read_text())["routes"]
    _write(_led(tmp_path) / "decision.jsonl", [{"record_id": "n", "write_stamp": _st(SINCE + 5)}])
    res = mon.check(tmp_path, SINCE)
    spec = fx["/api/monitor/ledger-unstamped"]
    assert set(spec["top"]) <= set(res)
    for info in res["ledgers"].values():
        assert set(spec["per_ledger"]) <= set(info)
    a, _ = asa.overlay({"total_trades": 1}, None, str(tmp_path / "none.csv"))
    assert set(fx["/api/state"]["analytics"]) <= set(a)


def test_api_state_total_trades_is_disk_rows(tmp_path):
    p = tmp_path / "trades_3factor.csv"
    p.write_text('id,note\na,"multi\nline"\nb,x\n')
    a, ts = asa.overlay({"total_trades": 260, "win_rate": 1.0}, "T", str(p))
    assert a["total_trades"] == 2 and a["total_trades_memory"] == 260 and a["total_trades_mismatch"] is True
    assert a["total_trades_source"] == "DISK_CSV" and ts == "T" and a["win_rate"] == 1.0
    with p.open("a") as fh:
        fh.write("c,y\n")
    assert asa.overlay({"total_trades": 3}, None, str(p))[0]["total_trades"] == 3
    missing = asa.overlay({"total_trades": 3}, None, str(tmp_path / "gone.csv"))[0]
    assert missing["total_trades"] is None and missing["total_trades_mismatch"] is True


def test_epoch_read_from_single_config_and_others_rejected(monkeypatch):
    assert EPOCH == research_freeze.FREEZE_DATA_EPOCH_ID == "ce-20261004-v31-freeze21b"
    monkeypatch.setenv("DATA_EPOCH_ID", EPOCH)
    assert lws.build_write_stamp(data_epoch_id=EPOCH, floor_ts=FLOOR, now=FLOOR + 1,
                                 realism={"fill_model": "X"})["countable"] is True
    other = lws.build_write_stamp(data_epoch_id="ce-20261025-next", floor_ts=FLOOR, now=FLOOR + 1,
                                  realism={"fill_model": "X"})
    assert other["non_countable_reason"] == lws.REASON_WRONG_EPOCH
    monkeypatch.setenv("DATA_EPOCH_ID", "ce-20261025-next")  # env drifting off the pin fails closed
    assert lws.configured_epoch_id() is None
    assert lws.build_write_stamp(data_epoch_id=EPOCH, floor_ts=FLOOR, now=FLOOR + 1,
                                 realism={"fill_model": "X"})["countable"] is False


def test_no_epoch_literal_left_in_stamp_and_tile_table():
    here = Path(__file__).resolve().parent
    for rel in ("ledger_write_stamp.py", "research/tile_realism_table.py"):
        assert '"ce-20261004-v31-freeze21b"' not in (here / rel).read_text()

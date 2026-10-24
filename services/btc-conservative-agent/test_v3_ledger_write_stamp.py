"""RK R8 fix-now (24 Oct): every V3 ledger writer stamps at write time through ONE helper; fail closed otherwise."""
import json
import re
from pathlib import Path
from unittest import mock

import pytest

import analyzer_epoch_guard as aeg
import data_epoch as de
import ledger_stamp_monitor as mon
import ledger_write_stamp as lws
from research_v3_contract import LEDGER_NAMES
from research_v3_store import V3EvidenceStore

EPOCH = lws.EXPECTED_DATA_EPOCH_ID
FLOOR = 1792263671.26
HERE = Path(__file__).resolve().parent


def _root(tmp_path, floor=FLOOR):
    if floor is not None:
        (tmp_path / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": floor}))
    return tmp_path


def _rows(root, ledger):
    path = Path(root) / "v3" / "ledgers" / f"{ledger}.jsonl"
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


@pytest.mark.parametrize("ledger", ["decision", "lifecycle", "order_intent", "market_segment", "execution"])
def test_every_ledger_row_is_stamped_at_write_time(tmp_path, ledger):
    root = _root(tmp_path)
    with mock.patch.object(de, "active_epoch_id", return_value=EPOCH), \
         mock.patch.object(lws.time, "time", return_value=FLOOR + 100):
        res = V3EvidenceStore(root, epoch_id="ep").append(ledger, {"record_id": f"{ledger}:x", "episode_id": "e1"})
    assert res.get("written"), res
    row = _rows(root, ledger)[-1]
    stamp = row["write_stamp"]
    assert row["data_epoch_id"] == EPOCH and stamp["data_epoch_id"] == EPOCH
    assert stamp["fresh_start_epoch_ts"] == FLOOR and stamp["written_at_ts"] == FLOOR + 100
    assert stamp["realism"]["fill_model"] and stamp["countable"] is True
    assert lws.row_non_countable_reason(row) is None
    # The guard can now place the row (it was UNSTAMPED before this fix).
    assert aeg.nested_ts_class(row, FLOOR) == aeg.NESTED_OK
    guard = aeg.EpochGuard({"epoch_id": EPOCH, "started_at_ts": FLOOR - 10}, floor_ts=FLOOR, require_floor=True)
    assert guard.admit_v3_ledger_row(f"v3/ledgers/{ledger}.jsonl", row) is True


def test_unstamped_row_before_fix_is_unplaceable():
    assert aeg.nested_ts_class({"record_id": "decision:scan-1", "decision_stage": "SCAN"}, FLOOR) == aeg.NESTED_UNSTAMPED


@pytest.mark.parametrize("epoch,floor,now,realism,reason", [
    (None, FLOOR, FLOOR + 1, {"fill_model": "X"}, lws.REASON_NO_EPOCH),
    ("ce-other", FLOOR, FLOOR + 1, {"fill_model": "X"}, lws.REASON_WRONG_EPOCH),
    (EPOCH, None, FLOOR + 1, {"fill_model": "X"}, lws.REASON_NO_FLOOR),
    (EPOCH, FLOOR, FLOOR - 1, {"fill_model": "X"}, lws.REASON_BEFORE_FLOOR),
    (EPOCH, FLOOR, FLOOR + 1, None, lws.REASON_NO_REALISM),
])
def test_fail_closed_stamp_is_non_countable(epoch, floor, now, realism, reason):
    stamp = lws.build_write_stamp(data_epoch_id=epoch, floor_ts=floor, now=now, realism=realism)
    assert stamp["countable"] is False and stamp["non_countable_reason"] == reason
    assert lws.row_non_countable_reason({"write_stamp": stamp}) == reason


def test_fail_closed_rows_are_written_but_never_admitted(tmp_path):
    root = _root(tmp_path, floor=None)  # fresh_start_epoch.json missing
    with mock.patch.object(de, "active_epoch_id", return_value=None):
        V3EvidenceStore(root, epoch_id="ep").append("lifecycle", {"record_id": "lifecycle:y", "episode_id": "e"})
    row = _rows(root, "lifecycle")[-1]
    assert row["write_stamp"]["countable"] is False
    assert row["write_stamp"]["non_countable_reason"] == lws.REASON_NO_EPOCH
    guard = aeg.EpochGuard({"epoch_id": EPOCH, "started_at_ts": FLOOR - 10}, floor_ts=FLOOR, require_floor=True)
    assert guard.admit_v3_ledger_row("v3/ledgers/lifecycle.jsonl", row) is False
    assert aeg.NESTED_NON_COUNTABLE in guard.rejected["v3/ledgers/lifecycle.jsonl"]


def test_guard_lists_non_countable_and_unstamped_rows_as_unplaced(tmp_path):
    path = tmp_path / "decision.jsonl"
    good = lws.build_write_stamp(data_epoch_id=EPOCH, floor_ts=FLOOR, now=FLOOR + 5, realism={"fill_model": "X"})
    bad = lws.build_write_stamp(data_epoch_id=None, floor_ts=FLOOR, now=FLOOR + 5, realism={"fill_model": "X"})
    rows = [{"record_id": "a", "write_stamp": good, "data_epoch_id": EPOCH},
            {"record_id": "b", "write_stamp": bad}, {"record_id": "c"}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    manifest = {"epoch_id": EPOCH, "started_at_ts": FLOOR - 10}
    listed = list(aeg.file_unplaced_rows(path, "v3/ledgers/decision.jsonl", manifest, floor_ts=FLOOR))
    assert sorted(r["row_id"] for r in listed) == ["b", "c"]
    counts = aeg.file_epoch_row_counts(path, "v3/ledgers/decision.jsonl", manifest, floor_ts=FLOOR)
    assert counts["unstamped"] == 2


def test_existing_stamp_is_replayed_unchanged_for_idempotency(tmp_path):
    stamp = lws.build_write_stamp(data_epoch_id=EPOCH, floor_ts=FLOOR, now=FLOOR + 1, realism={"fill_model": "X"})
    row = {"record_id": "r", "write_stamp": stamp}
    assert lws.stamp_ledger_row(row, data_dir=tmp_path)["write_stamp"] is stamp


def test_no_module_writes_v3_ledgers_outside_the_store():
    """R4 pin: the store's append is the only writer of v3/ledgers/<name>.jsonl rows."""
    allowed = {"research_v3_store.py", "lifecycle_tail_repair.py", "lifecycle_orphan_head_repair.py"}
    pattern = re.compile(r"ledgers[\"'/ ,)]*(?:/|,)\s*f?[\"'](?:%s)\.jsonl" % "|".join(LEDGER_NAMES))
    offenders = []
    for path in HERE.rglob("*.py"):
        if path.name.startswith("test_") or path.name in allowed:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in pattern.finditer(text):
            window = text[m.end():m.end() + 200]
            if re.search(r"\.open\([\"']a|open\([^)]*[\"']a[b]?[\"']", window):
                offenders.append(f"{path.name}:{text[:m.start()].count(chr(10)) + 1}")
    assert offenders == []


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))


def test_monitor_alerts_on_unstamped_rows_since_ts(tmp_path):
    ok_old = lws.build_write_stamp(data_epoch_id=EPOCH, floor_ts=FLOOR, now=FLOOR + 10, realism={"fill_model": "X"})
    ok_new = lws.build_write_stamp(data_epoch_id=EPOCH, floor_ts=FLOOR, now=FLOOR + 30, realism={"fill_model": "X"})
    led = tmp_path / "v3" / "ledgers"
    _write(led / "decision.jsonl", [{"record_id": "legacy"}, {"record_id": "o", "write_stamp": ok_old},
                                     {"record_id": "n", "write_stamp": ok_new}, {"record_id": "u1"}])
    _write(led / "lifecycle.jsonl", [{"record_id": "o", "write_stamp": ok_old}, {"record_id": "n", "write_stamp": ok_new}])
    res = mon.check(tmp_path, FLOOR + 20)
    assert res["status"] == "RED" and res["alert"] and res["unstamped_rows_since"] == 1
    assert res["ledgers"]["decision"]["sample_row_ids"] == ["u1"]
    assert mon.main(["--data-dir", str(tmp_path), "--since", str(FLOOR + 20)]) == 2


def _fill_other_streams(led, stamp, *skip):
    """HM R2: non-allowlisted streams with no write since the cutoff are RED."""
    for n in mon.LEDGERS:
        if n not in skip and n not in mon.RETIRED_STREAMS:
            _write(led / f"{n}.jsonl", [{"record_id": "fill-" + n, "write_stamp": stamp}])


def test_monitor_green_and_exclusion_list(tmp_path):
    ok_old = lws.build_write_stamp(data_epoch_id=EPOCH, floor_ts=FLOOR, now=FLOOR + 10, realism={"fill_model": "X"})
    ok_new = lws.build_write_stamp(data_epoch_id=EPOCH, floor_ts=FLOOR, now=FLOOR + 30, realism={"fill_model": "X"})
    led = tmp_path / "v3" / "ledgers"
    listed = {"record_id": "listed"}
    _write(led / "decision.jsonl", [{"record_id": "o", "write_stamp": ok_old}, listed, {"record_id": "n", "write_stamp": ok_new}])
    import hashlib
    sha = hashlib.sha256(json.dumps(listed, sort_keys=True).encode()).hexdigest()
    _fill_other_streams(led, ok_new, "decision")
    res = mon.check(tmp_path, FLOOR + 20, exclusions={sha})
    # retired market_segment is IDLE with its reason, never GREEN
    assert res["status"] == "IDLE" and res["unstamped_rows_since"] == 0 and not res["red_streams"]
    assert res["ledgers"]["decision"]["excluded_listed"] == 1


def test_monitor_never_green_when_boundary_not_reached(tmp_path):
    led = tmp_path / "v3" / "ledgers"
    _write(led / "decision.jsonl", [{"record_id": f"x{i}", "write_stamp": lws.build_write_stamp(
        data_epoch_id=EPOCH, floor_ts=FLOOR, now=FLOOR + 50, realism={"fill_model": "X"})} for i in range(50)])
    _fill_other_streams(led, lws.build_write_stamp(data_epoch_id=EPOCH, floor_ts=FLOOR, now=FLOOR + 50,
                                                   realism={"fill_model": "X"}), "decision")
    res = mon.check(tmp_path, FLOOR + 20, max_bytes=100)
    assert res["status"] == "AMBER" and res["alert"]


def test_trade_row_without_epoch_stamp_gets_its_own_reason():
    from research import tile_realism_table as trt
    assert trt.row_exclusion({"net_pnl_bp": 1.0}) == trt.REASON_EPOCH_MISSING


def test_prepared_repair_and_wal_replay_keep_durable_stamp_bytes(tmp_path):
    """Crash repair replays the durable row's stamp exactly; a fresh write is still stamped."""
    root = _root(tmp_path)
    with mock.patch.object(de, "active_epoch_id", return_value=EPOCH):
        store = V3EvidenceStore(root, epoch_id="ep")
        first = store.append("decision", {"record_id": "r1", "value": 1})
        again = store.append("decision", {"record_id": "r1", "value": 1})
    assert first.get("written") is True and again.get("duplicate") is True
    found, stamp = store._durable_write_stamp("decision", "r1")
    if found:  # receipt-backed store: the durable stamp is what a repair would replay
        assert stamp == _rows(root, "decision")[0]["write_stamp"]
    assert isinstance(_rows(root, "decision")[0].get("write_stamp"), dict)

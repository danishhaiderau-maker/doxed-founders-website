"""EpochGuard rejects rows older than fresh_start_epoch.json even when stamped with the (pinned) current DATA_EPOCH_ID."""
import json

import analyzer_epoch_guard as eg
import data_epoch as de

EPOCH_ID = "ce-20261004-v31-freeze21b"
MANIFEST = {"epoch_id": EPOCH_ID, "started_at_ts": 1_791_000_000.0}
FLOOR = 1_792_263_671.0  # 2026-10-18 06:01:11 AEDT
REL = "trades_3factor.jsonl"


def row(ts, stamp=EPOCH_ID, **extra):
    r = {"close_ts": ts, "data_epoch_id": stamp}
    if stamp is None:
        r.pop("data_epoch_id")
    r.update(extra)
    return r


def test_stamped_current_id_row_before_fresh_start_is_rejected():
    g = eg.EpochGuard(MANIFEST, floor_ts=FLOOR)
    assert g.admit(REL, row(FLOOR - 1)) is False
    assert g.admit(REL, row(FLOOR + 1)) is True
    assert g.rejected[REL] == {de.PRE_EPOCH: 1}


def test_without_a_floor_behaviour_is_unchanged():
    g = eg.EpochGuard(MANIFEST)
    assert g.admit(REL, row(FLOOR - 1)) is True


def test_row_with_no_timestamp_fails_closed_when_floor_set():
    g = eg.EpochGuard(MANIFEST, floor_ts=FLOOR)
    assert g.admit(REL, {"data_epoch_id": EPOCH_ID}) is False
    assert g.rejected[REL] == {de.UNDATED: 1}


def test_unstamped_pre_floor_and_foreign_rows_still_rejected():
    g = eg.EpochGuard(MANIFEST, floor_ts=FLOOR)
    assert g.admit(REL, row(FLOOR - 5, stamp=None)) is False
    assert g.admit(REL, row(FLOOR + 5, stamp="ce-other")) is False


def test_admit_line_filter_rows_and_assert_pure_share_the_floor():
    g = eg.EpochGuard(MANIFEST, floor_ts=FLOOR)
    old, new = json.dumps(row(FLOOR - 10)), json.dumps(row(FLOOR + 10))
    assert g.admit_line(REL, old) is False and g.admit_line(REL, new) is True
    assert g.filter_rows(REL, [row(FLOOR - 1), row(FLOOR + 1)]) == [row(FLOOR + 1)]
    try:
        g.assert_pure(REL, [row(FLOOR - 1)])
    except eg.PreEpochRowError:
        pass
    else:
        raise AssertionError("pre-floor row passed assert_pure")


def test_epoch_independent_streams_are_not_floored():
    rel = sorted(de.EPOCH_INDEPENDENT_BASES)[0]
    g = eg.EpochGuard(MANIFEST, floor_ts=FLOOR)
    assert g.admit(rel, {"close_ts": FLOOR - 1000}) is True


def test_from_data_dir_loads_the_floor_from_fresh_start_epoch_json(tmp_path):
    (tmp_path / de.MANIFEST_NAME).write_text(json.dumps({**MANIFEST, "schema": de.MANIFEST_SCHEMA}))
    (tmp_path / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": FLOOR}))
    assert eg.fresh_floor_ts(tmp_path) == FLOOR
    other = tmp_path.parent / (tmp_path.name + "_x" ) / "deep"
    other.mkdir(parents=True)
    assert eg.fresh_floor_ts(other) is None
    sub = tmp_path / "runtime"
    sub.mkdir()
    assert eg.fresh_floor_ts(sub) == FLOOR  # volume root one level up


def test_file_pre_epoch_rows_counts_stamped_pre_floor_rows(tmp_path):
    path = tmp_path / REL
    path.write_text("\n".join(json.dumps(row(t)) for t in (FLOOR - 5, FLOOR + 5, FLOOR - 1)) + "\n")
    assert eg.file_pre_epoch_rows(path, REL, MANIFEST, FLOOR) == 2
    assert eg.file_pre_epoch_rows(path, REL, MANIFEST) == 0


def test_declared_epoch_without_fresh_start_file_fails_closed(tmp_path):
    (tmp_path / de.MANIFEST_NAME).write_text(json.dumps({**MANIFEST, "schema": de.MANIFEST_SCHEMA}))
    g = eg.EpochGuard.from_data_dir(tmp_path)
    assert g.declared and g.floor_missing and not g.floor_loaded
    assert g.admit(REL, row(FLOOR + 10)) is False
    assert g.admit_line(REL, json.dumps(row(FLOOR + 10))) is False
    assert g.rejected[REL] == {eg.NO_FRESH_FLOOR: 2}
    try:
        g.assert_pure(REL, [row(FLOOR + 10)])
    except eg.PreEpochRowError:
        pass
    else:
        raise AssertionError("assert_pure passed without a floor")
    block = g.receipt_block()
    assert block["floor_loaded"] is False and block["floor_ts"] is None and block["floor_missing_fail_closed"] is True


def test_receipt_block_publishes_floor_ts_and_loaded(tmp_path):
    (tmp_path / de.MANIFEST_NAME).write_text(json.dumps({**MANIFEST, "schema": de.MANIFEST_SCHEMA}))
    (tmp_path / "fresh_start_epoch.json").write_text(json.dumps({"epoch_ts": FLOOR}))
    g = eg.EpochGuard.from_data_dir(tmp_path)
    block = g.receipt_block()
    assert block["floor_ts"] == FLOOR and block["floor_loaded"] is True and block["floor_missing_fail_closed"] is False
    assert g.admit(REL, row(FLOOR + 1)) is True


def test_no_manifest_declared_still_admits_everything_even_without_a_floor(tmp_path):
    g = eg.EpochGuard.from_data_dir(tmp_path)
    assert not g.declared and not g.floor_missing and g.admit(REL, {"x": 1}) is True

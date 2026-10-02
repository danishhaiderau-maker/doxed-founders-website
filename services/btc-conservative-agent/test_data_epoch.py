"""data_epoch: manifest lifecycle, stamping, row classification, fingerprints."""

from __future__ import annotations

import json
import time

import pytest

import data_epoch as de

EPOCH = "ce-20261004-v31-clean"


def test_epoch_id_format():
    assert de.valid_epoch_id(EPOCH) and de.valid_epoch_id("ce-20261004T111000Z-a")
    for bad in ("", "epoch-v22-abc", "ce-2026104-x", "ce-20261004-", "ce-20261004-UPPER", None):
        assert not de.valid_epoch_id(bad)
    with pytest.raises(ValueError):
        de.new_manifest("bad", started_at_ts=time.time())


def test_runtime_manifest_is_stable_across_restart_and_records_previous(tmp_path):
    assert de.ensure_runtime_manifest(tmp_path, None) is None
    first = de.ensure_runtime_manifest(tmp_path, EPOCH, now=1_790_000_000.0, source_git_rev="abc")
    again = de.ensure_runtime_manifest(tmp_path, EPOCH, now=1_790_009_999.0)
    assert again["started_at_ts"] == first["started_at_ts"] == 1_790_000_000.0
    nxt = de.ensure_runtime_manifest(tmp_path, "ce-20261101-next", now=1_792_000_000.0)
    assert nxt["previous"]["epoch_id"] == EPOCH
    assert de.load_manifest(tmp_path)["epoch_id"] == "ce-20261101-next"


def test_stamp_never_touches_sealed_or_already_stamped_rows():
    assert de.stamp({"a": 1}, EPOCH) == {"a": 1, "data_epoch_id": EPOCH}
    sealed = {"a": 1, "row_sha256": "f" * 64}
    assert de.stamp(sealed, EPOCH) is sealed
    assert de.stamp({"data_epoch_id": "ce-20200101-old"}, EPOCH)["data_epoch_id"] == "ce-20200101-old"
    row = {"a": 1}
    assert de.stamp(row, None) is row
    de.stamp(row, EPOCH)
    assert row == {"a": 1}


def test_line_version_priority_and_release_shaped_policy_version():
    line = b'{"epoch_id":"epoch-v22-x","bot_version":"v31-a-v6","data_epoch_id":"' + EPOCH.encode() + b'"}'
    assert de.line_version(line) == f"data_epoch_id={EPOCH}"
    assert de.line_version(b'{"epoch_id":"epoch-v22-x","bot_version":"v31-a-v6"}') == "bot_version=v31-a-v6"
    assert de.line_version(b'{"policy_version":"XVENUE_LEAD_W10S|x","epoch_id":"epoch-v22-x"}') == "epoch_id=epoch-v22-x"
    assert de.line_version(b'{"a":1}') == "UNVERSIONED"


def test_line_ts_and_classification():
    m = de.new_manifest(EPOCH, started_at_ts=1_790_000_000.0)
    old = b'{"ts": 1789999000.5, "x": 1}'
    new = b'{"recorded_at": "2026-09-22T10:00:00Z", "signal_ts": 1790000100}'
    assert de.line_ts(old) == 1789999000.5
    assert de.line_ts(new) == 1790000100.0
    assert de.classify("signal_replay.jsonl", stamp_value=None, ts=1789999000.5, manifest=m) == de.PRE_EPOCH
    assert de.classify("signal_replay.jsonl", stamp_value=None, ts=1790000100.0, manifest=m) == de.UNSTAMPED_POST_EPOCH
    assert de.classify("trades_3factor.csv", stamp_value=None, ts=1790000100.0, manifest=m) == de.CURRENT_UNSTAMPED
    assert de.classify("v3/ledgers/execution.jsonl", stamp_value=None, ts=1790000100.0, manifest=m) == de.CURRENT_UNSTAMPED
    assert de.classify("x.jsonl", stamp_value=EPOCH, ts=None, manifest=m) == de.CURRENT
    assert de.classify("x.jsonl", stamp_value="ce-20200101-old", ts=None, manifest=m) == de.FOREIGN
    assert de.classify("x.jsonl", stamp_value=None, ts=None, manifest=m) == de.UNDATED
    assert de.classify("market_microstructure_1s.jsonl.4", stamp_value=None, ts=1.0, manifest=m) == de.INDEPENDENT
    assert de.classify("x.jsonl", stamp_value=None, ts=1790000100.0, manifest=None) == de.LEGACY
    assert de.classify_row("x.jsonl", {"ts": 1789999000.5}, m) == de.PRE_EPOCH


def test_fingerprint_dead_fields_and_drift():
    rows = [{"a": i, "b": None, "c": {"d": 0}, "kind": "fill"} for i in range(1, 30)]
    fp = de.fingerprint(rows)
    assert fp["rows_sampled"] == 29 and set(fp["fields"]) == {"a", "b", "c.d", "kind"}
    dead = {d["field"]: d["status"] for d in de.dead_fields(fp)}
    assert dead == {"b": "DEAD_NULL", "c.d": "DEAD_ZERO"}
    announced = {"a": ["number"], "b": ["null"], "c.d": ["number"], "kind": ["string"]}
    assert de.schema_drift(announced, fp)["status"] == "MATCH"
    rows.append({"a": "text", "e": 1})
    drift = de.schema_drift(announced, de.fingerprint(rows))
    assert drift["status"] == "DRIFT" and drift["new_fields"] == ["e"] and drift["type_changes"][0]["field"] == "a"
    assert de.schema_drift(None, fp)["status"] == "UNANNOUNCED"
    assert de.record_kind({"kind": "fill"}) == "kind=fill" and de.record_kind({}) == "*"
    assert json.dumps(fp)

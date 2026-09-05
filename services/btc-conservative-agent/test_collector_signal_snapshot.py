import copy
import hashlib
import json

import pytest

import collector_signal_snapshot as snapshots
from collector_v22 import build_research_event
from collector_v22_provisional import load_provisional_events, upsert_provisional_event

SIGNAL = 1_700_000_040.0


def candle(timestamp):
    return [timestamp * 1000, 100, 101, 99, 100, 1]


def event(**overrides):
    args = dict(trade_id="snapshot-1", epoch_id="epoch-1", signal_ts=SIGNAL,
                signal_price=100, candles_1m=[candle(SIGNAL - 120), candle(SIGNAL - 60)],
                rsi_at_signal=42, atr14_pct=0.3, would_block=False,
                would_block_reason="original", feature_snapshot={"nested": {"adx": 27}},
                decision_tree={"schema": "test-tree", "signal_rsi": 42})
    args.update(overrides)
    return build_research_event(**args)


def freeze(tmp_path, record=None):
    return snapshots.freeze_signal_snapshot(record or event(), data_dir=tmp_path, captured_at=SIGNAL + 5)


def load(tmp_path, ref, **overrides):
    args = dict(data_dir=tmp_path, event_id="snapshot-1", epoch_id="epoch-1", signal_ts=SIGNAL)
    args.update(overrides)
    return snapshots.load_signal_snapshot(ref, **args)


@pytest.mark.parametrize("rejected", [False, True])
def test_frozen_context_survives_delayed_maturation_for_both_paths(tmp_path, rejected):
    initial = event(rejected=rejected, submitted=not rejected)
    ref = freeze(tmp_path, initial)
    initial["feature_snapshot_at_signal"]["nested"]["adx"] = 99
    delayed = event(rejected=rejected, submitted=not rejected,
                    candles_1m=[candle(SIGNAL + 15000)], rsi_at_signal=88, atr14_pct=9,
                    would_block=True, feature_snapshot={"future": True},
                    frozen_signal_snapshot_ref=ref, snapshot_data_dir=str(tmp_path))
    frozen = load(tmp_path, ref)
    for key in snapshots.FIELDS:
        assert delayed[key] == frozen["evidence"][key]
    assert delayed["feature_snapshot_at_signal"]["nested"]["adx"] == 27
    assert delayed["research_signal_snapshot_ref"] == ref
    assert delayed["canonical_tape"]["coverage"]["eligible"] is False
    assert frozen["availability_at_signal_verified"] is False
    assert frozen["capture_basis"] == "FIRST_COLLECTOR_CAPTURE"


def test_restart_and_rejected_promotion_keep_small_original_reference(tmp_path):
    ref = freeze(tmp_path)
    source = {"trade_id": "snapshot-1", "research_signal_snapshot_ref": ref, "collector_rejected": True}
    upsert_provisional_event("snapshot-1", source, epoch_id="epoch-1", data_dir=str(tmp_path))
    changed = freeze(tmp_path, event(rsi_at_signal=12))
    upsert_provisional_event("snapshot-1", {"trade_id": "snapshot-1",
                             "research_signal_snapshot_ref": changed, "collector_rejected": False},
                             epoch_id="epoch-1", data_dir=str(tmp_path))
    restored = load_provisional_events(epoch_id="epoch-1", data_dir=str(tmp_path))["snapshot-1"]
    assert restored["research_signal_snapshot_ref"] == ref
    assert restored["collector_rejected"] is False
    assert "candles" not in json.dumps(restored)
    assert len(json.dumps(restored)) < 1024
    assert load(tmp_path, ref)["evidence"]["rsi_at_signal"] == 42


def test_existing_reference_reused_without_writes(tmp_path, monkeypatch):
    record = event()
    record["research_signal_snapshot_ref"] = freeze(tmp_path, record)
    monkeypatch.setattr(snapshots.tempfile, "mkstemp", lambda **kw: pytest.fail("must not rewrite"))
    assert freeze(tmp_path, record) == record["research_signal_snapshot_ref"]


@pytest.mark.parametrize("identity", [{"event_id": "other"}, {"epoch_id": "other"}, {"signal_ts": SIGNAL + 1}])
def test_snapshot_identity_cannot_be_rebound(tmp_path, identity):
    with pytest.raises(ValueError, match="REFERENCE_INVALID"):
        load(tmp_path, freeze(tmp_path), **identity)


def test_tampered_or_missing_snapshot_does_not_fallback(tmp_path):
    ref = freeze(tmp_path)
    path = tmp_path / "v3" / "signal_snapshots_v1" / (ref["sha256"] + ".json")
    payload = path.read_bytes()
    path.write_bytes(payload.replace(b'"rsi_at_signal":42', b'"rsi_at_signal":43'))
    with pytest.raises(ValueError, match="HASH_MISMATCH"):
        event(frozen_signal_snapshot_ref=ref, snapshot_data_dir=str(tmp_path))
    path.unlink()
    with pytest.raises(FileNotFoundError):
        event(frozen_signal_snapshot_ref=ref, snapshot_data_dir=str(tmp_path))


def test_size_limit_and_atomic_create_failure_leave_no_temporary_files(tmp_path, monkeypatch):
    record = event()
    monkeypatch.setattr(snapshots, "MAX_SNAPSHOT_BYTES", 32)
    with pytest.raises(ValueError, match="SIZE_LIMIT"):
        freeze(tmp_path, record)
    assert not (tmp_path / "v3").exists()
    monkeypatch.setattr(snapshots, "MAX_SNAPSHOT_BYTES", 4 * 1024 * 1024)
    monkeypatch.setattr(snapshots.os, "link", lambda *a: (_ for _ in ()).throw(OSError("injected")))
    with pytest.raises(OSError, match="injected"):
        freeze(tmp_path, record)
    assert list((tmp_path / "v3" / "signal_snapshots_v1").iterdir()) == []


def test_bad_reference_path_and_nonfinite_capture_refused(tmp_path):
    ref = freeze(tmp_path)
    bad = copy.deepcopy(ref)
    bad["sha256"] = "../escape"
    with pytest.raises(ValueError, match="REFERENCE_INVALID"):
        load(tmp_path, bad)
    with pytest.raises(ValueError, match="CAPTURE_TIME_INVALID"):
        snapshots.freeze_signal_snapshot(event(), data_dir=tmp_path, captured_at=float("nan"))


@pytest.mark.parametrize("bad", [None, True, False, "", " ", " bad", 0])
@pytest.mark.parametrize("field", ["event_id", "epoch_id"])
def test_invalid_identity_values_refused(tmp_path, field, bad):
    record = event()
    record[field] = bad
    with pytest.raises(ValueError, match="IDENTITY_INVALID"):
        freeze(tmp_path, record)


@pytest.mark.parametrize("path", [None, "../escape.json", "v3/signal_snapshots_v1/other.json"])
def test_reference_requires_exact_canonical_relative_path(tmp_path, path):
    ref = freeze(tmp_path)
    ref["relative_path"] = path
    with pytest.raises(ValueError, match="REFERENCE_INVALID"):
        load(tmp_path, ref)


@pytest.mark.parametrize("field,value", [
    ("capture_basis", "AT_SIGNAL_OBSERVED"), ("capture_basis", None),
    ("availability_at_signal_verified", True), ("availability_at_signal_verified", 0),
    ("availability_at_signal_verified", None),
    ("captured_at", True), ("captured_at", False), ("captured_at", "1700000040"),
    ("captured_at", None), ("captured_at", -1), ("captured_at", 0),
    ("captured_at", 1e100), ("captured_at", SIGNAL - 1),
])
def test_rehashed_invalid_capture_semantics_refused_by_storage_reader(tmp_path, field, value):
    ref = freeze(tmp_path)
    material = load(tmp_path, ref)
    material[field] = value
    payload = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    digest = hashlib.sha256(payload).hexdigest()
    ref.update(sha256=digest, bytes=len(payload), relative_path=f"v3/signal_snapshots_v1/{digest}.json")
    (tmp_path / ref["relative_path"]).write_bytes(payload)
    with pytest.raises(ValueError, match="CONTENT_INVALID|CAPTURE_TIME_INVALID"):
        load(tmp_path, ref)


@pytest.mark.parametrize("value", [True, False, None, "123", float("inf"), float("nan"), 1e100])
def test_freeze_refuses_invalid_capture_without_creating_artifact(tmp_path, value):
    with pytest.raises(ValueError, match="CAPTURE_TIME_INVALID"):
        snapshots.freeze_signal_snapshot(event(), data_dir=tmp_path, captured_at=value)
    assert not (tmp_path / "v3").exists()


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e999", "-1e999"])
def test_shared_decoder_rejects_nonfinite_numbers_anywhere(tmp_path, token):
    ref = freeze(tmp_path)
    payload = (tmp_path / ref["relative_path"]).read_bytes().replace(b'"rsi_at_signal":42',
                                                                             f'"rsi_at_signal":{token}'.encode())
    with pytest.raises(ValueError, match="NONFINITE_JSON"):
        snapshots.decode_signal_snapshot(payload, identity=ref["identity"])


def test_shared_decoder_rejects_duplicate_nested_keys(tmp_path):
    ref = freeze(tmp_path)
    payload = (tmp_path / ref["relative_path"]).read_bytes().replace(b'"adx":27', b'"adx":27,"adx":99')
    with pytest.raises(ValueError, match="DUPLICATE_JSON_KEY"):
        snapshots.decode_signal_snapshot(payload, identity=ref["identity"])


def test_shared_decoder_retains_delayed_capture_unknown_availability(tmp_path):
    ref = freeze(tmp_path)
    material = load(tmp_path, ref)
    material["captured_at"] = SIGNAL + 7200
    payload = json.dumps(material).encode()
    decoded = snapshots.decode_signal_snapshot(payload, identity=ref["identity"])
    assert decoded["availability_at_signal_verified"] is False
    assert decoded["captured_at"] == SIGNAL + 7200


def test_freeze_refuses_capture_before_signal_and_accepts_exact_boundary(tmp_path):
    with pytest.raises(ValueError, match="CAPTURE_TIME_INVALID"):
        snapshots.freeze_signal_snapshot(event(), data_dir=tmp_path, captured_at=SIGNAL - 1)
    assert not (tmp_path / "v3").exists()
    ref = snapshots.freeze_signal_snapshot(event(), data_dir=tmp_path, captured_at=SIGNAL)
    assert load(tmp_path, ref)["captured_at"] == SIGNAL

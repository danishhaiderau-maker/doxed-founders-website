"""RK PASS 25 Oct 2026: open_read_only tolerates a stale identity ONLY on an allowlisted retired
stream whose file proves no writes since retirement. Everything else still conflicts."""
import json
import os

import pytest

import ledger_delete_by_sha as lds
import retired_streams
from research_v3_store import RetiredStreamUnreadable, V3EvidenceStore

STALE = "a724a2140000000000000000000000000000000"


class _Death(BaseException):
    pass


def _store(root):
    s = V3EvidenceStore(root, epoch_id="epoch-test")
    for led in ("decision", "lifecycle", "market_segment"):
        for i in range(4):
            assert s.append(led, {"record_id": f"{led}-{i}", "i": i})["written"]
    return s


def _stale(root, led, **change):
    p = root / "v3" / "receipts" / "emergency_record_idempotency_v1" / led / "complete.json"
    row = json.loads(p.read_text("utf-8"))
    row["identity"].update(change or {"deployed_revision": STALE})
    p.write_text(json.dumps(row), "utf-8")


def _pin(monkeypatch, root, led="market_segment"):
    path = root / "v3" / "ledgers" / f"{led}.jsonl"
    st = os.stat(path)
    monkeypatch.setitem(retired_streams.RETIREMENT_FINGERPRINTS, led, {
        "rows": path.read_bytes().count(b"\n"), "mtime": int(st.st_mtime), "size": st.st_size})
    return path


def test_allowlist_is_exactly_market_segment():
    assert retired_streams.RETIRED_STREAM_ALLOWLIST == {"market_segment": "MARKET_SEGMENTS_RETIRED_R17"}
    assert retired_streams.RETIREMENT_FINGERPRINTS["market_segment"] == {
        "rows": 18322, "mtime": 1792830018, "size": 37969967}


def test_stale_retired_pointer_passes_and_is_unreadable_retired(tmp_path, monkeypatch):
    _store(tmp_path)
    path = _pin(monkeypatch, tmp_path)
    before = path.read_bytes()
    _stale(tmp_path, "market_segment")
    ro = V3EvidenceStore.open_read_only(tmp_path)
    assert ro._identity_binding()["deployed_revision"] != STALE
    assert ro.epoch_id == "epoch-test"
    with pytest.raises(RetiredStreamUnreadable):
        ro.ledger_path("market_segment")
    with pytest.raises(ValueError):
        ro.ledger_generation_paths("market_segment")
    v = ro.verify()
    assert v["ledger_status"]["market_segment"] == "RETIRED"
    assert v["retired_streams"] == {"market_segment": {"status": "RETIRED", "reason": "MARKET_SEGMENTS_RETIRED_R17"}}
    assert v["ledger_counts"]["market_segment"] == 0
    assert v["ledger_status"]["decision"] == "GREEN" and v["ledger_counts"]["decision"] == 4
    # stale pointer is not republished, ledger untouched
    p = tmp_path / "v3" / "receipts" / "emergency_record_idempotency_v1" / "market_segment" / "complete.json"
    assert json.loads(p.read_text())["identity"]["deployed_revision"] == STALE
    assert path.read_bytes() == before


def test_stale_active_pointer_conflicts(tmp_path, monkeypatch):
    _store(tmp_path)
    _pin(monkeypatch, tmp_path)
    _stale(tmp_path, "decision")
    with pytest.raises(ValueError, match="identity conflict"):
        V3EvidenceStore.open_read_only(tmp_path)


def test_stale_active_pointer_conflicts_even_with_retired_stale(tmp_path, monkeypatch):
    _store(tmp_path)
    _pin(monkeypatch, tmp_path)
    _stale(tmp_path, "market_segment")
    _stale(tmp_path, "lifecycle")
    with pytest.raises(ValueError, match="identity conflict"):
        V3EvidenceStore.open_read_only(tmp_path)


@pytest.mark.parametrize("how", ["append", "touch", "truncate_same_mtime", "rotation", "missing"])
def test_retired_stream_with_new_writes_conflicts(tmp_path, monkeypatch, how):
    _store(tmp_path)
    path = _pin(monkeypatch, tmp_path)
    _stale(tmp_path, "market_segment")
    st = os.stat(path)
    if how == "append":
        with open(path, "ab") as fh:
            fh.write(b'{"record_id":"late"}\n')
        os.utime(path, (st.st_atime, st.st_mtime))
    elif how == "touch":
        os.utime(path, (st.st_atime, st.st_mtime + 5))
    elif how == "truncate_same_mtime":
        data = path.read_bytes()
        path.write_bytes(data.replace(b"\n", b" ", 1))  # same size, one row fewer
        os.utime(path, (st.st_atime, st.st_mtime))
    elif how == "rotation":
        (path.parent / "market_segment.jsonl.1").write_bytes(b"")
    else:
        path.unlink()
    with pytest.raises(ValueError, match="identity conflict"):
        V3EvidenceStore.open_read_only(tmp_path)


def test_retired_stale_pointer_with_other_epoch_conflicts(tmp_path, monkeypatch):
    _store(tmp_path)
    _pin(monkeypatch, tmp_path)
    _stale(tmp_path, "market_segment", epoch_id="epoch-other", deployed_revision=STALE)
    with pytest.raises(ValueError, match="identity conflict"):
        V3EvidenceStore.open_read_only(tmp_path)


@pytest.mark.parametrize("led", ["decision", "lifecycle", "order_intent", "opportunity", "execution"])
def test_non_allowlisted_stale_stream_conflicts(tmp_path, monkeypatch, led):
    _store(tmp_path)
    _pin(monkeypatch, tmp_path)
    _pin(monkeypatch, tmp_path, "decision")  # a fingerprint alone never allowlists a stream
    _stale(tmp_path, led)
    with pytest.raises(ValueError, match="identity conflict"):
        V3EvidenceStore.open_read_only(tmp_path)


def test_unknown_stale_stream_conflicts(tmp_path, monkeypatch):
    _store(tmp_path)
    _pin(monkeypatch, tmp_path)
    d = tmp_path / "v3" / "receipts" / "emergency_record_idempotency_v1"
    (d / "mystery").mkdir()
    row = json.loads((d / "decision" / "complete.json").read_text())
    row["identity"]["deployed_revision"] = STALE
    (d / "mystery" / "complete.json").write_text(json.dumps(row))
    with pytest.raises(ValueError, match="identity conflict"):
        V3EvidenceStore.open_read_only(tmp_path)
    assert retired_streams.retired_stream_unchanged(tmp_path / "v3" / "ledgers", "mystery")[0] is False


def test_unchanged_identity_retired_stream_still_normal(tmp_path, monkeypatch):
    _store(tmp_path)
    ro = V3EvidenceStore.open_read_only(tmp_path)  # no stale pointer: nothing excluded
    assert ro._retired_excluded == {}
    assert ro.verify()["ledger_status"]["market_segment"] == "GREEN"


def test_boot_recover_and_snapshot_delete_with_stale_retired_pointer(tmp_path, monkeypatch):
    s = _store(tmp_path)
    led = s.ledger_path("decision")
    before = led.read_bytes()
    line = before.splitlines(keepends=True)[1]
    import hashlib
    entry = {"stream": "v3/ledgers/decision.jsonl", "row_sha256": hashlib.sha256(line.rstrip(b"\r\n")).hexdigest()}
    text = json.dumps(entry) + "\n"
    real = s._atomic_json_receipt
    calls = []

    def dying(path, row, **kw):
        calls.append(path)
        if len(calls) == 2:
            raise _Death()
        return real(path, row, **kw)

    monkeypatch.setattr(s, "_atomic_json_receipt", dying)
    with pytest.raises(_Death):
        lds.delete_ledger_rows_by_sha(s, text, lds.list_sha256(text), dry_run=False)
    monkeypatch.setattr(s, "_atomic_json_receipt", real)
    _pin(monkeypatch, tmp_path)
    _stale(tmp_path, "market_segment")
    fresh = V3EvidenceStore.open_read_only(tmp_path)  # previously: identity conflict
    out = lds.restore_incomplete_deletes(fresh)
    assert [r["ledger"] for r in out] == ["decision"]
    assert led.read_bytes() == before
    snap = next((tmp_path / "v3" / lds.SNAPSHOT_DIRNAME).iterdir())
    assert lds.delete_rollback_snapshot(fresh, str(snap))["deleted"] is True
    lc = fresh.ledger_path("lifecycle")
    lc_before = lc.read_bytes()
    lline = lc_before.splitlines(keepends=True)[2]
    ltext = json.dumps({"stream": "v3/ledgers/lifecycle.jsonl",
                        "row_sha256": hashlib.sha256(lline.rstrip(b"\r\n")).hexdigest()}) + "\n"
    dry = lds.delete_ledger_rows_by_sha(fresh, ltext, lds.list_sha256(ltext), dry_run=True)
    assert dry and lc.read_bytes() == lc_before
    ms = json.dumps({"stream": "v3/ledgers/market_segment.jsonl", "row_sha256": "0" * 64}) + "\n"
    with pytest.raises((lds.DeleteRefused, ValueError)):  # retired stream serves no rows
        lds.delete_ledger_rows_by_sha(fresh, ms, lds.list_sha256(ms), dry_run=True)

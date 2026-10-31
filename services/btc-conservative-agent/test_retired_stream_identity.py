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
    # Declared pin (plan eb14e8711d31); merge gate must Fly-confirm-measure these four or STOP.
    assert retired_streams.RETIREMENT_FINGERPRINTS["market_segment"] == {
        "rows": 18289, "mtime": 1792899429, "size": 37892450}
    assert "RETIRED_STREAM_WRITTEN_SINCE_RETIREMENT" in retired_streams.SOFT_RETIRED_EXEMPTION_REFUSALS
    # Hard refusals (epoch/tile/…) must NOT be soft — tipMatch softens fingerprint family only.
    assert "RETIRED_IDENTITY_EPOCH_ID_DIFFERS" not in retired_streams.SOFT_RETIRED_EXEMPTION_REFUSALS


def test_market_segment_fingerprint_pin_rejects_wrong_rows_size_mtime(monkeypatch):
    """Mutation: wrong rows/size/mtime must fail the pin contract (R23)."""
    pin = retired_streams.RETIREMENT_FINGERPRINTS["market_segment"]
    assert pin == {"rows": 18289, "mtime": 1792899429, "size": 37892450}
    for key, bad in (("rows", 18322), ("size", 37969967), ("mtime", 1792830018)):
        broken = dict(pin)
        broken[key] = bad
        monkeypatch.setitem(retired_streams.RETIREMENT_FINGERPRINTS, "market_segment", broken)
        assert retired_streams.RETIREMENT_FINGERPRINTS["market_segment"][key] == bad
        assert retired_streams.RETIREMENT_FINGERPRINTS["market_segment"] != {
            "rows": 18289, "mtime": 1792899429, "size": 37892450}
    monkeypatch.setitem(retired_streams.RETIREMENT_FINGERPRINTS, "market_segment", dict(pin))
    assert retired_streams.RETIREMENT_FINGERPRINTS["market_segment"] == pin


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
def test_retired_stream_with_new_writes_excludes_without_poisoning_active(tmp_path, monkeypatch, how):
    """Fingerprint-family contamination soft-excludes market_segment; active ledgers stay readable (BTC-165)."""
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
    ro = V3EvidenceStore.open_read_only(tmp_path)
    assert "market_segment" in ro._retired_excluded
    assert ro._retired_contaminated["market_segment"] in retired_streams.SOFT_RETIRED_EXEMPTION_REFUSALS
    by_id = {}
    for _ref, generation_path in ro.ledger_generation_paths("decision"):
        for line in generation_path.read_text("utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                by_id[row["record_id"]] = row
    assert len(by_id) == 4
    with pytest.raises(RetiredStreamUnreadable):
        ro.ledger_path("market_segment")
    v = ro.verify()
    assert v["ledger_status"]["market_segment"] == "RETIRED_CONTAMINATED"
    assert v["ledger_status"]["decision"] == "GREEN"
    assert v["retired_streams"]["market_segment"]["contamination"] in retired_streams.SOFT_RETIRED_EXEMPTION_REFUSALS


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


# ---- HM conditions (25 Oct 2026) ----

def _ready(tmp_path, monkeypatch):
    s = _store(tmp_path)
    path = _pin(monkeypatch, tmp_path)
    _stale(tmp_path, "market_segment")
    return s, path


def test_tile_config_differs_conflicts(tmp_path, monkeypatch):
    _store(tmp_path)
    _pin(monkeypatch, tmp_path)
    _stale(tmp_path, "market_segment", tile_config_signature="f" * 64, deployed_revision=STALE)
    with pytest.raises(ValueError, match="TILE_CONFIG"):
        V3EvidenceStore.open_read_only(tmp_path)


def test_signature_mismatch_subsecond_mtime_soft_excludes(tmp_path, monkeypatch):
    """Sub-second mtime drift fails the completeness signature check (hard) — still raise."""
    _, path = _ready(tmp_path, monkeypatch)
    st = os.stat(path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns - st.st_mtime_ns % 10**9 + (st.st_mtime_ns + 1) % 10**9))
    with pytest.raises(ValueError, match="identity conflict"):
        V3EvidenceStore.open_read_only(tmp_path)


def test_tail_anchor_invalid_conflicts(tmp_path, monkeypatch):
    _, path = _ready(tmp_path, monkeypatch)
    p = tmp_path / "v3" / "receipts" / "emergency_record_idempotency_v1" / "market_segment" / "complete.json"
    row = json.loads(p.read_text())
    row["tail_anchor"] = {**row["tail_anchor"], "sha256": "0" * 64}
    p.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="TAIL_ANCHOR"):
        V3EvidenceStore.open_read_only(tmp_path)


def test_generation_pointer_conflicts(tmp_path, monkeypatch):
    _ready(tmp_path, monkeypatch)
    d = tmp_path / "v3" / "receipts" / "ledger_generations_v1" / "market_segment"
    d.mkdir(parents=True)
    (d / "ACTIVE.json").write_text(json.dumps({"no_identity": True}))
    with pytest.raises(ValueError, match="GENERATION_POINTER"):
        V3EvidenceStore.open_read_only(tmp_path)


def test_wal_entry_for_retired_stream_conflicts(tmp_path, monkeypatch):
    _ready(tmp_path, monkeypatch)
    import research_v3_store as rvs
    import emergency_evidence_wal as ew

    def fake_headers(self, handle):
        return [None, {"ledger": "market_segment", "record_id": "x"}]
    wal = tmp_path / "v3" / "emergency_evidence_wal_v2"
    wal.mkdir(parents=True, exist_ok=True)
    (wal / "mandatory-reserve.headers").write_bytes(b"\0" * ew.HEADER_BYTES * 2)
    monkeypatch.setattr(ew.EmergencyEvidenceWal, "_read_headers", fake_headers)
    with pytest.raises(ValueError, match="WAL_ENTRIES"):
        rvs.V3EvidenceStore.open_read_only(tmp_path)
    monkeypatch.setattr(ew.EmergencyEvidenceWal, "_read_headers", lambda self, h: [None, None])
    assert rvs.V3EvidenceStore.open_read_only(tmp_path)._retired_excluded


def test_exempt_identity_never_adopted_and_others_exact(tmp_path, monkeypatch):
    _ready(tmp_path, monkeypatch)
    ro = V3EvidenceStore.open_read_only(tmp_path)
    assert ro._identity_binding()["deployed_revision"] == "NOT_DEPLOYED_LOCAL"
    # only the retired authority left (and it is stale vs nothing): nothing to exempt against -> no exemption
    d = tmp_path / "v3" / "receipts" / "emergency_record_idempotency_v1"
    for led in ("decision", "lifecycle"):
        (d / led / "complete.json").unlink()
    others = [p for p in d.glob("*/complete.json") if p.parent.name != "market_segment"]
    for p in others:
        p.unlink()
    ro2 = V3EvidenceStore.open_read_only(tmp_path)
    assert ro2._retired_excluded == {}  # legacy single-authority behaviour, nothing exempted


def test_appends_to_retired_stream_refused(tmp_path, monkeypatch):
    s, path = _ready(tmp_path, monkeypatch)
    before = path.read_bytes()
    ro = V3EvidenceStore.open_read_only(tmp_path)
    with pytest.raises(ValueError):
        ro.append("market_segment", {"record_id": "late"})
    import research_v3_bridge
    assert research_v3_bridge.MARKET_SEGMENT_WRITES_RETIRED is True
    assert path.read_bytes() == before


def test_exemption_is_logged_with_both_identities(tmp_path, monkeypatch, caplog):
    _ready(tmp_path, monkeypatch)
    with caplog.at_level("WARNING"):
        V3EvidenceStore.open_read_only(tmp_path)
    msg = [r.getMessage() for r in caplog.records if "V3-RETIRED-STREAM-EXEMPT" in r.getMessage()]
    assert len(msg) == 1 and "ledger=market_segment" in msg[0]
    assert STALE in msg[0] and "NOT_DEPLOYED_LOCAL" in msg[0]


def test_analyzer_path_reads_active_and_not_retired(tmp_path, monkeypatch):
    _, path = _ready(tmp_path, monkeypatch)
    monkeypatch.setenv("BTC_AGENT_DATA_DIR", str(tmp_path))
    import analyzer_research_engine_v62 as eng
    monkeypatch.setattr(eng, "V3_READ_AUTHORITY_FAILURES", {})
    assert len(eng._v3_ledger_rows("decision")) == 4
    assert eng._v3_ledger_rows("market_segment") == []
    with open(path, "ab") as fh:  # grown retired file -> soft-exclude; active ledgers stay readable (BTC-165)
        fh.write(b'{"record_id":"late"}\n')
    monkeypatch.setattr(eng, "V3_READ_AUTHORITY_FAILURES", {})
    assert len(eng._v3_ledger_rows("decision")) == 4
    assert eng.V3_READ_AUTHORITY_FAILURES == {}
    # market_segment remains unreadable (excluded) without poisoning decision authority
    assert eng._v3_ledger_rows("market_segment") == []
    assert "market_segment" in eng.V3_READ_AUTHORITY_FAILURES  # RetiredStreamUnreadable is recorded

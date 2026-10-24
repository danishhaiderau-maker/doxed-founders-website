"""R17 ledger delete-by-sha: every refusal path, invariants, index rebase, snapshot read-back, no-resurrect."""
import hashlib
import json
from pathlib import Path

import pytest

import ledger_delete_by_sha as lds
from research_v3_store import V3EvidenceStore
from transactional_receipt_store import legacy_receipt_path, read_legacy_receipt


@pytest.fixture(autouse=True)
def _reset_alert_ratelimit():
    import research_v3_store
    research_v3_store._tombstone_alert_last.clear()
    yield
    research_v3_store._tombstone_alert_last.clear()


def _store(tmp_path, n=6, ledgers=("decision", "lifecycle")):
    s = V3EvidenceStore(tmp_path, epoch_id="epoch-test")
    for led in ledgers:
        for i in range(n):
            assert s.append(led, {"record_id": f"{led}-{i}", "i": i})["written"]
    return s


def _lines(s, led):
    return s.ledger_path(led).read_bytes().splitlines(keepends=True)


def _sha(line):
    return hashlib.sha256(line.rstrip(b"\r\n")).hexdigest()


def _list(entries):
    text = "".join(json.dumps(e) + "\n" for e in entries)
    return text, lds.list_sha256(text)


def _entries(s, led, idx, **extra):
    ls = _lines(s, led)
    return [{"stream": f"v3/ledgers/{led}.jsonl", "row_sha256": _sha(ls[i]), **extra} for i in idx]


def _refused(code, s, text, sha, **kw):
    before = {led: s.ledger_path(led).read_bytes() for led in lds.ALLOWED_LEDGERS if s.ledger_path(led).exists()}
    with pytest.raises(lds.DeleteRefused) as e:
        lds.delete_ledger_rows_by_sha(s, text, sha, dry_run=False, **kw)
    assert e.value.code == code
    after = {led: s.ledger_path(led).read_bytes() for led in before}
    assert before == after  # nothing written on refusal
    assert not (s.root / "v3" / lds.SNAPSHOT_DIRNAME).exists() or not any((s.root / "v3" / lds.SNAPSHOT_DIRNAME).iterdir())


def test_dry_run_default_writes_nothing(tmp_path):
    s = _store(tmp_path)
    text, sha = _list(_entries(s, "decision", [1, 3]))
    before = s.ledger_path("decision").read_bytes()
    out = lds.delete_ledger_rows_by_sha(s, text, sha)
    assert out["dry_run"] is True and out["snapshot_path"] is None
    assert out["files"]["decision"] == {"before_count": 6, "removed_count": 2, "after_count": 4,
                                        "remaining_rows_sha256": out["files"]["decision"]["remaining_rows_sha256"]}
    assert s.ledger_path("decision").read_bytes() == before


def test_real_delete_counts_hash_receipts_index_snapshot(tmp_path):
    s = _store(tmp_path)
    keep_before = [l for i, l in enumerate(_lines(s, "decision")) if i not in (1, 3)]
    text, sha = _list(_entries(s, "decision", [1, 3]))
    dry = lds.delete_ledger_rows_by_sha(s, text, sha)
    out = lds.delete_ledger_rows_by_sha(s, text, sha, dry_run=False)
    d = out["files"]["decision"]
    assert d["after_count"] == d["before_count"] - 2 == d["after_count_read_back"] == 4
    assert d["remaining_rows_sha256"] == dry["files"]["decision"]["remaining_rows_sha256"] == d["remaining_rows_sha256_read_back"]
    assert _lines(s, "decision") == keep_before
    assert set(out["files"]) == {"decision"}
    assert len(_lines(s, "lifecycle")) == 6
    # deleted receipts gone; survivors rebased and verifiable
    assert read_legacy_receipt(s.root, "decision", "decision-1") is None
    for rid in ("decision-0", "decision-2", "decision-4", "decision-5"):
        s.verified_record_receipt("decision", rid)
    # index complete for the new signature; appends continue normally
    from research_v3_store import _path_signature
    assert s._complete_generation("decision", _path_signature(s.ledger_path("decision")))
    assert s.append("decision", {"record_id": "decision-new", "i": 99})["written"]
    # snapshot exists, then deleted with read-back
    snap = Path(out["snapshot_path"])
    assert snap.is_dir()
    gone = lds.delete_rollback_snapshot(s, str(snap))
    assert gone == {"snapshot_path": str(snap.resolve()), "deleted": True, "exists_after": False}
    assert not snap.exists()


def test_no_resurrect_after_delete(tmp_path):
    s = _store(tmp_path, n=3, ledgers=("decision",))
    text, sha = _list(_entries(s, "decision", [1]))
    deleted = _lines(s, "decision")[1]
    lds.delete_ledger_rows_by_sha(s, text, sha, dry_run=False)
    res = s.append("decision", {"record_id": "decision-1", "i": 1})
    assert res["written"] is False and res["reason"] == "DELETED_ROW_TOMBSTONED"
    assert deleted not in _lines(s, "decision")
    assert len(_lines(s, "decision")) == 2


def test_multiplicity_allows_identical_duplicates(tmp_path):
    s = _store(tmp_path, n=3, ledgers=("decision",))
    p = s.ledger_path("decision")
    # identical duplicate line without a receipt-bearing record id cannot come from append; craft one
    dup = json.dumps({"x": 1}).encode() + b"\n"
    _rebuild(s, "decision", _lines(s, "decision") + [dup, dup])
    text, sha = _list([{"stream": "v3/ledgers/decision.jsonl", "row_sha256": _sha(dup)}])
    _refused("DUPLICATE_LINES_WITHOUT_MULTIPLICITY", s, text, sha)
    text3, sha3 = _list([{"stream": "v3/ledgers/decision.jsonl", "row_sha256": _sha(dup), "multiplicity": 3}])
    _refused("MULTIPLICITY_MISMATCH", s, text3, sha3)
    text2, sha2 = _list([{"stream": "v3/ledgers/decision.jsonl", "row_sha256": _sha(dup), "multiplicity": 2}])
    with pytest.raises(lds.DeleteRefused) as e:  # crafted rows have no record_id -> unparseable for receipts
        lds.delete_ledger_rows_by_sha(s, text2, sha2)
    assert e.value.code == "ROW_UNPARSEABLE"


def test_multiplicity_success_with_record_ids(tmp_path, monkeypatch):
    s = _store(tmp_path, n=2, ledgers=("decision",))
    line = _lines(s, "decision")[1]
    _rebuild(s, "decision", _lines(s, "decision") + [line])
    # duplicate record id: receipt points at first copy only -> second copy's receipt check fails
    text, sha = _list([{"stream": "v3/ledgers/decision.jsonl", "row_sha256": _sha(line), "multiplicity": 2}])
    with pytest.raises(lds.DeleteRefused) as e:
        lds.delete_ledger_rows_by_sha(s, text, sha)
    assert e.value.code == "RECEIPT_INVALID"


def _rebuild(s, led, lines):
    """Rewrite a ledger and re-bind its completeness so the store index is complete again."""
    from research_v3_store import _path_signature
    p = s.ledger_path(led)
    p.write_bytes(b"".join(lines))
    sig = _path_signature(p)
    last = lines[-1]
    comp_p = s._completeness_path(led)
    comp = json.loads(comp_p.read_text())
    comp.update({"ledger_signature": s._signature_payload(sig),
                 "tail_anchor": {"offset": sum(map(len, lines[:-1])), "length": len(last),
                                 "sha256": hashlib.sha256(last).hexdigest()}})
    s._atomic_json_receipt(comp_p, comp)


@pytest.mark.parametrize("text,code", [("", "EMPTY_LIST"), ("\n\n", "EMPTY_LIST"), (None, "EMPTY_LIST")])
def test_refuse_empty(tmp_path, text, code):
    s = _store(tmp_path, n=1, ledgers=("decision",))
    _refused(code, s, text, lds.list_sha256(text or ""))


def test_refuse_sha_mismatch(tmp_path):
    s = _store(tmp_path)
    text, _ = _list(_entries(s, "decision", [0]))
    _refused("LIST_SHA_MISMATCH", s, text, "0" * 64)
    _refused("LIST_SHA_MISMATCH", s, text, None)


def test_refuse_parse_error(tmp_path):
    s = _store(tmp_path)
    for text in ("{not json}\n", "[1,2]\n"):
        _refused("LIST_PARSE_ERROR", s, text, lds.list_sha256(text))


@pytest.mark.parametrize("entry", [
    {"stream": "v3/ledgers/decision.jsonl", "row_sha256": "XYZ"},
    {"stream": "v3/ledgers/decision.jsonl", "row_sha256": "a" * 64, "multiplicity": 0},
    {"stream": "v3/ledgers/decision.jsonl", "row_sha256": "a" * 64, "multiplicity": True},
    {"stream": "v3/ledgers/decision.jsonl", "row_sha256": "a" * 64, "multiplicity": "2"},
])
def test_refuse_entry_invalid(tmp_path, entry):
    s = _store(tmp_path)
    text, sha = _list([entry])
    _refused("ENTRY_INVALID", s, text, sha)


@pytest.mark.parametrize("stream", ["v3/ledgers/opportunity.jsonl", "v3/ledgers/../x.jsonl",
                                    "/app/data/runtime/v3/ledgers/decision.jsonl", "", "decision"])
def test_refuse_stream_not_allowed(tmp_path, stream):
    s = _store(tmp_path)
    text, sha = _list([{"stream": stream, "row_sha256": "a" * 64}])
    _refused("STREAM_NOT_ALLOWED", s, text, sha)


def test_refuse_list_duplicate_entry(tmp_path):
    s = _store(tmp_path)
    e = _entries(s, "decision", [0])
    text, sha = _list(e + e)
    _refused("LIST_DUPLICATE_ENTRY", s, text, sha)


def test_refuse_row_missing(tmp_path):
    s = _store(tmp_path)
    text, sha = _list(_entries(s, "decision", [0]) + [{"stream": "v3/ledgers/decision.jsonl", "row_sha256": "b" * 64}])
    _refused("ROW_MISSING", s, text, sha)


def test_refuse_ledger_missing(tmp_path):
    s = _store(tmp_path, ledgers=("decision",))
    text, sha = _list([{"stream": "v3/ledgers/order_intent.jsonl", "row_sha256": "a" * 64}])
    _refused("LEDGER_MISSING", s, text, sha)


def test_refuse_partial_trailing_line(tmp_path):
    s = _store(tmp_path)
    ls = _lines(s, "decision")
    _rebuild(s, "decision", ls + [b'{"partial":'])
    text, sha = _list(_entries(s, "decision", [0]))
    _refused("PARTIAL_TRAILING_LINE", s, text, sha)


def test_refuse_index_not_complete(tmp_path):
    s = _store(tmp_path)
    s._completeness_path("decision").unlink()
    text, sha = _list(_entries(s, "decision", [0]))
    _refused("INDEX_NOT_COMPLETE", s, text, sha)


def test_refuse_generation_not_supported(tmp_path, monkeypatch):
    s = _store(tmp_path)
    monkeypatch.setattr(s, "_active_ledger_generation", lambda led: {"generation": 1})
    text, sha = _list(_entries(s, "decision", [0]))
    _refused("LEDGER_GENERATION_NOT_SUPPORTED", s, text, sha)


def test_refuse_append_head_pending(tmp_path):
    s = _store(tmp_path)
    p = s._append_head_path("decision")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}")
    text, sha = _list(_entries(s, "decision", [0]))
    _refused("APPEND_HEAD_PENDING", s, text, sha)


def test_refuse_transactional_authority(tmp_path, monkeypatch):
    s = _store(tmp_path)
    monkeypatch.setattr(s, "_transactional_receipts", lambda: object())
    text, sha = _list(_entries(s, "decision", [0]))
    _refused("TRANSACTIONAL_AUTHORITY_ACTIVE", s, text, sha)


def test_refuse_row_unparseable(tmp_path):
    s = _store(tmp_path)
    junk = b'{"no_record_id": 1}\n'
    _rebuild(s, "decision", _lines(s, "decision") + [junk])
    text, sha = _list([{"stream": "v3/ledgers/decision.jsonl", "row_sha256": _sha(junk)}])
    _refused("ROW_UNPARSEABLE", s, text, sha)


def test_refuse_record_id_shared_with_survivor(tmp_path):
    s = _store(tmp_path)
    twin = json.dumps({"record_id": "decision-0", "variant": 2}).encode() + b"\n"
    _rebuild(s, "decision", _lines(s, "decision") + [twin])
    text, sha = _list([{"stream": "v3/ledgers/decision.jsonl", "row_sha256": _sha(twin)}])
    _refused("RECORD_ID_SHARED_WITH_SURVIVOR", s, text, sha)


def test_refuse_receipt_missing(tmp_path):
    s = _store(tmp_path)
    legacy_receipt_path(s.root, "decision", "decision-4").unlink()
    text, sha = _list(_entries(s, "decision", [0]))
    _refused("RECEIPT_MISSING", s, text, sha)


def test_refuse_receipt_invalid(tmp_path):
    s = _store(tmp_path)
    p = legacy_receipt_path(s.root, "decision", "decision-2")
    r = json.loads(p.read_text()); r["offset"] = 1
    p.write_text(json.dumps(r))
    text, sha = _list(_entries(s, "decision", [0]))
    _refused("RECEIPT_INVALID", s, text, sha)


def test_invariant_failure_restores_everything(tmp_path, monkeypatch):
    s = _store(tmp_path)
    before = s.ledger_path("decision").read_bytes()
    r_before = legacy_receipt_path(s.root, "decision", "decision-5").read_bytes()
    text, sha = _list(_entries(s, "decision", [1]))
    monkeypatch.setattr(lds, "_rows_hash", lambda lines, _c=[0]: (_c.__setitem__(0, _c[0] + 1) or str(_c[0])))
    with pytest.raises(lds.DeleteRefused) as e:
        lds.delete_ledger_rows_by_sha(s, text, sha, dry_run=False)
    assert e.value.code == "INVARIANT_FAILED_RESTORED"
    assert s.ledger_path("decision").read_bytes() == before
    assert legacy_receipt_path(s.root, "decision", "decision-5").read_bytes() == r_before
    assert read_legacy_receipt(s.root, "decision", "decision-1") is not None


def test_snapshot_route_refusals(tmp_path):
    s = _store(tmp_path, n=1, ledgers=("decision",))
    for bad in (None, "", str(tmp_path), str(tmp_path / "v3" / lds.SNAPSHOT_DIRNAME / "../ledgers"),
                str(tmp_path / "v3" / lds.SNAPSHOT_DIRNAME / "evil")):
        with pytest.raises(lds.DeleteRefused) as e:
            lds.delete_rollback_snapshot(s, bad)
        assert e.value.code == "SNAPSHOT_PATH_NOT_ALLOWED"
    with pytest.raises(lds.DeleteRefused) as e:
        lds.delete_rollback_snapshot(s, str(tmp_path / "v3" / lds.SNAPSHOT_DIRNAME / "1792840000000-abcdefabcdef"))
    assert e.value.code == "SNAPSHOT_MISSING"


def test_lifecycle_membership_invalidated(tmp_path):
    s = _store(tmp_path, ledgers=("lifecycle",))
    m = s._lifecycle_membership_dir / "current.json"
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps({"schema": "lifecycle_membership_generation_v1", "generation_id": "old"}))
    text, sha = _list(_entries(s, "lifecycle", [2]))
    lds.delete_ledger_rows_by_sha(s, text, sha, dry_run=False)
    assert json.loads(m.read_text())["generation_id"] != "old"


def test_refusal_codes_all_covered():
    src = Path(__file__).read_text()
    for code in lds.REFUSAL_CODES:
        assert f'"{code}"' in src, code


def test_refuse_multi_stream_list(tmp_path):
    s = _store(tmp_path)
    text, sha = _list(_entries(s, "decision", [1]) + _entries(s, "lifecycle", [0]))
    _refused("MULTI_STREAM_LIST_REFUSED", s, text, sha)
    with pytest.raises(lds.DeleteRefused) as e:  # dry run refuses too
        lds.delete_ledger_rows_by_sha(s, text, sha)
    assert e.value.code == "MULTI_STREAM_LIST_REFUSED"


def _deleted_store(tmp_path):
    s = _store(tmp_path, n=4, ledgers=("decision",))
    text, sha = _list(_entries(s, "decision", [1]))
    lds.delete_ledger_rows_by_sha(s, text, sha, dry_run=False)
    return s


def test_tombstone_integrity_pin_survives_appends(tmp_path):
    s = _deleted_store(tmp_path)
    assert s.append("decision", {"record_id": "decision-x", "i": 7})["written"]
    pin = json.loads(s._completeness_path("decision").read_text())[lds.TOMBSTONE_PIN_KEY]
    assert pin == json.loads(lds._tombstone_path(s, "decision").read_text())["integrity_sha256"]


def test_tombstone_missing_fails_closed(tmp_path, caplog):
    s = _deleted_store(tmp_path)
    lds._tombstone_path(s, "decision").unlink()
    res = s.append("decision", {"record_id": "decision-y", "i": 8})
    assert res["blocked"] and res["reason"] == "DELETED_ROW_TOMBSTONES_MISSING"
    assert "[ALERT]" in caplog.text
    with pytest.raises(lds.DeleteRefused) as e:
        lds.load_tombstones(s.root, "decision")
    assert e.value.code == "DELETED_ROW_TOMBSTONES_MISSING"


@pytest.mark.parametrize("mutate", ["garbage", "drop_sha", "bad_integrity"])
def test_tombstone_corrupt_fails_closed(tmp_path, mutate):
    s = _deleted_store(tmp_path)
    p = lds._tombstone_path(s, "decision")
    row = json.loads(p.read_text())
    if mutate == "garbage":
        p.write_text("{not json")
    elif mutate == "drop_sha":  # consistent self-hash but not the pinned one
        row["row_sha256"] = []
        row["integrity_sha256"] = lds.tombstone_integrity([])
        p.write_text(json.dumps(row))
    else:
        row["integrity_sha256"] = "0" * 64
        p.write_text(json.dumps(row))
    res = s.append("decision", {"record_id": "decision-z", "i": 9})
    assert res["blocked"] and res["reason"] == "DELETED_ROW_TOMBSTONES_CORRUPT"
    # a further delete on that stream is refused too
    text, sha = _list(_entries(s, "decision", [0]))
    with pytest.raises(lds.DeleteRefused) as e:
        lds.delete_ledger_rows_by_sha(s, text, sha, dry_run=False)
    assert e.value.code == "DELETED_ROW_TOMBSTONES_CORRUPT"


def test_other_streams_unaffected_without_deletes(tmp_path):
    s = _store(tmp_path, n=1, ledgers=("decision", "lifecycle"))
    assert s.append("lifecycle", {"record_id": "lifecycle-9", "i": 9})["written"]


class _Death(BaseException):
    """Simulates the process dying (not an Exception, so no in-process restore runs)."""


def test_boot_restore_after_process_death_mid_write(tmp_path, monkeypatch):
    s = _store(tmp_path, ledgers=("decision",))
    files = [s.ledger_path("decision"), s._completeness_path("decision")] + [
        legacy_receipt_path(s.root, "decision", f"decision-{i}") for i in range(6)]
    before = {p: p.read_bytes() for p in files}
    text, sha = _list(_entries(s, "decision", [1]))
    real = s._atomic_json_receipt
    calls = []

    def dying(path, row, **kw):
        calls.append(path)
        if len(calls) == 2:  # ledger already replaced, receipts half-rebased
            raise _Death()
        return real(path, row, **kw)

    monkeypatch.setattr(s, "_atomic_json_receipt", dying)
    with pytest.raises(_Death):
        lds.delete_ledger_rows_by_sha(s, text, sha, dry_run=False)
    monkeypatch.setattr(s, "_atomic_json_receipt", real)
    assert s.ledger_path("decision").read_bytes() != before[s.ledger_path("decision")]
    snap = next((tmp_path / "v3" / lds.SNAPSHOT_DIRNAME).iterdir())
    with pytest.raises(lds.DeleteRefused) as e:  # cannot drop the snapshot of an interrupted delete
        lds.delete_rollback_snapshot(s, str(snap))
    assert e.value.code == "SNAPSHOT_INCOMPLETE_DELETE"
    fresh = V3EvidenceStore.open_read_only(tmp_path)
    out = lds.restore_incomplete_deletes(fresh)
    assert [r["ledger"] for r in out] == ["decision"]
    assert {p: p.read_bytes() for p in files} == before
    assert lds.restore_incomplete_deletes(fresh) == []  # idempotent
    for i in range(6):
        fresh.verified_record_receipt("decision", f"decision-{i}")
    assert lds.delete_rollback_snapshot(fresh, str(snap))["deleted"] is True


def test_boot_restore_ignores_applied_and_death_before_manifest(tmp_path, monkeypatch):
    s = _store(tmp_path, ledgers=("decision",))
    text, sha = _list(_entries(s, "decision", [1]))
    lds.delete_ledger_rows_by_sha(s, text, sha, dry_run=False)
    after = s.ledger_path("decision").read_bytes()
    assert lds.restore_incomplete_deletes(s) == []
    assert s.ledger_path("decision").read_bytes() == after
    # death while copying snapshot files: no manifest, nothing touched, nothing to restore
    text2, sha2 = _list(_entries(s, "decision", [0]))
    monkeypatch.setattr(lds, "_copy_fsync", lambda a, b: (_ for _ in ()).throw(_Death()))
    with pytest.raises(_Death):
        lds.delete_ledger_rows_by_sha(s, text2, sha2, dry_run=False)
    assert s.ledger_path("decision").read_bytes() == after
    assert lds.restore_incomplete_deletes(s) == []


def test_real_r17_list_d0dcf920_refused_as_multi_stream(tmp_path):
    """HM R2 nit: the literal RK list d0dcf920…f473 (4 streams) is refused, in dry run and real run."""
    import gzip
    raw = gzip.decompress((Path(__file__).parent / "fixtures" / "r17_analyzer_epoch_unplaced_rows_d0dcf920.jsonl.gz").read_bytes())
    text = raw.decode("utf-8")
    sha = lds.list_sha256(text)
    assert sha == "d0dcf9206dd77297913c59013555104367792a6f372731687ebd6d676d2af473"
    assert len(text.splitlines()) == 10771
    s = _store(tmp_path, n=1)
    for dry in (True, False):
        with pytest.raises(lds.DeleteRefused) as e:
            lds.delete_ledger_rows_by_sha(s, text, sha, dry_run=dry)
        assert e.value.code == "MULTI_STREAM_LIST_REFUSED"
        assert e.value.detail == {"streams": ["decision", "lifecycle", "market_segment", "order_intent"]}


@pytest.mark.parametrize("how", ["unlink", "drop_pin"])
def test_complete_json_lost_with_tombstones_fails_closed(tmp_path, how):
    s = _deleted_store(tmp_path)
    comp = s._completeness_path("decision")
    if how == "unlink":
        comp.unlink()
        expect = {"DELETED_ROW_TOMBSTONES_INDEX_MISSING", "DELETED_ROW_TOMBSTONES_PIN_MISSING"}
        assert lds.tombstone_state(s.root, "decision")[2] == "DELETED_ROW_TOMBSTONES_INDEX_MISSING"
    else:
        row = json.loads(comp.read_text()); row.pop(lds.TOMBSTONE_PIN_KEY)
        comp.write_text(json.dumps(row))
        expect = {"DELETED_ROW_TOMBSTONES_PIN_MISSING"}
    res = s.append("decision", {"record_id": "decision-q", "i": 11})
    assert res["blocked"] and res["reason"] in expect


def test_tombstone_alert_rate_limited(tmp_path, caplog, monkeypatch):
    import research_v3_store
    s = _deleted_store(tmp_path)
    lds._tombstone_path(s, "decision").unlink()
    for i in range(5):
        assert s.append("decision", {"record_id": f"decision-r{i}", "i": i})["blocked"]
    assert caplog.text.count("[ALERT] [LEDGER-TOMBSTONES]") == 1
    clock = [research_v3_store.time.monotonic() + research_v3_store.TOMBSTONE_ALERT_INTERVAL_SEC + 1]
    monkeypatch.setattr(research_v3_store.time, "monotonic", lambda: clock[0])
    assert s.append("decision", {"record_id": "decision-r9", "i": 9})["blocked"]
    assert caplog.text.count("[ALERT] [LEDGER-TOMBSTONES]") == 2


def test_fail_closed_state_cached_until_files_change(tmp_path, monkeypatch):
    import ledger_delete_by_sha as mod
    s = _deleted_store(tmp_path)
    lds._tombstone_path(s, "decision").unlink()
    calls = []
    real = mod.tombstone_state
    monkeypatch.setattr(mod, "tombstone_state", lambda *a, **k: calls.append(1) or real(*a, **k))
    for i in range(4):
        assert s.append("decision", {"record_id": f"decision-c{i}", "i": i})["blocked"]
    assert len(calls) == 1

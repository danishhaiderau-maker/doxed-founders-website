"""HM (c): restore test pinned to the keep manifest, HEAD-verified, misses counted separately; reconcile; plan."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import r2_backup as rb  # noqa: E402
import r2_quarantine_plan as plan  # noqa: E402


class _NoSuchKey(Exception):
    def __init__(self):
        self.response = {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}


def _ts(s):
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


class FakeS3:
    """In-memory bucket: key -> (body, metadata, last_modified)."""

    def __init__(self):
        self.objs = {}

    def put(self, key, raw: bytes, when="2026-10-16T01:00:00Z", z=None):
        body = raw if z is None else z
        self.objs[key] = (body, {"sha256": hashlib.sha256(raw).hexdigest(), "raw-size": str(len(raw))}, _ts(when))

    def head_object(self, Bucket, Key):
        if Key not in self.objs:
            raise _NoSuchKey()
        return {"Metadata": self.objs[Key][1], "ContentLength": len(self.objs[Key][0])}

    def get_object(self, Bucket, Key):
        if Key not in self.objs:
            raise _NoSuchKey()
        return {"Body": io.BytesIO(self.objs[Key][0])}

    def get_paginator(self, _name):
        outer = self

        class P:
            def paginate(self, Bucket):
                items = [{"Key": k, "Size": len(v[0]), "LastModified": v[2]} for k, v in sorted(outer.objs.items())]
                yield {"Contents": items[:2]}
                yield {"Contents": items[2:]}
        return P()


ident = lambda b: b  # noqa: E731  (fake "compression" = identity)


def _state_for(s3, keys):
    return {k: {"sig": [1, 1], "sha256": hashlib.sha256(s3.objs[k + ".zst"][0]).hexdigest(), "raw": 1, "zst": 1}
            for k in keys}


def test_13_oct_failure_is_reproduced_and_counted_as_missing_not_ok():
    s3 = FakeS3()
    for i in range(3):
        s3.put(f"fly-mirror-segments/f{i}.zst".replace(".zst", "") + ".zst", f"data{i}".encode())
    state = {f"fly-mirror-segments/f{i}": {"sig": [1, 1], "sha256": hashlib.sha256(f"data{i}".encode()).hexdigest()}
             for i in range(3)}
    for i in range(3, 300):   # stale keys the wipe deleted from the bucket
        state[f"fly-mirror-segments/f{i}"] = {"sig": [1, 1], "sha256": "00" * 32}
    r = rb.restore_test(state, s3, ["fly-mirror-segments/"], sample=100, rng=random.Random(1), decompress=ident)
    assert r["missing"] > 80 and r["passed"] is False and r["bad"] == 0 and r["checked"] == 100
    rec = rb.build_receipt(state=state, report=None, restore=r, keep={"prefixes": ["fly-mirror-segments/"], "sha256": "x"})
    assert rec["ok"] is False and rec["missing"] == r["missing"] and rec["state_keys"] == 300


def test_restore_samples_only_pinned_keys_and_head_check_precedes_get():
    s3 = FakeS3()
    s3.put("keep-a/x.zst", b"x")
    state = {"keep-a/x": {"sha256": hashlib.sha256(b"x").hexdigest()},
             "other/stale-1": {"sha256": "00" * 32}, "other/stale-2": {"sha256": "00" * 32}}
    calls = []
    orig_head, orig_get = s3.head_object, s3.get_object
    s3.head_object = lambda **kw: (calls.append(("head", kw["Key"])), orig_head(**kw))[1]
    s3.get_object = lambda **kw: (calls.append(("get", kw["Key"])), orig_get(**kw))[1]
    r = rb.restore_test(state, s3, ["keep-a/"], sample=100, decompress=ident)
    assert r["pinned_pool"] == 1 and r["checked"] == 1 and r["ok"] == 1 and r["missing"] == 0 and r["passed"] is True
    assert calls == [("head", "keep-a/x.zst"), ("get", "keep-a/x.zst")]


def test_hash_mismatch_is_bad_not_missing_and_empty_pinned_set_is_not_a_pass():
    s3 = FakeS3()
    s3.put("keep/a.zst", b"real")
    state = {"keep/a": {"sha256": "ab" * 32}}
    r = rb.restore_test(state, s3, ["keep/"], sample=10, decompress=ident)
    assert r["bad"] == 1 and r["missing"] == 0 and r["passed"] is False
    empty = rb.restore_test({}, s3, ["keep/"], sample=10, decompress=ident)
    assert empty["checked"] == 0 and empty["empty_pinned_set"] and empty["passed"] is False
    assert rb.restore_test({}, s3, ["keep/"], sample=10, decompress=ident, allow_empty=True)["passed"] is True
    none_pinned = rb.restore_test({"x/y": {"sha256": "00" * 32}}, s3, [], sample=10, decompress=ident)
    assert none_pinned["pinned_pool"] == 0 and none_pinned["passed"] is False


def test_reconcile_rebuilds_state_from_listing_intersect_keep_and_counts():
    s3 = FakeS3()
    s3.put("keep/a.zst", b"A"); s3.put("keep/b.zst", b"B")           # in bucket, a in state, b adoptable
    s3.put("old/z.zst", b"Z", when="2026-10-13T16:30:00Z")           # outside keep (pre-wipe re-upload)
    s3.put("_quarantine/20261013/old/q.zst", b"Q"); s3.put("_meta/state.json.zst", b"M")
    state = {"keep/a": {"sig": [1, 1], "sha256": hashlib.sha256(b"A").hexdigest(), "raw": 1, "zst": 1},
             "keep/gone": {"sig": [1, 1], "sha256": "11" * 32}, "old/z": {"sig": [1, 1], "sha256": "22" * 32}}
    head = lambda k: dict(s3.objs[k][1]) if k in s3.objs else None  # noqa: E731
    new, rep = rb.reconcile_state(state, rb.list_bucket(s3), ["keep/"], head=head)
    assert sorted(new) == ["keep/a", "keep/b"]
    assert new["keep/b"]["sig"] == rb.SIG_UNKNOWN and new["keep/b"]["sha256"] == hashlib.sha256(b"B").hexdigest()
    assert rep["missing"] == 1 and rep["missing_examples"] == ["keep/gone"]
    assert rep["r2_objects"] == 2 and rep["state_keys_before"] == 3 and rep["state_keys_after"] == 2
    assert rep["quarantined"] == 1 and rep["outside_keep"] == 1 and rep["dropped_not_pinned"] == 1
    assert rep["adopted_from_bucket"] == 1


def test_reconcile_then_restore_hits_zero_missing_in_a_100_sample():
    s3 = FakeS3()
    for i in range(150):
        s3.put(f"keep/f{i}.zst", f"d{i}".encode())
    stale = {f"keep/f{i}": {"sig": [1, 1], "sha256": hashlib.sha256(f"d{i}".encode()).hexdigest()} for i in range(150)}
    stale.update({f"keep/dead{i}": {"sig": [1, 1], "sha256": "00" * 32} for i in range(2000)})
    before = rb.restore_test(stale, s3, ["keep/"], sample=100, rng=random.Random(3), decompress=ident)
    assert before["missing"] > 50
    fixed, rep = rb.reconcile_state(stale, rb.list_bucket(s3), ["keep/"], head=None)
    after = rb.restore_test(fixed, s3, ["keep/"], sample=100, rng=random.Random(3), decompress=ident)
    assert after["missing"] == 0 and after["bad"] == 0 and after["checked"] == 100 and after["passed"]
    assert rep["missing"] == 2000                      # dropped by reconcile (historical, not current)
    _, verify = rb.reconcile_state(fixed, rb.list_bucket(s3), ["keep/"], head=None)   # what cmd_sync receipts
    rec = rb.build_receipt(state=fixed, report=verify, restore=after, keep={"prefixes": ["keep/"], "sha256": "k"},
                           extra={"reconciled_dropped": rep["missing"]})
    assert rec["ok"] is True and rec["reconciled_dropped"] == 2000 and rec["missing"] == 0 and rec["r2_objects"] == 150 and rec["state_keys"] == 150


def test_receipt_not_ok_while_quarantine_pending_or_errors():
    r = {"passed": True, "missing": 0, "bad": 0, "checked": 5}
    k = {"prefixes": ["a/"], "sha256": "k"}
    assert rb.build_receipt(state={}, report={"missing": 0}, restore=r, keep=k)["ok"] is True
    assert rb.build_receipt(state={}, report={"missing": 0}, restore=r, keep=k, quarantine_pending=3)["ok"] is False
    assert rb.build_receipt(state={}, report={"missing": 0}, restore=r, keep=k, errors=1)["ok"] is False
    assert rb.build_receipt(state={}, report={"missing": 0}, restore=None, keep=k)["ok"] is False


def test_keep_manifest_loading_is_hashed_and_order_independent(tmp_path):
    f = tmp_path / "keep.json"
    f.write_text(json.dumps({"r2_keep_prefixes": ["b/", "a/"]}))
    a = rb.load_keep(keep_manifest=str(f))
    b = rb.load_keep(keep_prefixes="a/,b/")
    assert a["prefixes"] == ["a/", "b/"] and a["sha256"] == b["sha256"]
    assert rb.load_keep()["prefixes"] == []


def test_quarantine_plan_lists_only_the_window_and_has_no_move_code():
    s3 = FakeS3()
    for i in range(5):
        s3.put(f"fly-mirror-segments/p{i}.zst", b"x", when="2026-10-13T16:30:00Z")
    s3.put("fly-mirror-segments/early.zst", b"x", when="2026-10-13T15:59:59Z")
    s3.put("fly-mirror-segments/late.zst", b"x", when="2026-10-13T17:00:00Z")
    s3.put("keep/pinned.zst", b"x", when="2026-10-13T16:30:00Z")
    s3.put("_meta/state.json.zst", b"x", when="2026-10-13T16:30:00Z")
    s3.put("_quarantine/20261013/a.zst", b"x", when="2026-10-13T16:30:00Z")
    summary, rows = plan.build_plan(rb.list_bucket(s3), ["keep/"], rb.QUARANTINE_SINCE, rb.QUARANTINE_UNTIL, 5)
    assert summary["candidates"] == 5 and summary["count_matches_expected"] is True
    assert summary["mode"] == "DRY_RUN_NO_MOVE" and len(summary["plan_sha256"]) == 64
    assert all(r["quarantine_key"].startswith("_quarantine/20261013/") for r in rows)
    assert plan.build_plan(rb.list_bucket(s3), ["keep/"], rb.QUARANTINE_SINCE, rb.QUARANTINE_UNTIL, 5930)[0][
        "count_matches_expected"] is False
    src = (Path(plan.__file__)).read_text()
    for forbidden in ("copy_object", "delete_object", "delete_objects", "put_object", "upload_file", "os.remove",
                      "shutil"):
        assert forbidden not in src, forbidden
    assert quarantine_in_backup_has_no_mutations()


def quarantine_in_backup_has_no_mutations():
    src = Path(rb.__file__).read_text()
    return "delete_object" not in src and "copy_object" not in src and "os.remove(path" not in src

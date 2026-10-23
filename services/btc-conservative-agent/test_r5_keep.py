"""R5 keep sets (HM C7): open lifecycles, prereg window, shipped-segment keep seqs. Read-only helpers."""
from __future__ import annotations

import json
import sqlite3

import lifecycle_bundles
import r5_keep

KEY = {"collection_epoch_id": "ep", "episode_id": "e1", "policy_signature": "p", "research_lane": "L"}
SHA = "ab" * 32


def _index(root, *, dirty=(), retry=(), events=()):
    db = root / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    cols = "collection_epoch_id TEXT, episode_id TEXT, policy_signature TEXT, research_lane TEXT"
    con.execute(f"CREATE TABLE dirty_lifecycle ({cols})")
    con.execute(f"CREATE TABLE qualification_retry ({cols}, retry_at REAL, reason TEXT, updated_unix REAL)")
    con.execute(f"CREATE TABLE lifecycle_event (ledger TEXT, byte_offset INTEGER, row_length INTEGER, {cols})")
    for k in dirty:
        con.execute("INSERT INTO dirty_lifecycle VALUES (?,?,?,?)", tuple(k.values()))
    for k in retry:
        con.execute("INSERT INTO qualification_retry VALUES (?,?,?,?,0,'x',0)", tuple(k.values()))
    for ledger, off, length, k in events:
        con.execute("INSERT INTO lifecycle_event VALUES (?,?,?,?,?,?,?)", (ledger, off, length, *k.values()))
    con.commit()
    con.close()


def test_identity_matches_lifecycle_bundles():
    assert r5_keep.identity_id(KEY) == lifecycle_bundles.LifecycleKey(**KEY).identity_id


def test_open_lifecycles_reads_pointer_ledger_rows_and_is_readonly(tmp_path):
    ledger = tmp_path / "v3" / "ledgers" / "signals.jsonl"
    ledger.parent.mkdir(parents=True)
    first = json.dumps({"observed_ts": 1000.0, "market_segment_ref": {"sha256": SHA}}).encode()
    second = json.dumps({"ts": 900.0, "x_segment_refs": [{"sha256": "cd" * 32}, {"sha256": "bad"}]}).encode()
    ledger.write_bytes(first + b"\n" + second + b"\n")
    retry_key = dict(KEY, episode_id="e2")
    _index(tmp_path, dirty=[KEY], retry=[retry_key, KEY],
           events=[("signals", 0, len(first), KEY), ("signals", len(first) + 1, len(second), KEY)])
    ident = r5_keep.identity_id(KEY)
    ptr = tmp_path / "v3" / "lifecycle_transfer_bundles" / "index" / f"{ident}.json"
    ptr.parent.mkdir(parents=True)
    ptr.write_text(json.dumps({"bundle_id": "transfer-" + "ee" * 32}))
    db = tmp_path / "v3" / "lifecycle_bundle_index" / "lifecycle_index.sqlite3"
    before = db.read_bytes()
    info = r5_keep.open_lifecycles(tmp_path)
    assert info["open_count"] == 2 and info["error"] is None and info["truncated"] is False
    assert info["bundle_ids"] == {"ee" * 32}
    assert info["segment_shas"] == {SHA, "cd" * 32}
    assert info["earliest_start_ts"] == 900.0
    assert db.read_bytes() == before


def test_open_lifecycles_empty_without_index_and_truncates(tmp_path):
    assert r5_keep.open_lifecycles(tmp_path)["open_count"] == 0
    _index(tmp_path, dirty=[dict(KEY, episode_id=f"e{i}") for i in range(5)])
    info = r5_keep.open_lifecycles(tmp_path, max_keys=3)
    assert info["open_count"] == 3 and info["truncated"] is True


def test_prereg_window_and_keep_since():
    assert r5_keep.prereg_window_start({}) is None
    assert r5_keep.prereg_window_start({"R5_PREREG_KEEP_SINCE_TS": "500"}) == 500.0
    assert r5_keep.keep_since_ts({"earliest_start_ts": 900.0}, {"R5_PREREG_KEEP_SINCE_TS": "500"}) == 500.0
    assert r5_keep.keep_since_ts({}, {}) is None


def test_segment_keep_seqs_binary_search(tmp_path):
    base = tmp_path / "v1"
    (base / "seg").mkdir(parents=True)
    (base / "man").mkdir()
    for seq in range(1, 11):
        tok = f"{seq:012d}"
        (base / "seg" / f"{tok}.tar.gz").write_bytes(b"x")
        (base / "man" / f"{tok}.json").write_text(json.dumps({"window_end": seq * 100.0}))
    assert r5_keep.segment_keep_seqs(tmp_path, "v1", None) == set()
    assert r5_keep.segment_keep_seqs(tmp_path, "v1", 650.0) == {7, 8, 9, 10}
    assert r5_keep.segment_keep_seqs(tmp_path, "v1", 650.0, shipped_seq=8) == {7, 8}
    assert r5_keep.segment_keep_seqs(tmp_path, "nope", 1.0) == set()

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_segment_fly_parity as parity  # noqa: E402


def _entry(data: bytes) -> dict:
    return {"class": "snapshot", "sha256": hashlib.sha256(data).hexdigest()}


def test_hash_cache_reuses_unchanged_files_and_rehashes_changed_ones(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.json").write_bytes(b"alpha")
    (tree / "b.json").write_bytes(b"beta")
    files = {"a.json": _entry(b"alpha"), "b.json": _entry(b"beta")}
    cache_path = tmp_path / "cache.json"

    first = parity.HashCache(cache_path, now=1000.0)
    assert parity.classify(files, [], tree, {}, {}, first)["verdict"] == "GREEN"
    first.save()
    assert (first.hashed, first.hits) == (2, 0)

    second = parity.HashCache(cache_path, now=1100.0)
    assert parity.classify(files, [], tree, {}, {}, second)["verdict"] == "GREEN"
    assert (second.hashed, second.hits) == (0, 2)

    (tree / "b.json").write_bytes(b"BETA")
    st = (tree / "b.json").stat()
    os.utime(tree / "b.json", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    third = parity.HashCache(cache_path, now=1200.0)
    report = parity.classify(files, [], tree, {}, {}, third)
    assert report["verdict"] == "RED" and report["counts"]["sealed_mismatch"] == 1
    assert (third.hashed, third.hits) == (1, 1)


def test_hash_cache_reverifies_old_entries_from_disk(tmp_path):
    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.json").write_bytes(b"alpha")
    cache_path = tmp_path / "cache.json"
    first = parity.HashCache(cache_path, now=0.0, reverify_sec=100.0)
    first.sha256("a.json", tree / "a.json")
    first.save()
    later = parity.HashCache(cache_path, now=250.0, reverify_sec=100.0)
    later.sha256("a.json", tree / "a.json")
    assert (later.hashed, later.hits) == (1, 0)


def test_corrupt_cache_file_is_ignored(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text("{not json", encoding="utf-8")
    assert parity.HashCache(cache_path).entries == {}


def test_scan_stops_at_its_deadline_and_keeps_partial_cache(tmp_path):
    import time

    import pytest

    tree = tmp_path / "tree"
    tree.mkdir()
    (tree / "a.json").write_bytes(b"alpha")
    files = {"a.json": _entry(b"alpha")}
    cache_path = tmp_path / "cache.json"
    seed = parity.HashCache(cache_path, now=1000.0)
    seed.entries = {"gone.json": [1, 1, "x", 1000.0]}
    with pytest.raises(parity.ParityTimeout) as exc:
        parity.classify(files, [], tree, {}, {}, seed, deadline=time.monotonic() - 1)
    assert exc.value.checked == 0
    seed.save(keep_unused=True)
    assert "gone.json" in parity.HashCache(cache_path, now=1000.0).entries
    assert parity.classify(files, [], tree, {}, {}, None, deadline=time.monotonic() + 60)["verdict"] == "GREEN"


# ---- cut-off /files download: retry/resume (custody stall 19 Oct) ------------------------------------------------------
import http.client as _hc


class _Resp:
    def __init__(self, data, status=200, headers=None, cut_after=None):
        self.data, self.status, self.headers, self.pos, self.cut = data, status, headers or {}, 0, cut_after

    def read(self, n=-1):
        if self.cut is not None and self.pos >= self.cut:
            raise _hc.IncompleteRead(b"", len(self.data) - self.pos)
        end = len(self.data) if n < 0 else self.pos + n
        if self.cut is not None:
            end = min(end, self.cut)
        out = self.data[self.pos:end]
        self.pos = end
        return out

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


BODY = b'{"seq": 7, "files": {"a": 1}}' * 1000


def test_incomplete_read_resumes_with_range():
    calls, sleeps = [], []

    def opener(req, timeout=None):
        calls.append(req.get_header("Range"))
        if len(calls) == 1:
            return _Resp(BODY, headers={"Content-Length": str(len(BODY))}, cut_after=5000)
        start = int(req.get_header("Range").split("=")[1].rstrip("-"))
        return _Resp(BODY[start:], status=206, headers={"Content-Range": f"bytes {start}-{len(BODY)-1}/{len(BODY)}"})
    old, parity.FETCH_CHUNK = parity.FETCH_CHUNK, 1024
    try:
        assert parity._fetch_bytes("http://x/files", "t", urlopen=opener, sleep=sleeps.append) == BODY
    finally:
        parity.FETCH_CHUNK = old
    assert calls == [None, "bytes=5000-"] and sleeps == [2.0]


def test_server_ignoring_range_restarts_from_zero_and_gives_up_after_attempts():
    sleeps = []
    n = {"c": 0}

    def opener(req, timeout=None):
        n["c"] += 1
        if n["c"] < 3:
            return _Resp(BODY, headers={"Content-Length": str(len(BODY))}, cut_after=3000)
        return _Resp(BODY, headers={"Content-Length": str(len(BODY))})
    assert parity._fetch_bytes("http://x/files", "t", urlopen=opener, sleep=sleeps.append) == BODY
    assert sleeps == [2.0, 5.0]

    def always_cut(req, timeout=None):
        return _Resp(BODY, headers={"Content-Length": str(len(BODY))}, cut_after=100)
    sleeps.clear()
    import pytest
    with pytest.raises(_hc.IncompleteRead):
        parity._fetch_bytes("http://x/files", "t", urlopen=always_cut, sleep=sleeps.append, attempts=3)
    assert sleeps == [2.0, 5.0]


def test_short_body_without_exception_counts_as_cut_off_and_401_is_not_retried():
    import urllib.error
    import pytest
    seq = iter([_Resp(BODY[:10], headers={"Content-Length": str(len(BODY))}), _Resp(BODY, headers={"Content-Length": str(len(BODY))})])
    assert parity._fetch_bytes("http://x", "t", urlopen=lambda r, timeout=None: next(seq), sleep=lambda s: None) == BODY

    def denied(req, timeout=None):
        raise urllib.error.HTTPError("http://x", 401, "no", {}, None)
    with pytest.raises(urllib.error.HTTPError):
        parity._fetch_bytes("http://x", "t", urlopen=denied, sleep=lambda s: (_ for _ in ()).throw(AssertionError("no retry")))

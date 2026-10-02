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

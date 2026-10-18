import os
import threading
import time
from pathlib import Path

import pytest

import research_v3_store as rvs

pytestmark = pytest.mark.skipif(os.name == "nt", reason="posix flock path")


def test_lock_file_is_removed_after_release(tmp_path):
    lock = tmp_path / ".locks" / "a.lock"
    with rvs._advisory_file_lock(lock):
        assert lock.exists()
    assert not lock.exists()
    assert list((tmp_path / ".locks").iterdir()) == []


def test_lock_removed_even_when_the_body_raises(tmp_path):
    lock = tmp_path / ".locks" / "b.lock"
    with pytest.raises(RuntimeError):
        with rvs._advisory_file_lock(lock):
            raise RuntimeError("boom")
    assert not lock.exists()


def test_mutual_exclusion_holds_with_unlink_between_contenders(tmp_path):
    lock = tmp_path / ".locks" / "c.lock"
    inside, overlap, counter = [0], [0], [0]

    def worker():
        for _ in range(40):
            with rvs._advisory_file_lock(lock):
                inside[0] += 1
                if inside[0] > 1:
                    overlap[0] += 1
                v = counter[0]
                time.sleep(0.0005)
                counter[0] = v + 1
                inside[0] -= 1

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert overlap[0] == 0 and counter[0] == 240
    assert not lock.exists()


def test_store_exclusive_leaves_no_lock_files(tmp_path):
    store = rvs.V3EvidenceStore.__new__(rvs.V3EvidenceStore)
    store.root = tmp_path.resolve()
    store.lock_dir = store.root / "v3" / ".locks"
    target = store.root / "v3" / "receipts" / "x.json"
    for _ in range(5):
        with store._exclusive(target):
            pass
    assert list(store.lock_dir.iterdir()) == []

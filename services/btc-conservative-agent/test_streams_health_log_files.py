import os
import time

import monitor_streams as ms


def test_log_files_expose_mtime_and_missing(tmp_path):
    (tmp_path / "decisions_3factor.csv").write_text("a\n")
    os.utime(tmp_path / "decisions_3factor.csv", (1000, 1000))
    out = ms.health(str(tmp_path), time.time())["log_files"]
    assert out["decisions_3factor.csv"]["exists"] and out["decisions_3factor.csv"]["mtime"] == 1000
    assert out["ai_errors_3factor.csv"] == {"exists": False, "mtime": None, "age_sec": None, "bytes": 0}


def test_runtime_subdir_fallback(tmp_path):
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "ai_errors_3factor.csv").write_text("a\n")
    out = ms.file_mtimes(str(tmp_path), time.time())
    assert out["ai_errors_3factor.csv"]["exists"] is True


def test_file_mtimes_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(ms, "_file_mtimes", boom)
    assert ms.file_mtimes("/nonexistent", time.time()) == {}
    assert ms.health("/nonexistent", time.time())["log_files"] == {}

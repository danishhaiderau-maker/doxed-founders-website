import os
import time

import monitor_streams as ms


def test_log_files_expose_mtime_and_missing(tmp_path):
    (tmp_path / "decisions_3factor.csv").write_text("a\n")
    os.utime(tmp_path / "decisions_3factor.csv", (1000, 1000))
    out = ms.health(str(tmp_path), time.time())["log_files"]
    assert out["decisions_3factor.csv"]["exists"] and out["decisions_3factor.csv"]["mtime"] == 1000
    assert out["ai_errors_3factor.csv"] == {"exists": False, "mtime": None, "age_sec": None, "bytes": 0}
